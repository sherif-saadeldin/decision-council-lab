from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from getpass import getpass
from pathlib import Path
from typing import Protocol

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from council.config import Settings
from council.config_profiles import (
    ConfigProfile,
    config_path,
    init_config_file,
    load_config_file,
    upsert_profile,
)
from council.credentials import is_ollama_openai_compatible
from council.doctor import run_doctor
from council.model_presets import (
    FREE_HOSTED_PRESET_NAMES,
    MODEL_PRESETS,
    OLLAMA_BASE_URL,
    OLLAMA_PRESET_NAMES,
    get_preset,
)
from council.runtime import RuntimeOptions
from council.secrets import credential_source, set_keyring_secret, validate_secret_name
from council.smoke import SmokeReport, SmokeRequest, run_smoke

SUPPORTED_NON_INTERACTIVE_PROFILES: frozenset[str] = frozenset(
    {"mock", "ollama-local", "openai-mini", "openrouter-sonnet"},
)

SETUP_SMOKE_TIMEOUT_SECONDS = 60.0
SETUP_SMOKE_DEBATE_ROUNDS = 0


class ProviderKind(str, Enum):
    MOCK = "mock"
    OLLAMA = "ollama"
    OPENAI = "openai"
    HOSTED = "hosted"
    CANCEL = "cancel"


@dataclass(frozen=True)
class SetupPlan:
    profile_name: str
    profile: ConfigProfile
    smoke_preset: str | None
    secret_env: str | None
    needs_secret: bool
    smoke_default: bool
    doctor_default: bool = True


@dataclass(frozen=True)
class SetupResult:
    cancelled: bool = False
    exit_code: int = 0
    active_profile: str | None = None
    config_path: Path | None = None
    message: str | None = None


class SetupPrompter(Protocol):
    def print(self, message: str) -> None: ...

    def choose(self, title: str, options: list[str]) -> int:
        """Return 1-based option index, or 0 to cancel."""

    def confirm(self, message: str, *, default: bool = True) -> bool: ...

    def text(self, prompt: str, *, default: str = "") -> str: ...


@dataclass
class ScriptedPrompter:
    """Test prompter with scripted responses."""

    script: list[object]
    _index: int = 0
    lines: list[str] = field(default_factory=list)

    def print(self, message: str) -> None:
        self.lines.append(message)

    def _next(self) -> object:
        if self._index >= len(self.script):
            msg = "ScriptedPrompter ran out of scripted answers."
            raise RuntimeError(msg)
        value = self.script[self._index]
        self._index += 1
        return value

    def choose(self, title: str, options: list[str]) -> int:
        value = self._next()
        if isinstance(value, int):
            return value
        return int(str(value))

    def confirm(self, message: str, *, default: bool = True) -> bool:
        value = self._next()
        return bool(value)

    def text(self, prompt: str, *, default: str = "") -> str:
        value = self._next()
        return str(value) if value != "" else default


class RichSetupPrompter:
    def __init__(self, console: Console) -> None:
        self._console = console

    def print(self, message: str) -> None:
        self._console.print(message)

    def choose(self, title: str, options: list[str]) -> int:
        self._console.print(f"\n[bold]{title}[/bold]")
        for index, option in enumerate(options, start=1):
            self._console.print(f"  {index}. {option}")
        self._console.print("  0. Cancel")
        while True:
            raw = Prompt.ask("Choice", default="1")
            if raw.strip() == "0":
                return 0
            try:
                choice = int(raw)
            except ValueError:
                self._console.print("[yellow]Enter a number from the list.[/yellow]")
                continue
            if 1 <= choice <= len(options):
                return choice
            self._console.print("[yellow]Invalid choice.[/yellow]")

    def confirm(self, message: str, *, default: bool = True) -> bool:
        return Confirm.ask(message, default=default)

    def text(self, prompt: str, *, default: str = "") -> str:
        return Prompt.ask(prompt, default=default)


