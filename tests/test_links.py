from pathlib import Path

import pytest

from skill_manager.config import SkillRef
from skill_manager.links import LinkError, ensure_link


def _make_skill(cache: Path, repo: str, path: str, content: str = "# skill\n") -> Path:
    """Lay out a cached skill dir with SKILL.md; return its resolved path."""
    target = cache / repo / path
    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text(content, encoding="utf-8")
    return target.resolve()


def test_ensure_link_creates(tmp_path: Path) -> None:
    cache = tmp_path / "cache" / "repos"
    _make_skill(cache, "tw93/Waza", "skills/read")
    skills_dir = tmp_path / "proj" / ".agents" / "skills"
    res = ensure_link(SkillRef("read", "tw93/Waza", "skills/read"), cache, skills_dir)
    assert res.action == "created"
    link = skills_dir / "read"
    assert link.is_symlink()
    assert (link / "SKILL.md").read_text() == "# skill\n"


def test_ensure_link_idempotent(tmp_path: Path) -> None:
    cache = tmp_path / "cache" / "repos"
    _make_skill(cache, "tw93/Waza", "skills/read")
    skills_dir = tmp_path / "proj" / ".agents" / "skills"
    ensure_link(SkillRef("read", "tw93/Waza", "skills/read"), cache, skills_dir)
    res = ensure_link(SkillRef("read", "tw93/Waza", "skills/read"), cache, skills_dir)
    assert res.action == "exists"


def test_ensure_link_skips_external_symlink(tmp_path: Path) -> None:
    cache = tmp_path / "cache" / "repos"
    _make_skill(cache, "tw93/Waza", "skills/read")
    skills_dir = tmp_path / "proj" / ".agents" / "skills"
    skills_dir.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "SKILL.md").write_text("# other\n", encoding="utf-8")
    (skills_dir / "read").symlink_to(elsewhere)
    res = ensure_link(SkillRef("read", "tw93/Waza", "skills/read"), cache, skills_dir)
    assert res.action == "skipped"
    # unchanged: still points elsewhere
    assert (skills_dir / "read").readlink() == elsewhere


def test_ensure_link_skips_real_directory(tmp_path: Path) -> None:
    cache = tmp_path / "cache" / "repos"
    _make_skill(cache, "tw93/Waza", "skills/read")
    skills_dir = tmp_path / "proj" / ".agents" / "skills"
    (skills_dir / "read").mkdir(parents=True)
    res = ensure_link(SkillRef("read", "tw93/Waza", "skills/read"), cache, skills_dir)
    assert res.action == "skipped"
    assert not (skills_dir / "read").is_symlink()


def test_ensure_link_path_not_found(tmp_path: Path) -> None:
    cache = tmp_path / "cache" / "repos"
    cache.mkdir(parents=True)
    skills_dir = tmp_path / "proj" / ".agents" / "skills"
    with pytest.raises(LinkError, match="not found"):
        ensure_link(SkillRef("read", "tw93/Waza", "skills/missing"), cache, skills_dir)


def test_ensure_link_rejects_repo_escape(tmp_path: Path) -> None:
    cache = tmp_path / "cache" / "repos"
    repo_root = cache / "tw93" / "Waza"
    outside = (repo_root / "../../outside").resolve()
    outside.mkdir(parents=True)
    (outside / "SKILL.md").write_text("# outside\n", encoding="utf-8")
    skills_dir = tmp_path / "proj" / ".agents" / "skills"
    with pytest.raises(LinkError, match="escapes"):
        ensure_link(SkillRef("read", "tw93/Waza", "../../outside"), cache, skills_dir)


def test_ensure_link_no_skill_md(tmp_path: Path) -> None:
    cache = tmp_path / "cache" / "repos"
    (cache / "tw93/Waza" / "skills/read").mkdir(parents=True)
    skills_dir = tmp_path / "proj" / ".agents" / "skills"
    with pytest.raises(LinkError, match=r"SKILL\.md"):
        ensure_link(SkillRef("read", "tw93/Waza", "skills/read"), cache, skills_dir)


def test_ensure_link_creates_skills_dir(tmp_path: Path) -> None:
    cache = tmp_path / "cache" / "repos"
    _make_skill(cache, "tw93/Waza", "skills/read")
    skills_dir = tmp_path / "proj" / ".agents" / "skills"
    assert not skills_dir.exists()
    ensure_link(SkillRef("read", "tw93/Waza", "skills/read"), cache, skills_dir)
    assert skills_dir.is_dir()


def test_ensure_link_root_path(tmp_path: Path) -> None:
    cache = tmp_path / "cache" / "repos"
    _make_skill(cache, "tw93/Kami", ".")
    skills_dir = tmp_path / "proj" / ".agents" / "skills"
    res = ensure_link(SkillRef("kami", "tw93/Kami", "."), cache, skills_dir)
    assert res.action == "created"
    assert (skills_dir / "kami").is_symlink()
    assert (skills_dir / "kami" / "SKILL.md").is_file()
