from __future__ import annotations

import os
from typing import Literal

import keyring

KEYRING_SERVICE = "decision-council-lab"

SUPPORTED_SECRET_NAMES: frozenset[str] = frozenset({"OPENAI_API_KEY", "LLM_API_KEY"})

CredentialSource = Literal["env", "keyring", "missing"]


class UnknownSecretNameError(ValueError):
    """Raised when a secrets CLI command uses an unsupported name."""

    def __init__(self, name: str) -> None:
        self.name = name
        supported = ", ".join(sorted(SUPPORTED_SECRET_NAMES))
        super().__init__(f"Unknown secret {name!r}. Supported: {supported}.")


def validate_secret_name(name: str) -> None:
    if name not in SUPPORTED_SECRET_NAMES:
        raise UnknownSecretNameError(name)


def credential_source(name: str) -> CredentialSource:
    validate_secret_name(name)
    if os.getenv(name, "").strip():
        return "env"
    if is_keyring_secret_set(name):
        return "keyring"
    return "missing"


def resolve_secret_value(name: str) -> str | None:
    """Return secret value: environment variable wins over OS keyring."""
    validate_secret_name(name)
    env_value = os.getenv(name, "").strip()
    if env_value:
        return env_value
    return get_keyring_secret(name)


def is_keyring_secret_set(name: str) -> bool:
    validate_secret_name(name)
    value = get_keyring_secret(name)
    return bool(value)


def get_keyring_secret(name: str) -> str | None:
    validate_secret_name(name)
    try:
        value = keyring.get_password(KEYRING_SERVICE, name)
    except Exception:  # noqa: BLE001
        return None
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def set_keyring_secret(name: str, value: str) -> None:
    validate_secret_name(name)
    keyring.set_password(KEYRING_SERVICE, name, value)


def delete_keyring_secret(name: str) -> None:
    validate_secret_name(name)
    try:
        keyring.delete_password(KEYRING_SERVICE, name)
    except keyring.errors.PasswordDeleteError:
        pass


def list_secret_statuses() -> list[tuple[str, bool]]:
    return [(name, is_secret_available(name)) for name in sorted(SUPPORTED_SECRET_NAMES)]


def is_secret_available(name: str) -> bool:
    return credential_source(name) != "missing"
