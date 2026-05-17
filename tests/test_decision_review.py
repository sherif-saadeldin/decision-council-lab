"""Slice 5.9 — Decision review loop and approval lifecycle.

Covers:
- default state of a new council run is `draft`
- approve / reject / archive flows persist to run.json + run.md
- review history is appended in order
- revising a parent and approving the child marks the parent as superseded
- pack gating refuses draft runs and honors `--allow-unapproved`
- run catalog surfaces the lifecycle status and revision_of fields
- /approve last, /reject last, /revise last, /review last, /archive
- /thread shows lifecycle markers
- archive blocks further review transitions
- no live network in tests
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from council.chat import (
    APPROVE_NOW_PROMPT,
    ChatSession,
    ChatSessionState,
    build_chat_context,
)
from council.config import Settings
from council.council_session import CouncilSessionResult
from council.decision_thread import DecisionThreadMeta, summarize_for_context
from council.models import CouncilRunResult, DecisionDossier, DecisionType
from council.providers.models import ProviderMetadata
from council.review import (
    ReviewTransitionError,
    approve_run,
    archive_run,
    mark_revision_of,
    reject_run,
)
from council.review_model import (
    LifecycleState,
    PACK_GATE_BLOCKED_REASON,
    ReviewAction,
    default_review,
    is_pack_allowed,
    resolve_actor,
)
from council.role_routing import build_council_routing
from council.run_catalog import get_run_summary, list_recent_runs
from council.storage import save_run


# --- fixtures ---------------------------------------------------------------


def _dossier(run_id: str, question: str = "Should we ship?") -> DecisionDossier:
    return DecisionDossier(
        run_id=run_id,
        decision_question=question,
        decision_type=DecisionType.PROCEED_WITH_CONSTRAINTS,
        direct_answer="Proceed.",
        why_this_decision=["a", "b", "c"],
        what_would_change_mind=["x", "y", "z"],
        next_actions=["A", "B", "C"],
        do_not_do=["D", "E", "F"],
        approval_gate="gate",
        evidence_gaps=["G", "H", "I"],
        recommendation="Proceed.",
        confidence_score=0.7,
    )


def _council_result(run_id: str = "run-1") -> CouncilRunResult:
    return CouncilRunResult(
        dossier=_dossier(run_id),
        agent_briefs=[],
        provider_metadata=ProviderMetadata(
            provider_name="mock",
            model_name="mock-council-v1",
            mode="mock",
            supports_structured_output=True,
            supports_streaming=False,
        ),
        council_mode="multi",
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


# --- defaults and pure helpers ---------------------------------------------


def test_default_review_is_draft_with_empty_history() -> None:
    review = default_review()
    assert review.status == LifecycleState.DRAFT
    assert review.history == []
    assert review.approved_by is None
    assert review.rejected_by is None
    assert review.superseded_by_run_id is None
    assert review.is_revision_of is None


def test_new_council_run_defaults_to_draft() -> None:
    result = _council_result()
    assert result.review.status == LifecycleState.DRAFT


def test_resolve_actor_falls_back_to_local(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("DCOUNCIL_REVIEW_ACTOR", "USER", "USERNAME"):
        monkeypatch.delenv(key, raising=False)
    assert resolve_actor() == "local"


def test_resolve_actor_explicit_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DCOUNCIL_REVIEW_ACTOR", "env-actor")
    assert resolve_actor("explicit-actor") == "explicit-actor"


def test_is_pack_allowed_only_for_approved() -> None:
    assert is_pack_allowed(default_review()) is False
    review = default_review()
    review.status = LifecycleState.APPROVED
    assert is_pack_allowed(review) is True
    review.status = LifecycleState.REJECTED
    assert is_pack_allowed(review) is False
    # Override unlocks any state.
    assert is_pack_allowed(default_review(), override=True) is True


# --- approve / reject / archive persistence ---------------------------------


@pytest.fixture
def saved_run(tmp_path: Path) -> tuple[Settings, str]:
    settings = Settings(llm_mode="mock", runs_dir=tmp_path, mock_model="mock-council-v1")
    save_run(_council_result("run-1"), settings=settings)
    return settings, "run-1"


def test_approve_run_writes_status_and_history(saved_run) -> None:
    settings, run_id = saved_run
    updated = approve_run(settings.runs_dir, run_id, actor="alice", note="LGTM")
    assert updated.review.status == LifecycleState.APPROVED
    assert updated.review.approved_by == "alice"
    assert updated.review.reviewed_at is not None
    assert updated.review.history[-1].action == ReviewAction.APPROVED
    assert updated.review.history[-1].actor == "alice"
    # Persisted to disk.
    raw = json.loads(
        (settings.runs_dir / run_id / "run.json").read_text(encoding="utf-8")
    )
    assert raw["review"]["status"] == "approved"
    assert raw["review"]["approved_by"] == "alice"
    md = (settings.runs_dir / run_id / "run.md").read_text(encoding="utf-8")
    assert "## Review Status" in md
    assert "approved" in md
    assert "alice" in md


def test_reject_run_requires_reason(saved_run) -> None:
    settings, run_id = saved_run
    with pytest.raises(ReviewTransitionError):
        reject_run(settings.runs_dir, run_id, actor="bob", note="")


def test_reject_run_persists_reason(saved_run) -> None:
    settings, run_id = saved_run
    updated = reject_run(
        settings.runs_dir, run_id, actor="bob", note="Confidence too low."
    )
    assert updated.review.status == LifecycleState.REJECTED
    assert updated.review.rejected_by == "bob"
    assert updated.review.review_reason == "Confidence too low."
    md = (settings.runs_dir / run_id / "run.md").read_text(encoding="utf-8")
    assert "Confidence too low." in md


def test_archive_run_blocks_further_transitions(saved_run) -> None:
    settings, run_id = saved_run
    archive_run(settings.runs_dir, run_id, actor="carol", note="EOL")
    with pytest.raises(ReviewTransitionError, match="archived"):
        approve_run(settings.runs_dir, run_id, actor="carol")


def test_revision_chain_supersedes_parent_on_approval(tmp_path: Path) -> None:
    settings = Settings(llm_mode="mock", runs_dir=tmp_path, mock_model="mock-council-v1")
    parent = _council_result("parent-1")
    save_run(parent, settings=settings)
    # Child is contextually linked AND marked as a revision.
    parent_context = summarize_for_context(parent)
    child_thread = DecisionThreadMeta(
        parent_run_id="parent-1",
        thread_id="parent-1",
        context_summary=parent_context,
    )
    child = _council_result("child-1")
    child.decision_thread = child_thread
    save_run(child, settings=settings)
    mark_revision_of(
        settings.runs_dir, "child-1", "parent-1", actor="reviewer", note="Round 2."
    )
    # Approve the revision; the parent flips to superseded automatically.
    approve_run(settings.runs_dir, "child-1", actor="reviewer", note="Accepted.")
    parent_summary = get_run_summary(tmp_path, "parent-1")
    child_summary = get_run_summary(tmp_path, "child-1")
    assert parent_summary.lifecycle_status == "superseded"
    assert parent_summary.superseded_by_run_id == "child-1"
    assert child_summary.lifecycle_status == "approved"
    assert child_summary.is_revision_of == "parent-1"


def test_review_history_appends_in_order(saved_run) -> None:
    settings, run_id = saved_run
    approve_run(settings.runs_dir, run_id, actor="alice", note="LGTM")
    # Subsequent reject is allowed (state transitions are recorded, not locked).
    updated = reject_run(
        settings.runs_dir, run_id, actor="bob", note="Reverted on review."
    )
    actions = [event.action for event in updated.review.history]
    assert actions == [ReviewAction.APPROVED, ReviewAction.REJECTED]


# --- run catalog surfaces ---------------------------------------------------


def test_run_catalog_surfaces_lifecycle_status(tmp_path: Path) -> None:
    settings = Settings(llm_mode="mock", runs_dir=tmp_path, mock_model="mock-council-v1")
    save_run(_council_result("draft-1"), settings=settings)
    save_run(_council_result("approved-1"), settings=settings)
    approve_run(tmp_path, "approved-1", actor="alice")
    summaries = {s.run_id: s for s in list_recent_runs(tmp_path, limit=10)}
    assert summaries["draft-1"].lifecycle_status == "draft"
    assert summaries["approved-1"].lifecycle_status == "approved"


def test_runs_show_includes_lifecycle_label(tmp_path: Path) -> None:
    """`render_runs_show` should print the status line for any run."""
    from council.cli import render_runs_show

    settings = Settings(llm_mode="mock", runs_dir=tmp_path, mock_model="mock-council-v1")
    save_run(_council_result("review-1"), settings=settings)
    approve_run(tmp_path, "review-1", actor="alice", note="Approved for ship.")
    summary = get_run_summary(tmp_path, "review-1")
    buf = StringIO()
    render_runs_show(Console(file=buf, force_terminal=True, width=120), summary)
    text = buf.getvalue()
    assert "Status" in text
    assert "approved" in text


# --- chat command surfaces --------------------------------------------------


def test_cmd_approve_last_marks_approved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(llm_mode="mock", runs_dir=tmp_path, mock_model="mock-council-v1")
    save_run(_council_result("chat-1"), settings=settings)
    state = ChatSessionState(last_run_id="chat-1")
    session, out, _ = _make_session(settings, monkeypatch, state=state)
    session.handle_line("/approve last shipped via chat")
    summary = get_run_summary(tmp_path, "chat-1")
    assert summary.lifecycle_status == "approved"
    text = out.getvalue()
    assert "Approved" in text
    # Rich auto-highlights digits inside the run id; match the stable prefix.
    assert "chat-" in text


def test_cmd_reject_last_requires_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(llm_mode="mock", runs_dir=tmp_path, mock_model="mock-council-v1")
    save_run(_council_result("chat-2"), settings=settings)
    state = ChatSessionState(last_run_id="chat-2")
    session, _, err = _make_session(settings, monkeypatch, state=state)
    session.handle_line("/reject last")  # no reason -> blocked
    assert "reason is required" in err.getvalue()
    summary = get_run_summary(tmp_path, "chat-2")
    assert summary.lifecycle_status == "draft"


def test_cmd_reject_last_with_reason_persists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(llm_mode="mock", runs_dir=tmp_path, mock_model="mock-council-v1")
    save_run(_council_result("chat-3"), settings=settings)
    state = ChatSessionState(last_run_id="chat-3")
    session, _, _ = _make_session(settings, monkeypatch, state=state)
    session.handle_line("/reject last too risky for this quarter")
    summary = get_run_summary(tmp_path, "chat-3")
    assert summary.lifecycle_status == "rejected"


def test_cmd_review_last_shows_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(llm_mode="mock", runs_dir=tmp_path, mock_model="mock-council-v1")
    save_run(_council_result("chat-4"), settings=settings)
    state = ChatSessionState(last_run_id="chat-4")
    session, out, _ = _make_session(settings, monkeypatch, state=state)
    session.handle_line("/approve last")
    out.truncate(0)
    out.seek(0)
    session.handle_line("/review last")
    text = out.getvalue()
    assert "Decision Review" in text
    assert "approved" in text
    assert "History" in text


def test_cmd_archive_last_marks_archived(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(llm_mode="mock", runs_dir=tmp_path, mock_model="mock-council-v1")
    save_run(_council_result("chat-5"), settings=settings)
    state = ChatSessionState(last_run_id="chat-5")
    session, _, _ = _make_session(settings, monkeypatch, state=state)
    session.handle_line("/archive last")
    summary = get_run_summary(tmp_path, "chat-5")
    assert summary.lifecycle_status == "archived"


def test_cmd_revise_loads_context_and_marks_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(llm_mode="mock", runs_dir=tmp_path, mock_model="mock-council-v1")
    save_run(_council_result("parent-rev"), settings=settings)

    def runner(_req, **_kw):
        return _session_result(_council_result("child-rev"))

    state = ChatSessionState(last_run_id="parent-rev")
    # Slice 6.0 adds a "Show full council breakdown?" confirm between
    # the short-form render and the inline approval prompt.
    # Sequence: Run revision=Y, Show full breakdown=N, Approve=N, Pack=N.
    confirms = iter([True, False, False, False])
    session, _, _ = _make_session(
        settings,
        monkeypatch,
        council_runner=runner,
        confirm_fn=lambda msg, default: next(confirms),
        state=state,
    )
    session.handle_line("/revise parent-rev make it cheaper")
    child_summary = get_run_summary(tmp_path, "child-rev")
    assert child_summary.is_revision_of == "parent-rev"
    # The revision context was installed on the session before the run.
    assert session.state.current_context is not None
    assert session.state.current_context.run_id == "parent-rev"


def test_after_council_run_chat_offers_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(llm_mode="mock", runs_dir=tmp_path, mock_model="mock-council-v1")

    def runner(_req, **_kw):
        return _session_result(_council_result("inline-1"))

    confirm_log: list[str] = []

    def confirm(message: str, default: bool) -> bool:
        confirm_log.append(message)
        # No to council confirm? Actually we use /council so no council confirm.
        # Sequence: APPROVE_NOW_PROMPT -> No, Create pack? -> No.
        return False

    session, out, _ = _make_session(
        settings, monkeypatch, council_runner=runner, confirm_fn=confirm
    )
    session.handle_line("/council Should we ship?")
    assert APPROVE_NOW_PROMPT in confirm_log
    assert "Decision state" in out.getvalue()
    assert "draft" in out.getvalue()


def test_thread_marks_revision_and_supersession(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(llm_mode="mock", runs_dir=tmp_path, mock_model="mock-council-v1")
    parent = _council_result("thr-parent")
    save_run(parent, settings=settings)
    child = _council_result("thr-child")
    child.decision_thread = DecisionThreadMeta(
        parent_run_id="thr-parent",
        thread_id="thr-parent",
        context_summary=summarize_for_context(parent),
    )
    save_run(child, settings=settings)
    mark_revision_of(tmp_path, "thr-child", "thr-parent", actor="r")
    approve_run(tmp_path, "thr-child", actor="r", note="ship")
    state = ChatSessionState(current_thread_id="thr-parent")
    session, out, _ = _make_session(settings, monkeypatch, state=state)
    session.handle_line("/thread")
    text = out.getvalue()
    assert "[root]" in text
    assert "[child]" in text
    assert "[revision]" in text
    assert "[approved]" in text
    assert "[superseded]" in text


def test_pack_gating_message_includes_recovery_hint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(llm_mode="mock", runs_dir=tmp_path, mock_model="mock-council-v1")
    save_run(_council_result("gate-1"), settings=settings)
    state = ChatSessionState(last_run_id="gate-1")
    session, _, err = _make_session(
        settings, monkeypatch, state=state, confirm_fn=lambda _m, _d: True
    )
    session.handle_line("/pack last")
    text = err.getvalue()
    assert PACK_GATE_BLOCKED_REASON in text
    assert "/approve last" in text
    assert "/review last" in text


def test_unknown_run_id_in_review_command_does_not_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(llm_mode="mock", runs_dir=tmp_path, mock_model="mock-council-v1")
    state = ChatSessionState(last_run_id="never-saved")
    session, _, err = _make_session(settings, monkeypatch, state=state)
    session.handle_line("/approve last")
    assert "Run not found" in err.getvalue()


# --- CLI override flag ------------------------------------------------------


def test_council_cli_blocks_pack_without_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from main import main as cli_main

    monkeypatch.chdir(tmp_path)
    code = cli_main(
        [
            "council",
            "Pack gating?",
            "--council-presets",
            "mock,mock,mock,mock,mock,mock",
            "--routing-mode",
            "manual",
            "--runs-dir",
            str(tmp_path),
            "--quiet",
            "--debate-rounds",
            "0",
            "--yes-pack",
        ]
    )
    assert code == 1
    assert "Pack generation blocked" in capsys.readouterr().err


def test_council_cli_override_allows_pack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from main import main as cli_main

    monkeypatch.chdir(tmp_path)
    code = cli_main(
        [
            "council",
            "Pack override?",
            "--council-presets",
            "mock,mock,mock,mock,mock,mock",
            "--routing-mode",
            "manual",
            "--runs-dir",
            str(tmp_path),
            "--quiet",
            "--debate-rounds",
            "0",
            "--yes-pack",
            "--allow-unapproved-pack",
        ]
    )
    assert code == 0
    runs = list(tmp_path.iterdir())
    assert any((r / "implementation_plan.md").exists() for r in runs)


# --- isolation guard --------------------------------------------------------


def test_review_module_does_not_open_network(saved_run) -> None:
    import socket

    real = socket.socket

    def blocked(*_a, **_k):  # pragma: no cover - guard
        raise AssertionError("review storage opened a socket")

    settings, run_id = saved_run
    try:
        socket.socket = blocked  # type: ignore[assignment]
        approve_run(settings.runs_dir, run_id, actor="net-actor")
        reject_run(settings.runs_dir, run_id, actor="net-actor", note="re-rejected")
    finally:
        socket.socket = real  # type: ignore[assignment]
