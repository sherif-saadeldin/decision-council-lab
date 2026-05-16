from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from council.compare import CompareConfigError, CompareRequest, ComparisonReport, build_targets, parse_csv_targets
from council.config import Settings
from council.config_profiles import (
    ConfigProfile,
    ConfigProfileError,
    CouncilConfigFile,
    UnknownConfigProfileError,
    init_config_file,
    load_config_file,
    profile_display_rows,
    resolve_debate_rounds_with_profile,
    resolve_profile_name,
    resolve_runtime_with_profile,
    resolve_settings_with_profile,
    set_active_profile,
)
from council.doctor import CheckStatus, DoctorCheck
from council.model_presets import MODEL_PRESETS, list_preset_names
from council.models import DEFAULT_DEBATE_ROUNDS, RUN_SCHEMA_VERSION, CouncilRunResult
from council.providers.errors import (
    MissingProviderConfigError,
    MissingProviderCredentialError,
    ProviderResponseError,
    UnknownModelPresetError,
    UnsupportedProviderModeError,
)
from council.providers.factory import SUPPORTED_LLM_MODES
from council.runtime import RuntimeOptions
from council.smoke import DEFAULT_SMOKE_QUESTION, SmokeReport, SmokeRequest
from council.secrets import (
    UnknownSecretNameError,
    delete_keyring_secret,
    list_secret_statuses,
    set_keyring_secret,
    validate_secret_name,
)
from council.version import APP_VERSION

KNOWN_PROJECT_ERRORS: tuple[type[Exception], ...] = (
    MissingProviderCredentialError,
    MissingProviderConfigError,
    UnknownModelPresetError,
    UnsupportedProviderModeError,
    ProviderResponseError,
    ConfigProfileError,
    UnknownConfigProfileError,
    UnknownSecretNameError,
    CompareConfigError,
)

CLI_COMMANDS = frozenset(
    {
        "run",
        "presets",
        "doctor",
        "version",
        "config",
        "secrets",
        "compare",
        "benchmark",
        "smoke",
    }
)
PREVIEW_ITEM_COUNT = 3


def normalize_argv(argv: list[str] | None) -> list[str]:
    """Map legacy invocations to subcommands for backward compatibility."""
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        return argv
    if argv[0] in CLI_COMMANDS:
        return argv
    if "--list-presets" in argv:
        return ["presets"]
    return ["run", *argv]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="decision-council-lab",
        description="Decision council CLI — run, diagnose, and manage presets.",
    )
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser(
        "run",
        help="Run the council on a decision question.",
    )
    _add_run_arguments(run_parser)

    subparsers.add_parser("presets", help="List model routing presets.")
    doctor_parser = subparsers.add_parser("doctor", help="Check provider configuration.")
    doctor_parser.add_argument(
        "--preset",
        metavar="PRESET_NAME",
        help="Apply preset before running checks.",
    )
    doctor_parser.add_argument(
        "--live",
        action="store_true",
        help="Initialize provider (no completion call) to validate setup.",
    )
    _add_profile_argument(doctor_parser)
    subparsers.add_parser("version", help="Show app and schema version.")

    config_parser = subparsers.add_parser("config", help="Manage local config profiles.")
    config_sub = config_parser.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("init", help="Create .dcouncil/config.toml with sample profiles.")
    config_sub.add_parser("list", help="List config profiles.")
    config_show = config_sub.add_parser("show", help="Show profile values (no secrets).")
    config_show.add_argument("profile", help="Profile name to display.")
    config_use = config_sub.add_parser("use", help="Set active_profile in config.toml.")
    config_use.add_argument("profile", help="Profile name to activate.")

    secrets_parser = subparsers.add_parser("secrets", help="Manage API keys in the OS keyring.")
    secrets_sub = secrets_parser.add_subparsers(dest="secrets_command", required=True)
    secrets_set = secrets_sub.add_parser("set", help="Store a secret in the OS keyring.")
    secrets_set.add_argument("name", help="Secret name (OPENAI_API_KEY or LLM_API_KEY).")
    secrets_get = secrets_sub.add_parser("get", help="Report whether a secret is set (never prints value).")
    secrets_get.add_argument("name", help="Secret name (OPENAI_API_KEY or LLM_API_KEY).")
    secrets_sub.add_parser("list", help="List supported secrets and availability.")
    secrets_delete = secrets_sub.add_parser("delete", help="Remove a secret from the OS keyring.")
    secrets_delete.add_argument("name", help="Secret name (OPENAI_API_KEY or LLM_API_KEY).")

    compare_parser = subparsers.add_parser(
        "compare",
        help="Run the same question across multiple presets/profiles and compare.",
    )
    _add_compare_arguments(compare_parser)
    benchmark_parser = subparsers.add_parser(
        "benchmark",
        help="Alias for compare.",
    )
    _add_compare_arguments(benchmark_parser)

    smoke_parser = subparsers.add_parser(
        "smoke",
        help="Manual live-provider smoke test (not used by pytest).",
    )
    _add_smoke_arguments(smoke_parser)

    return parser


