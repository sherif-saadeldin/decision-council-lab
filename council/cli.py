from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from council.compare import CompareConfigError, CompareRequest, ComparisonReport, build_targets, parse_csv_targets
from council.costing import CouncilBudgetExceededError
from council.provider_availability import HostedProviderUnavailableError
from council.prompt_loader import (
    PromptLoadError,
    UnknownSystemProfileError,
    list_prompt_files,
    list_system_profiles,
    load_system_profile,
    profile_bundle_hash,
)
from council.verdict_quality import VerdictQualityError, decision_label
from council.council_session import CouncilSessionRequest
from council.role_routing import CouncilRouting, parse_council_preset_list
from council.run_catalog import RunNotFoundError, list_recent_runs
from council.config import Settings
from council.config_profiles import (
    ConfigProfile,
    ConfigProfileError,
    CouncilConfigFile,
    UnknownConfigProfileError,
    init_config_file,
    load_config_file,
    profile_display_rows,
    resolve_debate_rounds_with_profile,
    resolve_profile_name,
    resolve_runtime_with_profile,
    resolve_settings_with_profile,
    set_active_profile,
)
from council.doctor import CheckStatus, DoctorCheck
from council.model_presets import MODEL_PRESETS, list_preset_names
from council.models import DEFAULT_DEBATE_ROUNDS, RUN_SCHEMA_VERSION, CouncilRunResult
from council.providers.api_mode import InvalidApiModeError
from council.providers.errors import (
    MissingProviderConfigError,
    MissingProviderCredentialError,
    ProviderResponseError,
    UnknownModelPresetError,
    UnsupportedProviderModeError,
)
from council.providers.factory import SUPPORTED_LLM_MODES
from council.runtime import RuntimeOptions
from council.smoke import DEFAULT_SMOKE_QUESTION, SmokeReport, SmokeRequest
from council.secrets import (
    UnknownSecretNameError,
    delete_keyring_secret,
    list_secret_statuses,
    set_keyring_secret,
    validate_secret_name,
)
from council.sources.models import SourcePack
from council.version import APP_VERSION

KNOWN_PROJECT_ERRORS: tuple[type[Exception], ...] = (
    MissingProviderCredentialError,
    MissingProviderConfigError,
    UnknownModelPresetError,
    UnsupportedProviderModeError,
    ProviderResponseError,
    ConfigProfileError,
    UnknownConfigProfileError,
    UnknownSecretNameError,
    CompareConfigError,
    InvalidApiModeError,
    ValueError,
    RunNotFoundError,
    CouncilBudgetExceededError,
    HostedProviderUnavailableError,
    VerdictQualityError,
    PromptLoadError,
    UnknownSystemProfileError,
)

CLI_COMMANDS = frozenset(
    {
        "run",
        "presets",
        "doctor",
        "version",
        "config",
        "secrets",
        "compare",
        "benchmark",
        "smoke",
        "setup",
        "council",
        "runs",
        "prompts",
        "chat",
        # Slice 5.10: lifecycle verbs reachable from CI / scripts without
        # requiring an interactive chat session.
        "approve",
        "reject",
        "archive",
        "review",
        "pack",
        "sources",
    }
)
PREVIEW_ITEM_COUNT = 3


