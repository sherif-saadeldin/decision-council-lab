from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from rich.console import Console

from council.chat import ChatSession, ChatSessionState, build_chat_context
from council.config import Settings
from council.models import CouncilRunResult, DecisionDossier, DecisionType
from council.providers.models import ProviderMetadata
from council.sources.scanner import ScanLimits, scan_source_input
from council.sources.relevance import rank_source_pack
from council.sources.models import SourceQueryContext
from council.sources.models import SourceRelevanceRecord
from council.sources.service import SourceService
from council.sources.source_context_builder import SourceContextCaps, build_source_context
from council.storage import save_run
from main import main


def _run_result(run_id: str = "src-run") -> CouncilRunResult:
    return CouncilRunResult(
        dossier=DecisionDossier(
            run_id=run_id,
            decision_question="Should we ship this?",
            decision_type=DecisionType.PROCEED_WITH_CONSTRAINTS,
            direct_answer="Proceed with constraints and a short pilot window.",
            recommendation="Proceed with constraints.",
            why_this_decision=["Evidence exists", "Scope is narrow", "Rollback is easy"],
            what_would_change_mind=["Usage drops", "Cost spikes", "Support load rises"],
            next_actions=["Run pilot", "Measure", "Review"],
            do_not_do=["Skip review", "Expand early", "Ignore metrics"],
            approval_gate="Approval is required before broad rollout.",
            confidence_score=0.7,
        ),
        provider_metadata=ProviderMetadata(
            provider_name="mock",
            model_name="mock-council-v1",
            mode="mock",
            supports_structured_output=True,
            supports_streaming=False,
        ),
        council_mode="multi",
    )


def test_scan_folder_collects_supported_files(tmp_path: Path) -> None:
    (tmp_path / "readme.md").write_text("# Hello\nworld\n", encoding="utf-8")
    (tmp_path / "script.py").write_text("def run():\n    return True\n", encoding="utf-8")
    pack = scan_source_input(
        source_pack_id="pack-1",
        name="demo",
        source_path=tmp_path,
    )
    assert pack.file_count == 2
    assert ".md" in pack.included_extensions
    assert ".py" in pack.included_extensions


def test_scan_ignores_forbidden_folders(tmp_path: Path) -> None:
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "skip.js").write_text("export const x = 1", encoding="utf-8")
    (tmp_path / "keep.ts").write_text("export function ok() { return true }", encoding="utf-8")
    pack = scan_source_input(
        source_pack_id="pack-2",
        name="demo",
        source_path=tmp_path,
    )
    assert pack.file_count == 1
    assert any("ignored directory" in item for item in pack.ignored_files)


def test_scan_skips_binary_files(tmp_path: Path) -> None:
    bad = tmp_path / "bad.txt"
    bad.write_bytes(b"\x00\x01\x02")
    pack = scan_source_input(
        source_pack_id="pack-3",
        name="demo",
        source_path=tmp_path,
    )
    assert pack.file_count == 0
    assert any("binary" in item for item in pack.ignored_files)


def test_max_file_size_enforced(tmp_path: Path) -> None:
    data = "x" * 100
    (tmp_path / "large.md").write_text(data, encoding="utf-8")
    pack = scan_source_input(
        source_pack_id="pack-4",
        name="demo",
        source_path=tmp_path,
        limits=ScanLimits(max_file_bytes=10),
    )
    assert pack.file_count == 0
    assert any("too large" in item for item in pack.ignored_files)


def test_secret_redaction_in_snippets(tmp_path: Path) -> None:
    (tmp_path / "secrets.txt").write_text(
        "OPENAI_API_KEY=sk-1234567890\npassword=letmein\n", encoding="utf-8"
    )
    pack = scan_source_input(
        source_pack_id="pack-5",
        name="demo",
        source_path=tmp_path,
    )
    snippet_text = "\n".join(s.text for s in pack.summaries[0].snippets)
    assert "[REDACTED]" in snippet_text
    assert "sk-1234567890" not in snippet_text


