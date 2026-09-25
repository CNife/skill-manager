"""Shared pytest fixtures for skill-manager tests.

Provides a file:// source-repo factory (offline GitHub stand-in) and XDG
isolation so tests never touch the real ~/.config or ~/.cache.
"""

import fcntl
import os
import pty
import struct
import subprocess
import sys
import tempfile
import termios
from pathlib import Path
from typing import NamedTuple

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
    at the repo root (repo-root skill case). ``extra_files`` maps relative path
    -> content for non-skill files (assets, root files, noise trees) that the
    materialization set must or must not bring along. Returns the source repo
    Path; tests build the ``file://`` URL and may add commits to simulate
    upstream advances.
    """

    def _make(
        name: str,
        skills: dict[str, str],
        *,
        extra_files: dict[str, str] | None = None,
    ) -> Path:
        repo = tmp_path / "sources" / name
        repo.mkdir(parents=True)
        _git(["init"], repo)
        _git(["config", "user.email", "test@example.com"], repo)
        _git(["config", "user.name", "Test"], repo)
        for skill_path, content in skills.items():
            skill_dir = repo if skill_path == "." else repo / skill_path
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
        for file_path, content in (extra_files or {}).items():
            target = repo / file_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
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


class PtyRun(NamedTuple):
    """Output of one CLI run in a pty: the terminal session plus exit code.

    ``out`` is stdout as a terminal saw it (ANSI and CRLF intact), ``err`` is
    stderr. human/machine artifacts are asserted per stream, because the tracks
    themselves split on stdout.
    """

    out: str
    err: str
    exit_code: int

    @property
    def text(self) -> str:
        """stdout with CRLF/CR folded to LF (a pty writes ONLCR)."""
        return self.out.replace("\r\n", "\n").replace("\r", "\n")

    @property
    def lines(self) -> list[str]:
        return self.text.splitlines()

    @property
    def err_lines(self) -> list[str]:
        return self.err.replace("\r\n", "\n").splitlines()


@pytest.fixture
def run_in_pty():
    """Run ``python -m skill_manager <args>`` with stdout on a real pty.

    The second driver at the CLI seam: CliRunner proves the JSON track (its
    stdout is a pipe), a pty proves the Human track. stderr goes to a temp file
    (a pipe could fill up and deadlock the child), so a run with stdout on the
    pty also proves the track is decided by stdout alone.
    ``width`` fixes the terminal size via ``TIOCSWINSZ`` so layout assertions
    are deterministic.
    """

    def _run(
        args: list[str],
        *,
        width: int = 100,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> PtyRun:
        master, slave = pty.openpty()
        try:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, width, 0, 0))
            child_env = dict(os.environ)
            # A developer's NO_COLOR would hide the very ANSI we assert on.
            child_env.pop("NO_COLOR", None)
            child_env["TERM"] = "xterm-256color"
            child_env.update(env or {})
            with tempfile.TemporaryFile() as err_file:
                proc = subprocess.Popen(
                    [sys.executable, "-m", "skill_manager", *args],
                    stdin=slave,
                    stdout=slave,
                    stderr=err_file,
                    cwd=str(cwd) if cwd is not None else None,
                    env=child_env,
                    close_fds=True,
                )
                os.close(slave)
                chunks: list[bytes] = []
                while True:
                    try:
                        data = os.read(master, 65536)
                    except OSError:  # EIO: the slave side is gone
                        break
                    if not data:
                        break
                    chunks.append(data)
                exit_code = proc.wait()
                err_file.seek(0)
                stderr = err_file.read().decode("utf-8", errors="replace")
        finally:
            os.close(master)
        return PtyRun(b"".join(chunks).decode("utf-8", errors="replace"), stderr, exit_code)

    return _run
