"""Source repository git operations for skill-manager.

ensure_source clones/pulls a repo into the cache and records its HEAD in the
global config. All git calls use subprocess with list arguments (no shell).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from skill_manager.config import GlobalConfig, Source


class SourceError(Exception):
    """Raised when a git operation on a source repo fails."""


def repo_url(repo: str) -> str:
    """Derive the HTTPS clone URL for an ``owner/repo`` identifier."""
    return f"https://github.com/{repo}.git"


def _run_git(args: list[str], cwd: Path | None = None) -> str:
    """Run git with list args (no shell). Return stdout. Raise SourceError on failure."""
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        cmd = " ".join(args)
        raise SourceError(f"git {cmd} failed (exit {result.returncode}): {result.stderr.strip()}")
    return result.stdout.strip()


def ensure_source(
    repo: str,
    global_config: GlobalConfig,
    cache_root: Path,
    *,
    url: str | None = None,
) -> str:
    """Clone repo into cache if missing, else pull --ff-only. Record HEAD+url in global_config.

    Returns the current HEAD commit sha. ``url`` defaults to ``repo_url(repo)``;
    tests pass a ``file://`` URL to use a local repo as an offline GitHub stand-in.
    """
    actual_url = url if url is not None else repo_url(repo)
    dest = cache_root / repo
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        _run_git(["clone", "--quiet", actual_url, str(dest)])
    else:
        # pull --ff-only (not bare fetch) so HEAD advances when upstream moves;
        # the cache is a pure mirror with no local commits, so ff always succeeds.
        _run_git(["pull", "--quiet", "--ff-only"], cwd=dest)
    head = _run_git(["rev-parse", "HEAD"], cwd=dest)
    global_config.sources[repo] = Source(repo=repo, commit=head, url=actual_url)
    return head


def remove_source(
    repo: str,
    global_config: GlobalConfig,
    cache_root: Path,
) -> bool:
    """Remove a source repo from global config and delete its cached clone.


    Returns ``True`` if the cache directory was actually deleted.
    """
    dest = cache_root / repo
    had_cache = dest.is_dir()
    if had_cache:
        shutil.rmtree(dest)
    del global_config.sources[repo]
    return had_cache
