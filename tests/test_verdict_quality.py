from __future__ import annotations

from pathlib import Path

import pytest

from council.config import Settings
from council.council_session import CouncilSessionRequest, run_council_session
from council.engine import run_council
from council.implementation_pack import write_implementation_pack
from council.models import DecisionDossier, DecisionType
from council.runtime import RuntimeOptions
from council.verdict_quality import (
    GENERIC_VERDICT_PHRASES,
    VerdictQualityError,
    direct_answer_repeats_question,
    ensure_verdict_quality_for_pack,
    is_generic_verdict_text,
    is_verdict_quality_sufficient,
    verdict_quality_issues,
)
from tests.conftest import assert_mock_run_schema


def test_mock_council_has_direct_answer(mock_settings: Settings) -> None:
    result, _ = run_council("Should we migrate the billing service to Rust?", settings=mock_settings)
    assert_mock_run_schema(result)
    direct = result.dossier.direct_answer.strip()
    question = result.dossier.decision_question.strip()
    assert len(direct) >= 20
    assert question.lower() not in direct.lower()
    assert direct.startswith(("Yes", "No", "Pause", "Proceed with constraints", "Reject"))


def test_direct_answer_does_not_contain_full_question(mock_settings: Settings) -> None:
    question = (
        "Should I continue building this Decision Council tool as an internal CLI first?"
    )
    result, _ = run_council(question, settings=mock_settings)
    direct = result.dossier.direct_answer.strip()
    assert question not in direct
    assert not direct_answer_repeats_question(direct, question)


def test_quality_checker_rejects_direct_answer_that_repeats_question() -> None:
    question = (
        "Should I continue building this Decision Council tool as an internal CLI first?"
    )
    echoed = (
        f"Yes—build '{question}' as an internal capability first, with scope limited to CLI workflows."
    )
    dossier = DecisionDossier(
        run_id="echo-run",
        decision_question=question,
        decision_type=DecisionType.PROCEED_WITH_CONSTRAINTS,
        direct_answer=echoed,
        why_this_decision=["One", "Two", "Three"],
        what_would_change_mind=["A", "B", "C"],
        next_actions=["N1", "N2", "N3"],
        do_not_do=["D1", "D2", "D3"],
        approval_gate="Approve scope before implementation pack work begins.",
        recommendation="Proceed with constraints.",
    )
    assert direct_answer_repeats_question(echoed, question)
    issues = verdict_quality_issues(dossier)
    assert any("repeats" in issue for issue in issues)
    assert not is_verdict_quality_sufficient(dossier)


def test_mock_direct_answer_is_clean_sentence(mock_settings: Settings) -> None:
    result, _ = run_council(
        "Should we build an internal analytics dashboard?",
        settings=mock_settings,
    )
    direct = result.dossier.direct_answer.strip()
    assert direct.count("?") == 0
    assert '"' not in direct and "'" not in direct
    assert direct.startswith(("Yes", "No", "Pause", "Proceed with constraints", "Reject"))


def test_mock_council_has_do_next_do_not_do_and_approval_gate(mock_settings: Settings) -> None:
    result, _ = run_council("Should we hire two engineers for the API team?", settings=mock_settings)
    dossier = result.dossier
    assert len(dossier.next_actions) >= 3
    assert len(dossier.do_not_do) >= 3
    assert len(dossier.approval_gate.strip()) >= 20
    assert len(dossier.why_this_decision) >= 3
    assert len(dossier.what_would_change_mind) >= 3


def test_mock_verdict_is_not_generic_placeholder(mock_settings: Settings) -> None:
    result, _ = run_council("Should we build an internal analytics dashboard?", settings=mock_settings)
    combined = " ".join(
        [
            result.dossier.direct_answer,
            result.dossier.recommendation,
            result.dossier.approval_gate,
            *result.dossier.do_not_do,
        ]
    ).lower()
    for phrase in GENERIC_VERDICT_PHRASES:
        assert phrase not in combined, f"generic phrase found: {phrase!r}"
    assert not is_generic_verdict_text(result.dossier.direct_answer)


def test_council_markdown_puts_direct_answer_first(mock_settings: Settings) -> None:
    request = CouncilSessionRequest(
        question="Direct answer ordering test?",
        council_presets=["mock"],
        debate_rounds=0,
        base_settings=mock_settings,
        runtime=RuntimeOptions(show_progress=False),
    )
    session = run_council_session(request)
    from council.storage import save_run

    _, md_path = save_run(session.result, settings=mock_settings)
    text = md_path.read_text(encoding="utf-8")
    direct_idx = text.index("## Direct Answer")
    summary_idx = text.index("## Council Session Summary")
    assert direct_idx < summary_idx
    assert session.result.dossier.direct_answer.strip() in text


def test_implementation_pack_blocked_without_quality_fields(tmp_path: Path) -> None:
    bad = DecisionDossier(
        run_id="bad-run",
        decision_question="Ship feature X?",
        decision_type=DecisionType.PAUSE,
        direct_answer="Maybe later.",
        recommendation="Pause",
        next_actions=["Wait"],
        do_not_do=["Rush"],
        approval_gate="TBD",
    )
    assert not is_verdict_quality_sufficient(bad)
    with pytest.raises(VerdictQualityError, match="Implementation pack blocked"):
        ensure_verdict_quality_for_pack(bad)

    from council.models import CouncilRunResult
    from council.providers.models import ProviderMetadata

    result = CouncilRunResult(
        dossier=bad,
        agent_briefs=[],
        provider_metadata=ProviderMetadata(
            provider_name="mock",
            model_name="mock",
            mode="mock",
            supports_structured_output=True,
            supports_streaming=False,
        ),
    )
    run_dir = tmp_path / "bad-run"
    run_dir.mkdir()
    with pytest.raises(VerdictQualityError):
        write_implementation_pack(run_dir, result)


def test_implementation_pack_succeeds_with_quality_verdict(mock_settings: Settings) -> None:
    request = CouncilSessionRequest(
        question="Pack gate quality test?",
        council_presets=["mock"],
        debate_rounds=0,
        base_settings=mock_settings,
        runtime=RuntimeOptions(show_progress=False),
    )
    session = run_council_session(request)
    assert is_verdict_quality_sufficient(session.result.dossier)
    run_dir = mock_settings.runs_dir / session.result.dossier.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    paths = write_implementation_pack(run_dir, session.result)
    assert paths
    header = (run_dir / "mvp_scope.md").read_text(encoding="utf-8")
    assert session.result.dossier.direct_answer.strip() in header
    assert session.result.dossier.approval_gate.strip() in header
