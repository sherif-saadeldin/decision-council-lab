from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from openai import APITimeoutError

from council.cli import (
    normalize_argv,
    parse_args,
    resolve_debate_rounds,
    resolve_runtime_options,
)
from council.config import Settings
from council.doctor import CheckStatus, resolve_doctor_settings, run_doctor
from council.engine import run_council
from council.providers.openai_errors import safe_compatible_error_message, timeout_message
from council.runtime import FAST_DEBATE_ROUNDS, RuntimeOptions
from main import main


def test_normalize_argv_legacy_question() -> None:
    assert normalize_argv(["Should we ship?"]) == ["run", "Should we ship?"]


def test_normalize_argv_list_presets() -> None:
    assert normalize_argv(["--list-presets"]) == ["presets"]


def test_parse_args_run_subcommand() -> None:
    args = parse_args(["run", "What should we do?"])
    assert args.command == "run"
    assert args.question == "What should we do?"


def test_fast_mode_runtime_options() -> None:
    args = parse_args(["run", "Fast?", "--fast", "--debate-rounds", "2"])
    runtime = resolve_runtime_options(args)
    assert runtime.fast_mode is True
    assert resolve_debate_rounds(args, runtime) == FAST_DEBATE_ROUNDS


def test_timeout_passed_to_runtime() -> None:
    args = parse_args(["run", "Timeout?", "--timeout-seconds", "45", "--max-retries", "2"])
    runtime = resolve_runtime_options(args)
    assert runtime.timeout_seconds == 45.0
    assert runtime.max_retries == 2


def test_quiet_disables_progress() -> None:
    args = parse_args(["run", "Quiet?", "--quiet"])
    runtime = resolve_runtime_options(args)
    assert runtime.show_progress is False


def test_main_presets_command(capsys) -> None:
    code = main(["presets"])
    captured = capsys.readouterr()
    assert code == 0
    assert "Model presets" in captured.out
    assert "mock" in captured.out


def test_main_version_command(capsys) -> None:
    code = main(["version"])
    captured = capsys.readouterr()
    assert code == 0
    assert "1.10" in captured.out


def test_main_doctor_mock_mode(capsys) -> None:
    code = main(["doctor", "--preset", "mock"])
    captured = capsys.readouterr()
    assert code == 0
    assert "mock" in captured.out.lower()


def test_doctor_openrouter_missing_key_reports_safely(real_settings_from_env: None) -> None:
    with patch.dict(
        "os.environ",
        {"LLM_API_KEY": "", "OPENAI_API_KEY": "", "LLM_MODE": "mock"},
        clear=False,
    ):
        settings = resolve_doctor_settings(preset="openrouter-sonnet")
        checks = run_doctor(settings, live=False)
    llm_check = next(c for c in checks if c.name == "LLM_API_KEY")
    assert llm_check.status == CheckStatus.FAIL
    assert "source: missing" in llm_check.message
    assert "sk-" not in llm_check.message


def test_doctor_openrouter_missing_key_via_cli(capsys, real_settings_from_env: None) -> None:
    with patch.dict("os.environ", {"LLM_API_KEY": "", "OPENAI_API_KEY": ""}, clear=False):
        code = main(["doctor", "--preset", "openrouter-sonnet"])
    captured = capsys.readouterr()
    assert code == 1
    assert "LLM_API_KEY" in captured.out
    assert "sk-" not in captured.out


def test_legacy_list_presets_still_works(capsys) -> None:
    code = main(["--list-presets"])
    captured = capsys.readouterr()
    assert code == 0
    assert "Model presets" in captured.out


def test_quiet_suppresses_progress(capsys) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        code = main(["Quiet progress?", "--runs-dir", tmp, "--quiet"])
    captured = capsys.readouterr()
    assert code == 0
    assert "> context" not in captured.out.lower()


def test_progress_shown_when_not_quiet(capsys) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        code = main(["Progress?", "--runs-dir", tmp, "--debate-rounds", "0"])
    captured = capsys.readouterr()
    assert code == 0
    assert "> context" in captured.out.lower()
    assert "> chair" in captured.out.lower()


def test_fast_mode_skips_debate_in_run(mock_settings: Settings) -> None:
    runtime = RuntimeOptions(fast_mode=True, show_progress=False)
    result, _ = run_council(
        "Fast run?",
        settings=mock_settings,
        debate_rounds=2,
        runtime=runtime,
    )
    assert result.debate_transcript is None


def test_timeout_config_reaches_provider(tmp_path: Path) -> None:
    from council.providers.factory import create_provider

    settings = Settings(
        llm_mode="openai",
        runs_dir=tmp_path,
        mock_model="mock-council-v1",
        openai_api_key="test-key",
        openai_model="gpt-4.1-mini",
    )
    runtime = RuntimeOptions(timeout_seconds=99.0, max_retries=1, show_progress=False)
    provider = create_provider(settings, runtime=runtime)
    assert provider._timeout_seconds == 99.0  # type: ignore[attr-defined]
    assert provider._max_retries == 1  # type: ignore[attr-defined]


def test_timeout_message_format() -> None:
    assert timeout_message(30.0) == "Provider request timed out after 30 seconds."


def test_timeout_error_mapping() -> None:
    message = safe_compatible_error_message(
        APITimeoutError(request=None),  # type: ignore[arg-type]
        provider_name="ollama",
        credential_env="LLM_API_KEY",
        timeout_seconds=30.0,
    )
    assert message == "Provider request timed out after 30 seconds."


def test_doctor_ollama_uses_tags_without_network() -> None:
    settings = resolve_doctor_settings(preset="ollama-qwen")

    def fake_tags(url: str, timeout: float) -> tuple[bool, list[str] | None, str]:
        assert "/api/tags" in url
        assert "localhost" in url
        return True, ["qwen3.5:9b"], "Found 1 installed model(s)"

    checks = run_doctor(settings, tags_fetcher=fake_tags)
    ollama = next(c for c in checks if c.name == "ollama")
    model = next(c for c in checks if c.name == "ollama_model")
    assert ollama.status == CheckStatus.OK
    assert model.status == CheckStatus.OK
