from __future__ import annotations

from council.models import AgentBrief, AgentRole, DebateTranscript, DecisionDossier, DecisionType
from council.verdict_quality import decision_label


def _topic_snippet(question: str, *, max_len: int = 72) -> str:
    text = " ".join(question.split())
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


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
    topic = _topic_snippet(question)
    signals = _question_signals(question)
    research_impl = _brief_implication(briefs, AgentRole.RESEARCH)
    skeptic_impl = _brief_implication(briefs, AgentRole.SKEPTIC)
    risk_impl = _brief_implication(briefs, AgentRole.RISK)
    operator_impl = _brief_implication(briefs, AgentRole.OPERATOR)

    if signals["hire"] and signals["invest"]:
        decision_type = DecisionType.PAUSE
        direct_answer = (
            f"No—pause hiring and major spend on “{topic}” until you validate "
            "role scope and runway with finance and the hiring manager."
        )
    elif signals["migrate"] and signals["build"]:
        decision_type = DecisionType.PROCEED_WITH_CONSTRAINTS
        direct_answer = (
            f"Yes—migrate “{topic}” in phases, but only after you lock a rollback plan "
            "and cap the first slice to production-critical paths."
        )
    elif signals["build"] and signals["internal"]:
        decision_type = DecisionType.PROCEED_WITH_CONSTRAINTS
        direct_answer = (
            f"Yes—build “{topic}” as an internal capability first, with scope limited to "
            "CLI workflows and measurable dossier quality before any external launch."
        )
    elif signals["build"]:
        decision_type = DecisionType.PROCEED_WITH_CONSTRAINTS
        direct_answer = (
            f"Yes—start “{topic}” with a narrow, testable slice, because the council "
            "favors learning from real usage before expanding scope."
        )
    elif signals["invest"]:
        decision_type = DecisionType.PAUSE
        direct_answer = (
            f"Pause the investment in “{topic}” until you close the evidence gaps "
            "the council flagged on ROI, adoption, and downside scenarios."
        )
    else:
        decision_type = DecisionType.PROCEED_WITH_CONSTRAINTS
        direct_answer = (
            f"Proceed with constraints on “{topic}”: run a time-boxed validation "
            "using the council briefs before committing irreversible resources."
        )

    label = decision_label(decision_type)
    why = [
        f"Research finding supports action on “{topic}”: {research_impl or 'viable path with precedents.'}",
        f"Skeptic stress-test still allows {label} if scope stays narrow: {skeptic_impl or 'risks are manageable with gates.'}",
        f"Operator sequencing makes {label} executable now: {operator_impl or 'dependencies are clear enough to start.'}",
    ]
    change_mind = [
        f"Two consecutive council runs on “{topic}” fail to produce actionable direct answers.",
        "Stakeholders with veto power reject the scoped plan or refuse the approval gate.",
        f"Risk materializes on “{topic}” in a way briefs did not anticipate: {risk_impl or 'new blocking dependency.'}",
    ]
    do_next = [
        f"Write a one-page scope for “{topic}” that matches the {label} verdict and share it with owners.",
        "Run the council again on “{topic}” after you add any missing facts listed in evidence_gaps.",
        f"Schedule a 30-minute decision review on “{topic}” focused on Do Not Do items before build work.",
    ]
    do_not_do = [
        f"Do not expand “{topic}” into auth, billing, or multi-tenant features before the first slice ships.",
        "Do not treat mock council output as proof of production readiness for “{topic}”.",
        f"Do not skip the approval gate below and start implementation pack work for “{topic}”.",
    ]
    approval_gate = (
        f"Approve the {label} verdict and the scoped Do Next list for “{topic}” before "
        "generating an implementation pack or assigning build resources."
    )

    disagreement_resolution = (
        f"Chair sides with Research and Operator on a constrained path for “{topic}” while "
        f"incorporating Skeptic and Risk limits: {skeptic_impl or 'narrow scope'} / "
        f"{risk_impl or 'explicit kill criteria'}."
    )
    if debate_transcript and debate_transcript.rounds:
        last = debate_transcript.rounds[-1]
        tensions = ", ".join(last.moderator.deciding_tensions) or "scope vs proof"
        disagreement_resolution = (
            f"After {debate_transcript.rounds_completed} debate rounds on “{topic}”, "
            f"the chair adopts {label} and weights moderator tension: {tensions}."
        )

    recommendation = (
        f"{label.upper()} on “{topic}”: {direct_answer} "
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
            research_impl
            or f"Focused delivery on “{topic}” reduces ambiguity faster than debate alone."
        ),
        strongest_argument_against=(
            skeptic_impl
            or f"Remaining unknowns on “{topic}” could invalidate the plan after sunk cost."
        ),
        deciding_factor=(
            f"The council prioritizes evidence-backed sequencing for “{topic}” over "
            f"breadth; {label} wins only with the listed constraints."
        ),
        confidence_rationale=(
            "Confidence reflects alignment on sequencing but penalizes unresolved "
            "evidence_gaps and any unsupported assumptions in specialist briefs."
        ),
        assumptions=[
            f"The decision owner can enforce scope boundaries on “{topic}”.",
            "Council briefs reflect the real constraints, not aspirational plans.",
            "A follow-up review will happen before irreversible spend.",
        ],
        arguments_for=why[:2] + [research_impl or f"Clear upside for “{topic}” if scoped."],
        arguments_against=[
            skeptic_impl or f"Proof burden for “{topic}” is still high.",
            risk_impl or "Downside scenarios remain under-explored.",
        ],
        risks=[
            f"Scope creep on “{topic}” before the first slice proves value.",
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
            f"What is the smallest shippable slice of “{topic}”?",
            "Who owns veto if kill criteria trigger?",
        ],
        evidence_gaps=[
            f"No measured baseline for “{topic}” outcomes was provided in the question.",
            "Stakeholder sign-off process is not defined.",
        ],
        proposed_metrics=[
            "proposed: decision owner confirms approval gate in writing",
            "proposed: two council reruns stay consistent on direct_answer",
        ],
        unsupported_assumptions=[
            f"Assumes the team can pause parallel workstreams affecting “{topic}”.",
        ],
    )
