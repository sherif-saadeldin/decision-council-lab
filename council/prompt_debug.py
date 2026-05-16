from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from council.models import CouncilRunResult
from council.redaction import redact_secrets


@dataclass
class PromptDebugEntry:
    step: str
    instructions: str
    user_content: str
    role: str | None = None


@dataclass
class PromptDebugCollector:
    entries: list[PromptDebugEntry] = field(default_factory=list)

    def record(
        self,
        *,
        step: str,
        instructions: str,
        user_content: str,
        role: str | None = None,
    ) -> None:
        self.entries.append(
            PromptDebugEntry(
                step=step,
                role=role,
                instructions=instructions,
                user_content=user_content,
            )
        )


def format_prompt_debug_markdown(
    result: CouncilRunResult,
    collector: PromptDebugCollector,
    *,
    secrets: list[str] | None = None,
) -> str:
    lines = [
        "# Prompt Debug",
        "",
        f"- **Run ID:** `{result.dossier.run_id}`",
        f"- **Provider:** {result.provider_metadata.provider_name}",
        f"- **Model:** {result.provider_metadata.model_name}",
        "",
        "> Debug capture only. No API keys or secrets are stored here.",
        "",
    ]

    for index, entry in enumerate(collector.entries, start=1):
        title = entry.step if entry.role is None else f"{entry.step} ({entry.role})"
        lines.extend(
            [
                f"## Step {index}: {title}",
                "",
                "### Instructions",
                "",
                "```text",
                redact_secrets(entry.instructions, secrets),
                "```",
                "",
                "### User content",
                "",
                "```text",
                redact_secrets(entry.user_content, secrets),
                "```",
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def save_prompt_debug(
    result: CouncilRunResult,
    collector: PromptDebugCollector,
    runs_dir: Path,
    *,
    secrets: list[str] | None = None,
) -> Path:
    run_dir = runs_dir / result.dossier.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "prompt_debug.md"
    path.write_text(
        format_prompt_debug_markdown(result, collector, secrets=secrets),
        encoding="utf-8",
    )
    return path
