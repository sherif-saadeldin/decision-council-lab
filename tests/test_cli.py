from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from council.cli import format_user_error, parse_args, resolve_settings
from council.providers.errors import (
    MissingProviderCredentialError,
    ProviderResponseError,
    UnsupportedProviderModeError,
)
from council.providers.openai_errors import AUTH_FAILED_MESSAGE
from main import main


def test_parser_requires_question_or_help() -> None:
    args = parse_args(["What should we do?"])
    assert args.command == "run"
    assert args.question == "What should we do?"


def test_resolve_settings_overrides_runs_dir() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        args = parse_args(["run", "Test?", "--runs-dir", tmp])
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
    code = main([])
    assert code == 2


def test_format_user_error_uses_api_detail_only() -> None:
    exc = ProviderResponseError("openai", AUTH_FAILED_MESSAGE, source="api")
    assert format_user_error(exc) == AUTH_FAILED_MESSAGE
    assert "openai provider error" not in format_user_error(exc)


def test_main_provider_error_exit_code_no_traceback(capsys) -> None:
    auth_error = ProviderResponseError("openai", AUTH_FAILED_MESSAGE, source="api")

    with patch("main.run_council", side_effect=auth_error):
        code = main(["Auth failure test?"])

    captured = capsys.readouterr()
    assert code == 1
    assert AUTH_FAILED_MESSAGE in captured.err
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out
    assert "langgraph" not in captured.err.lower()


def test_main_quiet_provider_error_is_concise(capsys) -> None:
    auth_error = ProviderResponseError("openai", AUTH_FAILED_MESSAGE, source="api")

    with patch("main.run_council", side_effect=auth_error):
        code = main(["Quiet auth failure?", "--quiet"])

    captured = capsys.readouterr()
    assert code == 1
    assert captured.err.strip() == AUTH_FAILED_MESSAGE
    assert "Error" not in captured.err
    assert "Traceback" not in captured.err


def test_main_missing_credential_error_no_traceback(capsys) -> None:
    missing = MissingProviderCredentialError("openai", "OPENAI_API_KEY")

    with patch("main.run_council", side_effect=missing):
        code = main(["Missing key test?"])

    captured = capsys.readouterr()
    assert code == 1
    assert "OPENAI_API_KEY" in captured.err
    assert "Traceback" not in captured.err


def test_main_unsupported_mode_error_no_traceback(capsys) -> None:
    unsupported = UnsupportedProviderModeError("anthropic", ("mock", "openai"))

    with patch("main.run_council", side_effect=unsupported):
        code = main(["Unsupported mode test?"])

    captured = capsys.readouterr()
    assert code == 1
    assert "anthropic" in captured.err
    assert "Traceback" not in captured.err


def test_main_does_not_swallow_unexpected_errors() -> None:
    with patch("main.run_council", side_effect=RuntimeError("unexpected bug")):
        with pytest.raises(RuntimeError, match="unexpected bug"):
            main(["Unexpected failure?"])


def test_mock_mode_still_works_via_cli(capsys) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        code = main(["Mock CLI test?", "--runs-dir", tmp, "--quiet"])
    assert code == 0


def test_sources_parser_exists() -> None:
    args = parse_args(["sources", "list"])
    assert args.command == "sources"
    assert args.sources_command == "list"


def test_sources_query_parser_exists() -> None:
    args = parse_args(["sources", "query", "pack-1", "Should we ship?"])
    assert args.command == "sources"
    assert args.sources_command == "query"
    assert args.source_pack_id == "pack-1"


def test_run_parser_accepts_source_flags() -> None:
    args = parse_args(
        [
            "run",
            "Should we ship?",
            "--source-pack",
            "abc",
            "--source-path",
            "docs",
        ]
    )
    assert args.source_packs == ["abc"]
    assert args.source_paths == ["docs"]


def test_sources_commands_scan_list_show_remove(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "note.md").write_text("# Title\nhello", encoding="utf-8")
    code = main(["sources", "scan", str(tmp_path / "docs"), "--name", "docs-pack"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Source pack" in out
    code = main(["sources", "list"])
    assert code == 0
    out = capsys.readouterr().out
    assert "docs-pack" in out
