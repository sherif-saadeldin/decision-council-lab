from __future__ import annotations

from dataclasses import dataclass

from council.intake import (
    DecisionIntake,
    apply_intake_answer,
    empty_intake,
    format_intake_summary,
    is_intake_complete,
    next_intake_question,
    parse_mode,
)


@dataclass(frozen=True)
class IntakeRequest:
    intake: DecisionIntake
    field: str
    answer: str


@dataclass(frozen=True)
class IntakeResult:
    intake: DecisionIntake
    next_field: str | None
    complete: bool
    summary: str
    error: str | None = None


class IntakeService:
    def start(self, *, initial_goal: str | None = None) -> IntakeResult:
        intake = empty_intake()
        if initial_goal:
            intake = apply_intake_answer(intake, "goal", initial_goal)
        return self._result(intake)

    def answer(self, request: IntakeRequest) -> IntakeResult:
        if request.field == "preferred_mode" and parse_mode(request.answer.strip()) is None:
            return self._result(
                request.intake,
                error=(
                    "I didn't recognize that mode. Try a number 1-6 or a keyword "
                    "like 'deep', 'risk', 'plan'."
                ),
            )
        return self._result(
            apply_intake_answer(request.intake, request.field, request.answer)
        )

    def _result(self, intake: DecisionIntake, *, error: str | None = None) -> IntakeResult:
        question = next_intake_question(intake)
        return IntakeResult(
            intake=intake,
            next_field=question.field if question is not None else None,
            complete=is_intake_complete(intake),
            summary=format_intake_summary(intake),
            error=error,
        )

