import os
import shutil
import subprocess
from pathlib import Path

import pytest
from helpers import skill_md

from skill_manager.cli import run_available_skills
from skill_manager.config import GlobalConfig
from skill_manager.sources import SourceError, clone_source, pull_source, repo_url


def test_repo_url() -> None:
    assert repo_url("tw93/Waza") == "https://github.com/tw93/Waza.git"


def test_clone_source_clones_missing(tmp_path: Path, make_source_repo) -> None:
    repo = make_source_repo("waza", {"skills/read": "# read\n"})
    url = f"file://{repo}"
    cache = tmp_path / "cache" / "repos"
    cfg = GlobalConfig()
    head = clone_source("tw93/Waza", cfg, cache, url=url)
    assert (cache / "tw93" / "Waza" / ".git").exists()
    assert len(head) == 40 and all(c in "0123456789abcdef" for c in head)
    assert cfg.sources["tw93/Waza"].commit == head
    assert cfg.sources["tw93/Waza"].url == url


def test_clone_source_idempotent(tmp_path: Path, make_source_repo) -> None:
    repo = make_source_repo("waza", {"skills/read": "# read\n"})
    url = f"file://{repo}"
    cache = tmp_path / "cache" / "repos"
    cfg = GlobalConfig()
    head1 = clone_source("tw93/Waza", cfg, cache, url=url)
    head2 = clone_source("tw93/Waza", cfg, cache, url=url)
    assert head1 == head2


def test_clone_source_never_pulls(tmp_path: Path, make_source_repo, git) -> None:
    """clone_source on an existing cache must not pull: upstream moves are ignored."""
    repo = make_source_repo("waza", {"skills/read": "# read\n"})
    url = f"file://{repo}"
    cache = tmp_path / "cache" / "repos"
    cfg = GlobalConfig()
    head1 = clone_source("tw93/Waza", cfg, cache, url=url)
    (repo / "skills" / "read" / "extra.txt").write_text("x", encoding="utf-8")
    git(["add", "."], repo)
    git(["commit", "-m", "advance"], repo)
    head2 = clone_source("tw93/Waza", cfg, cache, url=url)
    assert head2 == head1


def test_clone_source_root_path(tmp_path: Path, make_source_repo) -> None:
    repo = make_source_repo("kami", {".": "# kami\n"})
    url = f"file://{repo}"
    cache = tmp_path / "cache" / "repos"
    cfg = GlobalConfig()
    clone_source("tw93/Kami", cfg, cache, url=url)
    assert (cache / "tw93" / "Kami" / "SKILL.md").is_file()


def test_pull_source_returns_old_and_new(tmp_path: Path, make_source_repo, git) -> None:
    repo = make_source_repo("waza", {"skills/read": "# read\n"})
    url = f"file://{repo}"
    cache = tmp_path / "cache" / "repos"
    cfg = GlobalConfig()
    head1 = clone_source("tw93/Waza", cfg, cache, url=url)
    (repo / "skills" / "read" / "extra.txt").write_text("x", encoding="utf-8")
    git(["add", "."], repo)
    git(["commit", "-m", "advance"], repo)
    old, new = pull_source("tw93/Waza", cfg, cache)
    assert old == head1
    assert new != head1
    assert cfg.sources["tw93/Waza"].commit == new


def test_pull_source_noop_returns_equal_heads(tmp_path: Path, make_source_repo) -> None:
    repo = make_source_repo("waza", {"skills/read": "# read\n"})
    url = f"file://{repo}"
    cache = tmp_path / "cache" / "repos"
    cfg = GlobalConfig()
    head = clone_source("tw93/Waza", cfg, cache, url=url)
    old, new = pull_source("tw93/Waza", cfg, cache)
    assert old == new == head


def test_pull_source_missing_cache_raises(tmp_path: Path) -> None:
    cache = tmp_path / "cache" / "repos"
    cfg = GlobalConfig()
    with pytest.raises(SourceError, match="not cached"):
        pull_source("x/y", cfg, cache)


