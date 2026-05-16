from __future__ import annotations

from rich.console import Console

from council.cli import (
    KNOWN_PROJECT_ERRORS,
    build_parser,
    render_known_error,
    render_preset_list,
    render_result,
    resolve_settings,
)
from council.engine import run_council
from council.prompt_debug import save_prompt_debug
from council.storage import save_run


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    console = Console()
    error_console = Console(stderr=True)

    if args.list_presets:
        render_preset_list(console)
        return 0

    if not args.question or not args.question.strip():
        parser.print_help()
        return 1

    try:
        settings = resolve_settings(args)
        question = args.question.strip()

        result, debug_collector = run_council(
            question,
            settings=settings,
            save_prompt_debug=args.save_prompt_debug,
        )
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
        )
        return 0
    except KNOWN_PROJECT_ERRORS as exc:
        render_known_error(error_console, exc, quiet=args.quiet)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
