from __future__ import annotations

from rich.console import Console

from council.cli import build_parser, render_result, resolve_settings
from council.engine import run_council
from council.storage import save_run


def main(argv: list[str] | None = None) -> int:
    console = Console()
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.question or not args.question.strip():
        parser.print_help()
        return 1

    settings = resolve_settings(args)
    question = args.question.strip()

    result = run_council(question, settings=settings)
    json_path, md_path = save_run(result, settings=settings)
    render_result(
        console,
        result,
        json_path,
        md_path,
        quiet=args.quiet,
        runs_dir=settings.runs_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
