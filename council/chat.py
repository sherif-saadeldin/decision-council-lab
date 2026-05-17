from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal

from rich.console import Console
from rich.panel import Panel

from council.cli import (
    KNOWN_PROJECT_ERRORS,
    _print_preview_list,
    render_comparison_result,
    render_doctor,
    render_known_error,
    render_preset_list,
    render_prompts_inventory,
    render_runs_list,
    render_runs_show,
)
from council.compare import CompareRequest, ComparisonTarget, run_comparison
from council.config import Settings
from council.config_profiles import (
    ConfigProfile,
    CouncilConfigFile,
    UnknownConfigProfileError,
    load_config_file,
    resolve_debate_rounds_with_profile,
    resolve_profile_name,
    resolve_runtime_with_profile,
    resolve_settings_with_profile,
    set_active_profile,
)
from council.provider_availability import (
    HostedProviderUnavailableError,
    credential_source_for_preset,
    preset_is_hosted,
)
from council.council_session import (
    CouncilSessionRequest,
    CouncilSessionResult,
    plan_council_session,
    run_council_session,
)
from council.costing import enforce_cost_budget
from council.engine import run_council
from council.implementation_pack import write_implementation_pack
from council.models import CouncilRunResult
from council.progress import NullProgressReporter
from council.run_catalog import RunNotFoundError, get_run_summary
from council.runtime import RuntimeOptions
from council.storage import save_run
from council.verdict_quality import VerdictQualityError, decision_label, ensure_verdict_quality_for_pack

CHAT_PROMPT = "chat> "
DEFAULT_ROUTING_MODE = "economy"
DEFAULT_SYSTEM_PROFILE = "default"

# Shell-like input guard: tokens that almost certainly indicate a user pasted
# a shell command into chat instead of a question. We refuse these to avoid
# (a) burning a council run on a paste, and (b) confusing users who expect
# the chat to "run" the command.
SHELL_LIKE_PREFIXES: tuple[str, ...] = (
    "uv ",
    "uvx ",
    "python ",
    "python3 ",
    "py ",
    "pip ",
    "pipx ",
    "npm ",
    "npx ",
    "yarn ",
    "pnpm ",
    "git ",
    "gh ",
    "ls ",
    "cd ",
    "rm ",
    "mv ",
    "cp ",
    "cat ",
    "echo ",
    "curl ",
    "wget ",
    "docker ",
    "kubectl ",
    "make ",
    "bash ",
    "sh ",
    "powershell ",
    "pwsh ",
    "./",
)
SHELL_REJECTION_MESSAGE = (
    "This is chat mode, not a shell. Use slash commands "
    "(/help) or exit to run shell commands."
)

HOSTED_FAILURE_HINT = (
    "Provider failed. Use `/profile mock` to switch to the offline mock, or "
    "run `/doctor` to see what's missing."
)

CHAT_HELP_LINES: tuple[str, ...] = (
    "/council <question>  — multi-model council (economy routing)",
    "/run <question>      — single-provider council run",
    "/compare <question>  — compare mock preset (offline-safe default)",
    "/doctor              — configuration checks",
    "/presets             — list model presets",
    "/setup               — interactive setup wizard",
    "/runs                — list recent runs",
    "/show <run_id|last>  — inspect a saved run",
    "/pack <run_id|last>  — generate implementation pack (requires approval)",
    "/prompts             — system prompt inventory",
    "/profile [name|list] — show, list, or switch active config profile",
    "/status              — show active profile, routing, provider, last run",
    "/help                — show this help",
    "/exit                — leave chat",
    "",
    "Type a question without / to run council (confirms first).",
)


def looks_like_shell_command(text: str) -> bool:
    """Heuristic: refuse pasted shell commands so they don't become questions."""
    stripped = text.strip()
    if not stripped:
        return False
    lowered = stripped.lower()
    for prefix in SHELL_LIKE_PREFIXES:
        if lowered.startswith(prefix):
            return True
    return False


class ChatLineKind(str, Enum):
    EMPTY = "empty"
    SLASH = "slash"
    NATURAL = "natural"


@dataclass(frozen=True)
class ParsedChatLine:
    kind: ChatLineKind
    command: str = ""
    args: str = ""