def test_source_pack_storage_round_trip(tmp_path: Path) -> None:
    source_path = tmp_path / "src"
    source_path.mkdir()
    (source_path / "a.md").write_text("# A\nhello", encoding="utf-8")
    service = SourceService(base_dir=tmp_path / ".dcouncil" / "sources")
    saved = service.scan_and_save(source_path, name="docs")
    loaded = service.load(saved.source_pack_id)
    assert loaded.source_pack_id == saved.source_pack_id
    assert service.remove(saved.source_pack_id) is True


def test_source_context_is_capped(tmp_path: Path) -> None:
    source_path = tmp_path / "src"
    source_path.mkdir()
    (source_path / "a.md").write_text("# A\n" + ("word " * 400), encoding="utf-8")
    service = SourceService(base_dir=tmp_path / ".dcouncil" / "sources")
    saved = service.scan_and_save(source_path, name="docs")
    payload = service.build_context(
        source_pack_ids=[saved.source_pack_id],
        context_char_limit=120,
    )
    assert len(payload.summary) <= 120
    assert "[context truncated]" in payload.summary or payload.excluded_files


def test_run_persists_source_metadata_and_markdown_section(tmp_path: Path) -> None:
    result = _run_result("src-run-1").model_copy(
        update={
            "source_pack_ids": ["pack-1"],
            "source_context_summary": "Using docs/source.md for constraints.",
            "source_relevance": [],
        }
    )
    settings = Settings(llm_mode="mock", runs_dir=tmp_path, mock_model="mock-council-v1")
    save_run(result, settings=settings)
    run_dir = tmp_path / "src-run-1"
    payload = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert payload["source_pack_ids"] == ["pack-1"]
    assert "source_context_summary" in payload
    markdown = (run_dir / "run.md").read_text(encoding="utf-8")
    assert "## Sources Used" in markdown
    assert "## Why These Sources Were Prioritized" not in markdown


def test_chat_source_commands(mock_settings, monkeypatch) -> None:
    monkeypatch.setattr("council.chat.load_config_file", lambda path=None: None)
    source_root = mock_settings.runs_dir / "src"
    source_root.mkdir(parents=True, exist_ok=True)
    (source_root / "a.md").write_text("# Title\nhello", encoding="utf-8")
    monkeypatch.chdir(mock_settings.runs_dir)
    ctx = build_chat_context(mock_settings, config_profile_name=None)
    out = StringIO()
    session = ChatSession(
        console=Console(file=out, force_terminal=True, width=120),
        error_console=Console(file=StringIO(), force_terminal=True, width=120),
        ctx=ctx,
        state=ChatSessionState(),
        confirm_fn=lambda _m, _d: False,
    )
    session.handle_line(f"/source scan {source_root}")
    assert session.state.active_source_pack_ids
    active = session.state.active_source_pack_ids[0]
    session.handle_line("/sources")
    session.handle_line(f"/source show {active}")
    session.handle_line("/source query architecture")
    session.handle_line("/source clear")
    assert session.state.active_source_pack_ids == []


def test_relevance_ranking_prefers_filename_and_keyword_matches(tmp_path: Path) -> None:
    (tmp_path / "ARCHITECTURE.md").write_text("# service layer\nchat flow council", encoding="utf-8")
    (tmp_path / "notes.md").write_text("# random\nmisc text", encoding="utf-8")
    pack = scan_source_input(source_pack_id="rank-1", name="rank", source_path=tmp_path)
    ranked = rank_source_pack(
        pack,
        SourceQueryContext(question="council service layer architecture chat flow"),
    )
    assert ranked[0].summary.path == "ARCHITECTURE.md"
    assert ranked[0].score >= ranked[1].score
    assert "filename match" in ranked[0].reasons


