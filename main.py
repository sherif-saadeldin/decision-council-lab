from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from rich.console import Console

from council.cli import (
    KNOWN_PROJECT_ERRORS,
    build_compare_request,
    build_council_request,
    build_smoke_request,
    parse_args,
    render_comparison_result,
    render_cost_estimate,
    render_council_result,
    render_smoke_report,
    render_config_init,
    render_config_list,
    render_config_show,
    render_config_use,
    render_doctor,
    render_known_error,
    render_review,
    render_runs_list,
    render_runs_show,
    render_preset_list,
    render_result,
    render_secrets_delete,
    render_secrets_get,
    render_secrets_list,
    render_secrets_set,
    render_sources_list,
    render_sources_query,
    render_sources_remove,
    render_sources_show,
    render_prompts_inventory,
    render_version,
    resolve_debate_rounds,
    resolve_runs_dir,
    resolve_runtime_options,
    resolve_settings,
)
from council.compare import run_comparison
from council.config import Settings
from council.costing import enforce_cost_budget
from council.council_session import run_council_session
from council.setup import run_setup
from council.smoke import run_smoke
from council.doctor import run_doctor
from council.engine import run_council
from council.progress import ConsoleProgressReporter, NullProgressReporter
from council.run_catalog import RunNotFoundError
from council.services.council_service import CouncilRequest, CouncilService, MultiCouncilRequest
from council.services.pack_service import PackGenerationBlockedError, PackRequest, PackService
from council.services.review_service import RejectRequest, ReviewRequest, ReviewService
from council.services.run_service import RunQuery, RunService
from council.storage.run_store import FileRunStore
from council.sources.service import SourceService


def main(argv: list[str] | None = None) -> int:
    console = Console()
    error_console = Console(stderr=True)

    try:
        args = parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2

    command = args.command

    if command == "presets":
        render_preset_list(console)
        return 0

    if command == "version":
        render_version(console)
        return 0

    if command == "prompts":
        try:
            profile = getattr(args, "system_profile", None) or "default"
            render_prompts_inventory(console, system_profile=profile)
            return 0
        except KNOWN_PROJECT_ERRORS as exc:
            render_known_error(error_console, exc, quiet=False)
            return 1

    if command == "doctor":
        try:
            settings = resolve_settings(args)
            runtime = resolve_runtime_options(args)
            checks = run_doctor(
                settings,
                live=bool(args.live),
                live_completion=bool(getattr(args, "live_completion", False)),
                runtime=runtime,
            )
            return render_doctor(console, checks)
        except KNOWN_PROJECT_ERRORS as exc:
            render_known_error(error_console, exc, quiet=False)
            return 1

    if command == "config":
        return _config_command(args, console, error_console)

    if command == "secrets":
        return _secrets_command(args, console, error_console)

    if command in ("compare", "benchmark"):
        return _compare_command(args, console, error_console)

    if command == "smoke":
        return _smoke_command(args, console, error_console)

    if command == "run":
        return _run_command(args, console, error_console)

    if command == "setup":
        return _setup_command(args, console, error_console)

    if command == "council":
        return _council_command(args, console, error_console)

    if command == "runs":
        return _runs_command(args, console, error_console)

    if command == "chat":
        return _chat_command(args, console, error_console)

    if command == "sources":
        return _sources_command(args, console, error_console)

    # Slice 5.10: lifecycle verbs reachable from CI / scripts.
    if command == "approve":
        return _approve_command(args, console, error_console)
    if command == "reject":
        return _reject_command(args, console, error_console)
    if command == "archive":
        return _archive_command(args, console, error_console)
    if command == "review":
        return _review_command(args, console, error_console)
    if command == "pack":
        return _pack_command(args, console, error_console)

    error_console.print(
        "Unknown command. Use: run, council, chat, runs, compare, smoke, setup, presets, "
        "prompts, doctor, version, config, secrets, approve, reject, archive, review, pack, sources.",
        style="red",
    )
    return 1


def _smoke_command(args, console: Console, error_console: Console) -> int:
    try:
        request = build_smoke_request(args)
        report = run_smoke(request)
        render_smoke_report(console, report)
        return 0 if report.success else 1
    except KNOWN_PROJECT_ERRORS as exc:
        render_known_error(error_console, exc, quiet=False)
        return 1