def normalize_argv(argv: list[str] | None) -> list[str]:
    """Map legacy invocations to subcommands for backward compatibility."""
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        return argv
    if argv[0] in CLI_COMMANDS:
        return argv
    if "--list-presets" in argv:
        return ["presets"]
    return ["run", *argv]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="decision-council-lab",
        description="Decision council CLI — run, diagnose, and manage presets.",
    )
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser(
        "run",
        help="Run the council on a decision question.",
    )
    _add_run_arguments(run_parser)
    _add_source_arguments(run_parser)

    subparsers.add_parser("presets", help="List model routing presets.")
    doctor_parser = subparsers.add_parser("doctor", help="Check provider configuration.")
    doctor_parser.add_argument(
        "--preset",
        metavar="PRESET_NAME",
        help="Apply preset before running checks.",
    )
    doctor_parser.add_argument(
        "--live",
        action="store_true",
        help="Initialize provider (no completion call) to validate setup.",
    )
    doctor_parser.add_argument(
        "--live-completion",
        action="store_true",
        help="Run one minimal live completion (JSON ping); short timeout.",
    )
    _add_profile_argument(doctor_parser)
    _add_api_mode_argument(doctor_parser)
    subparsers.add_parser("version", help="Show app and schema version.")

    config_parser = subparsers.add_parser("config", help="Manage local config profiles.")
    config_sub = config_parser.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("init", help="Create .dcouncil/config.toml with sample profiles.")
    config_sub.add_parser("list", help="List config profiles.")
    config_show = config_sub.add_parser("show", help="Show profile values (no secrets).")
    config_show.add_argument("profile", help="Profile name to display.")
    config_use = config_sub.add_parser("use", help="Set active_profile in config.toml.")
    config_use.add_argument("profile", help="Profile name to activate.")

    secrets_parser = subparsers.add_parser("secrets", help="Manage API keys in the OS keyring.")
    secrets_sub = secrets_parser.add_subparsers(dest="secrets_command", required=True)
    secrets_set = secrets_sub.add_parser("set", help="Store a secret in the OS keyring.")
    secrets_set.add_argument("name", help="Secret name (OPENAI_API_KEY or LLM_API_KEY).")
    secrets_get = secrets_sub.add_parser("get", help="Report whether a secret is set (never prints value).")
    secrets_get.add_argument("name", help="Secret name (OPENAI_API_KEY or LLM_API_KEY).")
    secrets_sub.add_parser("list", help="List supported secrets and availability.")
    secrets_delete = secrets_sub.add_parser("delete", help="Remove a secret from the OS keyring.")
    secrets_delete.add_argument("name", help="Secret name (OPENAI_API_KEY or LLM_API_KEY).")

    compare_parser = subparsers.add_parser(
        "compare",
        help="Run the same question across multiple presets/profiles and compare.",
    )
    _add_compare_arguments(compare_parser)
    _add_source_arguments(compare_parser)
    benchmark_parser = subparsers.add_parser(
        "benchmark",
        help="Alias for compare.",
    )
    _add_compare_arguments(benchmark_parser)
    _add_source_arguments(benchmark_parser)

    smoke_parser = subparsers.add_parser(
        "smoke",
        help="Manual live-provider smoke test (not used by pytest).",
    )
    _add_smoke_arguments(smoke_parser)

    setup_parser = subparsers.add_parser(
        "setup",
        help="Interactive wizard for local config, secrets, doctor, and smoke.",
    )
    setup_parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Apply a built-in profile without prompts.",
    )
    setup_parser.add_argument(
        "--profile",
        metavar="PROFILE_NAME",
        help=(
            "Profile to apply with --non-interactive "
            "(mock, ollama-local, openai-mini, openrouter-sonnet)."
        ),
    )

    council_parser = subparsers.add_parser(
        "council",
        help="Multi-model council: each role may use a different preset/model.",
    )
    _add_council_arguments(council_parser)
    _add_source_arguments(council_parser)

    runs_parser = subparsers.add_parser("runs", help="List and inspect saved council runs.")
    runs_sub = runs_parser.add_subparsers(dest="runs_command", required=True)
    runs_list = runs_sub.add_parser("list", help="List the 10 most recent runs.")
    _add_runs_dir_argument(runs_list)
    runs_show = runs_sub.add_parser("show", help="Show run paths and a short summary.")
    runs_show.add_argument("run_id", help="Run ID (directory name under runs/).")
    _add_runs_dir_argument(runs_show)

    prompts_parser = subparsers.add_parser(
        "prompts",
        help="List system prompt files, versions, and checksums.",
    )
    prompts_parser.add_argument(
        "--system-profile",
        default="default",
        metavar="NAME",
        help="System prompt profile to summarize (default: default).",
    )

    chat_parser = subparsers.add_parser(
        "chat",
        help="Interactive chat session over council commands.",
    )
    _add_runs_dir_argument(chat_parser)
    _add_profile_argument(chat_parser)
    _add_system_profile_argument(chat_parser)

    # Slice 5.10: lifecycle verbs as first-class subcommands. Mirror the
    # chat slash vocabulary (/approve, /reject, /archive, /review, /pack)
    # so users learn one set of names.
    approve_parser = subparsers.add_parser(
        "approve",
        help="Mark a saved council run as approved.",
    )
    approve_parser.add_argument("run_id", help="Run ID to approve.")
    approve_parser.add_argument(
        "--note",
        default="",
        help="Optional review note recorded with the approval.",
    )
    _add_actor_argument(approve_parser)
    _add_runs_dir_argument(approve_parser)

    reject_parser = subparsers.add_parser(
        "reject",
        help="Mark a saved council run as rejected (reason required).",
    )
    reject_parser.add_argument("run_id", help="Run ID to reject.")
    reject_parser.add_argument(
        "--reason",
        required=True,
        help="Required reason recorded with the rejection.",
    )
    _add_actor_argument(reject_parser)
    _add_runs_dir_argument(reject_parser)

    archive_parser = subparsers.add_parser(
        "archive",
        help="Archive a saved council run (blocks further review transitions).",
    )
    archive_parser.add_argument("run_id", help="Run ID to archive.")
    archive_parser.add_argument(
        "--note",
        default="",
        help="Optional note recorded with the archival event.",
    )
    _add_actor_argument(archive_parser)
    _add_runs_dir_argument(archive_parser)

    review_parser = subparsers.add_parser(
        "review",
        help="Show lifecycle state, review actors, and audit history for a run.",
    )
    review_parser.add_argument("run_id", help="Run ID to inspect.")
    _add_runs_dir_argument(review_parser)

    pack_parser = subparsers.add_parser(
        "pack",
        help="Generate the implementation pack for an approved run.",
    )
    pack_parser.add_argument("run_id", help="Run ID to generate a pack for.")
    pack_parser.add_argument(
        "--allow-unapproved",
        action="store_true",
        dest="allow_unapproved",
        help="Bypass the lifecycle gate and generate a pack on a draft run.",
    )
    _add_runs_dir_argument(pack_parser)

    sources_parser = subparsers.add_parser(
        "sources",
        help="Manage local source packs used for decision context.",
    )
    sources_sub = sources_parser.add_subparsers(dest="sources_command", required=True)
    sources_scan = sources_sub.add_parser("scan", help="Scan a folder/file into a local source pack.")
    sources_scan.add_argument("path", help="Folder or file path to scan.")
    sources_scan.add_argument("--name", default=None, help="Optional source pack display name.")
    sources_sub.add_parser("list", help="List saved source packs.")
    sources_show = sources_sub.add_parser("show", help="Show source pack details.")
    sources_show.add_argument("source_pack_id", help="Source pack ID.")
    sources_query = sources_sub.add_parser("query", help="Query ranked relevance for a source pack.")
    sources_query.add_argument("source_pack_id", help="Source pack ID.")
    sources_query.add_argument("question", help="Question used for deterministic ranking.")
    sources_remove = sources_sub.add_parser("remove", help="Delete a saved source pack.")
    sources_remove.add_argument("source_pack_id", help="Source pack ID.")

    return parser


def _add_actor_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--actor",
        metavar="NAME",
        default=None,
        help=(
            "Actor label recorded in the review audit history. "
            "Falls back to DCOUNCIL_REVIEW_ACTOR / USER / USERNAME / 'local'."
        ),
    )


def _add_council_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "question",
        nargs="?",
        default=None,
        help="Decision question for the multi-model council.",
    )
    parser.add_argument(
        "--routing-mode",
        choices=["economy", "balanced", "premium", "manual"],
        default="economy",
        help="Cost-aware preset routing (default: economy). Use manual with explicit presets.",
    )
    parser.add_argument(
        "--council-presets",
        metavar="PRESETS",
        help="Comma-separated presets mapped to council roles (researcher→chair).",
    )
    parser.add_argument(
        "--max-cost-usd",
        type=float,
        default=None,
        metavar="USD",
        help="Abort if estimated cost high bound exceeds this value (estimate only).",
    )
    parser.add_argument(
        "--max-llm-calls",
        type=int,
        default=None,
        metavar="N",
        help="Abort if planned LLM calls exceed this count.",
    )
    parser.add_argument(
        "--max-debate-rounds",
        type=int,
        default=None,
        metavar="N",
        help="Cap debate rounds after routing-mode default is applied.",
    )
    parser.add_argument(
        "--dry-run-cost",
        action="store_true",
        help="Print cost estimate and routing table; do not call providers.",
    )
    parser.add_argument(
        "--allow-over-budget",
        action="store_true",
        help="Run even when --max-cost-usd or --max-llm-calls would block.",
    )
    parser.add_argument(
        "--require-live-providers",
        action="store_true",
        help="Validate hosted presets with a live completion before running council.",
    )
    for slot in (
        "researcher",
        "advocate",
        "skeptic",
        "risk",
        "operator",
        "chair",
    ):
        parser.add_argument(
            f"--{slot}-preset",
            metavar="PRESET_NAME",
            dest=f"{slot}_preset",
            help=f"Preset for the {slot} role.",
        )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=None,
        help="Directory for run artifacts (default: RUNS_DIR env or ./runs).",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Print only artifact paths.",
    )
    parser.add_argument(
        "--debate-rounds",
        type=int,
        default=None,
        metavar="N",
        help="Debate rounds (default depends on --routing-mode; use 0 to skip).",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Per-request provider timeout (default: 120).",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=None,
        metavar="N",
        help="Retry count for failed provider API calls.",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Fast mode: concise prompts.",
    )
    parser.add_argument(
        "--create-pack",
        action="store_true",
        help="Write implementation_plan.md, task_breakdown.md, cursor_prompt.md, risk_register.md.",
    )
    parser.add_argument(
        "--yes-pack",
        action="store_true",
        help="Non-interactive: same as --create-pack (skip prompt).",
    )
    parser.add_argument(
        "--allow-unapproved-pack",
        action="store_true",
        help=(
            "Allow pack generation for draft/un-approved decisions. "
            "Default: packs require lifecycle status=approved (Slice 5.9)."
        ),
    )
    _add_repair_json_argument(parser)
    _add_api_mode_argument(parser)
    _add_system_profile_argument(parser)


