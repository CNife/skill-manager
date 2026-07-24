"""XDG-aware path resolution for skill-manager.

All path functions are pure: they compute locations without creating them.
Directory creation is the caller's responsibility (config/sources layers).
"""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "skill-manager"


def _xdg(env_var: str, default_subdir: str) -> Path:
    """Resolve an XDG base directory, falling back to $HOME/<default_subdir>."""
    value = os.environ.get(env_var)
    if value:
        return Path(value)
    return Path.home() / default_subdir


def config_dir() -> Path:
    """Global config directory: $XDG_CONFIG_HOME/skill-manager (default ~/.config/skill-manager)."""
    return _xdg("XDG_CONFIG_HOME", ".config") / APP_NAME


def config_file() -> Path:
    """Global config file path."""
    return config_dir() / "config.json"


def cache_dir() -> Path:
    """Global cache directory: $XDG_CACHE_HOME/skill-manager (default ~/.cache/skill-manager)."""
    return _xdg("XDG_CACHE_HOME", ".cache") / APP_NAME


def repos_cache_dir() -> Path:
    """Root of cloned source repositories."""
    return cache_dir() / "repos"


def project_config_path(project_dir: Path | None = None) -> Path:
    """Path to the project's ``.skill-manager.json``."""
    base = Path(project_dir) if project_dir is not None else Path.cwd()
    return base / ".skill-manager.json"


def project_skills_dir(project_dir: Path | None = None) -> Path:
    """Path to the project's skill link directory (``./.agents/skills/``)."""
    base = Path(project_dir) if project_dir is not None else Path.cwd()
    return base / ".agents" / "skills"


def global_skills_config_path() -> Path:
    """Path to the user-global skills declaration file (``~/.skill-manager.json``).

    Same shape as a project config; the user's home is treated as the global
    "project". Sources and the cache remain shared via :func:`config_file` and
    :func:`repos_cache_dir`.
    """
    return Path.home() / ".skill-manager.json"


def global_skills_dir() -> Path:
    """Path to the user-global skill link directory (``~/.agents/skills/``)."""
    return Path.home() / ".agents" / "skills"