def _compare_command(args, console: Console, error_console: Console) -> int:
    question = args.question.strip()
    if not question:
        error_console.print("A decision question is required.", style="red")
        return 1

    try:
        request = build_compare_request(args)
        source_payload = _resolve_source_payload(args)
        if source_payload.summary:
            request = replace(
                request,
                question=(
                    "Source context:\n"
                    f"{source_payload.summary}\n\n"
                    f"Question:\n{request.question}"
                ),
            )
        report, json_path, md_path = run_comparison(request)
        render_comparison_result(
            console,
            report,
            json_path,
            md_path,
            quiet=bool(args.quiet),
        )
        failures = sum(1 for entry in report.entries if not entry.success)
        return 1 if failures == len(report.entries) else 0
    except KNOWN_PROJECT_ERRORS as exc:
        render_known_error(error_console, exc, quiet=bool(args.quiet))
        return 1


def _run_command(args, console: Console, error_console: Console) -> int:
    question = args.question.strip()
    if not question:
        error_console.print("A decision question is required.", style="red")
        return 1

    try:
        settings = resolve_settings(args)
        runtime = resolve_runtime_options(args)
        source_payload = _resolve_source_payload(args)
        debate_rounds = resolve_debate_rounds(args, runtime)
        progress = (
            ConsoleProgressReporter(console, enabled=runtime.show_progress)
            if runtime.show_progress
            else NullProgressReporter()
        )
        service = CouncilService(
            FileRunStore(settings.runs_dir),
            run_council_fn=run_council,
        )
        execution = service.run_standard(
            CouncilRequest(
                question=question,
                settings=settings,
                runtime=runtime,
                debate_rounds=debate_rounds,
                save_prompt_debug=args.save_prompt_debug,
                progress=progress,
                source_pack_ids=source_payload.source_pack_ids,
                source_context_summary=source_payload.summary,
                source_relevance=source_payload.relevance,
                source_excluded_files=source_payload.excluded_files,
                source_context_warnings=source_payload.warnings,
            )
        )

        render_result(
            console,
            execution.result,
            execution.json_path,
            execution.md_path,
            quiet=args.quiet,
            runs_dir=settings.runs_dir,
            prompt_debug_path=execution.prompt_debug_path,
            fast_mode=runtime.fast_mode,
        )
        return 0
    except KNOWN_PROJECT_ERRORS as exc:
        render_known_error(error_console, exc, quiet=args.quiet)
        return 1


def _runs_command(args, console: Console, error_console: Console) -> int:
    try:
        runs_dir = resolve_runs_dir(args)
        service = RunService(FileRunStore(runs_dir))
        sub = getattr(args, "runs_command", None)
        if sub == "list":
            render_runs_list(console, runs_dir)
            return 0
        if sub == "show":
            summary = service.summary(RunQuery(args.run_id.strip()))
            render_runs_show(console, summary)
            return 0
        error_console.print("Usage: runs list | runs show RUN_ID", style="red")
        return 1
    except RunNotFoundError as exc:
        error_console.print(str(exc), style="red")
        return 1
    except KNOWN_PROJECT_ERRORS as exc:
        render_known_error(error_console, exc, quiet=False)
        return 1


def _council_command(args, console: Console, error_console: Console) -> int:
    from rich.prompt import Confirm

    question = (getattr(args, "question", None) or "").strip()
    if not question:
        error_console.print("A decision question is required.", style="red")
        return 1

    try:
        request = build_council_request(args)
        source_payload = _resolve_source_payload(args)
        request = replace(
            request,
            source_pack_ids=source_payload.source_pack_ids,
            source_context_summary=source_payload.summary,
            source_relevance=source_payload.relevance,
            source_excluded_files=source_payload.excluded_files,
            source_context_warnings=source_payload.warnings,
        )
        service = CouncilService(
            FileRunStore((request.base_settings or Settings.from_env()).runs_dir),
            run_council_session_fn=run_council_session,
        )
        plan = service.plan_multi(request)
        enforce_cost_budget(
            plan.cost_estimate,
            max_cost_usd=request.max_cost_usd,
            max_llm_calls=request.max_llm_calls,
            allow_over_budget=request.allow_over_budget,
        )
        if request.dry_run_cost:
            render_cost_estimate(
                console,
                plan.cost_estimate,
                routing=plan.routing,
                preset_availability=plan.preset_availability,
            )
            return 0

        runtime = request.runtime or resolve_runtime_options(args)
        progress = (
            ConsoleProgressReporter(console, enabled=runtime.show_progress)
            if runtime.show_progress
            else NullProgressReporter()
        )
        create_pack = request.create_pack
        if request.prompt_create_pack and not create_pack:
            # Skip the interactive prompt in non-TTY or --quiet runs so piped
            # invocations (CI, scripts) don't crash with EOFError. Users who
            # want a pack non-interactively pass --create-pack or --yes-pack.
            import sys as _sys

            if bool(getattr(args, "quiet", False)) or not _sys.stdin.isatty():
                create_pack = False
            else:
                create_pack = Confirm.ask("Create implementation pack?", default=False)
        execution = service.run_multi(
            MultiCouncilRequest(
                session_request=request,
                progress=progress,
                plan=plan,
                create_pack=create_pack,
            )
        )

        render_council_result(
            console,
            execution.result,
            execution.json_path,
            execution.md_path,
            quiet=bool(args.quiet),
            role_play_warning=execution.session.role_play_warning,
            pack_paths=execution.pack_paths,
            cost_estimate=execution.session.cost_estimate,
            routing_warnings=execution.routing_warnings,
        )
        return 0
    except PackGenerationBlockedError:
        error_console.print(
            "Pack generation blocked: decision is not approved. "
            "Re-run with --allow-unapproved-pack, or approve via "
            "`uv run python main.py chat` (/approve <run_id>).",
            style="red",
        )
        return 1
    except KNOWN_PROJECT_ERRORS as exc:
        render_known_error(error_console, exc, quiet=bool(getattr(args, "quiet", False)))
        return 1


