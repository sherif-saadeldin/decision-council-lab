from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from council.cli import parse_args
from council.config_profiles import init_config_file, load_config_file
from council.setup import (
    SUPPORTED_NON_INTERACTIVE_PROFILES,
    ScriptedPrompter,
    run_setup,
)
from council.smoke import DEFAULT_SMOKE_QUESTION, SmokeReport
from main import main


def _noop_doctor(*_a: object, **_k: object) -> list[object]:
    return []


def _success_smoke(request: object, **_k: object) -> SmokeReport:
    preset = getattr(request, "preset", "mock")
    return SmokeReport(
        success=True,
        preset=preset,
        question=DEFAULT_SMOKE_QUESTION,
        provider_name="mock",
        model_name="mock-council-v1",
        elapsed_seconds=0.01,
        run_id="setup-test",
        run_json_path="runs/setup-test/run.json",
        run_md_path="runs/setup-test/run.md",
        decision_type="proceed",
        confidence_score=0.5,
    )


def test_setup_parser_exists() -> None:
    args = parse_args(["setup", "--non-interactive", "--profile", "mock"])
    assert args.command == "setup"
    assert args.non_interactive is True
    assert args.profile == "mock"


def test_setup_non_interactive_requires_profile() -> None:
    result = run_setup(
        interactive=False,
        profile_name=None,
        config_path_override=Path(".dcouncil/config.toml"),
        doctor_fn=_noop_doctor,
        smoke_fn=_success_smoke,
    )
    assert result.exit_code == 2
    assert result.message
    assert "--profile" in result.message


def test_setup_non_interactive_unsupported_profile_message() -> None:
    result = run_setup(
        interactive=False,
        profile_name="not-a-profile",
        config_path_override=Path(".dcouncil/config.toml"),
        doctor_fn=_noop_doctor,
        smoke_fn=_success_smoke,
    )
    assert result.exit_code == 2
    assert "Unsupported profile" in (result.message or "")


def test_setup_cancel_exits_cleanly(tmp_path: Path) -> None:
    config_file = tmp_path / ".dcouncil" / "config.toml"
    prompter = ScriptedPrompter(script=[0])
    result = run_setup(
        interactive=True,
        prompter=prompter,
        config_path_override=config_file,
        doctor_fn=_noop_doctor,
        smoke_fn=_success_smoke,
    )
    assert result.cancelled is True
    assert result.exit_code == 0
    assert not config_file.exists()


def test_mock_non_interactive_setup_creates_config_without_secrets(tmp_path: Path) -> None:
    config_file = tmp_path / ".dcouncil" / "config.toml"
    result = run_setup(
        interactive=False,
        profile_name="mock",
        config_path_override=config_file,
        doctor_fn=_noop_doctor,
        smoke_fn=_success_smoke,
    )
    assert result.exit_code == 0
    assert config_file.exists()
    text = config_file.read_text(encoding="utf-8")
    assert 'active_profile = "mock"' in text
    assert "[profiles.mock]" in text
    assert "sk-" not in text
    assert "OPENAI_API_KEY =" not in text
    assert "LLM_API_KEY =" not in text
    raw = tomllib.loads(text)
    assert raw["profiles"]["mock"]["mode"] == "mock"


def test_ollama_non_interactive_setup_without_key_in_config(tmp_path: Path) -> None:
    config_file = tmp_path / ".dcouncil" / "config.toml"
    result = run_setup(
        interactive=False,
        profile_name="ollama-local",
        config_path_override=config_file,
        doctor_fn=_noop_doctor,
        smoke_fn=_success_smoke,
    )
    assert result.exit_code == 0
    raw = tomllib.loads(config_file.read_text(encoding="utf-8"))
    assert "llm_api_key" not in raw["profiles"]["ollama-local"]
    profile = raw["profiles"]["ollama-local"]
    assert profile["provider_name"] == "ollama"
    assert profile["model"] == "qwen3.5:9b"


def test_hosted_non_interactive_does_not_write_secrets_to_config(tmp_path: Path) -> None:
    config_file = tmp_path / ".dcouncil" / "config.toml"
    result = run_setup(
        interactive=False,
        profile_name="openrouter-sonnet",
        config_path_override=config_file,
        doctor_fn=_noop_doctor,
        smoke_fn=_success_smoke,
    )
    assert result.exit_code == 0
    text = config_file.read_text(encoding="utf-8")
    assert "sk-test-secret-value" not in text
    assert "LLM_API_KEY =" not in text
    raw = tomllib.loads(text)
    assert raw["profiles"]["openrouter-sonnet"]["preset"] == "openrouter-sonnet"


def test_setup_preserves_existing_profiles(tmp_path: Path) -> None:
    config_file = tmp_path / ".dcouncil" / "config.toml"
    init_config_file(config_file)
    result = run_setup(
        interactive=False,
        profile_name="openai-mini",
        config_path_override=config_file,
        doctor_fn=_noop_doctor,
        smoke_fn=_success_smoke,
    )
    assert result.exit_code == 0
    config = load_config_file(config_file)
    assert config is not None
    assert "mock" in config.profile_names()
    assert "ollama-local" in config.profile_names()
    assert "openai-mini" in config.profile_names()
    assert config.active_profile == "openai-mini"


def test_hosted_setup_stores_secret_via_callback_not_config(tmp_path: Path) -> None:
    config_file = tmp_path / ".dcouncil" / "config.toml"
    stored: dict[str, str] = {}

    def capture_store(name: str, value: str) -> None:
        stored[name] = value

    prompter = ScriptedPrompter(
        script=[
            4,
            1,
            "openrouter-test",
            True,
            True,
            False,
        ]
    )

    result = run_setup(
        interactive=True,
        prompter=prompter,
        config_path_override=config_file,
        store_secret_fn=capture_store,
        secret_prompt_fn=lambda _label: "sk-test-hosted-key-not-real",
        doctor_fn=_noop_doctor,
        smoke_fn=_success_smoke,
    )
    assert result.exit_code == 0
    assert stored.get("LLM_API_KEY") == "sk-test-hosted-key-not-real"
    text = config_file.read_text(encoding="utf-8")
    assert "sk-test-hosted-key-not-real" not in text


def test_main_setup_mock_non_interactive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_file = tmp_path / ".dcouncil" / "config.toml"
    monkeypatch.chdir(tmp_path)
    with patch("main.run_doctor", _noop_doctor), patch("main.run_smoke", _success_smoke):
        code = main(["setup", "--non-interactive", "--profile", "mock"])
    assert code == 0
    assert config_file.exists()


@pytest.mark.parametrize("profile_name", sorted(SUPPORTED_NON_INTERACTIVE_PROFILES))
def test_supported_non_interactive_profiles(profile_name: str, tmp_path: Path) -> None:
    config_file = tmp_path / ".dcouncil" / "config.toml"
    result = run_setup(
        interactive=False,
        profile_name=profile_name,
        config_path_override=config_file,
        doctor_fn=_noop_doctor,
        smoke_fn=_success_smoke,
    )
    assert result.exit_code == 0
    config = load_config_file(config_file)
    assert config is not None
    assert config.active_profile == profile_name
