from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from council.config import Settings
from council.model_presets import apply_preset
from council.models import DEFAULT_DEBATE_ROUNDS
from council.providers.api_mode import normalize_api_mode
from council.runtime import DEFAULT_TIMEOUT_SECONDS, RuntimeOptions

DEFAULT_CONFIG_PATH = Path(".dcouncil/config.toml")

SECRET_FIELD_NAMES = frozenset(
    {
        "api_key",
        "openai_api_key",
        "llm_api_key",
        "secret",
        "token",
        "password",
        "credential",
    }
)

SAMPLE_CONFIG_TOML = """\
# Decision Council local config (no secrets — API keys via env or keyring only)
active_profile = "mock"

[profiles.mock]
mode = "mock"
provider_name = "mock"
model = "mock-council-v1"
debate_rounds = 2

[profiles.openai-mini]
preset = "openai-mini"
timeout_seconds = 120
debate_rounds = 2

[profiles.ollama-local]
mode = "openai_compatible"
provider_name = "ollama"
base_url = "http://localhost:11434/v1"
model = "qwen3.5:9b"
timeout_seconds = 180
max_retries = 0
debate_rounds = 1
"""


class ConfigProfileError(ValueError):
    """Raised for invalid or missing config profile operations."""


class UnknownConfigProfileError(ConfigProfileError):
    def __init__(self, profile_name: str, available: tuple[str, ...]) -> None:
        self.profile_name = profile_name
        self.available_profiles = available
        available_text = ", ".join(available) or "(none)"
        super().__init__(f"Unknown config profile {profile_name!r}. Available: {available_text}.")


@dataclass(frozen=True)
class ConfigProfile:
    name: str
    mode: str | None = None
    provider_name: str | None = None
    base_url: str | None = None
    model: str | None = None
    preset: str | None = None
    timeout_seconds: float | None = None
    max_retries: int | None = None
    fast: bool | None = None
    debate_rounds: int | None = None


@dataclass(frozen=True)
class CouncilConfigFile:
    path: Path
    active_profile: str
    profiles: dict[str, ConfigProfile]

    def profile_names(self) -> list[str]:
        return sorted(self.profiles.keys())

    def get_profile(self, name: str) -> ConfigProfile:
        if name not in self.profiles:
            raise UnknownConfigProfileError(name, tuple(self.profile_names()))
        return self.profiles[name]


def config_path(path: Path | None = None) -> Path:
    return path or DEFAULT_CONFIG_PATH


def init_config_file(path: Path | None = None, *, force: bool = False) -> Path:
    target = config_path(path)
    if target.exists() and not force:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(SAMPLE_CONFIG_TOML, encoding="utf-8")
    _validate_no_secrets_in_file(target)
    return target


def load_config_file(path: Path | None = None) -> CouncilConfigFile | None:
    target = config_path(path)
    if not target.exists():
        return None
    raw = tomllib.loads(target.read_text(encoding="utf-8"))
    _reject_secret_fields(raw, source=str(target))
    active = str(raw.get("active_profile", "")).strip() or "mock"
    profiles_block = raw.get("profiles")
    if not isinstance(profiles_block, dict):
        profiles_block = _profiles_from_dotted_keys(raw)
    profiles: dict[str, ConfigProfile] = {}
    for name, data in profiles_block.items():
        if not isinstance(data, dict):
            msg = f"Profile {name!r} must be a table."
            raise ConfigProfileError(msg)
        profiles[name] = _profile_from_dict(str(name), data)
    if not profiles:
        msg = f"No profiles defined in {target}."
        raise ConfigProfileError(msg)
    if active not in profiles:
        raise UnknownConfigProfileError(active, tuple(sorted(profiles.keys())))
    return CouncilConfigFile(path=target, active_profile=active, profiles=profiles)


