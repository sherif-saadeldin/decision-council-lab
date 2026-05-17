"""Guided decision intake — Slice 6.0.

Conversation-first surface over the council. A new chat user typing
natural language no longer goes straight to council; instead they walk
through a tiny structured intake (goal → mode → context → constraints →
success → risks → notes) and confirm a summary before any LLM call
fires.

Pure-logic module: no I/O, no network, no secrets. The chat session in
`council/chat.py` drives the flow; the council session in
`council/council_session.py` consumes the structured result and prepends
it to the chair question.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel, Field

INTAKE_BLOCK_HEADER = "Decision intake"


class DecisionMode(str, Enum):
    """How the user wants the council to help them think this time."""

    FAST_ANSWER = "fast_answer"
    DEEP_ANALYSIS = "deep_analysis"
    PRESSURE_TEST = "pressure_test"
    BUILD_PLAN = "build_plan"
    RISK_REVIEW = "risk_review"
    EXECUTION_ROADMAP = "execution_roadmap"


@dataclass(frozen=True)
class ModeProfile:
    """How a `DecisionMode` maps onto existing routing knobs.

    `slot_emphasis` is a free-text label only — V1 still uses the
    standard balanced/economy/premium presets. The label is rendered in
    `/mode` and in the intake summary so the chair (and the user) can
    see which lens the council is being asked to apply.
    """

    mode: DecisionMode
    label: str
    routing_mode: str  # economy / balanced / premium
    debate_rounds: int
    slot_emphasis: str
    description: str


MODE_PROFILES: dict[DecisionMode, ModeProfile] = {
    DecisionMode.FAST_ANSWER: ModeProfile(
        DecisionMode.FAST_ANSWER,
        label="Fast answer",
        routing_mode="economy",
        debate_rounds=0,
        slot_emphasis="cheapest viable chair, no debate",
        description="Quick verdict. No debate rounds. Lean on local/free presets.",
    ),
    DecisionMode.DEEP_ANALYSIS: ModeProfile(
        DecisionMode.DEEP_ANALYSIS,
        label="Deep analysis",
        routing_mode="balanced",
        debate_rounds=2,
        slot_emphasis="full council, multi-model debate",
        description="Multi-model debate. Balanced council; widest perspective.",
    ),
    DecisionMode.PRESSURE_TEST: ModeProfile(
        DecisionMode.PRESSURE_TEST,
        label="Pressure test",
        routing_mode="balanced",
        debate_rounds=2,
        slot_emphasis="skeptic + risk emphasized",
        description="Stress-test assumptions. Skeptic and risk roles weighted heavier.",
    ),
    DecisionMode.BUILD_PLAN: ModeProfile(
        DecisionMode.BUILD_PLAN,
        label="Build plan",
        routing_mode="balanced",
        debate_rounds=1,
        slot_emphasis="operator emphasized",
        description="Concrete next steps. Operator role guides scope and sequencing.",
    ),
    DecisionMode.RISK_REVIEW: ModeProfile(
        DecisionMode.RISK_REVIEW,
        label="Risk review",
        routing_mode="balanced",
        debate_rounds=1,
        slot_emphasis="risk-led, kill criteria heavy",
        description="Surfaces failure modes and kill criteria. Risk-led.",
    ),
    DecisionMode.EXECUTION_ROADMAP: ModeProfile(
        DecisionMode.EXECUTION_ROADMAP,
        label="Execution roadmap",
        routing_mode="balanced",
        debate_rounds=1,
        slot_emphasis="operator + implementation focus",
        description="Operator + implementation focus. Encourages pack generation.",
    ),
}


def mode_profile(mode: DecisionMode) -> ModeProfile:
    return MODE_PROFILES[mode]


_MODE_ORDER: tuple[DecisionMode, ...] = (
    DecisionMode.FAST_ANSWER,
    DecisionMode.DEEP_ANALYSIS,
    DecisionMode.PRESSURE_TEST,
    DecisionMode.BUILD_PLAN,
    DecisionMode.RISK_REVIEW,
    DecisionMode.EXECUTION_ROADMAP,
)


# Free-text aliases the user can type instead of the numeric choice.
_MODE_ALIASES: dict[str, DecisionMode] = {
    "1": DecisionMode.FAST_ANSWER,
    "fast": DecisionMode.FAST_ANSWER,
    "fast_answer": DecisionMode.FAST_ANSWER,
    "fast answer": DecisionMode.FAST_ANSWER,
    "quick": DecisionMode.FAST_ANSWER,
    "2": DecisionMode.DEEP_ANALYSIS,
    "deep": DecisionMode.DEEP_ANALYSIS,
    "deep_analysis": DecisionMode.DEEP_ANALYSIS,
    "deep analysis": DecisionMode.DEEP_ANALYSIS,
    "analysis": DecisionMode.DEEP_ANALYSIS,
    "3": DecisionMode.PRESSURE_TEST,
    "pressure": DecisionMode.PRESSURE_TEST,
    "pressure_test": DecisionMode.PRESSURE_TEST,
    "pressure test": DecisionMode.PRESSURE_TEST,
    "stress": DecisionMode.PRESSURE_TEST,
    "4": DecisionMode.BUILD_PLAN,
    "build": DecisionMode.BUILD_PLAN,
    "build_plan": DecisionMode.BUILD_PLAN,
    "build plan": DecisionMode.BUILD_PLAN,
    "plan": DecisionMode.BUILD_PLAN,
    "5": DecisionMode.RISK_REVIEW,
    "risk": DecisionMode.RISK_REVIEW,
    "risk_review": DecisionMode.RISK_REVIEW,
    "risk review": DecisionMode.RISK_REVIEW,
    "6": DecisionMode.EXECUTION_ROADMAP,
    "execution": DecisionMode.EXECUTION_ROADMAP,
    "execution_roadmap": DecisionMode.EXECUTION_ROADMAP,
    "execution roadmap": DecisionMode.EXECUTION_ROADMAP,
    "roadmap": DecisionMode.EXECUTION_ROADMAP,
}


def parse_mode(text: str) -> DecisionMode | None:
    """Best-effort parse for free-form mode answers ('2', 'deep', 'risk')."""
    if not text:
        return None
    return _MODE_ALIASES.get(text.strip().lower())


def mode_picker_prompt() -> str:
    """Compact numbered list shown when asking for a decision mode."""
    lines = ["How should I help you think? (number or name)"]
    for index, mode in enumerate(_MODE_ORDER, start=1):
        profile = MODE_PROFILES[mode]
        lines.append(f"  {index}. {profile.label} — {profile.description}")
    return "\n".join(lines)


@dataclass(frozen=True)
class IntakeQuestion:
    """One step in the guided conversation."""

    field: str
    prompt: str
    is_list: bool = False
    optional: bool = False


# Order matters — `_next_unanswered_field` walks this in order. Mode is
# asked second (right after the goal) so the rest of the flow can later
# branch on it if we want to prune questions per-mode.
INTAKE_QUESTIONS: tuple[IntakeQuestion, ...] = (
    IntakeQuestion(
        "goal",
        "What are you trying to achieve?",
    ),
    IntakeQuestion(
        "preferred_mode",
        mode_picker_prompt(),
    ),
    IntakeQuestion(
        "context",
        "What's the relevant context — team, market, stage?",
    ),
    IntakeQuestion(
        "constraints",
        (
            "What are your main constraints? Think time, money, legal, "
            "skills, team size, stress tolerance. List any that apply."
        ),
        is_list=True,
    ),
    IntakeQuestion(
        "success_definition",
        "What does success look like 3–6 months from now?",
    ),
    IntakeQuestion(
        "risks",
        "What's the biggest risk or fear you have about this?",
        is_list=True,
    ),
    IntakeQuestion(
        "notes",
        "Anything else I should know? (Press Enter to skip.)",
        optional=True,
    ),
)


class DecisionIntake(BaseModel):
    """Structured snapshot of the user's situation, collected via chat."""

    goal: str = ""
    context: str = ""
    constraints: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    success_definition: str = ""
    preferred_mode: DecisionMode | None = None
    notes: str = ""

    def has_field_value(self, field: str) -> bool:
        value = getattr(self, field, None)
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, list):
            return len(value) > 0
        return True


