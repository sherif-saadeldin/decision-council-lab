from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from council.config import Settings
from council.credentials import redaction_secrets, strip_ollama_dummy_from_text
from council.providers.base import LLMProvider
from council.providers.errors import MissingProviderCredentialError, ProviderResponseError
from council.providers.factory import create_provider
from council.redaction import redact_secrets
from council.runtime import RuntimeOptions

LIVE_PING_USER_PROMPT = 'Return only JSON: {"ok": true}'
LIVE_PING_INSTRUCTIONS = "Return only JSON matching the schema. No other text."
LIVE_PING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}

ProviderFactory = Callable[[Settings, RuntimeOptions], LLMProvider]
LivePingFn = Callable[[LLMProvider], tuple[bool, str | None]]


def _safe_failure_reason(exc: BaseException, settings: Settings) -> str:
    if isinstance(exc, ProviderResponseError):
        message = exc.detail
    elif isinstance(exc, MissingProviderCredentialError):
        message = str(exc)
    else:
        message = f"{type(exc).__name__}"
    return strip_ollama_dummy_from_text(
        redact_secrets(message, redaction_secrets(settings)),
    )


def default_live_ping(provider: LLMProvider) -> tuple[bool, str | None]:
    ping = getattr(provider, "live_ping", None)
    if callable(ping):
        return ping()
    return False, "Provider does not support live completion ping."


def run_live_completion_check(
    settings: Settings,
    runtime: RuntimeOptions | None = None,
    *,
    provider_factory: ProviderFactory | None = None,
    live_ping_fn: LivePingFn | None = None,
) -> tuple[bool, str | None]:
    """Run one minimal completion; return (ok, failure_reason). Never returns raw payload."""
    runtime = runtime or RuntimeOptions(timeout_seconds=15.0, max_retries=0)
    factory = provider_factory or create_provider
    ping_fn = live_ping_fn or default_live_ping
    try:
        provider = factory(settings, runtime)
    except Exception as exc:  # noqa: BLE001
        return False, _safe_failure_reason(exc, settings)
    try:
        return ping_fn(provider)
    except Exception as exc:  # noqa: BLE001
        return False, _safe_failure_reason(exc, settings)


def validate_ping_json(raw_text: str) -> tuple[bool, str | None]:
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        return False, "Response was not valid JSON."
    if isinstance(payload, dict) and payload.get("ok") is True:
        return True, None
    return False, 'JSON did not contain "ok": true.'
