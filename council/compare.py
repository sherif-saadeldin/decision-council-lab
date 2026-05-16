from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from council.config import Settings
from council.config_profiles import (
    ConfigProfile,
    CouncilConfigFile,
    load_config_file,
    resolve_debate_rounds_with_profile,
    resolve_runtime_with_profile,
    resolve_settings_with_profile,
)
from council.engine import run_council
from council.model_presets import apply_preset
from council.models import CouncilRunResult, DecisionDossier, DecisionType, DebateTranscript
from council.progress import NullProgressReporter
from council.providers.errors import ProviderResponseError
from council.redaction import redact_secrets
from council.runtime import RuntimeOptions
from council.storage import save_run

COMPARISON_SCHEMA_VERSION = "1.0"
TOP_RISKS_COUNT = 3
TOP_ACTIONS_COUNT = 3

TargetKind = Literal["preset", "profile"]

_DECISION_RANK: dict[DecisionType, int] = {
    DecisionType.PROCEED: 4,
    DecisionType.PROCEED_WITH_CONSTRAINTS: 3,
    DecisionType.PAUSE: 2,
    DecisionType.REJECT: 1,
}


class CompareConfigError(ValueError):
    """Raised when compare/benchmark is invoked without valid targets."""


@dataclass(frozen=True)
class ComparisonTarget:
    kind: TargetKind
    name: str


@dataclass(frozen=True)
class CompareRequest:
    question: str
    targets: tuple[ComparisonTarget, ...]
    runs_dir: Path | None = None
    debate_rounds: int | None = None
    timeout_seconds: float | None = None
    max_retries: int | None = None
    fast: bool = False
    fast_explicit: bool = False


class ComparisonRunEntry(BaseModel):
    label: str
    kind: TargetKind
    success: bool
    run_id: str | None = None
    run_json_path: str | None = None
    run_md_path: str | None = None
    error: str | None = None
    decision_type: str | None = None
    confidence_score: float | None = None
    deciding_factor: str | None = None
    top_risks: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    disagreement_summary: str | None = None
    provider_name: str | None = None
    model_name: str | None = None


class ComparisonEvaluator(BaseModel):
    most_decisive: str
    most_cautious: str
    most_actionable: str
    weakest_output: str
    recommended: str


class ComparisonReport(BaseModel):
    schema_version: str = COMPARISON_SCHEMA_VERSION
    comparison_id: str
    timestamp: datetime
    question: str
    presets_tested: list[str] = Field(default_factory=list)
    profiles_tested: list[str] = Field(default_factory=list)
    debate_rounds: int
    entries: list[ComparisonRunEntry] = Field(default_factory=list)
    evaluator: ComparisonEvaluator


