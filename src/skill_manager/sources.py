"""Source repository git operations for skill-manager.

Two operations with one invariant: only ``sync`` and ``source update`` may
update already cached content.

- ``clone_source``: clone a missing repo into the cache and record its HEAD in
  the global config. Never pulls — an existing cache is left untouched.
- ``pull_source``: ``git pull --ff-only`` in an existing cache; returns
  ``(old_head, new_head)`` so callers can report real updates. Raises
  ``SourceError`` on failure (never swallowed).

All git calls use subprocess with list arguments (no shell).
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


def clone_source(
    repo: str,
    global_config: GlobalConfig,
    cache_root: Path,
    *,
    url: str | None = None,
) -> str:
    """Ensure ``repo`` is cloned into the cache; record HEAD+url in ``global_config``.

    Never pulls: an existing cached clone is left untouched (only its HEAD is
    re-read). Returns the current HEAD commit sha. ``url`` defaults to
    ``repo_url(repo)``; tests pass a ``file://`` URL to use a local repo as an
    offline GitHub stand-in.
    """
    actual_url = url if url is not None else repo_url(repo)
    dest = cache_root / repo
    if not dest.resolve().is_relative_to(cache_root.resolve()):
        raise SourceError(f"repo {repo!r} escapes the cache directory (must be 'owner/repo')")
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        _run_git(["clone", "--quiet", actual_url, str(dest)])
    head = _run_git(["rev-parse", "HEAD"], cwd=dest)
    global_config.sources[repo] = Source(repo=repo, commit=head, url=actual_url)
    return head


def pull_source(
    repo: str,
    global_config: GlobalConfig,
    cache_root: Path,
) -> tuple[str, str]:
    """Pull --ff-only in the cached clone of ``repo``; return ``(old_head, new_head)``.

    The cache is a pure mirror with no local commits, so ff always succeeds when
    upstream is reachable. Records the new HEAD in ``global_config``. Raises
    ``SourceError`` when the cache is missing or the pull fails.
    """
    dest = cache_root / repo
    if not dest.is_dir():
        raise SourceError(f"source repo {repo!r} is not cached")
    old = _run_git(["rev-parse", "HEAD"], cwd=dest)
    _run_git(["pull", "--quiet", "--ff-only"], cwd=dest)
    new = _run_git(["rev-parse", "HEAD"], cwd=dest)
    src = global_config.sources.get(repo)
    url = src.url if src is not None else repo_url(repo)
    global_config.sources[repo] = Source(repo=repo, commit=new, url=url)
    return old, new


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
