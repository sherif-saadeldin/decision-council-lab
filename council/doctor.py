from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from council.config import Settings
from council.credentials import is_ollama_openai_compatible
from council.model_presets import OLLAMA_BASE_URL, list_preset_names
from council.ollama_probe import (
    TagsFetcher,
    default_tags_fetcher,
    format_missing_model_message,
    model_is_installed,
    ollama_tags_url,
)
from council.providers.api_mode import CHAT_PREFERRED_PROVIDERS, resolve_effective_api_mode
from council.secrets import credential_source
from council.models import RUN_SCHEMA_VERSION
from council.providers.factory import SUPPORTED_LLM_MODES, create_provider
from council.runtime import RuntimeOptions
from council.version import APP_VERSION


class CheckStatus(str, Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: CheckStatus
    message: str


def resolve_doctor_settings(
    *,
    preset: str | None = None,
    profile_name: str | None = None,
) -> Settings:
    from council.config_profiles import (
        load_config_file,
        resolve_profile_name,
        resolve_settings_with_profile,
    )

    settings = Settings.from_env()
    config = load_config_file()
    name = resolve_profile_name(cli_profile=profile_name, config=config)
    profile = config.get_profile(name) if name and config else None
    return resolve_settings_with_profile(settings, profile=profile, cli_preset=preset)


def run_doctor(
    settings: Settings,
    *,
    live: bool = False,
    http_probe: Callable[[str, float], tuple[bool, str]] | None = None,
    tags_fetcher: TagsFetcher | None = None,
    runtime: RuntimeOptions | None = None,
) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    probe = http_probe or _default_http_probe
    fetch_tags = tags_fetcher or default_tags_fetcher

    checks.append(
        DoctorCheck(
            name="app",
            status=CheckStatus.OK,
            message=f"decision-council-lab {APP_VERSION}, schema {RUN_SCHEMA_VERSION}",
        )
    )
    checks.append(
        DoctorCheck(
            name="mode",
            status=CheckStatus.OK if settings.llm_mode in SUPPORTED_LLM_MODES else CheckStatus.FAIL,
            message=f"LLM_MODE={settings.llm_mode!r}",
        )
    )
    checks.append(
        DoctorCheck(
            name="provider",
            status=CheckStatus.OK,
            message=(
                f"provider={settings.llm_provider_name!r}, "
                f"model={settings.openai_model if settings.llm_mode == 'openai' else settings.llm_model or settings.mock_model!r}"
            ),
        )
    )

    checks.extend(_credential_checks(settings))
    checks.append(_api_mode_check(settings, runtime))

    if settings.llm_mode == "openai_compatible" and settings.llm_provider_name == "ollama":
        checks.extend(_ollama_checks(settings, fetch_tags))
    elif settings.llm_mode == "openai_compatible" and settings.llm_base_url:
        models_url = settings.llm_base_url.rstrip("/") + "/models"
        ok, detail = probe(models_url, 5.0)
        checks.append(
            DoctorCheck(
                name="base_url",
                status=CheckStatus.OK if ok else CheckStatus.WARN,
                message=(
                    f"Reachable ({settings.llm_base_url})"
                    if ok
                    else f"Could not reach {settings.llm_base_url}: {detail}"
                ),
            )
        )

    if live:
        checks.append(_live_provider_check(settings, runtime=runtime))
    else:
        checks.append(
            DoctorCheck(
                name="live",
                status=CheckStatus.SKIP,
                message="Skipped live API validation (use --live to enable).",
            )
        )

    return checks


def _credential_checks(settings: Settings) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    if settings.llm_mode == "mock":
        checks.append(
            DoctorCheck(
                name="credentials",
                status=CheckStatus.OK,
                message="Mock mode — no API key required.",
            )
        )
        return checks

    if settings.llm_mode == "openai":
        checks.append(_credential_source_check("OPENAI_API_KEY", required_for="openai mode"))
        return checks

    if is_ollama_openai_compatible(settings):
        checks.append(
            DoctorCheck(
                name="LLM_API_KEY",
                status=CheckStatus.OK,
                message="source: not required for Ollama",
            )
        )
    else:
        checks.append(_hosted_llm_api_key_check(settings))
    base_ok = bool(settings.llm_base_url)
    checks.append(
        DoctorCheck(
            name="LLM_BASE_URL",
            status=CheckStatus.OK if base_ok else CheckStatus.FAIL,
            message="Set" if base_ok else "Missing",
        )
    )
    model_ok = bool(settings.llm_model)
    checks.append(
        DoctorCheck(
            name="LLM_MODEL",
            status=CheckStatus.OK if model_ok else CheckStatus.FAIL,
            message="Set" if model_ok else "Missing",
        )
    )
    return checks


def _ollama_checks(settings: Settings, fetch_tags: TagsFetcher) -> list[DoctorCheck]:
    base = settings.llm_base_url or OLLAMA_BASE_URL
    tags_url = ollama_tags_url(base)
    ok, installed, detail = fetch_tags(tags_url, 5.0)
    checks: list[DoctorCheck] = []
    if not ok:
        checks.append(
            DoctorCheck(
                name="ollama",
                status=CheckStatus.FAIL,
                message=f"Could not reach Ollama at {base} ({tags_url}): {detail}",
            )
        )
        checks.append(
            DoctorCheck(
                name="ollama_model",
                status=CheckStatus.SKIP,
                message="Skipped — Ollama endpoint unreachable.",
            )
        )
        return checks

    checks.append(
        DoctorCheck(
            name="ollama",
            status=CheckStatus.OK,
            message=f"Reachable at {base} ({detail})",
        )
    )

    configured = settings.llm_model or ""
    if not configured:
        checks.append(
            DoctorCheck(
                name="ollama_model",
                status=CheckStatus.FAIL,
                message="LLM_MODEL is not set.",
            )
        )
        return checks

    names = installed or []
    if model_is_installed(configured, names):
        checks.append(
            DoctorCheck(
                name="ollama_model",
                status=CheckStatus.OK,
                message=f"Model {configured!r} is installed.",
            )
        )
    else:
        checks.append(
            DoctorCheck(
                name="ollama_model",
                status=CheckStatus.FAIL,
                message=format_missing_model_message(configured, names),
            )
        )
    return checks


def _api_mode_check(settings: Settings, runtime: RuntimeOptions | None) -> DoctorCheck:
    if settings.llm_mode == "mock":
        return DoctorCheck(
            name="api_mode",
            status=CheckStatus.SKIP,
            message="Not applicable for mock mode.",
        )
    preference = runtime.api_mode if runtime is not None else "auto"
    if (
        settings.llm_mode == "openai_compatible"
        and settings.llm_provider_name in CHAT_PREFERRED_PROVIDERS
    ):
        effective = resolve_effective_api_mode(
            preference,
            provider_name=settings.llm_provider_name,
        )
        detail = (
            f"preference={preference!r}, effective={effective!r} "
            f"({settings.llm_provider_name!r} uses chat completions; auto resolves to chat)"
        )
    else:
        detail = f"preference={preference!r} (auto tries Responses API, may fall back to chat)"
    return DoctorCheck(name="api_mode", status=CheckStatus.OK, message=detail)


def _live_provider_check(
    settings: Settings,
    *,
    runtime: RuntimeOptions | None,
) -> DoctorCheck:
    try:
        options = runtime or RuntimeOptions(timeout_seconds=10.0)
        provider = create_provider(settings, runtime=options)
        meta = provider.metadata
    except Exception as exc:  # noqa: BLE001
        return DoctorCheck(
            name="live",
            status=CheckStatus.FAIL,
            message=f"Provider initialization failed: {type(exc).__name__}",
        )
    used = meta.api_mode_used or "not yet invoked"
    return DoctorCheck(
        name="live",
        status=CheckStatus.OK,
        message=(
            f"Provider initialized (no completion call). "
            f"api_mode preference={meta.api_mode_preference!r}, used={used!r}."
        ),
    )


def _hosted_llm_api_key_check(settings: Settings) -> DoctorCheck:
    provider = settings.llm_provider_name
    hint = _provider_key_hint(provider)
    source = credential_source("LLM_API_KEY")
    if source == "missing":
        return DoctorCheck(
            name="LLM_API_KEY",
            status=CheckStatus.FAIL,
            message=f"source: missing ({hint})",
        )
    return DoctorCheck(
        name="LLM_API_KEY",
        status=CheckStatus.OK,
        message=f"source: {source} ({hint})",
    )


def _provider_key_hint(provider_name: str) -> str:
    hints = {
        "nvidia": "set LLM_API_KEY from build.nvidia.com",
        "groq": "set LLM_API_KEY from console.groq.com",
        "cerebras": "set LLM_API_KEY from cloud.cerebras.ai",
        "openrouter": "set LLM_API_KEY from openrouter.ai/keys",
    }
    return hints.get(provider_name, "required for openai_compatible mode")


def _credential_source_check(name: str, *, required_for: str) -> DoctorCheck:
    source = credential_source(name)
    if source == "missing":
        return DoctorCheck(
            name=name,
            status=CheckStatus.FAIL,
            message=f"source: missing (required for {required_for})",
        )
    return DoctorCheck(
        name=name,
        status=CheckStatus.OK,
        message=f"source: {source}",
    )


def _default_http_probe(url: str, timeout: float) -> tuple[bool, str]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            if 200 <= response.status < 500:
                return True, f"Reachable ({url})"
            return False, f"HTTP {response.status}"
    except urllib.error.URLError as exc:
        return False, str(exc.reason) if exc.reason else "connection failed"
    except TimeoutError:
        return False, "timed out"


def available_presets_hint() -> str:
    return ", ".join(list_preset_names())
