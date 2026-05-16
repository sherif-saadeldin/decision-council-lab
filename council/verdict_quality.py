from __future__ import annotations

from council.models import DecisionDossier, DecisionType

MIN_BULLET_ITEMS = 3
MIN_DIRECT_ANSWER_LEN = 20
# Longest question substring allowed inside direct_answer (chars).
MIN_QUESTION_ECHO_LEN = 32
# Fraction of normalized question length that triggers echo detection.
QUESTION_ECHO_RATIO = 0.45

GENERIC_VERDICT_PHRASES: tuple[str, ...] = (
    "time-boxed internal prototype",
    "proceed with a time-boxed",
    "proceed with building the decision council",
    "internal tool first, with scope constrained",
    "mock-only reasoning can look rigorous",
    "the cost of a small prototype is lower than committing to a full platform",
    "learning velocity and traceability over immediate distribution",
    "until dossier quality is proven",
    "before external packaging",
)

DECISION_TYPE_LABELS: dict[DecisionType, str] = {
    DecisionType.PROCEED: "proceed",
    DecisionType.PROCEED_WITH_CONSTRAINTS: "proceed with constraints",
    DecisionType.PAUSE: "pause",
    DecisionType.REJECT: "reject",
}


class VerdictQualityError(ValueError):
    """Raised when a dossier lacks required actionable verdict fields."""


def decision_label(decision_type: DecisionType) -> str:
    return DECISION_TYPE_LABELS.get(decision_type, decision_type.value.replace("_", " "))


def is_generic_verdict_text(text: str) -> bool:
    lowered = text.strip().lower()
    if not lowered:
        return True
    return any(phrase in lowered for phrase in GENERIC_VERDICT_PHRASES)


def _normalize_for_echo(text: str) -> str:
    return " ".join(text.lower().split())


def direct_answer_repeats_question(direct_answer: str, question: str) -> bool:
    """True when direct_answer embeds a long fragment of the decision question."""
    direct = _normalize_for_echo(direct_answer)
    normalized_question = _normalize_for_echo(question)
    if not direct or not normalized_question:
        return False

    if normalized_question in direct:
        return True

    q_len = len(normalized_question)
    if q_len < MIN_QUESTION_ECHO_LEN:
        return False

    threshold = max(MIN_QUESTION_ECHO_LEN, int(q_len * QUESTION_ECHO_RATIO))
    for length in range(q_len, threshold - 1, -1):
        for start in range(0, q_len - length + 1):
            fragment = normalized_question[start : start + length]
            if len(fragment) >= threshold and fragment in direct:
                return True
    return False


def _has_items(items: list[str], *, minimum: int = MIN_BULLET_ITEMS) -> bool:
    cleaned = [item.strip() for item in items if item and item.strip()]
    return len(cleaned) >= minimum


def verdict_quality_issues(dossier: DecisionDossier) -> list[str]:
    issues: list[str] = []
    direct = dossier.direct_answer.strip()
    if len(direct) < MIN_DIRECT_ANSWER_LEN:
        issues.append("direct_answer is missing or too short")
    elif is_generic_verdict_text(direct):
        issues.append("direct_answer reads like generic placeholder text")
    elif direct_answer_repeats_question(direct, dossier.decision_question):
        issues.append("direct_answer repeats or quotes too much of the decision question")

    if dossier.decision_type not in DecisionType:
        issues.append("decision_type is invalid")

    if not _has_items(dossier.why_this_decision):
        issues.append("why_this_decision needs at least 3 concrete reasons")
    if not _has_items(dossier.what_would_change_mind):
        issues.append("what_would_change_mind needs at least 3 conditions")
    if not _has_items(dossier.next_actions):
        issues.append("do_next (next_actions) needs at least 3 specific actions")
    if not _has_items(dossier.do_not_do):
        issues.append("do_not_do needs at least 3 explicit anti-actions")

    gate = dossier.approval_gate.strip()
    if len(gate) < MIN_DIRECT_ANSWER_LEN:
        issues.append("approval_gate is missing or too short")
    elif is_generic_verdict_text(gate):
        issues.append("approval_gate reads like generic placeholder text")

    return issues


def is_verdict_quality_sufficient(dossier: DecisionDossier) -> bool:
    return not verdict_quality_issues(dossier)


def ensure_verdict_quality_for_pack(dossier: DecisionDossier) -> None:
    issues = verdict_quality_issues(dossier)
    if issues:
        detail = "; ".join(issues)
        msg = (
            "Implementation pack blocked: verdict quality is insufficient. "
            f"Fix chair output first ({detail})."
        )
        raise VerdictQualityError(msg)


def format_verdict_sections_markdown(lines: list[str], dossier: DecisionDossier) -> None:
    """Append the required seven-part verdict format for council run.md."""
    lines.extend(
        [
            "",
            "## Direct Answer",
            "",
            dossier.direct_answer.strip() or "_Not recorded._",
            "",
            "## Decision",
            "",
            decision_label(dossier.decision_type),
            "",
            "## Why This Decision",
            "",
        ]
    )
    _append_bullets(lines, dossier.why_this_decision)
    lines.extend(["", "## What Would Change My Mind", ""])
    _append_bullets(lines, dossier.what_would_change_mind)
    lines.extend(["", "## Do Next", ""])
    _append_bullets(lines, dossier.next_actions)
    lines.extend(["", "## Do Not Do", ""])
    _append_bullets(lines, dossier.do_not_do)
    lines.extend(
        [
            "",
            "## Approval Gate",
            "",
            dossier.approval_gate.strip() or "_Not recorded._",
            "",
        ]
    )


def _append_bullets(lines: list[str], items: list[str]) -> None:
    cleaned = [item.strip() for item in items if item and item.strip()]
    if cleaned:
        lines.extend(f"- {item}" for item in cleaned)
    else:
        lines.append("_None recorded._")
