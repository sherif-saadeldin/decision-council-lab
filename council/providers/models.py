from __future__ import annotations

from pydantic import BaseModel, Field

from council.models import AgentBrief, AgentRole


class TokenUsage(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class ProviderMetadata(BaseModel):
    provider_name: str
    model_name: str
    mode: str
    supports_structured_output: bool = False
    supports_streaming: bool = False


class ProviderRequest(BaseModel):
    role: AgentRole
    question: str
    prior_briefs: list[AgentBrief] = Field(default_factory=list)
    run_id: str | None = None


class ProviderResponse(BaseModel):
    brief: AgentBrief
    token_usage: TokenUsage | None = None
    latency_ms: float | None = None
    raw_response: str | None = None