def _add_api_mode_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--api-mode",
        choices=["responses", "chat", "auto"],
        default="auto",
        help="API transport: OpenAI Responses (preferred), chat completions, or auto fallback.",
    )


def _add_repair_json_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repair-json",
        action="store_true",
        help=(
            "On parse failure for openai_compatible providers, retry once with "
            "stricter JSON-only instructions."
        ),
    )


def _add_profile_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--profile",
        metavar="PROFILE_NAME",
        help="Config profile from .dcouncil/config.toml (overrides active_profile).",
    )


def _add_compare_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "question",
        help="Decision question to run across each preset/profile.",
    )
    parser.add_argument(
        "--presets",
        metavar="NAMES",
        help="Comma-separated model presets (e.g. mock,ollama-qwen).",
    )
    parser.add_argument(
        "--profiles",
        metavar="NAMES",
        help="Comma-separated config profiles from .dcouncil/config.toml.",
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=None,
        help="Directory for run and comparison artifacts (default: RUNS_DIR env or ./runs).",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Print only comparison artifact paths.",
    )
    parser.add_argument(
        "--debate-rounds",
        type=int,
        default=None,
        metavar="N",
        help=(
            f"Debate rounds for each run (default: {DEFAULT_DEBATE_ROUNDS}; "
            "use 0 to skip; fast mode forces 0)."
        ),
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Per-request provider timeout for live LLM calls (default: 120).",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=None,
        metavar="N",
        help="Retry count for failed provider API calls.",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Fast mode: skip debate, concise prompts.",
    )
    _add_repair_json_argument(parser)
    _add_api_mode_argument(parser)
    _add_system_profile_argument(parser)


def _add_smoke_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--preset",
        metavar="PRESET_NAME",
        required=True,
        help="Model preset to exercise against a live provider.",
    )
    parser.add_argument(
        "--question",
        default=None,
        help=f"Decision question (default: {DEFAULT_SMOKE_QUESTION!r}).",
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=None,
        help="Directory for run artifacts (default: RUNS_DIR env or ./runs).",
    )
    parser.add_argument(
        "--debate-rounds",
        type=int,
        default=0,
        metavar="N",
        help="Debate rounds before chair (default: 0 for speed).",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Per-request provider timeout for live LLM calls (default: 120).",
    )
    _add_repair_json_argument(parser)
    _add_api_mode_argument(parser)
    _add_system_profile_argument(parser)


def build_council_request(args: argparse.Namespace) -> CouncilSessionRequest:
    from council.routing_modes import (
        has_explicit_slot_presets,
        normalize_routing_mode,
        resolve_debate_rounds,
    )

    question = getattr(args, "question", None) or ""
    presets = parse_council_preset_list(getattr(args, "council_presets", None))
    routing_mode = normalize_routing_mode(getattr(args, "routing_mode", "economy"))
    explicit = has_explicit_slot_presets(
        council_presets=presets or None,
        researcher_preset=getattr(args, "researcher_preset", None),
        advocate_preset=getattr(args, "advocate_preset", None),
        skeptic_preset=getattr(args, "skeptic_preset", None),
        risk_preset=getattr(args, "risk_preset", None),
        operator_preset=getattr(args, "operator_preset", None),
        chair_preset=getattr(args, "chair_preset", None),
    )
    if explicit and routing_mode != "manual":
        routing_mode = routing_mode  # keep mode for debate defaults; slots stay explicit
    base = Settings.from_env()
    if getattr(args, "runs_dir", None) is not None:
        base = _settings_with_runs_dir(base, args.runs_dir)
    runtime = resolve_runtime_options(args)
    cli_debate = (
        int(args.debate_rounds) if getattr(args, "debate_rounds", None) is not None else None
    )
    debate_rounds = resolve_debate_rounds(
        routing_mode,
        cli_debate_rounds=cli_debate,
        max_debate_rounds=getattr(args, "max_debate_rounds", None),
    )
    return CouncilSessionRequest(
        question=question,
        routing_mode=routing_mode,
        council_presets=presets or None,
        researcher_preset=getattr(args, "researcher_preset", None),
        advocate_preset=getattr(args, "advocate_preset", None),
        skeptic_preset=getattr(args, "skeptic_preset", None),
        risk_preset=getattr(args, "risk_preset", None),
        operator_preset=getattr(args, "operator_preset", None),
        chair_preset=getattr(args, "chair_preset", None),
        debate_rounds=debate_rounds,
        max_cost_usd=getattr(args, "max_cost_usd", None),
        max_llm_calls=getattr(args, "max_llm_calls", None),
        max_debate_rounds=getattr(args, "max_debate_rounds", None),
        dry_run_cost=bool(getattr(args, "dry_run_cost", False)),
        allow_over_budget=bool(getattr(args, "allow_over_budget", False)),
        require_live_providers=bool(getattr(args, "require_live_providers", False)),
        create_pack=bool(getattr(args, "create_pack", False) or getattr(args, "yes_pack", False)),
        prompt_create_pack=not bool(getattr(args, "yes_pack", False) or getattr(args, "create_pack", False)),
        runtime=runtime,
        base_settings=base,
        allow_unapproved_pack=bool(getattr(args, "allow_unapproved_pack", False)),
        source_pack_ids=list(getattr(args, "source_packs", None) or []),
        source_context_summary="",
    )


