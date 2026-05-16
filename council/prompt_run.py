from __future__ import annotations

from council.models import CouncilRunResult, PromptRunMetadata
from council.prompt_loader import (
    build_prompt_versions,
    profile_bundle_hash,
    profile_prompt_records,
)


def build_prompt_run_metadata(system_profile: str) -> PromptRunMetadata:
    records = profile_prompt_records(system_profile)
    return PromptRunMetadata(
        system_profile=system_profile,
        prompt_versions=build_prompt_versions(system_profile),
        prompt_files=[record.relative_name for record in records],
        prompt_hash=profile_bundle_hash(system_profile),
    )


def attach_prompt_metadata(
    result: CouncilRunResult,
    *,
    system_profile: str,
) -> CouncilRunResult:
    return result.model_copy(
        update={"prompt_metadata": build_prompt_run_metadata(system_profile)}
    )
