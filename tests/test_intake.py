"""Slice 6.0 — Guided Decision Conversation Flow.

Covers the conversation-first surface:
- DecisionIntake model + flow helpers (pure-logic)
- DecisionMode mapping to routing/debate defaults
- Chat /intake, /edit, /clear-intake, /mode, /summary
- Natural input opens the guided intake; first message becomes the goal
- Mid-intake answers advance through questions in order
- Intake summary panel + confirm/edit/discard branches
- Council receives the intake context (prepended to chair question, saved on result)
- Short-form result by default; full breakdown opt-in
- Existing /council bypass still skips intake
- Schema 1.10 round-trip preserves intake
- No live network
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from council.chat import (
    INTAKE_RUN_PROMPT,
    SHOW_FULL_BREAKDOWN_PROMPT,
    ChatSession,
    ChatSessionState,
    build_chat_context,
)
from council.config import Settings
from council.council_session import CouncilSessionRequest, CouncilSessionResult
from council.intake import (
    INTAKE_QUESTIONS,
    DecisionIntake,
    DecisionMode,
    apply_intake_answer,
    compose_question_with_intake,
    empty_intake,
    format_intake_block,
    format_intake_summary,
    is_intake_complete,
    mode_picker_prompt,
    mode_profile,
    next_intake_question,
    parse_mode,
    routing_for_mode,
)
from council.models import (
    CouncilRunResult,
    DecisionDossier,
    DecisionType,
    RUN_SCHEMA_VERSION,
)
from council.providers.models import ProviderMetadata
from council.role_routing import build_council_routing
from council.storage import save_run


# --- fixtures ---------------------------------------------------------------


def _good_dossier(run_id: str) -> DecisionDossier:
    return DecisionDossier(
        run_id=run_id,
        decision_question="Should we ship the caching layer?",
        decision_type=DecisionType.PROCEED_WITH_CONSTRAINTS,
        direct_answer="Proceed with constraints — start with a narrow, flagged pilot.",
        why_this_decision=["Solid evidence", "Manageable scope", "Reversible plan"],
        what_would_change_mind=["Adoption stalls", "Budget cut", "Provider degrades"],
        next_actions=["Define cache hit-rate target", "Ship behind a flag", "Review in 2 weeks"],
        do_not_do=["Default-on the flag", "Skip metrics review", "Couple to billing"],
        approval_gate="VP must approve flag default before GA.",
        evidence_gaps=["No A/B baseline yet", "No churn data", "No tier mix"],
        recommendation="Proceed with constraints — pilot for two weeks.",
        confidence_score=0.7,
    )


def _result(run_id: str = "intake-1") -> CouncilRunResult:
    return CouncilRunResult(
        dossier=_good_dossier(run_id),
        agent_briefs=[],
        provider_metadata=ProviderMetadata(
            provider_name="mock",
            model_name="mock-council-v1",
            mode="mock",
        ),
        council_mode="multi",
    )


def _session_result(run_id: str = "intake-1") -> CouncilSessionResult:
    return CouncilSessionResult(
        result=_result(run_id),
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
    input_fn=None,
) -> tuple[ChatSession, StringIO, StringIO]:
    monkeypatch.setattr("council.chat.load_config_file", lambda path=None: None)
    ctx = build_chat_context(settings, config_profile_name=None)
    out, err = StringIO(), StringIO()
    kwargs: dict = dict(
        console=Console(file=out, force_terminal=True, width=120),
        error_console=Console(file=err, force_terminal=True, width=120),
        ctx=ctx,
        state=state or ChatSessionState(),
        council_runner=council_runner,
        confirm_fn=confirm_fn or (lambda _m, _d: False),
    )
    if input_fn is not None:
        kwargs["input_fn"] = input_fn
    session = ChatSession(**kwargs)
    return session, out, err


# --- pure-logic intake module ----------------------------------------------


def test_default_intake_is_empty() -> None:
    intake = empty_intake()
    assert intake.goal == ""
    assert intake.constraints == []
    assert intake.preferred_mode is None
    assert not is_intake_complete(intake)


def test_next_intake_question_walks_order_skipping_optional() -> None:
    intake = empty_intake()
    # goal first
    q = next_intake_question(intake)
    assert q is not None and q.field == "goal"
    intake = apply_intake_answer(intake, "goal", "Build an AI startup.")
    # then preferred_mode
    q = next_intake_question(intake)
    assert q is not None and q.field == "preferred_mode"
    intake = apply_intake_answer(intake, "preferred_mode", "deep")
    assert intake.preferred_mode == DecisionMode.DEEP_ANALYSIS
    # context → constraints → success → risks → (optional notes)
    intake = apply_intake_answer(intake, "context", "Solo founder, pre-seed.")
    intake = apply_intake_answer(intake, "constraints", "time, money, solo")
    assert intake.constraints == ["time", "money", "solo"]
    intake = apply_intake_answer(intake, "success_definition", "10 paying users.")
    intake = apply_intake_answer(intake, "risks", "burnout, slow growth")
    assert is_intake_complete(intake)
    # The flow still surfaces the optional notes question last.
    q = next_intake_question(intake)
    assert q is not None and q.field == "notes"


@pytest.mark.parametrize(
    "text,mode",
    [
        ("1", DecisionMode.FAST_ANSWER),
        ("fast", DecisionMode.FAST_ANSWER),
        ("Fast Answer", DecisionMode.FAST_ANSWER),
        ("2", DecisionMode.DEEP_ANALYSIS),
        ("deep", DecisionMode.DEEP_ANALYSIS),
        ("3", DecisionMode.PRESSURE_TEST),
        ("pressure test", DecisionMode.PRESSURE_TEST),
        ("4", DecisionMode.BUILD_PLAN),
        ("plan", DecisionMode.BUILD_PLAN),
        ("5", DecisionMode.RISK_REVIEW),
        ("risk", DecisionMode.RISK_REVIEW),
        ("6", DecisionMode.EXECUTION_ROADMAP),
        ("roadmap", DecisionMode.EXECUTION_ROADMAP),
    ],
)
def test_parse_mode_accepts_numbers_and_keywords(text: str, mode: DecisionMode) -> None:
    assert parse_mode(text) == mode


def test_parse_mode_returns_none_for_garbage() -> None:
    assert parse_mode("") is None
    assert parse_mode("nonsense") is None
    assert parse_mode("99") is None


def test_routing_for_mode_maps_to_existing_routing_knobs() -> None:
    fast = routing_for_mode(DecisionMode.FAST_ANSWER)
    assert fast is not None
    assert fast.routing_mode == "economy"
    assert fast.debate_rounds == 0
    deep = routing_for_mode(DecisionMode.DEEP_ANALYSIS)
    assert deep is not None
    assert deep.routing_mode == "balanced"
    assert deep.debate_rounds == 2
    assert routing_for_mode(None) is None


def test_format_intake_summary_renders_all_fields() -> None:
    intake = DecisionIntake(
        goal="Ship the cache",
        context="Pre-seed, solo founder.",
        constraints=["time", "money"],
        risks=["adoption stalls"],
        success_definition="10 paying users",
        preferred_mode=DecisionMode.PRESSURE_TEST,
        notes="VP must sign off.",
    )
    text = format_intake_summary(intake)
    assert "Here's my understanding" in text
    assert "Ship the cache" in text
    assert "Pressure test" in text
    assert "- time" in text and "- money" in text
    assert "- adoption stalls" in text
    assert "10 paying users" in text
    assert "VP must sign off" in text


def test_format_intake_block_is_stable_for_chair_prompt() -> None:
    intake = DecisionIntake(
        goal="Ship the cache",
        constraints=["time"],
        preferred_mode=DecisionMode.DEEP_ANALYSIS,
    )
    block = format_intake_block(intake)
    # Stable header so chair prompts are reproducible across runs.
    assert block.startswith("Decision intake")
    assert "Goal: Ship the cache" in block
    assert "Preferred mode: Deep analysis" in block
    assert "  - time" in block


def test_compose_question_with_intake_prefixes_block() -> None:
    intake = DecisionIntake(goal="Ship", preferred_mode=DecisionMode.FAST_ANSWER)
    composed = compose_question_with_intake("What's the next step?", intake)
    assert composed.startswith("Decision intake")
    assert "Question:\nWhat's the next step?" in composed


def test_mode_picker_prompt_lists_all_six_modes() -> None:
    prompt = mode_picker_prompt()
    for mode in DecisionMode:
        assert mode_profile(mode).label in prompt


# --- guided chat flow -------------------------------------------------------


@pytest.fixture
def mock_chat_settings(tmp_path: Path) -> Settings:
    return Settings(llm_mode="mock", runs_dir=tmp_path, mock_model="mock-council-v1")


def test_natural_input_opens_intake_with_goal(
    mock_chat_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _, _ = _make_session(mock_chat_settings, monkeypatch)
    session.handle_line("I want to build an AI movie startup.")
    intake = session.state.current_intake
    assert intake is not None
    assert intake.goal == "I want to build an AI movie startup."
    # We're now waiting on the next question (preferred_mode).
    assert session.state.current_intake_field == "preferred_mode"


def test_mid_intake_answers_advance_through_questions(
    mock_chat_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop one short of the last required field so we can inspect the
    in-flight draft. Answering all 6 required fields would trigger the
    summary/confirm flow, which with a default confirm_fn discards the
    draft — making `current_intake` unobservable."""
    session, _, _ = _make_session(mock_chat_settings, monkeypatch)
    session.handle_line("Build an AI startup.")
    session.handle_line("2")  # mode = deep analysis
    session.handle_line("Pre-seed, two co-founders.")
    session.handle_line("time, money")
    session.handle_line("Hit 1000 weekly active users in 90 days.")
    intake = session.state.current_intake
    assert intake is not None
    assert intake.goal == "Build an AI startup."
    assert intake.preferred_mode == DecisionMode.DEEP_ANALYSIS
    assert intake.context == "Pre-seed, two co-founders."
    assert intake.constraints == ["time", "money"]
    assert intake.success_definition == "Hit 1000 weekly active users in 90 days."
    # The next required field is `risks`.
    assert session.state.current_intake_field == "risks"


