from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from council.config import Settings
from council.engine import get_provider, run_council
from council.models import AgentRole, RUN_SCHEMA_VERSION
from council.providers.errors import UnsupportedProviderModeError
from council.storage import save_run


def test_get_provider_rejects_non_mock_mode() -> None:
    settings = Settings(
        llm_mode="anthropic",
        runs_dir=Path("./runs"),
        mock_model="mock-council-v1",
    )
    with pytest.raises(UnsupportedProviderModeError, match="Unsupported LLM_MODE"):
        get_provider(settings)


def test_run_council_produces_complete_dossier() -> None:
    question = "Should I build a decision council engine as an internal tool first?"
    result, _ = run_council(question)

    dossier = result.dossier
    assert dossier.decision_question == question
    assert dossier.recommendation
    assert dossier.decision_type
    assert dossier.strongest_argument_for
    assert dossier.strongest_argument_against
    assert dossier.deciding_factor
    assert dossier.confidence_rationale
    assert dossier.disagreement_resolution
    assert dossier.assumptions
    assert dossier.arguments_for
    assert dossier.arguments_against
    assert dossier.risks
    assert dossier.kill_criteria
    assert dossier.next_actions
    assert dossier.open_questions
    assert 0.0 <= dossier.confidence_score <= 1.0
    assert result.provider_metadata.provider_name == "mock"
    assert len(result.agent_briefs) == len(AgentRole) - 1
    assert all(brief.role_specific_finding for brief in result.agent_briefs)


def test_save_run_writes_json_and_markdown() -> None:
    result, _ = run_council("Should we prototype internally first?")

    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(llm_mode="mock", runs_dir=Path(tmp), mock_model="mock-council-v1")
        json_path, md_path = save_run(result, settings=settings)

        assert json_path.exists()
        assert md_path.exists()
        assert json_path.parent.name == result.dossier.run_id
        json_text = json_path.read_text(encoding="utf-8")
        md_text = md_path.read_text(encoding="utf-8")

        assert f'"schema_version": "{RUN_SCHEMA_VERSION}"' in json_text
        assert '"decision_type"' in json_text
        assert "# Decision Council Dossier" in md_text
        assert "## Chair Judgment" in md_text
        assert "## Confidence Score" in md_text
        assert md_text.index("## Assumptions") < md_text.index("## Recommendation")
        assert "raw_response" not in md_text
        assert '"provider_metadata"' in json_text