def build_smoke_request(args: argparse.Namespace) -> SmokeRequest:
    question = getattr(args, "question", None) or DEFAULT_SMOKE_QUESTION
    return SmokeRequest(
        preset=args.preset,
        question=question,
        runs_dir=getattr(args, "runs_dir", None),
        debate_rounds=max(0, int(args.debate_rounds)),
        timeout_seconds=getattr(args, "timeout_seconds", None),
        repair_json=bool(getattr(args, "repair_json", False)),
        api_mode=str(getattr(args, "api_mode", "auto")),
    )


def build_compare_request(args: argparse.Namespace) -> CompareRequest:
    presets = parse_csv_targets(getattr(args, "presets", None))
    profiles = parse_csv_targets(getattr(args, "profiles", None))
    targets = build_targets(presets, profiles)
    argv = getattr(args, "_argv", None)
    fast_explicit = bool(argv and "--fast" in argv)
    return CompareRequest(
        question=args.question,
        targets=targets,
        runs_dir=getattr(args, "runs_dir", None),
        debate_rounds=getattr(args, "debate_rounds", None),
        timeout_seconds=getattr(args, "timeout_seconds", None),
        max_retries=getattr(args, "max_retries", None),
        fast=bool(getattr(args, "fast", False)),
        fast_explicit=fast_explicit,
        repair_json=bool(getattr(args, "repair_json", False)),
        api_mode=str(getattr(args, "api_mode", "auto")),
    )


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "question",
        help="Decision question for the council to deliberate on.",
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=None,
        help="Directory for run artifacts (default: RUNS_DIR env or ./runs).",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Print only paths to saved artifacts (suppress progress).",
    )
    parser.add_argument(
        "--save-prompt-debug",
        action="store_true",
        help="Save prompt/debug context to runs/<run_id>/prompt_debug.md (no secrets).",
    )
    parser.add_argument(
        "--preset",
        metavar="PRESET_NAME",
        help="Model routing preset (overrides LLM_MODE/model env defaults; keys from env or keyring).",
    )
    parser.add_argument(
        "--debate-rounds",
        type=int,
        default=None,
        metavar="N",
        help=(
            f"Debate rounds before chair (default: {DEFAULT_DEBATE_ROUNDS}; "
            "use 0 to skip; fast mode forces 0)."
        ),
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Per-request provider timeout for live LLM calls (default: 120).",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=None,
        metavar="N",
        help="Retry count for failed provider API calls (profile/env default if omitted).",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Fast mode: skip debate, concise prompts, labeled in output.",
    )
    _add_profile_argument(parser)
    _add_repair_json_argument(parser)
    _add_api_mode_argument(parser)
    _add_system_profile_argument(parser)


def _add_system_profile_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--system-profile",
        default="default",
        metavar="NAME",
        help="System prompt profile for role identity (default: default).",
    )


def _add_source_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--source-pack",
        action="append",
        dest="source_packs",
        default=None,
        metavar="SOURCE_PACK_ID",
        help="Attach a saved local source pack by ID (repeatable).",
    )
    parser.add_argument(
        "--source-path",
        action="append",
        dest="source_paths",
        default=None,
        metavar="PATH",
        help="Scan a local folder/file and attach as temporary source context (repeatable).",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    normalized = normalize_argv(argv)
    if not normalized:
        parser.print_help()
        raise SystemExit(2)
    args = parser.parse_args(normalized)
    args._argv = normalized
    return args


def _load_config_context() -> CouncilConfigFile | None:
    try:
        return load_config_file()
    except ConfigProfileError:
        raise


def _resolve_selected_profile(args: argparse.Namespace) -> ConfigProfile | None:
    config = _load_config_context()
    name = resolve_profile_name(cli_profile=getattr(args, "profile", None), config=config)
    if name is None:
        return None
    if config is None:
        config = load_config_file()
    if config is None:
        return None
    return config.get_profile(name)


def resolve_settings(args: argparse.Namespace) -> Settings:
    base = Settings.from_env()
    profile = _resolve_selected_profile(args)
    preset = getattr(args, "preset", None)
    settings = resolve_settings_with_profile(base, profile=profile, cli_preset=preset)
    runs_dir = getattr(args, "runs_dir", None)
    if runs_dir is not None:
        settings = Settings(
            llm_mode=settings.llm_mode,
            runs_dir=runs_dir,
            mock_model=settings.mock_model,
            openai_api_key=settings.openai_api_key,
            openai_model=settings.openai_model,
            llm_provider_name=settings.llm_provider_name,
            llm_base_url=settings.llm_base_url,
            llm_api_key=settings.llm_api_key,
            llm_model=settings.llm_model,
        )
    return settings


def resolve_runtime_options(args: argparse.Namespace) -> RuntimeOptions:
    from dataclasses import replace

    profile = _resolve_selected_profile(args)
    argv = getattr(args, "_argv", None)
    fast_explicit = bool(argv and "--fast" in argv)
    runtime = resolve_runtime_with_profile(
        profile,
        cli_timeout=getattr(args, "timeout_seconds", None),
        cli_max_retries=getattr(args, "max_retries", None),
        cli_fast=bool(getattr(args, "fast", False)),
        cli_fast_explicit=fast_explicit,
        quiet=bool(getattr(args, "quiet", False)),
        cli_repair_json=bool(getattr(args, "repair_json", False)),
        cli_api_mode=str(getattr(args, "api_mode", "auto")),
    )
    system_profile = getattr(args, "system_profile", None) or "default"
    return replace(runtime, system_profile=system_profile)


def _add_runs_dir_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=None,
        help="Directory for run artifacts (default: RUNS_DIR env or ./runs).",
    )


def resolve_runs_dir(args: argparse.Namespace) -> Path:
    settings = Settings.from_env()
    runs_dir = getattr(args, "runs_dir", None)
    if runs_dir is not None:
        return Path(runs_dir)
    return settings.runs_dir


def _settings_with_runs_dir(settings: Settings, runs_dir: Path) -> Settings:
    return Settings(
        llm_mode=settings.llm_mode,
        runs_dir=runs_dir,
        mock_model=settings.mock_model,
        openai_api_key=settings.openai_api_key,
        openai_model=settings.openai_model,
        llm_provider_name=settings.llm_provider_name,
        llm_base_url=settings.llm_base_url,
        llm_api_key=settings.llm_api_key,
        llm_model=settings.llm_model,
    )


def resolve_debate_rounds(args: argparse.Namespace, runtime: RuntimeOptions) -> int:
    profile = _resolve_selected_profile(args)
    return resolve_debate_rounds_with_profile(
        profile,
        cli_debate_rounds=getattr(args, "debate_rounds", None),
        runtime=runtime,
    )


