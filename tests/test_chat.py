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
    looks_like_shell_command,
    parse_chat_line,
    render_chat_help,
    render_chat_welcome,
    run_chat_session,
)
from council.config_profiles import (
    ConfigProfile,
    init_config_file,
    load_config_file,
    set_active_profile,
)
from council.provider_availability import HostedProviderUnavailableError
from council.providers.errors import MissingProviderCredentialError
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


def test_pack_last_blocked_until_approved(
    mock_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slice 5.9: a draft run cannot generate a pack — the chat-level
    confirm should never even be reached."""
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
        return True

    err = StringIO()
    session = ChatSession(
        console=Console(file=StringIO(), force_terminal=True, width=120),
        error_console=Console(file=err, force_terminal=True, width=120),
        ctx=ctx,
        state=state,
        confirm_fn=confirm,
    )
    session.handle_line("/pack last")
    assert confirms == []  # blocked before reaching the chat confirm
    assert not (tmp_path / result.dossier.run_id / "mvp_scope.md").exists()
    assert "Decision is not approved yet" in err.getvalue()


def test_pack_last_writes_after_approval(
    mock_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After `/approve last`, `/pack last` may proceed when the chat user
    confirms."""
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
    session.handle_line("/approve last shipping for review")
    session.handle_line("/pack last")
    assert (tmp_path / result.dossier.run_id / "mvp_scope.md").is_file()


def test_pack_last_override_bypasses_lifecycle_gate(
    mock_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`/pack last --allow-unapproved` writes the pack even on a draft."""
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
    session.handle_line("/pack last --allow-unapproved")
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


# --- /profile, /status, shell guard, error recovery -------------------------


def _make_session(
    ctx,
    *,
    state: ChatSessionState | None = None,
    council_runner=None,
    confirm_fn=None,
) -> tuple[ChatSession, StringIO, StringIO]:
    out = StringIO()
    err = StringIO()
    session = ChatSession(
        console=Console(file=out, force_terminal=True, width=120),
        error_console=Console(file=err, force_terminal=True, width=120),
        ctx=ctx,
        state=state or ChatSessionState(),
        council_runner=council_runner,
        confirm_fn=confirm_fn or (lambda _m, _d: False),
    )
    return session, out, err


def test_looks_like_shell_command_detects_common_paste_forms() -> None:
    assert looks_like_shell_command("uv run python main.py chat")
    assert looks_like_shell_command("git status")
    assert looks_like_shell_command("python -V")
    assert looks_like_shell_command("./script.sh")
    assert not looks_like_shell_command("Should we ship the MVP?")
    assert not looks_like_shell_command("how do I run uv?")  # not a paste


def test_natural_shell_paste_is_rejected_not_run(chat_ctx) -> None:
    ctx = chat_ctx
    confirm_calls: list[str] = []

    def confirm(message: str, default: bool) -> bool:
        confirm_calls.append(message)
        return True

    runner = MagicMock()
    session, _, err = _make_session(ctx, council_runner=runner, confirm_fn=confirm)
    session.handle_line("uv run python main.py chat")
    assert runner.call_count == 0
    assert confirm_calls == []  # confirm not even reached
    # Rich injects ANSI escapes around tokens like "/help", so check on a
    # phrase Rich will leave untouched.
    assert "This is chat mode, not a shell" in err.getvalue()


def test_status_command_shows_profile_and_routing(chat_ctx) -> None:
    ctx = chat_ctx
    state = ChatSessionState(last_run_id="run-xyz-001")
    session, out, _ = _make_session(ctx, state=state)
    assert session.handle_line("/status") == "continue"
    text = out.getvalue()
    assert "Chat Status" in text
    assert "economy" in text
    assert "default" in text
    assert "run-xyz-001" in text


def test_profile_without_args_shows_active(
    mock_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.toml"
    init_config_file(config_path)
    monkeypatch.setattr(
        "council.chat.load_config_file",
        lambda path=None: load_config_file(config_path),
    )
    ctx = build_chat_context(mock_settings, config_profile_name="mock")
    session, out, _ = _make_session(ctx)
    session.handle_line("/profile")
    assert "mock" in out.getvalue()


def test_profile_list_shows_all_profiles(
    mock_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.toml"
    init_config_file(config_path)
    monkeypatch.setattr(
        "council.chat.load_config_file",
        lambda path=None: load_config_file(config_path),
    )
    ctx = build_chat_context(mock_settings, config_profile_name="mock")
    session, out, _ = _make_session(ctx)
    session.handle_line("/profile list")
    text = out.getvalue()
    assert "mock" in text
    assert "ollama-local" in text
    assert "openai-mini" in text


def test_profile_mock_alias_switches_active(
    mock_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.toml"
    init_config_file(config_path)
    # Start with a different active profile, then chat /profile mock to switch.
    set_active_profile("ollama-local", config_path)
    monkeypatch.setattr(
        "council.chat.load_config_file",
        lambda path=None: load_config_file(config_path),
    )
    ctx = build_chat_context(mock_settings, config_profile_name="ollama-local")
    session, out, _ = _make_session(ctx)
    session.handle_line("/profile mock")
    text = out.getvalue()
    assert "Switched active profile to" in text
    assert "mock" in text
    assert session.state.config_profile_name == "mock"
    on_disk = load_config_file(config_path)
    assert on_disk is not None
    assert on_disk.active_profile == "mock"


def test_profile_use_unknown_does_not_exit(
    mock_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.toml"
    init_config_file(config_path)
    monkeypatch.setattr(
        "council.chat.load_config_file",
        lambda path=None: load_config_file(config_path),
    )
    ctx = build_chat_context(mock_settings, config_profile_name="mock")
    session, _, err = _make_session(ctx)
    assert session.handle_line("/profile use not-a-profile") == "continue"
    assert "not-a-profile" in err.getvalue()


def test_chat_recovers_from_hosted_provider_failure(chat_ctx, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = chat_ctx
    # Force the chat context to look hosted so the hint fires.
    hosted_profile = ConfigProfile(name="hosted-fake", preset="openrouter-sonnet")
    from dataclasses import replace as dc_replace

    new_ctx = dc_replace(ctx, config_profile=hosted_profile)

    def boom(*_a, **_k):
        raise HostedProviderUnavailableError("openrouter live ping failed: 401")

    confirm_calls: list[tuple[str, bool]] = []

    def confirm(message: str, default: bool) -> bool:
        confirm_calls.append((message, default))
        # Decline the council-run confirm AND the fallback prompt — the
        # classification UX should still print without any fallback action.
        return "Run council on this?" in message  # only the first confirm: Yes

    session, _, err = _make_session(new_ctx, council_runner=boom, confirm_fn=confirm)
    assert session.handle_line("Should we ship?") == "continue"
    err_text = err.getvalue()
    assert "openrouter" in err_text
    # New recovery UX prints a Reason line and a Fix line; Rich won't
    # decorate the leading 'Reason' phrase.
    assert "Reason" in err_text
    assert "Fix" in err_text


def test_chat_known_error_via_slash_keeps_session_alive(chat_ctx) -> None:
    ctx = chat_ctx
    hosted_profile = ConfigProfile(name="hosted-fake", mode="openai_compatible", preset="openrouter-sonnet")
    from dataclasses import replace as dc_replace

    new_ctx = dc_replace(ctx, config_profile=hosted_profile)

    def boom(*_a, **_k):
        raise MissingProviderCredentialError("openrouter", "LLM_API_KEY")

    # Decline the fallback prompt — the test is verifying that the session
    # stays alive and prints the recovery hint, not the fallback path.
    session, _, err = _make_session(new_ctx, council_runner=boom, confirm_fn=lambda _m, _d: False)
    assert session.handle_line("/council Should we ship?") == "continue"
    err_text = err.getvalue()
    assert "LLM_API_KEY" in err_text
    assert "auth_failure" in err_text


def test_bare_slash_does_not_crash() -> None:
    """`/`, `/ `, `/   ` must parse as empty, not raise IndexError."""
    for line in ("/", "/ ", "/   "):
        parsed = parse_chat_line(line)
        assert parsed.kind == ChatLineKind.EMPTY, line


def test_handle_line_accepts_bare_slash(chat_ctx) -> None:
    ctx = chat_ctx
    session, _, _ = _make_session(ctx)
    assert session.handle_line("/") == "continue"
    assert session.handle_line("/ ") == "continue"


def test_chat_corrupt_config_surfaces_friendly_message(
    mock_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed config.toml must not crash chat with a raw TOML error."""
    from io import StringIO as _S

    from council.cli import render_known_error
    from council.config_profiles import CorruptConfigFileError

    bad = tmp_path / "config.toml"
    bad.write_text("this is = not = toml\n[broken\n", encoding="utf-8")
    monkeypatch.setattr(
        "council.chat.load_config_file",
        lambda path=None: load_config_file(bad),
    )
    # build_chat_context must propagate a ConfigProfileError (caught upstream).
    with pytest.raises(CorruptConfigFileError) as excinfo:
        build_chat_context(mock_settings)
    err = _S()
    render_known_error(Console(file=err, force_terminal=True, width=120), excinfo.value, quiet=False)
    text = err.getvalue()
    assert "config init" in text  # recovery hint reaches the user
