from __future__ import annotations

import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from council.config import Settings
from council.model_presets import OLLAMA_BASE_URL, apply_preset, list_preset_names
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


def resolve_doctor_settings(*, preset: str | None = None) -> Settings:
    settings = Settings.from_env()
    if preset:
        settings = apply_preset(settings, preset)
    return settings


def run_doctor(
    settings: Settings,
    *,
    live: bool = False,
    http_probe: Callable[[str, float], tuple[bool, str]] | None = None,
) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    probe = http_probe or _default_http_probe

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

    if settings.llm_mode == "openai_compatible" and settings.llm_provider_name == "ollama":
        checks.append(_ollama_reachability_check(settings, probe))
    elif settings.llm_mode == "openai_compatible" and settings.llm_base_url:
        checks.append(
            DoctorCheck(
                name="base_url",
                status=CheckStatus.OK,
                message=f"LLM_BASE_URL configured ({settings.llm_base_url})",
            )
        )

    if live:
        checks.append(_live_provider_check(settings))
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
        present = _env_present("OPENAI_API_KEY")
        checks.append(
            DoctorCheck(
                name="OPENAI_API_KEY",
                status=CheckStatus.OK if present else CheckStatus.FAIL,
                message="Set" if present else "Missing (required for openai mode)",
            )
        )
        return checks

    llm_key_present = _env_present("LLM_API_KEY")
    checks.append(
        DoctorCheck(
            name="LLM_API_KEY",
            status=CheckStatus.OK if llm_key_present else CheckStatus.FAIL,
            message="Set" if llm_key_present else "Missing (required for openai_compatible mode)",
        )
    )
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


def _ollama_reachability_check(
    settings: Settings,
    probe: Callable[[str, float], tuple[bool, str]],
) -> DoctorCheck:
    base = settings.llm_base_url or OLLAMA_BASE_URL
    url = base.rstrip("/") + "/models"
    ok, detail = probe(url, 3.0)
    return DoctorCheck(
        name="ollama",
        status=CheckStatus.OK if ok else CheckStatus.WARN,
        message=detail if ok else f"Could not reach Ollama at {base}: {detail}",
    )


def _live_provider_check(settings: Settings) -> DoctorCheck:
    try:
        provider = create_provider(settings, runtime=RuntimeOptions(timeout_seconds=10.0))
        _ = provider.metadata
    except Exception as exc:  # noqa: BLE001
        return DoctorCheck(
            name="live",
            status=CheckStatus.FAIL,
            message=f"Provider initialization failed: {type(exc).__name__}",
        )
    return DoctorCheck(
        name="live",
        status=CheckStatus.OK,
        message="Provider initialized (no completion call made).",
    )


def _env_present(name: str) -> bool:
    return bool(os.getenv(name, "").strip())


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