def render_config_list(console: Console) -> None:
    config = load_config_file()
    if config is None:
        console.print("No config file found. Run: python main.py config init", style="yellow")
        return
    table = Table(title=f"Config profiles ({config.path})")
    table.add_column("Profile", style="bold")
    table.add_column("Active")
    table.add_column("Mode")
    table.add_column("Provider")
    table.add_column("Model / preset")
    for name in config.profile_names():
        profile = config.profiles[name]
        label = profile.preset or profile.model or "—"
        table.add_row(
            name,
            "yes" if name == config.active_profile else "",
            profile.mode or "—",
            profile.provider_name or "—",
            label,
        )
    console.print(table)


def render_config_show(console: Console, profile_name: str) -> None:
    config = load_config_file()
    if config is None:
        raise ConfigProfileError("No config file found. Run: python main.py config init")
    profile = config.get_profile(profile_name)
    table = Table(title=f"Profile: {profile_name}")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    for field, value in profile_display_rows(profile):
        table.add_row(field, value)
    console.print(table)


def render_config_init(console: Console, path: Path | None = None) -> None:
    created = init_config_file(path)
    console.print(f"[green]Config ready:[/green] {created}")
    console.print("API keys: environment variables or `python main.py secrets set`.")


def render_config_use(console: Console, profile_name: str) -> None:
    path = set_active_profile(profile_name)
    console.print(f"[green]Active profile:[/green] {profile_name} ({path})")


def render_preset_list(console: Console) -> None:
    table = Table(title="Model presets")
    table.add_column("Preset", style="bold")
    table.add_column("Provider")
    table.add_column("Model")
    table.add_column("Mode")
    table.add_column("Base URL")
    for name in list_preset_names():
        preset = MODEL_PRESETS[name]
        base_url = preset.base_url or "—"
        table.add_row(name, preset.provider_name, preset.model, preset.llm_mode, base_url)
    console.print(table)
    console.print(
        "\nAPI keys from env or keyring: OPENAI_API_KEY (openai presets) or LLM_API_KEY (compatible). "
        "Env wins over keyring. Ollama presets accept LLM_API_KEY=ollama (or omit — defaults locally)."
    )
    console.print(
        "Ollama preset models must match `ollama list` exactly — edit council/model_presets.py if needed."
    )


def _runs_show_command(run_id: str) -> str:
    return f"uv run python main.py runs show {run_id}"


def render_cost_estimate(
    console: Console,
    estimate,
    *,
    routing: CouncilRouting | None = None,
    preset_availability=None,
) -> None:
    table = Table(title="Council cost estimate (conservative; not live billing)")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("Routing mode", estimate.routing_mode)
    table.add_row("Debate rounds", str(estimate.debate_rounds))
    table.add_row("LLM calls (planned)", str(estimate.llm_call_count))
    table.add_row("Estimated USD", f"${estimate.estimated_cost_usd:.4f}")
    table.add_row("Range (low–high)", f"${estimate.estimated_cost_usd_low:.4f} – ${estimate.estimated_cost_usd_high:.4f}")
    table.add_row("Note", "Estimate from preset metadata only")
    console.print(table)

    if routing is not None:
        route_table = Table(title="Role routing")
        route_table.add_column("Role", style="bold")
        route_table.add_column("Preset")
        route_table.add_column("Tier")
        route_table.add_column("Calls")
        route_table.add_column("Est. USD")
        from council.preset_economics import get_preset_economics

        line_by_slot = {line.slot: line for line in estimate.slot_lines}
        for slot in ("researcher", "advocate", "skeptic", "risk", "operator", "chair"):
            assignment = routing.assignments[slot]
            tier = get_preset_economics(assignment.preset).cost_tier
            line = line_by_slot.get(slot)
            calls = str(line.llm_calls) if line else "0"
            usd = f"${line.estimated_usd:.4f}" if line else "—"
            route_table.add_row(slot, assignment.preset, tier, calls, usd)
        console.print(route_table)
        for warning in routing.routing_warnings:
            console.print(f"[yellow]{warning}[/yellow]")

    if preset_availability:
        avail_table = Table(title="Preset availability (estimate)")
        avail_table.add_column("Preset")
        avail_table.add_column("Credential source")
        avail_table.add_column("Availability")
        seen: set[str] = set()
        for item in preset_availability:
            if item.preset in seen:
                continue
            seen.add(item.preset)
            avail_table.add_row(
                item.preset,
                item.credential_source,
                item.display_availability(),
            )
        console.print(avail_table)


def render_council_result(
    console: Console,
    result,
    json_path: Path,
    md_path: Path,
    *,
    quiet: bool,
    role_play_warning: str | None = None,
    pack_paths: list[Path] | None = None,
    cost_estimate=None,
    routing_warnings: list[str] | None = None,
) -> None:
    dossier = result.dossier
    run_id = dossier.run_id
    next_cmd = _runs_show_command(run_id)
    confidence_pct = f"{dossier.confidence_score:.0%}"
    decision_type = decision_label(dossier.decision_type)
    direct = dossier.direct_answer.strip() or dossier.recommendation.split("\n", 1)[0]

    if quiet:
        console.print(json_path)
        console.print(md_path)
        for path in pack_paths or []:
            console.print(path)
        console.print(next_cmd)
        return

    if role_play_warning:
        console.print(f"[yellow]{role_play_warning}[/yellow]\n")
    for warning in routing_warnings or []:
        console.print(f"[yellow]{warning}[/yellow]")
    if routing_warnings:
        console.print()

    console.print(
        Panel.fit(
            f"[bold]Direct answer:[/bold] {direct}\n\n"
            f"[bold]Decision:[/bold] {decision_type}\n"
            f"Run ID: [cyan]{run_id}[/cyan]\n"
            f"Confidence: {confidence_pct}\n"
            f"Deciding factor: {dossier.deciding_factor}",
            title="Multi-Model Council Verdict",
            border_style="green",
        )
    )

    _print_preview_list(console, "Do next", dossier.next_actions)
    _print_preview_list(console, "Do not do", dossier.do_not_do)
    if dossier.approval_gate.strip():
        console.print(f"[bold]Approval gate[/bold]\n  {dossier.approval_gate.strip()}\n")

    if cost_estimate is not None:
        render_cost_estimate(console, cost_estimate)

    if result.role_assignments:
        table = Table(title="Models per role")
        table.add_column("Role", style="bold")
        table.add_column("Preset")
        table.add_column("Provider")
        table.add_column("Model")
        for item in result.role_assignments:
            table.add_row(item.slot, item.preset, item.provider_name, item.model_name)
        console.print(table)

    console.print("[green]Saved JSON:[/green]")
    console.print(f"  {json_path}", highlight=False, markup=False)
    console.print("[green]Saved Markdown:[/green]")
    console.print(f"  {md_path}", highlight=False, markup=False)
    if pack_paths:
        console.print("[green]Implementation pack:[/green]")
        for path in pack_paths:
            console.print(f"  {path}", highlight=False, markup=False)
    console.print("\n[bold]Next command:[/bold]")
    console.print(f"  {next_cmd}", highlight=False, markup=False)