def parse_csv_targets(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def build_targets(presets: list[str], profiles: list[str]) -> tuple[ComparisonTarget, ...]:
    if not presets and not profiles:
        msg = "At least one of --presets or --profiles is required."
        raise CompareConfigError(msg)
    targets: list[ComparisonTarget] = []
    targets.extend(ComparisonTarget(kind="preset", name=name) for name in presets)
    targets.extend(ComparisonTarget(kind="profile", name=name) for name in profiles)
    return tuple(targets)


def run_comparison(request: CompareRequest) -> tuple[ComparisonReport, Path, Path]:
    base = Settings.from_env()
    if request.runs_dir is not None:
        base = _settings_with_runs_dir(base, request.runs_dir)

    config = load_config_file()
    runtime_base = resolve_runtime_with_profile(
        None,
        cli_timeout=request.timeout_seconds,
        cli_max_retries=request.max_retries,
        cli_fast=request.fast,
        cli_fast_explicit=request.fast_explicit,
        quiet=True,
    )
    debate_default = resolve_debate_rounds_with_profile(
        None,
        cli_debate_rounds=request.debate_rounds,
        runtime=runtime_base,
    )

    comparison_id = str(uuid4())
    entries: list[ComparisonRunEntry] = []

    for target in request.targets:
        entry = _run_single_target(
            request.question,
            target,
            base=base,
            config=config,
            runtime_base=runtime_base,
            debate_rounds=debate_default,
            fast_explicit=request.fast_explicit,
        )
        entries.append(entry)

    presets_tested = [t.name for t in request.targets if t.kind == "preset"]
    profiles_tested = [t.name for t in request.targets if t.kind == "profile"]
    evaluator = _evaluate_entries(entries)

    report = ComparisonReport(
        comparison_id=comparison_id,
        timestamp=datetime.now(timezone.utc),
        question=request.question.strip(),
        presets_tested=presets_tested,
        profiles_tested=profiles_tested,
        debate_rounds=debate_default,
        entries=entries,
        evaluator=evaluator,
    )
    json_path, md_path = save_comparison(report, runs_dir=base.runs_dir)
    return report, json_path, md_path


def save_comparison(report: ComparisonReport, *, runs_dir: Path) -> tuple[Path, Path]:
    comparison_dir = runs_dir / "comparisons" / report.comparison_id
    comparison_dir.mkdir(parents=True, exist_ok=True)
    json_path = comparison_dir / "comparison.json"
    md_path = comparison_dir / "comparison.md"
    json_path.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(_format_comparison_markdown(report), encoding="utf-8")
    return json_path, md_path


def _run_single_target(
    question: str,
    target: ComparisonTarget,
    *,
    base: Settings,
    config: CouncilConfigFile | None,
    runtime_base: RuntimeOptions,
    debate_rounds: int,
    fast_explicit: bool,
) -> ComparisonRunEntry:
    label = target.name
    try:
        settings, profile, runtime, rounds = _resolve_target(
            target,
            base=base,
            config=config,
            runtime_base=runtime_base,
            debate_rounds=debate_rounds,
            fast_explicit=fast_explicit,
        )
        result, _ = run_council(
            question,
            settings=settings,
            debate_rounds=rounds,
            runtime=runtime,
            progress=NullProgressReporter(),
        )
        json_path, md_path = save_run(result, settings=settings)
        return _entry_from_result(
            target,
            result=result,
            json_path=json_path,
            md_path=md_path,
        )
    except Exception as exc:  # noqa: BLE001
        settings_for_redaction = base
        try:
            settings_for_redaction, _, _, _ = _resolve_target(
                target,
                base=base,
                config=config,
                runtime_base=runtime_base,
                debate_rounds=debate_rounds,
                fast_explicit=fast_explicit,
            )
        except Exception:  # noqa: BLE001
            pass
        return ComparisonRunEntry(
            label=label,
            kind=target.kind,
            success=False,
            error=_safe_error_message(exc, settings_for_redaction),
        )


def _resolve_target(
    target: ComparisonTarget,
    *,
    base: Settings,
    config: CouncilConfigFile | None,
    runtime_base: RuntimeOptions,
    debate_rounds: int,
    fast_explicit: bool,
) -> tuple[Settings, ConfigProfile | None, RuntimeOptions, int]:
    profile: ConfigProfile | None = None
    if target.kind == "preset":
        settings = apply_preset(base, target.name)
    else:
        if config is None:
            from council.config_profiles import ConfigProfileError

            msg = "No config file found. Run: python main.py config init"
            raise ConfigProfileError(msg)
        profile = config.get_profile(target.name)
        settings = resolve_settings_with_profile(base, profile=profile, cli_preset=None)

    if profile is not None:
        runtime = resolve_runtime_with_profile(
            profile,
            cli_timeout=runtime_base.timeout_seconds,
            cli_max_retries=runtime_base.max_retries,
            cli_fast=runtime_base.fast_mode,
            cli_fast_explicit=fast_explicit,
            quiet=True,
        )
    else:
        runtime = runtime_base
    rounds = resolve_debate_rounds_with_profile(
        profile,
        cli_debate_rounds=debate_rounds,
        runtime=runtime,
    )
    return settings, profile, runtime, rounds


def _entry_from_result(
    target: ComparisonTarget,
    *,
    result: CouncilRunResult,
    json_path: Path,
    md_path: Path,
) -> ComparisonRunEntry:
    dossier = result.dossier
    return ComparisonRunEntry(
        label=target.name,
        kind=target.kind,
        success=True,
        run_id=dossier.run_id,
        run_json_path=str(json_path),
        run_md_path=str(md_path),
        decision_type=dossier.decision_type.value,
        confidence_score=dossier.confidence_score,
        deciding_factor=dossier.deciding_factor,
        top_risks=dossier.risks[:TOP_RISKS_COUNT],
        next_actions=dossier.next_actions[:TOP_ACTIONS_COUNT],
        evidence_gaps=dossier.evidence_gaps,
        disagreement_summary=_disagreement_summary(dossier, result.debate_transcript),
        provider_name=result.provider_metadata.provider_name,
        model_name=result.provider_metadata.model_name,
    )


def _disagreement_summary(
    dossier: DecisionDossier,
    transcript: DebateTranscript | None,
) -> str:
    parts: list[str] = []
    if dossier.disagreement_resolution.strip():
        parts.append(dossier.disagreement_resolution.strip())
    if transcript and transcript.final_unresolved_disagreements:
        parts.extend(transcript.final_unresolved_disagreements[:3])
    if not parts:
        return "No major disagreements recorded."
    return "; ".join(parts)


def _evaluate_entries(entries: list[ComparisonRunEntry]) -> ComparisonEvaluator:
    successful = [entry for entry in entries if entry.success]
    if not successful:
        na = "n/a (no successful runs)"
        return ComparisonEvaluator(
            most_decisive=na,
            most_cautious=na,
            most_actionable=na,
            weakest_output=na,
            recommended=na,
        )

    def decisiveness(entry: ComparisonRunEntry) -> tuple[float, int]:
        decision = DecisionType(entry.decision_type or DecisionType.PAUSE.value)
        return (entry.confidence_score or 0.0, _DECISION_RANK.get(decision, 0))

    def caution(entry: ComparisonRunEntry) -> tuple[float, int]:
        decision = DecisionType(entry.decision_type or DecisionType.PAUSE.value)
        return (entry.confidence_score or 0.0, _DECISION_RANK.get(decision, 0))

    most_decisive_entry = max(successful, key=decisiveness)
    most_cautious_entry = min(successful, key=caution)
    most_actionable_entry = max(successful, key=lambda e: len(e.next_actions))
    weakest_entry = min(
        successful,
        key=lambda e: (
            e.confidence_score or 0.0,
            -len(e.evidence_gaps),
        ),
    )
    recommended_label = _target_label(most_decisive_entry)

    return ComparisonEvaluator(
        most_decisive=_target_label(most_decisive_entry),
        most_cautious=_target_label(most_cautious_entry),
        most_actionable=_target_label(most_actionable_entry),
        weakest_output=_target_label(weakest_entry),
        recommended=recommended_label,
    )


def _target_label(entry: ComparisonRunEntry) -> str:
    return f"{entry.label} ({entry.kind})"


def _safe_error_message(exc: Exception, settings: Settings) -> str:
    if isinstance(exc, ProviderResponseError) and exc.source == "api":
        message = exc.detail
    else:
        message = str(exc)
    secrets = [key for key in (settings.openai_api_key, settings.llm_api_key) if key]
    return redact_secrets(message, secrets)


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


def _format_comparison_markdown(report: ComparisonReport) -> str:
    lines = [
        "# Council Comparison",
        "",
        f"- **Comparison ID:** {report.comparison_id}",
        f"- **Timestamp:** {report.timestamp.isoformat()}",
        f"- **Schema:** {report.schema_version}",
        "",
        "## Question",
        "",
        report.question,
        "",
        "## Targets",
        "",
        f"- **Presets:** {', '.join(report.presets_tested) or '(none)'}",
        f"- **Profiles:** {', '.join(report.profiles_tested) or '(none)'}",
        f"- **Debate rounds:** {report.debate_rounds}",
        "",
        "## Results",
        "",
    ]

    for entry in report.entries:
        status = "success" if entry.success else "failed"
        lines.append(f"### {entry.label} ({entry.kind}) — {status}")
        lines.append("")
        if entry.success:
            confidence_pct = f"{(entry.confidence_score or 0) * 100:.0f}%"
            lines.extend(
                [
                    f"- **Decision type:** {entry.decision_type}",
                    f"- **Confidence:** {confidence_pct}",
                    f"- **Deciding factor:** {entry.deciding_factor or '—'}",
                    f"- **Provider:** {entry.provider_name} / {entry.model_name}",
                    f"- **Run ID:** {entry.run_id}",
                    f"- **Artifacts:** `{entry.run_json_path}`, `{entry.run_md_path}`",
                ]
            )
            _bullet_subsection(lines, "Top risks", entry.top_risks)
            _bullet_subsection(lines, "Next actions", entry.next_actions)
            _bullet_subsection(lines, "Evidence gaps", entry.evidence_gaps)
            lines.extend(
                [
                    "",
                    f"**Disagreement summary:** {entry.disagreement_summary or '—'}",
                    "",
                ]
            )
        else:
            lines.extend([f"- **Error:** {entry.error or 'Unknown error'}", ""])

    ev = report.evaluator
    lines.extend(
        [
            "## Evaluator (rule-based)",
            "",
            f"- **Most decisive:** {ev.most_decisive}",
            f"- **Most cautious:** {ev.most_cautious}",
            f"- **Most actionable:** {ev.most_actionable}",
            f"- **Weakest output:** {ev.weakest_output}",
            f"- **Recommended for future use:** {ev.recommended}",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _bullet_subsection(lines: list[str], heading: str, items: list[str]) -> None:
    if not items:
        return
    lines.append(f"- **{heading}:**")
    lines.extend(f"  - {item}" for item in items)
