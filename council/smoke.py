from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from council.config import Settings
from council.doctor import CheckStatus, DoctorCheck, run_doctor
from council.engine import run_council
from council.model_presets import apply_preset, get_preset
from council.models import CouncilRunResult
from council.progress import NullProgressReporter, StageTrackingProgress
from council.providers.errors import ProviderResponseError
from council.providers.failures import classify_provider_failure
from council.credentials import redaction_secrets, strip_ollama_dummy_from_text
from council.provider_availability import credential_source_for_preset
from council.redaction import redact_secrets
from council.providers.api_mode import normalize_api_mode
from council.runtime import DEFAULT_SMOKE_MAX_RUN_SECONDS, DEFAULT_TIMEOUT_SECONDS, RuntimeOptions
from council.storage import save_run

DEFAULT_SMOKE_QUESTION = (
    "Smoke test: Is the decision council pipeline responding with a valid structured dossier?"
)

RunCouncilFn = Callable[..., tuple[CouncilRunResult, object | None]]
SaveRunFn = Callable[[CouncilRunResult, Settings | None], tuple[Path, Path]]
DoctorFn = Callable[..., list[DoctorCheck]]


@dataclass(frozen=True)
class SmokeRequest:
    preset: str
    question: str = DEFAULT_SMOKE_QUESTION
    runs_dir: Path | None = None
    debate_rounds: int = 0
    timeout_seconds: float | None = None
    repair_json: bool = False
    api_mode: str = "auto"
    skip_preflight: bool = False


class SmokeReport(BaseModel):
    success: bool
    preset: str
    question: str
    provider_name: str | None = None
    model_name: str | None = None
    elapsed_seconds: float = 0.0
    run_id: str | None = None
    run_json_path: str | None = None
    run_md_path: str | None = None
    decision_type: str | None = None
    confidence_score: float | None = None
    has_evidence_gaps: bool = False
    has_proposed_metrics: bool = False
    error: str | None = None
    failure_reason: str | None = None
    failed_stage: str | None = None
    api_mode_preference: str | None = None
    api_mode_used: str | None = None
    auth_failure: bool = False
    credential_source: str | None = None


def run_smoke_preflight(
    settings: Settings,
    runtime: RuntimeOptions,
    *,
    doctor_fn: DoctorFn = run_doctor,
) -> tuple[bool, str | None, str | None]:
    if settings.llm_mode == "mock":
        return True, None, None
    checks = doctor_fn(settings, runtime=runtime)
    blocking = [check for check in checks if check.status == CheckStatus.FAIL]
    if not blocking:
        return True, None, None
    first = blocking[0]
    return False, first.name, first.message


def run_smoke(
    request: SmokeRequest,
    *,
    run_council_fn: RunCouncilFn = run_council,
    save_run_fn: SaveRunFn = save_run,
    doctor_fn: DoctorFn = run_doctor,
) -> SmokeReport:
    """Run a single live-provider smoke check. Intended for manual CLI use only."""
    preset_meta = get_preset(request.preset)
    started = time.perf_counter()
    settings: Settings | None = None
    progress = StageTrackingProgress(NullProgressReporter())
    try:
        settings = _resolve_smoke_settings(request)
        runtime = RuntimeOptions(
            timeout_seconds=request.timeout_seconds or DEFAULT_TIMEOUT_SECONDS,
            max_retries=0,
            fast_mode=True,
            show_progress=False,
            repair_json=request.repair_json,
            api_mode=normalize_api_mode(request.api_mode),
            max_run_seconds=DEFAULT_SMOKE_MAX_RUN_SECONDS,
        )
        if not request.skip_preflight:
            progress.on_stage("preflight")
            ok, failed_stage, message = run_smoke_preflight(
                settings,
                runtime,
                doctor_fn=doctor_fn,
            )
            if not ok:
                elapsed = time.perf_counter() - started
                return SmokeReport(
                    success=False,
                    preset=request.preset,
                    question=request.question.strip(),
                    provider_name=preset_meta.provider_name,
                    model_name=preset_meta.model,
                    elapsed_seconds=elapsed,
                    error=message,
                    failure_reason="preflight_failure",
                    failed_stage=failed_stage,
                    api_mode_preference=normalize_api_mode(request.api_mode),
                )

        progress.on_stage("council")
        result, _ = run_council_fn(
            request.question.strip(),
            settings=settings,
            debate_rounds=max(0, request.debate_rounds),
            runtime=runtime,
            progress=progress,
        )
        progress.on_stage("save")
        json_path, md_path = save_run_fn(result, settings)
        elapsed = time.perf_counter() - started
        dossier = result.dossier
        meta = result.provider_metadata
        return SmokeReport(
            success=True,
            preset=request.preset,
            question=request.question.strip(),
            provider_name=meta.provider_name,
            model_name=meta.model_name,
            elapsed_seconds=elapsed,
            run_id=dossier.run_id,
            run_json_path=str(json_path),
            run_md_path=str(md_path),
            decision_type=dossier.decision_type.value,
            confidence_score=dossier.confidence_score,
            has_evidence_gaps=_has_evidence_gaps(result),
            has_proposed_metrics=_has_proposed_metrics(result),
            api_mode_preference=meta.api_mode_preference,
            api_mode_used=meta.api_mode_used,
        )
    except Exception as exc:  # noqa: BLE001
        elapsed = time.perf_counter() - started
        settings_for_redaction = settings or _resolve_smoke_settings(request)
        failure_reason = classify_provider_failure(exc)
        auth_failure = failure_reason == "auth_failure"
        cred_source: str | None = None
        if auth_failure:
            cred_source = credential_source_for_preset(request.preset, settings_for_redaction)
        return SmokeReport(
            success=False,
            preset=request.preset,
            question=request.question.strip(),
            provider_name=preset_meta.provider_name,
            model_name=preset_meta.model,
            elapsed_seconds=elapsed,
            error=_safe_error_message(exc, settings_for_redaction),
            failure_reason=failure_reason,
            failed_stage=progress.current_stage,
            api_mode_preference=normalize_api_mode(request.api_mode),
            auth_failure=auth_failure,
            credential_source=cred_source,
        )


def _resolve_smoke_settings(request: SmokeRequest) -> Settings:
    base = Settings.from_env()
    if request.runs_dir is not None:
        base = _settings_with_runs_dir(base, request.runs_dir)
    return apply_preset(base, request.preset)


def _has_evidence_gaps(result: CouncilRunResult) -> bool:
    if result.dossier.evidence_gaps:
        return True
    return any(brief.evidence_gaps for brief in result.agent_briefs)


def _has_proposed_metrics(result: CouncilRunResult) -> bool:
    if result.dossier.proposed_metrics:
        return True
    return any(brief.proposed_metrics for brief in result.agent_briefs)


def _safe_error_message(exc: Exception, settings: Settings) -> str:
    if isinstance(exc, ProviderResponseError) and exc.source == "api":
        message = exc.detail
    else:
        message = str(exc)
    return strip_ollama_dummy_from_text(redact_secrets(message, redaction_secrets(settings)))


def _settings_with_runs_dir(settings: Settings, runs_dir: Path) -> Settings:
    return Settings(
        llm_mode=settings.llm_mode,
        runs_dir=runs_dir,
        mock_model=settings.mock_model,
        openai_api_key=settings.openai_api_key,
        openai_model=settings.openai_model,
        llm_provider_name=settings.llm_provider_name,
        llm_base_url=settings.llm_base_url,
        llm_api_key=settings.llm_api_key,
        llm_model=settings.llm_model,
    )
