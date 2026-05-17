from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from council.sources.models import SourceFileSummary, SourcePack
from council.sources.summarizer import summarize_text_file

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".md",
        ".txt",
        ".json",
        ".csv",
        ".yaml",
        ".yml",
        ".toml",
        ".py",
        ".js",
        ".ts",
        ".tsx",
    }
)

IGNORED_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "__pycache__",
        "dist",
        "build",
        "runs",
        ".dcouncil",
    }
)


@dataclass(frozen=True)
class ScanLimits:
    max_file_bytes: int = 250_000
    max_total_bytes: int = 1_500_000
    follow_symlinks: bool = False


def scan_source_input(
    *,
    source_pack_id: str,
    name: str,
    source_path: Path,
    limits: ScanLimits | None = None,
) -> SourcePack:
    limits = limits or ScanLimits()
    root = source_path.expanduser().resolve()
    ignored: list[str] = []
    warnings: list[str] = []
    summaries: list[SourceFileSummary] = []
    total_bytes = 0

    if root.is_file():
        candidates = [root]
        root_path: Path | None = None
        file_paths = [root]
    else:
        candidates = _iter_text_files(root, limits=limits, ignored=ignored)
        root_path = root
        file_paths = []

    for file_path in candidates:
        rel = _relative_display(file_path, root_path)
        extension = file_path.suffix.lower()
        if extension not in SUPPORTED_EXTENSIONS:
            ignored.append(f"{rel} (unsupported extension)")
            continue
        if file_path.is_symlink() and not limits.follow_symlinks:
            ignored.append(f"{rel} (symlink)")
            continue
        size_bytes = file_path.stat().st_size
        if size_bytes > limits.max_file_bytes:
            ignored.append(f"{rel} (too large)")
            continue
        if _is_binary(file_path):
            ignored.append(f"{rel} (binary)")
            continue
        if total_bytes + size_bytes > limits.max_total_bytes:
            warnings.append("max total source size reached; remaining files skipped")
            break
        text = file_path.read_text(encoding="utf-8", errors="replace")
        summary = summarize_text_file(
            file_path=file_path,
            rel_path=rel,
            extension=extension,
            content=text,
            size_bytes=size_bytes,
        )
        summaries.append(summary)
        total_bytes += size_bytes
        if root_path is None:
            file_paths.append(file_path)

    return SourcePack.from_paths(
        source_pack_id=source_pack_id,
        name=name,
        root_path=root_path,
        file_paths=file_paths,
        summaries=summaries,
        ignored_files=ignored[:300],
        warnings=_dedupe(warnings),
    )


def _iter_text_files(
    root: Path,
    *,
    limits: ScanLimits,
    ignored: list[str],
) -> list[Path]:
    if not root.exists():
        return []
    if not root.is_dir():
        return [root]
    output: list[Path] = []
    for child in root.rglob("*"):
        if child.is_symlink() and not limits.follow_symlinks:
            ignored.append(f"{child} (symlink)")
            continue
        if child.is_dir():
            if child.name in IGNORED_DIR_NAMES:
                ignored.append(f"{child} (ignored directory)")
            continue
        if _is_in_ignored_dir(child, root):
            ignored.append(f"{child} (ignored directory)")
            continue
        output.append(child)
    output.sort()
    return output


def _is_in_ignored_dir(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    return any(part in IGNORED_DIR_NAMES for part in rel.parts[:-1])


def _relative_display(path: Path, root: Path | None) -> str:
    if root is None:
        return str(path)
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _is_binary(path: Path) -> bool:
    data = path.read_bytes()[:4096]
    return b"\x00" in data


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output

