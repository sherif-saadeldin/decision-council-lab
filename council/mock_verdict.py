from __future__ import annotations

import re

from council.models import AgentBrief, AgentRole, DebateTranscript, DecisionDossier, DecisionType
from council.verdict_quality import decision_label

_QUESTION_STARTERS = re.compile(
    r"^(?:should|shall|can|could|would|will|do|does|did|is|are|was|were|"
    r"have|has|had|may|might|must)\b\s+",
    re.IGNORECASE,
)
_LEADING_PRONOUNS = re.compile(r"^(?:we|i|you|they|our|my|your)\s+", re.IGNORECASE)


def _decision_subject(question: str, signals: dict[str, bool]) -> str:
    """Short subject label for supporting fields — never the full question."""
    if signals["migrate"] and signals["build"]:
        return "the migration"
    if signals["build"] and signals["internal"]:
        return "the internal build"
    if signals["build"]:
        return "the build"
    if signals["hire"]:
        return "the hiring plan"
    if signals["invest"]:
        return "the investment"
    text = " ".join(question.split()).strip(" ?.!")
    text = _QUESTION_STARTERS.sub("", text)
    text = _LEADING_PRONOUNS.sub("", text)
    words = [word for word in re.findall(r"[a-z0-9]+", text.lower()) if len(word) > 2]
    if not words:
        return "this initiative"
    label = " ".join(words[:4])
    if len(label) > 40:
        label = " ".join(words[:3])
    return label


def _stance_opener(decision_type: DecisionType, *, hire_pause: bool = False) -> str:
    if hire_pause:
        return "No—pause"
    if decision_type == DecisionType.PROCEED:
        return "Yes—"
    if decision_type == DecisionType.PROCEED_WITH_CONSTRAINTS:
        return "Proceed with constraints—"
    if decision_type == DecisionType.PAUSE:
        return "Pause—"
    if decision_type == DecisionType.REJECT:
        return "Reject—"
    return "Pause—"


def _question_signals(question: str) -> dict[str, bool]:
    lowered = question.lower()
    return {
        "build": any(w in lowered for w in ("build", "create", "implement", "ship", "launch")),
        "hire": any(w in lowered for w in ("hire", "recruit", "headcount", "team")),
        "invest": any(w in lowered for w in ("invest", "fund", "budget", "spend")),
        "migrate": any(w in lowered for w in ("migrate", "rewrite", "refactor", "replace")),
        "internal": "internal" in lowered or "in-house" in lowered,
        "first": "first" in lowered or "before" in lowered,
    }


def _brief_implication(briefs: list[AgentBrief], role: AgentRole) -> str:
    for brief in briefs:
        if brief.role == role:
            return brief.decision_implication.strip()
    return ""