def run_setup(
    *,
    interactive: bool,
    profile_name: str | None = None,
    config_path_override: Path | None = None,
    prompter: SetupPrompter | None = None,
    console: Console | None = None,
    store_secret_fn: Callable[[str, str], None] | None = None,
    secret_prompt_fn: Callable[[str], str] | None = None,
    doctor_fn: Callable[..., list] | None = None,
    smoke_fn: Callable[..., object] | None = None,
) -> SetupResult:
    target = config_path(config_path_override)
    if not interactive:
        return _run_non_interactive(
            profile_name,
            config_path_override=target,
            console=console,
            store_secret_fn=store_secret_fn,
            secret_prompt_fn=secret_prompt_fn,
            doctor_fn=doctor_fn,
            smoke_fn=smoke_fn,
        )

    rich_console = console or Console()
    prompt = prompter or RichSetupPrompter(rich_console)
    return _run_interactive(
        target,
        prompt,
        console=rich_console,
        store_secret_fn=store_secret_fn,
        secret_prompt_fn=secret_prompt_fn,
        doctor_fn=doctor_fn,
        smoke_fn=smoke_fn,
    )


def _run_non_interactive(
    profile_name: str | None,
    *,
    config_path_override: Path,
    console: Console | None,
    store_secret_fn: Callable[[str, str], None] | None,
    secret_prompt_fn: Callable[[str], str] | None,
    doctor_fn: Callable[..., list] | None,
    smoke_fn: Callable[..., object] | None,
) -> SetupResult:
    if not profile_name:
        return SetupResult(
            cancelled=True,
            exit_code=2,
            message=(
                "Non-interactive setup requires --profile. "
                f"Supported: {', '.join(sorted(SUPPORTED_NON_INTERACTIVE_PROFILES))}"
            ),
        )
    key = profile_name.strip()
    if key not in SUPPORTED_NON_INTERACTIVE_PROFILES:
        return SetupResult(
            cancelled=True,
            exit_code=2,
            message=(
                f"Unsupported profile {key!r} for --non-interactive. "
                f"Supported: {', '.join(sorted(SUPPORTED_NON_INTERACTIVE_PROFILES))}"
            ),
        )
    plan = non_interactive_plan(key)
    return _execute_plan(
        plan,
        config_path_override=config_path_override,
        console=console,
        prompter=None,
        store_secret_fn=store_secret_fn,
        secret_prompt_fn=secret_prompt_fn,
        doctor_fn=doctor_fn,
        smoke_fn=smoke_fn,
        interactive=False,
    )


def _run_interactive(
    target: Path,
    prompter: SetupPrompter,
    *,
    console: Console,
    store_secret_fn: Callable[[str, str], None] | None,
    secret_prompt_fn: Callable[[str], str] | None,
    doctor_fn: Callable[..., list] | None,
    smoke_fn: Callable[..., object] | None,
) -> SetupResult:
    _show_welcome(console)
    provider_choice = prompter.choose(
        "Choose a provider",
        [
            "Mock only (no API key)",
            "Local Ollama",
            "OpenAI direct",
            "OpenRouter / other hosted (OpenAI-compatible)",
            "Skip / cancel",
        ],
    )
    if provider_choice == 0 or provider_choice == 5:
        prompter.print("\nSetup cancelled.")
        return SetupResult(cancelled=True, exit_code=0, message="Setup cancelled.")

    kind = (
        ProviderKind.MOCK,
        ProviderKind.OLLAMA,
        ProviderKind.OPENAI,
        ProviderKind.HOSTED,
    )[provider_choice - 1]

    plan = _plan_from_interactive(kind, prompter)
    if plan is None:
        prompter.print("\nSetup cancelled.")
        return SetupResult(cancelled=True, exit_code=0, message="Setup cancelled.")

    return _execute_plan(
        plan,
        config_path_override=target,
        console=console,
        prompter=prompter,
        store_secret_fn=store_secret_fn,
        secret_prompt_fn=secret_prompt_fn,
        doctor_fn=doctor_fn,
        smoke_fn=smoke_fn,
        interactive=True,
    )


def _show_welcome(console: Console) -> None:
    console.print(
        Panel.fit(
            "[bold]Decision Council — Setup Wizard[/bold]\n\n"
            "This configures your local project profile in "
            "[cyan].dcouncil/config.toml[/cyan].\n"
            "API keys are stored in the OS keyring or environment — "
            "[bold]never[/bold] in config.toml.",
            border_style="cyan",
        )
    )


