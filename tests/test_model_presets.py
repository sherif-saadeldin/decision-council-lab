from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from council.config import Settings
from council.model_presets import MODEL_PRESETS, apply_preset, list_preset_names
from council.providers.errors import UnknownModelPresetError
from council.providers.factory import create_provider
from main import main


EXPECTED_PRESETS = (
    "mock",
    "openai-mini",
    "openrouter-sonnet",
    "openrouter-gemini",
    "openrouter-deepseek",
    "openrouter-qwen",
)


def test_registry_contains_expected_presets() -> None:
    assert set(list_preset_names()) == set(EXPECTED_PRESETS)
    for name in EXPECTED_PRESETS:
        assert name in MODEL_PRESETS


def test_presets_do_not_store_api_keys() -> None:
    blob = json.dumps({name: preset.__dict__ for name, preset in MODEL_PRESETS.items()})
    assert "sk-" not in blob
    assert "OPENAI_API_KEY" not in blob
    assert "LLM_API_KEY" not in blob


def test_apply_mock_preset_overrides_mode() -> None:
    base = Settings(
        llm_mode="openai",
        runs_dir=Path("./runs"),
        mock_model="mock-council-v1",
        openai_api_key="should-not-use",
        openai_model="gpt-4.1-mini",
    )
    settings = apply_preset(base, "mock")
    assert settings.llm_mode == "mock"
    assert settings.mock_model == "mock-council-v1"


def test_apply_openrouter_preset_maps_compatible_fields() -> None:
    base = Settings.from_env()
    settings = apply_preset(base, "openrouter-sonnet")
    assert settings.llm_mode == "openai_compatible"
    assert settings.llm_provider_name == "openrouter"
    assert settings.llm_base_url == "https://openrouter.ai/api/v1"
    assert settings.llm_model == "anthropic/claude-sonnet-4.5"


def test_unknown_preset_raises_clean_error() -> None:
    with pytest.raises(UnknownModelPresetError, match="Unknown model preset"):
        apply_preset(Settings.from_env(), "not-a-real-preset")


def test_list_presets_main(capsys) -> None:
    code = main(["--list-presets"])
    captured = capsys.readouterr()
    assert code == 0
    for name in EXPECTED_PRESETS:
        assert name in captured.out
    assert "Provider" in captured.out or "openrouter" in captured.out


def test_unknown_preset_cli_no_traceback(capsys) -> None:
    code = main(["Test?", "--preset", "unknown-preset"])
    captured = capsys.readouterr()
    assert code == 1
    assert "Unknown model preset" in captured.err
    assert "Traceback" not in captured.err


def test_mock_preset_run_via_cli(capsys) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        code = main(["Preset mock test?", "--preset", "mock", "--runs-dir", tmp, "--quiet"])
    assert code == 0


def test_openrouter_preset_missing_llm_api_key_clean_error(capsys) -> None:
    env = {
        "LLM_MODE": "mock",
        "LLM_API_KEY": "",
        "OPENAI_API_KEY": "",
    }
    with patch.dict("os.environ", env, clear=False):
        code = main(["Test?", "--preset", "openrouter-sonnet", "--quiet"])

    captured = capsys.readouterr()
    assert code == 1
    assert "LLM_API_KEY" in captured.err
    assert "Traceback" not in captured.err


def test_openai_preset_missing_openai_api_key_clean_error(capsys) -> None:
    env = {
        "OPENAI_API_KEY": "",
        "LLM_API_KEY": "",
    }
    with patch.dict("os.environ", env, clear=False):
        code = main(["Test?", "--preset", "openai-mini", "--quiet"])

    captured = capsys.readouterr()
    assert code == 1
    assert "OPENAI_API_KEY" in captured.err
    assert "Traceback" not in captured.err


def test_openrouter_preset_provider_metadata() -> None:
    settings = apply_preset(Settings.from_env(), "openrouter-gemini")
    settings = Settings(
        llm_mode=settings.llm_mode,
        runs_dir=Path("./runs"),
        mock_model=settings.mock_model,
        llm_provider_name=settings.llm_provider_name,
        llm_base_url=settings.llm_base_url,
        llm_api_key="test-key",
        llm_model=settings.llm_model,
    )
    provider = create_provider(settings)
    assert provider.metadata.provider_name == "openrouter"
    assert provider.metadata.mode == "openai_compatible"
    assert "gemini" in provider.metadata.model_name.lower()
