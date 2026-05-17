"""Decision threads — Slice 5.8.

Lightweight conversational continuity for the chat. Three responsibilities:

1. Detect when a chat line looks like a contextual follow-up
   (`looks_like_follow_up`).
2. Reduce a previous `CouncilRunResult` to a small structured
   `DecisionContext` that we can prepend to a new question (no full run.md).
3. Persist linkage metadata (`DecisionThreadMeta`) on a new run so the JSON
   and markdown record `parent_run_id`, `thread_id`, and the structured
   `context_summary`.

Pure-logic module: no I/O, no network, no secrets.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from council.models import CouncilRunResult, DecisionDossier, DecisionType

CONTEXT_BLOCK_HEADER = "Using previous decision context"
CONTEXT_PREVIEW_LIMIT = 3  # top N next-actions / do-not-do / evidence gaps


# Conservative phrase set — we only auto-prompt when the user obviously
# refers to a prior decision. Anything outside this set is treated as a
# brand-new question to avoid surprise context attachment.
_FOLLOW_UP_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bwhat if\b", re.IGNORECASE),
    re.compile(r"\bchange (it|that|this)\b", re.IGNORECASE),
    re.compile(r"\b(make|do) (it|that) (cheaper|faster|simpler|smaller|stronger)\b", re.IGNORECASE),
    re.compile(r"\bwhat about\b", re.IGNORECASE),
    re.compile(r"\brevise (it|that|this)\b", re.IGNORECASE),
    re.compile(r"\bimprove (it|that|this)\b", re.IGNORECASE),
    re.compile(r"\b(continue|continue the (analysis|decision|thread))\b", re.IGNORECASE),
    re.compile(r"\b(refine|tweak|adjust) (it|that|the (decision|plan|recommendation))\b", re.IGNORECASE),
    re.compile(r"\b(why did (you|we) (decide|recommend))\b", re.IGNORECASE),
    re.compile(r"\b(what changed|what has changed)\b", re.IGNORECASE),
    re.compile(r"\b(reconsider|rethink|revisit)\b", re.IGNORECASE),
)


def looks_like_follow_up(text: str) -> bool:
    """True when the user's natural-language line probably refers to a
    previous decision. Conservative on purpose — false negatives are fine
    (the user can still attach context manually via `/use`)."""
    if not text or not text.strip():
        return False
    for pattern in _FOLLOW_UP_PATTERNS:
        if pattern.search(text):
            return True
    return False


class DecisionContext(BaseModel):
    """Structured, compact snapshot of a prior decision suitable for
    prepending to a new council question. Deliberately small — no agent
    briefs, no debate transcript, no raw provider payloads."""

    run_id: str
    decision_question: str
    direct_answer: str
    decision_type: DecisionType
    next_actions: list[str] = Field(default_factory=list)
    do_not_do: list[str] = Field(default_factory=list)
    approval_gate: str = ""
    evidence_gaps: list[str] = Field(default_factory=list)


class DecisionThreadMeta(BaseModel):
    """Persisted linkage metadata for a contextual run."""

    parent_run_id: str
    thread_id: str
    context_summary: DecisionContext


def summarize_for_context(result: CouncilRunResult) -> DecisionContext:
    """Reduce a run to the smallest useful snapshot for follow-up prompting.

    Keeps only the top `CONTEXT_PREVIEW_LIMIT` next actions, do-not-do
    items, and evidence gaps — enough signal for the chair to recognise
    continuity without bloating the prompt.
    """
    dossier = result.dossier
    return _summarize_dossier(dossier)


def _summarize_dossier(dossier: DecisionDossier) -> DecisionContext:
    direct = (dossier.direct_answer or dossier.recommendation.split("\n", 1)[0]).strip()
    return DecisionContext(
        run_id=dossier.run_id,
        decision_question=dossier.decision_question.strip(),
        direct_answer=direct,
        decision_type=dossier.decision_type,
        next_actions=list(dossier.next_actions)[:CONTEXT_PREVIEW_LIMIT],
        do_not_do=list(dossier.do_not_do)[:CONTEXT_PREVIEW_LIMIT],
        approval_gate=dossier.approval_gate.strip(),
        evidence_gaps=list(dossier.evidence_gaps)[:CONTEXT_PREVIEW_LIMIT],
    )


def derive_thread_id(parent: CouncilRunResult) -> str:
    """Compute the thread id for a child of `parent`.

    First-generation children anchor the thread on the parent's run id.
    Grandchildren and beyond inherit the parent's existing thread id, so
    the whole chain shares a single anchor.
    """
    thread = parent.decision_thread
    if thread is not None and thread.thread_id:
        return thread.thread_id
    return parent.dossier.run_id


def build_thread_meta(parent: CouncilRunResult) -> DecisionThreadMeta:
    """Build linkage metadata that should attach to a new run whose
    decision was reached with `parent` as its prior context."""
    return DecisionThreadMeta(
        parent_run_id=parent.dossier.run_id,
        thread_id=derive_thread_id(parent),
        context_summary=summarize_for_context(parent),
    )


def format_context_block(context: DecisionContext) -> str:
    """Produce the structured text block we prepend to a new question.

    Format is deterministic so chair prompts remain stable across runs.
    Bullet lists are clipped to `CONTEXT_PREVIEW_LIMIT` items by
    construction in `summarize_for_context`.
    """
    lines = [
        CONTEXT_BLOCK_HEADER,
        f"Parent run: {context.run_id}",
        f"Original question: {context.decision_question}",
        f"Prior direct answer: {context.direct_answer}",
        f"Prior decision: {context.decision_type.value}",
    ]
    if context.next_actions:
        lines.append("Prior next actions:")
        lines.extend(f"  - {item}" for item in context.next_actions)
    if context.do_not_do:
        lines.append("Prior do-not-do:")
        lines.extend(f"  - {item}" for item in context.do_not_do)
    if context.approval_gate:
        lines.append(f"Prior approval gate: {context.approval_gate}")
    if context.evidence_gaps:
        lines.append("Prior evidence gaps:")
        lines.extend(f"  - {item}" for item in context.evidence_gaps)
    return "\n".join(lines)


def compose_question_with_context(question: str, context: DecisionContext) -> str:
    """Prepend the structured context to a new follow-up question."""
    block = format_context_block(context)
    return f"{block}\n\nFollow-up question:\n{question.strip()}"