def _plan_from_interactive(kind: ProviderKind, prompter: SetupPrompter) -> SetupPlan | None:
    if kind == ProviderKind.MOCK:
        return _plan_mock(prompter)
    if kind == ProviderKind.OLLAMA:
        return _plan_ollama(prompter)
    if kind == ProviderKind.OPENAI:
        return _plan_openai(prompter)
    if kind == ProviderKind.HOSTED:
        return _plan_hosted(prompter)
    return None


def _plan_mock(prompter: SetupPrompter) -> SetupPlan | None:
    profile_name = prompter.text("Profile name", default="mock")
    if not profile_name.strip():
        return None
    return non_interactive_plan("mock", profile_name=profile_name.strip())


def _plan_openai(prompter: SetupPrompter) -> SetupPlan | None:
    preset_choice = prompter.choose("OpenAI preset", ["openai-mini"])
    if preset_choice == 0:
        return None
    profile_name = prompter.text("Profile name", default="openai-mini")
    if not profile_name.strip():
        return None
    return non_interactive_plan("openai-mini", profile_name=profile_name.strip())


def _plan_hosted(prompter: SetupPrompter) -> SetupPlan | None:
    options = [
        "openrouter-sonnet",
        "openrouter-gemini",
        "openrouter-deepseek",
        "openrouter-qwen",
        *FREE_HOSTED_PRESET_NAMES,
    ]
    preset_choice = prompter.choose("Hosted preset (verify model IDs in provider console)", options)
    if preset_choice == 0:
        return None
    preset_name = options[preset_choice - 1]
    default_profile = "openrouter-sonnet" if preset_name.startswith("openrouter") else preset_name
    profile_name = prompter.text("Profile name", default=default_profile)
    if not profile_name.strip():
        return None
    return _plan_from_preset(
        profile_name.strip(),
        preset_name,
        needs_secret=True,
        secret_env="LLM_API_KEY",
        smoke_default=False,
    )


def _plan_ollama(prompter: SetupPrompter) -> SetupPlan | None:
    options = [*OLLAMA_PRESET_NAMES, "Custom model name (manual)"]
    preset_choice = prompter.choose(
        "Ollama preset (model must match `ollama list` exactly)",
        options,
    )
    if preset_choice == 0:
        return None
    profile_name = prompter.text("Profile name", default="ollama-local")
    if not profile_name.strip():
        return None
    profile_name = profile_name.strip()
    if preset_choice == len(options):
        prompter.print(
            "[yellow]Warning:[/yellow] The model name must match `ollama list` exactly."
        )
        model = prompter.text("Ollama model name", default="qwen3.5:9b")
        if not model.strip():
            return None
        profile = ConfigProfile(
            name=profile_name,
            mode="openai_compatible",
            provider_name="ollama",
            base_url=OLLAMA_BASE_URL,
            model=model.strip(),
            timeout_seconds=180,
            max_retries=0,
            debate_rounds=0,
        )
        return SetupPlan(
            profile_name=profile_name,
            profile=profile,
            smoke_preset="ollama-qwen",
            secret_env=None,
            needs_secret=False,
            smoke_default=True,
        )
    preset_name = options[preset_choice - 1]
    return _plan_from_preset(
        profile_name,
        preset_name,
        needs_secret=False,
        secret_env=None,
        smoke_default=True,
    )


def _plan_from_preset(
    profile_name: str,
    preset_name: str,
    *,
    needs_secret: bool,
    secret_env: str | None,
    smoke_default: bool,
) -> SetupPlan:
    preset = get_preset(preset_name)
    if preset.llm_mode == "mock":
        profile = ConfigProfile(
            name=profile_name,
            mode="mock",
            provider_name="mock",
            model=preset.model,
            debate_rounds=0,
        )
    elif preset.llm_mode == "openai":
        profile = ConfigProfile(
            name=profile_name,
            preset=preset_name,
            timeout_seconds=120,
            debate_rounds=2,
        )
    else:
        profile = ConfigProfile(
            name=profile_name,
            preset=preset_name,
            timeout_seconds=120 if not preset_name.startswith("ollama") else 180,
            max_retries=0,
            debate_rounds=0 if preset.provider_name == "ollama" else 2,
        )
    return SetupPlan(
        profile_name=profile_name,
        profile=profile,
        smoke_preset=preset_name,
        secret_env=secret_env,
        needs_secret=needs_secret,
        smoke_default=smoke_default,
    )