def empty_intake() -> DecisionIntake:
    return DecisionIntake()


def question_for_field(field: str) -> IntakeQuestion | None:
    for question in INTAKE_QUESTIONS:
        if question.field == field:
            return question
    return None


def next_intake_question(intake: DecisionIntake) -> IntakeQuestion | None:
    """Walk INTAKE_QUESTIONS in order; return the first unanswered required one."""
    for question in INTAKE_QUESTIONS:
        if question.optional:
            continue
        if not intake.has_field_value(question.field):
            return question
    # All required fields answered — check optional fields too so we
    # eventually ask 'notes'. We only return the first optional one that
    # is still empty so the user can skip it.
    for question in INTAKE_QUESTIONS:
        if not question.optional:
            continue
        if not intake.has_field_value(question.field):
            return question
    return None


def is_intake_complete(intake: DecisionIntake) -> bool:
    """All required fields filled. Optional fields are not required."""
    for question in INTAKE_QUESTIONS:
        if question.optional:
            continue
        if not intake.has_field_value(question.field):
            return False
    return True


def _split_list_answer(answer: str) -> list[str]:
    """Split a comma/semicolon/newline-separated answer into items."""
    parts = re.split(r"[,;\n]+", answer)
    return [p.strip() for p in parts if p.strip()]


