from __future__ import annotations

import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Literal

from rich.console import Console
from rich.panel import Panel

from council.cli import (
    KNOWN_PROJECT_ERRORS,
    render_comparison_result,
    render_doctor,
    render_known_error,
    render_preset_list,
    render_prompts_inventory,
    render_runs_list,
    render_runs_show,
    render_sources_list,
    render_sources_show,
    render_sources_query,
)
from council.compare import CompareRequest, ComparisonTarget, run_comparison
from council.config import Settings
from council.chat_state import (
    DEFAULT_ROUTING_MODE,
    DEFAULT_SYSTEM_PROFILE,
    ChatContext,
    ChatSessionState,
    DoctorCacheEntry,
)
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
from council.decision_thread import (
    CONTEXT_BLOCK_HEADER,
    looks_like_follow_up,
    summarize_for_context,
)
from council.doctor import CheckStatus, DoctorCheck
from council.intake import (
    DecisionIntake,
    apply_intake_answer,
    editable_fields,
    empty_intake,
    format_intake_summary,
    is_intake_complete,
    mode_picker_prompt,
    mode_profile,
    next_intake_question,
    parse_mode,
    question_for_field,
    routing_for_mode,
)
from council.provider_availability import (
    credential_source_for_preset,
    estimate_preset_availability,
    preset_is_hosted,
)
from council.review import ReviewTransitionError
from council.rendering.chat_renderer import (
    CHAT_HELP_LINES,
    render_chat_help,
    render_chat_verdict,
    render_chat_verdict_short,
    render_chat_welcome,
)
from council.rendering.review_renderer import lifecycle_style, render_review
from council.rendering.status_renderer import health_style
from council.review_model import (
    PACK_GATE_BLOCKED_REASON,
    LifecycleState,
    is_pack_allowed,
    resolve_actor,
)
from council.council_session import (
    CouncilSessionResult,
)
from council.models import CouncilRunResult
from council.progress import NullProgressReporter
from council.run_catalog import RunNotFoundError, get_run_summary
from council.services.council_service import ChatCouncilRequest, CouncilRequest, CouncilService
from council.services.pack_service import PackGenerationBlockedError, PackRequest, PackService
from council.services.provider_service import ProviderRecoveryRequest, ProviderRecoveryService
from council.services.review_service import (
    RejectRequest,
    ReviewRequest,
    ReviewService,
    RevisionRequest,
)
from council.services.run_service import RunQuery, RunService
from council.storage.run_store import FileRunStore
from council.sources.service import SourceService
from council.runtime_profiles import apply_profile_to_settings, resolve_operational_profile
from council.verdict_quality import VerdictQualityError

__all__ = [
    "APPROVE_NOW_PROMPT",
    "CHAT_HELP_LINES",
    "ChatLineKind",
    "ChatSession",
    "ChatSessionState",
    "DoctorCacheEntry",
    "FALLBACK_PROFILE_NAME",
    "FALLBACK_PROMPT",
    "FOLLOW_UP_PROMPT_TEMPLATE",
    "HEALTH_FAILED",
    "HEALTH_HEALTHY",
    "HEALTH_UNCHECKED",
    "HEALTH_WARNING",
    "build_chat_context",
    "looks_like_shell_command",
    "parse_chat_line",
    "parse_doctor_args",
    "render_chat_help",
    "render_chat_welcome",
    "run_chat_session",
    "summarize_doctor_checks",
]

CHAT_PROMPT = "chat> "
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
    # Note: `make ` was removed in Slice 5.8 because it false-positives on
    # natural follow-up phrases like "make it cheaper". Council users who
    # want to paste Makefile commands can use slash commands instead.
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

# When `/doctor` has never run in this session, /status reports this health.
HEALTH_UNCHECKED = "unchecked"
HEALTH_HEALTHY = "healthy"
HEALTH_WARNING = "warning"
HEALTH_FAILED = "failed"

FALLBACK_PROMPT = "Fallback to mock profile? [Y/n]"
FALLBACK_PROFILE_NAME = "mock"

INTAKE_OPENING_LINE = (
    "Let's think this through together. I'll ask a few quick questions, "
    "summarize what I heard, then run the council."
)
INTAKE_RUN_PROMPT = "Ready for me to run the council analysis?"
INTAKE_EDIT_PROMPT = "Edit intake first?"
INTAKE_DISCARDED_HINT = (
    "Intake cleared. Type a fresh goal to start over, or use /council to skip "
    "intake entirely."
)
SHOW_FULL_BREAKDOWN_PROMPT = "Want the full council breakdown?"

