"""Slice 5.10 — CLI surface for the review lifecycle.

Mirrors the chat slash verbs (/approve, /reject, /archive, /review, /pack)
as first-class `main.py` subcommands so CI / scripts / cron can govern
decisions without an interactive chat session.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from council.config import Settings
from council.models import CouncilRunResult, DecisionDossier, DecisionType
from council.providers.models import ProviderMetadata
from council.run_catalog import get_run_summary
from council.storage import save_run
from main import main


def _dossier(run_id: str) -> DecisionDossier:
    return DecisionDossier(
        run_id=run_id,
        decision_question="Should we ship the caching layer with a 2-week pilot?",
        decision_type=DecisionType.PROCEED_WITH_CONSTRAINTS,
        # `ensure_verdict_quality_for_pack` requires direct_answer and
        # approval_gate to be at least 20 chars and bullet lists to have
        # at least 3 items — so make the test dossier pass-quality.
        direct_answer="Proceed with constraints — start with a narrow, flagged pilot.",
        why_this_decision=["Solid evidence base", "Manageable scope", "Reversible plan"],
        what_would_change_mind=["Adoption stalls", "Budget cut", "Provider degrades"],
        next_actions=[
            "Define cache hit-rate target",
            "Ship behind a flag",
            "Review in 2 weeks",
        ],
        do_not_do=[
            "Default-on the flag",
            "Skip the pilot metrics review",
            "Couple to billing logic",
        ],
        approval_gate="VP must approve the flag default before GA.",
        evidence_gaps=[
            "No A/B baseline yet",
            "No churn correlation data",
            "No tier-mix study",
        ],
        recommendation="Proceed with constraints — pilot for two weeks then GA.",
        confidence_score=0.7,
    )


def _council_result(run_id: str) -> CouncilRunResult:
    return CouncilRunResult(
        dossier=_dossier(run_id),
        agent_briefs=[],
        provider_metadata=ProviderMetadata(
            provider_name="mock",
            model_name="mock-council-v1",
            mode="mock",
            supports_structured_output=True,
            supports_streaming=False,
        ),
        council_mode="multi",
    )


@pytest.fixture
def saved_run(tmp_path: Path) -> tuple[Path, str]:
    settings = Settings(llm_mode="mock", runs_dir=tmp_path, mock_model="mock-council-v1")
    save_run(_council_result("cli-1"), settings=settings)
    return tmp_path, "cli-1"


# --- approve ----------------------------------------------------------------


def test_cli_approve_marks_run_approved(
    saved_run, capsys: pytest.CaptureFixture[str]
) -> None:
    runs_dir, run_id = saved_run
    code = main(["approve", run_id, "--runs-dir", str(runs_dir), "--actor", "alice"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Approved" in out
    summary = get_run_summary(runs_dir, run_id)
    assert summary.lifecycle_status == "approved"


def test_cli_approve_with_note_persists(saved_run) -> None:
    runs_dir, run_id = saved_run
    code = main(
        [
            "approve",
            run_id,
            "--runs-dir",
            str(runs_dir),
            "--actor",
            "alice",
            "--note",
            "shipping it",
        ]
    )
    assert code == 0
    from council.review import load_run_result

    result = load_run_result(runs_dir, run_id)
    assert result.review.approved_by == "alice"
    assert result.review.review_reason == "shipping it"


def test_cli_approve_unknown_run_returns_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["approve", "no-such-run", "--runs-dir", str(tmp_path)])
    assert code == 1
    assert "Run not found" in capsys.readouterr().err


def test_cli_approve_uses_env_actor_when_flag_omitted(
    saved_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs_dir, run_id = saved_run
    monkeypatch.setenv("DCOUNCIL_REVIEW_ACTOR", "env-bob")
    # Strip USER/USERNAME so the env actor wins.
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.delenv("USERNAME", raising=False)
    code = main(["approve", run_id, "--runs-dir", str(runs_dir)])
    assert code == 0
    summary = get_run_summary(runs_dir, run_id)
    assert summary.lifecycle_status == "approved"
    from council.review import load_run_result

    result = load_run_result(runs_dir, run_id)
    assert result.review.approved_by == "env-bob"


# --- reject -----------------------------------------------------------------


def test_cli_reject_requires_reason_flag(saved_run) -> None:
    """argparse refuses to dispatch; main() catches SystemExit and returns 2."""
    runs_dir, run_id = saved_run
    code = main(["reject", run_id, "--runs-dir", str(runs_dir)])
    assert code == 2


def test_cli_reject_with_reason_persists(saved_run) -> None:
    runs_dir, run_id = saved_run
    code = main(
        [
            "reject",
            run_id,
            "--runs-dir",
            str(runs_dir),
            "--reason",
            "scope too broad",
            "--actor",
            "carol",
        ]
    )
    assert code == 0
    summary = get_run_summary(runs_dir, run_id)
    assert summary.lifecycle_status == "rejected"
    from council.review import load_run_result

    result = load_run_result(runs_dir, run_id)
    assert result.review.rejected_by == "carol"
    assert result.review.review_reason == "scope too broad"


# --- archive ----------------------------------------------------------------


def test_cli_archive_marks_archived_and_blocks_further_transitions(
    saved_run, capsys: pytest.CaptureFixture[str]
) -> None:
    runs_dir, run_id = saved_run
    code = main(["archive", run_id, "--runs-dir", str(runs_dir), "--note", "EOL"])
    assert code == 0
    assert "Archived" in capsys.readouterr().out
    # Further approve must now fail with the archived guard.
    code = main(["approve", run_id, "--runs-dir", str(runs_dir)])
    assert code == 1
    assert "archived" in capsys.readouterr().err


# --- review -----------------------------------------------------------------


def test_cli_review_shows_history(saved_run, capsys: pytest.CaptureFixture[str]) -> None:
    runs_dir, run_id = saved_run
    main(["approve", run_id, "--runs-dir", str(runs_dir), "--actor", "alice"])
    capsys.readouterr()  # drain approve output
    code = main(["review", run_id, "--runs-dir", str(runs_dir)])
    assert code == 0
    text = capsys.readouterr().out
    assert "Decision Review" in text
    assert "approved" in text
    assert "alice" in text
    assert "History" in text


def test_cli_review_unknown_run_returns_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["review", "no-such-run", "--runs-dir", str(tmp_path)])
    assert code == 1
    assert "Run not found" in capsys.readouterr().err


# --- pack -------------------------------------------------------------------


def test_cli_pack_blocked_on_draft(
    saved_run, capsys: pytest.CaptureFixture[str]
) -> None:
    runs_dir, run_id = saved_run
    code = main(["pack", run_id, "--runs-dir", str(runs_dir)])
    assert code == 1
    err = capsys.readouterr().err
    assert "Decision is not approved" in err
    assert run_id in err
    assert not (runs_dir / run_id / "implementation_plan.md").exists()


def test_cli_pack_after_approval_writes_files(
    saved_run, capsys: pytest.CaptureFixture[str]
) -> None:
    runs_dir, run_id = saved_run
    main(["approve", run_id, "--runs-dir", str(runs_dir), "--actor", "alice"])
    capsys.readouterr()
    code = main(["pack", run_id, "--runs-dir", str(runs_dir)])
    assert code == 0
    out = capsys.readouterr().out
    assert "Implementation pack:" in out
    assert (runs_dir / run_id / "mvp_scope.md").is_file()
    assert (runs_dir / run_id / "implementation_plan.md").is_file()


def test_cli_pack_override_bypasses_gate_on_draft(
    saved_run, capsys: pytest.CaptureFixture[str]
) -> None:
    runs_dir, run_id = saved_run
    code = main([
        "pack",
        run_id,
        "--runs-dir",
        str(runs_dir),
        "--allow-unapproved",
    ])
    assert code == 0
    assert (runs_dir / run_id / "implementation_plan.md").is_file()


def test_cli_pack_unknown_run_returns_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["pack", "no-such-run", "--runs-dir", str(tmp_path)])
    assert code == 1
    assert "Run not found" in capsys.readouterr().err


# --- legacy positional safety ----------------------------------------------


def test_legacy_positional_still_routes_to_run() -> None:
    """`main.py "Q"` legacy must keep routing to `run`, not get captured
    by any of the new lifecycle verbs."""
    from council.cli import normalize_argv

    assert normalize_argv(["Should we ship?"]) == ["run", "Should we ship?"]
    # Real lifecycle verbs are not captured as positional questions.
    assert normalize_argv(["approve", "abc"]) == ["approve", "abc"]
    assert normalize_argv(["reject", "abc"]) == ["reject", "abc"]
    assert normalize_argv(["pack", "abc"]) == ["pack", "abc"]


# --- revision chain --------------------------------------------------------


def test_cli_approve_revision_supersedes_parent(tmp_path: Path) -> None:
    """End-to-end: parent and child saved, mark child as revision via the
    review module, then `approve <child>` flips the parent to superseded."""
    settings = Settings(llm_mode="mock", runs_dir=tmp_path, mock_model="mock-council-v1")
    save_run(_council_result("parent-1"), settings=settings)
    save_run(_council_result("child-1"), settings=settings)
    from council.review import mark_revision_of

    mark_revision_of(tmp_path, "child-1", "parent-1", actor="reviewer")
    code = main(["approve", "child-1", "--runs-dir", str(tmp_path), "--actor", "reviewer"])
    assert code == 0
    parent_summary = get_run_summary(tmp_path, "parent-1")
    child_summary = get_run_summary(tmp_path, "child-1")
    assert child_summary.lifecycle_status == "approved"
    assert parent_summary.lifecycle_status == "superseded"
    assert parent_summary.superseded_by_run_id == "child-1"
