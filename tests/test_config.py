import json
from pathlib import Path

import pytest

from skill_manager.config import (
    ConfigError,
    GlobalConfig,
    SkillDeclarations,
    SkillRef,
    Source,
    derived_sources,
    load_global_config,
    load_skill_declarations,
    save_global_config,
    save_skill_declarations,
)


def _write(path: Path, data: object) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def test_load_skill_declarations_valid(tmp_path: Path) -> None:
    p = tmp_path / ".skill-manager.json"
    _write(
        p,
        {
            "skills": [
                {"name": "read", "repo": "tw93/Waza", "path": "skills/read"},
                {"name": "kami", "repo": "tw93/Kami", "path": "."},
            ]
        },
    )
    cfg = load_skill_declarations(p)
    assert cfg.skills == [
        SkillRef("read", "tw93/Waza", "skills/read"),
        SkillRef("kami", "tw93/Kami", "."),
    ]


def test_load_skill_declarations_missing(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_skill_declarations(tmp_path / ".skill-manager.json")


def test_load_skill_declarations_bad_json(tmp_path: Path) -> None:
    p = tmp_path / ".skill-manager.json"
    p.write_text("{not valid", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid JSON"):
        load_skill_declarations(p)


def test_load_skill_declarations_not_object(tmp_path: Path) -> None:
    p = tmp_path / ".skill-manager.json"
    _write(p, ["a"])
    with pytest.raises(ConfigError, match="JSON object"):
        load_skill_declarations(p)


def test_load_skill_declarations_missing_skills(tmp_path: Path) -> None:
    p = tmp_path / ".skill-manager.json"
    _write(p, {"foo": 1})
    with pytest.raises(ConfigError, match="skills"):
        load_skill_declarations(p)


@pytest.mark.parametrize("missing", ["name", "repo", "path"])
def test_skill_missing_field(tmp_path: Path, missing: str) -> None:
    entry = {"name": "read", "repo": "tw93/Waza", "path": "skills/read"}
    entry.pop(missing)
    p = tmp_path / ".skill-manager.json"
    _write(p, {"skills": [entry]})
    with pytest.raises(ConfigError, match=missing):
        load_skill_declarations(p)


@pytest.mark.parametrize("bad", [".", "..", "a/b", "a/", "/a", ""])
def test_invalid_name(tmp_path: Path, bad: str) -> None:
    p = tmp_path / ".skill-manager.json"
    _write(p, {"skills": [{"name": bad, "repo": "tw93/Waza", "path": "skills/read"}]})
    with pytest.raises(ConfigError, match="name"):
        load_skill_declarations(p)


@pytest.mark.parametrize(
    "bad",
    [
        "tw93",
        "tw93/Waza/x",
        "/Waza",
        "tw93/",
        "",
        "../evil",
        "owner/..",
        "owner/.",
        "./repo",
        "owner/r\\x",
        "owner/repo with space",
    ],
)
def test_invalid_repo(tmp_path: Path, bad: str) -> None:
    p = tmp_path / ".skill-manager.json"
    _write(p, {"skills": [{"name": "read", "repo": bad, "path": "skills/read"}]})
    with pytest.raises(ConfigError, match="repo"):
        load_skill_declarations(p)


@pytest.mark.parametrize(
    "bad",
    [
        "/abs/read",
        "../outside",
        "skills/../read",
        "skills//read",
        "skills/read/",
        "skills/./read",
        "skills\\read",
    ],
)
def test_invalid_path_rejected(tmp_path: Path, bad: str) -> None:
    p = tmp_path / ".skill-manager.json"
    _write(p, {"skills": [{"name": "read", "repo": "tw93/Waza", "path": bad}]})
    with pytest.raises(ConfigError, match="repo-internal"):
        load_skill_declarations(p)


def test_duplicate_names(tmp_path: Path) -> None:
    p = tmp_path / ".skill-manager.json"
    _write(
        p,
        {
            "skills": [
                {"name": "read", "repo": "tw93/Waza", "path": "skills/read"},
                {"name": "read", "repo": "tw93/Waza", "path": "skills/other"},
            ]
        },
    )
    with pytest.raises(ConfigError, match="duplicate"):
        load_skill_declarations(p)


def test_derived_sources_unique_order() -> None:
    cfg = SkillDeclarations(
        skills=[
            SkillRef("a", "o1/r1", "p1"),
            SkillRef("b", "o2/r2", "p2"),
            SkillRef("c", "o1/r1", "p3"),
        ]
    )
    assert derived_sources(cfg) == ["o1/r1", "o2/r2"]


def test_load_global_config_missing(tmp_path: Path) -> None:
    assert load_global_config(tmp_path / "config.json").sources == {}


def test_load_global_config_valid(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    _write(
        p, {"sources": {"tw93/Waza": {"commit": "abc", "url": "https://github.com/tw93/Waza.git"}}}
    )
    cfg = load_global_config(p)
    assert cfg.sources["tw93/Waza"] == Source(
        "tw93/Waza", "abc", "https://github.com/tw93/Waza.git"
    )


def test_global_config_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "cfg" / "config.json"
    cfg = GlobalConfig(
        sources={"tw93/Waza": Source("tw93/Waza", "abc", "https://github.com/tw93/Waza.git")}
    )
    save_global_config(p, cfg)
    assert load_global_config(p) == cfg


def test_save_skill_declarations_roundtrip(tmp_path: Path) -> None:
    """Save then load a SkillDeclarations, verify round-trip equality."""
    p = tmp_path / ".skill-manager.json"
    cfg = SkillDeclarations(
        skills=[
            SkillRef("read", "tw93/Waza", "skills/read"),
            SkillRef("kami", "tw93/Kami", "."),
        ]
    )
    save_skill_declarations(p, cfg)
    loaded = load_skill_declarations(p)
    assert loaded.skills == cfg.skills


def test_save_skill_declarations_creates_parent_dir(tmp_path: Path) -> None:
    """save_skill_declarations creates parent directories automatically."""
    p = tmp_path / "a" / "b" / ".skill-manager.json"
    cfg = SkillDeclarations(skills=[SkillRef("read", "tw93/Waza", "skills/read")])
    save_skill_declarations(p, cfg)
    assert p.is_file()
    loaded = load_skill_declarations(p)
    assert loaded.skills == cfg.skills
