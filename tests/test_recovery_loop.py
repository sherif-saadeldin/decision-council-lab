"""Slice 5.7 — Profile-aware doctor and recovery loop.

Covers:
- `/doctor` uses the active profile and prints the profile header
- `/doctor --live` / `--live-completion` flag parsing and UX (latency, timeout)
- `/status` shows cached health and last-doctor timestamp
- doctor cache updates after each `/doctor` invocation
- hosted auth-failure classification → recovery commands
- hosted timeout classification → recovery commands
- fallback-to-mock prompt (offered, accepted, declined)
- chat session survives repeated provider failures
- recovery classification module is offline (no network)
"""

from __future__ import annotations

from dataclasses import replace as dc_replace
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from council.chat import (
    FALLBACK_PROFILE_NAME,
    FALLBACK_PROMPT,
    HEALTH_FAILED,
    HEALTH_HEALTHY,
    HEALTH_UNCHECKED,
    HEALTH_WARNING,
    ChatSession,
    ChatSessionState,
    DoctorCacheEntry,
    build_chat_context,
    parse_doctor_args,
    summarize_doctor_checks,
)
from council.config import Settings
from council.config_profiles import (
    ConfigProfile,
    init_config_file,
    load_config_file,
)
from council.doctor import CheckStatus, DoctorCheck
from council.provider_availability import HostedProviderUnavailableError
from council.providers.errors import (
    MissingProviderCredentialError,
    ProviderResponseError,
)
from council.recovery import (
    RECOVERY_AUTH,
    RECOVERY_DEFAULT,
    RECOVERY_NETWORK,
    RECOVERY_RATE_LIMIT,
    classify_for_recovery,
    is_recoverable_with_mock,
)


def _make_session(
    ctx,
    *,
    state: ChatSessionState | None = None,
    doctor_runner=None,
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
        doctor_runner=doctor_runner,
        council_runner=council_runner,
        confirm_fn=confirm_fn or (lambda _m, _d: False),
    )
    return session, out, err


# --- classification ---------------------------------------------------------


def test_classify_missing_credential_is_auth_failure_with_env_var() -> None:
    exc = MissingProviderCredentialError("openrouter", "LLM_API_KEY")
    failure = classify_for_recovery(exc)
    assert failure.category == "auth_failure"
    assert "LLM_API_KEY" in failure.summary
    assert failure.recovery_commands == RECOVERY_AUTH
    assert is_recoverable_with_mock(failure)


def test_classify_provider_response_auth_failure() -> None:
    exc = ProviderResponseError(
        "openrouter",
        "401 Unauthorized — invalid api key",
        source="api",
        failure_kind="auth_failure",
    )
    failure = classify_for_recovery(exc)
    assert failure.category == "auth_failure"
    assert failure.recovery_commands == RECOVERY_AUTH


def test_classify_provider_response_timeout() -> None:
    exc = ProviderResponseError(
        "groq",
        "Request timed out after 30s",
        source="api",
        failure_kind="timeout",
    )
    failure = classify_for_recovery(exc)
    assert failure.category == "timeout"
    assert failure.recovery_commands == RECOVERY_DEFAULT


def test_classify_provider_response_parse_failure() -> None:
    exc = ProviderResponseError(
        "openrouter",
        "JSON malformed",
        source="response",
    )
    failure = classify_for_recovery(exc)
    assert failure.category == "parse_failure"
    assert "/profile mock" in " ".join(failure.recovery_commands)


def test_classify_hosted_unavailable_with_auth_keyword_promotes_to_auth() -> None:
    exc = HostedProviderUnavailableError("openrouter live ping failed: 401 Unauthorized")
    failure = classify_for_recovery(exc)
    assert failure.category == "auth_failure"


def test_classify_rate_limit_from_message() -> None:
    exc = ProviderResponseError(
        "groq", "429 Too Many Requests — rate limit hit", source="api", failure_kind="api_failure"
    )
    failure = classify_for_recovery(exc)
    # rate limit shows under the api_failure umbrella with specialized recovery.
    assert failure.recovery_commands == RECOVERY_RATE_LIMIT