def _add_repair_json_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repair-json",
        action="store_true",
        help=(
            "On parse failure for openai_compatible providers, retry once with "
            "stricter JSON-only instructions."
        ),
    )


def _add_profile_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--profile",
        metavar="PROFILE_NAME",
        help="Config profile from .dcouncil/config.toml (overrides active_profile).",
    )


def _add_compare_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "question",
        help="Decision question to run across each preset/profile.",
    )
    parser.add_argument(
        "--presets",
        metavar="NAMES",
        help="Comma-separated model presets (e.g. mock,ollama-qwen).",
    )
    parser.add_argument(
        "--profiles",
        metavar="NAMES",
        help="Comma-separated config profiles from .dcouncil/config.toml.",
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=None,
        help="Directory for run and comparison artifacts (default: RUNS_DIR env or ./runs).",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Print only comparison artifact paths.",
    )
    parser.add_argument(
        "--debate-rounds",
        type=int,
        default=None,
        metavar="N",
        help=(
            f"Debate rounds for each run (default: {DEFAULT_DEBATE_ROUNDS}; "
            "use 0 to skip; fast mode forces 0)."
        ),
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Per-request provider timeout for live LLM calls (default: 120).",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=None,
        metavar="N",
        help="Retry count for failed provider API calls.",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Fast mode: skip debate, concise prompts.",
    )
    _add_repair_json_argument(parser)


def _add_smoke_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--preset",
        metavar="PRESET_NAME",
        required=True,
        help="Model preset to exercise against a live provider.",
    )
    parser.add_argument(
        "--question",
        default=None,
        help=f"Decision question (default: {DEFAULT_SMOKE_QUESTION!r}).",
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=None,
        help="Directory for run artifacts (default: RUNS_DIR env or ./runs).",
    )
    parser.add_argument(
        "--debate-rounds",
        type=int,
        default=0,
        metavar="N",
        help="Debate rounds before chair (default: 0 for speed).",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Per-request provider timeout for live LLM calls (default: 120).",
    )
    _add_repair_json_argument(parser)


def build_smoke_request(args: argparse.Namespace) -> SmokeRequest:
    question = getattr(args, "question", None) or DEFAULT_SMOKE_QUESTION
    return SmokeRequest(
        preset=args.preset,
        question=question,
        runs_dir=getattr(args, "runs_dir", None),
        debate_rounds=max(0, int(args.debate_rounds)),
        timeout_seconds=getattr(args, "timeout_seconds", None),
        repair_json=bool(getattr(args, "repair_json", False)),
    )


def build_compare_request(args: argparse.Namespace) -> CompareRequest:
    presets = parse_csv_targets(getattr(args, "presets", None))
    profiles = parse_csv_targets(getattr(args, "profiles", None))
    targets = build_targets(presets, profiles)
    argv = getattr(args, "_argv", None)
    fast_explicit = bool(argv and "--fast" in argv)
    return CompareRequest(
        question=args.question,
        targets=targets,
        runs_dir=getattr(args, "runs_dir", None),
        debate_rounds=getattr(args, "debate_rounds", None),
        timeout_seconds=getattr(args, "timeout_seconds", None),
        max_retries=getattr(args, "max_retries", None),
        fast=bool(getattr(args, "fast", False)),
        fast_explicit=fast_explicit,
        repair_json=bool(getattr(args, "repair_json", False)),
    )


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "question",
        help="Decision question for the council to deliberate on.",
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=None,
        help="Directory for run artifacts (default: RUNS_DIR env or ./runs).",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Print only paths to saved artifacts (suppress progress).",
    )
    parser.add_argument(
        "--save-prompt-debug",
        action="store_true",
        help="Save prompt/debug context to runs/<run_id>/prompt_debug.md (no secrets).",
    )
    parser.add_argument(
        "--preset",
        metavar="PRESET_NAME",
        help="Model routing preset (overrides LLM_MODE/model env defaults; keys from env or keyring).",
    )
    parser.add_argument(
        "--debate-rounds",
        type=int,
        default=None,
        metavar="N",
        help=(
            f"Debate rounds before chair (default: {DEFAULT_DEBATE_ROUNDS}; "
            "use 0 to skip; fast mode forces 0)."
        ),
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Per-request provider timeout for live LLM calls (default: 120).",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=None,
        metavar="N",
        help="Retry count for failed provider API calls (profile/env default if omitted).",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Fast mode: skip debate, concise prompts, labeled in output.",
    )
    _add_profile_argument(parser)
    _add_repair_json_argument(parser)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    normalized = normalize_argv(argv)
    if not normalized:
        parser.print_help()
        raise SystemExit(2)
    args = parser.parse_args(normalized)
    args._argv = normalized
    return args


