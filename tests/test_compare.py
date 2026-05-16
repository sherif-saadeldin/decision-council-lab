from __future__ import annotations

import json
from pathlib import Path

import pytest

from council.cli import build_compare_request, parse_args
from council.compare import (
    CompareConfigError,
    CompareRequest,
    ComparisonTarget,
    build_targets,
    run_comparison,
)
from council.config_profiles import init_config_file
from council.redaction import assert_no_credential_leaks
from council.secrets import delete_keyring_secret, set_keyring_secret
from main import main

SECRET_VALUE = "sk-9f3a2b1c8e7d6a5b4c3d2e1f0a9b8c7d6"


def test_build_targets_requires_at_least_one() -> None:
    with pytest.raises(CompareConfigError):
        build_targets([], [])


def test_parse_compare_args() -> None:
    args = parse_args(
        [
            "compare",
            "Should we ship?",
            "--presets",
            "mock,openai-mini",
            "--debate-rounds",
            "1",
        ]
    )
    assert args.command == "compare"
    request = build_compare_request(args)
    assert request.question == "Should we ship?"
    assert len(request.targets) == 2
    assert request.targets[0] == ComparisonTarget(kind="preset", name="mock")


def test_compare_mock_preset_saves_artifacts(tmp_path: Path, capsys) -> None:
    code = main(
        [
            "compare",
            "Compare mock only?",
            "--presets",
            "mock",
            "--runs-dir",
            str(tmp_path),
            "--debate-rounds",
            "0",
            "-q",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    comparison_dirs = list((tmp_path / "comparisons").iterdir())
    assert len(comparison_dirs) == 1
    comparison_dir = comparison_dirs[0]
    json_path = comparison_dir / "comparison.json"
    md_path = comparison_dir / "comparison.md"
    assert json_path.exists()
    assert md_path.exists()
    assert "comparison.json" in captured.out.replace("\n", "")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["question"] == "Compare mock only?"
    assert payload["presets_tested"] == ["mock"]
    assert len(payload["entries"]) == 1
    assert payload["entries"][0]["success"] is True
    assert payload["entries"][0]["run_json_path"]


def test_compare_two_presets_sequential(tmp_path: Path) -> None:
    request = CompareRequest(
        question="Two mocks?",
        targets=(
            ComparisonTarget(kind="preset", name="mock"),
            ComparisonTarget(kind="preset", name="mock"),
        ),
        runs_dir=tmp_path,
        debate_rounds=0,
    )
    report, json_path, _ = run_comparison(request)
    assert len(report.entries) == 2
    assert all(entry.success for entry in report.entries)
    assert json_path.parent.name == report.comparison_id


def test_compare_captures_provider_failure_not_fatal(
    tmp_path: Path,
    real_settings_from_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    delete_keyring_secret("LLM_API_KEY")
    delete_keyring_secret("OPENAI_API_KEY")

    request = CompareRequest(
        question="Partial failure?",
        targets=(
            ComparisonTarget(kind="preset", name="mock"),
            ComparisonTarget(kind="preset", name="openrouter-sonnet"),
        ),
        runs_dir=tmp_path,
        debate_rounds=0,
    )
    report, json_path, md_path = run_comparison(request)
    assert json_path.exists()
    assert md_path.exists()
    mock_entry = report.entries[0]
    fail_entry = report.entries[1]
    assert mock_entry.success is True
    assert fail_entry.success is False
    assert fail_entry.error
    assert "LLM_API_KEY" in fail_entry.error or "Missing" in fail_entry.error
    assert_no_credential_leaks(json_path.read_text(encoding="utf-8"), [SECRET_VALUE])


def test_benchmark_alias_matches_compare(tmp_path: Path, capsys) -> None:
    code = main(
        [
            "benchmark",
            "Alias check?",
            "--presets",
            "mock",
            "--runs-dir",
            str(tmp_path),
            "--debate-rounds",
            "0",
            "-q",
        ]
    )
    assert code == 0
    assert list((tmp_path / "comparisons").iterdir())


def test_compare_profiles_from_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = init_config_file(tmp_path / "config.toml")
    from council.config_profiles import load_config_file

    monkeypatch.setattr("council.compare.load_config_file", lambda: load_config_file(config_path))
    request = CompareRequest(
        question="Profile compare?",
        targets=(ComparisonTarget(kind="profile", name="mock"),),
        runs_dir=tmp_path,
        debate_rounds=0,
    )
    report, _, _ = run_comparison(request)
    assert report.entries[0].success is True
    assert report.profiles_tested == ["mock"]


def test_comparison_artifacts_exclude_secrets(tmp_path: Path) -> None:
    set_keyring_secret("OPENAI_API_KEY", SECRET_VALUE)
    request = CompareRequest(
        question="No secret leak?",
        targets=(ComparisonTarget(kind="preset", name="mock"),),
        runs_dir=tmp_path,
        debate_rounds=0,
    )
    _, json_path, md_path = run_comparison(request)
    json_text = json_path.read_text(encoding="utf-8")
    md_text = md_path.read_text(encoding="utf-8")
    assert_no_credential_leaks(json_text, [SECRET_VALUE])
    assert_no_credential_leaks(md_text, [SECRET_VALUE])


def test_compare_all_failures_exit_code(
    tmp_path: Path,
    real_settings_from_env: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    code = main(
        [
            "compare",
            "All fail?",
            "--presets",
            "openai-mini",
            "--runs-dir",
            str(tmp_path),
            "--debate-rounds",
            "0",
            "-q",
        ]
    )
    assert code == 1
    comparison_dirs = list((tmp_path / "comparisons").iterdir())
    assert comparison_dirs
    payload = json.loads((comparison_dirs[0] / "comparison.json").read_text(encoding="utf-8"))
    assert payload["entries"][0]["success"] is False
