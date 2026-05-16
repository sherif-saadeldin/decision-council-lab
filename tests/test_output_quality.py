from __future__ import annotations

from pathlib import Path

from council.config import Settings
from council.engine import run_council
from council.models import RUN_SCHEMA_VERSION, DecisionType
from tests.conftest import assert_mock_run_schema
from council.storage import save_run
from main import main


def test_chair_output_contains_decision_type(mock_settings: Settings) -> None:
    result, _ = run_council("Should we build an internal tool first?", settings=mock_settings)
    assert result.dossier.decision_type in DecisionType
    assert_mock_run_schema(result)


def test_markdown_includes_chair_judgment_fields(mock_settings: Settings) -> None:
    result, _ = run_council("Internal council tool first?", settings=mock_settings)
    _, md_path = save_run(result, settings=mock_settings)
    md_text = md_path.read_text(encoding="utf-8")

    assert "## Chair Judgment" in md_text
    assert "**Strongest argument for:**" in md_text
    assert "**Strongest argument against:**" in md_text
    assert "**Deciding factor:**" in md_text
    assert result.dossier.strongest_argument_for in md_text


def test_schema_version_is_1_4(mock_settings: Settings) -> None:
    result, _ = run_council("Test schema version?", settings=mock_settings)
    assert result.schema_version == RUN_SCHEMA_VERSION
    assert RUN_SCHEMA_VERSION == "1.4"


def test_prompt_debug_off_by_default(tmp_path: Path) -> None:
    settings = Settings(llm_mode="mock", runs_dir=tmp_path, mock_model="mock-council-v1")
    result, collector = run_council("No debug?", settings=settings, save_prompt_debug=False)

    assert collector is None
    debug_path = tmp_path / result.dossier.run_id / "prompt_debug.md"
    assert not debug_path.exists()


def test_prompt_debug_file_created_when_flag_used(tmp_path: Path) -> None:
    settings = Settings(llm_mode="mock", runs_dir=tmp_path, mock_model="mock-council-v1")
    result, collector = run_council("Debug please?", settings=settings, save_prompt_debug=True)

    assert collector is not None
    assert len(collector.entries) > 0

    from council.prompt_debug import save_prompt_debug

    debug_path = save_prompt_debug(result, collector, tmp_path)
    assert debug_path.exists()
    text = debug_path.read_text(encoding="utf-8")
    assert "# Prompt Debug" in text
    assert "chair_synthesis" in text
    assert "sk-" not in text


def test_main_save_prompt_debug_flag(tmp_path: Path) -> None:
    code = main(["Flag test?", "--runs-dir", str(tmp_path), "--save-prompt-debug", "--quiet"])
    assert code == 0
    run_dirs = list(tmp_path.iterdir())
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "prompt_debug.md").exists()
    assert (run_dirs[0] / "run.json").exists()


def test_agent_briefs_include_quality_fields(mock_settings: Settings) -> None:
    result, _ = run_council("Quality fields check?", settings=mock_settings)
    brief = result.agent_briefs[0]
    assert brief.role_specific_finding
    assert brief.evidence_basis
    assert brief.uncertainty
    assert brief.decision_implication
    assert isinstance(brief.evidence_gaps, list)
    assert isinstance(brief.proposed_metrics, list)
    assert isinstance(brief.unsupported_assumptions, list)