def _load_config_context() -> CouncilConfigFile | None:
    try:
        return load_config_file()
    except ConfigProfileError:
        raise


def _resolve_selected_profile(args: argparse.Namespace) -> ConfigProfile | None:
    config = _load_config_context()
    name = resolve_profile_name(cli_profile=getattr(args, "profile", None), config=config)
    if name is None:
        return None
    if config is None:
        config = load_config_file()
    if config is None:
        return None
    return config.get_profile(name)


def resolve_settings(args: argparse.Namespace) -> Settings:
    base = Settings.from_env()
    profile = _resolve_selected_profile(args)
    preset = getattr(args, "preset", None)
    settings = resolve_settings_with_profile(base, profile=profile, cli_preset=preset)
    runs_dir = getattr(args, "runs_dir", None)
    if runs_dir is not None:
        settings = Settings(
            llm_mode=settings.llm_mode,
            runs_dir=runs_dir,
            mock_model=settings.mock_model,
            openai_api_key=settings.openai_api_key,
            openai_model=settings.openai_model,
            llm_provider_name=settings.llm_provider_name,
            llm_base_url=settings.llm_base_url,
            llm_api_key=settings.llm_api_key,
            llm_model=settings.llm_model,
        )
    return settings


def resolve_runtime_options(args: argparse.Namespace) -> RuntimeOptions:
    profile = _resolve_selected_profile(args)
    argv = getattr(args, "_argv", None)
    fast_explicit = bool(argv and "--fast" in argv)
    return resolve_runtime_with_profile(
        profile,
        cli_timeout=getattr(args, "timeout_seconds", None),
        cli_max_retries=getattr(args, "max_retries", None),
        cli_fast=bool(getattr(args, "fast", False)),
        cli_fast_explicit=fast_explicit,
        quiet=bool(getattr(args, "quiet", False)),
        cli_repair_json=bool(getattr(args, "repair_json", False)),
    )


def resolve_debate_rounds(args: argparse.Namespace, runtime: RuntimeOptions) -> int:
    profile = _resolve_selected_profile(args)
    return resolve_debate_rounds_with_profile(
        profile,
        cli_debate_rounds=getattr(args, "debate_rounds", None),
        runtime=runtime,
    )


def render_config_list(console: Console) -> None:
    config = load_config_file()
    if config is None:
        console.print("No config file found. Run: python main.py config init", style="yellow")
        return
    table = Table(title=f"Config profiles ({config.path})")
    table.add_column("Profile", style="bold")
    table.add_column("Active")
    table.add_column("Mode")
    table.add_column("Provider")
    table.add_column("Model / preset")
    for name in config.profile_names():
        profile = config.profiles[name]
        label = profile.preset or profile.model or "—"
        table.add_row(
            name,
            "yes" if name == config.active_profile else "",
            profile.mode or "—",
            profile.provider_name or "—",
            label,
        )
    console.print(table)


def render_config_show(console: Console, profile_name: str) -> None:
    config = load_config_file()
    if config is None:
        raise ConfigProfileError("No config file found. Run: python main.py config init")
    profile = config.get_profile(profile_name)
    table = Table(title=f"Profile: {profile_name}")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    for field, value in profile_display_rows(profile):
        table.add_row(field, value)
    console.print(table)


def render_config_init(console: Console, path: Path | None = None) -> None:
    created = init_config_file(path)
    console.print(f"[green]Config ready:[/green] {created}")
    console.print("API keys: environment variables or `python main.py secrets set`.")


def render_config_use(console: Console, profile_name: str) -> None:
    path = set_active_profile(profile_name)
    console.print(f"[green]Active profile:[/green] {profile_name} ({path})")


