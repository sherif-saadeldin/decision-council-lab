from __future__ import annotations

from pathlib import Path

import pytest

from council.models import CouncilRunResult, DecisionDossier, DecisionType
from council.decision_thread import build_thread_meta
from council.providers.models import ProviderMetadata
from council.review_model import LifecycleState
from council.storage.run_store import FileRunStore, RunStore


def _result(run_id: str, question: str = "Should we ship?") -> CouncilRunResult:
    return CouncilRunResult(
        dossier=DecisionDossier(
            run_id=run_id,
            decision_question=question,
            decision_type=DecisionType.PROCEED_WITH_CONSTRAINTS,
            direct_answer="Proceed with constraints after a narrow pilot.",
            recommendation="Proceed with constraints.",
            next_actions=["Pilot", "Measure", "Review"],
            do_not_do=["Skip metrics", "Expand scope", "Ignore risks"],
            approval_gate="Approval required before rollout.",
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


def test_file_run_store_satisfies_protocol_and_round_trips(tmp_path: Path) -> None:
    store: RunStore = FileRunStore(tmp_path)
    saved = store.save_run(_result("store-1"))

    assert saved.json_path.is_file()
    assert saved.md_path.is_file()

    loaded = store.load_run("store-1")
    assert loaded.dossier.run_id == "store-1"
    assert loaded.provider_metadata.provider_name == "mock"
    assert store.get_run_summary("store-1").run_id == "store-1"
    assert [item.run_id for item in store.list_runs()] == ["store-1"]


def test_file_run_store_update_rewrites_same_run(tmp_path: Path) -> None:
    store = FileRunStore(tmp_path)
    result = _result("store-2")
    store.save_run(result)
    updated = result.model_copy(
        update={
            "dossier": result.dossier.model_copy(
                update={"direct_answer": "Pause until the pilot evidence is available."}
            )
        }
    )

    store.update_run("store-2", updated)

    assert "Pause until" in store.load_run("store-2").dossier.direct_answer
    with pytest.raises(ValueError):
        store.update_run("wrong-id", updated)


def test_file_run_store_archive_and_thread_queries(tmp_path: Path) -> None:
    store = FileRunStore(tmp_path)
    parent = _result("thread-root")
    child = _result("thread-child").model_copy(
        update={"decision_thread": build_thread_meta(parent)}
    )
    store.save_run(parent)
    store.save_run(child)

    archived = store.archive_run("thread-root", actor="tester", note="done")
    thread = store.thread_queries("thread-root")

    assert archived.review.status == LifecycleState.ARCHIVED
    assert {item.run_id for item in thread} == {"thread-root", "thread-child"}
