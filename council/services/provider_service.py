from __future__ import annotations

from dataclasses import dataclass

from council.config_profiles import ConfigProfile
from council.provider_availability import HostedProviderUnavailableError, preset_is_hosted
from council.providers.errors import MissingProviderCredentialError, ProviderResponseError
from council.recovery import (
    ClassifiedFailure,
    classify_for_recovery,
    format_recovery_lines,
    is_provider_failure,
    is_recoverable_with_mock,
)


@dataclass(frozen=True)
class ProviderRecoveryRequest:
    exc: Exception
    config_profile_name: str | None
    config_profile: ConfigProfile | None
    fallback_profile_name: str = "mock"


@dataclass(frozen=True)
class ProviderRecoveryPlan:
    is_provider_failure: bool
    failure: ClassifiedFailure | None
    reason: str = ""
    fix: str = ""
    suggestions: str = ""
    offer_fallback: bool = False


class ProviderRecoveryService:
    def analyze(self, request: ProviderRecoveryRequest) -> ProviderRecoveryPlan:
        if not is_provider_failure(request.exc):
            return ProviderRecoveryPlan(is_provider_failure=False, failure=None)
        failure = classify_for_recovery(request.exc)
        reason, fix, suggestions = format_recovery_lines(failure)
        offer_fallback = self._should_offer_fallback(request, failure)
        return ProviderRecoveryPlan(
            is_provider_failure=True,
            failure=failure,
            reason=reason,
            fix=fix,
            suggestions=suggestions,
            offer_fallback=offer_fallback,
        )

    def _should_offer_fallback(
        self,
        request: ProviderRecoveryRequest,
        failure: ClassifiedFailure,
    ) -> bool:
        if request.config_profile_name == request.fallback_profile_name:
            return False
        if not (
            isinstance(request.exc, HostedProviderUnavailableError)
            or self._is_hosted_failure(request.exc, request.config_profile)
        ):
            return False
        return is_recoverable_with_mock(failure)

    @staticmethod
    def _is_hosted_failure(
        exc: Exception,
        profile: ConfigProfile | None,
    ) -> bool:
        if not isinstance(exc, (MissingProviderCredentialError, ProviderResponseError)):
            return False
        if profile is None:
            return False
        if profile.preset and preset_is_hosted(profile.preset):
            return True
        mode = (profile.mode or "").lower()
        return mode in {"openai", "openai_compatible"}

