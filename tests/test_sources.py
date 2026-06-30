from pathlib import Path

import pytest

from skill_manager.config import GlobalConfig
from skill_manager.sources import SourceError, ensure_source, repo_url


def test_repo_url() -> None:
    assert repo_url("tw93/Waza") == "https://github.com/tw93/Waza.git"


def test_ensure_source_clones_missing(tmp_path: Path, make_source_repo) -> None:
    repo = make_source_repo("waza", {"skills/read": "# read\n"})
    url = f"file://{repo}"
    cache = tmp_path / "cache" / "repos"
    cfg = GlobalConfig()
    head = ensure_source("tw93/Waza", cfg, cache, url=url)
    assert (cache / "tw93" / "Waza" / ".git").exists()
    assert len(head) == 40 and all(c in "0123456789abcdef" for c in head)
    assert cfg.sources["tw93/Waza"].commit == head
    assert cfg.sources["tw93/Waza"].url == url


def test_ensure_source_idempotent(tmp_path: Path, make_source_repo) -> None:
    repo = make_source_repo("waza", {"skills/read": "# read\n"})
    url = f"file://{repo}"
    cache = tmp_path / "cache" / "repos"
    cfg = GlobalConfig()
    head1 = ensure_source("tw93/Waza", cfg, cache, url=url)
    head2 = ensure_source("tw93/Waza", cfg, cache, url=url)
    assert head1 == head2


def test_ensure_source_pulls_existing(tmp_path: Path, make_source_repo) -> None:
    repo = make_source_repo("waza", {"skills/read": "# read\n"})
    url = f"file://{repo}"
    cache = tmp_path / "cache" / "repos"
    cfg = GlobalConfig()
    ensure_source("tw93/Waza", cfg, cache, url=url)
    # second call takes the pull branch (dest exists); must not raise
    head = ensure_source("tw93/Waza", cfg, cache, url=url)
    assert cfg.sources["tw93/Waza"].commit == head


def test_ensure_source_root_path(tmp_path: Path, make_source_repo) -> None:
    repo = make_source_repo("kami", {".": "# kami\n"})
    url = f"file://{repo}"
    cache = tmp_path / "cache" / "repos"
    cfg = GlobalConfig()
    ensure_source("tw93/Kami", cfg, cache, url=url)
    assert (cache / "tw93" / "Kami" / "SKILL.md").is_file()


def test_ensure_source_updates_head_after_upstream_advance(
    tmp_path: Path, make_source_repo, git
) -> None:
    repo = make_source_repo("waza", {"skills/read": "# read\n"})
    url = f"file://{repo}"
    cache = tmp_path / "cache" / "repos"
    cfg = GlobalConfig()
    head1 = ensure_source("tw93/Waza", cfg, cache, url=url)
    # advance upstream
    (repo / "skills" / "read" / "extra.txt").write_text("x", encoding="utf-8")
    git(["add", "."], repo)
    git(["commit", "-m", "advance"], repo)
    head2 = ensure_source("tw93/Waza", cfg, cache, url=url)
    assert head2 != head1
    assert cfg.sources["tw93/Waza"].commit == head2


def test_ensure_source_git_failure(tmp_path: Path) -> None:
    cache = tmp_path / "cache" / "repos"
    cfg = GlobalConfig()
    with pytest.raises(SourceError, match="clone"):
        ensure_source("x/y", cfg, cache, url="file:///nonexistent/repo")