def render_runs_list(console: Console, runs_dir: Path, *, limit: int = 10) -> None:
    summaries = list_recent_runs(runs_dir, limit=limit)
    if not summaries:
        console.print(f"No runs found in {runs_dir.resolve()}.")
        return

    table = Table(title=f"Recent runs (last {limit})")
    table.add_column("Kind", style="bold")
    table.add_column("Run ID")
    table.add_column("Time (UTC)")
    table.add_column("Status")
    table.add_column("Question")
    table.add_column("Chair")
    table.add_column("Thread")
    for item in summaries:
        kind_style = "cyan" if item.run_kind == "council" else "dim"
        question = item.question.replace("\n", " ")
        if len(question) > 44:
            question = question[:43] + "…"
        chair = f"{item.chair_provider} / {item.chair_model}"
        if len(chair) > 32:
            chair = chair[:31] + "…"
        if item.thread_id:
            thread_label = item.thread_id[:8] + "…"
        else:
            thread_label = "—"
        status_label = _lifecycle_label(item.lifecycle_status)
        table.add_row(
            f"[{kind_style}]{item.run_kind}[/{kind_style}]",
            item.run_id,
            item.timestamp.strftime("%Y-%m-%d %H:%M"),
            status_label,
            question or "—",
            chair,
            thread_label,
        )
    console.print(table)
    console.print(
        "\nInspect a run: [bold]uv run python main.py runs show RUN_ID[/bold]"
    )


_LIFECYCLE_STYLES: dict[str, str] = {
    "draft": "dim",
    "under_review": "yellow",
    "approved": "green",
    "rejected": "red",
    "superseded": "magenta",
    "archived": "dim",
}


def _lifecycle_label(status: str) -> str:
    """Color-coded short label for a lifecycle status. `approved` adds a
    leading check mark so the eye finds approved runs quickly in /runs."""
    style = _LIFECYCLE_STYLES.get(status, "white")
    text = "[v] approved" if status == "approved" else status
    return f"[{style}]{text}[/{style}]"


def render_review(console: Console, run_id: str, result) -> None:
    """Render the lifecycle-state panel for `review` (CLI) — mirrors the
    chat `/review` panel so the two surfaces look identical."""
    review = result.review
    thread = getattr(result, "decision_thread", None)
    status_style = _LIFECYCLE_STYLES.get(review.status.value, "white")
    lines = [
        f"Run ID    : [cyan]{run_id}[/cyan]",
        f"Status    : [{status_style}]{review.status.value}[/{status_style}]",
    ]
    if review.approved_by:
        lines.append(f"Approved  : {review.approved_by}")
    if review.rejected_by:
        lines.append(f"Rejected  : {review.rejected_by}")
    if review.review_reason:
        lines.append(f"Note      : {review.review_reason}")
    if review.reviewed_at:
        lines.append(
            f"Reviewed  : {review.reviewed_at.strftime('%Y-%m-%d %H:%M:%SZ')}"
        )
    if review.is_revision_of:
        lines.append(f"Revision of : [cyan]{review.is_revision_of}[/cyan]")
    if review.superseded_by_run_id:
        lines.append(
            f"Superseded by: [cyan]{review.superseded_by_run_id}[/cyan]"
        )
    if thread is not None:
        lines.append(f"Thread    : [cyan]{thread.thread_id}[/cyan]")
        lines.append(f"Parent    : [cyan]{thread.parent_run_id}[/cyan]")
    if review.history:
        lines.append("")
        lines.append("History:")
        for event in review.history:
            ts = event.timestamp.strftime("%Y-%m-%d %H:%M:%SZ")
            note = f" — {event.note}" if event.note else ""
            lines.append(
                f"  {ts}  {event.action.value} by {event.actor}{note}"
            )
    console.print(
        Panel("\n".join(lines), title="Decision Review", border_style="blue")
    )


def _run_artifact_path_lines(path: Path) -> tuple[str, str]:
    """Return (display path, full path) for run artifact output."""
    resolved = path.resolve()
    full = str(resolved)
    try:
        display = str(resolved.relative_to(Path.cwd()))
    except ValueError:
        display = full
    return display, full


def render_runs_show(console: Console, summary) -> None:
    confidence = (
        f"{summary.confidence_score:.0%}"
        if summary.confidence_score is not None
        else "—"
    )
    thread_line = ""
    if summary.thread_id or summary.parent_run_id:
        thread_id = summary.thread_id or "—"
        parent = summary.parent_run_id or "—"
        thread_line = f"\nThread: [cyan]{thread_id}[/cyan]\nParent: [cyan]{parent}[/cyan]"
    review_lines = f"\nStatus: {_lifecycle_label(summary.lifecycle_status)}"
    if summary.is_revision_of:
        review_lines += f"\nRevision of: [cyan]{summary.is_revision_of}[/cyan]"
    if summary.superseded_by_run_id:
        review_lines += f"\nSuperseded by: [cyan]{summary.superseded_by_run_id}[/cyan]"
    console.print(
        Panel.fit(
            f"Run ID: [cyan]{summary.run_id}[/cyan]\n"
            f"Kind: {summary.run_kind}\n"
            f"Time (UTC): {summary.timestamp.isoformat()}\n"
            f"Confidence: {confidence}"
            f"{review_lines}"
            f"{thread_line}",
            title="Run Summary",
        )
    )
    console.print(f"[bold]Question[/bold]\n{summary.question or '—'}\n")
    console.print(f"[bold]Verdict preview[/bold]\n{summary.decision_preview or '—'}\n")
    console.print(f"[bold]Chair[/bold] {summary.chair_provider} / {summary.chair_model}\n")

    md_display, md_full = _run_artifact_path_lines(summary.md_path)
    json_display, json_full = _run_artifact_path_lines(summary.json_path)
    console.print("[bold]Markdown[/bold]")
    console.print(f"  File: {summary.md_path.name}", highlight=False, markup=False)
    console.print(f"  Open: {md_full}", highlight=False, markup=False)
    if md_display != md_full:
        console.print(f"  Path: {md_display}", highlight=False, markup=False)
    console.print("[bold]JSON[/bold]")
    console.print(f"  File: {summary.json_path.name}", highlight=False, markup=False)
    console.print(f"  Open: {json_full}", highlight=False, markup=False)
    if json_display != json_full:
        console.print(f"  Path: {json_display}", highlight=False, markup=False)

    if not summary.md_path.is_file():
        console.print("\n[yellow]Markdown file missing on disk.[/yellow]")


