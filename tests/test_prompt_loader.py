from __future__ import annotations

from io import StringIO

import pytest
from rich.console import Console

from council.cli import render_prompts_inventory
from council.config import Settings
from council.engine import run_council
from council.models import RUN_SCHEMA_VERSION
from council.prompt_loader import (
    PromptLoadError,
    UnknownSystemProfileError,
    clear_prompt_cache,
    compose_system_prompt,
    load_prompt_file,
    load_system_profile,
    profile_bundle_hash,
    system_profile_context,
)
from council.models import AgentRole
from council.prompts import EVIDENCE_GUARDRAILS, agent_instructions, chair_instructions


@pytest.fixture(autouse=True)
def _reset_prompt_cache() -> None:
    clear_prompt_cache()
    yield
    clear_prompt_cache()


def test_prompt_files_load_with_version_and_hash() -> None:
    record = load_prompt_file("base.md")
    assert record.content
    assert record.version == "1.0.0"
    assert len(record.sha256) == 64
    assert "Decision Council" in record.content


def test_compose_system_prompt_includes_base_and_role() -> None:
    text = compose_system_prompt("research", profile_name="default")
    assert "Decision Council" in text
    assert "Research Agent" in text
    assert "global" not in text.lower() or "Global Behavior" in text


def test_missing_prompt_raises_clear_error(tmp_path) -> None:
    with pytest.raises(PromptLoadError, match="not found"):
        load_prompt_file("does-not-exist.md")


def test_unknown_profile_raises_clear_error() -> None:
    with pytest.raises(UnknownSystemProfileError, match="Unknown system profile"):
        load_system_profile("nonexistent-profile")


def test_profile_bundle_hash_is_stable() -> None:
    first = profile_bundle_hash("default")
    second = profile_bundle_hash("default")
    assert first == second
    assert len(first) == 64


def test_run_json_includes_prompt_metadata(mock_settings: Settings) -> None:
    with system_profile_context("default"):
        result, _ = run_council("Prompt metadata test?", settings=mock_settings, debate_rounds=0)
    assert result.schema_version == RUN_SCHEMA_VERSION
    meta = result.prompt_metadata
    assert meta is not None
    assert meta.system_profile == "default"
    assert "base.md" in meta.prompt_files
    assert meta.prompt_versions.get("base.md") == "1.0.0"
    assert len(meta.prompt_hash) == 64


def test_agent_instructions_use_loaded_identity() -> None:
    with system_profile_context("default"):
        text = agent_instructions(AgentRole.RESEARCH)
    assert "Research Agent" in text
    assert EVIDENCE_GUARDRAILS in text


def test_chair_instructions_include_verdict_schema() -> None:
    with system_profile_context("default"):
        text = chair_instructions()
    assert "Chair" in text
    assert "direct_answer" in text


def test_prompts_command_renders_inventory() -> None:
    buffer = StringIO()
    render_prompts_inventory(Console(file=buffer, force_terminal=True, width=120))
    output = buffer.getvalue()
    assert "base.md" in output
    assert "researcher.md" in output
    assert "Bundle hash" in output


def test_main_prompts_command(capsys) -> None:
    from main import main

    code = main(["prompts"])
    assert code == 0
    assert "base.md" in capsys.readouterr().out
