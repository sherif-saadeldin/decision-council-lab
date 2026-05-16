from __future__ import annotations

from rich.console import Console

from council.cli import (
    KNOWN_PROJECT_ERRORS,
    parse_args,
    render_config_init,
    render_config_list,
    render_config_show,
    render_config_use,
    render_doctor,
    render_known_error,
    render_preset_list,
    render_result,
    render_version,
    resolve_debate_rounds,
    resolve_runtime_options,
    resolve_settings,
)
from council.doctor import run_doctor
from council.engine import run_council
from council.progress import ConsoleProgressReporter, NullProgressReporter
from council.prompt_debug import save_prompt_debug
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

    if command == "doctor":
        try:
            settings = resolve_settings(args)
            checks = run_doctor(settings, live=bool(args.live))
            return render_doctor(console, checks)
        except KNOWN_PROJECT_ERRORS as exc:
            render_known_error(error_console, exc, quiet=False)
            return 1

    if command == "config":
        return _config_command(args, console, error_console)

    if command == "run":
        return _run_command(args, console, error_console)

    error_console.print(
        "Unknown command. Use: run, presets, doctor, version, config.",
        style="red",
    )
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
