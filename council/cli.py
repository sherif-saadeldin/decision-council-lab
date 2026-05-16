from __future__ import annotations

import argparse
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from council.config import Settings
from council.models import CouncilRunResult

PREVIEW_ITEM_COUNT = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="decision-council-lab",
        description="Run the mock decision council and save JSON + Markdown artifacts.",
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
    return parser


def resolve_settings(args: argparse.Namespace) -> Settings:
    settings = Settings.from_env()
    if args.runs_dir is not None:
        return Settings(
            llm_mode=settings.llm_mode,
            runs_dir=args.runs_dir,
            mock_model=settings.mock_model,
        )
    return settings


def render_result(
    console: Console,
    result: CouncilRunResult,
    json_path: Path,
    md_path: Path,
    *,
    quiet: bool,
    runs_dir: Path,
) -> None:
    if quiet:
        console.print(json_path)
        console.print(md_path)
        return

    dossier = result.dossier
    confidence_pct = f"{dossier.confidence_score:.0%}"

    console.print(
        Panel.fit(
            f"[bold]{dossier.recommendation}[/bold]\n\n"
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


def _print_preview_list(console: Console, title: str, items: list[str]) -> None:
    if not items:
        return
    console.print(f"[bold]{title}[/bold]")
    for item in items[:PREVIEW_ITEM_COUNT]:
        console.print(f"  • {item}")
    if len(items) > PREVIEW_ITEM_COUNT:
        console.print(f"  … and {len(items) - PREVIEW_ITEM_COUNT} more in the dossier")
    console.print()
