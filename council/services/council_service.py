from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from council.config import Settings
from council.config_profiles import ConfigProfile, resolve_debate_rounds_with_profile
from council.costing import enforce_cost_budget
from council.council_session import (
    CouncilSessionPlan,
    CouncilSessionRequest,
    CouncilSessionResult,
    plan_council_session,
    run_council_session,
)
from council.decision_thread import DecisionContext
from council.engine import run_council
from council.intake import DecisionIntake, mode_profile
from council.models import CouncilRunResult
from council.progress import NullProgressReporter, ProgressReporter
from council.prompt_debug import PromptDebugCollector, save_prompt_debug
from council.routing_modes import resolve_debate_rounds
from council.runtime import RuntimeOptions
from council.services.pack_service import PackService
from council.storage.run_store import RunArtifacts, RunStore
from council.sources.models import SourceRelevanceRecord


RunCouncilFn = Callable[..., tuple[CouncilRunResult, PromptDebugCollector | None]]
RunCouncilSessionFn = Callable[..., CouncilSessionResult]


@dataclass(frozen=True)
class CouncilRequest:
    question: str
    settings: Settings
    runtime: RuntimeOptions
    debate_rounds: int
    save_prompt_debug: bool = False
    progress: ProgressReporter | None = None
    source_pack_ids: list[str] | None = None
    source_context_summary: str = ""
    source_relevance: list[SourceRelevanceRecord] | None = None
    source_excluded_files: list[str] | None = None
    source_context_warnings: list[str] | None = None


@dataclass(frozen=True)
class CouncilExecutionResult:
    result: CouncilRunResult
    artifacts: RunArtifacts
    debug_collector: PromptDebugCollector | None = None
    prompt_debug_path: Path | None = None

    @property
    def json_path(self) -> Path:
        return self.artifacts.json_path

    @property
    def md_path(self) -> Path:
        return self.artifacts.md_path


@dataclass(frozen=True)
class MultiCouncilRequest:
    session_request: CouncilSessionRequest
    progress: ProgressReporter | None = None
    plan: CouncilSessionPlan | None = None
    create_pack: bool | None = None


@dataclass(frozen=True)
class MultiCouncilExecutionResult:
    session: CouncilSessionResult
    artifacts: RunArtifacts
    pack_paths: list[Path]
    routing_warnings: list[str]

    @property
    def result(self) -> CouncilRunResult:
        return self.session.result

    @property
    def json_path(self) -> Path:
        return self.artifacts.json_path

    @property
    def md_path(self) -> Path:
        return self.artifacts.md_path


@dataclass(frozen=True)
class ChatCouncilRequest:
    question: str
    routing_mode: str
    settings: Settings
    runtime: RuntimeOptions
    config_profile: ConfigProfile | None = None
    parent_context: DecisionContext | None = None
    thread_id: str | None = None
    intake: DecisionIntake | None = None
    council_runner: RunCouncilSessionFn | None = None
    source_pack_ids: list[str] | None = None
    source_context_summary: str = ""
    source_relevance: list[SourceRelevanceRecord] | None = None
    source_excluded_files: list[str] | None = None
    source_context_warnings: list[str] | None = None


@dataclass(frozen=True)
class ChatCouncilExecutionResult:
    session: CouncilSessionResult
    artifacts: RunArtifacts

    @property
    def result(self) -> CouncilRunResult:
        return self.session.result

    @property
    def json_path(self) -> Path:
        return self.artifacts.json_path

    @property
    def md_path(self) -> Path:
        return self.artifacts.md_path