def test_relevance_heading_weighting(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# Deployment plan\nother", encoding="utf-8")
    (tmp_path / "b.md").write_text("# Notes\nother", encoding="utf-8")
    pack = scan_source_input(source_pack_id="rank-2", name="rank", source_path=tmp_path)
    ranked = rank_source_pack(
        pack,
        SourceQueryContext(question="deployment plan"),
    )
    assert ranked[0].summary.path == "a.md"
    assert "heading/title match" in ranked[0].reasons


def test_exact_phrase_boost(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# Topic\nservice layer boundaries", encoding="utf-8")
    (tmp_path / "b.md").write_text("# Topic\nservice boundaries", encoding="utf-8")
    pack = scan_source_input(source_pack_id="rank-3", name="rank", source_path=tmp_path)
    ranked = rank_source_pack(
        pack,
        SourceQueryContext(question="service layer boundaries"),
    )
    assert ranked[0].summary.path == "a.md"
    assert "exact phrase boost" in ranked[0].reasons


def test_strategic_docs_are_prioritized_over_tests_by_default(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Product vision\nroadmap and plan", encoding="utf-8")
    (tmp_path / "tests_feature.md").write_text("# tests\nroadmap and plan", encoding="utf-8")
    pack = scan_source_input(source_pack_id="rank-6", name="rank", source_path=tmp_path)
    ranked = rank_source_pack(
        pack,
        SourceQueryContext(question="product roadmap plan"),
    )
    assert ranked[0].summary.path == "README.md"


def test_context_builder_deduplicates_snippets(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# A\nsame line\nsame line\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("# B\nsame line\nsame line\n", encoding="utf-8")
    pack = scan_source_input(source_pack_id="rank-4", name="rank", source_path=tmp_path)
    built = build_source_context(
        packs=[pack],
        query=SourceQueryContext(question="same line"),
        caps=SourceContextCaps(max_snippets=2, max_files=2, max_chars=500),
    )
    snippets = [snippet for item in built.relevance for snippet in item.snippets]
    assert len(snippets) <= 2
    assert len(set(snippets)) == len(snippets)


def test_explainability_metadata_present(tmp_path: Path) -> None:
    (tmp_path / "roadmap.md").write_text("# roadmap\nimplementation plan", encoding="utf-8")
    pack = scan_source_input(source_pack_id="rank-5", name="rank", source_path=tmp_path)
    payload = SourceService(base_dir=tmp_path / ".dcouncil" / "sources")
    payload.save(pack)
    context = payload.build_context(
        source_pack_ids=[pack.source_pack_id],
        question="implementation roadmap plan",
    )
    assert context.relevance
    top = context.relevance[0]
    assert top.score >= 0.0
    assert top.why_selected
    assert top.matched_terms


def test_cli_sources_query_command(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "docs"
    src.mkdir()
    (src / "ARCHITECTURE.md").write_text("# service layer\nchat flow", encoding="utf-8")
    assert main(["sources", "scan", str(src), "--name", "docs"]) == 0
    capsys.readouterr()
    source_pack_id = SourceService().list_packs()[0].source_pack_id
    code = main(["sources", "query", source_pack_id, "service layer chat"])
    assert code == 0
    text = capsys.readouterr().out
    assert "Source relevance query" in text
    assert "relevance:" in text


def test_markdown_relevance_section_rendered(tmp_path: Path) -> None:
    result = _run_result("src-run-2").model_copy(
        update={
            "source_pack_ids": ["pack-1"],
            "source_context_summary": "Using source context",
            "source_relevance": [
                SourceRelevanceRecord(
                    source_pack_id="pack-1",
                    path="ARCHITECTURE.md",
                    extension=".md",
                    score=0.91,
                    matched_terms=["council", "service"],
                    why_selected=["keyword overlap"],
                    snippets=["service layer"],
                )
            ],
            "source_excluded_files": ["notes.md (max files cap)"],
        }
    )
    settings = Settings(llm_mode="mock", runs_dir=tmp_path, mock_model="mock-council-v1")
    save_run(result, settings=settings)
    markdown = (tmp_path / "src-run-2" / "run.md").read_text(encoding="utf-8")
    assert "## Why These Sources Were Prioritized" in markdown
    assert "ARCHITECTURE.md" in markdown
    assert "relevance:" in markdown