FOLLOW_UP_PROMPT_TEMPLATE = "Use previous decision context from {run_id}? [Y/n]"
APPROVE_NOW_PROMPT = "Approve now?"
PACK_GATE_HINT_LINES: tuple[str, ...] = (
    PACK_GATE_BLOCKED_REASON,
    "Try one of:",
    "  /approve last",
    "  /review last",
    "Or override with: /pack <run_id> --allow-unapproved",
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


def parse_doctor_args(args: str) -> tuple[bool, bool]:
    """Parse `/doctor --live --live-completion` flags. Unknown tokens ignored.

    Returns (live, live_completion). `--live-completion` implies live.
    """
    tokens = {t.strip().lower() for t in args.split() if t.strip()}
    live_completion = "--live-completion" in tokens or "live-completion" in tokens
    live = live_completion or "--live" in tokens or "live" in tokens
    return live, live_completion


def summarize_doctor_checks(checks: list[DoctorCheck]) -> tuple[str, int, int, int]:
    """Reduce a doctor check list to (health, ok_count, warn_count, fail_count)."""
    ok = sum(1 for c in checks if c.status == CheckStatus.OK)
    warn = sum(1 for c in checks if c.status == CheckStatus.WARN)
    fail = sum(1 for c in checks if c.status == CheckStatus.FAIL)
    if fail:
        return HEALTH_FAILED, ok, warn, fail
    if warn:
        return HEALTH_WARNING, ok, warn, fail
    return HEALTH_HEALTHY, ok, warn, fail


def _health_style(health: str) -> str:
    return health_style(health)


def _lifecycle_style(status: str) -> str:
    """Rich style for a lifecycle label. Mirrors `cli._lifecycle_label`."""
    return lifecycle_style(status)


def _thread_markers(item, *, thread_id: str) -> list[str]:
    """Per-run markers for `/thread` (e.g. `[root] [revision] [approved]`).

    Brackets are pre-escaped with a leading backslash so Rich panel rendering
    prints them as literal characters instead of treating them as style tags.
    """
    markers: list[str] = []
    markers.append(r"\[root]" if item.run_id == thread_id else r"\[child]")
    if item.is_revision_of:
        markers.append(r"\[revision]")
    status = item.lifecycle_status
    if status == LifecycleState.APPROVED.value:
        markers.append(r"\[approved]")
    elif status == LifecycleState.REJECTED.value:
        markers.append(r"\[rejected]")
    elif status == LifecycleState.SUPERSEDED.value:
        markers.append(r"\[superseded]")
    elif status == LifecycleState.ARCHIVED.value:
        markers.append(r"\[archived]")
    elif status == LifecycleState.UNDER_REVIEW.value:
        markers.append(r"\[under_review]")
    else:
        markers.append(r"\[draft]")
    return markers


def _first_failing_message(checks: list[DoctorCheck]) -> str:
    for check in checks:
        if check.status == CheckStatus.FAIL:
            return f"{check.name}: {check.message}"
    for check in checks:
        if check.status == CheckStatus.WARN:
            return f"{check.name}: {check.message}"
    return "all checks passed"


def _human_source_recap(payload) -> str:
    important: list[str] = []
    implementation: list[str] = []
    for item in payload.relevance[:8]:
        path = item.path.lower()
        if any(token in path for token in ("readme", "architecture", "roadmap", "spec", "build_order", "plan", "vision", "product", "todo")):
            important.append(item.path)
        else:
            implementation.append(item.path)
    if important:
        return (
            "[cyan]I reviewed your strategic docs first[/cyan]: "
            + ", ".join(important[:3])
        )
    if implementation:
        return (
            "[cyan]I reviewed your implementation history before generating this verdict[/cyan]: "
            + ", ".join(implementation[:3])
        )
    return "[cyan]I reviewed the attached source context before generating this verdict.[/cyan]"


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
    # NOTE: do NOT call resolve_profile_name(config=config) here when config is
    # None — that path re-reads .dcouncil/config.toml from cwd via the
    # config_profiles module-local import, which bypasses test monkeypatches
    # on `council.chat.load_config_file`. Pre-Slice-5.9.1 this leaked any
    # real (or corrupt) cwd config into chat-fixture tests.
    if config_profile_name:
        profile_name: str | None = config_profile_name
    elif config is not None:
        profile_name = config.active_profile
    else:
        profile_name = None
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


def load_run_result(runs_dir: Path, run_id: str) -> CouncilRunResult:
    return FileRunStore(runs_dir).load_run(run_id)


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

    def _store(self) -> FileRunStore:
        return FileRunStore(self.ctx.settings.runs_dir)

    def _council_service(self) -> CouncilService:
        return CouncilService(self._store())

    def _review_service(self) -> ReviewService:
        return ReviewService(self._store())

    def _pack_service(self) -> PackService:
        return PackService(self._store())

    def _run_service(self) -> RunService:
        return RunService(self._store())

    def _source_service(self) -> SourceService:
        return SourceService()

    def handle_line(self, line: str) -> Literal["continue", "exit"]:
        parsed = parse_chat_line(line)
        if parsed.kind == ChatLineKind.EMPTY:
            return "continue"
        if parsed.kind == ChatLineKind.NATURAL:
            return self._handle_natural(parsed.args)
        return self._handle_slash(parsed.command, parsed.args)

    def _handle_natural(self, line: str) -> Literal["continue", "exit"]:
        if looks_like_shell_command(line):
            self.error_console.print(SHELL_REJECTION_MESSAGE, style="yellow")
            return "continue"

        # If we're mid-intake, this line answers the current question.
        if self.state.current_intake is not None and self.state.current_intake_field:
            self._record_intake_answer(line)
            return "continue"

        # Slice 5.8 still applies: a clear follow-up phrase against a
        # recent run is a contextual continuation, not a fresh intake.
        # Try the follow-up path FIRST so users mid-thread don't get
        # asked goal/mode/context all over again.
        if self._try_follow_up_branch(line):
            return "continue"

        # Otherwise this line opens a new conversation. Start an intake
        # using `line` as the goal — that's the most natural way to
        # interpret "I want to build an AI movie startup."
        return self._start_intake(initial_goal=line)

    def _try_follow_up_branch(self, line: str) -> bool:
        """Slice 5.8 path. Returns True when we handled `line` as a
        contextual follow-up (and therefore should NOT start an intake)."""
        if not self.state.last_run_id:
            return False
        if self.state.current_context is not None:
            # Already have context — fall straight through to council on a
            # confirm. Behaves like the old direct-to-council path.
            return self._run_with_existing_context(line)
        if not looks_like_follow_up(line):
            return False
        if not self.confirm_fn(
            FOLLOW_UP_PROMPT_TEMPLATE.format(run_id=self.state.last_run_id),
            True,
        ):
            return False
        if not self._load_context_from_run(self.state.last_run_id):
            return False
        return self._run_with_existing_context(line)

    def _run_with_existing_context(self, question: str) -> bool:
        if not self.confirm_fn("Ready for me to run the council analysis?", True):
            self.console.print("[dim]Cancelled.[/dim]")
            return True
        try:
            self._run_council(question)
        except KNOWN_PROJECT_ERRORS as exc:
            self._handle_provider_failure(
                exc, retry=lambda: self._run_council(question)
            )
        return True

    # --- Slice 6.0: guided intake conversation ------------------------------

    def _start_intake(self, *, initial_goal: str | None = None) -> Literal["continue", "exit"]:
        intake = empty_intake()
        if initial_goal:
            intake = apply_intake_answer(intake, "goal", initial_goal)
        self.state.current_intake = intake
        self.console.print(f"[dim]{INTAKE_OPENING_LINE}[/dim]")
        if initial_goal:
            self.console.print(f"[green]Goal noted:[/green] {initial_goal}")
        return self._advance_intake()

    def _advance_intake(self) -> Literal["continue", "exit"]:
        """Ask the next intake question, or jump to confirm when done."""
        intake = self.state.current_intake
        assert intake is not None
        question = next_intake_question(intake)
        if question is None or is_intake_complete(intake):
            return self._confirm_intake_and_maybe_run()
        self.state.current_intake_field = question.field
        # Render the prompt without Rich markup interpretation so '(...)'
        # and numbered lists in mode_picker_prompt render verbatim.
        self.console.print(question.prompt, highlight=False, markup=False)
        return "continue"

    def _record_intake_answer(self, answer: str) -> Literal["continue", "exit"]:
        intake = self.state.current_intake
        field = self.state.current_intake_field
        assert intake is not None and field is not None
        question = question_for_field(field)
        text = answer.strip()
        # Optional fields ('notes') accept an empty answer to skip.
        if not text and question is not None and question.optional:
            self.state.current_intake_field = None
            return self._advance_intake()
        # Mode field needs to parse; bad inputs re-ask without advancing.
        if field == "preferred_mode" and parse_mode(text) is None:
            self.error_console.print(
                "I didn't recognize that mode. Try a number 1–6 or a keyword "
                "like 'deep', 'risk', 'plan'.",
                style="yellow",
            )
            return "continue"
        updated = apply_intake_answer(intake, field, answer)
        self.state.current_intake = updated
        if field == "preferred_mode":
            self.state.current_mode = updated.preferred_mode
        self.state.current_intake_field = None
        return self._advance_intake()

    def _confirm_intake_and_maybe_run(self) -> Literal["continue", "exit"]:
        intake = self.state.current_intake
        assert intake is not None
        self._render_intake_summary(intake)
        if self.confirm_fn(INTAKE_RUN_PROMPT, True):
            self._finalize_intake_and_run()
            return "continue"
        # User said no — offer an edit pass before discarding.
        if self.confirm_fn(INTAKE_EDIT_PROMPT, False):
            field = self._prompt_for_field_to_edit()
            if field is not None:
                self.state.current_intake_field = field
                question = question_for_field(field)
                if question is not None:
                    self.console.print(
                        question.prompt, highlight=False, markup=False
                    )
            return "continue"
        self._clear_intake_state()
        self.console.print(f"[dim]{INTAKE_DISCARDED_HINT}[/dim]")
        return "continue"

    def _prompt_for_field_to_edit(self) -> str | None:
        """Use input_fn directly so the user can type the field name to edit."""
        self.console.print(
            f"Which field? ({', '.join(editable_fields())})"
        )
        try:
            raw = self.input_fn("edit field> ")
        except (EOFError, KeyboardInterrupt):
            return None
        field = raw.strip().lower()
        if field not in editable_fields():
            self.error_console.print(
                f"Unknown field {field!r}. Edit cancelled.", style="yellow"
            )
            return None
        return field

    def _finalize_intake_and_run(self) -> None:
        intake = self.state.current_intake
        assert intake is not None
        question_text = self._intake_to_question(intake)
        # Snapshot the intake before we hand it to the council so it
        # survives a clear-intake mid-flight.
        self.state.last_intake = intake
        mode_routing = routing_for_mode(intake.preferred_mode)
        # Apply the mode's routing preference to this session unless the
        # user has already overridden via /mode.
        if mode_routing is not None:
            self.state.routing_mode = mode_routing.routing_mode
            self.state.current_mode = intake.preferred_mode
        try:
            self._run_council(question_text, intake=intake)
        except KNOWN_PROJECT_ERRORS as exc:
            self._handle_provider_failure(
                exc, retry=lambda: self._run_council(question_text, intake=intake)
            )
        # Clear current intake but keep last_intake for /summary reuse.
        self.state.current_intake = None
        self.state.current_intake_field = None

    def _intake_to_question(self, intake: DecisionIntake) -> str:
        """Turn the intake into a one-line question for the chair.

        The full structured context is prepended separately via
        `compose_question_with_intake`; this string is the human-shaped
        question itself.
        """
        goal = intake.goal.strip() or "(no explicit goal)"
        return f"Given the situation above, what should I do about: {goal}"

    def _render_intake_summary(self, intake: DecisionIntake) -> None:
        from rich.panel import Panel

        text = format_intake_summary(intake)
        self.console.print(
            Panel(text, title="Decision intake", border_style="cyan")
        )

    def _clear_intake_state(self) -> None:
        self.state.current_intake = None
        self.state.current_intake_field = None

    def _handle_slash(self, command: str, args: str) -> Literal["continue", "exit"]:
        handlers: dict[str, Callable[[str], Literal["continue", "exit"]]] = {
            "exit": lambda _a: self._cmd_exit(),
            "quit": lambda _a: self._cmd_exit(),
            "help": lambda _a: self._cmd_help(),
            "council": self._cmd_council,
            "run": self._cmd_run,
            "compare": self._cmd_compare,
            "doctor": self._cmd_doctor,
            "presets": lambda _a: self._cmd_presets(),
            "setup": lambda _a: self._cmd_setup(),
            "runs": lambda _a: self._cmd_runs(),
            "show": self._cmd_show,
            "pack": self._cmd_pack,
            "prompts": lambda _a: self._cmd_prompts(),
            "profile": self._cmd_profile,
            "status": lambda _a: self._cmd_status(),
            "context": lambda _a: self._cmd_context(),
            "forget": lambda _a: self._cmd_forget(),
            "use": self._cmd_use,
            "thread": lambda _a: self._cmd_thread(),
            "approve": self._cmd_approve,
            "reject": self._cmd_reject,
            "revise": self._cmd_revise,
            "review": self._cmd_review,
            "archive": self._cmd_archive,
            # Slice 6.0: guided intake commands.
            "intake": lambda _a: self._cmd_intake(),
            "edit": self._cmd_edit,
            "clear-intake": lambda _a: self._cmd_clear_intake(),
            "clear_intake": lambda _a: self._cmd_clear_intake(),  # accept either form
            "mode": self._cmd_mode,
            "summary": lambda _a: self._cmd_summary(),
            "sources": lambda _a: self._cmd_sources(),
            "source": self._cmd_source,
        }
        handler = handlers.get(command)
        if handler is None:
            self.error_console.print(f"Unknown command: /{command}. Type /help.", style="red")
            return "continue"
        try:
            return handler(args)
        except KNOWN_PROJECT_ERRORS as exc:
            self._handle_provider_failure(exc)
            return "continue"

    def _handle_provider_failure(
        self,
        exc: Exception,
        *,
        retry: Callable[[], None] | None = None,
    ) -> None:
        """Single funnel for known errors. Always renders the original message,
        then a category + actionable fix + recovery suggestions. For hosted
        failures, optionally offers a one-keystroke fallback to mock and
        retries the original action under the new profile."""
        render_known_error(self.error_console, exc, quiet=False)
        recovery = ProviderRecoveryService().analyze(
            ProviderRecoveryRequest(
                exc=exc,
                config_profile_name=self.state.config_profile_name,
                config_profile=self.ctx.config_profile,
                fallback_profile_name=FALLBACK_PROFILE_NAME,
            )
        )
        if not recovery.is_provider_failure:
            # Non-provider known error (e.g. ConfigProfileError) — no recovery loop.
            return
        failure = recovery.failure
        assert failure is not None
        self.state.last_failure = failure
        self.error_console.print(recovery.reason, style="yellow")
        self.error_console.print(recovery.fix, style="yellow")
        self.error_console.print(recovery.suggestions, style="yellow")

        if not recovery.offer_fallback:
            return
        if not self.confirm_fn(FALLBACK_PROMPT, True):
            self.console.print("[dim]Staying on the current profile.[/dim]")
            return
        if not self._switch_to_fallback_profile():
            return
        if retry is None:
            return
        # Re-run the original action under the mock profile. Any new failure
        # propagates through the same funnel — but with no further fallback
        # offer (we are already on mock).
        try:
            retry()
        except KNOWN_PROJECT_ERRORS as exc2:
            render_known_error(self.error_console, exc2, quiet=False)

    def _switch_to_fallback_profile(self) -> bool:
        """Switch active profile to mock. Returns True on success."""
        config = self.ctx.config
        if config is None or FALLBACK_PROFILE_NAME not in config.profiles:
            self.error_console.print(
                f"No `{FALLBACK_PROFILE_NAME}` profile available — run /setup.",
                style="red",
            )
            return False
        try:
            set_active_profile(FALLBACK_PROFILE_NAME, config.path)
        except KNOWN_PROJECT_ERRORS as exc:
            render_known_error(self.error_console, exc, quiet=False)
            return False
        self.ctx = build_chat_context(
            self.ctx.settings,
            system_profile=self.state.system_profile,
            routing_mode=self.state.routing_mode,
            config_profile_name=FALLBACK_PROFILE_NAME,
        )
        self.state.config_profile_name = FALLBACK_PROFILE_NAME
        # Health changes too — invalidate the cached doctor entry.
        self.state.last_doctor = None
        self.console.print(
            f"[green]Switched to[/green] [cyan]{FALLBACK_PROFILE_NAME}[/cyan]."
        )
        return True

    def _cmd_exit(self) -> Literal["exit"]:
        self.console.print("Goodbye.")
        return "exit"

    def _cmd_help(self) -> Literal["continue", "exit"]:
        render_chat_help(self.console)
        return "continue"

    def _cmd_doctor(self, args: str = "") -> Literal["continue", "exit"]:
        from council.doctor import run_doctor

        live, live_completion = parse_doctor_args(args)
        runner = self.doctor_runner or run_doctor

        self._print_doctor_header(live=live, live_completion=live_completion)

        start = time.perf_counter()
        # Older runners (no live kwargs) still work — we always pass runtime.
        try:
            checks = runner(
                self.ctx.settings,
                runtime=self.ctx.runtime,
                live=live,
                live_completion=live_completion,
            )
        except TypeError:
            # Test stub that ignores kwargs — call without the live flags.
            checks = runner(self.ctx.settings, runtime=self.ctx.runtime)
        elapsed = time.perf_counter() - start

        render_doctor(self.console, checks)

        health, ok, warn, fail = summarize_doctor_checks(checks)
        self.state.last_doctor = DoctorCacheEntry(
            profile_name=self.state.config_profile_name or "(env defaults)",
            health=health,
            ran_at=datetime.now(timezone.utc),
            failed_check_count=fail,
            warn_check_count=warn,
            ok_check_count=ok,
            live=live,
            live_completion=live_completion,
            latency_seconds=elapsed if (live or live_completion) else None,
            summary=_first_failing_message(checks),
        )

        if live or live_completion:
            self.console.print(
                f"[dim]Live validation finished in {elapsed:.2f}s "
                f"(timeout {self.ctx.runtime.timeout_seconds:g}s).[/dim]"
            )

        if health == HEALTH_FAILED and self._active_profile_is_hosted():
            self._print_recovery_block(
                "Recommended recovery:",
                ("/profile mock", "/doctor", "/setup"),
            )
        return "continue"

    def _print_doctor_header(self, *, live: bool, live_completion: bool) -> None:
        settings = self.ctx.settings
        profile = self.ctx.config_profile
        profile_name = self.state.config_profile_name or "(env defaults)"
        provider = settings.llm_provider_name
        mode = settings.llm_mode
        model = self._active_model_label()
        cred_source = self._credential_source_label(profile)
        api_mode = self.ctx.runtime.api_mode
        availability = self._availability_label(profile)
        live_label = (
            "live-completion" if live_completion else ("live" if live else "preflight only")
        )
        lines = [
            f"Profile        : [cyan]{profile_name}[/cyan]",
            f"Provider/mode  : [cyan]{provider}[/cyan] ({mode})",
            f"Model          : [cyan]{model}[/cyan]",
            f"Credential src : [cyan]{cred_source}[/cyan]",
            f"API mode       : [cyan]{api_mode}[/cyan]",
            f"Availability   : [cyan]{availability}[/cyan]",
            f"Mode           : [cyan]{live_label}[/cyan]",
        ]
        self.console.print(
            Panel("\n".join(lines), title="Doctor — active profile", border_style="cyan")
        )

    def _active_profile_is_hosted(self) -> bool:
        profile = self.ctx.config_profile
        if profile is None:
            return False
        if profile.preset and preset_is_hosted(profile.preset):
            return True
        return (profile.mode or "").lower() in {"openai", "openai_compatible"}

    def _print_recovery_block(self, title: str, commands: tuple[str, ...]) -> None:
        body = "\n".join(f"  {cmd}" for cmd in commands)
        self.error_console.print(
            Panel(body, title=title, border_style="yellow")
        )

    def _active_model_label(self) -> str:
        """Pick the model name actually in use given the current mode."""
        settings = self.ctx.settings
        mode = (settings.llm_mode or "").lower()
        if mode == "mock":
            return settings.mock_model or "—"
        if mode == "openai":
            return settings.openai_model or "—"
        return settings.llm_model or "—"

    def _credential_source_label(self, profile: ConfigProfile | None) -> str:
        if profile is not None and profile.preset:
            return credential_source_for_preset(profile.preset, self.ctx.settings)
        mode = self.ctx.settings.llm_mode
        if mode == "mock":
            return "not_required"
        from council.secrets import credential_source

        if mode == "openai":
            return credential_source("OPENAI_API_KEY")
        return credential_source("LLM_API_KEY")

    def _availability_label(self, profile: ConfigProfile | None) -> str:
        if profile is not None and profile.preset:
            return estimate_preset_availability(
                profile.preset, self.ctx.settings
            ).display_availability()
        mode = self.ctx.settings.llm_mode
        if mode == "mock":
            return "available"
        return "live unchecked"

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
        model = self._active_model_label()
        cred_source = self._credential_source_label(profile)
        last_run = self.state.last_run_id or "—"
        doctor = self.state.last_doctor
        if doctor is None:
            health = HEALTH_UNCHECKED
            health_detail = "run `/doctor` to populate"
            ran_at = "—"
        else:
            health = doctor.health
            stale = (
                "" if doctor.profile_name == config_label
                else f" (cached for {doctor.profile_name!r}; profile changed since)"
            )
            health_detail = (
                f"{doctor.ok_check_count} ok / {doctor.warn_check_count} warn / "
                f"{doctor.failed_check_count} fail — {doctor.summary}{stale}"
            )
            ran_at = doctor.ran_at.strftime("%Y-%m-%d %H:%M:%SZ")
        lines = [
            f"Config profile : [cyan]{config_label}[/cyan]",
            f"System profile : [cyan]{self.state.system_profile}[/cyan]",
            f"Routing mode   : [cyan]{self.state.routing_mode}[/cyan]",
            f"Operating mode : [cyan]{self.state.operational_profile}[/cyan]",
            f"Provider/mode  : [cyan]{provider}[/cyan] ({mode})",
            f"Preset         : [cyan]{preset or '—'}[/cyan]",
            f"Model          : [cyan]{model or '—'}[/cyan]",
            f"Credential src : [cyan]{cred_source}[/cyan]",
            f"API mode       : [cyan]{self.ctx.runtime.api_mode}[/cyan]",
            f"Health         : [{_health_style(health)}]{health}[/{_health_style(health)}]"
            f"  ({health_detail})",
            f"Last doctor    : [cyan]{ran_at}[/cyan]",
            f"Last run id    : [cyan]{last_run}[/cyan]",
            f"Active sources : [cyan]{', '.join(self.state.active_source_pack_ids) or '—'}[/cyan]",
        ]
        if self.state.operational_fallback:
            lines.append(f"Mode note      : [yellow]{self.state.operational_fallback}[/yellow]")
        if self.state.last_failure is not None:
            lines.append(
                f"Last failure   : [yellow]{self.state.last_failure.category}[/yellow] — "
                f"{self.state.last_failure.summary}"
            )
        self.console.print(Panel("\n".join(lines), title="Chat Status", border_style="blue"))
        return "continue"

    def _cmd_pack(self, args: str) -> Literal["continue", "exit"]:
        tokens = args.split()
        override = False
        positional: list[str] = []
        for token in tokens:
            if token in ("--allow-unapproved", "--allow-unapproved-pack"):
                override = True
            else:
                positional.append(token)
        run_id = resolve_run_id_arg(self.state, " ".join(positional))
        if not run_id:
            self.error_console.print("No run ID. Run council first or pass /pack RUN_ID.", style="red")
            return "continue"
        try:
            result = self._run_service().load(RunQuery(run_id))
        except RunNotFoundError as exc:
            self.error_console.print(str(exc), style="red")
            return "continue"
        if not is_pack_allowed(result.review, override=override):
            self._print_pack_gate_hint()
            return "continue"
        if not self.confirm_fn("Generate implementation pack for this run?", False):
            self.console.print("[dim]Pack generation cancelled.[/dim]")
            return "continue"
        try:
            pack = self._pack_service().generate(
                PackRequest(run_id=run_id, allow_unapproved=override)
            )
            self.console.print("[green]Implementation pack:[/green]")
            for path in pack.paths:
                self.console.print(f"  {path}")
        except (PackGenerationBlockedError, VerdictQualityError) as exc:
            render_known_error(self.error_console, exc, quiet=False)
        return "continue"

    def _print_pack_gate_hint(self) -> None:
        body = "\n".join(PACK_GATE_HINT_LINES)
        self.error_console.print(
            Panel(body, title="Pack blocked", border_style="yellow")
        )

    # --- review lifecycle commands (Slice 5.9) -----------------------------

    def _resolve_review_target(self, args: str) -> tuple[str | None, str]:
        """Split `<run_id|last> [note...]` into (run_id, note).

        Returns (None, '') and prints a usage error when no run is available.
        """
        parts = args.split(maxsplit=1)
        run_id_token = parts[0] if parts else ""
        note = parts[1].strip() if len(parts) > 1 else ""
        run_id = resolve_run_id_arg(self.state, run_id_token)
        if not run_id:
            self.error_console.print(
                "No run ID. Run council first or pass <run_id|last>.", style="red"
            )
            return None, ""
        return run_id, note

    def _cmd_approve(self, args: str) -> Literal["continue", "exit"]:
        run_id, note = self._resolve_review_target(args)
        if not run_id:
            return "continue"
        try:
            updated = self._review_service().approve(
                ReviewRequest(run_id=run_id, actor=resolve_actor(), note=note)
            )
        except (RunNotFoundError, ReviewTransitionError) as exc:
            self.error_console.print(str(exc), style="red")
            return "continue"
        self.console.print(
            f"[green]Approved[/green] {run_id} by [cyan]{updated.review.approved_by}[/cyan]."
        )
        parent = updated.review.is_revision_of
        if parent:
            self.console.print(
                f"[magenta]Superseded parent[/magenta] {parent}."
            )
        return "continue"

    def _cmd_reject(self, args: str) -> Literal["continue", "exit"]:
        run_id, note = self._resolve_review_target(args)
        if not run_id:
            return "continue"
        if not note:
            self.error_console.print(
                "A reason is required. Usage: /reject <run_id|last> <reason>", style="red"
            )
            return "continue"
        try:
            updated = self._review_service().reject(
                RejectRequest(run_id=run_id, actor=resolve_actor(), reason=note)
            )
        except (RunNotFoundError, ReviewTransitionError) as exc:
            self.error_console.print(str(exc), style="red")
            return "continue"
        self.console.print(
            f"[red]Rejected[/red] {run_id} by [cyan]{updated.review.rejected_by}[/cyan]."
        )
        return "continue"

    def _cmd_archive(self, args: str) -> Literal["continue", "exit"]:
        run_id, note = self._resolve_review_target(args)
        if not run_id:
            return "continue"
        try:
            updated = self._review_service().archive(
                ReviewRequest(run_id=run_id, actor=resolve_actor(), note=note)
            )
        except (RunNotFoundError, ReviewTransitionError) as exc:
            self.error_console.print(str(exc), style="red")
            return "continue"
        self.console.print(
            f"[dim]Archived[/dim] {run_id} (status: {updated.review.status.value})."
        )
        return "continue"

    def _cmd_review(self, args: str) -> Literal["continue", "exit"]:
        run_id, _ = self._resolve_review_target(args)
        if not run_id:
            return "continue"
        try:
            result = self._review_service().load(run_id)
        except RunNotFoundError as exc:
            self.error_console.print(str(exc), style="red")
            return "continue"
        render_review(self.console, run_id, result)
        return "continue"

    def _cmd_revise(self, args: str) -> Literal["continue", "exit"]:
        run_id, follow_up_question = self._resolve_review_target(args)
        if not run_id:
            return "continue"
        try:
            parent = self._review_service().load(run_id)
        except RunNotFoundError as exc:
            self.error_console.print(str(exc), style="red")
            return "continue"
        # Install the parent run as the active decision context so the next
        # natural-language council run is automatically contextual.
        parent_context = summarize_for_context(parent)
        self.state.current_context = parent_context
        self.state.current_context_run_id = parent.dossier.run_id
        thread = parent.decision_thread
        self.state.current_thread_id = (
            thread.thread_id if thread is not None else parent.dossier.run_id
        )
        # If the user typed a question inline ("/revise last make it cheaper"),
        # run it now and mark the new run as a revision of the parent.
        if not follow_up_question:
            self.console.print(
                f"[green]Revision context loaded[/green] from [cyan]{parent.dossier.run_id}[/cyan]. "
                "Type a follow-up question to run the revision."
            )
            return "continue"
        if not self.confirm_fn("Run council on this revision?", True):
            self.console.print("[dim]Revision cancelled.[/dim]")
            return "continue"
        try:
            self._run_council(follow_up_question)
        except KNOWN_PROJECT_ERRORS as exc:
            self._handle_provider_failure(
                exc,
                retry=lambda: self._run_council(follow_up_question),
            )
            return "continue"
        new_run_id = self.state.last_run_id
        if new_run_id and new_run_id != parent.dossier.run_id:
            try:
                self._review_service().mark_revision(
                    RevisionRequest(
                        run_id=new_run_id,
                        parent_run_id=parent.dossier.run_id,
                        actor=resolve_actor(),
                        note="Created via /revise.",
                    )
                )
                self.console.print(
                    f"[cyan]Marked {new_run_id} as a revision of {parent.dossier.run_id}.[/cyan] "
                    "Approve it to supersede the parent."
                )
            except (RunNotFoundError, ReviewTransitionError) as exc:
                self.error_console.print(str(exc), style="red")
        return "continue"

    def _cmd_run(self, question: str) -> Literal["continue", "exit"]:
        if not question:
            self.error_console.print("Usage: /run <question>", style="red")
            return "continue"
        plan = resolve_operational_profile(
            requested=self.state.operational_profile,
            settings=self.ctx.settings,
        )
        self.state.operational_profile = plan.effective
        self.state.operational_fallback = plan.fallback_summary
        if plan.fallback_summary:
            self.console.print(f"[yellow]{plan.fallback_summary}[/yellow]")
        debate_rounds = resolve_debate_rounds_with_profile(
            self.ctx.config_profile,
            cli_debate_rounds=None,
            runtime=self.ctx.runtime,
        )
        source_payload = self._source_service().build_context(
            source_pack_ids=self.state.active_source_pack_ids,
            question=question,
            intake_summary=format_intake_summary(self.state.last_intake) if self.state.last_intake else "",
            decision_mode=self.state.current_mode.value if self.state.current_mode else "",
        )
        if source_payload.summary:
            self.console.print(_human_source_recap(source_payload))
        execution = self._council_service().run_standard(
            CouncilRequest(
                question=question,
                settings=apply_profile_to_settings(self.ctx.settings, plan=plan),
                debate_rounds=debate_rounds,
                runtime=self.ctx.runtime,
                progress=NullProgressReporter(),
                source_pack_ids=source_payload.source_pack_ids,
                source_context_summary=source_payload.summary,
                source_relevance=source_payload.relevance,
                source_excluded_files=source_payload.excluded_files,
                source_context_warnings=source_payload.warnings,
            )
        )
        result = execution.result
        md_path = execution.md_path
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
        # Pass an explicit retry so a hosted-provider failure can trigger
        # the Slice 5.7 fallback-to-mock loop just like the natural-input
        # path does.
        try:
            self._run_council(question)
        except KNOWN_PROJECT_ERRORS as exc:
            self._handle_provider_failure(
                exc, retry=lambda: self._run_council(question)
            )
        return "continue"

    def _run_council(self, question: str, *, intake: DecisionIntake | None = None) -> None:
        plan = resolve_operational_profile(
            requested=self.state.operational_profile,
            settings=self.ctx.settings,
        )
        self.state.operational_profile = plan.effective
        self.state.operational_fallback = plan.fallback_summary
        if plan.fallback_summary:
            self.console.print(f"[yellow]{plan.fallback_summary}[/yellow]")
        context = self.state.current_context
        if context is not None:
            self.console.print(
                f"[cyan]{CONTEXT_BLOCK_HEADER} (parent {context.run_id})[/cyan]"
            )
        if intake is not None:
            self.console.print(
                "[cyan]I used your goal, constraints, and preferred decision style to guide this analysis.[/cyan]"
            )
        source_payload = self._source_service().build_context(
            source_pack_ids=self.state.active_source_pack_ids,
            question=question,
            intake_summary=format_intake_summary(intake) if intake is not None else "",
            decision_mode=self.state.current_mode.value if self.state.current_mode else "",
        )
        if source_payload.summary:
            self.console.print(_human_source_recap(source_payload))
        execution = self._council_service().run_chat_council(
            ChatCouncilRequest(
                question=question,
                routing_mode=plan.routing_mode if self.state.routing_mode == DEFAULT_ROUTING_MODE else self.state.routing_mode,
                settings=self.ctx.settings,
                runtime=self.ctx.runtime,
                config_profile=self.ctx.config_profile,
                parent_context=context,
                thread_id=self.state.current_thread_id,
                intake=intake,
                council_runner=self.council_runner,
                source_pack_ids=source_payload.source_pack_ids,
                source_context_summary=source_payload.summary,
                source_relevance=source_payload.relevance,
                source_excluded_files=source_payload.excluded_files,
                source_context_warnings=source_payload.warnings,
                council_presets=plan.council_presets,
            )
        )
        session = execution.session

        # Decision starts as draft. Persist first so /approve has something
        # to load from disk if the user accepts the inline approval prompt.
        md_path = execution.md_path
        self._record_session_memory(question, session.result, pack_paths=[])
        # Slice 6.0: human-first result. Show the short form by default;
        # the full panel is one confirm away.
        render_chat_verdict_short(self.console, session.result)
        if self.confirm_fn(SHOW_FULL_BREAKDOWN_PROMPT, False):
            render_chat_verdict(self.console, session.result)
        self.console.print(f"[green]Saved:[/green] {md_path}")
        self.console.print(
            f"[dim]Decision state:[/dim] [{_lifecycle_style(LifecycleState.DRAFT.value)}]"
            f"{LifecycleState.DRAFT.value}[/{_lifecycle_style(LifecycleState.DRAFT.value)}]"
        )

        approved = False
        approved_result = session.result
        if self.confirm_fn(APPROVE_NOW_PROMPT, False):
            try:
                updated = self._review_service().approve(
                    ReviewRequest(
                        run_id=session.result.dossier.run_id,
                        actor=resolve_actor(),
                        note="Approved inline after council run.",
                    )
                )
                approved = updated.review.status == LifecycleState.APPROVED
                approved_result = updated
                self.console.print(
                    f"[green]Approved by[/green] [cyan]{updated.review.approved_by}[/cyan]."
                )
            except ReviewTransitionError as exc:
                self.error_console.print(str(exc), style="red")
            except KNOWN_PROJECT_ERRORS as exc:
                self.error_console.print(str(exc), style="red")

        pack_paths: list[Path] = []
        if self.confirm_fn("Create implementation pack?", False):
            if not approved:
                self._print_pack_gate_hint()
            else:
                try:
                    pack = self._pack_service().generate_for_result(
                        approved_result,
                        resave_run_with_pack_paths=True,
                    )
                    pack_paths = pack.paths
                    self.console.print("[green]Implementation pack written.[/green]")
                except (PackGenerationBlockedError, VerdictQualityError) as exc:
                    render_known_error(self.error_console, exc, quiet=False)
        if pack_paths:
            self.state.last_pack_paths = list(pack_paths)

    # --- session memory + decision-thread helpers (Slice 5.8) ---------------

    def _record_session_memory(
        self,
        question: str,
        result: CouncilRunResult,
        pack_paths: list[Path],
    ) -> None:
        """Refresh the conversational memory after a successful council run."""
        dossier = result.dossier
        thread = result.decision_thread
        self.state.last_run_id = dossier.run_id
        self.state.last_question = question
        self.state.last_direct_answer = (
            dossier.direct_answer.strip()
            or dossier.recommendation.split("\n", 1)[0].strip()
        )
        self.state.last_decision_type = dossier.decision_type.value
        self.state.last_pack_paths = list(pack_paths)
        self.state.last_profile = self.state.config_profile_name
        self.state.last_routing_mode = self.state.routing_mode
        if thread is not None:
            # We were given a context; keep the thread anchor stable.
            self.state.current_thread_id = thread.thread_id
        elif self.state.current_thread_id is None:
            # First standalone run in this chat — anchor a new thread.
            self.state.current_thread_id = dossier.run_id

    def _maybe_attach_follow_up_context(self, question: str) -> None:
        """If the user typed a follow-up phrase and we have a last run, ask."""
        if self.state.current_context is not None:
            return
        if not self.state.last_run_id:
            return
        if not looks_like_follow_up(question):
            return
        if not self.confirm_fn(
            FOLLOW_UP_PROMPT_TEMPLATE.format(run_id=self.state.last_run_id),
            True,
        ):
            return
        self._load_context_from_run(self.state.last_run_id)

    def _load_context_from_run(self, run_id: str) -> bool:
        """Load `run_id` from disk and install it as the active context."""
        try:
            result = load_run_result(self.ctx.settings.runs_dir, run_id)
        except RunNotFoundError as exc:
            self.error_console.print(str(exc), style="red")
            return False
        context = summarize_for_context(result)
        thread = result.decision_thread
        self.state.current_context = context
        self.state.current_context_run_id = result.dossier.run_id
        self.state.current_thread_id = (
            thread.thread_id if thread is not None else result.dossier.run_id
        )
        return True

    def _cmd_context(self) -> Literal["continue", "exit"]:
        context = self.state.current_context
        if context is None:
            self.console.print(
                "No active decision context. Use [bold]/use RUN_ID[/bold] or accept "
                "the follow-up prompt after a council run."
            )
            return "continue"
        lines = [
            f"Run ID    : [cyan]{context.run_id}[/cyan]",
            f"Thread ID : [cyan]{self.state.current_thread_id or context.run_id}[/cyan]",
            f"Question  : {context.decision_question}",
            f"Decision  : [cyan]{context.decision_type.value}[/cyan]",
            f"Direct    : {context.direct_answer}",
        ]
        if context.next_actions:
            lines.append("Next actions:")
            lines.extend(f"  - {item}" for item in context.next_actions)
        if context.do_not_do:
            lines.append("Do not do:")
            lines.extend(f"  - {item}" for item in context.do_not_do)
        if context.approval_gate:
            lines.append(f"Approval  : {context.approval_gate}")
        if context.evidence_gaps:
            lines.append("Evidence gaps:")
            lines.extend(f"  - {item}" for item in context.evidence_gaps)
        self.console.print(
            Panel("\n".join(lines), title="Active Decision Context", border_style="blue")
        )
        return "continue"

    def _cmd_forget(self) -> Literal["continue", "exit"]:
        had_context = self.state.current_context is not None
        # Clear conversational memory but keep doctor cache and profile state.
        self.state.current_context = None
        self.state.current_context_run_id = None
        self.state.current_thread_id = None
        self.state.last_run_id = None
        self.state.last_question = None
        self.state.last_direct_answer = None
        self.state.last_decision_type = None
        self.state.last_pack_paths = []
        if had_context:
            self.console.print("[green]Cleared active decision context and session memory.[/green]")
        else:
            self.console.print("[dim]Cleared session memory (no context was active).[/dim]")
        return "continue"

    def _cmd_use(self, args: str) -> Literal["continue", "exit"]:
        run_id = args.strip()
        if not run_id:
            self.error_console.print("Usage: /use RUN_ID", style="red")
            return "continue"
        if self._load_context_from_run(run_id):
            ctx = self.state.current_context
            assert ctx is not None
            self.console.print(
                f"[green]Loaded decision context from[/green] [cyan]{ctx.run_id}[/cyan] "
                f"({ctx.decision_type.value})."
            )
        return "continue"

    def _cmd_thread(self) -> Literal["continue", "exit"]:
        thread_id = self.state.current_thread_id
        if thread_id is None:
            self.console.print(
                "No active thread. Run council or `/use RUN_ID` to start a thread."
            )
            return "continue"
        runs = self._run_service().thread_runs(thread_id)
        if not runs:
            self.console.print(
                f"No persisted runs found for thread [cyan]{thread_id}[/cyan]."
            )
            return "continue"
        lines = [f"Thread ID: [cyan]{thread_id}[/cyan] ({len(runs)} run(s))"]
        for index, item in enumerate(runs, start=1):
            markers = _thread_markers(item, thread_id=thread_id)
            preview = item.decision_preview[:60].replace("\n", " ")
            lines.append(
                f" {index}. {' '.join(markers)} {item.run_id} — "
                f"{item.timestamp.strftime('%Y-%m-%d %H:%M')} — {preview}"
            )
        self.console.print(
            Panel("\n".join(lines), title="Decision Thread", border_style="cyan")
        )
        return "continue"

    # --- Slice 6.0: intake commands ---------------------------------------

    def _cmd_intake(self) -> Literal["continue", "exit"]:
        if self.state.current_intake is None:
            # Start a fresh intake; goal will be the next thing the user types.
            self.state.current_intake = empty_intake()
            self.console.print(f"[dim]{INTAKE_OPENING_LINE}[/dim]")
            return self._advance_intake()
        self._render_intake_summary(self.state.current_intake)
        if self.state.current_intake_field:
            question = question_for_field(self.state.current_intake_field)
            if question is not None:
                self.console.print(
                    "[dim]Currently waiting on:[/dim]",
                )
                self.console.print(
                    question.prompt, highlight=False, markup=False
                )
        return "continue"

    def _cmd_summary(self) -> Literal["continue", "exit"]:
        intake = self.state.current_intake or self.state.last_intake
        if intake is None:
            self.console.print(
                "No intake yet. Type a goal naturally, or use /intake to start."
            )
            return "continue"
        self._render_intake_summary(intake)
        return "continue"

    def _cmd_clear_intake(self) -> Literal["continue", "exit"]:
        had_intake = self.state.current_intake is not None
        self._clear_intake_state()
        if had_intake:
            self.console.print(
                "[green]Cleared the current intake draft.[/green]"
            )
        else:
            self.console.print("[dim]No intake to clear.[/dim]")
        return "continue"

    def _cmd_edit(self, args: str) -> Literal["continue", "exit"]:
        intake = self.state.current_intake or self.state.last_intake
        if intake is None:
            self.error_console.print(
                "No intake to edit. Type a goal naturally to start one.",
                style="red",
            )
            return "continue"
        # If editing the snapshot of the last completed intake, lift it
        # back into current so the answer flow updates it in place.
        if self.state.current_intake is None and self.state.last_intake is not None:
            self.state.current_intake = self.state.last_intake
        field = args.strip().lower()
        if not field:
            field = self._prompt_for_field_to_edit() or ""
        if not field:
            return "continue"
        if field not in editable_fields():
            self.error_console.print(
                f"Unknown field {field!r}. Available: {', '.join(editable_fields())}",
                style="red",
            )
            return "continue"
        self.state.current_intake_field = field
        question = question_for_field(field)
        if question is not None:
            self.console.print(question.prompt, highlight=False, markup=False)
        return "continue"

    def _cmd_mode(self, args: str) -> Literal["continue", "exit"]:
        token = args.strip()
        if not token:
            current = (
                self.state.current_mode
                or (self.state.current_intake.preferred_mode if self.state.current_intake else None)
                or (self.state.last_intake.preferred_mode if self.state.last_intake else None)
            )
            if current is not None:
                profile = mode_profile(current)
                self.console.print(
                    f"Current mode: [cyan]{profile.label}[/cyan] "
                    f"(routing={profile.routing_mode}, debate={profile.debate_rounds}, "
                    f"{profile.slot_emphasis})"
                )
            else:
                self.console.print("No decision mode set.")
            self.console.print(mode_picker_prompt(), highlight=False, markup=False)
            return "continue"
        parsed = parse_mode(token)
        if parsed is None:
            self.error_console.print(
                "Unknown mode. Pick a number 1–6 or a keyword like 'deep', 'risk', 'plan'.",
                style="red",
            )
            return "continue"
        self.state.current_mode = parsed
        if self.state.current_intake is not None:
            self.state.current_intake = self.state.current_intake.model_copy(
                update={"preferred_mode": parsed}
            )
        profile = mode_profile(parsed)
        self.console.print(
            f"[green]Mode set to[/green] [cyan]{profile.label}[/cyan]: {profile.description}"
        )
        return "continue"

    def _cmd_sources(self) -> Literal["continue", "exit"]:
        render_sources_list(self.console, self._source_service().list_packs())
        if self.state.active_source_pack_ids:
            self.console.print(
                f"Active source packs: {', '.join(self.state.active_source_pack_ids)}"
            )
        return "continue"

    def _cmd_source(self, args: str) -> Literal["continue", "exit"]:
        tokens = args.split(maxsplit=2)
        if not tokens:
            self.error_console.print(
                "Usage: /source scan <path> | use <id> | clear | show <id> | query <question>",
                style="red",
            )
            return "continue"
        sub = tokens[0].lower()
        service = self._source_service()
        if sub == "scan":
            if len(tokens) < 2:
                self.error_console.print("Usage: /source scan <path>", style="red")
                return "continue"
            pack = service.scan_and_save(Path(tokens[1]))
            self.state.active_source_pack_ids = [pack.source_pack_id]
            render_sources_show(self.console, pack)
            self.console.print(f"[green]Got it. Active source pack:[/green] {pack.source_pack_id}")
            return "continue"
        if sub == "use":
            if len(tokens) < 2:
                self.error_console.print("Usage: /source use <source_pack_id>", style="red")
                return "continue"
            pack = service.load(tokens[1])
            self.state.active_source_pack_ids = [pack.source_pack_id]
            self.console.print(f"[green]Using source pack:[/green] {pack.source_pack_id}")
            return "continue"
        if sub == "clear":
            self.state.active_source_pack_ids = []
            self.console.print("[dim]Cleared source context for upcoming runs.[/dim]")
            return "continue"
        if sub == "show":
            if len(tokens) < 2:
                self.error_console.print("Usage: /source show <source_pack_id>", style="red")
                return "continue"
            render_sources_show(self.console, service.load(tokens[1]))
            return "continue"
        if sub == "query":
            question = args[len(tokens[0]) :].strip()
            if not question:
                self.error_console.print("Usage: /source query <question>", style="red")
                return "continue"
            if not self.state.active_source_pack_ids:
                self.error_console.print("No active source pack. Use /source use <id> first.", style="red")
                return "continue"
            for source_pack_id in self.state.active_source_pack_ids:
                payload = service.build_context(
                    source_pack_ids=[source_pack_id],
                    question=question,
                )
                render_sources_query(self.console, source_pack_id, payload)
            return "continue"
        self.error_console.print(
            "Usage: /source scan <path> | use <id> | clear | show <id> | query <question>",
            style="red",
        )
        return "continue"

    def run_loop(self) -> int:
        config_label = self.state.config_profile_name or resolve_active_config_profile_name(
            self.ctx.config
        )
        render_chat_welcome(
            self.console,
            config_profile_name=config_label,
            system_profile=self.state.system_profile,
            routing_mode=self.state.routing_mode,
            operational_profile=self.state.operational_profile,
            operational_note=self.state.operational_fallback,
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
    operational_profile: str | None = None,
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
    operational_plan = resolve_operational_profile(
        requested=operational_profile,
        settings=ctx.settings,
    )
    state = ChatSessionState(
        system_profile=system_profile,
        routing_mode=operational_plan.routing_mode if routing_mode == DEFAULT_ROUTING_MODE else routing_mode,
        config_profile_name=config_profile_name or resolve_profile_name(config=ctx.config),
        operational_profile=operational_plan.effective,
        operational_fallback=operational_plan.fallback_summary,
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
