from __future__ import annotations

import json

import pytest

from council.engine import run_council
from council.models import RUN_SCHEMA_VERSION, AgentRole
from council.providers.errors import ProviderResponseError
from council.providers.parsing import parse_agent_brief_payload, parse_dossier_payload
from council.config import Settings
from council.storage import save_run
from tests.conftest import assert_proposed_metrics_labeled


def test_mock_dossier_includes_evidence_gaps(mock_settings: Settings) -> None:
    result, _ = run_council("Should we build an internal tool first?", settings=mock_settings)
    assert result.dossier.evidence_gaps
    assert any("rubric" in gap.lower() or "baseline" in gap.lower() for gap in result.dossier.evidence_gaps)


def test_mock_proposed_metrics_are_labeled(mock_settings: Settings) -> None:
    result, _ = run_council("Quality guardrails check?", settings=mock_settings)
    assert result.dossier.proposed_metrics
    assert_proposed_metrics_labeled(result.dossier.proposed_metrics)
    for brief in result.agent_briefs:
        assert_proposed_metrics_labeled(brief.proposed_metrics)


def test_schema_version_incremented_to_1_5(mock_settings: Settings) -> None:
    result, _ = run_council("Schema bump?", settings=mock_settings)
    assert result.schema_version == "1.5"
    assert RUN_SCHEMA_VERSION == "1.5"


def test_markdown_shows_evidence_gaps_prominently(mock_settings: Settings) -> None:
    result, _ = run_council("Markdown evidence gaps?", settings=mock_settings)
    _, md_path = save_run(result, settings=mock_settings)
    md_text = md_path.read_text(encoding="utf-8")

    assert "## Evidence Gaps" in md_text
    assert result.dossier.evidence_gaps[0] in md_text
    assert "## Proposed Metrics" in md_text
    assert "**Proposed:**" in md_text


def test_parse_rejects_unlabeled_proposed_metrics() -> None:
    payload = {
        "headline": "H",
        "role_specific_finding": "F",
        "evidence_basis": "E",
        "uncertainty": "U",
        "decision_implication": "I",
        "reasoning": "R",
        "confidence": 0.5,
        "source_refs": [],
        "evidence_gaps": [],
        "proposed_metrics": ["error rate below 5%"],
        "unsupported_assumptions": [],
    }
    with pytest.raises(ProviderResponseError, match="validation failed"):
        parse_agent_brief_payload(json.dumps(payload), AgentRole.RISK, provider_name="mock")


def test_parse_accepts_labeled_proposed_metrics() -> None:
    payload = {
        "headline": "H",
        "role_specific_finding": "F",
        "evidence_basis": "E",
        "uncertainty": "U",
        "decision_implication": "I",
        "reasoning": "R",
        "confidence": 0.5,
        "source_refs": [],
        "evidence_gaps": ["No deadline provided."],
        "proposed_metrics": ["proposed: two schema-valid runs"],
        "unsupported_assumptions": [],
    }
    brief = parse_agent_brief_payload(json.dumps(payload), AgentRole.CONTEXT, provider_name="mock")
    assert brief.proposed_metrics == ["proposed: two schema-valid runs"]
    assert brief.evidence_gaps == ["No deadline provided."]


def test_parse_dossier_requires_guardrail_fields() -> None:
    from tests.test_openai_provider import VALID_DOSSIER_PAYLOAD

    dossier = parse_dossier_payload(
        json.dumps(VALID_DOSSIER_PAYLOAD),
        question="Ship?",
        run_id="run-guard",
        provider_name="openai",
    )
    assert dossier.evidence_gaps == ["Missing adoption data."]
    assert dossier.proposed_metrics[0].startswith("proposed:")
