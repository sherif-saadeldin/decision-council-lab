from __future__ import annotations

from pathlib import Path

import pytest

from council.costing import CouncilBudgetExceededError, count_council_llm_calls, enforce_cost_budget, estimate_council_cost
from council.council_session import CouncilSessionRequest, plan_council_session, run_council_session
from council.preset_economics import get_preset_economics
from council.role_routing import build_council_routing
from council.routing_modes import ECONOMY_SLOT_PRESETS, slot_presets_for_mode
from council.runtime import RuntimeOptions
from council.storage import save_run
from main import main


def test_economy_routing_uses_free_tier_for_non_chair(isolated_env: None) -> None:
    routing = build_council_routing(routing_mode="economy")
    assert routing.auto_routed is True
    for slot in ("researcher", "advocate", "skeptic", "risk", "operator"):
        assert routing.preset_for(slot) == "mock"
        tier = get_preset_economics(routing.preset_for(slot)).cost_tier
        assert tier in ("free", "cheap")
    assert routing.preset_for("chair") == "mock"


def test_economy_slot_map_matches_helpers() -> None:
    assert slot_presets_for_mode("economy") == ECONOMY_SLOT_PRESETS


def test_manual_routing_preserves_explicit_presets() -> None:
    routing = build_council_routing(
        routing_mode="manual",
        council_presets=["mock", "groq-llama", "nvidia-nemotron"],
    )
    assert routing.preset_for("researcher") == "mock"
    assert routing.preset_for("advocate") == "groq-llama"
    assert routing.preset_for("skeptic") == "nvidia-nemotron"


def test_count_llm_calls_includes_debate() -> None:
    assert count_council_llm_calls(0) == 5
    assert count_council_llm_calls(1) == 8
    assert count_council_llm_calls(2) == 11


def test_max_llm_calls_blocks_over_budget() -> None:
    routing = build_council_routing(routing_mode="economy")
    estimate = estimate_council_cost(routing, routing_mode="economy", debate_rounds=1)
    with pytest.raises(CouncilBudgetExceededError):
        enforce_cost_budget(estimate, max_cost_usd=None, max_llm_calls=1, allow_over_budget=False)


def test_max_cost_usd_blocks_expensive_plan() -> None:
    routing = build_council_routing(routing_mode="premium")
    estimate = estimate_council_cost(routing, routing_mode="premium", debate_rounds=2)
    with pytest.raises(CouncilBudgetExceededError):
        enforce_cost_budget(estimate, max_cost_usd=0.0001, max_llm_calls=None, allow_over_budget=False)


def test_allow_over_budget_skips_enforcement() -> None:
    routing = build_council_routing(routing_mode="premium")
    estimate = estimate_council_cost(routing, routing_mode="premium", debate_rounds=2)
    enforce_cost_budget(
        estimate,
        max_cost_usd=0.0001,
        max_llm_calls=1,
        allow_over_budget=True,
    )


def test_dry_run_cost_exits_without_session_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = {"run": False}

    def _fail_run(*_a, **_k):
        called["run"] = True
        raise AssertionError("run_council_session should not run")

    monkeypatch.setattr("main.run_council_session", _fail_run)
    code = main(
        [
            "council",
            "Dry run cost?",
            "--routing-mode",
            "economy",
            "--dry-run-cost",
            "--runs-dir",
            str(tmp_path),
            "--debate-rounds",
            "0",
        ]
    )
    assert code == 0
    assert called["run"] is False


def test_cost_estimate_in_markdown(
    mock_settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from council.providers.mock import MockProvider

    def _mock_provider_for_slot(routing, slot: str, **kwargs):
        assignment = routing.assignments[slot]
        return MockProvider(model_name=assignment.model_name, mode="mock")

    monkeypatch.setattr("council.council_session.provider_for_slot", _mock_provider_for_slot)

    request = CouncilSessionRequest(
        question="Cost in markdown?",
        routing_mode="economy",
        debate_rounds=0,
        base_settings=mock_settings,
        runtime=RuntimeOptions(show_progress=False),
    )
    plan = plan_council_session(request)
    session = run_council_session(request, plan=plan)
    assert session.result.cost_estimate is not None
    _, md_path = save_run(session.result, settings=mock_settings)
    text = md_path.read_text(encoding="utf-8")
    assert "## Cost Estimate" in text
    assert "economy" in text


def test_main_max_cost_blocks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    code = main(
        [
            "council",
            "Too expensive?",
            "--routing-mode",
            "premium",
            "--debate-rounds",
            "2",
            "--max-cost-usd",
            "0.0001",
            "--runs-dir",
            str(tmp_path),
        ]
    )
    assert code == 1