@dataclass
class ChatSessionState:
    last_run_id: str | None = None
    system_profile: str = DEFAULT_SYSTEM_PROFILE
    routing_mode: str = DEFAULT_ROUTING_MODE
    config_profile_name: str | None = None


@dataclass(frozen=True)
class ChatContext:
    settings: Settings
    config: CouncilConfigFile | None
    config_profile: ConfigProfile | None
    runtime: RuntimeOptions


def parse_chat_line(line: str) -> ParsedChatLine:
    text = line.strip()
    if not text:
        return ParsedChatLine(kind=ChatLineKind.EMPTY)
    if text.startswith("/"):
        # `/`, `/ `, or `/   foo` collapse to an empty command name — treat
        # those as empty rather than crashing on parts[0].
        parts = text[1:].split(maxsplit=1)
        if not parts or not parts[0]:
            return ParsedChatLine(kind=ChatLineKind.EMPTY)
        command = parts[0].lower()
        args = parts[1].strip() if len(parts) > 1 else ""
        return ParsedChatLine(kind=ChatLineKind.SLASH, command=command, args=args)
    return ParsedChatLine(kind=ChatLineKind.NATURAL, args=text)


def resolve_active_config_profile_name(config: CouncilConfigFile | None) -> str:
    if config is not None:
        return config.active_profile
    return "(env defaults)"


def build_chat_context(
    settings: Settings,
    *,
    system_profile: str = DEFAULT_SYSTEM_PROFILE,
    routing_mode: str = DEFAULT_ROUTING_MODE,
    config_profile_name: str | None = None,
) -> ChatContext:
    config = load_config_file()
    profile_name = config_profile_name or resolve_profile_name(config=config)
    profile: ConfigProfile | None = None
    if profile_name and config is not None:
        try:
            profile = config.get_profile(profile_name)
        except Exception:
            profile = None
    resolved_settings = resolve_settings_with_profile(settings, profile=profile, cli_preset=None)
    runtime = resolve_runtime_with_profile(
        profile,
        cli_timeout=None,
        cli_max_retries=None,
        cli_fast=False,
        cli_fast_explicit=False,
        quiet=True,
    )
    from dataclasses import replace

    runtime = replace(
        runtime,
        system_profile=system_profile,
        show_progress=False,
    )
    return ChatContext(
        settings=resolved_settings,
        config=config,
        config_profile=profile,
        runtime=runtime,
    )


def render_chat_welcome(
    console: Console,
    *,
    config_profile_name: str,
    system_profile: str,
    routing_mode: str,
) -> None:
    lines = [
        f"Config profile: [cyan]{config_profile_name}[/cyan]",
        f"System profile: [cyan]{system_profile}[/cyan]",
        f"Routing mode: [cyan]{routing_mode}[/cyan] (council default)",
        "",
        "Slash commands:",
        *CHAT_HELP_LINES,
    ]
    console.print(Panel("\n".join(lines), title="Decision Council Chat", border_style="blue"))


def render_chat_help(console: Console) -> None:
    console.print(Panel("\n".join(CHAT_HELP_LINES), title="Chat help", border_style="blue"))


def render_chat_verdict(console: Console, result: CouncilRunResult) -> None:
    dossier = result.dossier
    direct = dossier.direct_answer.strip() or dossier.recommendation.split("\n", 1)[0]
    decision_type = decision_label(dossier.decision_type)
    console.print(
        Panel.fit(
            f"[bold]{direct}[/bold]\n\n"
            f"Decision: {decision_type}\n"
            f"Run ID: [cyan]{dossier.run_id}[/cyan]",
            title="Direct Answer",
            border_style="green",
        )
    )
    _print_preview_list(console, "Do next", dossier.next_actions)
    _print_preview_list(console, "Do not do", dossier.do_not_do)
    if dossier.approval_gate.strip():
        console.print(f"[bold]Approval gate[/bold]\n  {dossier.approval_gate.strip()}\n")


def load_run_result(runs_dir: Path, run_id: str) -> CouncilRunResult:
    json_path = runs_dir / run_id / "run.json"
    if not json_path.is_file():
        raise RunNotFoundError(run_id, runs_dir)
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    return CouncilRunResult.model_validate(payload)


