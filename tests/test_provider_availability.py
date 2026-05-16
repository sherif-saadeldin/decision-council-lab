from __future__ import annotations

import pytest

from council.config import Settings
from council.doctor import CheckStatus, run_doctor
from council.live_completion import validate_ping_json
from council.provider_availability import (
    HostedProviderUnavailableError,
    apply_auto_routing_guards,
    build_preset_availability_for_routing,
    validate_hosted_presets_live,
)
from council.role_routing import build_council_routing
from council.routing_modes import ECONOMY_SLOT_PRESETS, HOSTED_CHAIR_FALLBACK_WARNING
from council.runtime import RuntimeOptions
from council.smoke import SmokeRequest, run_smoke
from main import main


def test_validate_ping_json_accepts_ok() -> None:
    ok, reason = validate_ping_json('{"ok": true}')
    assert ok is True
    assert reason is None


def test_doctor_live_completion_success_mock() -> None:
    settings = Settings(llm_mode="mock", runs_dir=Settings.from_env().runs_dir, mock_model="mock-council-v1")
    checks = run_doctor(settings, live_completion=True)
    live = next(c for c in checks if c.name == "live_completion")
    assert live.status == CheckStatus.OK
    assert "ok" in live.message.lower()
    assert "sk-" not in live.message


def test_doctor_live_completion_auth_failure_via_injected_ping() -> None:
    settings = Settings(
        llm_mode="openai_compatible",
        runs_dir=Settings.from_env().runs_dir,
        mock_model="mock-council-v1",
        llm_provider_name="openrouter",
        llm_base_url="https://openrouter.ai/api/v1",
        llm_model="anthropic/claude-sonnet-4.5",
        llm_api_key="sk-test-secret-key-not-real",
    )

    def fail_ping(_provider) -> tuple[bool, str | None]:
        return False, "openrouter authentication failed. Check LLM_API_KEY."

    checks = run_doctor(settings, live_completion=True, live_completion_fn=fail_ping)
    live = next(c for c in checks if c.name == "live_completion")
    assert live.status == CheckStatus.FAIL
    assert "authentication" in live.message.lower()
    assert "sk-test" not in live.message


def test_economy_falls_back_to_mock_chair_without_api_key(isolated_env: None) -> None:
    routing = build_council_routing(routing_mode="economy")
    assert routing.preset_for("chair") == "mock"
    assert HOSTED_CHAIR_FALLBACK_WARNING in routing.routing_warnings
    for slot in ("researcher", "advocate", "skeptic", "risk", "operator"):
        assert routing.preset_for(slot) == "mock"


def test_apply_auto_routing_guard_only_changes_chair() -> None:
    presets = dict(ECONOMY_SLOT_PRESETS)
    settings = Settings(llm_mode="mock", runs_dir=Settings.from_env().runs_dir, mock_model="mock-council-v1")
    updated, warnings = apply_auto_routing_guards(presets, settings)
    assert updated["chair"] == "mock"
    assert warnings == [HOSTED_CHAIR_FALLBACK_WARNING]


def test_require_live_providers_fails_without_network(isolated_env: None) -> None:
    settings = Settings.from_env()

    def fail_ping(_provider) -> tuple[bool, str | None]:
        return False, "authentication failed"

    with pytest.raises(HostedProviderUnavailableError) as exc_info:
        validate_hosted_presets_live(
            ["openrouter-sonnet"],
            settings,
            RuntimeOptions(timeout_seconds=5.0),
            live_ping_fn=fail_ping,
        )
    assert "openrouter-sonnet" in str(exc_info.value)
    assert "sk-" not in str(exc_info.value)


def test_dry_run_cost_shows_availability(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    code = main(
        [
            "council",
            "Dry run availability?",
            "--dry-run-cost",
            "--routing-mode",
            "economy",
            "--runs-dir",
            str(tmp_path),
        ]
    )
    assert code == 0


def test_smoke_auth_failure_reports_credential_source() -> None:
    def fail_council(*_a, **_k):
        from council.providers.errors import ProviderResponseError

        raise ProviderResponseError(
            "openrouter",
            "openrouter authentication failed. Check LLM_API_KEY.",
            source="api",
            failure_kind="auth_failure",
        )

    report = run_smoke(
        SmokeRequest(preset="openrouter-sonnet", skip_preflight=True),
        run_council_fn=fail_council,
    )
    assert report.success is False
    assert report.auth_failure is True
    assert report.failure_reason == "auth_failure"
    assert report.credential_source == "missing"
    assert "sk-" not in (report.error or "")


def test_build_preset_availability_for_routing() -> None:
    routing = build_council_routing(routing_mode="economy")
    rows = build_preset_availability_for_routing(routing, Settings.from_env())
    assert len(rows) == 6
    mock_row = next(r for r in rows if r.preset == "mock")
    assert mock_row.credential_source == "not_required"
    assert mock_row.availability == "available"
