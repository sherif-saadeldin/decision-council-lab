from __future__ import annotations

from pathlib import Path

from council.config import Settings
from council.runtime_profiles import resolve_operational_profile


def _settings(tmp_path: Path) -> Settings:
    return Settings(llm_mode="mock", runs_dir=tmp_path, mock_model="mock-council-v1")


def test_operational_profile_defaults_to_offline(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    plan = resolve_operational_profile(requested=None, settings=settings)
    assert plan.effective == "offline"
    assert plan.council_presets == ["mock"]
    assert "offline mode" in plan.startup_summary.lower()


def test_hosted_profile_degrades_safely_without_keys(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    plan = resolve_operational_profile(requested="hosted", settings=settings)
    assert plan.requested == "hosted"
    assert plan.effective == "offline"
    assert plan.fallback_summary is not None
    assert "continued using local reasoning" in plan.fallback_summary.lower()


def test_cheap_profile_uses_economy_with_hosted_compatible_key(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    settings = _settings(tmp_path)
    plan = resolve_operational_profile(requested="cheap", settings=settings)
    assert plan.effective == "cheap"
    assert plan.routing_mode == "economy"
    assert plan.council_presets is None
