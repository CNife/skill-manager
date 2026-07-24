"""Shared pytest fixtures for skill-manager tests.

Provides a file:// source-repo factory (offline GitHub stand-in) and XDG
isolation so tests never touch the real ~/.config or ~/.cache.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect XDG dirs and HOME to tmp so tests never touch real user state.

    HOME isolation matters for global-scope tests: ``~/.skill-manager.json`` and
    ``~/.agents/skills/`` resolve under the temp HOME, never the real one.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def make_source_repo(tmp_path: Path):
    """Factory: create a cloneable source repo with skill dirs containing SKILL.md.

    ``skills`` maps skill path -> SKILL.md content; path ``"."`` places SKILL.md
    at the repo root (repo-root skill case). Returns the source repo Path; tests
    build the ``file://`` URL and may add commits to simulate upstream advances.
    """

    def _make(name: str, skills: dict[str, str]) -> Path:
        repo = tmp_path / "sources" / name
        repo.mkdir(parents=True)
        _git(["init"], repo)
        _git(["config", "user.email", "test@example.com"], repo)
        _git(["config", "user.name", "Test"], repo)
        for skill_path, content in skills.items():
            skill_dir = repo if skill_path == "." else repo / skill_path
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
        _git(["add", "."], repo)
        _git(["commit", "-m", "init"], repo)
        return repo

    return _make


@pytest.fixture
def git():
    """Helper to run git in a given cwd (for tests that advance upstream)."""

    def _git(args: list[str], cwd: Path) -> None:
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    return _git