def _setup_command(args, console: Console, error_console: Console) -> int:
    from getpass import getpass

    from council.secrets import set_keyring_secret

    from council.config_profiles import config_path as setup_config_path

    try:
        result = run_setup(
            interactive=not bool(args.non_interactive),
            profile_name=getattr(args, "profile", None),
            config_path_override=setup_config_path(),
            console=console,
            store_secret_fn=set_keyring_secret,
            secret_prompt_fn=getpass,
            doctor_fn=run_doctor,
            smoke_fn=run_smoke,
        )
    except KNOWN_PROJECT_ERRORS as exc:
        render_known_error(error_console, exc, quiet=False)
        return 1
    if result.message:
        if result.exit_code == 0:
            console.print(result.message)
        else:
            error_console.print(result.message, style="red")
    return result.exit_code


def _secrets_command(args, console: Console, error_console: Console) -> int:
    from getpass import getpass

    sub = getattr(args, "secrets_command", None)
    try:
        if sub == "set":
            render_secrets_set(console, args.name, prompt_for_value=getpass)
            return 0
        if sub == "get":
            render_secrets_get(console, args.name)
            return 0
        if sub == "list":
            render_secrets_list(console)
            return 0
        if sub == "delete":
            render_secrets_delete(console, args.name)
            return 0
        error_console.print(
            "Usage: secrets set|get|list|delete NAME",
            style="red",
        )
        return 1
    except KNOWN_PROJECT_ERRORS as exc:
        render_known_error(error_console, exc, quiet=False)
        return 1


def _chat_command(args, console: Console, error_console: Console) -> int:
    from council.chat import run_chat_session

    try:
        settings = resolve_settings(args)
        runtime = resolve_runtime_options(args)
        profile_name = getattr(args, "profile", None)
        return run_chat_session(
            console,
            error_console,
            settings=settings,
            system_profile=runtime.system_profile,
            config_profile_name=profile_name,
        )
    except KNOWN_PROJECT_ERRORS as exc:
        render_known_error(error_console, exc, quiet=False)
        return 1


def _sources_command(args, console: Console, error_console: Console) -> int:
    try:
        service = SourceService()
        sub = getattr(args, "sources_command", None)
        if sub == "scan":
            pack = service.scan_and_save(Path(args.path), name=getattr(args, "name", None))
            render_sources_show(console, pack)
            return 0
        if sub == "list":
            render_sources_list(console, service.list_packs())
            return 0
        if sub == "show":
            render_sources_show(console, service.load(args.source_pack_id))
            return 0
        if sub == "query":
            payload = service.query(args.source_pack_id, args.question)
            render_sources_query(console, args.source_pack_id, payload)
            return 0
        if sub == "remove":
            removed = service.remove(args.source_pack_id)
            render_sources_remove(console, args.source_pack_id, removed)
            return 0 if removed else 1
        error_console.print("Usage: sources scan PATH | list | show ID | query ID QUESTION | remove ID", style="red")
        return 1
    except KNOWN_PROJECT_ERRORS as exc:
        render_known_error(error_console, exc, quiet=False)
        return 1


def _resolve_source_payload(args):
    service = SourceService()
    source_pack_ids = list(getattr(args, "source_packs", None) or [])
    source_paths = [Path(item) for item in (getattr(args, "source_paths", None) or [])]
    return service.build_context(
        source_pack_ids=source_pack_ids,
        source_paths=source_paths,
        question=(getattr(args, "question", None) or ""),
        decision_mode=str(getattr(args, "routing_mode", "")),
    )