def test_classify_network_failure_from_message() -> None:
    exc = ProviderResponseError(
        "openrouter",
        "Connection refused at api.openrouter.ai",
        source="api",
        failure_kind="network_failure",
    )
    failure = classify_for_recovery(exc)
    assert failure.category == "network_failure"
    assert failure.recovery_commands == RECOVERY_NETWORK


def test_classify_unknown_does_not_misleadingly_suggest_specific_fix() -> None:
    failure = classify_for_recovery(RuntimeError("something weird"))
    assert failure.category == "unknown"
    # Unknown still offers a safe escape hatch.
    assert "/profile mock" in " ".join(failure.recovery_commands)


# --- doctor cache + summarization ------------------------------------------


def test_summarize_doctor_checks_healthy() -> None:
    checks = [
        DoctorCheck(name="mode", status=CheckStatus.OK, message="ok"),
        DoctorCheck(name="creds", status=CheckStatus.OK, message="ok"),
    ]
    health, ok, warn, fail = summarize_doctor_checks(checks)
    assert health == HEALTH_HEALTHY
    assert (ok, warn, fail) == (2, 0, 0)


def test_summarize_doctor_checks_warning_promotes_over_ok() -> None:
    checks = [
        DoctorCheck(name="mode", status=CheckStatus.OK, message="ok"),
        DoctorCheck(name="base_url", status=CheckStatus.WARN, message="slow"),
    ]
    health, _, warn, fail = summarize_doctor_checks(checks)
    assert health == HEALTH_WARNING
    assert warn == 1
    assert fail == 0


def test_summarize_doctor_checks_fail_dominates() -> None:
    checks = [
        DoctorCheck(name="mode", status=CheckStatus.OK, message="ok"),
        DoctorCheck(name="creds", status=CheckStatus.WARN, message="warn"),
        DoctorCheck(name="LLM_API_KEY", status=CheckStatus.FAIL, message="missing"),
    ]
    health, _, _, fail = summarize_doctor_checks(checks)
    assert health == HEALTH_FAILED
    assert fail == 1


def test_parse_doctor_args_supports_flags_and_aliases() -> None:
    assert parse_doctor_args("") == (False, False)
    assert parse_doctor_args("--live") == (True, False)
    assert parse_doctor_args("--live-completion") == (True, True)
    # --live-completion implies live; order/extra whitespace doesn't matter.
    assert parse_doctor_args("  --live --live-completion  ") == (True, True)


# --- /doctor profile-aware behaviour ---------------------------------------


