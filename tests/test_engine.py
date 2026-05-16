from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from council.config import Settings
from council.engine import get_provider, run_council
from council.models import AgentRole
from council.storage import save_run


def test_get_provider_rejects_non_mock_mode() -> None:
    settings = Settings(llm_mode="openai", runs_dir=Path("./runs"), mock_model="mock-council-v1")
    with pytest.raises(ValueError, match="Mock mode only"):
        get_provider(settings)


def test_run_council_produces_complete_dossier() -> None:
    question = "Should I build a decision council engine as an internal tool first?"
    result = run_council(question)

    dossier = result.dossier
    assert dossier.decision_question == question
    assert dossier.recommendation
    assert dossier.assumptions
    assert dossier.arguments_for
    assert dossier.arguments_against
    assert dossier.risks
    assert dossier.kill_criteria
    assert dossier.next_actions
    assert dossier.open_questions
    assert 0.0 <= dossier.confidence_score <= 1.0
    assert result.provider_name == "mock"
    assert len(result.agent_briefs) == len(AgentRole) - 1


def test_save_run_writes_json_and_markdown() -> None:
    result = run_council("Should we prototype internally first?")

    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(llm_mode="mock", runs_dir=Path(tmp), mock_model="mock-council-v1")
        json_path, md_path = save_run(result, settings=settings)

        assert json_path.exists()
        assert md_path.exists()
        assert json_path.parent.name == result.dossier.run_id
        json_text = json_path.read_text(encoding="utf-8")
        md_text = md_path.read_text(encoding="utf-8")

        assert '"schema_version": "1.1"' in json_text
        assert '"decision_question"' in json_text
        assert "# Decision Council Dossier" in md_text
        assert "## Executive Summary" in md_text
        assert "## Confidence Score" in md_text
        assert md_text.index("## Assumptions") < md_text.index("## Recommendation")