def render_sources_list(console: Console, packs: list[SourcePack]) -> None:
    if not packs:
        console.print("No source packs found. Run: uv run python main.py sources scan PATH")
        return
    table = Table(title="Source packs")
    table.add_column("ID", style="bold")
    table.add_column("Name")
    table.add_column("Files")
    table.add_column("Bytes")
    table.add_column("Root / files")
    for pack in packs:
        table.add_row(
            pack.source_pack_id,
            pack.name,
            str(pack.file_count),
            str(pack.total_bytes),
            pack.display_root,
        )
    console.print(table)


def render_sources_show(console: Console, pack: SourcePack) -> None:
    lines = [
        f"ID: [cyan]{pack.source_pack_id}[/cyan]",
        f"Name: {pack.name}",
        f"Created: {pack.created_at.isoformat()}",
        f"Files: {pack.file_count}",
        f"Bytes: {pack.total_bytes}",
        f"Extensions: {', '.join(pack.included_extensions) if pack.included_extensions else '—'}",
    ]
    if pack.root_path:
        lines.append(f"Root: {pack.root_path}")
    if pack.warnings:
        lines.append("Warnings:")
        lines.extend(f"  - {item}" for item in pack.warnings)
    lines.append("")
    lines.append("Top files:")
    for summary in pack.summaries[:10]:
        lines.append(f"  - {summary.path} ({summary.size_bytes} bytes)")
    console.print(Panel("\n".join(lines), title="Source pack", border_style="cyan"))


def render_sources_remove(console: Console, source_pack_id: str, removed: bool) -> None:
    if removed:
        console.print(f"[green]Removed source pack[/green] {source_pack_id}")
    else:
        console.print(f"[yellow]Source pack not found:[/yellow] {source_pack_id}")


def render_sources_query(console: Console, source_pack_id: str, payload) -> None:
    lines = [f"Source pack: [cyan]{source_pack_id}[/cyan]"]
    if payload.matched_keywords:
        lines.append(f"Matched keywords: {', '.join(payload.matched_keywords[:12])}")
    lines.append("")
    lines.append("Top ranked files:")
    for item in payload.relevance[:10]:
        lines.append(f"- {item.path}")
        lines.append(f"  - score: {item.score:.2f}")
        if item.matched_terms:
            lines.append(f"  - matched: {', '.join(item.matched_terms[:8])}")
        if item.why_selected:
            lines.append(f"  - why: {', '.join(item.why_selected[:6])}")
        for snippet in item.snippets[:2]:
            lines.append(f"  - snippet: {snippet}")
    if payload.excluded_files:
        lines.append("")
        lines.append("Excluded:")
        lines.extend(f"- {entry}" for entry in payload.excluded_files[:10])
    console.print(Panel("\n".join(lines), title="Source relevance query", border_style="cyan"))


def render_smoke_report(console: Console, report: SmokeReport) -> None:
    status = "[green]success[/green]" if report.success else "[red]failure[/red]"
    table = Table(title=f"Smoke test — {report.preset} ({status})")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("Question", report.question)
    table.add_row("Provider", report.provider_name or "—")
    table.add_row("Model", report.model_name or "—")
    table.add_row("Elapsed", f"{report.elapsed_seconds:.2f}s")
    if report.success:
        confidence_pct = f"{(report.confidence_score or 0) * 100:.0f}%"
        if report.api_mode_preference:
            table.add_row("API mode (preference)", report.api_mode_preference)
        if report.api_mode_used:
            table.add_row("API mode (used)", report.api_mode_used)
        table.add_row("Run ID", report.run_id or "—")
        table.add_row("Decision type", report.decision_type or "—")
        table.add_row("Confidence", confidence_pct)
        table.add_row("Evidence gaps present", "yes" if report.has_evidence_gaps else "no")
        table.add_row("Proposed metrics present", "yes" if report.has_proposed_metrics else "no")
        table.add_row("JSON artifact", report.run_json_path or "—")
        table.add_row("Markdown artifact", report.run_md_path or "—")
    else:
        if report.failed_stage:
            table.add_row("Failed stage", report.failed_stage)
        if report.api_mode_preference:
            table.add_row("API mode (preference)", report.api_mode_preference)
        if report.failure_reason:
            table.add_row("Failure reason", report.failure_reason)
        table.add_row("Error", report.error or "Unknown error")
        if report.auth_failure:
            table.add_row("Auth failure", "yes")
            table.add_row("Credential source", report.credential_source or "missing")
    console.print(table)


def render_comparison_result(
    console: Console,
    report: ComparisonReport,
    json_path: Path,
    md_path: Path,
    *,
    quiet: bool,
) -> None:
    if quiet:
        console.print(json_path)
        console.print(md_path)
        return

    table = Table(title=f"Comparison {report.comparison_id}")
    table.add_column("Target", style="bold")
    table.add_column("Kind")
    table.add_column("Status")
    table.add_column("Decision")
    table.add_column("Confidence")
    for entry in report.entries:
        if entry.success:
            status = "[green]ok[/green]"
            decision = entry.decision_type or "—"
            confidence = f"{(entry.confidence_score or 0) * 100:.0f}%"
        else:
            status = "[red]failed[/red]"
            decision = "—"
            confidence = "—"
        table.add_row(entry.label, entry.kind, status, decision, confidence)
    console.print(table)
    console.print()
    ev = report.evaluator
    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="bold")
    summary.add_column()
    summary.add_row("Question", report.question)
    summary.add_row("Debate rounds", str(report.debate_rounds))
    summary.add_row("Most decisive", ev.most_decisive)
    summary.add_row("Recommended", ev.recommended)
    console.print(summary)
    console.print()
    try:
        json_display = json_path.relative_to(Path.cwd())
        md_display = md_path.relative_to(Path.cwd())
    except ValueError:
        json_display = json_path
        md_display = md_path
    console.print(f"[green]Saved comparison JSON:[/green] {json_display}")
    console.print(f"[green]Saved comparison Markdown:[/green] {md_display}")