@pytest.fixture
def chat_ctx_mock(mock_settings: Settings, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("council.chat.load_config_file", lambda path=None: None)
    return build_chat_context(mock_settings, config_profile_name="mock")


@pytest.fixture
def chat_ctx_with_config(
    mock_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    config_path = tmp_path / "config.toml"
    init_config_file(config_path)
    monkeypatch.setattr(
        "council.chat.load_config_file",
        lambda path=None: load_config_file(config_path),
    )
    return (
        build_chat_context(mock_settings, config_profile_name="mock"),
        config_path,
    )


def test_doctor_renders_profile_header_with_active_profile(chat_ctx_mock) -> None:
    captured_settings: list[Settings] = []

    def runner(settings, **_kw):
        captured_settings.append(settings)
        return [DoctorCheck(name="mode", status=CheckStatus.OK, message="mock mode")]

    state = ChatSessionState(config_profile_name="mock")
    session, out, _ = _make_session(chat_ctx_mock, state=state, doctor_runner=runner)
    session.handle_line("/doctor")
    text = out.getvalue()
    assert "Doctor — active profile" in text
    assert "Profile" in text
    assert "Provider/mode" in text
    assert "API mode" in text
    assert "Availability" in text
    # Runner received the ctx settings derived from the active profile.
    assert captured_settings and captured_settings[0].llm_mode == "mock"


def test_doctor_updates_cache_and_status_reads_it(chat_ctx_mock) -> None:
    def runner(_settings, **_kw):
        return [
            DoctorCheck(name="mode", status=CheckStatus.OK, message="ok"),
            DoctorCheck(name="creds", status=CheckStatus.OK, message="ok"),
        ]

    state = ChatSessionState(config_profile_name="mock")
    session, _, _ = _make_session(chat_ctx_mock, state=state, doctor_runner=runner)
    assert session.state.last_doctor is None
    session.handle_line("/doctor")
    cache = session.state.last_doctor
    assert isinstance(cache, DoctorCacheEntry)
    assert cache.health == HEALTH_HEALTHY
    assert cache.profile_name == "mock"
    # /status reads the cache rather than re-running checks.
    out2 = StringIO()
    session.console = Console(file=out2, force_terminal=True, width=120)
    session.handle_line("/status")
    text = out2.getvalue()
    assert "Health" in text
    assert "healthy" in text
    assert "Last doctor" in text


def test_status_reports_unchecked_before_doctor_runs(chat_ctx_mock) -> None:
    state = ChatSessionState(config_profile_name="mock")
    session, out, _ = _make_session(chat_ctx_mock, state=state)
    session.handle_line("/status")
    text = out.getvalue()
    assert HEALTH_UNCHECKED in text
    assert "run `/doctor`" in text


def test_doctor_live_flag_shows_latency_and_timeout(chat_ctx_mock) -> None:
    def runner(_settings, *, runtime=None, live=False, live_completion=False, **_kw):
        # Simulate a passing live check.
        return [
            DoctorCheck(name="mode", status=CheckStatus.OK, message="ok"),
            DoctorCheck(name="live", status=CheckStatus.OK, message="ok"),
        ]

    state = ChatSessionState(config_profile_name="mock")
    session, out, _ = _make_session(chat_ctx_mock, state=state, doctor_runner=runner)
    session.handle_line("/doctor --live")
    text = out.getvalue()
    assert "Live validation finished in" in text
    assert "timeout" in text
    cache = session.state.last_doctor
    assert cache is not None
    assert cache.live is True
    assert cache.latency_seconds is not None and cache.latency_seconds >= 0


def test_doctor_failed_on_hosted_profile_prints_recovery_block(
    chat_ctx_mock,
) -> None:
    hosted_profile = ConfigProfile(name="hosted-fake", preset="openrouter-sonnet")
    ctx = dc_replace(chat_ctx_mock, config_profile=hosted_profile)

    def runner(_settings, **_kw):
        return [
            DoctorCheck(name="LLM_API_KEY", status=CheckStatus.FAIL, message="missing"),
        ]

    state = ChatSessionState(config_profile_name="hosted-fake")
    session, _, err = _make_session(ctx, state=state, doctor_runner=runner)
    session.handle_line("/doctor")
    text = err.getvalue()
    assert "Recommended recovery" in text
    # The three recovery commands appear (Rich may decorate '/', so match by name).
    assert "profile mock" in text
    assert "doctor" in text
    assert "setup" in text


# --- recovery loop ----------------------------------------------------------


def test_recovery_offers_fallback_for_hosted_failure(
    chat_ctx_with_config,
) -> None:
    ctx, _config_path = chat_ctx_with_config
    hosted_profile = ConfigProfile(name="hosted-fake", preset="openrouter-sonnet")
    ctx = dc_replace(ctx, config_profile=hosted_profile)

    confirm_log: list[tuple[str, bool]] = []

    def confirm(message: str, default: bool) -> bool:
        confirm_log.append((message, default))
        return False  # decline fallback

    state = ChatSessionState(config_profile_name="hosted-fake")

    def boom(*_a, **_k):
        raise HostedProviderUnavailableError("openrouter ping failed: 401")

    session, _, err = _make_session(
        ctx, state=state, council_runner=boom, confirm_fn=confirm
    )
    session.handle_line("/council Should we ship?")
    text = err.getvalue()
    assert "Reason" in text
    assert "Fix" in text
    assert FALLBACK_PROMPT in [m for m, _ in confirm_log]
    # Decline preserved profile.
    assert session.state.config_profile_name == "hosted-fake"


def test_recovery_accepts_fallback_switches_profile_and_retries(
    chat_ctx_with_config,
) -> None:
    ctx, config_path = chat_ctx_with_config
    hosted_profile = ConfigProfile(name="hosted-fake", preset="openrouter-sonnet")
    ctx = dc_replace(ctx, config_profile=hosted_profile)

    call_count = {"n": 0}

    def runner(*_a, **_k):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise HostedProviderUnavailableError("openrouter ping failed: 401")
        # Second invocation (post-fallback) returns a real session result.
        from council.chat import CouncilSessionResult
        from council.models import CouncilRunResult, DecisionDossier, DecisionType
        from council.providers.models import ProviderMetadata
        from council.role_routing import build_council_routing

        result = CouncilRunResult(
            dossier=DecisionDossier(
                decision_question="Should we ship?",
                decision_type=DecisionType.PROCEED,
                direct_answer="Yes — proceed with constraints.",
                confidence_score=0.7,
                why_this_decision=["a", "b", "c"],
                what_would_change_mind=["x", "y", "z"],
                next_actions=["go"],
                do_not_do=["stop"],
                approval_gate="approve",
                recommendation="Yes",
            ),
            provider_metadata=ProviderMetadata(
                mode="mock", provider_name="mock", model_name="mock-council-v1"
            ),
            agent_briefs=[],
        )
        return CouncilSessionResult(
            result=result,
            routing=build_council_routing(council_presets=["mock"]),
            role_play_warning=None,
        )

    state = ChatSessionState(config_profile_name="hosted-fake")
    session, out, _ = _make_session(
        ctx,
        state=state,
        council_runner=runner,
        confirm_fn=lambda _m, _d: True,  # Yes to council confirm + Yes to fallback
    )
    # Slice 6.0: natural input routes through guided intake first. Use
    # /council to skip intake and exercise the direct council path that
    # this test is about (provider failure → fallback → retry).
    session.handle_line("/council Should we ship?")
    # Two attempts: original (failed) + retry under mock.
    assert call_count["n"] == 2
    assert session.state.config_profile_name == FALLBACK_PROFILE_NAME
    # Active profile on disk is now mock.
    on_disk = load_config_file(config_path)
    assert on_disk is not None
    assert on_disk.active_profile == FALLBACK_PROFILE_NAME
    # Cache is invalidated on profile switch.
    assert session.state.last_doctor is None
    text = out.getvalue()
    assert "Switched to" in text


def test_chat_survives_repeated_provider_failures(chat_ctx_with_config) -> None:
    ctx, _ = chat_ctx_with_config
    hosted_profile = ConfigProfile(name="hosted-fake", preset="openrouter-sonnet")
    ctx = dc_replace(ctx, config_profile=hosted_profile)

    raises = {"n": 0}

    def boom(*_a, **_k):
        raises["n"] += 1
        raise ProviderResponseError(
            "openrouter",
            "503 Service Unavailable",
            source="api",
            failure_kind="api_failure",
        )

    state = ChatSessionState(config_profile_name="hosted-fake")
    session, _, _ = _make_session(
        ctx,
        state=state,
        council_runner=boom,
        confirm_fn=lambda _m, _d: False,  # never fallback
    )
    for _ in range(5):
        assert session.handle_line("/council Should we ship?") == "continue"
    assert raises["n"] == 5
    # Last failure cached.
    assert session.state.last_failure is not None
    assert session.state.last_failure.category in {"api_failure", "unknown"}


def test_fallback_not_offered_when_already_on_mock(chat_ctx_with_config) -> None:
    ctx, _ = chat_ctx_with_config

    confirm_log: list[str] = []

    def confirm(message: str, default: bool) -> bool:
        confirm_log.append(message)
        return True

    def boom(*_a, **_k):
        raise ProviderResponseError(
            "mock", "synthetic failure", source="api", failure_kind="api_failure"
        )

    state = ChatSessionState(config_profile_name="mock")
    session, _, _ = _make_session(
        ctx, state=state, council_runner=boom, confirm_fn=confirm
    )
    session.handle_line("/council Q?")
    # Fallback prompt is NOT offered when the user is already on mock.
    assert FALLBACK_PROMPT not in confirm_log


# --- isolation guard --------------------------------------------------------


def test_classification_module_does_not_touch_network() -> None:
    """Spot-check: classification is pure and never opens a socket."""
    import socket

    original = socket.socket

    def blocked(*_a, **_k):  # pragma: no cover - guard
        raise AssertionError("classification opened a socket")

    try:
        socket.socket = blocked  # type: ignore[assignment]
        classify_for_recovery(MissingProviderCredentialError("p", "LLM_API_KEY"))
        classify_for_recovery(
            ProviderResponseError("p", "401", source="api", failure_kind="auth_failure")
        )
        classify_for_recovery(RuntimeError("anything"))
    finally:
        socket.socket = original  # type: ignore[assignment]
