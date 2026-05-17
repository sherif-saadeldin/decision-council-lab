from __future__ import annotations

from io import StringIO
from pathlib import Path

from rich.console import Console

from council.chat import ChatSession, ChatSessionState, build_chat_context
from council.config import Settings
from council.council_session import CouncilSessionResult
from council.models import CouncilRunResult, DecisionDossier, DecisionType
from council.providers.models import ProviderMetadata
from council.rendering.review_renderer import render_review
from council.review_model import LifecycleState
from council.role_routing import build_council_routing
from council.runtime import RuntimeOptions
from council.services.council_service import (
    ChatCouncilExecutionResult,
    ChatCouncilRequest,
    CouncilRequest,
    CouncilService,
)
from council.services.intake_service import IntakeRequest, IntakeService
from council.services.pack_service import PackRequest, PackService
from council.services.review_service import ReviewRequest, ReviewService
from council.storage.run_store import FileRunStore, RunArtifacts


def _quality_result(run_id: str = "svc-1") -> CouncilRunResult:
    return CouncilRunResult(
        dossier=DecisionDossier(
            run_id=run_id,
            decision_question="Should we ship the pilot?",
            decision_type=DecisionType.PROCEED_WITH_CONSTRAINTS,
            direct_answer="Proceed with constraints — start with a narrow, measured pilot.",
            why_this_decision=["Evidence is enough", "Scope is reversible", "Risk is bounded"],
            what_would_change_mind=["Pilot fails", "Cost spikes", "Users reject it"],
            next_actions=["Define metric", "Ship behind flag", "Review results"],
            do_not_do=["Skip review", "Launch broadly", "Ignore support load"],
            approval_gate="Owner must approve the pilot scope before rollout.",
            evidence_gaps=["No long-term data", "No support data", "No adoption cohort"],
            recommendation="Proceed with constraints.",
            confidence_score=0.7,
        ),
        provider_metadata=ProviderMetadata(
            provider_name="mock",
            model_name="mock-council-v1",
            mode="mock",
            supports_structured_output=True,
            supports_streaming=False,
        ),
        council_mode="multi",
    )


def _session_result(result: CouncilRunResult | None = None) -> CouncilSessionResult:
    return CouncilSessionResult(
        result=result or _quality_result("chat-svc-1"),
        routing=build_council_routing(council_presets=["mock"]),
        role_play_warning=None,
    )


def test_council_service_standard_run_persists_artifacts(mock_settings: Settings) -> None:
    service = CouncilService(FileRunStore(mock_settings.runs_dir))
    execution = service.run_standard(
        CouncilRequest(
            question="Should we ship?",
            settings=mock_settings,
            runtime=RuntimeOptions(show_progress=False),
            debate_rounds=0,
        )
    )

    assert execution.json_path.is_file()
    assert execution.md_path.is_file()
    assert execution.result.dossier.run_id


def test_review_and_pack_services_handle_lifecycle(tmp_path: Path) -> None:
    store = FileRunStore(tmp_path)
    result = _quality_result("pack-svc-1")
    store.save_run(result)

    approved = ReviewService(store).approve(
        ReviewRequest(run_id="pack-svc-1", actor="tester", note="ship")
    )
    pack = PackService(store).generate(PackRequest(run_id="pack-svc-1"))

    assert approved.review.status == LifecycleState.APPROVED
    assert any(path.name == "implementation_plan.md" for path in pack.paths)


def test_intake_service_advances_without_console() -> None:
    service = IntakeService()
    started = service.start(initial_goal="Choose the next slice")
    answered = service.answer(
        IntakeRequest(
            intake=started.intake,
            field="preferred_mode",
            answer="deep",
        )
    )

    assert started.next_field == "preferred_mode"
    assert answered.intake.preferred_mode is not None
    assert "Choose the next slice" in answered.summary


def test_review_renderer_is_presentation_only(tmp_path: Path) -> None:
    store = FileRunStore(tmp_path)
    store.save_run(_quality_result("render-1"))
    result = ReviewService(store).approve(ReviewRequest(run_id="render-1", actor="alice"))
    out = StringIO()

    render_review(Console(file=out, force_terminal=True, width=120), "render-1", result)

    text = out.getvalue()
    assert "Decision Review" in text
    assert "approved" in text
    assert "alice" in text


def test_chat_calls_council_service_bridge(
    mock_settings: Settings,
    monkeypatch,
) -> None:
    monkeypatch.setattr("council.chat.load_config_file", lambda path=None: None)
    ctx = build_chat_context(mock_settings, config_profile_name=None)
    called: list[ChatCouncilRequest] = []

    class FakeCouncilService:
        def run_chat_council(self, request: ChatCouncilRequest) -> ChatCouncilExecutionResult:
            called.append(request)
            artifacts = RunArtifacts(
                json_path=mock_settings.runs_dir / "chat-svc-1" / "run.json",
                md_path=mock_settings.runs_dir / "chat-svc-1" / "run.md",
            )
            return ChatCouncilExecutionResult(
                session=_session_result(_quality_result("chat-svc-1")),
                artifacts=artifacts,
            )

    session = ChatSession(
        console=Console(file=StringIO(), force_terminal=True, width=120),
        error_console=Console(file=StringIO(), force_terminal=True, width=120),
        ctx=ctx,
        state=ChatSessionState(),
        confirm_fn=lambda _m, _d: False,
    )
    monkeypatch.setattr(session, "_council_service", lambda: FakeCouncilService())

    session.handle_line("/council Should we ship?")

    assert called
    assert called[0].question == "Should we ship?"
    assert session.state.last_run_id == "chat-svc-1"