def render_preset_list(console: Console) -> None:
    table = Table(title="Model presets")
    table.add_column("Preset", style="bold")
    table.add_column("Provider")
    table.add_column("Model")
    table.add_column("Mode")
    table.add_column("Base URL")
    for name in list_preset_names():
        preset = MODEL_PRESETS[name]
        base_url = preset.base_url or "—"
        table.add_row(name, preset.provider_name, preset.model, preset.llm_mode, base_url)
    console.print(table)
    console.print(
        "\nAPI keys from env or keyring: OPENAI_API_KEY (openai presets) or LLM_API_KEY (compatible). "
        "Env wins over keyring. Ollama presets accept LLM_API_KEY=ollama (or omit — defaults locally)."
    )
    console.print(
        "Ollama model tags are defaults — run `ollama list` and edit presets if your tags differ."
    )


def render_smoke_report(console: Console, report: SmokeReport) -> None:
    status = "[green]success[/green]" if report.success else "[red]failure[/red]"
    table = Table(title=f"Smoke test — {report.preset} ({status})")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("Question", report.question)
    table.add_row("Provider", report.provider_name or "—")
    table.add_row("Model", report.model_name or "—")
    table.add_row("Elapsed", f"{report.elapsed_seconds:.2f}s")
    if report.success:
        confidence_pct = f"{(report.confidence_score or 0) * 100:.0f}%"
        table.add_row("Run ID", report.run_id or "—")
        table.add_row("Decision type", report.decision_type or "—")
        table.add_row("Confidence", confidence_pct)
        table.add_row("Evidence gaps present", "yes" if report.has_evidence_gaps else "no")
        table.add_row("Proposed metrics present", "yes" if report.has_proposed_metrics else "no")
        table.add_row("JSON artifact", report.run_json_path or "—")
        table.add_row("Markdown artifact", report.run_md_path or "—")
    else:
        if report.failure_reason:
            table.add_row("Failure reason", report.failure_reason)
        table.add_row("Error", report.error or "Unknown error")
    console.print(table)


def render_comparison_result(
    console: Console,
    report: ComparisonReport,
    json_path: Path,
    md_path: Path,
    *,
    quiet: bool,
) -> None:
    if quiet:
        console.print(json_path)
        console.print(md_path)
        return

    table = Table(title=f"Comparison {report.comparison_id}")
    table.add_column("Target", style="bold")
    table.add_column("Kind")
    table.add_column("Status")
    table.add_column("Decision")
    table.add_column("Confidence")
    for entry in report.entries:
        if entry.success:
            status = "[green]ok[/green]"
            decision = entry.decision_type or "—"
            confidence = f"{(entry.confidence_score or 0) * 100:.0f}%"
        else:
            status = "[red]failed[/red]"
            decision = "—"
            confidence = "—"
        table.add_row(entry.label, entry.kind, status, decision, confidence)
    console.print(table)
    console.print()
    ev = report.evaluator
    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="bold")
    summary.add_column()
    summary.add_row("Question", report.question)
    summary.add_row("Debate rounds", str(report.debate_rounds))
    summary.add_row("Most decisive", ev.most_decisive)
    summary.add_row("Recommended", ev.recommended)
    console.print(summary)
    console.print()
    try:
        json_display = json_path.relative_to(Path.cwd())
        md_display = md_path.relative_to(Path.cwd())
    except ValueError:
        json_display = json_path
        md_display = md_path
    console.print(f"[green]Saved comparison JSON:[/green] {json_display}")
    console.print(f"[green]Saved comparison Markdown:[/green] {md_display}")


def render_secrets_set(console: Console, name: str, *, prompt_for_value: Callable[[str], str]) -> None:
    validate_secret_name(name)
    value = prompt_for_value(f"{name}: ")
    if not value.strip():
        console.print(f"[yellow]{name} not changed[/yellow] (empty input).")
        return
    set_keyring_secret(name, value.strip())
    console.print(f"[green]{name} stored in OS keyring.[/green]")


def render_secrets_get(console: Console, name: str) -> None:
    validate_secret_name(name)
    from council.secrets import credential_source

    source = credential_source(name)
    if source == "missing":
        console.print(f"{name} is not set (source: missing).")
    else:
        console.print(f"{name} is set (source: {source}).")


def render_secrets_list(console: Console) -> None:
    table = Table(title="Secrets (env overrides keyring)")
    table.add_column("Name", style="bold")
    table.add_column("Available")
    table.add_column("Source")
    from council.secrets import credential_source

    for name, available in list_secret_statuses():
        source = credential_source(name)
        table.add_row(
            name,
            "yes" if available else "no",
            source if available else "missing",
        )
    console.print(table)


