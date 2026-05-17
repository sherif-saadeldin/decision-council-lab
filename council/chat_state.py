from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from council.config import Settings
from council.config_profiles import ConfigProfile, CouncilConfigFile
from council.decision_thread import DecisionContext
from council.intake import DecisionIntake, DecisionMode
from council.recovery import ClassifiedFailure
from council.runtime import RuntimeOptions

DEFAULT_ROUTING_MODE = "economy"
DEFAULT_SYSTEM_PROFILE = "default"


@dataclass(frozen=True)
class DoctorCacheEntry:
    """Snapshot of the last `/doctor` invocation, used by `/status`."""

    profile_name: str
    health: str
    ran_at: datetime
    failed_check_count: int
    warn_check_count: int
    ok_check_count: int
    live: bool
    live_completion: bool
    latency_seconds: float | None = None
    summary: str = ""


@dataclass
class ChatSessionState:
    last_run_id: str | None = None
    system_profile: str = DEFAULT_SYSTEM_PROFILE
    routing_mode: str = DEFAULT_ROUTING_MODE
    config_profile_name: str | None = None
    last_doctor: DoctorCacheEntry | None = None
    last_failure: ClassifiedFailure | None = None
    last_question: str | None = None
    last_direct_answer: str | None = None
    last_decision_type: str | None = None
    last_pack_paths: list[Path] = field(default_factory=list)
    last_profile: str | None = None
    last_routing_mode: str | None = None
    current_context_run_id: str | None = None
    current_context: DecisionContext | None = None
    current_thread_id: str | None = None
    current_intake: DecisionIntake | None = None
    current_intake_field: str | None = None
    current_mode: DecisionMode | None = None
    last_intake: DecisionIntake | None = None
    active_source_pack_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ChatContext:
    settings: Settings
    config: CouncilConfigFile | None
    config_profile: ConfigProfile | None
    runtime: RuntimeOptions

