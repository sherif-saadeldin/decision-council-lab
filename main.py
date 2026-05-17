from __future__ import annotations

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
    render_runs_list,
    render_runs_show,
    render_preset_list,
    render_result,
    render_secrets_delete,
    render_secrets_get,
    render_secrets_list,
    render_secrets_set,
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
from council.council_session import plan_council_session, run_council_session
from council.setup import run_setup
from council.smoke import run_smoke
from council.doctor import run_doctor
from council.engine import run_council
from council.implementation_pack import write_implementation_pack
from council.verdict_quality import ensure_verdict_quality_for_pack
from council.progress import ConsoleProgressReporter, NullProgressReporter
from council.prompt_debug import save_prompt_debug
from council.run_catalog import RunNotFoundError, get_run_summary
from council.storage import save_run


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

    error_console.print(
        "Unknown command. Use: run, council, chat, runs, compare, smoke, setup, presets, "
        "prompts, doctor, version, config, secrets.",
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
        debate_rounds = resolve_debate_rounds(args, runtime)
        progress = (
            ConsoleProgressReporter(console, enabled=runtime.show_progress)
            if runtime.show_progress
            else NullProgressReporter()
        )

        result, debug_collector = run_council(
            question,
            settings=settings,
            debate_rounds=debate_rounds,
            save_prompt_debug=args.save_prompt_debug,
            runtime=runtime,
            progress=progress,
        )

        if progress is not None and runtime.show_progress:
            progress.on_stage("storage")

        json_path, md_path = save_run(result, settings=settings)

        prompt_debug_path = None
        if args.save_prompt_debug and debug_collector is not None:
            secrets = [
                key
                for key in (settings.openai_api_key, settings.llm_api_key)
                if key
            ]
            prompt_debug_path = save_prompt_debug(
                result,
                debug_collector,
                settings.runs_dir,
                secrets=secrets,
            )

        render_result(
            console,
            result,
            json_path,
            md_path,
            quiet=args.quiet,
            runs_dir=settings.runs_dir,
            prompt_debug_path=prompt_debug_path,
            fast_mode=runtime.fast_mode,
        )
        return 0
    except KNOWN_PROJECT_ERRORS as exc:
        render_known_error(error_console, exc, quiet=args.quiet)
        return 1


def _runs_command(args, console: Console, error_console: Console) -> int:
    try:
        runs_dir = resolve_runs_dir(args)
        sub = getattr(args, "runs_command", None)
        if sub == "list":
            render_runs_list(console, runs_dir)
            return 0
        if sub == "show":
            summary = get_run_summary(runs_dir, args.run_id.strip())
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
        plan = plan_council_session(request)
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
        session = run_council_session(request, progress=progress, plan=plan)
        settings = request.base_settings or Settings.from_env()

        pack_paths: list[Path] = []
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
        if create_pack:
            # Slice 5.9: gate pack on lifecycle approval unless overridden.
            from council.review_model import is_pack_allowed

            if not is_pack_allowed(
                session.result.review,
                override=request.allow_unapproved_pack,
            ):
                error_console.print(
                    "Pack generation blocked: decision is not approved. "
                    "Re-run with --allow-unapproved-pack, or approve via "
                    "`uv run python main.py chat` (/approve <run_id>).",
                    style="red",
                )
                return 1
            ensure_verdict_quality_for_pack(session.result.dossier)
            run_dir = settings.runs_dir / session.result.dossier.run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            pack_paths = write_implementation_pack(run_dir, session.result)

        json_path, md_path = save_run(
            session.result,
            settings=settings,
            implementation_pack_paths=pack_paths or None,
        )

        render_council_result(
            console,
            session.result,
            json_path,
            md_path,
            quiet=bool(args.quiet),
            role_play_warning=session.role_play_warning,
            pack_paths=pack_paths,
            cost_estimate=session.cost_estimate,
            routing_warnings=list(plan.routing.routing_warnings),
        )
        return 0
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


if __name__ == "__main__":
    raise SystemExit(main())