def render_secrets_delete(console: Console, name: str) -> None:
    validate_secret_name(name)
    delete_keyring_secret(name)
    console.print(f"[green]{name} removed from OS keyring.[/green]")


def render_version(console: Console) -> None:
    table = Table(title="decision-council-lab")
    table.add_column("Key", style="bold")
    table.add_column("Value")
    table.add_row("App version", APP_VERSION)
    table.add_row("Schema version", RUN_SCHEMA_VERSION)
    table.add_row("Supported modes", ", ".join(SUPPORTED_LLM_MODES))
    console.print(table)


def render_doctor(console: Console, checks: list[DoctorCheck]) -> int:
    table = Table(title="Doctor")
    table.add_column("Check", style="bold")
    table.add_column("Status")
    table.add_column("Message")
    exit_code = 0
    for check in checks:
        if check.status == CheckStatus.FAIL:
            exit_code = 1
        style = {
            CheckStatus.OK: "green",
            CheckStatus.WARN: "yellow",
            CheckStatus.FAIL: "red",
            CheckStatus.SKIP: "dim",
        }.get(check.status, "")
        table.add_row(check.name, f"[{style}]{check.status.value}[/{style}]", check.message)
    console.print(table)
    return exit_code


def render_result(
    console: Console,
    result: CouncilRunResult,
    json_path: Path,
    md_path: Path,
    *,
    quiet: bool,
    runs_dir: Path,
    prompt_debug_path: Path | None = None,
    fast_mode: bool = False,
) -> None:
    if quiet:
        console.print(json_path)
        console.print(md_path)
        if prompt_debug_path is not None:
            console.print(prompt_debug_path)
        return

    dossier = result.dossier
    confidence_pct = f"{dossier.confidence_score:.0%}"
    decision_type = dossier.decision_type.value.replace("_", " ")
    fast_label = "\n[yellow]Fast mode[/yellow]" if fast_mode else ""

    console.print(
        Panel.fit(
            f"[bold]{dossier.recommendation}[/bold]\n\n"
            f"Decision type: {decision_type}\n"
            f"Confidence: {confidence_pct}\n"
            f"Run ID: {dossier.run_id}\n"
            f"Mode: {result.provider_metadata.mode} "
            f"({result.provider_metadata.provider_name} / {result.provider_metadata.model_name})"
            f"{fast_label}",
            title="Executive Summary",
        )
    )

    preview = Table.grid(padding=(0, 2))
    preview.add_column(style="bold")
    preview.add_column()
    preview.add_row("Question", dossier.decision_question)
    preview.add_row("Deciding factor", dossier.deciding_factor)
    preview.add_row("Timestamp", dossier.timestamp.isoformat())
    preview.add_row("Runs dir", str(runs_dir.resolve()))
    if fast_mode:
        preview.add_row("Profile", "fast (debate skipped, concise prompts)")
    console.print(preview)
    console.print()

    _print_preview_list(console, "Top risks", dossier.risks)
    _print_preview_list(console, "Next actions", dossier.next_actions)

    try:
        json_display = json_path.relative_to(Path.cwd())
        md_display = md_path.relative_to(Path.cwd())
    except ValueError:
        json_display = json_path
        md_display = md_path

    console.print(f"[green]Saved JSON:[/green] {json_display}")
    console.print(f"[green]Saved Markdown:[/green] {md_display}")
    if prompt_debug_path is not None:
        try:
            debug_display = prompt_debug_path.relative_to(Path.cwd())
        except ValueError:
            debug_display = prompt_debug_path
        console.print(f"[green]Saved prompt debug:[/green] {debug_display}")


def format_user_error(exc: Exception) -> str:
    if isinstance(exc, ProviderResponseError) and exc.source == "api":
        return exc.detail
    return str(exc)


def render_known_error(console: Console, exc: Exception, *, quiet: bool) -> None:
    message = format_user_error(exc)
    if quiet:
        console.print(message, style="red")
        return
    console.print(Panel.fit(message, title="Error", border_style="red"))


def _print_preview_list(console: Console, title: str, items: list[str]) -> None:
    if not items:
        return
    console.print(f"[bold]{title}[/bold]")
    for item in items[:PREVIEW_ITEM_COUNT]:
        console.print(f"  • {item}")
    if len(items) > PREVIEW_ITEM_COUNT:
        console.print(f"  … and {len(items) - PREVIEW_ITEM_COUNT} more in the dossier")
    console.print()
