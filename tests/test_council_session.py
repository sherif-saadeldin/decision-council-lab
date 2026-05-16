from __future__ import annotations

from pathlib import Path

import pytest

from council.cli import build_council_request, parse_args
from council.council_session import CouncilSessionRequest, run_council_session
from council.implementation_pack import write_implementation_pack
from council.role_routing import ROLE_PLAY_WARNING, build_council_routing
from council.runtime import RuntimeOptions
from council.storage import save_run
from main import main


def test_council_parser_exists() -> None:
    args = parse_args(
        [
            "council",
            "Should we ship?",
            "--council-presets",
            "mock,mock,mock,mock,mock,mock",
        ]
    )
    assert args.command == "council"
    assert args.question == "Should we ship?"


def test_build_council_routing_maps_presets_to_roles() -> None:
    routing = build_council_routing(
        routing_mode="manual",
        council_presets=["mock", "openrouter-free-qwen", "groq-llama", "nvidia-nemotron"],
    )
    assert routing.preset_for("researcher") == "mock"
    assert routing.preset_for("advocate") == "openrouter-free-qwen"
    assert routing.preset_for("skeptic") == "groq-llama"
    assert routing.preset_for("risk") == "nvidia-nemotron"
    assert routing.unique_preset_count == 4
    assert routing.role_play_only is False


def test_single_preset_warns_role_play_only() -> None:
    routing = build_council_routing(council_presets=["mock"])
    assert routing.role_play_only is True
    assert routing.role_play_warning == ROLE_PLAY_WARNING


def test_multi_model_session_records_per_role_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from council.providers.mock import MockProvider

    def _mock_provider_for_slot(routing, slot: str, **kwargs):
        assignment = routing.assignments[slot]
        return MockProvider(model_name=assignment.model_name, mode="mock")

    monkeypatch.setattr("council.council_session.provider_for_slot", _mock_provider_for_slot)
    monkeypatch.setattr("council.multi_debate.provider_for_slot", _mock_provider_for_slot)

    from council.config import Settings

    request = CouncilSessionRequest(
        question="Should we build an internal council tool first?",
        routing_mode="manual",
        council_presets=["mock", "openrouter-free-qwen", "groq-llama", "nvidia-nemotron"],
        debate_rounds=1,
        base_settings=Settings(
            llm_mode="mock",
            runs_dir=tmp_path,
            mock_model="mock-council-v1",
        ),
        runtime=RuntimeOptions(show_progress=False),
    )
    session = run_council_session(request)
    result = session.result
    assert result.council_mode == "multi"
    assert len(result.role_assignments) == 6
    presets = {item.preset for item in result.role_assignments}
    assert "mock" in presets
    assert "openrouter-free-qwen" in presets
    assert result.agent_briefs
    assert result.debate_transcript is not None
    assert result.debate_transcript.rounds[0].risk_officer is not None
    models = {item.model_name for item in result.role_assignments}
    assert len(models) >= 2


def test_implementation_pack_created_only_when_requested(tmp_path: Path) -> None:
    from council.config import Settings

    request = CouncilSessionRequest(
        question="Pack test?",
        routing_mode="manual",
        council_presets=["mock"],
        debate_rounds=0,
        base_settings=Settings(
            llm_mode="mock",
            runs_dir=tmp_path,
            mock_model="mock-council-v1",
        ),
        runtime=RuntimeOptions(show_progress=False),
    )
    session = run_council_session(request)
    json_path, _ = save_run(session.result, settings=request.base_settings)
    run_dir = json_path.parent
    assert not (run_dir / "implementation_plan.md").exists()

    paths = write_implementation_pack(run_dir, session.result)
    assert len(paths) == 6
    assert (run_dir / "implementation_plan.md").exists()
    assert (run_dir / "task_breakdown.md").exists()
    assert (run_dir / "cursor_build_prompt.md").exists()
    assert (run_dir / "mvp_scope.md").exists()
    assert (run_dir / "approval_checklist.md").exists()
    assert (run_dir / "risk_register.md").exists()
    text = (run_dir / "implementation_plan.md").read_text(encoding="utf-8")
    assert "sk-" not in text


def test_main_council_multi_mock(capsys, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    code = main(
        [
            "council",
            "Council CLI test?",
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
    assert code == 0
    runs = list(tmp_path.iterdir())
    assert runs
    run_dir = runs[0]
    assert (run_dir / "run.json").exists()
    assert (run_dir / "implementation_plan.md").exists()


def test_build_council_request_role_presets() -> None:
    args = parse_args(
        [
            "council",
            "Q?",
            "--researcher-preset",
            "mock",
            "--chair-preset",
            "openrouter-sonnet",
        ]
    )
    request = build_council_request(args)
    assert request.researcher_preset == "mock"
    assert request.chair_preset == "openrouter-sonnet"
