"""Slice 5.8 — Decision threads and contextual follow-ups.

Covers:
- follow-up phrase detection (positive + negative cases)
- DecisionContext summary generation from a CouncilRunResult
- DecisionThreadMeta persistence on a council run (JSON + markdown)
- parent_run_id / thread_id linkage including grand-child inheritance
- chat /context, /forget, /use, /thread commands
- follow-up prompt auto-attaches previous context when user accepts
- context cleared by /forget
- topic change does not auto-attach context
- list_thread_runs returns the full chain
- no live network (no provider calls in tests)
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from council.chat import (
    FOLLOW_UP_PROMPT_TEMPLATE,
    ChatSession,
    ChatSessionState,
    build_chat_context,
)
from council.config import Settings
from council.council_session import CouncilSessionRequest, CouncilSessionResult, run_council_session
from council.decision_thread import (
    CONTEXT_BLOCK_HEADER,
    CONTEXT_PREVIEW_LIMIT,
    DecisionThreadMeta,
    build_thread_meta,
    compose_question_with_context,
    derive_thread_id,
    format_context_block,
    looks_like_follow_up,
    summarize_for_context,
)
from council.models import (
    CouncilRunResult,
    DecisionDossier,
    DecisionType,
    RUN_SCHEMA_VERSION,
)
from council.providers.models import ProviderMetadata
from council.role_routing import build_council_routing
from council.run_catalog import get_run_summary, list_recent_runs, list_thread_runs
from council.runtime import RuntimeOptions
from council.storage import save_run


# --- fixtures ---------------------------------------------------------------


def _mock_dossier(*, run_id: str, question: str = "Should we ship?") -> DecisionDossier:
    return DecisionDossier(
        run_id=run_id,
        decision_question=question,
        decision_type=DecisionType.PROCEED_WITH_CONSTRAINTS,
        direct_answer="Proceed with constraints — ship behind a flag.",
        why_this_decision=["a", "b", "c"],
        what_would_change_mind=["x", "y", "z"],
        next_actions=["Define metrics", "Ship behind flag", "Review in 2 weeks", "Extra ignored"],
        do_not_do=["Skip metrics", "Default-on", "Hardcode", "Extra ignored"],
        approval_gate="VP must approve flag default before GA.",
        evidence_gaps=["No A/B baseline", "No churn data", "No tier mix", "Extra ignored"],
        recommendation="Proceed with constraints.",
        confidence_score=0.7,
    )


def _mock_council_result(
    *,
    run_id: str = "run-root",
    question: str = "Should we ship?",
    decision_thread: DecisionThreadMeta | None = None,
) -> CouncilRunResult:
    return CouncilRunResult(
        dossier=_mock_dossier(run_id=run_id, question=question),
        agent_briefs=[],
        provider_metadata=ProviderMetadata(
            provider_name="mock",
            model_name="mock-council-v1",
            mode="mock",
            supports_structured_output=True,
            supports_streaming=False,
        ),
        council_mode="multi",
        decision_thread=decision_thread,
    )


def _session_result(result: CouncilRunResult) -> CouncilSessionResult:
    return CouncilSessionResult(
        result=result,
        routing=build_council_routing(council_presets=["mock"]),
        role_play_warning=None,
    )


def _make_session(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    *,
    council_runner=None,
    confirm_fn=None,
    state: ChatSessionState | None = None,
) -> tuple[ChatSession, StringIO, StringIO]:
    monkeypatch.setattr("council.chat.load_config_file", lambda path=None: None)
    ctx = build_chat_context(settings, config_profile_name=None)
    out, err = StringIO(), StringIO()
    session = ChatSession(
        console=Console(file=out, force_terminal=True, width=120),
        error_console=Console(file=err, force_terminal=True, width=120),
        ctx=ctx,
        state=state or ChatSessionState(),
        council_runner=council_runner,
        confirm_fn=confirm_fn or (lambda _m, _d: False),
    )
    return session, out, err


# --- follow-up detection ----------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "what if we used GCC instead?",
        "Make it cheaper",
        "what about Anthropic?",
        "Revise that recommendation",
        "improve it",
        "continue",
        "rethink this",
        "what changed since last time?",
        "Why did we decide that?",
    ],
)
def test_looks_like_follow_up_positive(text: str) -> None:
    assert looks_like_follow_up(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "Should we adopt Postgres?",
        "Pick a CI provider",
        "uv run python main.py chat",  # also a shell paste — guarded separately
        "Plan the Q3 roadmap.",
        "",
        "   ",
    ],
)
def test_looks_like_follow_up_negative(text: str) -> None:
    assert looks_like_follow_up(text) is False


# --- summary + composition --------------------------------------------------


def test_summarize_for_context_caps_lists_and_keeps_essentials() -> None:
    result = _mock_council_result()
    context = summarize_for_context(result)
    assert context.run_id == "run-root"
    assert context.decision_question == "Should we ship?"
    assert context.decision_type == DecisionType.PROCEED_WITH_CONSTRAINTS
    assert context.next_actions == [
        "Define metrics",
        "Ship behind flag",
        "Review in 2 weeks",
    ]
    assert context.do_not_do == ["Skip metrics", "Default-on", "Hardcode"]
    assert context.evidence_gaps == [
        "No A/B baseline",
        "No churn data",
        "No tier mix",
    ]
    assert len(context.next_actions) <= CONTEXT_PREVIEW_LIMIT
    assert context.approval_gate.startswith("VP must")


def test_format_context_block_includes_header_and_parent_id() -> None:
    context = summarize_for_context(_mock_council_result())
    block = format_context_block(context)
    assert CONTEXT_BLOCK_HEADER in block
    assert "Parent run: run-root" in block
    assert "Prior decision: proceed_with_constraints" in block
    assert "Prior next actions:" in block


def test_compose_question_with_context_prefixes_block() -> None:
    context = summarize_for_context(_mock_council_result())
    composed = compose_question_with_context("Make it cheaper.", context)
    assert composed.startswith(CONTEXT_BLOCK_HEADER)
    assert "Follow-up question:\nMake it cheaper." in composed


# --- thread linkage helpers -------------------------------------------------


def test_derive_thread_id_anchors_on_parent_run_id() -> None:
    parent = _mock_council_result(run_id="root-001")
    assert derive_thread_id(parent) == "root-001"


def test_derive_thread_id_inherits_existing_thread() -> None:
    parent_meta = DecisionThreadMeta(
        parent_run_id="root-001",
        thread_id="root-001",
        context_summary=summarize_for_context(_mock_council_result(run_id="root-001")),
    )
    child = _mock_council_result(run_id="child-002", decision_thread=parent_meta)
    grandchild_thread_id = derive_thread_id(child)
    # Grandchild inherits the thread anchor, not the child's own run id.
    assert grandchild_thread_id == "root-001"


def test_build_thread_meta_carries_context_summary() -> None:
    parent = _mock_council_result(run_id="root-001")
    meta = build_thread_meta(parent)
    assert meta.parent_run_id == "root-001"
    assert meta.thread_id == "root-001"
    assert meta.context_summary.run_id == "root-001"


# --- run persistence (Pydantic + JSON + markdown) ---------------------------


def test_council_run_result_round_trip_preserves_decision_thread() -> None:
    parent_context = summarize_for_context(_mock_council_result(run_id="root-001"))
    meta = DecisionThreadMeta(
        parent_run_id="root-001",
        thread_id="root-001",
        context_summary=parent_context,
    )
    child = _mock_council_result(run_id="child-002", decision_thread=meta)
    payload = child.model_dump(mode="json")
    assert payload["decision_thread"]["parent_run_id"] == "root-001"
    assert payload["decision_thread"]["thread_id"] == "root-001"
    restored = CouncilRunResult.model_validate(payload)
    assert restored.decision_thread is not None
    assert restored.decision_thread.parent_run_id == "root-001"
    assert restored.decision_thread.context_summary.next_actions == parent_context.next_actions


def test_save_run_writes_thread_metadata_to_json_and_markdown(tmp_path: Path) -> None:
    settings = Settings(llm_mode="mock", runs_dir=tmp_path, mock_model="mock-council-v1")
    parent_context = summarize_for_context(_mock_council_result(run_id="root-001"))
    meta = DecisionThreadMeta(
        parent_run_id="root-001",
        thread_id="root-001",
        context_summary=parent_context,
    )
    child = _mock_council_result(run_id="child-002", decision_thread=meta)
    json_path, md_path = save_run(child, settings=settings)
    raw_json = json_path.read_text(encoding="utf-8")
    raw_md = md_path.read_text(encoding="utf-8")
    assert '"decision_thread"' in raw_json
    assert '"parent_run_id": "root-001"' in raw_json
    assert "Previous Context Used" in raw_md
    assert "root-001" in raw_md
    assert "Prior next actions" in raw_md


def test_run_catalog_surfaces_thread_relationships(tmp_path: Path) -> None:
    settings = Settings(llm_mode="mock", runs_dir=tmp_path, mock_model="mock-council-v1")
    parent = _mock_council_result(run_id="root-001")
    save_run(parent, settings=settings)
    meta = DecisionThreadMeta(
        parent_run_id="root-001",
        thread_id="root-001",
        context_summary=summarize_for_context(parent),
    )
    child = _mock_council_result(run_id="child-002", decision_thread=meta)
    save_run(child, settings=settings)
    child_summary = get_run_summary(tmp_path, "child-002")
    assert child_summary.parent_run_id == "root-001"
    assert child_summary.thread_id == "root-001"
    chain = list_thread_runs(tmp_path, "root-001")
    ids = [item.run_id for item in chain]
    assert ids == ["root-001", "child-002"]


def test_runs_list_includes_thread_column(tmp_path: Path) -> None:
    settings = Settings(llm_mode="mock", runs_dir=tmp_path, mock_model="mock-council-v1")
    save_run(_mock_council_result(run_id="root-001"), settings=settings)
    summaries = list_recent_runs(tmp_path, limit=10)
    assert summaries[0].run_id == "root-001"
    # Root run with no decision_thread block returns None for both fields.
    assert summaries[0].thread_id is None
    assert summaries[0].parent_run_id is None


# --- contextual council invocation through the chat session -----------------


@pytest.fixture
def mock_chat_settings(tmp_path: Path) -> Settings:
    return Settings(llm_mode="mock", runs_dir=tmp_path, mock_model="mock-council-v1")


def test_follow_up_phrase_prompts_for_previous_context_and_attaches(
    mock_chat_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_request: dict[str, CouncilSessionRequest] = {}

    def runner(request, **_kw):
        captured_request["req"] = request
        return _session_result(_mock_council_result(run_id="run-followup-001"))

    confirms: list[tuple[str, bool]] = []

    def confirm(message: str, default: bool) -> bool:
        confirms.append((message, default))
        # Yes to: follow-up attach, Yes to: run council, No to: create pack.
        return "pack" not in message.lower()

    state = ChatSessionState(last_run_id="run-root", current_thread_id="run-root")
    session, _, _ = _make_session(
        mock_chat_settings, monkeypatch, council_runner=runner, confirm_fn=confirm, state=state
    )
    # Seed the runs_dir so /use / context attach can load.
    parent = _mock_council_result(run_id="run-root")
    save_run(parent, settings=mock_chat_settings)

    session.handle_line("Make it cheaper")
    request = captured_request["req"]
    assert request.parent_context is not None
    assert request.parent_context.run_id == "run-root"
    assert request.parent_run_id == "run-root"
    assert any(
        FOLLOW_UP_PROMPT_TEMPLATE.format(run_id="run-root") == msg
        for msg, _ in confirms
    )


def test_topic_change_does_not_auto_attach_previous_context(
    mock_chat_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, CouncilSessionRequest] = {}

    def runner(request, **_kw):
        captured["req"] = request
        return _session_result(_mock_council_result(run_id="new-topic-001"))

    state = ChatSessionState(last_run_id="run-root", current_thread_id="run-root")
    session, _, _ = _make_session(
        mock_chat_settings,
        monkeypatch,
        council_runner=runner,
        confirm_fn=lambda _m, _d: True,
        state=state,
    )
    save_run(_mock_council_result(run_id="run-root"), settings=mock_chat_settings)
    session.handle_line("Should we adopt Postgres for analytics?")
    request = captured["req"]
    # No follow-up phrase was used, so no parent context attachment.
    assert request.parent_context is None
    assert request.parent_run_id is None


def test_cmd_use_loads_context_from_disk(
    mock_chat_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_run(_mock_council_result(run_id="root-load"), settings=mock_chat_settings)
    session, out, _ = _make_session(mock_chat_settings, monkeypatch)
    session.handle_line("/use root-load")
    assert session.state.current_context is not None
    assert session.state.current_context.run_id == "root-load"
    assert session.state.current_thread_id == "root-load"
    assert "root-load" in out.getvalue()


def test_cmd_use_unknown_run_does_not_set_context(
    mock_chat_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _, err = _make_session(mock_chat_settings, monkeypatch)
    session.handle_line("/use no-such-run")
    assert session.state.current_context is None
    assert "Run not found" in err.getvalue()


def test_cmd_context_shows_active_context(
    mock_chat_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_run(_mock_council_result(run_id="ctx-001"), settings=mock_chat_settings)
    session, out, _ = _make_session(mock_chat_settings, monkeypatch)
    session.handle_line("/use ctx-001")
    out.truncate(0)
    out.seek(0)
    session.handle_line("/context")
    text = out.getvalue()
    assert "Active Decision Context" in text
    assert "ctx-001" in text
    assert "proceed_with_constraints" in text


def test_cmd_context_without_active_context_explains(
    mock_chat_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, out, _ = _make_session(mock_chat_settings, monkeypatch)
    session.handle_line("/context")
    assert "No active decision context" in out.getvalue()


def test_cmd_forget_clears_context_and_session_memory(
    mock_chat_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_run(_mock_council_result(run_id="ctx-002"), settings=mock_chat_settings)
    state = ChatSessionState(
        last_run_id="ctx-002",
        last_question="prior",
        last_direct_answer="prior answer",
        last_decision_type="proceed",
        current_thread_id="ctx-002",
    )
    session, _, _ = _make_session(mock_chat_settings, monkeypatch, state=state)
    session.handle_line("/use ctx-002")
    assert session.state.current_context is not None
    session.handle_line("/forget")
    assert session.state.current_context is None
    assert session.state.current_context_run_id is None
    assert session.state.current_thread_id is None
    assert session.state.last_run_id is None
    assert session.state.last_question is None
    assert session.state.last_pack_paths == []


def test_cmd_thread_lists_chain_runs(
    mock_chat_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = _mock_council_result(run_id="thread-root")
    save_run(parent, settings=mock_chat_settings)
    meta = DecisionThreadMeta(
        parent_run_id="thread-root",
        thread_id="thread-root",
        context_summary=summarize_for_context(parent),
    )
    child = _mock_council_result(run_id="thread-child", decision_thread=meta)
    save_run(child, settings=mock_chat_settings)
    state = ChatSessionState(current_thread_id="thread-root")
    session, out, _ = _make_session(mock_chat_settings, monkeypatch, state=state)
    session.handle_line("/thread")
    text = out.getvalue()
    assert "Decision Thread" in text
    assert "thread-root" in text
    assert "thread-child" in text


def test_session_memory_populated_after_council_run(
    mock_chat_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def runner(_req, **_kw):
        return _session_result(_mock_council_result(run_id="mem-001"))

    session, _, _ = _make_session(
        mock_chat_settings,
        monkeypatch,
        council_runner=runner,
        confirm_fn=lambda _m, _d: False,  # decline pack
    )
    session.handle_line("/council Should we ship?")
    state = session.state
    assert state.last_run_id == "mem-001"
    assert state.last_question == "Should we ship?"
    assert state.last_direct_answer.startswith("Proceed with constraints")
    assert state.last_decision_type == "proceed_with_constraints"
    assert state.last_routing_mode == "economy"
    # No prior thread, so this run anchors a new one rooted at its own id.
    assert state.current_thread_id == "mem-001"


def test_chat_does_not_open_network(
    mock_chat_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard: thread loading and command dispatch open no sockets."""
    import socket

    real_socket = socket.socket

    def blocked(*_a, **_k):  # pragma: no cover - guard
        raise AssertionError("decision-thread chat path opened a socket")

    save_run(_mock_council_result(run_id="net-001"), settings=mock_chat_settings)
    session, _, _ = _make_session(mock_chat_settings, monkeypatch)
    try:
        socket.socket = blocked  # type: ignore[assignment]
        session.handle_line("/use net-001")
        session.handle_line("/context")
        session.handle_line("/forget")
        session.handle_line("/thread")
    finally:
        socket.socket = real_socket  # type: ignore[assignment]


# --- end-to-end run_council_session with parent_context ---------------------


def test_run_council_session_with_parent_context_persists_thread_meta(
    mock_chat_settings: Settings,
) -> None:
    parent_result = _mock_council_result(run_id="e2e-root")
    parent_context = summarize_for_context(parent_result)
    request = CouncilSessionRequest(
        question="Make it cheaper.",
        routing_mode="manual",
        council_presets=["mock"],
        debate_rounds=0,
        base_settings=mock_chat_settings,
        runtime=RuntimeOptions(show_progress=False),
        parent_context=parent_context,
        parent_run_id=parent_context.run_id,
        thread_id=parent_context.run_id,
    )
    session = run_council_session(request)
    result = session.result
    assert result.decision_thread is not None
    assert result.decision_thread.parent_run_id == "e2e-root"
    assert result.decision_thread.thread_id == "e2e-root"
    # The chair received the structured context block as part of the question.
    assert CONTEXT_BLOCK_HEADER in result.dossier.decision_question
    assert "Follow-up question:" in result.dossier.decision_question
    # Schema version reflects slice 5.8.
    assert result.schema_version == RUN_SCHEMA_VERSION