def apply_intake_answer(
    intake: DecisionIntake,
    field: str,
    answer: str,
) -> DecisionIntake:
    """Return a new DecisionIntake with `field` updated from `answer`.

    The caller decides which field is being answered (typically the one
    `next_intake_question` returned). For the mode field, free-text and
    numeric answers are both accepted; unparseable values leave the
    field unset so the caller can re-ask.
    """
    question = question_for_field(field)
    if question is None:
        return intake
    text = answer.strip()
    update: dict[str, object] = {}
    if field == "preferred_mode":
        parsed = parse_mode(text)
        if parsed is not None:
            update["preferred_mode"] = parsed
    elif question.is_list:
        update["constraints" if field == "constraints" else field] = _split_list_answer(text)
    else:
        update[field] = text
    return intake.model_copy(update=update)


def format_intake_summary(intake: DecisionIntake) -> str:
    """Render the confirmation summary the user sees before council fires.

    Concise on purpose — this is the panel the user reads to decide
    whether the council has the right context. Verbose dumps belong in
    `run.md` / `/show`.
    """
    mode_label = "—"
    if intake.preferred_mode is not None:
        mode_label = mode_profile(intake.preferred_mode).label
    lines = [
        "Here's my understanding:",
        "",
        f"Goal              : {intake.goal or '—'}",
        f"Mode              : {mode_label}",
        f"Context           : {intake.context or '—'}",
    ]
    if intake.constraints:
        lines.append("Constraints       :")
        lines.extend(f"  - {item}" for item in intake.constraints)
    else:
        lines.append("Constraints       : —")
    lines.append(f"Success criteria  : {intake.success_definition or '—'}")
    if intake.risks:
        lines.append("Biggest risk      :")
        lines.extend(f"  - {item}" for item in intake.risks)
    else:
        lines.append("Biggest risk      : —")
    if intake.notes.strip():
        lines.append(f"Notes             : {intake.notes.strip()}")
    return "\n".join(lines)


def format_intake_block(intake: DecisionIntake) -> str:
    """Structured text block prepended to the chair question.

    Mirrors `decision_thread.format_context_block` so both layers compose
    deterministically into the chair prompt.
    """
    mode_label = mode_profile(intake.preferred_mode).label if intake.preferred_mode else "—"
    lines = [INTAKE_BLOCK_HEADER]
    if intake.goal:
        lines.append(f"Goal: {intake.goal}")
    if intake.preferred_mode:
        lines.append(f"Preferred mode: {mode_label}")
    if intake.context:
        lines.append(f"Context: {intake.context}")
    if intake.constraints:
        lines.append("Constraints:")
        lines.extend(f"  - {item}" for item in intake.constraints)
    if intake.success_definition:
        lines.append(f"Success criteria: {intake.success_definition}")
    if intake.risks:
        lines.append("Biggest risks:")
        lines.extend(f"  - {item}" for item in intake.risks)
    if intake.notes.strip():
        lines.append(f"Notes: {intake.notes.strip()}")
    return "\n".join(lines)


def compose_question_with_intake(question: str, intake: DecisionIntake) -> str:
    """Prepend the structured intake block to the chair question."""
    block = format_intake_block(intake)
    return f"{block}\n\nQuestion:\n{question.strip()}"


def routing_for_mode(mode: DecisionMode | None) -> ModeProfile | None:
    """Look up the routing/debate defaults for a decision mode."""
    if mode is None:
        return None
    return mode_profile(mode)


def editable_fields() -> tuple[str, ...]:
    return tuple(q.field for q in INTAKE_QUESTIONS)
