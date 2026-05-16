from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

TagsFetcher = Callable[[str, float], tuple[bool, list[str] | None, str]]


def ollama_tags_url(base_url: str) -> str:
    """Map an OpenAI-compatible Ollama base URL to the native /api/tags endpoint."""
    trimmed = base_url.rstrip("/")
    if trimmed.endswith("/v1"):
        root = trimmed[:-3]
    else:
        root = trimmed
    return f"{root.rstrip('/')}/api/tags"


def parse_ollama_tag_names(payload: dict[str, Any]) -> list[str]:
    models = payload.get("models")
    if not isinstance(models, list):
        return []
    names: list[str] = []
    for entry in models:
        if isinstance(entry, dict):
            name = entry.get("name")
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
    return names


def model_is_installed(configured: str, installed: list[str]) -> bool:
    return configured in installed


def format_missing_model_message(configured: str, installed: list[str]) -> str:
    if installed:
        available = ", ".join(sorted(installed))
        return (
            f"Model {configured!r} is not installed in Ollama. "
            f"Available models: {available}. "
            f"Run: ollama pull {configured}"
        )
    return (
        f"Model {configured!r} is not installed in Ollama and no models were reported. "
        f"Run: ollama pull {configured}"
    )


def default_tags_fetcher(url: str, timeout: float) -> tuple[bool, list[str] | None, str]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            if response.status != 200:
                return False, None, f"HTTP {response.status}"
            raw = response.read()
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                return False, None, "invalid /api/tags payload"
            names = parse_ollama_tag_names(payload)
            return True, names, f"Found {len(names)} installed model(s)"
    except urllib.error.URLError as exc:
        reason = str(exc.reason) if exc.reason else "connection failed"
        return False, None, reason
    except TimeoutError:
        return False, None, "timed out"
    except json.JSONDecodeError:
        return False, None, "invalid JSON from /api/tags"
