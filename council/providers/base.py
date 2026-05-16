from __future__ import annotations

from abc import ABC, abstractmethod

from council.models import AgentBrief, AgentRole


class LLMProvider(ABC):
    """Provider abstraction for future OpenAI / Anthropic / Gemini integrations."""

    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def model_name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def generate_brief(
        self,
        role: AgentRole,
        question: str,
        prior_briefs: list[AgentBrief],
    ) -> AgentBrief:
        raise NotImplementedError
