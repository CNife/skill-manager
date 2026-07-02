"""Config data model, JSON I/O, and validation for skill-manager.

Pure I/O + validation: functions take explicit Path arguments and do not import
paths.py. Callers (sources/cli layers) resolve locations via paths.* and pass them in.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath


class ConfigError(Exception):
    """Raised when a config file is missing, malformed, or invalid."""


@dataclass(frozen=True)
class SkillRef:
    name: str
    repo: str
    path: str


@dataclass
class ProjectConfig:
    skills: list[SkillRef]


@dataclass(frozen=True)
class Source:
    repo: str
    commit: str
    url: str


@dataclass
class GlobalConfig:
    sources: dict[str, Source] = field(default_factory=dict)


def derived_sources(config: ProjectConfig) -> list[str]:
    """Unique repo identifiers from a project config, in first-seen order."""
    seen: set[str] = set()
    repos: list[str] = []
    for skill in config.skills:
        if skill.repo not in seen:
            seen.add(skill.repo)
            repos.append(skill.repo)
    return repos


def _validate_name(name: str, path: Path) -> None:
    if not name or PurePosixPath(name).name != name or name in (".", ".."):
        raise ConfigError(f"skill name {name!r} must be a single path component in {path}")


def _is_safe_repo_component(value: str) -> bool:
    """True when value is a single GitHub-like slug component, not a path."""
    return (
        bool(value)
        and value not in (".", "..")
        and "\\" not in value
        and PurePosixPath(value).name == value
        and all(c.isascii() and (c.isalnum() or c in "._-") for c in value)
    )


def _validate_repo(repo: str, name: str, path: Path) -> None:
    parts = repo.split("/")
    if len(parts) != 2 or not all(_is_safe_repo_component(part) for part in parts):
        raise ConfigError(f"skill {name!r} repo {repo!r} must be safe 'owner/repo' in {path}")


def validate_repo(repo: str, context: str) -> None:
    """Validate that ``repo`` is a safe ``owner/repo`` format.


    Returns ``None`` on success; raises ``ConfigError`` with a message
    including ``context`` (e.g. the CLI command name) on failure.
    """
    parts = repo.split("/")
    if len(parts) != 2 or not all(_is_safe_repo_component(part) for part in parts):
        raise ConfigError(f"invalid repo {repo!r}: must be 'owner/repo' ({context})")


def _validate_path(skill_path: str, name: str, path: Path) -> None:
    if skill_path == ".":
        return
    segments = skill_path.split("/")
    if (
        PurePosixPath(skill_path).is_absolute()
        or "\\" in skill_path
        or any(segment in ("", ".", "..") for segment in segments)
    ):
        raise ConfigError(
            f"skill {name!r} path {skill_path!r} must be a relative repo-internal directory in {path}"
        )


def _parse_skill(item: object, path: Path) -> SkillRef:
    if not isinstance(item, dict):
        raise ConfigError(f"each skill must be an object in {path}, got {type(item).__name__}")
    name = item.get("name")
    repo = item.get("repo")
    skill_path = item.get("path")
    if not isinstance(name, str) or not name:
        raise ConfigError(f"skill entry missing 'name' in {path}: {item!r}")
    if not isinstance(repo, str) or not repo:
        raise ConfigError(f"skill {name!r} missing 'repo' in {path}")
    if not isinstance(skill_path, str) or not skill_path:
        raise ConfigError(f"skill {name!r} missing 'path' in {path}")
    _validate_name(name, path)
    _validate_repo(repo, name, path)
    _validate_path(skill_path, name, path)
    return SkillRef(name=name, repo=repo, path=skill_path)


def _check_duplicate_names(skills: list[SkillRef], path: Path) -> None:
    seen: set[str] = set()
    for skill in skills:
        if skill.name in seen:
            raise ConfigError(f"duplicate skill name {skill.name!r} in {path}")
        seen.add(skill.name)


def load_project_config(path: Path) -> ProjectConfig:
    if not path.is_file():
        raise ConfigError(f"project config not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"invalid JSON in {path}: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(
            f"project config must be a JSON object in {path}, got {type(data).__name__}"
        )
    skills_raw = data.get("skills")
    if not isinstance(skills_raw, list):
        raise ConfigError(f"project config missing 'skills' list in {path}")
    skills = [_parse_skill(item, path) for item in skills_raw]
    _check_duplicate_names(skills, path)
    return ProjectConfig(skills=skills)


def load_global_config(path: Path) -> GlobalConfig:
    if not path.is_file():
        return GlobalConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"invalid JSON in {path}: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(
            f"global config must be a JSON object in {path}, got {type(data).__name__}"
        )
    sources_raw = data.get("sources", {})
    if not isinstance(sources_raw, dict):
        raise ConfigError(f"global config 'sources' must be an object in {path}")
    sources: dict[str, Source] = {}
    for repo, entry in sources_raw.items():
        if not isinstance(entry, dict):
            raise ConfigError(f"global config source {repo!r} must be an object in {path}")
        commit = entry.get("commit", "")
        url = entry.get("url", "")
        if not isinstance(commit, str) or not isinstance(url, str):
            raise ConfigError(f"global config source {repo!r} has non-string fields in {path}")
        sources[repo] = Source(repo=repo, commit=commit, url=url)
    return GlobalConfig(sources=sources)


def save_global_config(path: Path, config: GlobalConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "sources": {
            repo: {"commit": src.commit, "url": src.url} for repo, src in config.sources.items()
        }
    }
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def save_project_config(path: Path, config: ProjectConfig) -> None:
    """Save a ProjectConfig back to a JSON file.

    Creates parent directories if they don't exist. Uses the same JSON
    shape as load_project_config expects.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"skills": [{"name": s.name, "repo": s.repo, "path": s.path} for s in config.skills]}
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