def non_interactive_plan(
    profile_key: str,
    *,
    profile_name: str | None = None,
) -> SetupPlan:
    name = profile_name or profile_key
    if profile_key == "mock":
        return SetupPlan(
            profile_name=name,
            profile=ConfigProfile(
                name=name,
                mode="mock",
                provider_name="mock",
                model="mock-council-v1",
                debate_rounds=0,
            ),
            smoke_preset="mock",
            secret_env=None,
            needs_secret=False,
            smoke_default=True,
        )
    if profile_key == "ollama-local":
        return SetupPlan(
            profile_name=name,
            profile=ConfigProfile(
                name=name,
                mode="openai_compatible",
                provider_name="ollama",
                base_url=OLLAMA_BASE_URL,
                model="qwen3.5:9b",
                timeout_seconds=180,
                max_retries=0,
                debate_rounds=0,
            ),
            smoke_preset="ollama-qwen",
            secret_env=None,
            needs_secret=False,
            smoke_default=True,
        )
    if profile_key == "openai-mini":
        return _plan_from_preset(
            name,
            "openai-mini",
            needs_secret=True,
            secret_env="OPENAI_API_KEY",
            smoke_default=False,
        )
    if profile_key == "openrouter-sonnet":
        return _plan_from_preset(
            name,
            "openrouter-sonnet",
            needs_secret=True,
            secret_env="LLM_API_KEY",
            smoke_default=False,
        )
    msg = f"Unknown non-interactive profile: {profile_key}"
    raise ValueError(msg)


def _execute_plan(
    plan: SetupPlan,
    *,
    config_path_override: Path,
    console: Console | None,
    prompter: SetupPrompter | None,
    store_secret_fn: Callable[[str, str], None] | None,
    secret_prompt_fn: Callable[[str], str] | None,
    doctor_fn: Callable[..., list] | None,
    smoke_fn: Callable[..., object] | None,
    interactive: bool,
) -> SetupResult:
    out = console or Console()
    prompt = prompter

    if plan.needs_secret and plan.secret_env:
        has_key = credential_source(plan.secret_env) != "missing"
        if interactive and prompt is not None:
            if not has_key:
                store = prompt.confirm(
                    f"Store {plan.secret_env} in the OS keyring now?",
                    default=True,
                )
                if store:
                    _store_secret_interactive(
                        plan.secret_env,
                        store_secret_fn=store_secret_fn,
                        secret_prompt_fn=secret_prompt_fn,
                        prompter=prompt,
                    )
            else:
                prompt.print(
                    f"[green]{plan.secret_env}[/green] already configured "
                    f"(source: {credential_source(plan.secret_env)})."
                )
        elif not has_key and interactive:
            out.print(
                f"[yellow]Note:[/yellow] {plan.secret_env} is not set. "
                "Run: uv run python main.py secrets set "
                f"{plan.secret_env}"
            )

    if not config_path_override.exists():
        init_config_file(config_path_override)
    saved = upsert_profile(plan.profile, set_active=True, path=config_path_override)
    if prompt is not None:
        prompt.print(f"\n[green]Saved profile[/green] {plan.profile_name} → {saved}")

    doctor_passed = True
    run_doctor_now = plan.doctor_default
    if interactive and prompt is not None:
        run_doctor_now = prompt.confirm("Run doctor now?", default=True)
    if run_doctor_now:
        doctor_passed = _run_doctor_step(plan, saved, out, doctor_fn=doctor_fn)

    run_smoke_now = plan.smoke_default
    if interactive and prompt is not None and plan.smoke_preset:
        default_smoke = plan.smoke_default and (
            plan.profile.preset == "mock"
            or is_ollama_openai_compatible(_settings_for_profile(plan, config_path=saved))
        )
        if is_ollama_openai_compatible(_settings_for_profile(plan, config_path=saved)) and not doctor_passed:
            default_smoke = False
        if plan.needs_secret:
            default_smoke = False
        run_smoke_now = prompt.confirm("Run smoke test now?", default=default_smoke)
    if run_smoke_now and plan.smoke_preset:
        _run_smoke_step(plan, out, smoke_fn=smoke_fn)

    _show_final_screen(out, plan.profile_name, saved)
    return SetupResult(
        cancelled=False,
        exit_code=0,
        active_profile=plan.profile_name,
        config_path=saved,
    )


