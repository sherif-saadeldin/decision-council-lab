from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from council.cli import render_council_result, render_runs_show
from council.config import Settings
from council.council_session import CouncilSessionRequest, run_council_session
from council.implementation_pack import IMPLEMENTATION_PACK_FILENAMES, write_implementation_pack
from council.run_catalog import RunNotFoundError, get_run_summary, list_recent_runs
from council.runtime import RuntimeOptions
from council.storage import save_run
from main import main


def test_council_markdown_contains_summary_and_role_table(mock_settings: Settings) -> None:
    request = CouncilSessionRequest(
        question="Markdown layout test?",
        routing_mode="manual",
        council_presets=["mock"],
        debate_rounds=0,
        base_settings=mock_settings,
        runtime=RuntimeOptions(show_progress=False),
    )
    session = run_council_session(request)
    _, md_path = save_run(session.result, settings=mock_settings)
    text = md_path.read_text(encoding="utf-8")
    assert "## Council Session Summary" in text
    assert "## Role & Model Assignments" in text
    assert "| Role | Preset | Provider | Model |" in text
    assert "## Chair Verdict" in text
    assert "## Next Suggested Command" in text
    assert "runs show" in text


def test_implementation_pack_contains_run_id_and_approval_gates(
    mock_settings: Settings,
) -> None:
    request = CouncilSessionRequest(
        question="Pack quality test?",
        council_presets=["mock"],
        debate_rounds=0,
        base_settings=mock_settings,
        runtime=RuntimeOptions(show_progress=False),
    )
    session = run_council_session(request)
    run_dir = mock_settings.runs_dir / session.result.dossier.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    paths = write_implementation_pack(run_dir, session.result)
    assert len(paths) == len(IMPLEMENTATION_PACK_FILENAMES)
    run_id = session.result.dossier.run_id
    for name in IMPLEMENTATION_PACK_FILENAMES:
        content = (run_dir / name).read_text(encoding="utf-8")
        assert f"`{run_id}`" in content
        assert "Approval gates" in content or "approval gates" in content.lower()


def test_save_run_lists_pack_files_in_markdown(mock_settings: Settings) -> None:
    request = CouncilSessionRequest(
        question="Pack section in run.md?",
        council_presets=["mock"],
        debate_rounds=0,
        base_settings=mock_settings,
        runtime=RuntimeOptions(show_progress=False),
    )
    session = run_council_session(request)
    run_dir = mock_settings.runs_dir / session.result.dossier.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    pack_paths = write_implementation_pack(run_dir, session.result)
    _, md_path = save_run(
        session.result,
        settings=mock_settings,
        implementation_pack_paths=pack_paths,
    )
    text = md_path.read_text(encoding="utf-8")
    assert "## Implementation Pack" in text
    assert "mvp_scope.md" in text
    assert "approval_checklist.md" in text


def test_render_council_result_includes_next_command(mock_settings: Settings) -> None:
    from council.engine import run_council

    result, _ = run_council(
        "CLI output test?",
        settings=mock_settings,
        debate_rounds=0,
    )

    buffer = StringIO()
    console = Console(file=buffer, force_terminal=True, width=120)
    run_id = result.dossier.run_id
    render_council_result(
        console,
        result,
        mock_settings.runs_dir / run_id / "run.json",
        mock_settings.runs_dir / run_id / "run.md",
        quiet=False,
    )
    output = buffer.getvalue()
    assert run_id in output
    assert f"runs show {run_id}" in output


def test_runs_list_finds_recent_runs(mock_settings: Settings) -> None:
    request = CouncilSessionRequest(
        question="List runs test?",
        council_presets=["mock"],
        debate_rounds=0,
        base_settings=mock_settings,
        runtime=RuntimeOptions(show_progress=False),
    )
    session = run_council_session(request)
    save_run(session.result, settings=mock_settings)
    summaries = list_recent_runs(mock_settings.runs_dir, limit=10)
    assert any(item.run_id == session.result.dossier.run_id for item in summaries)
    assert any(item.run_kind == "council" for item in summaries)


def test_runs_show_missing_run_raises(mock_settings: Settings) -> None:
    with pytest.raises(RunNotFoundError):
        get_run_summary(mock_settings.runs_dir, "00000000-0000-0000-0000-000000000000")


def test_main_runs_show_missing_returns_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    code = main(["runs", "show", "missing-run-id", "--runs-dir", str(tmp_path)])
    assert code == 1


def test_main_runs_list_and_show(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(llm_mode="mock", runs_dir=tmp_path, mock_model="mock-council-v1")
    request = CouncilSessionRequest(
        question="Runs CLI test?",
        routing_mode="manual",
        council_presets=["mock"],
        debate_rounds=0,
        base_settings=settings,
        runtime=RuntimeOptions(show_progress=False),
    )
    session = run_council_session(request)
    save_run(session.result, settings=settings)
    run_id = session.result.dossier.run_id

    code = main(["runs", "list", "--runs-dir", str(tmp_path)])
    assert code == 0

    code = main(["runs", "show", run_id, "--runs-dir", str(tmp_path)])
    assert code == 0

    summary = get_run_summary(tmp_path, run_id)
    buffer = StringIO()
    render_runs_show(Console(file=buffer, force_terminal=True, width=120), summary)
    output = buffer.getvalue()
    assert run_id in output
    assert "run.md" in output
    assert "run.json" in output
    assert "Open:" in output
    normalized = "".join(output.split())
    assert str(summary.md_path.resolve()).replace(" ", "") in normalized
    assert str(summary.json_path.resolve()).replace(" ", "") in normalized
    assert "…" not in output
