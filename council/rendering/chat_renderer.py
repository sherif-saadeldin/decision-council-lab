from __future__ import annotations

from rich.console import Console
from rich.panel import Panel

from council.cli import _print_preview_list
from council.models import CouncilRunResult
from council.verdict_quality import decision_label


CHAT_HELP_LINES: tuple[str, ...] = (
    "── Conversation ──",
    "(Type naturally. I'll ask a few guided questions, summarize, then run the council.)",
    "/intake                       — show or start the guided intake",
    "/edit [field]                 — edit one intake field (goal, mode, context, ...)",
    "/clear-intake                 — discard the current intake draft",
    "/mode [name]                  — show or set the decision mode",
    "/summary                      — show the current intake summary",
    "/sources                      — list available source packs and active source",
    "/source scan <path>           — scan a local folder/file into a source pack",
    "/source use <source_pack_id>  — use a saved source pack in chat council runs",
    "/source show <source_pack_id> — inspect a source pack",
    "/source query <question>      — inspect ranked relevance for active source packs",
    "/source clear                 — clear the active source pack",
    "",
    "── Council ──",
    "/council <question>          — skip intake; run multi-model council directly",
    "/run <question>              — single-provider council run",
    "/compare <question>          — compare mock preset (offline-safe default)",
    "",
    "── Runs + lifecycle ──",
    "/runs                        — list recent runs",
    "/show <run_id|last>          — inspect a saved run",
    "/pack <run_id|last> [--allow-unapproved]",
    "                             — generate implementation pack (approved runs only)",
    "/approve <run_id|last> [note]   — mark a decision approved",
    "/reject  <run_id|last> <reason> — mark a decision rejected (reason required)",
    "/revise  <run_id|last>          — start a contextual follow-up as a revision",
    "/review  <run_id|last>          — show lifecycle state and review history",
    "/archive <run_id|last> [note]   — archive a decision",
    "/context                     — show the active decision context (if any)",
    "/use <run_id>                — load a previous run as decision context",
    "/forget                      — clear active decision context and session memory",
    "/thread                      — show the linked decision chain for the current thread",
    "",
    "── Config + health ──",
    "/doctor [--live|--live-completion]",
    "                             — health check for the active profile",
    "/presets                     — list model presets",
    "/setup                       — interactive setup wizard",
    "/prompts                     — system prompt inventory",
    "/profile [name|list]         — show, list, or switch active config profile",
    "/status                      — active profile, routing, cached health, last run",
    "/help                        — show this help",
    "/exit                        — leave chat",
)


def render_chat_welcome(
    console: Console,
    *,
    config_profile_name: str,
    system_profile: str,
    routing_mode: str,
    operational_profile: str,
    operational_note: str | None = None,
) -> None:
    lines = [
        f"Running in [cyan]{operational_profile}[/cyan] mode.",
        f"Conversation profile: [cyan]{config_profile_name}[/cyan]",
        f"Reasoning style: [cyan]{routing_mode}[/cyan]",
        f"System profile: [cyan]{system_profile}[/cyan]",
        "",
        "Slash commands:",
        *CHAT_HELP_LINES,
    ]
    if operational_note:
        lines.insert(1, operational_note)
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
