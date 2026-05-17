from __future__ import annotations

from rich.console import Console
from rich.panel import Panel

from council.cli import _print_preview_list
from council.models import CouncilRunResult
from council.verdict_quality import decision_label


CORE_HELP_LINES: tuple[str, ...] = (
    "Type naturally and I will guide intake, summarize, and run analysis.",
    "",
    "Core commands:",
    "/council <question>  — run council analysis directly",
    "/summary             — show your current intake summary",
    "/source use <name>   — attach source context (alias or id)",
    "/status              — show detailed session status",
    "/help advanced       — show all command groups",
    "/exit                — leave chat",
)

HELP_SOURCES_LINES: tuple[str, ...] = (
    "/sources                           — list source packs",
    "/source scan <path> [alias]        — scan and optionally set an alias",
    "/source use <alias|id>             — activate source pack",
    "/source alias <alias|id> <new>     — set or update source alias",
    "/source show <alias|id>            — inspect source pack details",
    "/source query <question>           — inspect ranked source relevance",
    "/source clear                      — clear active source context",
)

HELP_LIFECYCLE_LINES: tuple[str, ...] = (
    "/runs                              — list recent runs",
    "/show <run_id|last>                — inspect a saved run",
    "/approve <run_id|last> [note]      — approve a decision",
    "/reject <run_id|last> <reason>     — reject with required reason",
    "/revise <run_id|last>              — continue as a revision",
    "/review <run_id|last>              — show review history",
    "/archive <run_id|last> [note]      — archive a decision",
    "/pack <run_id|last> [--allow-unapproved] — generate implementation pack",
)

HELP_ADVANCED_LINES: tuple[str, ...] = (
    "/intake, /edit, /clear-intake, /mode, /summary",
    "/run, /compare, /doctor, /profile, /presets, /setup, /prompts, /status",
    "/context, /use, /forget, /thread",
    "/help lifecycle, /help sources",
)

# Backward compatibility for older imports/tests.
CHAT_HELP_LINES: tuple[str, ...] = CORE_HELP_LINES


def render_chat_welcome(
    console: Console,
    *,
    operational_profile: str,
    active_sources: list[str],
    has_previous_run: bool,
    has_active_thread: bool,
    operational_note: str | None = None,
) -> None:
    lines = ["Decision Workspace", f"Running in {operational_profile} mode."]
    if operational_note:
        lines.append(operational_note)
    lines.append(f"Active sources: {', '.join(active_sources) if active_sources else 'none'}")
    awareness: list[str] = []
    if active_sources:
        awareness.append("source context")
    if has_active_thread:
        awareness.append("active decision thread")
    if has_previous_run:
        awareness.append("previous council session")
    if awareness:
        lines.append("")
        lines.append("Continuing work related to:")
        lines.extend(f"- {item}" for item in awareness)
    lines.extend(["", "Type naturally to begin.", "Use /help for commands."])
    console.print("\n".join(lines))


def render_chat_help(console: Console, topic: str = "") -> None:
    key = topic.strip().lower()
    if key in {"advanced", "all"}:
        lines = ("Advanced help", "", *CORE_HELP_LINES, "", *HELP_ADVANCED_LINES)
    elif key == "lifecycle":
        lines = ("Lifecycle help", "", *HELP_LIFECYCLE_LINES)
    elif key == "sources":
        lines = ("Sources help", "", *HELP_SOURCES_LINES)
    else:
        lines = ("Help", "", *CORE_HELP_LINES, "", "More: /help sources | /help lifecycle | /help advanced")
    console.print("\n".join(lines))


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


def render_chat_verdict_short(console: Console, result: CouncilRunResult) -> None:
    dossier = result.dossier
    direct = dossier.direct_answer.strip() or dossier.recommendation.split("\n", 1)[0]
    reasons = [r for r in dossier.why_this_decision[:3] if r.strip()]
    warning = ""
    if dossier.do_not_do:
        warning = dossier.do_not_do[0].strip()
    elif dossier.risks:
        warning = dossier.risks[0].strip()
    next_step = dossier.next_actions[0].strip() if dossier.next_actions else ""
    body_lines = [f"[bold]{direct}[/bold]", ""]
    if reasons:
        body_lines.append("Why:")
        for reason in reasons:
            body_lines.append(f"  • {reason}")
        body_lines.append("")
    if warning:
        body_lines.append(f"Biggest warning: {warning}")
    if next_step:
        body_lines.append(f"Next step: {next_step}")
    body_lines.append("")
    body_lines.append(f"Run ID: [cyan]{dossier.run_id}[/cyan]")
    console.print(
        Panel.fit(
            "\n".join(body_lines),
            title="Direct Answer",
            border_style="green",
        )
    )
