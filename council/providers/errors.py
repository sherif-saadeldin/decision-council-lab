from __future__ import annotations


class UnsupportedProviderModeError(ValueError):
    """Raised when LLM_MODE is not registered in the provider factory."""

    def __init__(self, mode: str, supported_modes: tuple[str, ...]) -> None:
        self.mode = mode
        self.supported_modes = supported_modes
        supported = ", ".join(supported_modes) or "(none)"
        message = (
            f"Unsupported LLM_MODE={mode!r}. "
            f"Supported modes: {supported}. "
            "Real providers arrive in later slices; use mock for now."
        )
        super().__init__(message)
