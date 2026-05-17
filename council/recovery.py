"""Chat-facing recovery hints derived from provider failures.

Maps a raised exception to a short, stable summary + an actionable fix line +
a tiny set of recovery commands to print. Pure-logic — no I/O, no network,
no secrets. Sits on top of `council.providers.failures.classify_provider_failure`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from council.provider_availability import HostedProviderUnavailableError
from council.providers.errors import (
    FailureKind,
    MissingProviderCredentialError,
    ProviderResponseError,
)
from council.providers.failures import classify_provider_failure

# Recovery command bundles. Tiny on purpose so they stay skim-readable
# even when printed after an error panel.
RECOVERY_DEFAULT: tuple[str, ...] = ("/status", "/doctor", "/profile mock")
RECOVERY_AUTH: tuple[str, ...] = ("/profile mock", "/setup", "/doctor")
RECOVERY_NETWORK: tuple[str, ...] = ("/profile mock", "/doctor")
RECOVERY_RATE_LIMIT: tuple[str, ...] = ("/status", "/profile mock")
RECOVERY_PARSE: tuple[str, ...] = ("/doctor", "/profile mock")


@dataclass(frozen=True)
class ClassifiedFailure:
    category: FailureKind  # one of: auth_failure, timeout, parse_failure, network_failure, api_failure, rate_limit, unknown
    summary: str
    actionable_fix: str
    recovery_commands: tuple[str, ...]


_RATE_LIMIT_RE = re.compile(r"\b(429|rate[- ]?limit|too many requests|quota)\b", re.IGNORECASE)


def classify_for_recovery(
    exc: BaseException,
    *,
    message: str | None = None,
) -> ClassifiedFailure:
    """Turn an exception into a recovery hint suitable for the chat UI."""
    text = (message or str(exc) or "").strip()

    # MissingProviderCredentialError carries the env_var name — surface it
    # in the fix line so the user knows exactly what to set.
    if isinstance(exc, MissingProviderCredentialError):
        return ClassifiedFailure(
            category="auth_failure",
            summary=f"Missing credential: {exc.env_var}.",
            actionable_fix=(
                f"Set {exc.env_var} via `uv run python main.py secrets set {exc.env_var}`, "
                "or `/profile mock` to keep working offline."
            ),
            recovery_commands=RECOVERY_AUTH,
        )

    # Rate limit doesn't have a FailureKind in the existing taxonomy; promote
    # explicitly when the message clearly indicates one (429, quota, etc).
    if _RATE_LIMIT_RE.search(text):
        return ClassifiedFailure(
            category="api_failure",
            summary=text or "Provider rate limit hit.",
            actionable_fix=(
                "Wait and retry later, or `/profile mock` to keep working offline. "
                "Free tiers often exhaust quickly — try `--routing-mode economy`."
            ),
            recovery_commands=RECOVERY_RATE_LIMIT,
        )

    kind = (
        classify_provider_failure(exc)
        if isinstance(exc, Exception)
        else "unknown"
    )

    if isinstance(exc, HostedProviderUnavailableError) and kind == "unknown":
        # Live ping failures wrap the underlying reason in the message text.
        kind = _classify_text(text) or "api_failure"

    return _from_kind(kind, text)


def _classify_text(text: str) -> FailureKind | None:
    if not text:
        return None
    lower = text.lower()
    if any(token in lower for token in ("401", "403", "unauthorized", "forbidden", "invalid api key")):
        return "auth_failure"
    if "timed out" in lower or "timeout" in lower or "deadline exceeded" in lower:
        return "timeout"
    if any(
        token in lower
        for token in ("connection", "dns", "unreachable", "refused", "getaddrinfo")
    ):
        return "network_failure"
    return None


def _from_kind(kind: FailureKind, text: str) -> ClassifiedFailure:
    if kind == "auth_failure":
        return ClassifiedFailure(
            category=kind,
            summary=text or "Provider authentication failed.",
            actionable_fix=(
                "Likely a wrong or missing API key. "
                "Re-run `/setup` or `secrets set`, or `/profile mock` to keep working offline."
            ),
            recovery_commands=RECOVERY_AUTH,
        )
    if kind == "timeout":
        return ClassifiedFailure(
            category=kind,
            summary=text or "Provider request timed out.",
            actionable_fix=(
                "Retry, raise `--timeout-seconds`, or `/profile mock` to keep working offline."
            ),
            recovery_commands=RECOVERY_DEFAULT,
        )
    if kind == "parse_failure":
        return ClassifiedFailure(
            category=kind,
            summary=text or "Provider returned malformed output.",
            actionable_fix=(
                "Retry with `--repair-json`, or switch presets. "
                "Use `/profile mock` to verify the rest of your flow."
            ),
            recovery_commands=RECOVERY_PARSE,
        )
    if kind == "network_failure":
        return ClassifiedFailure(
            category=kind,
            summary=text or "Provider request failed at the network layer.",
            actionable_fix=(
                "Check your connection or the provider base URL, then `/doctor`. "
                "Use `/profile mock` while offline."
            ),
            recovery_commands=RECOVERY_NETWORK,
        )
    if kind == "api_failure":
        return ClassifiedFailure(
            category=kind,
            summary=text or "Provider API returned an error.",
            actionable_fix=(
                "Check provider status, then re-run `/doctor`. "
                "Use `/profile mock` to keep working offline."
            ),
            recovery_commands=RECOVERY_DEFAULT,
        )
    return ClassifiedFailure(
        category="unknown",
        summary=text or "Unknown provider error.",
        actionable_fix=(
            "Run `/doctor` for more detail, or `/profile mock` to keep working "
            "offline while you investigate."
        ),
        recovery_commands=RECOVERY_DEFAULT,
    )


def format_recovery_lines(failure: ClassifiedFailure) -> tuple[str, str, str]:
    """Render (reason, fix, suggestions) lines for the chat error panel."""
    reason = f"Reason ({failure.category}): {failure.summary}"
    fix = f"Fix: {failure.actionable_fix}"
    suggestions = "Try: " + "  ".join(failure.recovery_commands)
    return reason, fix, suggestions


def is_recoverable_with_mock(failure: ClassifiedFailure) -> bool:
    """Should we offer 'Fallback to mock?' for this failure shape?"""
    # parse_failure is a content-shape issue — mock will keep the flow alive
    # so the user can iterate; same for the network/auth/timeout family.
    # 'unknown' is conservative: still offer the escape hatch.
    return failure.category in {
        "auth_failure",
        "timeout",
        "parse_failure",
        "network_failure",
        "api_failure",
        "unknown",
    }


# Provider exception type alias used by chat error dispatch.
_PROVIDER_EXCS: tuple[type[BaseException], ...] = (
    MissingProviderCredentialError,
    ProviderResponseError,
    HostedProviderUnavailableError,
)


def is_provider_failure(exc: BaseException) -> bool:
    return isinstance(exc, _PROVIDER_EXCS)