def build_mock_dossier(
    *,
    question: str,
    briefs: list[AgentBrief],
    run_id: str,
    debate_transcript: DebateTranscript | None,
) -> DecisionDossier:
    signals = _question_signals(question)
    subject = _decision_subject(question, signals)
    research_impl = _brief_implication(briefs, AgentRole.RESEARCH)
    skeptic_impl = _brief_implication(briefs, AgentRole.SKEPTIC)
    risk_impl = _brief_implication(briefs, AgentRole.RISK)
    operator_impl = _brief_implication(briefs, AgentRole.OPERATOR)
    hire_pause = bool(signals["hire"] and signals["invest"])

    if hire_pause:
        decision_type = DecisionType.PAUSE
        direct_answer = (
            f"{_stance_opener(decision_type, hire_pause=True)} hiring and major spend "
            "until role scope and runway are validated with finance and the hiring manager."
        )
    elif signals["migrate"] and signals["build"]:
        decision_type = DecisionType.PROCEED_WITH_CONSTRAINTS
        direct_answer = (
            f"{_stance_opener(decision_type)}migrate in phases only after a rollback plan "
            "is locked and the first slice is capped to production-critical paths."
        )
    elif signals["build"] and signals["internal"]:
        decision_type = DecisionType.PROCEED_WITH_CONSTRAINTS
        direct_answer = (
            f"{_stance_opener(decision_type)}ship an internal capability first, limiting scope "
            "to CLI workflows and measurable dossier quality before any external launch."
        )
    elif signals["build"]:
        decision_type = DecisionType.PROCEED_WITH_CONSTRAINTS
        direct_answer = (
            f"{_stance_opener(decision_type)}start with a narrow, testable slice and expand "
            "only after real usage proves value."
        )
    elif signals["invest"]:
        decision_type = DecisionType.PAUSE
        direct_answer = (
            f"{_stance_opener(decision_type)}the investment until evidence gaps on ROI, "
            "adoption, and downside scenarios are closed."
        )
    else:
        decision_type = DecisionType.PROCEED_WITH_CONSTRAINTS
        direct_answer = (
            f"{_stance_opener(decision_type)}run a time-boxed validation using council briefs "
            "before committing irreversible resources."
        )

    label = decision_label(decision_type)
    why = [
        f"Research supports action on {subject}: {research_impl or 'viable path with precedents.'}",
        f"Skeptic stress-test still allows {label} if scope stays narrow: "
        f"{skeptic_impl or 'risks are manageable with gates.'}",
        f"Operator sequencing makes {label} executable now: "
        f"{operator_impl or 'dependencies are clear enough to start.'}",
    ]
    change_mind = [
        "Two consecutive council runs fail to produce actionable direct answers.",
        "Stakeholders with veto power reject the scoped plan or refuse the approval gate.",
        f"Risk materializes in a way briefs did not anticipate: {risk_impl or 'new blocking dependency.'}",
    ]
    do_next = [
        f"Write a one-page scope for {subject} that matches the {label} verdict and share it with owners.",
        "Re-run the council after adding missing facts listed in evidence_gaps.",
        "Schedule a 30-minute decision review focused on Do Not Do items before build work.",
    ]
    do_not_do = [
        "Do not expand into auth, billing, or multi-tenant features before the first slice ships.",
        "Do not treat mock council output as proof of production readiness.",
        "Do not skip the approval gate and start implementation pack work early.",
    ]
    approval_gate = (
        f"Approve the {label} verdict and the scoped Do Next list before "
        "generating an implementation pack or assigning build resources."
    )

    disagreement_resolution = (
        f"Chair sides with Research and Operator on a constrained path for {subject} while "
        f"incorporating Skeptic and Risk limits: {skeptic_impl or 'narrow scope'} / "
        f"{risk_impl or 'explicit kill criteria'}."
    )
    if debate_transcript and debate_transcript.rounds:
        last = debate_transcript.rounds[-1]
        tensions = ", ".join(last.moderator.deciding_tensions) or "scope vs proof"
        disagreement_resolution = (
            f"After {debate_transcript.rounds_completed} debate rounds, "
            f"the chair adopts {label} and weights moderator tension: {tensions}."
        )

    recommendation = (
        f"{label.upper()} ({subject}): {direct_answer} "
        f"Deciding factor: execute the Do Next items under the stated approval gate."
    )

    return DecisionDossier(
        run_id=run_id,
        decision_question=question,
        decision_type=decision_type,
        direct_answer=direct_answer,
        why_this_decision=why,
        what_would_change_mind=change_mind,
        do_not_do=do_not_do,
        approval_gate=approval_gate,
        disagreement_resolution=disagreement_resolution,
        strongest_argument_for=(
            research_impl or f"Focused delivery on {subject} reduces ambiguity faster than debate alone."
        ),
        strongest_argument_against=(
            skeptic_impl or "Remaining unknowns could invalidate the plan after sunk cost."
        ),
        deciding_factor=(
            f"The council prioritizes evidence-backed sequencing over breadth; "
            f"{label} wins only with the listed constraints."
        ),
        confidence_rationale=(
            "Confidence reflects alignment on sequencing but penalizes unresolved "
            "evidence_gaps and any unsupported assumptions in specialist briefs."
        ),
        assumptions=[
            "The decision owner can enforce scope boundaries.",
            "Council briefs reflect the real constraints, not aspirational plans.",
            "A follow-up review will happen before irreversible spend.",
        ],
        arguments_for=why[:2] + [research_impl or "Clear upside if scoped."],
        arguments_against=[
            skeptic_impl or "Proof burden is still high.",
            risk_impl or "Downside scenarios remain under-explored.",
        ],
        risks=[
            "Scope creep before the first slice proves value.",
            "False confidence from generic synthesis not tied to the question.",
            "Skipping kill-criteria review after initial progress.",
        ],
        recommendation=recommendation,
        confidence_score=0.74 if decision_type == DecisionType.PROCEED_WITH_CONSTRAINTS else 0.68,
        kill_criteria=[
            "proposed: two consecutive runs produce actionable direct answers on the same question",
            "proposed: decision owner signs the approval gate before pack generation",
        ],
        next_actions=do_next,
        open_questions=[
            f"What is the smallest shippable slice for {subject}?",
            "Who owns veto if kill criteria trigger?",
        ],
        evidence_gaps=[
            "No measured baseline for outcomes was provided in the question.",
            "Stakeholder sign-off process is not defined.",
        ],
        proposed_metrics=[
            "proposed: decision owner confirms approval gate in writing",
            "proposed: two council reruns stay consistent on direct_answer",
        ],
        unsupported_assumptions=[
            "Assumes the team can pause parallel workstreams affecting delivery.",
        ],
    )
