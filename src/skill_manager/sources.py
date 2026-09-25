"""Source repository git operations for skill-manager.

Two operations with one invariant: only ``sync`` and ``source update`` may
update already cached content.

- ``clone_source``: clone a missing repo into the cache and record its HEAD in
  the global config. Never pulls — an existing cache is left untouched.
- ``pull_source``: ``git pull --ff-only`` in an existing cache; returns
  ``(old_head, new_head)`` so callers can report real updates. Raises
  ``SourceError`` on failure (never swallowed).

Cache shape: a cached Source is a *sparse slice*, not a full mirror — a blobless
partial clone (``--filter=blob:none --sparse``, history kept in full) whose
worktree materializes the materialization set: every directory in the tree that
holds a ``SKILL.md``, unfiltered (hidden and noise directories included, since
discovery can surface them under ``--all``), plus root-level files. A repo whose
root holds ``SKILL.md`` is materialized whole instead: such a skill's assets may
live in any subdirectory, and slicing would drop them silently.

Both operations apply the set — ``clone_source`` on a fresh clone (rolling the
clone back if it cannot be sliced, so no half-materialized cache survives a
failure), ``pull_source`` after every pull, which slices a pre-existing full
clone in place. The worktree is restored with git's own ``sparse-checkout
disable``. Cone mode is requested explicitly: ``set`` neither accepted a cone
flag nor defaulted to one before git 2.35, and a non-cone ``set`` would take the
directories as literal patterns and materialize almost nothing. Objects outside
the slice are fetched lazily from origin, so ``git log -p`` / ``git diff`` need
the network and fail slowly without it; ``rev-parse``, ``status`` and
``ls-tree`` stay offline-safe (which is all ``doctor`` and discovery use).

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


def _run_git(args: list[str], cwd: Path | None = None, *, strip: bool = True) -> str:
    """Run git with list args (no shell). Return stdout. Raise SourceError on failure."""
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        cmd = " ".join(args)
        raise SourceError(f"git {cmd} failed (exit {result.returncode}): {result.stderr.strip()}")
    return result.stdout.strip() if strip else result.stdout


_SKILL_MD = "SKILL.md"


def _materialization_dirs(dest: Path) -> list[str] | None:
    """Directories to materialize for ``dest``; ``None`` means the whole tree.

    Every directory holding a ``SKILL.md`` is materialized — unfiltered, since
    discovery may surface hidden and noise directories under ``--all``. Nested
    skill roots are dropped in favor of their outermost ancestor, mirroring the
    scanner's skill-root truncation. A ``SKILL.md`` at the tree root is a
    whole-repo skill whose assets may sit in any subdirectory: slicing would
    silently drop them, so the whole tree is materialized instead.
    """
    listing = _run_git(["ls-tree", "-r", "--name-only", "-z", "HEAD"], cwd=dest, strip=False)
    paths = [path for path in listing.split("\0") if path]
    if _SKILL_MD in paths:
        return None
    dirs: list[str] = []
    for parent in sorted(
        path[: -len(_SKILL_MD) - 1] for path in paths if path.endswith("/" + _SKILL_MD)
    ):
        if not any(parent.startswith(kept + "/") for kept in dirs):
            dirs.append(parent)
    return dirs


def _apply_materialization(dest: Path) -> None:
    """Bring ``dest``'s worktree in line with its materialization set.

    Cone mode is requested explicitly: ``set`` only defaults to it from git 2.35
    on, and a non-cone ``set`` would take the directories as literal patterns and
    silently materialize almost nothing.
    """
    dirs = _materialization_dirs(dest)
    if dirs is None:
        _run_git(["sparse-checkout", "disable"], cwd=dest)
    else:
        _run_git(["sparse-checkout", "set", "--cone", "--", *dirs], cwd=dest)


def clone_source(
    repo: str,
    global_config: GlobalConfig,
    cache_root: Path,
    *,
    url: str | None = None,
) -> str:
    """Ensure ``repo`` is cloned into the cache; record HEAD+url in ``global_config``.

    Never pulls: an existing cached clone is left untouched (only its HEAD is
    re-read). A fresh clone is materialized as a sparse slice (module docstring).
    Returns the current HEAD commit sha. ``url`` defaults to
    ``repo_url(repo)``; tests pass a ``file://`` URL to use a local repo as an
    offline GitHub stand-in.
    """
    actual_url = url if url is not None else repo_url(repo)
    dest = cache_root / repo
    if not dest.resolve().is_relative_to(cache_root.resolve()):
        raise SourceError(f"repo {repo!r} escapes the cache directory (must be 'owner/repo')")
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        _run_git(["clone", "--quiet", "--filter=blob:none", "--sparse", actual_url, str(dest)])
        try:
            _apply_materialization(dest)
        except SourceError:
            # A clone that cannot be sliced is not a usable cache: drop it so the
            # failure leaves nothing behind and a retry clones from scratch.
            shutil.rmtree(dest)
            raise
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
    upstream is reachable. The materialization set is re-derived afterwards, so
    upstream skills join the slice and a legacy full clone is slimmed in place.
    Records the new HEAD in ``global_config``. Raises ``SourceError`` when the
    cache is missing or the pull fails.
    """
    dest = cache_root / repo
    if not dest.is_dir():
        raise SourceError(f"source repo {repo!r} is not cached")
    old = _run_git(["rev-parse", "HEAD"], cwd=dest)
    _run_git(["pull", "--quiet", "--ff-only"], cwd=dest)
    new = _run_git(["rev-parse", "HEAD"], cwd=dest)
    _apply_materialization(dest)
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


def _git_readonly(args: list[str], cache_root: Path, repo: str) -> str | None:
    """Run a read-only git command in a cached repo; return stdout or None.

    Never raises: doctor degrades gracefully when a cache directory is not a
    valid git repo (e.g. manually created placeholder).
    """
    dest = cache_root / repo
    if not dest.is_dir():
        return None
    result = subprocess.run(["git", *args], cwd=dest, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def current_head(cache_root: Path, repo: str) -> str | None:
    """Return the current HEAD commit of a cached repo, or None if git fails."""
    return _git_readonly(["rev-parse", "HEAD"], cache_root, repo)


def detached_head(cache_root: Path, repo: str) -> bool | None:
    """True if the cached repo is in detached HEAD state.

    Returns None when the cache is missing or git fails (doctor skips the
    check in that case).
    """
    out = _git_readonly(["rev-parse", "--abbrev-ref", "HEAD"], cache_root, repo)
    return None if out is None else out == "HEAD"


def is_dirty(cache_root: Path, repo: str) -> bool | None:
    """True if the cached repo has uncommitted changes to tracked files.

    Returns None when the cache is missing or git fails. Untracked files are
    excluded (``--untracked-files=no``) per the doctor spec: they are noise
    and do not affect ``source update``.
    """
    out = _git_readonly(["status", "--porcelain", "--untracked-files=no"], cache_root, repo)
    return None if out is None else bool(out)