def resolve_run_id_arg(state: ChatSessionState, arg: str) -> str | None:
    token = arg.strip().lower()
    if token in ("last", "latest"):
        return state.last_run_id
    return arg.strip() or state.last_run_id


ConfirmFn = Callable[[str, bool], bool]
InputFn = Callable[[str], str]


@dataclass
class ChatSession:
    console: Console
    error_console: Console
    ctx: ChatContext
    state: ChatSessionState = field(default_factory=ChatSessionState)
    input_fn: InputFn = field(default=lambda prompt: input(prompt))
    confirm_fn: ConfirmFn = field(
        default=lambda message, default: _default_confirm(message, default)
    )
    council_runner: Callable[..., CouncilSessionResult] | None = None
    doctor_runner: Callable[..., list] | None = None
    setup_runner: Callable[..., object] | None = None  # SetupResult from council.setup

    def handle_line(self, line: str) -> Literal["continue", "exit"]:
        parsed = parse_chat_line(line)
        if parsed.kind == ChatLineKind.EMPTY:
            return "continue"
        if parsed.kind == ChatLineKind.NATURAL:
            return self._handle_natural(parsed.args)
        return self._handle_slash(parsed.command, parsed.args)

    def _handle_natural(self, question: str) -> Literal["continue", "exit"]:
        if looks_like_shell_command(question):
            self.error_console.print(SHELL_REJECTION_MESSAGE, style="yellow")
            return "continue"
        if not self.confirm_fn("Run council on this?", True):
            self.console.print("[dim]Cancelled.[/dim]")
            return "continue"
        try:
            self._run_council(question)
        except KNOWN_PROJECT_ERRORS as exc:
            self._render_provider_error(exc)
        return "continue"

    def _handle_slash(self, command: str, args: str) -> Literal["continue", "exit"]:
        handlers: dict[str, Callable[[str], Literal["continue", "exit"]]] = {
            "exit": lambda _a: self._cmd_exit(),
            "quit": lambda _a: self._cmd_exit(),
            "help": lambda _a: self._cmd_help(),
            "council": self._cmd_council,
            "run": self._cmd_run,
            "compare": self._cmd_compare,
            "doctor": lambda _a: self._cmd_doctor(),
            "presets": lambda _a: self._cmd_presets(),
            "setup": lambda _a: self._cmd_setup(),
            "runs": lambda _a: self._cmd_runs(),
            "show": self._cmd_show,
            "pack": self._cmd_pack,
            "prompts": lambda _a: self._cmd_prompts(),
            "profile": self._cmd_profile,
            "status": lambda _a: self._cmd_status(),
        }
        handler = handlers.get(command)
        if handler is None:
            self.error_console.print(f"Unknown command: /{command}. Type /help.", style="red")
            return "continue"
        try:
            return handler(args)
        except KNOWN_PROJECT_ERRORS as exc:
            self._render_provider_error(exc)
            return "continue"

    def _render_provider_error(self, exc: Exception) -> None:
        render_known_error(self.error_console, exc, quiet=False)
        if isinstance(exc, HostedProviderUnavailableError) or self._is_hosted_failure(exc):
            self.error_console.print(HOSTED_FAILURE_HINT, style="yellow")

    def _is_hosted_failure(self, exc: Exception) -> bool:
        """Best-effort: does this error stem from a hosted provider config?"""
        try:
            from council.providers.errors import (
                MissingProviderCredentialError,
                ProviderResponseError,
            )
        except ImportError:
            return False
        if not isinstance(exc, (MissingProviderCredentialError, ProviderResponseError)):
            return False
        profile = self.ctx.config_profile
        if profile is None:
            return False
        # Heuristic: profile uses a hosted preset, or hosted mode (openai/openai_compatible)
        if profile.preset and preset_is_hosted(profile.preset):
            return True
        mode = (profile.mode or "").lower()
        return mode in {"openai", "openai_compatible"}

    def _cmd_exit(self) -> Literal["exit"]:
        self.console.print("Goodbye.")
        return "exit"

    def _cmd_help(self) -> Literal["continue", "exit"]:
        render_chat_help(self.console)
        return "continue"

    def _cmd_doctor(self) -> Literal["continue", "exit"]:
        from council.doctor import run_doctor

        runner = self.doctor_runner or run_doctor
        checks = runner(self.ctx.settings, runtime=self.ctx.runtime)
        render_doctor(self.console, checks)
        return "continue"

    def _cmd_presets(self) -> Literal["continue", "exit"]:
        render_preset_list(self.console)
        return "continue"

    def _cmd_setup(self) -> Literal["continue", "exit"]:
        from getpass import getpass

        from council.secrets import set_keyring_secret
        from council.setup import run_setup
        from council.smoke import run_smoke
        from council.config_profiles import config_path

        runner = self.setup_runner or run_setup
        from council.setup import SetupResult

        raw = runner(
            interactive=True,
            profile_name=None,
            config_path_override=config_path(),
            console=self.console,
            store_secret_fn=set_keyring_secret,
            secret_prompt_fn=getpass,
            doctor_fn=self.doctor_runner,
            smoke_fn=run_smoke,
        )
        if isinstance(raw, SetupResult) and raw.message:
            target = self.console if raw.exit_code == 0 else self.error_console
            target.print(raw.message, style="red" if raw.exit_code else None)
        return "continue"

    def _cmd_runs(self) -> Literal["continue", "exit"]:
        render_runs_list(self.console, self.ctx.settings.runs_dir)
        return "continue"

    def _cmd_show(self, args: str) -> Literal["continue", "exit"]:
        run_id = resolve_run_id_arg(self.state, args)
        if not run_id:
            self.error_console.print("No run ID. Run council first or pass /show RUN_ID.", style="red")
            return "continue"
        summary = get_run_summary(self.ctx.settings.runs_dir, run_id)
        render_runs_show(self.console, summary)
        return "continue"

    def _cmd_prompts(self) -> Literal["continue", "exit"]:
        render_prompts_inventory(self.console, system_profile=self.state.system_profile)
        return "continue"

    def _cmd_profile(self, args: str) -> Literal["continue", "exit"]:
        token = args.strip()
        if not token:
            return self._show_active_profile()
        parts = token.split(maxsplit=1)
        verb = parts[0].lower()
        rest = parts[1].strip() if len(parts) > 1 else ""
        if verb == "list":
            return self._list_profiles()
        if verb == "show":
            return self._show_active_profile()
        if verb == "use":
            if not rest:
                self.error_console.print("Usage: /profile use NAME", style="red")
                return "continue"
            return self._switch_profile(rest)
        # `/profile <name>` is sugar for `/profile use <name>`.
        return self._switch_profile(token)

    def _show_active_profile(self) -> Literal["continue", "exit"]:
        config = self.ctx.config
        if config is None:
            self.console.print(
                "No config file loaded. Run [bold]/setup[/bold] or "
                "[bold]config init[/bold] outside chat."
            )
            return "continue"
        self.console.print(
            f"Active config profile: [cyan]{config.active_profile}[/cyan] "
            f"({config.path})"
        )
        return "continue"

    def _list_profiles(self) -> Literal["continue", "exit"]:
        config = self.ctx.config
        if config is None:
            self.console.print("No config file loaded.")
            return "continue"
        for name in config.profile_names():
            marker = "*" if name == config.active_profile else " "
            self.console.print(f" {marker} {name}")
        return "continue"

    def _switch_profile(self, name: str) -> Literal["continue", "exit"]:
        config = self.ctx.config
        if config is None:
            self.error_console.print(
                "No config file present. Run config init first.", style="red"
            )
            return "continue"
        try:
            set_active_profile(name, config.path)
        except UnknownConfigProfileError as exc:
            self.error_console.print(str(exc), style="red")
            return "continue"
        # Rebuild context against the new active profile so subsequent
        # commands use the right settings/runtime.
        new_ctx = build_chat_context(
            self.ctx.settings,
            system_profile=self.state.system_profile,
            routing_mode=self.state.routing_mode,
            config_profile_name=name,
        )
        self.ctx = new_ctx
        self.state.config_profile_name = name
        self.console.print(
            f"[green]Switched active profile to[/green] [cyan]{name}[/cyan]."
        )
        return "continue"

    def _cmd_status(self) -> Literal["continue", "exit"]:
        config = self.ctx.config
        profile = self.ctx.config_profile
        config_label = config.active_profile if config else "(env defaults)"
        provider = self.ctx.settings.llm_provider_name
        mode = self.ctx.settings.llm_mode
        preset = profile.preset if profile else None
        model = (
            self.ctx.settings.llm_model
            or self.ctx.settings.openai_model
            or self.ctx.settings.mock_model
        )
        cred_source: str
        if preset:
            cred_source = credential_source_for_preset(preset, self.ctx.settings)
        elif mode == "mock":
            cred_source = "not_required"
        elif mode == "openai":
            from council.secrets import credential_source

            cred_source = credential_source("OPENAI_API_KEY")
        else:
            from council.secrets import credential_source

            cred_source = credential_source("LLM_API_KEY")
        last_run = self.state.last_run_id or "—"
        lines = [
            f"Config profile : [cyan]{config_label}[/cyan]",
            f"System profile : [cyan]{self.state.system_profile}[/cyan]",
            f"Routing mode   : [cyan]{self.state.routing_mode}[/cyan]",
            f"Provider/mode  : [cyan]{provider}[/cyan] ({mode})",
            f"Preset         : [cyan]{preset or '—'}[/cyan]",
            f"Model          : [cyan]{model or '—'}[/cyan]",
            f"Credential src : [cyan]{cred_source}[/cyan]",
            f"Last run id    : [cyan]{last_run}[/cyan]",
        ]
        self.console.print(Panel("\n".join(lines), title="Chat Status", border_style="blue"))
        return "continue"

    def _cmd_pack(self, args: str) -> Literal["continue", "exit"]:
        run_id = resolve_run_id_arg(self.state, args)
        if not run_id:
            self.error_console.print("No run ID. Run council first or pass /pack RUN_ID.", style="red")
            return "continue"
        if not self.confirm_fn("Generate implementation pack for this run?", False):
            self.console.print("[dim]Pack generation cancelled.[/dim]")
            return "continue"
        try:
            result = load_run_result(self.ctx.settings.runs_dir, run_id)
            ensure_verdict_quality_for_pack(result.dossier)
            run_dir = self.ctx.settings.runs_dir / run_id
            paths = write_implementation_pack(run_dir, result)
            self.console.print("[green]Implementation pack:[/green]")
            for path in paths:
                self.console.print(f"  {path}")
        except VerdictQualityError as exc:
            render_known_error(self.error_console, exc, quiet=False)
        return "continue"

    def _cmd_run(self, question: str) -> Literal["continue", "exit"]:
        if not question:
            self.error_console.print("Usage: /run <question>", style="red")
            return "continue"
        debate_rounds = resolve_debate_rounds_with_profile(
            self.ctx.config_profile,
            cli_debate_rounds=None,
            runtime=self.ctx.runtime,
        )
        result, _ = run_council(
            question,
            settings=self.ctx.settings,
            debate_rounds=debate_rounds,
            runtime=self.ctx.runtime,
            progress=NullProgressReporter(),
        )
        _, md_path = save_run(result, settings=self.ctx.settings)
        self.state.last_run_id = result.dossier.run_id
        render_chat_verdict(self.console, result)
        self.console.print(f"[green]Saved:[/green] {md_path}")
        return "continue"

    def _cmd_compare(self, question: str) -> Literal["continue", "exit"]:
        if not question:
            self.error_console.print("Usage: /compare <question>", style="red")
            return "continue"
        request = CompareRequest(
            question=question,
            targets=(ComparisonTarget(kind="preset", name="mock"),),
            runs_dir=self.ctx.settings.runs_dir,
            debate_rounds=0,
            fast=self.ctx.runtime.fast_mode,
        )
        report, json_path, md_path = run_comparison(request)
        render_comparison_result(
            self.console,
            report,
            json_path,
            md_path,
            quiet=False,
        )
        return "continue"

    def _cmd_council(self, question: str) -> Literal["continue", "exit"]:
        if not question:
            self.error_console.print("Usage: /council <question>", style="red")
            return "continue"
        self._run_council(question)
        return "continue"

    def _run_council(self, question: str) -> None:
        from council.routing_modes import resolve_debate_rounds

        debate_rounds = resolve_debate_rounds(
            self.state.routing_mode,
            cli_debate_rounds=resolve_debate_rounds_with_profile(
                self.ctx.config_profile,
                cli_debate_rounds=None,
                runtime=self.ctx.runtime,
            ),
            max_debate_rounds=None,
        )
        request = CouncilSessionRequest(
            question=question,
            routing_mode=self.state.routing_mode,
            debate_rounds=debate_rounds,
            base_settings=self.ctx.settings,
            runtime=self.ctx.runtime,
            prompt_create_pack=False,
            create_pack=False,
        )
        plan = plan_council_session(request)
        enforce_cost_budget(
            plan.cost_estimate,
            max_cost_usd=request.max_cost_usd,
            max_llm_calls=request.max_llm_calls,
            allow_over_budget=request.allow_over_budget,
        )
        if self.council_runner is not None:
            session = self.council_runner(
                request,
                progress=NullProgressReporter(),
                plan=plan,
            )
        else:
            session = run_council_session(
                request,
                progress=NullProgressReporter(),
                plan=plan,
            )

        pack_paths: list[Path] = []
        if self.confirm_fn("Create implementation pack?", False):
            ensure_verdict_quality_for_pack(session.result.dossier)
            run_dir = self.ctx.settings.runs_dir / session.result.dossier.run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            pack_paths = write_implementation_pack(run_dir, session.result)

        _, md_path = save_run(
            session.result,
            settings=self.ctx.settings,
            implementation_pack_paths=pack_paths or None,
        )
        self.state.last_run_id = session.result.dossier.run_id
        render_chat_verdict(self.console, session.result)
        self.console.print(f"[green]Saved:[/green] {md_path}")
        if pack_paths:
            self.console.print("[green]Implementation pack written.[/green]")

    def run_loop(self) -> int:
        config_label = self.state.config_profile_name or resolve_active_config_profile_name(
            self.ctx.config
        )
        render_chat_welcome(
            self.console,
            config_profile_name=config_label,
            system_profile=self.state.system_profile,
            routing_mode=self.state.routing_mode,
        )
        while True:
            try:
                line = self.input_fn(CHAT_PROMPT)
            except (EOFError, KeyboardInterrupt):
                self.console.print()
                break
            status = self.handle_line(line)
            if status == "exit":
                break
        return 0