def test_clone_source_rejects_path_escape(tmp_path: Path) -> None:
    """F3: a repo identifier escaping the cache root is refused before any git call."""
    cache = tmp_path / "cache" / "repos"
    cfg = GlobalConfig()
    with pytest.raises(SourceError, match="escapes"):
        clone_source("../../evil", cfg, cache, url="file:///nonexistent")
    assert not (tmp_path / "evil").exists()
    assert cfg.sources == {}


def test_clone_source_git_failure(tmp_path: Path) -> None:
    cache = tmp_path / "cache" / "repos"
    cfg = GlobalConfig()
    with pytest.raises(SourceError, match="clone"):
        clone_source("x/y", cfg, cache, url="file:///nonexistent/repo")


def test_pull_source_failure_propagates(tmp_path: Path, make_source_repo) -> None:
    """pull failures raise SourceError instead of being swallowed."""
    repo = make_source_repo("waza", {"skills/read": "# read\n"})
    url = f"file://{repo}"
    cache = tmp_path / "cache" / "repos"
    cfg = GlobalConfig()
    clone_source("tw93/Waza", cfg, cache, url=url)
    subprocess.run(
        ["git", "remote", "set-url", "origin", "file:///nonexistent/offline"],
        cwd=cache / "tw93" / "Waza",
        check=True,
        capture_output=True,
    )
    with pytest.raises(SourceError, match="pull"):
        pull_source("tw93/Waza", cfg, cache)


# ── sparse materialization ────────────────────────────────────────────────────


def _worktree_files(cache: Path, repo: str) -> set[str]:
    """Relative paths of every file materialized in the cached worktree of ``repo``."""
    root = cache / repo
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


def _skill_keys(result) -> set[tuple[str, str]]:
    """(name, path) pairs of an ``AvailableSkillsResult``."""
    return {(skill["name"], skill["path"]) for skill in result.skills}


def test_clone_source_materializes_only_skill_dirs(tmp_path: Path, make_source_repo) -> None:
    """A fresh cache is a slice: skill dirs (assets included) plus root files only."""
    repo = make_source_repo(
        "waza",
        {"skills/read": "# read\n"},
        extra_files={
            "skills/read/assets/notes.txt": "notes\n",
            "scripts/build.sh": "#!/bin/sh\n",
            "src/main.py": "print(1)\n",
            "README.md": "# waza\n",
        },
    )
    cache = tmp_path / "cache" / "repos"
    clone_source("tw93/Waza", GlobalConfig(), cache, url=f"file://{repo}")

    files = _worktree_files(cache, "tw93/Waza")
    assert "skills/read/SKILL.md" in files
    assert "skills/read/assets/notes.txt" in files  # assets land recursively
    assert "README.md" in files  # root files land
    assert "scripts/build.sh" not in files  # non-skill dirs stay uncloned
    assert "src/main.py" not in files


def test_cloned_cache_discovers_every_skill(tmp_path: Path, make_source_repo) -> None:
    """Discovery is unchanged: hidden and noise skills still surface from the slice."""
    repo = make_source_repo(
        "waza",
        {
            "skills/active": skill_md("active"),
            ".archive/old": skill_md("old"),
            "node_modules/pkg/fake": skill_md("fake"),
        },
    )
    cache = tmp_path / "cache" / "repos"
    clone_source("tw93/Waza", GlobalConfig(), cache, url=f"file://{repo}")

    assert _skill_keys(run_available_skills(cache, repo="tw93/Waza")) == {
        ("active", "skills/active")
    }
    assert _skill_keys(run_available_skills(cache, repo="tw93/Waza", include_all=True)) == {
        ("active", "skills/active"),
        ("old", ".archive/old"),
        ("fake", "node_modules/pkg/fake"),
    }


