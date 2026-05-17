from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import tomllib

from council.cli import (
    parse_args,
    resolve_debate_rounds,
    resolve_runtime_options,
    resolve_settings,
)
from council.config import Settings
from council.config_profiles import (
    CONFIG_BACKUP_SUFFIX,
    ConfigProfileError,
    CorruptConfigFileError,
    UnknownConfigProfileError,
    init_config_file,
    load_config_file,
    profile_display_rows,
    set_active_profile,
    validate_config_file,
)
from main import main


def test_config_init_creates_file(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    created = init_config_file(config_path)
    assert created.exists()
    raw = tomllib.loads(created.read_text(encoding="utf-8"))
    assert "profiles" in raw
    assert raw["active_profile"] == "mock"
    text = created.read_text(encoding="utf-8")
    assert "api_key" not in text.lower()
    assert "OPENAI_API_KEY" not in text


def test_config_init_rejects_secrets_in_custom_write(tmp_path: Path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text('[profiles.x]\nopenai_api_key = "nope"\n', encoding="utf-8")
    with pytest.raises(ConfigProfileError, match="not allowed"):
        load_config_file(bad)


def test_config_list_shows_profiles(tmp_path: Path, capsys) -> None:
    init_config_file(tmp_path / "config.toml")
    with patch("council.cli.load_config_file", lambda: load_config_file(tmp_path / "config.toml")):
        from council.cli import render_config_list
        from rich.console import Console

        render_config_list(Console())
    captured = capsys.readouterr()
    assert "mock" in captured.out
    assert "ollama-local" in captured.out


def test_config_show_never_prints_secrets(tmp_path: Path, capsys) -> None:
    path = init_config_file(tmp_path / "config.toml")
    with patch("council.cli.load_config_file", lambda: load_config_file(path)):
        from council.cli import render_config_show
        from rich.console import Console

        render_config_show(Console(), "mock")
    captured = capsys.readouterr()
    assert "env or keyring" in captured.out
    assert "sk-" not in captured.out


def test_config_use_changes_active_profile(tmp_path: Path) -> None:
    path = init_config_file(tmp_path / "config.toml")
    set_active_profile("ollama-local", path)
    config = load_config_file(path)
    assert config is not None
    assert config.active_profile == "ollama-local"
    assert 'active_profile = "ollama-local"' in path.read_text(encoding="utf-8")


def test_config_use_does_not_duplicate_active_profile_line(tmp_path: Path) -> None:
    """Regression guard for the 'duplicate active_profile' corruption bug."""
    path = init_config_file(tmp_path / "config.toml")
    set_active_profile("ollama-local", path)
    set_active_profile("mock", path)
    set_active_profile("openai-mini", path)
    text = path.read_text(encoding="utf-8")
    occurrences = [line for line in text.splitlines() if line.startswith("active_profile")]
    assert len(occurrences) == 1, occurrences
    # The file must still parse cleanly via the safe loader.
    config = load_config_file(path)
    assert config is not None
    assert config.active_profile == "openai-mini"


def test_config_use_creates_backup(tmp_path: Path) -> None:
    path = init_config_file(tmp_path / "config.toml")
    before = path.read_text(encoding="utf-8")
    set_active_profile("ollama-local", path)
    backup = path.with_suffix(path.suffix + CONFIG_BACKUP_SUFFIX)
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == before


def test_load_config_rejects_invalid_toml(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("not = valid = toml\n[unterminated\n", encoding="utf-8")
    with pytest.raises(CorruptConfigFileError) as excinfo:
        load_config_file(path)
    message = str(excinfo.value)
    assert "config init" in message
    assert str(path) in message


def test_load_config_wraps_tomllib_duplicate_key_error(tmp_path: Path) -> None:
    """Reproduces the user-reported 'Cannot overwrite a value' corruption."""
    path = tmp_path / "config.toml"
    path.write_text(
        'active_profile = "mock"\n'
        'active_profile = "other"\n'
        "[profiles.mock]\nmode = \"mock\"\nprovider_name = \"mock\"\n"
        "model = \"mock-council-v1\"\n",
        encoding="utf-8",
    )
    with pytest.raises(CorruptConfigFileError) as excinfo:
        load_config_file(path)
    message = str(excinfo.value)
    # Raw TOML error is wrapped with a recovery hint.
    assert "config init" in message
    assert str(path) in message


def test_validate_config_file_reports_corruption(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("oops not = toml at all\n[\n", encoding="utf-8")
    ok, message = validate_config_file(path)
    assert ok is False
    assert message and "config init" in message


def test_validate_config_file_ok_for_fresh_init(tmp_path: Path) -> None:
    path = init_config_file(tmp_path / "config.toml")
    ok, message = validate_config_file(path)
    assert ok is True
    assert message is None


def test_save_config_file_is_atomic_no_partial_file(tmp_path: Path) -> None:
    """If a write fails mid-stream, the previous file must remain readable."""
    path = init_config_file(tmp_path / "config.toml")
    original = path.read_text(encoding="utf-8")
    from council.config_profiles import save_config_file

    profiles = load_config_file(path).profiles  # type: ignore[union-attr]
    with patch("council.config_profiles._atomic_write_text", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            save_config_file("ollama-local", dict(profiles), path)
    # The original file must be intact.
    assert path.read_text(encoding="utf-8") == original


def test_load_config_surfaces_friendly_error_for_chat_callers(tmp_path: Path) -> None:
    """`build_chat_context`-style callers must see ConfigProfileError, not TOMLDecodeError."""
    path = tmp_path / "config.toml"
    path.write_text('active_profile = "mock"\n[profiles.x\nmode = "mock"\n', encoding="utf-8")
    with pytest.raises(ConfigProfileError):
        load_config_file(path)


def test_run_profile_applies_values(tmp_path: Path) -> None:
    path = init_config_file(tmp_path / "config.toml")
    config = load_config_file(path)
    assert config is not None
    profile = config.get_profile("ollama-local")

    base = Settings.from_env()
    from council.config_profiles import resolve_settings_with_profile

    settings = resolve_settings_with_profile(base, profile=profile, cli_preset=None)
    assert settings.llm_mode == "openai_compatible"
    assert settings.llm_provider_name == "ollama"
    assert settings.llm_model == "qwen3.5:9b"


def test_preset_overrides_profile_routing(tmp_path: Path) -> None:
    path = init_config_file(tmp_path / "config.toml")
    config = load_config_file(path)
    profile = config.get_profile("ollama-local")

    base = Settings(
        llm_mode="mock",
        runs_dir=Path("./runs"),
        mock_model="mock-council-v1",
    )
    from council.config_profiles import resolve_settings_with_profile

    settings = resolve_settings_with_profile(
        base,
        profile=profile,
        cli_preset="mock",
    )
    assert settings.llm_mode == "mock"


def test_cli_flags_override_profile_runtime(tmp_path: Path) -> None:
    path = init_config_file(tmp_path / "config.toml")
    with patch("council.cli.load_config_file", lambda: load_config_file(path)):
        args = parse_args(
            [
                "run",
                "Q",
                "--profile",
                "ollama-local",
                "--timeout-seconds",
                "45",
                "--max-retries",
                "3",
                "--quiet",
            ]
        )
        runtime = resolve_runtime_options(args)
    assert runtime.timeout_seconds == 45.0
    assert runtime.max_retries == 3


def test_doctor_profile_applies(tmp_path: Path, capsys) -> None:
    path = init_config_file(tmp_path / "config.toml")
    with patch("council.config_profiles.DEFAULT_CONFIG_PATH", path):
        code = main(["doctor", "--profile", "mock"])
    assert code == 0


def test_main_config_init_and_list(tmp_path: Path, capsys) -> None:
    with patch("council.config_profiles.DEFAULT_CONFIG_PATH", tmp_path / "config.toml"):
        assert main(["config", "init"]) == 0
        code = main(["config", "list"])
    assert code == 0
    assert "mock" in capsys.readouterr().out


def test_run_with_profile_flag(tmp_path: Path, capsys) -> None:
    path = init_config_file(tmp_path / "config.toml")
    with patch("council.config_profiles.DEFAULT_CONFIG_PATH", path):
        code = main(
            [
                "run",
                "Profile run?",
                "--profile",
                "mock",
                "--runs-dir",
                str(tmp_path / "runs"),
                "--debate-rounds",
                "0",
                "--quiet",
            ]
        )
    assert code == 0


def test_unknown_profile_raises(tmp_path: Path) -> None:
    path = init_config_file(tmp_path / "config.toml")
    config = load_config_file(path)
    assert config is not None
    with pytest.raises(UnknownConfigProfileError):
        config.get_profile("not-a-profile")


def test_profile_display_rows_redact_secrets() -> None:
    from council.config_profiles import ConfigProfile

    rows = dict(profile_display_rows(ConfigProfile(name="x", mode="mock")))
    assert rows["OPENAI_API_KEY"] == "env or keyring (not in config)"
    assert rows["LLM_API_KEY"] == "env or keyring (not in config)"


def test_resolve_settings_integration(tmp_path: Path) -> None:
    path = init_config_file(tmp_path / "config.toml")
    with patch("council.cli.load_config_file", lambda: load_config_file(path)):
        args = parse_args(["run", "Q?", "--profile", "ollama-local", "--runs-dir", str(tmp_path)])
        settings = resolve_settings(args)
    assert settings.llm_provider_name == "ollama"


def test_fast_cli_overrides_profile_debate(tmp_path: Path) -> None:
    path = init_config_file(tmp_path / "config.toml")
    with patch("council.cli.load_config_file", lambda: load_config_file(path)):
        args = parse_args(
            [
                "run",
                "Fast?",
                "--profile",
                "mock",
                "--fast",
                "--runs-dir",
                str(tmp_path),
            ]
        )
        runtime = resolve_runtime_options(args)
        rounds = resolve_debate_rounds(args, runtime)
    assert runtime.fast_mode is True
    assert rounds == 0
