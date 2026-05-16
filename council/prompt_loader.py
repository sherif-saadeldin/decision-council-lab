from __future__ import annotations

import contextvars
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import tomllib

from council.models import AgentRole

_active_profile: contextvars.ContextVar[str] = contextvars.ContextVar(
    "system_profile",
    default="default",
)

_PACKAGE_ROOT = Path(__file__).resolve().parent
SYSTEM_PROMPTS_DIR = _PACKAGE_ROOT / "system_prompts"
SYSTEM_PROFILES_DIR = _PACKAGE_ROOT / "system_profiles"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

_AGENT_ROLE_KEYS: dict[AgentRole, str] = {
    AgentRole.RESEARCH: "research",
    AgentRole.SKEPTIC: "skeptic",
    AgentRole.RISK: "risk",
    AgentRole.OPERATOR: "operator",
    AgentRole.CHAIR: "chair",
}

_DEBATE_ROLE_KEYS: dict[str, str] = {
    "advocate": "advocate",
    "skeptic": "skeptic",
}


class PromptLoadError(FileNotFoundError):
    """Raised when a system prompt file or profile cannot be loaded."""


class UnknownSystemProfileError(PromptLoadError):
    def __init__(self, profile_name: str, available: tuple[str, ...]) -> None:
        self.profile_name = profile_name
        self.available_profiles = available
        available_text = ", ".join(available) or "(none)"
        super().__init__(
            f"Unknown system profile {profile_name!r}. Available profiles: {available_text}."
        )


@dataclass(frozen=True)
class PromptFileRecord:
    """Loaded markdown prompt with metadata."""

    relative_name: str
    absolute_path: Path
    content: str
    version: str
    sha256: str
    modified_at: datetime

    @property
    def display_name(self) -> str:
        return self.relative_name


@dataclass(frozen=True)
class SystemProfile:
    name: str
    profile_version: str
    base_file: str
    role_files: dict[str, str]
    profile_path: Path

    def role_filename(self, role_key: str) -> str | None:
        return self.role_files.get(role_key)

    def all_relative_files(self) -> tuple[str, ...]:
        names = [self.base_file, *sorted(set(self.role_files.values()))]
        return tuple(dict.fromkeys(names))


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        return {}, raw
    block = match.group(1)
    body = raw[match.end() :]
    meta: dict[str, str] = {}
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" in stripped:
            key, value = stripped.split(":", 1)
            meta[key.strip()] = value.strip().strip('"').strip("'")
        elif "=" in stripped:
            key, value = stripped.split("=", 1)
            meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta, body.strip()


def _file_mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


@lru_cache(maxsize=64)
def _load_prompt_markdown_cached(relative_name: str) -> PromptFileRecord:
    path = SYSTEM_PROMPTS_DIR / relative_name
    if not path.is_file():
        msg = f"System prompt file not found: {relative_name} (expected at {path})"
        raise PromptLoadError(msg)
    raw = path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(raw)
    digest = _sha256_text(raw)
    version = meta.get("version") or digest[:12]
    return PromptFileRecord(
        relative_name=relative_name,
        absolute_path=path,
        content=body,
        version=version,
        sha256=digest,
        modified_at=_file_mtime(path),
    )


def load_prompt_file(relative_name: str) -> PromptFileRecord:
    return _load_prompt_markdown_cached(relative_name)


def list_prompt_files() -> list[PromptFileRecord]:
    if not SYSTEM_PROMPTS_DIR.is_dir():
        return []
    records: list[PromptFileRecord] = []
    for path in sorted(SYSTEM_PROMPTS_DIR.glob("*.md")):
        records.append(load_prompt_file(path.name))
    return records


@lru_cache(maxsize=16)
def load_system_profile(profile_name: str) -> SystemProfile:
    path = SYSTEM_PROFILES_DIR / f"{profile_name}.toml"
    if not path.is_file():
        available = tuple(p.stem for p in SYSTEM_PROFILES_DIR.glob("*.toml"))
        raise UnknownSystemProfileError(profile_name, available)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    prompts = data.get("prompts")
    if not isinstance(prompts, dict):
        msg = f"Profile {profile_name!r} is missing [prompts] section."
        raise PromptLoadError(msg)
    base_file = prompts.get("base")
    if not isinstance(base_file, str) or not base_file.strip():
        msg = f"Profile {profile_name!r} must set prompts.base."
        raise PromptLoadError(msg)
    roles_raw = data.get("roles")
    role_files: dict[str, str] = {}
    if isinstance(roles_raw, dict):
        for key, value in roles_raw.items():
            if isinstance(key, str) and isinstance(value, str) and value.strip():
                role_files[key] = value.strip()
    return SystemProfile(
        name=str(data.get("name") or profile_name),
        profile_version=str(data.get("version") or "1"),
        base_file=base_file.strip(),
        role_files=role_files,
        profile_path=path,
    )


def list_system_profiles() -> list[str]:
    if not SYSTEM_PROFILES_DIR.is_dir():
        return []
    return sorted(path.stem for path in SYSTEM_PROFILES_DIR.glob("*.toml"))


def agent_role_key(role: AgentRole) -> str | None:
    return _AGENT_ROLE_KEYS.get(role)


def debate_role_key(debate_role: str) -> str | None:
    return _DEBATE_ROLE_KEYS.get(debate_role)


def compose_system_prompt(
    role_key: str | None,
    *,
    profile_name: str = "default",
    suffix: str = "",
) -> str:
    """Compose base + optional role markdown + programmatic suffix."""
    profile = load_system_profile(profile_name)
    base = load_prompt_file(profile.base_file)
    parts = [base.content]
    if role_key:
        role_file = profile.role_filename(role_key)
        if role_file is None:
            msg = f"Role {role_key!r} is not defined in system profile {profile_name!r}."
            raise PromptLoadError(msg)
        role_record = load_prompt_file(role_file)
        if role_record.relative_name != base.relative_name:
            parts.append(role_record.content)
    if suffix.strip():
        parts.append(suffix.strip())
    return "\n\n".join(parts)


def profile_prompt_records(profile_name: str) -> list[PromptFileRecord]:
    profile = load_system_profile(profile_name)
    return [load_prompt_file(name) for name in profile.all_relative_files()]


def profile_bundle_hash(profile_name: str) -> str:
    profile = load_system_profile(profile_name)
    hasher = hashlib.sha256()
    hasher.update(profile.name.encode("utf-8"))
    hasher.update(profile.profile_version.encode("utf-8"))
    for relative_name in profile.all_relative_files():
        record = load_prompt_file(relative_name)
        hasher.update(record.sha256.encode("utf-8"))
    return hasher.hexdigest()


def build_prompt_versions(profile_name: str) -> dict[str, str]:
    return {record.relative_name: record.version for record in profile_prompt_records(profile_name)}


def clear_prompt_cache() -> None:
    _load_prompt_markdown_cached.cache_clear()
    load_system_profile.cache_clear()


def get_system_profile() -> str:
    return _active_profile.get()


class system_profile_context:
    """Temporarily set the active system prompt profile for a council run."""

    def __init__(self, profile_name: str) -> None:
        self._profile_name = profile_name
        self._token: contextvars.Token[str] | None = None

    def __enter__(self) -> str:
        self._token = _active_profile.set(self._profile_name)
        return self._profile_name

    def __exit__(self, *_args: object) -> None:
        if self._token is not None:
            _active_profile.reset(self._token)
