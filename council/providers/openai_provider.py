from __future__ import annotations

import json
import time
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError

from council.models import AgentBrief, AgentRole, DecisionDossier
from council.providers.base import LLMProvider
from council.providers.errors import ProviderResponseError
from council.providers.models import ProviderMetadata, ProviderRequest, ProviderResponse

AGENT_BRIEF_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "reasoning": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "source_refs": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["headline", "reasoning", "confidence", "source_refs"],
    "additionalProperties": False,
}

DOSSIER_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "arguments_for": {"type": "array", "items": {"type": "string"}},
        "arguments_against": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "recommendation": {"type": "string"},
        "confidence_score": {"type": "number", "minimum": 0, "maximum": 1},
        "kill_criteria": {"type": "array", "items": {"type": "string"}},
        "next_actions": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "assumptions",
        "arguments_for",
        "arguments_against",
        "risks",
        "recommendation",
        "confidence_score",
        "kill_criteria",
        "next_actions",
        "open_questions",
    ],
    "additionalProperties": False,
}

ROLE_GUIDANCE: dict[AgentRole, str] = {
    AgentRole.CONTEXT: "Clarify stakeholders, constraints, and what success looks like.",
    AgentRole.RESEARCH: "Summarize relevant options, precedents, and evidence.",
    AgentRole.SKEPTIC: "Challenge assumptions and identify weak reasoning.",
    AgentRole.RISK: "Surface downside risks, failure modes, and mitigations.",
    AgentRole.OPERATOR: "Focus on execution path, sequencing, and operational feasibility.",
    AgentRole.CHAIR: "Synthesize council input into a decision dossier.",
}


class _AgentBriefPayload(BaseModel):
    headline: str
    reasoning: str
    confidence: float = Field(ge=0.0, le=1.0)
    source_refs: list[str] = Field(default_factory=list)


class _DossierPayload(BaseModel):
    assumptions: list[str]
    arguments_for: list[str]
    arguments_against: list[str]
    risks: list[str]
    recommendation: str
    confidence_score: float = Field(ge=0.0, le=1.0)
    kill_criteria: list[str]
    next_actions: list[str]
    open_questions: list[str]


