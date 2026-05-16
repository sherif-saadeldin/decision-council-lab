from __future__ import annotations

import argparse
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from council.config import Settings
from council.models import CouncilRunResult
from council.providers.errors import (
    MissingProviderCredentialError,
    ProviderResponseError,
    UnsupportedProviderModeError,
)

KNOWN_PROJECT_ERRORS: tuple[type[Exception], ...] = (
    MissingProviderCredentialError,
    UnsupportedProviderModeError,
    ProviderResponseError,
)

PREVIEW_ITEM_COUNT = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="decision-council-lab",
        description="Run the decision council and save JSON + Markdown artifacts.",
    )
    parser.add_argument(
        "question",
        nargs="?",
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
        help="Print only paths to saved artifacts.",
    )
    parser.add_argument(
        "--save-prompt-debug",
        action="store_true",
        help="Save prompt/debug context to runs/<run_id>/prompt_debug.md (no secrets).",
    )
    return parser


def resolve_settings(args: argparse.Namespace) -> Settings:
    base = Settings.from_env()
    if args.runs_dir is None:
        return base
    return Settings(
        llm_mode=base.llm_mode,
        runs_dir=args.runs_dir,
        mock_model=base.mock_model,
        openai_api_key=base.openai_api_key,
        openai_model=base.openai_model,
    )


def render_result(
    console: Console,
    result: CouncilRunResult,
    json_path: Path,
    md_path: Path,
    *,
    quiet: bool,
    runs_dir: Path,
    prompt_debug_path: Path | None = None,
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

    console.print(
        Panel.fit(
            f"[bold]{dossier.recommendation}[/bold]\n\n"
            f"Decision type: {decision_type}\n"
            f"Confidence: {confidence_pct}\n"
            f"Run ID: {dossier.run_id}\n"
            f"Mode: {result.provider_metadata.mode} "
            f"({result.provider_metadata.provider_name} / {result.provider_metadata.model_name})",
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