def test_pull_source_keeps_slice_and_adds_new_skill_dir(
    tmp_path: Path, make_source_repo, git
) -> None:
    """A pull re-derives the set: the slice survives and new upstream skills join it."""
    repo = make_source_repo(
        "waza",
        {"skills/read": "# read\n"},
        extra_files={"scripts/build.sh": "#!/bin/sh\n"},
    )
    cache = tmp_path / "cache" / "repos"
    cfg = GlobalConfig()
    clone_source("tw93/Waza", cfg, cache, url=f"file://{repo}")

    (repo / "plugins" / "extra" / "deep").mkdir(parents=True)
    (repo / "plugins" / "extra" / "SKILL.md").write_text("# extra\n", encoding="utf-8")
    (repo / "plugins" / "extra" / "deep" / "asset.txt").write_text("asset\n", encoding="utf-8")
    git(["add", "."], repo)
    git(["commit", "-m", "advance"], repo)

    pull_source("tw93/Waza", cfg, cache)
    files = _worktree_files(cache, "tw93/Waza")
    assert "plugins/extra/SKILL.md" in files  # new skill dir joins the set
    assert "plugins/extra/deep/asset.txt" in files  # with its assets
    assert "skills/read/SKILL.md" in files  # existing slice survives
    assert "scripts/build.sh" not in files  # pull does not restore the full tree


def test_pull_source_sparsifies_existing_full_clone(tmp_path: Path, make_source_repo) -> None:
    """A legacy full clone is sliced in place by the next pull — no migration step."""
    repo = make_source_repo(
        "waza",
        {"skills/read": "# read\n"},
        extra_files={"scripts/build.sh": "#!/bin/sh\n", "README.md": "# waza\n"},
    )
    cache = tmp_path / "cache" / "repos"
    dest = cache / "tw93" / "Waza"
    dest.parent.mkdir(parents=True)
    subprocess.run(
        ["git", "clone", "--quiet", f"file://{repo}", str(dest)], check=True, capture_output=True
    )
    assert (dest / "scripts" / "build.sh").is_file()  # legacy cache starts as a full tree

    pull_source("tw93/Waza", GlobalConfig(), cache)

    files = _worktree_files(cache, "tw93/Waza")
    assert "skills/read/SKILL.md" in files
    assert "README.md" in files
    assert "scripts/build.sh" not in files


def test_clone_source_root_skill_materializes_whole_tree(
    tmp_path: Path, make_source_repo, git
) -> None:
    """A whole-repo skill keeps every asset: its tree is never sliced."""
    repo = make_source_repo(
        "kami",
        {".": "# kami\n"},
        extra_files={"scripts/build.sh": "#!/bin/sh\n", "docs/notes.md": "notes\n"},
    )
    cache = tmp_path / "cache" / "repos"
    cfg = GlobalConfig()
    clone_source("tw93/Kami", cfg, cache, url=f"file://{repo}")

    files = _worktree_files(cache, "tw93/Kami")
    assert "SKILL.md" in files
    assert "scripts/build.sh" in files  # assets may live anywhere in a repo-root skill
    assert "docs/notes.md" in files

    (repo / "tools").mkdir()
    (repo / "tools" / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    git(["add", "."], repo)
    git(["commit", "-m", "advance"], repo)
    pull_source("tw93/Kami", cfg, cache)
    assert (cache / "tw93" / "Kami" / "tools" / "run.sh").is_file()


def test_clone_source_materialization_failure_leaves_no_cache(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clone whose slice cannot be materialized is removed, so a retry starts clean."""
    real_git = shutil.which("git")
    assert real_git is not None
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    shim = shim_dir / "git"
    shim.write_text(
        "#!/bin/sh\n"
        'if [ "$1 $2" = "sparse-checkout set" ]; then\n'
        '  echo "error: unknown option cone" >&2\n'
        "  exit 129\n"
        "fi\n"
        f'exec "{real_git}" "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    real_path = os.environ["PATH"]
    monkeypatch.setenv("PATH", f"{shim_dir}:{real_path}")

    repo = make_source_repo("waza", {"skills/read": "# read\n"})
    cache = tmp_path / "cache" / "repos"
    cfg = GlobalConfig()
    with pytest.raises(SourceError, match="sparse-checkout"):
        clone_source("tw93/Waza", cfg, cache, url=f"file://{repo}")
    assert not (cache / "tw93" / "Waza").exists()  # no half-materialized cache left behind
    assert cfg.sources == {}

    monkeypatch.setenv("PATH", real_path)
    clone_source("tw93/Waza", cfg, cache, url=f"file://{repo}")  # retry succeeds
    assert (cache / "tw93" / "Waza" / "skills" / "read" / "SKILL.md").is_file()
