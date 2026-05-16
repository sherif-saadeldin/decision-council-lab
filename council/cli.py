from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from council.config import Settings
from council.doctor import CheckStatus, DoctorCheck
from council.engine import effective_debate_rounds
from council.model_presets import MODEL_PRESETS, apply_preset, list_preset_names
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
from council.version import APP_VERSION

KNOWN_PROJECT_ERRORS: tuple[type[Exception], ...] = (
    MissingProviderCredentialError,
    MissingProviderConfigError,
    UnknownModelPresetError,
    UnsupportedProviderModeError,
    ProviderResponseError,
)

CLI_COMMANDS = frozenset({"run", "presets", "doctor", "version"})
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
    subparsers.add_parser("version", help="Show app and schema version.")

    return parser


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
        help="Model routing preset (overrides LLM_MODE/model env defaults; keys still from env).",
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
        default=0,
        metavar="N",
        help="Retry count for failed provider API calls (default: 0).",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Fast mode: skip debate, concise prompts, labeled in output.",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    normalized = normalize_argv(argv)
    if not normalized:
        parser.print_help()
        raise SystemExit(2)
    return parser.parse_args(normalized)


def resolve_settings(args: argparse.Namespace) -> Settings:
    base = Settings.from_env()
    runs_dir = getattr(args, "runs_dir", None)
    preset = getattr(args, "preset", None)
    if runs_dir is not None:
        base = Settings(
            llm_mode=base.llm_mode,
            runs_dir=runs_dir,
            mock_model=base.mock_model,
            openai_api_key=base.openai_api_key,
            openai_model=base.openai_model,
            llm_provider_name=base.llm_provider_name,
            llm_base_url=base.llm_base_url,
            llm_api_key=base.llm_api_key,
            llm_model=base.llm_model,
        )
    if preset:
        base = apply_preset(base, preset)
    return base


def resolve_runtime_options(args: argparse.Namespace) -> RuntimeOptions:
    quiet = bool(getattr(args, "quiet", False))
    fast_mode = bool(getattr(args, "fast", False))
    timeout = getattr(args, "timeout_seconds", None)
    max_retries = int(getattr(args, "max_retries", 0) or 0)
    return RuntimeOptions(
        timeout_seconds=timeout,
        max_retries=max(0, max_retries),
        fast_mode=fast_mode,
        show_progress=not quiet,
    )


def resolve_debate_rounds(args: argparse.Namespace, runtime: RuntimeOptions) -> int:
    requested = getattr(args, "debate_rounds", None)
    if requested is None:
        requested = DEFAULT_DEBATE_ROUNDS
    return effective_debate_rounds(requested, runtime)


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
        "\nAPI keys from env: OPENAI_API_KEY (openai presets) or LLM_API_KEY (compatible). "
        "Ollama presets accept LLM_API_KEY=ollama (or omit — defaults to ollama locally)."
    )
    console.print(
        "Ollama model tags are defaults — run `ollama list` and edit presets if your tags differ."
    )


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
