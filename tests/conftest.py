from __future__ import annotations

from collections.abc import Callable, Generator
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from council.config import Settings

if TYPE_CHECKING:
    from council.models import CouncilRunResult

PROVIDER_ENV_KEYS: tuple[str, ...] = (
    "LLM_MODE",
    "RUNS_DIR",
    "DEFAULT_MODEL_MOCK",
    "DEFAULT_MODEL_OPENAI",
    "OPENAI_API_KEY",
    "LLM_PROVIDER_NAME",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
)

MOCK_ENV_DEFAULTS: dict[str, str] = {
    "LLM_MODE": "mock",
    "DEFAULT_MODEL_MOCK": "mock-council-v1",
}

_REAL_SETTINGS_FROM_ENV = Settings.from_env.__func__  # type: ignore[attr-defined]


def _default_mock_settings(runs_dir: Path) -> Settings:
    return Settings(
        llm_mode="mock",
        runs_dir=runs_dir,
        mock_model="mock-council-v1",
    )


@pytest.fixture
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Generator[None, None, None]:
    """Clear provider-related env vars and pin Settings.from_env to mock."""
    for key in PROVIDER_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in MOCK_ENV_DEFAULTS.items():
        monkeypatch.setenv(key, value)
    mock_settings = _default_mock_settings(tmp_path)

    @classmethod
    def _from_env(cls: type[Settings]) -> Settings:
        return mock_settings

    monkeypatch.setattr(Settings, "from_env", _from_env)
    yield


@pytest.fixture(autouse=True)
def _isolate_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    """In-memory keyring so tests never touch the OS credential store."""
    store: dict[tuple[str, str], str] = {}

    def get_password(service: str, username: str) -> str | None:
        return store.get((service, username))

    def set_password(service: str, username: str, password: str) -> None:
        store[(service, username)] = password

    def delete_password(service: str, username: str) -> None:
        store.pop((service, username), None)

    monkeypatch.setattr("council.secrets.keyring.get_password", get_password)
    monkeypatch.setattr("council.secrets.keyring.set_password", set_password)
    monkeypatch.setattr("council.secrets.keyring.delete_password", delete_password)


@pytest.fixture(autouse=True)
def _autouse_isolated_env(isolated_env: None) -> None:
    """Every test runs offline with mock-oriented defaults unless it opts out."""


@pytest.fixture
def mock_settings(tmp_path: Path) -> Settings:
    """Explicit mock Settings for run_council, CLI overrides, and save_run."""
    return _default_mock_settings(tmp_path)


@pytest.fixture
def real_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Restore real Settings.from_env (reads os.environ) for env-parsing tests."""
    monkeypatch.setattr(Settings, "from_env", classmethod(_REAL_SETTINGS_FROM_ENV))
    yield


@pytest.fixture(autouse=True)
def block_openai_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject live Responses API calls; provider construction still works with a stub client."""
    stub_client = MagicMock()
    stub_client.responses.create.side_effect = AssertionError(
        "Live LLM API call attempted during tests. Mock the client or use LLM_MODE=mock."
    )

    def stub_openai(*_args: object, **_kwargs: object) -> MagicMock:
        return stub_client

    monkeypatch.setattr("council.providers.openai_compatible.OpenAI", stub_openai)


def assert_proposed_metrics_labeled(metrics: list[str]) -> None:
    for metric in metrics:
        assert metric.lower().startswith("proposed:"), f"metric missing proposed: prefix: {metric!r}"


def assert_mock_run_schema(result: CouncilRunResult) -> None:
    """Structural guardrail checks for mock council output."""
    assert result.provider_metadata.provider_name == "mock"
    assert result.provider_metadata.mode == "mock"
    assert result.dossier.decision_type is not None
    assert result.dossier.recommendation
    assert_proposed_metrics_labeled(result.dossier.proposed_metrics)
    for brief in result.agent_briefs:
        assert_proposed_metrics_labeled(brief.proposed_metrics)


@pytest.fixture
def run_mock_council(mock_settings: Settings) -> Callable[[str], CouncilRunResult]:
    from council.engine import run_council

    def _run(question: str, *, debate_rounds: int | None = None) -> CouncilRunResult:
        kwargs = {"settings": mock_settings}
        if debate_rounds is not None:
            kwargs["debate_rounds"] = debate_rounds
        result, _ = run_council(question, **kwargs)
        return result

    return _run
