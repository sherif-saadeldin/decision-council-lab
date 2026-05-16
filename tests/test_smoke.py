from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from council.cli import build_smoke_request, parse_args, render_smoke_report
from council.models import CouncilRunResult, DecisionDossier, DecisionType
from council.providers.errors import MissingProviderCredentialError
from council.providers.models import ProviderMetadata
from council.redaction import assert_no_credential_leaks
from council.secrets import set_keyring_secret
from council.smoke import DEFAULT_SMOKE_QUESTION, SmokeReport, SmokeRequest, run_smoke
from main import main
from rich.console import Console

SECRET_VALUE = "sk-9f3a2b1c8e7d6a5b4c3d2e1f0a9b8c7d6"


def _sample_result(*, with_quality_fields: bool = True) -> CouncilRunResult:
    dossier = DecisionDossier(
        decision_question="Smoke?",
        decision_type=DecisionType.PROCEED,
        confidence_score=0.82,
        deciding_factor="Pipeline healthy",
        recommendation="Proceed with monitoring",
        evidence_gaps=["Missing latency baseline."] if with_quality_fields else [],
        proposed_metrics=["proposed: error_rate < 1%"] if with_quality_fields else [],
    )
    return CouncilRunResult(
        dossier=dossier,
        provider_metadata=ProviderMetadata(
            mode="mock",
            provider_name="mock",
            model_name="mock-council-v1",
        ),
        agent_briefs=[],
    )


def test_parse_smoke_args_defaults() -> None:
    args = parse_args(["smoke", "--preset", "mock"])
    assert args.command == "smoke"
    request = build_smoke_request(args)
    assert request.preset == "mock"
    assert request.question == DEFAULT_SMOKE_QUESTION
    assert request.debate_rounds == 0


def test_parse_smoke_args_overrides() -> None:
    args = parse_args(
        [
            "smoke",
            "--preset",
            "ollama-qwen",
            "--question",
            "Custom smoke question?",
            "--debate-rounds",
            "1",
            "--timeout-seconds",
            "60",
        ]
    )
    request = build_smoke_request(args)
    assert request.question == "Custom smoke question?"
    assert request.debate_rounds == 1
    assert request.timeout_seconds == 60.0


def test_run_smoke_uses_patched_runner_without_network(tmp_path: Path) -> None:
    result = _sample_result()

    def fake_run_council(question: str, **kwargs: object) -> tuple[CouncilRunResult, None]:
        return result, None

    def fake_save_run(
        saved: CouncilRunResult,
        settings: object | None = None,
    ) -> tuple[Path, Path]:
        run_dir = tmp_path / saved.dossier.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        json_path = run_dir / "run.json"
        md_path = run_dir / "run.md"
        json_path.write_text("{}", encoding="utf-8")
        md_path.write_text("# run", encoding="utf-8")
        return json_path, md_path

    report = run_smoke(
        SmokeRequest(preset="mock", runs_dir=tmp_path, debate_rounds=0),
        run_council_fn=fake_run_council,
        save_run_fn=fake_save_run,
    )
    assert report.success is True
    assert report.provider_name == "mock"
    assert report.has_evidence_gaps is True
    assert report.has_proposed_metrics is True
    assert report.run_json_path
    assert Path(report.run_json_path).exists()


def test_render_smoke_success_report(capsys) -> None:
    report = SmokeReport(
        success=True,
        preset="mock",
        question=DEFAULT_SMOKE_QUESTION,
        provider_name="mock",
        model_name="mock-council-v1",
        elapsed_seconds=1.23,
        run_id="run-123",
        run_json_path="runs/run-123/run.json",
        run_md_path="runs/run-123/run.md",
        decision_type="proceed",
        confidence_score=0.75,
        has_evidence_gaps=True,
        has_proposed_metrics=True,
    )
    render_smoke_report(Console(), report)
    captured = capsys.readouterr()
    assert "success" in captured.out
    assert "mock-council-v1" in captured.out
    assert "Evidence gaps present" in captured.out
    assert SECRET_VALUE not in captured.out


def test_render_smoke_failure_report_safely(capsys) -> None:
    report = SmokeReport(
        success=False,
        preset="openai-mini",
        question=DEFAULT_SMOKE_QUESTION,
        provider_name="openai",
        model_name="gpt-4.1-mini",
        elapsed_seconds=0.5,
        error="Missing required environment variable OPENAI_API_KEY for provider 'openai'.",
        failure_reason="auth_failure",
    )
    render_smoke_report(Console(), report)
    captured = capsys.readouterr()
    assert "failure" in captured.out
    assert "auth_failure" in captured.out
    assert "OPENAI_API_KEY" in captured.out
    assert SECRET_VALUE not in captured.out


def test_main_smoke_success_via_patched_runner(capsys) -> None:
    report = SmokeReport(
        success=True,
        preset="mock",
        question=DEFAULT_SMOKE_QUESTION,
        provider_name="mock",
        model_name="mock-council-v1",
        elapsed_seconds=0.1,
        run_id="abc",
        run_json_path="runs/abc/run.json",
        run_md_path="runs/abc/run.md",
        decision_type="proceed",
        confidence_score=0.9,
    )
    with patch("main.run_smoke", return_value=report):
        code = main(["smoke", "--preset", "mock"])
    captured = capsys.readouterr()
    assert code == 0
    assert "success" in captured.out
    assert SECRET_VALUE not in captured.out


def test_main_smoke_failure_exit_code(capsys) -> None:
    report = SmokeReport(
        success=False,
        preset="openrouter-sonnet",
        question=DEFAULT_SMOKE_QUESTION,
        provider_name="openrouter",
        model_name="anthropic/claude-sonnet-4.5",
        elapsed_seconds=0.2,
        error="openrouter authentication failed. Check LLM_API_KEY.",
    )
    with patch("main.run_smoke", return_value=report):
        code = main(["smoke", "--preset", "openrouter-sonnet"])
    assert code == 1
    assert "failure" in capsys.readouterr().out


def test_run_smoke_failure_from_runner_without_network(tmp_path: Path) -> None:
    def failing_run(question: str, **kwargs: object) -> tuple[CouncilRunResult, None]:
        raise MissingProviderCredentialError("openai", "OPENAI_API_KEY")

    report = run_smoke(
        SmokeRequest(preset="openai-mini", runs_dir=tmp_path, skip_preflight=True),
        run_council_fn=failing_run,
        save_run_fn=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("should not save")),
    )
    assert report.success is False
    assert report.failure_reason == "auth_failure"
    assert report.error
    assert "OPENAI_API_KEY" in report.error
    assert_no_credential_leaks(report.error, [SECRET_VALUE])


def test_smoke_output_excludes_keyring_secret(capsys) -> None:
    set_keyring_secret("OPENAI_API_KEY", SECRET_VALUE)
    report = SmokeReport(
        success=True,
        preset="mock",
        question=DEFAULT_SMOKE_QUESTION,
        provider_name="mock",
        model_name="mock-council-v1",
        elapsed_seconds=0.01,
        run_id="id",
        run_json_path="runs/id/run.json",
        run_md_path="runs/id/run.md",
        decision_type="proceed",
        confidence_score=0.5,
    )
    with patch("main.run_smoke", return_value=report):
        main(["smoke", "--preset", "mock"])
    assert_no_credential_leaks(capsys.readouterr().out, [SECRET_VALUE])