def render_secrets_set(console: Console, name: str, *, prompt_for_value: Callable[[str], str]) -> None:
    validate_secret_name(name)
    value = prompt_for_value(f"{name}: ")
    if not value.strip():
        console.print(f"[yellow]{name} not changed[/yellow] (empty input).")
        return
    set_keyring_secret(name, value.strip())
    console.print(f"[green]{name} stored in OS keyring.[/green]")


def render_secrets_get(console: Console, name: str) -> None:
    validate_secret_name(name)
    from council.secrets import credential_source

    source = credential_source(name)
    if source == "missing":
        console.print(f"{name} is not set (source: missing).")
    else:
        console.print(f"{name} is set (source: {source}).")


def render_secrets_list(console: Console) -> None:
    table = Table(title="Secrets (env overrides keyring)")
    table.add_column("Name", style="bold")
    table.add_column("Available")
    table.add_column("Source")
    from council.secrets import credential_source

    for name, available in list_secret_statuses():
        source = credential_source(name)
        table.add_row(
            name,
            "yes" if available else "no",
            source if available else "missing",
        )
    console.print(table)


def render_secrets_delete(console: Console, name: str) -> None:
    validate_secret_name(name)
    delete_keyring_secret(name)
    console.print(f"[green]{name} removed from OS keyring.[/green]")


def render_prompts_inventory(console: Console, *, system_profile: str = "default") -> None:
    profile = load_system_profile(system_profile)
    bundle_hash = profile_bundle_hash(system_profile)
    console.print(
        Panel.fit(
            f"Profile: [cyan]{profile.name}[/cyan] (v{profile.profile_version})\n"
            f"Bundle hash: {bundle_hash}",
            title="System prompts",
        )
    )
    table = Table(title="Prompt files")
    table.add_column("File", style="bold")
    table.add_column("Version")
    table.add_column("SHA-256", overflow="fold")
    table.add_column("Modified (UTC)")
    for record in list_prompt_files():
        table.add_row(
            record.relative_name,
            record.version,
            record.sha256[:16] + "…",
            record.modified_at.strftime("%Y-%m-%d %H:%M"),
        )
    console.print(table)
    profiles = list_system_profiles()
    if profiles:
        console.print(f"\nAvailable profiles: {', '.join(profiles)}")


def render_version(console: Console) -> None:
    table = Table(title="decision-council-lab")
    table.add_column("Key", style="bold")
    table.add_column("Value")
    table.add_row("App version", APP_VERSION)
    table.add_row("Schema version", RUN_SCHEMA_VERSION)
    table.add_row("Supported modes", ", ".join(SUPPORTED_LLM_MODES))
    console.print(table)


def render_doctor(console: Console, checks: list[DoctorCheck]) -> int:
    table = Table(title="Doctor")
    table.add_column("Check", style="bold")
    table.add_column("Status")
    table.add_column("Message")
    exit_code = 0
    for check in checks:
        if check.status == CheckStatus.FAIL:
            exit_code = 1
        style = {
            CheckStatus.OK: "green",
            CheckStatus.WARN: "yellow",
            CheckStatus.FAIL: "red",
            CheckStatus.SKIP: "dim",
        }.get(check.status, "")
        table.add_row(check.name, f"[{style}]{check.status.value}[/{style}]", check.message)
    console.print(table)
    return exit_code


def render_result(
    console: Console,
    result: CouncilRunResult,
    json_path: Path,
    md_path: Path,
    *,
    quiet: bool,
    runs_dir: Path,
    prompt_debug_path: Path | None = None,
    fast_mode: bool = False,
) -> None:
    if quiet:
        console.print(json_path)
        console.print(md_path)
        if prompt_debug_path is not None:
            console.print(prompt_debug_path)
        return

    dossier = result.dossier
    confidence_pct = f"{dossier.confidence_score:.0%}"
    decision_type = decision_label(dossier.decision_type)
    direct = dossier.direct_answer.strip() or dossier.recommendation.split("\n", 1)[0]
    fast_label = "\n[yellow]Fast mode[/yellow]" if fast_mode else ""

    console.print(
        Panel.fit(
            f"[bold]Direct answer:[/bold] {direct}\n\n"
            f"[bold]Decision:[/bold] {decision_type}\n"
            f"Confidence: {confidence_pct}\n"
            f"Run ID: {dossier.run_id}\n"
            f"Mode: {result.provider_metadata.mode} "
            f"({result.provider_metadata.provider_name} / {result.provider_metadata.model_name})"
            f"{fast_label}",
            title="Executive Summary",
        )
    )

    preview = Table.grid(padding=(0, 2))
    preview.add_column(style="bold")
    preview.add_column()
    preview.add_row("Question", dossier.decision_question)
    preview.add_row("Deciding factor", dossier.deciding_factor)
    preview.add_row("Timestamp", dossier.timestamp.isoformat())
    preview.add_row("Runs dir", str(runs_dir.resolve()))
    if fast_mode:
        preview.add_row("Profile", "fast (debate skipped, concise prompts)")
    console.print(preview)
    console.print()

    _print_preview_list(console, "Top risks", dossier.risks)
    _print_preview_list(console, "Do next", dossier.next_actions)
    _print_preview_list(console, "Do not do", dossier.do_not_do)
    if dossier.approval_gate.strip():
        console.print(f"[bold]Approval gate[/bold]\n  {dossier.approval_gate.strip()}\n")

    try:
        json_display = json_path.relative_to(Path.cwd())
        md_display = md_path.relative_to(Path.cwd())
    except ValueError:
        json_display = json_path
        md_display = md_path

    console.print(f"[green]Saved JSON:[/green] {json_display}")
    console.print(f"[green]Saved Markdown:[/green] {md_display}")
    if prompt_debug_path is not None:
        try:
            debug_display = prompt_debug_path.relative_to(Path.cwd())
        except ValueError:
            debug_display = prompt_debug_path
        console.print(f"[green]Saved prompt debug:[/green] {debug_display}")


def format_user_error(exc: Exception) -> str:
    if isinstance(exc, ProviderResponseError) and exc.source == "api":
        return exc.detail
    return str(exc)


def render_known_error(console: Console, exc: Exception, *, quiet: bool) -> None:
    message = format_user_error(exc)
    if quiet:
        console.print(message, style="red")
        return
    console.print(Panel.fit(message, title="Error", border_style="red"))


def _print_preview_list(console: Console, title: str, items: list[str]) -> None:
    if not items:
        return
    console.print(f"[bold]{title}[/bold]")
    for item in items[:PREVIEW_ITEM_COUNT]:
        console.print(f"  • {item}")
    if len(items) > PREVIEW_ITEM_COUNT:
        console.print(f"  … and {len(items) - PREVIEW_ITEM_COUNT} more in the dossier")
    console.print()
