from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn, TypeVar, cast

from openai import OpenAI

from council.credentials import is_ollama_dummy_key
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
from council.providers.api_mode import (
    CHAT_PREFERRED_PROVIDERS,
    DEFAULT_API_MODE,
    ApiModePreference,
    ApiModeUsed,
    normalize_api_mode,
    should_fallback_to_chat,
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
from council.raw_response_debug import save_raw_response

REPAIR_JSON_SUFFIX = (
    "\n\nReturn ONLY valid JSON matching the schema. "
    "No markdown fences, no commentary, no text before or after the JSON object."
)

JSON_ONLY_SUFFIX = (
    "\n\nRespond with a single JSON object only. "
    "No markdown fences, no commentary, no text before or after the JSON object."
)

T = TypeVar("T")


def _raise_compatible(
    exc: BaseException,
    *,
    provider_name: str,
    credential_env: str,
    timeout_seconds: float | None,
) -> NoReturn:
    raise_compatible_provider_error(
        exc,
        provider_name=provider_name,
        credential_env=credential_env,
        timeout_seconds=timeout_seconds,
    )
    raise AssertionError("unreachable")


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
        timeout_seconds: float | None = None,
        max_retries: int = 0,
        runs_dir: Path | None = None,
        repair_json: bool = False,
        api_mode: str | ApiModePreference = DEFAULT_API_MODE,
    ) -> None:
        self._api_key = api_key
        self._credential_env = credential_env
        self._timeout_seconds = timeout_seconds
        self._max_retries = max(0, max_retries)
        self._runs_dir = runs_dir
        self._repair_json = repair_json
        self._api_mode_preference = normalize_api_mode(api_mode)
        self._api_mode_locked: ApiModeUsed | None = None
        if client is not None:
            self._client = client
        elif base_url is not None:
            self._client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=timeout_seconds,
                max_retries=0,
            )
        else:
            self._client = OpenAI(
                api_key=api_key,
                timeout=timeout_seconds,
                max_retries=0,
            )
        self._metadata = ProviderMetadata(
            provider_name=provider_name,
            model_name=model_name,
            mode=mode,
            supports_structured_output=True,
            supports_streaming=False,
            api_mode_preference=self._api_mode_preference,
            api_mode_used=None,
        )

    @property
    def metadata(self) -> ProviderMetadata:
        return self._metadata.model_copy(
            update={"api_mode_used": self._api_mode_locked},
        )

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
            fast_mode=request.fast_mode,
        )

        def parse_fn(text: str) -> AgentBrief:
            return parse_agent_brief_payload(
                text,
                request.role,
                provider_name=self._metadata.provider_name,
            )

        def retry_call() -> str:
            return self._call_structured_json(
                instructions=instructions,
                user_content=user_content,
                schema_name="agent_brief",
                schema=AGENT_BRIEF_JSON_SCHEMA,
                fast_mode=request.fast_mode,
                repair=True,
            )

        brief = self._parse_with_recovery(
            raw_text,
            request.run_id,
            parse_fn=parse_fn,
            retry_call=retry_call if self._should_attempt_repair() else None,
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

        def parse_fn(text: str) -> DebateRound:
            return parse_debate_round_payload(
                text,
                round_number=round_number,
                provider_name=self._metadata.provider_name,
            )

        def retry_call() -> str:
            return self._call_structured_json(
                instructions=instructions,
                user_content=user_content,
                schema_name="debate_round",
                schema=DEBATE_ROUND_JSON_SCHEMA,
                repair=True,
            )

        return self._parse_with_recovery(
            raw_text,
            run_id,
            parse_fn=parse_fn,
            retry_call=retry_call if self._should_attempt_repair() else None,
        )

    def synthesize_dossier(
        self,
        question: str,
        briefs: list[AgentBrief],
        run_id: str,
        *,
        debug_collector: PromptDebugCollector | None = None,
        debate_transcript: DebateTranscript | None = None,
        fast_mode: bool = False,
    ) -> DecisionDossier:
        instructions = chair_instructions()
        user_content = format_dossier_user_prompt(question, briefs, debate_transcript)
        if fast_mode:
            user_content = f"{user_content}\n\nFast mode: chair synthesis should be concise."
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

        def parse_fn(text: str) -> DecisionDossier:
            return parse_dossier_payload(
                text,
                question=question,
                run_id=run_id,
                provider_name=self._metadata.provider_name,
            )

        def retry_call() -> str:
            return self._call_structured_json(
                instructions=instructions,
                user_content=user_content,
                schema_name="decision_dossier",
                schema=DOSSIER_JSON_SCHEMA,
                repair=True,
            )

        return self._parse_with_recovery(
            raw_text,
            run_id,
            parse_fn=parse_fn,
            retry_call=retry_call if self._should_attempt_repair() else None,
        )

    def _should_attempt_repair(self) -> bool:
        return self._repair_json and self._metadata.mode == "openai_compatible"

    def _lock_api_mode(self, mode: ApiModeUsed) -> None:
        self._api_mode_locked = mode

    def _resolve_transport(self) -> ApiModeUsed:
        if self._api_mode_preference == "chat":
            return "chat"
        if self._api_mode_preference == "responses":
            return "responses"
        if self._api_mode_locked is not None:
            return self._api_mode_locked
        if self._metadata.provider_name in CHAT_PREFERRED_PROVIDERS:
            return "chat"
        return "responses"

    def _parse_with_recovery(
        self,
        raw_text: str,
        run_id: str | None,
        *,
        parse_fn: Callable[[str], T],
        retry_call: Callable[[], str] | None,
    ) -> T:
        try:
            return parse_fn(raw_text)
        except ProviderResponseError as exc:
            if exc.source != "response":
                raise
            self._save_raw_on_failure(run_id, raw_text)
            if retry_call is not None:
                repaired_raw = retry_call()
                try:
                    return parse_fn(repaired_raw)
                except ProviderResponseError:
                    self._save_raw_on_failure(run_id, repaired_raw)
                    raise
            raise

    def _save_raw_on_failure(self, run_id: str | None, raw_text: str) -> None:
        if not run_id or self._runs_dir is None:
            return
        secrets = (
            [self._api_key]
            if self._api_key and not is_ollama_dummy_key(self._api_key)
            else None
        )
        save_raw_response(
            self._runs_dir,
            run_id,
            raw_text,
            secrets,
        )

    def _call_structured_json(
        self,
        *,
        instructions: str,
        user_content: str,
        schema_name: str,
        schema: dict[str, Any],
        fast_mode: bool = False,
        repair: bool = False,
    ) -> str:
        if fast_mode:
            user_content = f"{user_content}\n\nFast mode: be concise."
        if repair:
            instructions = f"{instructions}{REPAIR_JSON_SUFFIX}"

        transport = self._resolve_transport()
        if transport == "chat":
            text = self._invoke_chat(
                instructions=instructions,
                user_content=user_content,
                schema_name=schema_name,
                schema=schema,
            )
            self._lock_api_mode("chat")
            return text

        try:
            text = self._invoke_responses(
                instructions=instructions,
                user_content=user_content,
                schema_name=schema_name,
                schema=schema,
            )
            self._lock_api_mode("responses")
            return text
        except Exception as exc:  # noqa: BLE001
            if self._api_mode_preference == "auto" and should_fallback_to_chat(
                exc,
                provider_name=self._metadata.provider_name,
            ):
                text = self._invoke_chat(
                    instructions=instructions,
                    user_content=user_content,
                    schema_name=schema_name,
                    schema=schema,
                )
                self._lock_api_mode("chat")
                return text
            _raise_compatible(
                exc,
                provider_name=self._metadata.provider_name,
                credential_env=self._credential_env,
                timeout_seconds=self._timeout_seconds,
            )

    def _invoke_responses(
        self,
        *,
        instructions: str,
        user_content: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> str:
        attempts = self._max_retries + 1
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                response = self._client.responses.create(
                    model=self._metadata.model_name,
                    instructions=instructions,
                    input=user_content,
                    stream=False,
                    timeout=self._timeout_seconds,
                    text={
                        "format": {
                            "type": "json_schema",
                            "name": schema_name,
                            "strict": True,
                            "schema": schema,
                        }
                    },
                )
                output_text = getattr(response, "output_text", None)
                if not output_text or not str(output_text).strip():
                    msg = "API response contained no output text."
                    raise ProviderResponseError(
                        self._metadata.provider_name,
                        msg,
                        source="api",
                        failure_kind="api_failure",
                    )
                return str(output_text)
            except ProviderResponseError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt + 1 >= attempts:
                    raise
        if last_exc is not None:
            raise last_exc
        msg = "API call failed with no response."
        raise ProviderResponseError(
            self._metadata.provider_name,
            msg,
            source="api",
            failure_kind="api_failure",
        )

    def _invoke_chat(
        self,
        *,
        instructions: str,
        user_content: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> str:
        system_content = self._chat_system_content(instructions, schema_name, schema)
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]
        attempts = self._max_retries + 1
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                response = self._client.chat.completions.create(
                    model=self._metadata.model_name,
                    messages=cast(Any, messages),
                    temperature=0,
                    stream=False,
                    timeout=self._timeout_seconds,
                )
                choice = response.choices[0] if response.choices else None
                content = choice.message.content if choice and choice.message else None
                if not content or not str(content).strip():
                    msg = "Chat completion returned no message content."
                    raise ProviderResponseError(
                        self._metadata.provider_name,
                        msg,
                        source="api",
                        failure_kind="api_failure",
                    )
                return str(content)
            except ProviderResponseError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt + 1 >= attempts:
                    _raise_compatible(
                        exc,
                        provider_name=self._metadata.provider_name,
                        credential_env=self._credential_env,
                        timeout_seconds=self._timeout_seconds,
                    )
        if last_exc is not None:
            _raise_compatible(
                last_exc,
                provider_name=self._metadata.provider_name,
                credential_env=self._credential_env,
                timeout_seconds=self._timeout_seconds,
            )
        msg = "Chat API call failed with no response."
        raise ProviderResponseError(
            self._metadata.provider_name,
            msg,
            source="api",
            failure_kind="api_failure",
        )

    @staticmethod
    def _chat_system_content(
        instructions: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> str:
        schema_text = json.dumps(schema, indent=2, ensure_ascii=False)
        return (
            f"{instructions}{JSON_ONLY_SUFFIX}\n\n"
            f"JSON schema name: {schema_name}\n"
            f"JSON schema:\n{schema_text}"
        )

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
