from __future__ import annotations


class UnsupportedProviderModeError(ValueError):
    """Raised when LLM_MODE is not registered in the provider factory."""

    def __init__(self, mode: str, supported_modes: tuple[str, ...]) -> None:
        self.mode = mode
        self.supported_modes = supported_modes
        supported = ", ".join(supported_modes) or "(none)"
        message = (
            f"Unsupported LLM_MODE={mode!r}. "
            f"Supported modes: {supported}."
        )
        super().__init__(message)


class MissingProviderCredentialError(ValueError):
    """Raised when a provider mode is selected without required credentials."""

    def __init__(self, provider_name: str, env_var: str) -> None:
        self.provider_name = provider_name
        self.env_var = env_var
        message = (
            f"Missing required environment variable {env_var} "
            f"for provider {provider_name!r}. "
            "Set the variable in your environment or .env file."
        )
        super().__init__(message)


class MissingProviderConfigError(ValueError):
    """Raised when a provider mode is missing required non-secret configuration."""

    def __init__(self, provider_name: str, setting_name: str) -> None:
        self.provider_name = provider_name
        self.setting_name = setting_name
        message = (
            f"Missing required setting {setting_name} "
            f"for provider {provider_name!r}. "
            "Set the variable in your environment or .env file."
        )
        super().__init__(message)


class ProviderResponseError(RuntimeError):
    """Raised when provider API calls fail or output cannot be parsed."""

    def __init__(self, provider_name: str, detail: str, *, source: str = "response") -> None:
        self.provider_name = provider_name
        self.detail = detail
        self.source = source
        if source == "api":
            message = f"{provider_name} provider error: {detail}"
        else:
            message = f"{provider_name} provider returned malformed output: {detail}"
        super().__init__(message)
