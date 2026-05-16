from __future__ import annotations

from abc import ABC, abstractmethod

from council.models import AgentBrief, AgentRole
from council.providers.models import ProviderMetadata, ProviderRequest, ProviderResponse


class LLMProvider(ABC):
    """Contract for council LLM backends (mock now; OpenAI and others in later slices)."""

    @property
    @abstractmethod
    def metadata(self) -> ProviderMetadata:
        raise NotImplementedError

    @property
    def name(self) -> str:
        return self.metadata.provider_name

    @property
    def model_name(self) -> str:
        return self.metadata.model_name

    @abstractmethod
    def complete(self, request: ProviderRequest) -> ProviderResponse:
        raise NotImplementedError

    def generate_brief(
        self,
        role: AgentRole,
        question: str,
        prior_briefs: list[AgentBrief],
        *,
        run_id: str | None = None,
    ) -> AgentBrief:
        response = self.complete(
            ProviderRequest(
                role=role,
                question=question,
                prior_briefs=prior_briefs,
                run_id=run_id,
            )
        )
        return response.brief