class CouncilService:
    def __init__(
        self,
        store: RunStore,
        *,
        run_council_fn: RunCouncilFn = run_council,
        run_council_session_fn: RunCouncilSessionFn = run_council_session,
    ) -> None:
        self._store = store
        self._run_council = run_council_fn
        self._run_council_session = run_council_session_fn

    def run_standard(self, request: CouncilRequest) -> CouncilExecutionResult:
        question = request.question
        if request.source_context_summary.strip():
            question = (
                "Source context:\n"
                f"{request.source_context_summary.strip()}\n\n"
                f"Question:\n{request.question}"
            )
        result, debug_collector = self._run_council(
            question,
            settings=request.settings,
            debate_rounds=request.debate_rounds,
            save_prompt_debug=request.save_prompt_debug,
            runtime=request.runtime,
            progress=request.progress,
        )
        if (
            request.source_pack_ids
            or request.source_context_summary.strip()
            or request.source_relevance
            or request.source_excluded_files
            or request.source_context_warnings
        ):
            result = result.model_copy(
                update={
                    "source_pack_ids": list(request.source_pack_ids or []),
                    "source_context_summary": request.source_context_summary.strip(),
                    "source_relevance": list(request.source_relevance or []),
                    "source_excluded_files": list(request.source_excluded_files or []),
                    "source_context_warnings": list(request.source_context_warnings or []),
                }
            )
        if request.progress is not None and request.runtime.show_progress:
            request.progress.on_stage("storage")
        artifacts = self._store.save_run(result)
        prompt_debug_path = None
        if request.save_prompt_debug and debug_collector is not None:
            secrets = [
                key
                for key in (request.settings.openai_api_key, request.settings.llm_api_key)
                if key
            ]
            prompt_debug_path = save_prompt_debug(
                result,
                debug_collector,
                request.settings.runs_dir,
                secrets=secrets,
            )
        return CouncilExecutionResult(
            result=result,
            artifacts=artifacts,
            debug_collector=debug_collector,
            prompt_debug_path=prompt_debug_path,
        )

    def plan_multi(self, request: CouncilSessionRequest) -> CouncilSessionPlan:
        return plan_council_session(request)

    def run_multi(self, request: MultiCouncilRequest) -> MultiCouncilExecutionResult:
        session_request = request.session_request
        plan = request.plan or self.plan_multi(session_request)
        enforce_cost_budget(
            plan.cost_estimate,
            max_cost_usd=session_request.max_cost_usd,
            max_llm_calls=session_request.max_llm_calls,
            allow_over_budget=session_request.allow_over_budget,
        )
        session = self._run_council_session(
            session_request,
            progress=request.progress,
            plan=plan,
        )
        create_pack = session_request.create_pack if request.create_pack is None else request.create_pack
        pack_paths: list[Path] = []
        if create_pack:
            pack_result = PackService(self._store).generate_for_result(
                session.result,
                allow_unapproved=session_request.allow_unapproved_pack,
                resave_run_with_pack_paths=False,
            )
            pack_paths = pack_result.paths
        artifacts = self._store.save_run(
            session.result,
            implementation_pack_paths=pack_paths or None,
        )
        return MultiCouncilExecutionResult(
            session=session,
            artifacts=artifacts,
            pack_paths=pack_paths,
            routing_warnings=list(plan.routing.routing_warnings),
        )

    def run_chat_council(self, request: ChatCouncilRequest) -> ChatCouncilExecutionResult:
        session_request = self._build_chat_session_request(request)
        plan = self.plan_multi(session_request)
        enforce_cost_budget(
            plan.cost_estimate,
            max_cost_usd=session_request.max_cost_usd,
            max_llm_calls=session_request.max_llm_calls,
            allow_over_budget=session_request.allow_over_budget,
        )
        runner = request.council_runner or self._run_council_session
        session = runner(
            session_request,
            progress=NullProgressReporter(),
            plan=plan,
        )
        artifacts = self._store.save_run(session.result)
        return ChatCouncilExecutionResult(session=session, artifacts=artifacts)

    def _build_chat_session_request(
        self,
        request: ChatCouncilRequest,
    ) -> CouncilSessionRequest:
        intake_mode_profile = (
            mode_profile(request.intake.preferred_mode)
            if request.intake is not None and request.intake.preferred_mode is not None
            else None
        )
        profile_default = resolve_debate_rounds_with_profile(
            request.config_profile,
            cli_debate_rounds=None,
            runtime=request.runtime,
        )
        debate_rounds = resolve_debate_rounds(
            request.routing_mode,
            cli_debate_rounds=(
                intake_mode_profile.debate_rounds
                if intake_mode_profile is not None
                else profile_default
            ),
            max_debate_rounds=None,
        )
        context = request.parent_context
        return CouncilSessionRequest(
            question=request.question,
            routing_mode=request.routing_mode,
            debate_rounds=debate_rounds,
            base_settings=request.settings,
            runtime=request.runtime,
            prompt_create_pack=False,
            create_pack=False,
            parent_context=context,
            parent_run_id=(context.run_id if context is not None else None),
            thread_id=request.thread_id,
            intake=request.intake,
            source_pack_ids=list(request.source_pack_ids or []),
            source_context_summary=request.source_context_summary.strip(),
            source_relevance=list(request.source_relevance or []),
            source_excluded_files=list(request.source_excluded_files or []),
            source_context_warnings=list(request.source_context_warnings or []),
        )
