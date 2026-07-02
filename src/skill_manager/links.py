"""Symlink creation for project skills.

ensure_link creates a symlink in the project's ``.agents/skills/`` directory
pointing at a cloned skill's directory in the source cache. It never overwrites
an existing non-tool entry (safety default per FRD).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from skill_manager.config import SkillRef


class LinkError(Exception):
    """Raised when a skill's source path is missing or has no SKILL.md."""


@dataclass(frozen=True)
class LinkResult:
    name: str
    action: str  # "created" | "exists" | "skipped"
    target: Path


def link_points_to(link: Path, target: Path) -> bool:
    """True if ``link`` is a symlink whose stored target resolves to ``target``."""
    if not link.is_symlink():
        return False
    stored = Path(os.readlink(link))
    resolved = (link.parent / stored).resolve() if not stored.is_absolute() else stored
    return resolved == target.resolve()


def ensure_link(skill: SkillRef, cache_root: Path, skills_dir: Path) -> LinkResult:
    """Create or verify a symlink for ``skill`` under ``skills_dir``.

    Target is the absolute ``cache_root/<repo>/<path>`` directory. Returns a
    LinkResult: ``created`` (new), ``exists`` (already points to target), or
    ``skipped`` (occupied by an external symlink or non-symlink entry).
    """
    repo_root = (cache_root / skill.repo).resolve()
    target = (repo_root / skill.path).resolve()
    if not target.is_relative_to(repo_root):
        raise LinkError(f"skill {skill.name!r} path {skill.path!r} escapes repo {skill.repo}")
    if not target.is_dir():
        raise LinkError(f"skill {skill.name!r} path {skill.path!r} not found in {skill.repo}")
    if not (target / "SKILL.md").is_file():
        raise LinkError(f"skill {skill.name!r}: no SKILL.md at {target}")

    link = skills_dir / skill.name
    if link.is_symlink():
        if link_points_to(link, target):
            return LinkResult(skill.name, "exists", target)
        # external symlink -> do not overwrite (safety default)
        return LinkResult(skill.name, "skipped", target)
    if link.exists():
        # non-symlink real entry (dir/file) -> do not overwrite
        return LinkResult(skill.name, "skipped", target)

    skills_dir.mkdir(parents=True, exist_ok=True)
    os.symlink(target, link)  # absolute target: survives skills_dir relocation
    return LinkResult(skill.name, "created", target)