def _default_confirm(message: str, default: bool) -> bool:
    from rich.prompt import Confirm

    return bool(Confirm.ask(message, default=default))


def run_chat_session(
    console: Console,
    error_console: Console,
    *,
    settings: Settings,
    system_profile: str = DEFAULT_SYSTEM_PROFILE,
    config_profile_name: str | None = None,
    routing_mode: str = DEFAULT_ROUTING_MODE,
    input_fn: InputFn | None = None,
    confirm_fn: ConfirmFn | None = None,
    council_runner: Callable[..., CouncilSessionResult] | None = None,
    doctor_runner: Callable[..., list] | None = None,
    setup_runner: Callable[..., object] | None = None,
) -> int:
    ctx = build_chat_context(
        settings,
        system_profile=system_profile,
        routing_mode=routing_mode,
        config_profile_name=config_profile_name,
    )
    state = ChatSessionState(
        system_profile=system_profile,
        routing_mode=routing_mode,
        config_profile_name=config_profile_name or resolve_profile_name(config=ctx.config),
    )
    session = ChatSession(
        console=console,
        error_console=error_console,
        ctx=ctx,
        state=state,
        input_fn=input_fn or (lambda prompt: input(prompt)),
        confirm_fn=confirm_fn or _default_confirm,
        council_runner=council_runner,
        doctor_runner=doctor_runner,
        setup_runner=setup_runner,
    )
    if not sys.stdin.isatty() and input_fn is None:
        error_console.print("Chat requires an interactive terminal.", style="red")
        return 1
    return session.run_loop()