def test_unparseable_mode_re_asks_without_advancing(
    mock_chat_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _, err = _make_session(mock_chat_settings, monkeypatch)
    session.handle_line("Plan a launch.")
    session.handle_line("xyz unknown mode")
    assert session.state.current_intake_field == "preferred_mode"
    assert "I didn't recognize that mode" in err.getvalue()


def test_completed_intake_summary_and_run_council(
    mock_chat_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, CouncilSessionRequest] = {}

    def runner(request, **_kw):
        captured["req"] = request
        return _session_result()

    # Y to intake run, N to full breakdown, N to approve, N to pack.
    confirms = iter([True, False, False, False])
    session, out, _ = _make_session(
        mock_chat_settings,
        monkeypatch,
        council_runner=runner,
        confirm_fn=lambda _m, _d: next(confirms, False),
    )
    # Walk the intake to completion (skip optional notes via empty input).
    session.handle_line("Should we ship the cache?")
    session.handle_line("fast")
    session.handle_line("Solo founder, MVP.")
    session.handle_line("time, money")
    session.handle_line("First 10 paying users")
    session.handle_line("Burnout")
    session.handle_line("")  # skip optional notes -> summary + run prompt
    assert "req" in captured
    req = captured["req"]
    assert req.intake is not None
    assert req.intake.goal == "Should we ship the cache?"
    assert req.intake.preferred_mode == DecisionMode.FAST_ANSWER
    # Mode mapping pushed routing mode to economy + 0 rounds.
    assert req.routing_mode == "economy"
    assert req.debate_rounds == 0
    # The intake summary panel was rendered before the council ran.
    text = out.getvalue()
    assert "Decision intake" in text


def test_intake_summary_decline_clears_draft(
    mock_chat_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # All confirms = No: decline run, decline edit. Draft is discarded.
    session, out, _ = _make_session(
        mock_chat_settings, monkeypatch, confirm_fn=lambda _m, _d: False
    )
    session.handle_line("Ship the cache.")
    session.handle_line("fast")
    session.handle_line("Solo founder.")
    session.handle_line("time")
    session.handle_line("Ten users.")
    session.handle_line("Burnout.")
    session.handle_line("")  # optional notes -> goes to summary
    assert session.state.current_intake is None


def test_intake_edit_branch_uses_input_fn_to_pick_field(
    mock_chat_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decline run, accept edit, type 'goal' as the field to edit."""
    # Run prompt → N, Edit prompt → Y. Subsequent prompts default False.
    confirms = iter([False, True, False, False])
    inputs = iter(["goal"])
    session, _, _ = _make_session(
        mock_chat_settings,
        monkeypatch,
        confirm_fn=lambda _m, _d: next(confirms, False),
        input_fn=lambda _p: next(inputs, ""),
    )
    session.handle_line("Ship cache.")
    session.handle_line("fast")
    session.handle_line("Solo.")
    session.handle_line("time")
    session.handle_line("Ten users.")
    session.handle_line("Burnout.")
    session.handle_line("")  # optional notes -> summary -> N run -> Y edit -> 'goal'
    # We're now sitting on the goal question again, draft retained.
    assert session.state.current_intake is not None
    assert session.state.current_intake_field == "goal"


# --- slash commands --------------------------------------------------------


def test_cmd_intake_starts_when_none_active(
    mock_chat_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, out, _ = _make_session(mock_chat_settings, monkeypatch)
    session.handle_line("/intake")
    assert session.state.current_intake is not None
    assert session.state.current_intake_field == "goal"
    assert "Let's think this through" in out.getvalue()


def test_cmd_intake_shows_summary_when_active(
    mock_chat_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, out, _ = _make_session(mock_chat_settings, monkeypatch)
    session.handle_line("Build a startup")
    session.handle_line("deep")
    out.truncate(0)
    out.seek(0)
    session.handle_line("/intake")
    text = out.getvalue()
    assert "Build a startup" in text
    assert "Deep analysis" in text


def test_cmd_clear_intake_drops_draft(
    mock_chat_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, out, _ = _make_session(mock_chat_settings, monkeypatch)
    session.handle_line("Build a startup")
    assert session.state.current_intake is not None
    session.handle_line("/clear-intake")
    assert session.state.current_intake is None
    assert "Cleared" in out.getvalue()


def test_cmd_mode_set_via_keyword(
    mock_chat_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, out, _ = _make_session(mock_chat_settings, monkeypatch)
    session.handle_line("/mode risk")
    assert session.state.current_mode == DecisionMode.RISK_REVIEW
    text = out.getvalue()
    assert "Risk review" in text


def test_cmd_mode_set_via_number(
    mock_chat_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _, _ = _make_session(mock_chat_settings, monkeypatch)
    session.handle_line("/mode 4")
    assert session.state.current_mode == DecisionMode.BUILD_PLAN


def test_cmd_mode_unknown_reports_error(
    mock_chat_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _, err = _make_session(mock_chat_settings, monkeypatch)
    session.handle_line("/mode totally-unknown")
    assert "Unknown mode" in err.getvalue()


def test_cmd_summary_without_intake_is_helpful(
    mock_chat_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, out, _ = _make_session(mock_chat_settings, monkeypatch)
    session.handle_line("/summary")
    assert "No intake yet" in out.getvalue()


def test_cmd_edit_with_explicit_field_jumps_to_that_question(
    mock_chat_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _, _ = _make_session(mock_chat_settings, monkeypatch)
    session.handle_line("Ship cache.")
    session.handle_line("deep")
    # Now mid-flow on `context`. Jump back to `goal`.
    session.handle_line("/edit goal")
    assert session.state.current_intake_field == "goal"


# --- /council bypass + intake persistence + schema --------------------------


def test_slash_council_skips_intake(
    mock_chat_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, CouncilSessionRequest] = {}

    def runner(request, **_kw):
        captured["req"] = request
        return _session_result()

    session, _, _ = _make_session(
        mock_chat_settings,
        monkeypatch,
        council_runner=runner,
        confirm_fn=lambda _m, _d: False,
    )
    session.handle_line("/council Just give me an answer.")
    # No intake walked; request.intake is None on the bypass path.
    assert "req" in captured
    assert captured["req"].intake is None
    assert session.state.current_intake is None


def test_intake_round_trip_through_pydantic() -> None:
    intake = DecisionIntake(
        goal="Ship",
        context="Solo",
        constraints=["time"],
        risks=["burnout"],
        preferred_mode=DecisionMode.DEEP_ANALYSIS,
        success_definition="10 users",
    )
    payload = intake.model_dump(mode="json")
    restored = DecisionIntake.model_validate(payload)
    assert restored == intake


def test_intake_persists_on_council_run_result(
    mock_chat_settings: Settings,
) -> None:
    """End-to-end: intake attached → result.intake populated → run.json + run.md
    record it. Runs through the real run_council_session with mock providers."""
    from council.council_session import run_council_session
    from council.runtime import RuntimeOptions

    intake = DecisionIntake(
        goal="Should we ship the cache?",
        preferred_mode=DecisionMode.FAST_ANSWER,
        constraints=["time", "money"],
        success_definition="10 paying users",
    )
    request = CouncilSessionRequest(
        question="Help me decide on the cache.",
        routing_mode="manual",
        council_presets=["mock"],
        debate_rounds=0,
        base_settings=mock_chat_settings,
        runtime=RuntimeOptions(show_progress=False),
        intake=intake,
    )
    session = run_council_session(request)
    assert session.result.intake is not None
    assert session.result.intake.goal == "Should we ship the cache?"
    assert session.result.intake.preferred_mode == DecisionMode.FAST_ANSWER
    json_path, md_path = save_run(session.result, settings=mock_chat_settings)
    raw_json = json_path.read_text(encoding="utf-8")
    raw_md = md_path.read_text(encoding="utf-8")
    assert '"intake"' in raw_json
    assert '"Should we ship the cache?"' in raw_json
    assert "## Decision Intake" in raw_md
    assert "Fast answer" in raw_md
    # Schema bump landed.
    assert RUN_SCHEMA_VERSION == "1.10"


# --- short-form result + opt-in full breakdown -----------------------------


def test_short_form_result_does_not_dump_full_dossier(
    mock_chat_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slice 6.0: post-council, the default render is the short panel.
    The full 'Do not do' / 'Approval gate' sections only appear when
    the user opts in via the Show-full-breakdown confirm."""
    session, out, _ = _make_session(
        mock_chat_settings,
        monkeypatch,
        council_runner=lambda *_a, **_k: _session_result(),
        confirm_fn=lambda _m, _d: False,  # decline full breakdown, approve, pack
    )
    session.handle_line("/council Ship cache?")
    text = out.getvalue()
    assert "Direct Answer" in text
    assert "Why:" in text
    assert "Biggest warning" in text
    assert "Next step" in text
    # Full-form sections only appear via opt-in. The opt-in prompt itself
    # IS rendered, but the full panel isn't.
    assert SHOW_FULL_BREAKDOWN_PROMPT not in text or "Do not do" not in text


def test_full_breakdown_opt_in_renders_full_panel(
    mock_chat_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = iter([True, False, False])  # Y to full breakdown, N to approve+pack
    session, out, _ = _make_session(
        mock_chat_settings,
        monkeypatch,
        council_runner=lambda *_a, **_k: _session_result(),
        confirm_fn=lambda _m, _d: next(answers, False),
    )
    session.handle_line("/council Ship cache?")
    text = out.getvalue()
    assert "Do next" in text
    assert "Do not do" in text
    assert "Approval gate" in text


# --- INTAKE_RUN_PROMPT visible to fixture helpers --------------------------


def test_intake_run_prompt_constant_is_used() -> None:
    """Documentation test: the prompt string is stable so the chat tests
    can match on it without relying on Rich formatting quirks."""
    assert "Run council" in INTAKE_RUN_PROMPT


# --- no live network -------------------------------------------------------


def test_intake_module_does_not_open_network() -> None:
    import socket

    real = socket.socket

    def blocked(*_a, **_k):  # pragma: no cover - guard
        raise AssertionError("intake module opened a socket")

    try:
        socket.socket = blocked  # type: ignore[assignment]
        for q in INTAKE_QUESTIONS:
            apply_intake_answer(empty_intake(), q.field, "test")
        compose_question_with_intake("Q?", empty_intake())
        format_intake_summary(empty_intake())
        format_intake_block(empty_intake())
    finally:
        socket.socket = real  # type: ignore[assignment]
