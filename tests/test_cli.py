from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from council.cli import build_parser, resolve_settings
from main import main


def test_parser_requires_question_or_help() -> None:
    parser = build_parser()
    assert parser.parse_args(["What should we do?"]).question == "What should we do?"


def test_resolve_settings_overrides_runs_dir() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        args = build_parser().parse_args(["Test?", "--runs-dir", tmp])
        settings = resolve_settings(args)
        assert settings.runs_dir == Path(tmp)


def test_main_quiet_prints_artifact_paths_only(capsys) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        code = main(["Quiet test?", "--runs-dir", tmp, "--quiet"])
        captured = capsys.readouterr()
    assert code == 0
    assert "run.json" in captured.out
    assert "run.md" in captured.out
    assert "Executive Summary" not in captured.out


def test_main_without_question_prints_help() -> None:
    with patch("sys.argv", ["main.py"]):
        code = main([])
    assert code == 1