def _settings_for_profile(plan: SetupPlan, *, config_path: Path | None = None) -> Settings:
    from council.config_profiles import resolve_settings_with_profile

    config = load_config_file(config_path)
    if config and plan.profile_name in config.profiles:
        profile = config.get_profile(plan.profile_name)
    else:
        profile = plan.profile
    return resolve_settings_with_profile(Settings.from_env(), profile=profile, cli_preset=None)


def _store_secret_interactive(
    secret_name: str,
    *,
    store_secret_fn: Callable[[str, str], None] | None,
    secret_prompt_fn: Callable[[str], str] | None,
    prompter: SetupPrompter,
) -> None:
    validate_secret_name(secret_name)
    prompt_fn = secret_prompt_fn or (lambda label: getpass(f"{label}: "))
    store_fn = store_secret_fn or set_keyring_secret
    value = prompt_fn(secret_name)
    if not value.strip():
        prompter.print(f"[yellow]{secret_name} not stored[/yellow] (empty input).")
        return
    store_fn(secret_name, value.strip())
    prompter.print(f"[green]{secret_name} stored in OS keyring.[/green]")


def _run_doctor_step(
    plan: SetupPlan,
    config_file: Path,
    console: Console,
    *,
    doctor_fn: Callable[..., list] | None,
) -> bool:
    from council.cli import render_doctor

    settings = _settings_for_profile(plan, config_path=config_file)
    runtime = RuntimeOptions(api_mode="chat" if settings.llm_provider_name in {"ollama", "nvidia", "groq", "cerebras"} else "auto")
    run = doctor_fn or run_doctor
    checks = run(settings, runtime=runtime)
    console.print()
    code = render_doctor(console, checks)
    return code == 0


def _run_smoke_step(
    plan: SetupPlan,
    console: Console,
    *,
    smoke_fn: Callable[..., object] | None,
) -> None:
    from council.cli import render_smoke_report

    if not plan.smoke_preset:
        return
    preset = get_preset(plan.smoke_preset)
    api_mode = "chat" if preset.provider_name in {"ollama", "nvidia", "groq", "cerebras"} else "auto"
    request = SmokeRequest(
        preset=plan.smoke_preset,
        debate_rounds=SETUP_SMOKE_DEBATE_ROUNDS,
        timeout_seconds=SETUP_SMOKE_TIMEOUT_SECONDS,
        api_mode=api_mode,
    )
    run = smoke_fn or run_smoke
    report = run(request)
    if not isinstance(report, SmokeReport):
        msg = "Smoke runner returned an unexpected result type."
        raise TypeError(msg)
    console.print()
    render_smoke_report(console, report)


def _show_final_screen(console: Console, profile_name: str, config_file: Path) -> None:
    console.print(
        Panel.fit(
            f"[bold]Setup complete[/bold]\n\n"
            f"Active profile: [cyan]{profile_name}[/cyan]\n"
            f"Config: {config_file}\n\n"
            "Next commands:\n"
            '  uv run python main.py run "Your question"\n'
            "  uv run python main.py doctor\n"
            "  uv run python main.py presets",
            border_style="green",
        )
    )


def presets_for_provider_kind(kind: ProviderKind) -> tuple[str, ...]:
    if kind == ProviderKind.MOCK:
        return ("mock",)
    if kind == ProviderKind.OLLAMA:
        return OLLAMA_PRESET_NAMES
    if kind == ProviderKind.OPENAI:
        return ("openai-mini",)
    if kind == ProviderKind.HOSTED:
        return tuple(
            name
            for name in MODEL_PRESETS
            if name not in {"mock", "openai-mini", *OLLAMA_PRESET_NAMES}
        )
    return ()