def _config_command(args, console: Console, error_console: Console) -> int:
    sub = getattr(args, "config_command", None)
    try:
        if sub == "init":
            render_config_init(console)
            return 0
        if sub == "list":
            render_config_list(console)
            return 0
        if sub == "show":
            render_config_show(console, args.profile)
            return 0
        if sub == "use":
            render_config_use(console, args.profile)
            return 0
        error_console.print("Usage: config init | list | show PROFILE | use PROFILE", style="red")
        return 1
    except KNOWN_PROJECT_ERRORS as exc:
        render_known_error(error_console, exc, quiet=False)
        return 1


# --- Slice 5.10: lifecycle CLI verbs -------------------------------------


def _resolve_review_runs_dir(args) -> Path:
    """Same runs_dir resolution as `runs show` so verbs share one source."""
    return resolve_runs_dir(args)


def _approve_command(args, console: Console, error_console: Console) -> int:
    try:
        runs_dir = _resolve_review_runs_dir(args)
        result = ReviewService(FileRunStore(runs_dir)).approve(
            ReviewRequest(
                run_id=args.run_id.strip(),
                actor=getattr(args, "actor", None),
                note=getattr(args, "note", "") or "",
            )
        )
    except RunNotFoundError as exc:
        error_console.print(str(exc), style="red")
        return 1
    except KNOWN_PROJECT_ERRORS as exc:
        render_known_error(error_console, exc, quiet=False)
        return 1
    console.print(
        f"[green]Approved[/green] {args.run_id} by [cyan]{result.review.approved_by}[/cyan]."
    )
    parent = result.review.is_revision_of
    if parent:
        console.print(f"[magenta]Superseded parent[/magenta] {parent}.")
    return 0


def _reject_command(args, console: Console, error_console: Console) -> int:
    try:
        runs_dir = _resolve_review_runs_dir(args)
        result = ReviewService(FileRunStore(runs_dir)).reject(
            RejectRequest(
                run_id=args.run_id.strip(),
                actor=getattr(args, "actor", None),
                reason=args.reason,
            )
        )
    except RunNotFoundError as exc:
        error_console.print(str(exc), style="red")
        return 1
    except KNOWN_PROJECT_ERRORS as exc:
        render_known_error(error_console, exc, quiet=False)
        return 1
    console.print(
        f"[red]Rejected[/red] {args.run_id} by [cyan]{result.review.rejected_by}[/cyan]."
    )
    return 0


def _archive_command(args, console: Console, error_console: Console) -> int:
    try:
        runs_dir = _resolve_review_runs_dir(args)
        result = ReviewService(FileRunStore(runs_dir)).archive(
            ReviewRequest(
                run_id=args.run_id.strip(),
                actor=getattr(args, "actor", None),
                note=getattr(args, "note", "") or "",
            )
        )
    except RunNotFoundError as exc:
        error_console.print(str(exc), style="red")
        return 1
    except KNOWN_PROJECT_ERRORS as exc:
        render_known_error(error_console, exc, quiet=False)
        return 1
    console.print(
        f"[dim]Archived[/dim] {args.run_id} (status: {result.review.status.value})."
    )
    return 0


def _review_command(args, console: Console, error_console: Console) -> int:
    try:
        runs_dir = _resolve_review_runs_dir(args)
        run_id = args.run_id.strip()
        result = ReviewService(FileRunStore(runs_dir)).load(run_id)
    except RunNotFoundError as exc:
        error_console.print(str(exc), style="red")
        return 1
    except KNOWN_PROJECT_ERRORS as exc:
        render_known_error(error_console, exc, quiet=False)
        return 1
    render_review(console, run_id, result)
    return 0


def _pack_command(args, console: Console, error_console: Console) -> int:
    """Generate the implementation pack for an already-saved run."""
    from council.review_model import PACK_GATE_BLOCKED_REASON

    try:
        runs_dir = _resolve_review_runs_dir(args)
        run_id = args.run_id.strip()
        service = PackService(FileRunStore(runs_dir))
        pack = service.generate(
            PackRequest(
                run_id=run_id,
                allow_unapproved=bool(getattr(args, "allow_unapproved", False)),
            )
        )
    except RunNotFoundError as exc:
        error_console.print(str(exc), style="red")
        return 1
    except PackGenerationBlockedError:
        error_console.print(
            f"{PACK_GATE_BLOCKED_REASON} "
            f"Run `uv run python main.py approve {args.run_id.strip()}` first, "
            "or re-run pack with --allow-unapproved.",
            style="red",
        )
        return 1
    except KNOWN_PROJECT_ERRORS as exc:
        render_known_error(error_console, exc, quiet=False)
        return 1
    console.print("[green]Implementation pack:[/green]")
    for path in pack.paths:
        console.print(f"  {path}", highlight=False, markup=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
