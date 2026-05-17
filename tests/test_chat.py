from __future__ import annotations

from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from council.chat import (
    CHAT_HELP_LINES,
    ChatLineKind,
    ChatSession,
    ChatSessionState,
    build_chat_context,
    parse_chat_line,
    render_chat_help,
    render_chat_welcome,
    run_chat_session,
)
from council.config import Settings
from council.council_session import CouncilSessionResult
from council.doctor import CheckStatus, DoctorCheck
from council.models import CouncilRunResult, DecisionDossier, DecisionType
from council.providers.models import ProviderMetadata
from council.storage import save_run


def _mock_council_result(question: str = "Test?") -> CouncilRunResult:
    return CouncilRunResult(
        dossier=DecisionDossier(
            run_id="chat-run-001",
            decision_question=question,
            decision_type=DecisionType.PROCEED_WITH_CONSTRAINTS,
            direct_answer="Proceed with constraints—start with a narrow slice first.",
            why_this_decision=["One", "Two", "Three"],
            what_would_change_mind=["A", "B", "C"],
            next_actions=["Step one", "Step two", "Step three"],
            do_not_do=["Avoid one", "Avoid two", "Avoid three"],
            approval_gate="Approve scope before pack work.",
            recommendation="Proceed with constraints.",
            confidence_score=0.7,
        ),
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


def _session_result(question: str = "Test?") -> CouncilSessionResult:
    from council.role_routing import build_council_routing

    return CouncilSessionResult(
        result=_mock_council_result(question),
        routing=build_council_routing(council_presets=["mock"]),
        role_play_warning=None,
    )


def test_parse_chat_line_slash_and_natural() -> None:
    assert parse_chat_line("").kind == ChatLineKind.EMPTY
    slash = parse_chat_line("/council Should we ship?")
    assert slash.kind == ChatLineKind.SLASH
    assert slash.command == "council"
    assert slash.args == "Should we ship?"
    natural = parse_chat_line("Should we build internal tooling?")
    assert natural.kind == ChatLineKind.NATURAL
    assert "internal tooling" in natural.args


def test_chat_help_renders() -> None:
    buffer = StringIO()
    render_chat_help(Console(file=buffer, force_terminal=True, width=120))
    text = buffer.getvalue()
    assert "/council" in text
    assert "/exit" in text
    for line in CHAT_HELP_LINES:
        if line.strip():
            assert line.split()[0] in text or line in text


def test_welcome_shows_system_profile() -> None:
    buffer = StringIO()
    render_chat_welcome(
        Console(file=buffer, force_terminal=True, width=120),
        config_profile_name="mock",
        system_profile="default",
        routing_mode="economy",
    )
    text = buffer.getvalue()
    assert "default" in text
    assert "economy" in text
    assert "mock" in text


@pytest.fixture
def chat_ctx(mock_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> object:
    monkeypatch.setattr("council.chat.load_config_file", lambda: None)
    return build_chat_context(mock_settings, system_profile="default", config_profile_name=None)


def test_exit_command_ends_session(chat_ctx) -> None:
    ctx = chat_ctx
    session = ChatSession(
        console=Console(file=StringIO(), force_terminal=True, width=120),
        error_console=Console(file=StringIO(), force_terminal=True, width=120),
        ctx=ctx,
    )
    assert session.handle_line("/exit") == "exit"


def test_doctor_command_calls_doctor(chat_ctx) -> None:
    ctx = chat_ctx
    doctor = MagicMock(
        return_value=[DoctorCheck(name="mode", status=CheckStatus.OK, message="mock")]
    )
    out = StringIO()
    session = ChatSession(
        console=Console(file=out, force_terminal=True, width=120),
        error_console=Console(file=StringIO(), force_terminal=True, width=120),
        ctx=ctx,
        doctor_runner=doctor,
    )
    assert session.handle_line("/doctor") == "continue"
    doctor.assert_called_once()
    assert "Doctor" in out.getvalue()


def test_natural_question_asks_confirmation(chat_ctx) -> None:
    ctx = chat_ctx
    confirms: list[tuple[str, bool]] = []

    def confirm(message: str, default: bool) -> bool:
        confirms.append((message, default))
        return False

    session = ChatSession(
        console=Console(file=StringIO(), force_terminal=True, width=120),
        error_console=Console(file=StringIO(), force_terminal=True, width=120),
        ctx=ctx,
        confirm_fn=confirm,
        council_runner=MagicMock(),
    )
    session.handle_line("Should we ship the MVP?")
    assert confirms == [("Run council on this?", True)]


def test_council_flow_sets_last_run_and_shows_verdict(chat_ctx) -> None:
    ctx = chat_ctx
    out = StringIO()
    session = ChatSession(
        console=Console(file=out, force_terminal=True, width=120),
        error_console=Console(file=StringIO(), force_terminal=True, width=120),
        ctx=ctx,
        confirm_fn=lambda _m, _d: False,
        council_runner=lambda *_a, **_k: _session_result(),
    )
    session.handle_line("/council Ship the MVP?")
    assert session.state.last_run_id == "chat-run-001"
    text = out.getvalue()
    assert "Direct Answer" in text
    assert "Do next" in text
    assert "Do not do" in text
    assert "Approval gate" in text


def test_show_last_works(
    mock_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("council.chat.load_config_file", lambda: None)
    settings = Settings(
        llm_mode="mock",
        runs_dir=tmp_path,
        mock_model="mock-council-v1",
    )
    result = _mock_council_result()
    save_run(result, settings=settings)
    ctx = build_chat_context(settings, system_profile="default", config_profile_name=None)
    state = ChatSessionState(last_run_id=result.dossier.run_id)
    out = StringIO()
    session = ChatSession(
        console=Console(file=out, force_terminal=True, width=120),
        error_console=Console(file=StringIO(), force_terminal=True, width=120),
        ctx=ctx,
        state=state,
    )
    session.handle_line("/show last")
    text = out.getvalue()
    assert result.dossier.run_id in text
    assert "run.md" in text


def test_pack_last_requires_approval(
    mock_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("council.chat.load_config_file", lambda: None)
    settings = Settings(
        llm_mode="mock",
        runs_dir=tmp_path,
        mock_model="mock-council-v1",
    )
    result = _mock_council_result()
    save_run(result, settings=settings)
    ctx = build_chat_context(settings, system_profile="default", config_profile_name=None)
    state = ChatSessionState(last_run_id=result.dossier.run_id)
    confirms: list[tuple[str, bool]] = []

    def confirm(message: str, default: bool) -> bool:
        confirms.append((message, default))
        return False

    session = ChatSession(
        console=Console(file=StringIO(), force_terminal=True, width=120),
        error_console=Console(file=StringIO(), force_terminal=True, width=120),
        ctx=ctx,
        state=state,
        confirm_fn=confirm,
    )
    session.handle_line("/pack last")
    assert confirms == [("Generate implementation pack for this run?", False)]
    assert not (tmp_path / result.dossier.run_id / "mvp_scope.md").exists()


def test_pack_last_writes_when_approved(
    mock_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("council.chat.load_config_file", lambda: None)
    settings = Settings(
        llm_mode="mock",
        runs_dir=tmp_path,
        mock_model="mock-council-v1",
    )
    result = _mock_council_result()
    save_run(result, settings=settings)
    ctx = build_chat_context(settings, system_profile="default", config_profile_name=None)
    state = ChatSessionState(last_run_id=result.dossier.run_id)
    session = ChatSession(
        console=Console(file=StringIO(), force_terminal=True, width=120),
        error_console=Console(file=StringIO(), force_terminal=True, width=120),
        ctx=ctx,
        state=state,
        confirm_fn=lambda _m, _d: True,
    )
    session.handle_line("/pack last")
    assert (tmp_path / result.dossier.run_id / "mvp_scope.md").is_file()


def test_run_chat_session_loop_exits(mock_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("council.chat.load_config_file", lambda: None)
    inputs = iter(["/help", "/exit"])
    code = run_chat_session(
        Console(file=StringIO(), force_terminal=True, width=120),
        Console(file=StringIO(), force_terminal=True, width=120),
        settings=mock_settings,
        system_profile="default",
        input_fn=lambda _p: next(inputs),
    )
    assert code == 0


def test_main_chat_parser_exists() -> None:
    from council.cli import parse_args

    args = parse_args(["chat"])
    assert args.command == "chat"
    assert args.system_profile == "default"
