from __future__ import annotations

import time
from typing import Any

from openai import OpenAI

from council.debate_prompts import (
    DEBATE_ROUND_JSON_SCHEMA,
    debate_round_instructions,
    format_debate_round_user_prompt,
)
from council.models import AgentBrief, DebateRound, DebateTranscript, DecisionDossier
from council.prompt_debug import PromptDebugCollector
from council.prompts import (
    AGENT_BRIEF_JSON_SCHEMA,
    DOSSIER_JSON_SCHEMA,
    agent_instructions,
    chair_instructions,
    format_agent_user_prompt,
    format_dossier_user_prompt,
)
from council.providers.base import LLMProvider
from council.providers.errors import ProviderResponseError
from council.providers.openai_errors import raise_compatible_provider_error
from council.providers.models import ProviderMetadata, ProviderRequest, ProviderResponse
from council.providers.parsing import (
    parse_agent_brief_payload,
    parse_debate_round_payload,
    parse_dossier_payload,
)


class OpenAICompatibleProvider(LLMProvider):
    """OpenAI SDK client for OpenAI direct or any OpenAI-compatible HTTP endpoint."""

    def __init__(
        self,
        *,
        provider_name: str,
        api_key: str,
        model_name: str,
        mode: str = "openai_compatible",
        base_url: str | None = None,
        credential_env: str = "LLM_API_KEY",
        client: OpenAI | None = None,
    ) -> None:
        self._api_key = api_key
        self._credential_env = credential_env
        if client is not None:
            self._client = client
        elif base_url is not None:
            self._client = OpenAI(api_key=api_key, base_url=base_url)
        else:
            self._client = OpenAI(api_key=api_key)
        self._metadata = ProviderMetadata(
            provider_name=provider_name,
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
        instructions = agent_instructions(request.role)
        user_content = format_agent_user_prompt(request)
        self._record_debug(
            request.debug_collector,
            step=f"{request.role.value}_agent",
            role=request.role.value,
            instructions=instructions,
            user_content=user_content,
        )
        raw_text = self._call_structured_json(
            instructions=instructions,
            user_content=user_content,
            schema_name="agent_brief",
            schema=AGENT_BRIEF_JSON_SCHEMA,
        )
        brief = parse_agent_brief_payload(
            raw_text,
            request.role,
            provider_name=self._metadata.provider_name,
        )
        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        return ProviderResponse(
            brief=brief,
            token_usage=None,
            latency_ms=latency_ms,
            raw_response=raw_text,
        )

    def run_debate_round(
        self,
        *,
        question: str,
        briefs: list[AgentBrief],
        prior_rounds: list[DebateRound],
        round_number: int,
        total_rounds: int,
        run_id: str,
        debug_collector: PromptDebugCollector | None = None,
    ) -> DebateRound:
        _ = run_id
        instructions = debate_round_instructions()
        user_content = format_debate_round_user_prompt(
            question=question,
            briefs=briefs,
            prior_rounds=prior_rounds,
            round_number=round_number,
            total_rounds=total_rounds,
        )
        self._record_debug(
            debug_collector,
            step=f"debate_round_{round_number}",
            role="debate",
            instructions=instructions,
            user_content=user_content,
        )
        raw_text = self._call_structured_json(
            instructions=instructions,
            user_content=user_content,
            schema_name="debate_round",
            schema=DEBATE_ROUND_JSON_SCHEMA,
        )
        return parse_debate_round_payload(
            raw_text,
            round_number=round_number,
            provider_name=self._metadata.provider_name,
        )

    def synthesize_dossier(
        self,
        question: str,
        briefs: list[AgentBrief],
        run_id: str,
        *,
        debug_collector: PromptDebugCollector | None = None,
        debate_transcript: DebateTranscript | None = None,
    ) -> DecisionDossier:
        instructions = chair_instructions()
        user_content = format_dossier_user_prompt(question, briefs, debate_transcript)
        self._record_debug(
            debug_collector,
            step="chair_synthesis",
            role="chair",
            instructions=instructions,
            user_content=user_content,
        )
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
            provider_name=self._metadata.provider_name,
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
            raise_compatible_provider_error(
                exc,
                provider_name=self._metadata.provider_name,
                credential_env=self._credential_env,
            )

        output_text = getattr(response, "output_text", None)
        if not output_text or not str(output_text).strip():
            raise ProviderResponseError(
                self._metadata.provider_name,
                "API response contained no output text.",
                source="api",
            )
        return str(output_text)

    @staticmethod
    def _record_debug(
        collector: PromptDebugCollector | None,
        *,
        step: str,
        role: str | None,
        instructions: str,
        user_content: str,
    ) -> None:
        if collector is not None:
            collector.record(
                step=step,
                role=role,
                instructions=instructions,
                user_content=user_content,
            )
