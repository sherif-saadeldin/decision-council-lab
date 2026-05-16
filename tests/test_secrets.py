from __future__ import annotations

import json
from pathlib import Path

import pytest

from council.config import Settings
from council.config_profiles import init_config_file, profile_display_rows
from council.doctor import run_doctor
from council.providers.errors import MissingProviderCredentialError
from council.providers.factory import create_provider
from council.redaction import assert_no_credential_leaks
from council.runtime import RuntimeOptions
from council.secrets import (
    UnknownSecretNameError,
    credential_source,
    delete_keyring_secret,
    get_keyring_secret,
    list_secret_statuses,
    resolve_secret_value,
    set_keyring_secret,
)
from main import main


SECRET_VALUE = "sk-test-keyring-only-value-xyz"


def test_set_get_list_delete_keyring_lifecycle() -> None:
    set_keyring_secret("OPENAI_API_KEY", SECRET_VALUE)
    assert get_keyring_secret("OPENAI_API_KEY") == SECRET_VALUE
    assert credential_source("OPENAI_API_KEY") == "keyring"

    statuses = dict(list_secret_statuses())
    assert statuses["OPENAI_API_KEY"] is True
    assert statuses["LLM_API_KEY"] is False

    delete_keyring_secret("OPENAI_API_KEY")
    assert get_keyring_secret("OPENAI_API_KEY") is None
    assert credential_source("OPENAI_API_KEY") == "missing"


def test_env_overrides_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    set_keyring_secret("OPENAI_API_KEY", SECRET_VALUE)
    monkeypatch.setenv("OPENAI_API_KEY", "env-wins-key")
    assert resolve_secret_value("OPENAI_API_KEY") == "env-wins-key"
    assert credential_source("OPENAI_API_KEY") == "env"


def test_settings_from_env_uses_keyring_when_env_empty(
    real_settings_from_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    set_keyring_secret("OPENAI_API_KEY", SECRET_VALUE)
    settings = Settings.from_env()
    assert settings.openai_api_key == SECRET_VALUE


def test_invalid_secret_name_raises() -> None:
    with pytest.raises(UnknownSecretNameError):
        set_keyring_secret("NOT_A_SECRET", "x")


def test_secrets_get_never_prints_value(capsys) -> None:
    set_keyring_secret("LLM_API_KEY", SECRET_VALUE)
    code = main(["secrets", "get", "LLM_API_KEY"])
    captured = capsys.readouterr()
    assert code == 0
    assert SECRET_VALUE not in captured.out
    assert "source: keyring" in captured.out


def test_secrets_list_reports_sources(capsys) -> None:
    set_keyring_secret("OPENAI_API_KEY", SECRET_VALUE)
    code = main(["secrets", "list"])
    captured = capsys.readouterr()
    assert code == 0
    assert SECRET_VALUE not in captured.out
    assert "OPENAI_API_KEY" in captured.out
    assert "keyring" in captured.out


def test_secrets_delete_cli(capsys) -> None:
    set_keyring_secret("OPENAI_API_KEY", SECRET_VALUE)
    code = main(["secrets", "delete", "OPENAI_API_KEY"])
    captured = capsys.readouterr()
    assert code == 0
    assert get_keyring_secret("OPENAI_API_KEY") is None
    assert SECRET_VALUE not in captured.out


def test_secrets_unknown_name_cli(capsys) -> None:
    code = main(["secrets", "get", "BAD_KEY"])
    captured = capsys.readouterr()
    assert code == 1
    assert "Unknown secret" in captured.err
    assert SECRET_VALUE not in captured.err


def test_missing_credential_when_env_and_keyring_empty(
    real_settings_from_env: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    delete_keyring_secret("OPENAI_API_KEY")
    settings = Settings(
        llm_mode="openai",
        runs_dir=tmp_path,
        mock_model="mock-council-v1",
        openai_api_key=None,
    )
    with pytest.raises(MissingProviderCredentialError) as exc_info:
        create_provider(settings, runtime=RuntimeOptions(show_progress=False))
    message = str(exc_info.value)
    assert "OPENAI_API_KEY" in message
    assert_no_credential_leaks(message, [SECRET_VALUE])


def test_doctor_reports_keyring_source_safely(
    real_settings_from_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_MODE", "openai_compatible")
    monkeypatch.setenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("LLM_MODEL", "anthropic/claude-sonnet-4.5")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    set_keyring_secret("LLM_API_KEY", SECRET_VALUE)
    settings = Settings.from_env()
    checks = run_doctor(settings, live=False)
    llm_check = next(c for c in checks if c.name == "LLM_API_KEY")
    assert llm_check.message == "source: keyring"
    assert SECRET_VALUE not in llm_check.message


def test_run_artifacts_exclude_keyring_secret(
    run_mock_council,
    mock_settings: Settings,
) -> None:
    set_keyring_secret("OPENAI_API_KEY", SECRET_VALUE)
    result = run_mock_council("Keyring leak check?")
    from council.storage import save_run

    json_path, _ = save_run(result, settings=mock_settings)
    blob = json_path.read_text(encoding="utf-8")
    assert SECRET_VALUE not in blob


def test_config_file_never_contains_keyring_secret(tmp_path: Path) -> None:
    from council.config_profiles import ConfigProfile

    set_keyring_secret("OPENAI_API_KEY", SECRET_VALUE)
    path = init_config_file(tmp_path / "config.toml")
    text = path.read_text(encoding="utf-8")
    assert SECRET_VALUE not in text
    display = dict(profile_display_rows(ConfigProfile(name="mock", mode="mock")))
    assert SECRET_VALUE not in json.dumps(display)
    assert "env or keyring" in display["OPENAI_API_KEY"]
