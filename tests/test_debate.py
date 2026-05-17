from __future__ import annotations

import json
import pytest

from council.config import Settings
from council.engine import run_council
from council.models import DEFAULT_DEBATE_ROUNDS, RUN_SCHEMA_VERSION
from council.providers.parsing import parse_debate_round_payload
from council.storage import save_run
from main import main
from tests.test_openai_provider import VALID_BRIEF_PAYLOAD


def test_default_debate_rounds_constant() -> None:
    assert DEFAULT_DEBATE_ROUNDS == 2


def test_run_council_default_includes_two_debate_rounds(mock_settings: Settings) -> None:
    result, _ = run_council("Debate default rounds?", settings=mock_settings)
    assert result.debate_transcript is not None
    assert result.debate_transcript.rounds_completed == 2
    assert len(result.debate_transcript.rounds) == 2


def test_debate_rounds_zero_skips_transcript(mock_settings: Settings) -> None:
    result, _ = run_council(
        "No debate?",
        settings=mock_settings,
        debate_rounds=0,
    )
    assert result.debate_transcript is None


def test_markdown_contains_debate_transcript(mock_settings: Settings) -> None:
    result, _ = run_council("Markdown debate?", settings=mock_settings)
    _, md_path = save_run(result, settings=mock_settings)
    md_text = md_path.read_text(encoding="utf-8")

    assert "## Debate Transcript" in md_text
    assert md_text.index("## Debate Transcript") < md_text.index("## Chair Judgment")
    assert "### Round 1" in md_text
    assert "**Advocate**" in md_text
    assert "**Skeptic**" in md_text
    assert "**Moderator**" in md_text
    assert "**Unresolved disagreements (final):**" in md_text


def test_chair_uses_debate_context_in_disagreement_resolution(mock_settings: Settings) -> None:
    result, _ = run_council("Internal tool with debate?", settings=mock_settings)
    assert result.debate_transcript is not None
    assert "debate rounds" in result.dossier.disagreement_resolution.lower()


def test_json_includes_debate_transcript(mock_settings: Settings) -> None:
    result, _ = run_council("JSON debate?", settings=mock_settings)
    json_path, _ = save_run(result, settings=mock_settings)
    payload = json.loads(json_path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "1.8"
    assert payload["debate_transcript"] is not None
    assert payload["debate_transcript"]["rounds_completed"] == 2


def test_schema_version_is_current(mock_settings: Settings) -> None:
    result, _ = run_council("Schema check?", settings=mock_settings)
    assert result.schema_version == RUN_SCHEMA_VERSION
    assert RUN_SCHEMA_VERSION == "1.8"


def test_debate_positions_cite_agent_briefs(mock_settings: Settings) -> None:
    result, _ = run_council("Cite briefs?", settings=mock_settings)
    assert result.debate_transcript is not None
    first = result.debate_transcript.rounds[0]
    assert first.advocate.cited_roles
    assert first.skeptic.cited_roles
    assert first.skeptic.responds_to_prior


def test_round_two_skeptic_responds_to_advocate(mock_settings: Settings) -> None:
    result, _ = run_council("Round two exchange?", settings=mock_settings)
    second = result.debate_transcript.rounds[1]
    assert "advocate" in second.skeptic.responds_to_prior.lower() or second.skeptic.responds_to_prior


def test_parse_rejects_malformed_debate_round() -> None:
    payload = {
        "advocate": {
            "argument": "For",
            "cited_roles": [],
            "responds_to_prior": "None",
            "uncertainty": "High",
        },
    }
    from council.providers.errors import ProviderResponseError

    with pytest.raises(ProviderResponseError, match="validation failed"):
        parse_debate_round_payload(json.dumps(payload), round_number=1, provider_name="mock")


def test_cli_debate_rounds_flag(tmp_path) -> None:
    code = main(
        [
            "CLI debate?",
            "--runs-dir",
            str(tmp_path),
            "--debate-rounds",
            "0",
            "--quiet",
        ]
    )
    assert code == 0
    run_json = json.loads((list(tmp_path.iterdir())[0] / "run.json").read_text(encoding="utf-8"))
    assert run_json["debate_transcript"] is None


def test_prompt_debug_records_debate_steps(mock_settings: Settings) -> None:
    result, collector = run_council(
        "Debug debate?",
        settings=mock_settings,
        save_prompt_debug=True,
    )
    assert collector is not None
    steps = {entry.step for entry in collector.entries}
    assert "debate_round_1" in steps
    assert "debate_round_2" in steps
    assert "chair_synthesis" in steps
    assert result.debate_transcript is not None


def test_openai_provider_debate_round_parsing() -> None:
    payload = {
        "advocate": {
            "argument": "Proceed because research supports internal CLI artifacts.",
            "cited_roles": ["research"],
            "responds_to_prior": "Opening round.",
            "uncertainty": "Transfer to live models.",
        },
        "skeptic": {
            "argument": "Pause because skeptic brief warns of false confidence.",
            "cited_roles": ["skeptic"],
            "responds_to_prior": "Proceed because research supports internal CLI artifacts.",
            "uncertainty": "Review enforcement.",
        },
        "moderator": {
            "resolved_points": ["Scope is internal tooling."],
            "unresolved_points": ["Quality proof timing."],
            "deciding_tensions": ["Speed vs rigor."],
            "evidence_gaps": ["No rubric provided."],
        },
    }
    debate_round = parse_debate_round_payload(
        json.dumps(payload),
        round_number=1,
        provider_name="openai",
    )
    assert debate_round.advocate.cited_roles == ["research"]
    assert debate_round.moderator.unresolved_points


def test_preset_modes_unaffected_by_debate(mock_settings: Settings) -> None:
    from council.model_presets import apply_preset

    settings = apply_preset(mock_settings, "mock")
    result, _ = run_council("Preset debate?", settings=settings, debate_rounds=2)
    assert result.provider_metadata.provider_name == "mock"
    assert result.debate_transcript is not None


def test_openai_complete_still_works_with_mocked_client() -> None:
    from unittest.mock import MagicMock

    from council.models import AgentRole
    from council.providers.models import ProviderRequest
    from council.providers.openai_provider import OpenAIProvider

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.output_text = json.dumps(VALID_BRIEF_PAYLOAD)
    mock_client.responses.create.return_value = mock_response

    provider = OpenAIProvider(
        api_key="test-key",
        model_name="gpt-4.1-mini",
        client=mock_client,
    )
    response = provider.complete(
        ProviderRequest(role=AgentRole.CONTEXT, question="Ship?", prior_briefs=[])
    )
    assert response.brief.headline == "Test headline"
