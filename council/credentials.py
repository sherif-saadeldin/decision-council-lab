from __future__ import annotations

from council.config import Settings

OLLAMA_DUMMY_API_KEY = "ollama"


def is_ollama_openai_compatible(settings: Settings) -> bool:
    return (
        settings.llm_mode == "openai_compatible"
        and settings.llm_provider_name == "ollama"
    )


def is_ollama_dummy_key(api_key: str | None) -> bool:
    return api_key == OLLAMA_DUMMY_API_KEY


def resolve_llm_api_key(settings: Settings) -> str | None:
    """Return LLM API key from settings, or Ollama dummy when no real key is configured."""
    if settings.llm_api_key:
        return settings.llm_api_key
    if is_ollama_openai_compatible(settings):
        return OLLAMA_DUMMY_API_KEY
    return None


def redaction_secrets(settings: Settings) -> list[str]:
    """Credential values to redact from user-facing text and debug artifacts."""
    secrets: list[str] = []
    if settings.openai_api_key:
        secrets.append(settings.openai_api_key)
    if settings.llm_api_key and not is_ollama_dummy_key(settings.llm_api_key):
        secrets.append(settings.llm_api_key)
    return secrets


def strip_ollama_dummy_from_text(text: str) -> str:
    """Remove the local Ollama placeholder key from user-facing or debug output."""
    if OLLAMA_DUMMY_API_KEY not in text:
        return text
    return text.replace(OLLAMA_DUMMY_API_KEY, "").replace("  ", " ").strip()
