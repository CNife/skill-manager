from pathlib import Path

import pytest

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


def test_clone_source_git_failure(tmp_path: Path) -> None:
    cache = tmp_path / "cache" / "repos"
    cfg = GlobalConfig()
    with pytest.raises(SourceError, match="clone"):
        clone_source("x/y", cfg, cache, url="file:///nonexistent/repo")


def test_pull_source_failure_propagates(tmp_path: Path, make_source_repo) -> None:
    """pull failures raise SourceError instead of being swallowed."""
    import subprocess

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