def set_active_profile(profile_name: str, path: Path | None = None) -> Path:
    target = config_path(path)
    if not target.exists():
        msg = f"Config file not found: {target}. Run: python main.py config init"
        raise ConfigProfileError(msg)
    config = load_config_file(target)
    if config is None:
        raise ConfigProfileError(f"Could not load config: {target}")
    config.get_profile(profile_name)
    text = target.read_text(encoding="utf-8")
    updated = re.sub(
        r'^active_profile\s*=\s*".*?"',
        f'active_profile = "{profile_name}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if updated == text:
        updated = f'active_profile = "{profile_name}"\n' + text
    target.write_text(updated, encoding="utf-8")
    return target


def resolve_profile_name(
    *,
    cli_profile: str | None = None,
    config: CouncilConfigFile | None = None,
) -> str | None:
    if cli_profile:
        return cli_profile
    if config is not None:
        return config.active_profile
    loaded = load_config_file()
    if loaded is not None:
        return loaded.active_profile
    return None


def apply_profile_to_settings(settings: Settings, profile: ConfigProfile) -> Settings:
    updates: dict[str, Any] = {}
    if profile.mode is not None:
        updates["llm_mode"] = profile.mode.lower()
    if profile.provider_name is not None:
        updates["llm_provider_name"] = profile.provider_name
    if profile.base_url is not None:
        updates["llm_base_url"] = profile.base_url
    if profile.model is not None:
        mode = (profile.mode or settings.llm_mode).lower()
        if mode == "mock":
            updates["mock_model"] = profile.model
        elif mode == "openai":
            updates["openai_model"] = profile.model
        else:
            updates["llm_model"] = profile.model
    return replace(settings, **updates)


def apply_profile_to_runtime(profile: ConfigProfile, runtime: RuntimeOptions) -> RuntimeOptions:
    return replace(
        runtime,
        timeout_seconds=(
            profile.timeout_seconds
            if profile.timeout_seconds is not None
            else runtime.timeout_seconds
        ),
        max_retries=profile.max_retries if profile.max_retries is not None else runtime.max_retries,
        fast_mode=profile.fast if profile.fast is not None else runtime.fast_mode,
    )


def resolve_settings_with_profile(
    base: Settings,
    *,
    profile: ConfigProfile | None,
    cli_preset: str | None,
) -> Settings:
    settings = base
    if profile is not None:
        settings = apply_profile_to_settings(settings, profile)
    if cli_preset:
        settings = apply_preset(settings, cli_preset)
    elif profile is not None and profile.preset:
        settings = apply_preset(settings, profile.preset)
    return settings


def resolve_runtime_with_profile(
    profile: ConfigProfile | None,
    *,
    cli_timeout: float | None,
    cli_max_retries: int | None,
    cli_fast: bool,
    cli_fast_explicit: bool,
    quiet: bool,
    cli_repair_json: bool = False,
    cli_api_mode: str = "auto",
) -> RuntimeOptions:
    runtime = RuntimeOptions()
    if profile is not None:
        runtime = apply_profile_to_runtime(profile, runtime)
    timeout = (
        cli_timeout if cli_timeout is not None else runtime.timeout_seconds or DEFAULT_TIMEOUT_SECONDS
    )
    max_retries = cli_max_retries if cli_max_retries is not None else runtime.max_retries
    fast_mode = cli_fast if cli_fast_explicit else runtime.fast_mode
    return RuntimeOptions(
        timeout_seconds=timeout,
        max_retries=max(0, max_retries),
        fast_mode=fast_mode,
        show_progress=not quiet,
        repair_json=cli_repair_json,
        api_mode=normalize_api_mode(cli_api_mode),
    )


def resolve_debate_rounds_with_profile(
    profile: ConfigProfile | None,
    *,
    cli_debate_rounds: int | None,
    runtime: RuntimeOptions,
) -> int:
    if runtime.fast_mode:
        from council.runtime import FAST_DEBATE_ROUNDS

        return FAST_DEBATE_ROUNDS
    if cli_debate_rounds is not None:
        return max(0, cli_debate_rounds)
    if profile is not None and profile.debate_rounds is not None:
        return max(0, profile.debate_rounds)
    return DEFAULT_DEBATE_ROUNDS


def profile_display_rows(profile: ConfigProfile) -> list[tuple[str, str]]:
    rows = [
        ("mode", profile.mode or "—"),
        ("provider_name", profile.provider_name or "—"),
        ("base_url", profile.base_url or "—"),
        ("model", profile.model or "—"),
        ("preset", profile.preset or "—"),
        (
            "timeout_seconds",
            str(profile.timeout_seconds) if profile.timeout_seconds is not None else "—",
        ),
        ("max_retries", str(profile.max_retries) if profile.max_retries is not None else "—"),
        ("fast", str(profile.fast) if profile.fast is not None else "—"),
        (
            "debate_rounds",
            str(profile.debate_rounds) if profile.debate_rounds is not None else "—",
        ),
        ("OPENAI_API_KEY", "env or keyring (not in config)"),
        ("LLM_API_KEY", "env or keyring (not in config)"),
    ]
    return rows


def _profiles_from_dotted_keys(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    profiles: dict[str, dict[str, Any]] = {}
    pattern = re.compile(r"^profiles\.([A-Za-z0-9_-]+)$")
    for key, value in raw.items():
        match = pattern.match(key)
        if match and isinstance(value, dict):
            profiles[match.group(1)] = value
    return profiles


def _profile_from_dict(name: str, data: dict[str, Any]) -> ConfigProfile:
    _reject_secret_fields(data, source=f"profile {name!r}")
    return ConfigProfile(
        name=name,
        mode=_optional_str(data.get("mode")),
        provider_name=_optional_str(data.get("provider_name")),
        base_url=_optional_str(data.get("base_url")),
        model=_optional_str(data.get("model")),
        preset=_optional_str(data.get("preset")),
        timeout_seconds=_optional_float(data.get("timeout_seconds")),
        max_retries=_optional_int(data.get("max_retries")),
        fast=_optional_bool(data.get("fast")),
        debate_rounds=_optional_int(data.get("debate_rounds")),
    )


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return int(str(value))


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return float(str(value))


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    return bool(value)


def _reject_secret_fields(data: dict[str, Any], *, source: str) -> None:
    for key in data:
        normalized = key.lower().replace("-", "_")
        if normalized in SECRET_FIELD_NAMES or normalized.endswith("_key") or "secret" in normalized:
            msg = (
                f"Secret field {key!r} is not allowed in {source}. "
                "Use environment variables or `secrets set`."
            )
            raise ConfigProfileError(msg)


def _validate_no_secrets_in_file(path: Path) -> None:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    _reject_secret_fields(raw, source=str(path))
    profiles = raw.get("profiles")
    if isinstance(profiles, dict):
        for name, block in profiles.items():
            if isinstance(block, dict):
                _reject_secret_fields(block, source=f"profile {name!r}")