class OpenAIProvider(LLMProvider):
    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        mode: str = "openai",
        client: OpenAI | None = None,
    ) -> None:
        self._api_key = api_key
        self._client = client or OpenAI(api_key=api_key)
        self._metadata = ProviderMetadata(
            provider_name="openai",
            model_name=model_name,
            mode=mode,
            supports_structured_output=True,
            supports_streaming=False,
        )

    @property
    def metadata(self) -> ProviderMetadata:
        return self._metadata

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        started = time.perf_counter()
        instructions = self._agent_instructions(request.role)
        user_content = self._format_agent_user_prompt(request)
        raw_text = self._call_structured_json(
            instructions=instructions,
            user_content=user_content,
            schema_name="agent_brief",
            schema=AGENT_BRIEF_JSON_SCHEMA,
        )
        brief = parse_agent_brief_payload(raw_text, request.role, provider_name="openai")
        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        return ProviderResponse(
            brief=brief,
            token_usage=None,
            latency_ms=latency_ms,
            raw_response=raw_text,
        )

    def synthesize_dossier(
        self,
        question: str,
        briefs: list[AgentBrief],
        run_id: str,
    ) -> DecisionDossier:
        instructions = (
            "You are the Chair/Judge of a decision council. "
            "Synthesize the specialist briefs into one decision dossier. "
            "Return only JSON matching the schema."
        )
        user_content = self._format_dossier_user_prompt(question, briefs)
        raw_text = self._call_structured_json(
            instructions=instructions,
            user_content=user_content,
            schema_name="decision_dossier",
            schema=DOSSIER_JSON_SCHEMA,
        )
        return parse_dossier_payload(
            raw_text,
            question=question,
            run_id=run_id,
            provider_name="openai",
        )

    def _call_structured_json(
        self,
        *,
        instructions: str,
        user_content: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> str:
        try:
            response = self._client.responses.create(
                model=self._metadata.model_name,
                instructions=instructions,
                input=user_content,
                stream=False,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "strict": True,
                        "schema": schema,
                    }
                },
            )
        except Exception as exc:  # noqa: BLE001
            message = _redact_secrets(str(exc), self._api_key)
            raise ProviderResponseError("openai", f"API call failed: {message}") from exc

        output_text = getattr(response, "output_text", None)
        if not output_text or not str(output_text).strip():
            raise ProviderResponseError("openai", "API response contained no output text.")
        return str(output_text)

    def _agent_instructions(self, role: AgentRole) -> str:
        guidance = ROLE_GUIDANCE.get(role, "Provide a concise specialist brief.")
        return (
            f"You are the {role.value} agent in a decision council. {guidance} "
            "Respond with JSON only, matching the provided schema."
        )

    def _format_agent_user_prompt(self, request: ProviderRequest) -> str:
        prior = "\n".join(
            f"- {brief.role.value}: {brief.headline}" for brief in request.prior_briefs
        )
        prior_section = prior or "(none yet)"
        return (
            f"Decision question:\n{request.question}\n\n"
            f"Your role: {request.role.value}\n\n"
            f"Prior council briefs:\n{prior_section}\n\n"
            "Produce your agent brief."
        )

    def _format_dossier_user_prompt(self, question: str, briefs: list[AgentBrief]) -> str:
        lines = [f"Decision question:\n{question}\n", "Council briefs:"]
        for brief in briefs:
            lines.append(
                f"\n[{brief.role.value}]\n"
                f"Headline: {brief.headline}\n"
                f"Confidence: {brief.confidence:.2f}\n"
                f"Reasoning: {brief.reasoning}\n"
                f"Sources: {', '.join(brief.source_refs) if brief.source_refs else '(none)'}"
            )
        lines.append("\nProduce the final decision dossier.")
        return "\n".join(lines)


def parse_agent_brief_payload(
    raw_text: str,
    role: AgentRole,
    *,
    provider_name: str,
) -> AgentBrief:
    payload = _load_json_object(raw_text, provider_name)
    try:
        parsed = _AgentBriefPayload.model_validate(payload)
    except ValidationError as exc:
        raise ProviderResponseError(provider_name, f"agent brief validation failed: {exc}") from exc

    return AgentBrief(
        role=role,
        headline=parsed.headline.strip(),
        reasoning=parsed.reasoning.strip(),
        confidence=parsed.confidence,
        source_refs=parsed.source_refs,
    )


def parse_dossier_payload(
    raw_text: str,
    *,
    question: str,
    run_id: str,
    provider_name: str,
) -> DecisionDossier:
    payload = _load_json_object(raw_text, provider_name)
    try:
        parsed = _DossierPayload.model_validate(payload)
    except ValidationError as exc:
        raise ProviderResponseError(provider_name, f"dossier validation failed: {exc}") from exc

    return DecisionDossier(
        run_id=run_id,
        decision_question=question,
        assumptions=parsed.assumptions,
        arguments_for=parsed.arguments_for,
        arguments_against=parsed.arguments_against,
        risks=parsed.risks,
        recommendation=parsed.recommendation.strip(),
        confidence_score=parsed.confidence_score,
        kill_criteria=parsed.kill_criteria,
        next_actions=parsed.next_actions,
        open_questions=parsed.open_questions,
    )


def _load_json_object(raw_text: str, provider_name: str) -> dict[str, Any]:
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ProviderResponseError(provider_name, "response was not valid JSON.") from exc
    if not isinstance(data, dict):
        raise ProviderResponseError(provider_name, "response JSON must be an object.")
    return data


def _redact_secrets(text: str, api_key: str) -> str:
    redacted = text
    if api_key:
        redacted = redacted.replace(api_key, "[REDACTED]")
    return redacted
