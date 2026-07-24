"""Interactive enable/disable via injectable picker adapter (issue #37)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from helpers import skill_md

from skill_manager.cli import NotFoundError, run_disable, run_enable, run_sync
from skill_manager.config import (
    load_global_config,
    load_skill_declarations,
    save_global_config,
)
from skill_manager.picker import PickerCancelled, SkillChoice, SourceChoice
from skill_manager.sources import ensure_source


def _write_config(project: Path, skills: list[dict]) -> None:
    (project / ".skill-manager.json").write_text(json.dumps({"skills": skills}), encoding="utf-8")


def _env(tmp_path: Path, make_source_repo, skills: dict[str, str] | None = None):
    skills = skills or {
        "skills/read": skill_md("read", "Read things"),
        "skills/write": skill_md("write", "Write things"),
        "skills/kami": skill_md("kami", "Kami skill"),
    }
    upstream = make_source_repo("waza", skills)
    project = tmp_path / "proj"
    project.mkdir()
    cache = tmp_path / "repos"
    gconfig = tmp_path / "config.json"
    skills_dir = project / ".agents" / "skills"
    cfg = load_global_config(gconfig)
    ensure_source("tw93/Waza", cfg, cache, url=f"file://{upstream}")
    save_global_config(gconfig, cfg)
    return project, cache, gconfig, skills_dir, upstream


class FakePicker:
    """Scripted picker: queues return values / cancellations per call."""

    def __init__(
        self,
        *,
        source: str | None = None,
        enable_names: list[str] | None = None,
        disable_names: list[str] | None = None,
        cancel_on: str | None = None,
        source_choices_out: list | None = None,
        enable_choices_out: list | None = None,
    ) -> None:
        self.source = source
        self.enable_names = enable_names
        self.disable_names = disable_names
        self.cancel_on = cancel_on
        self.source_choices_out = source_choices_out
        self.enable_choices_out = enable_choices_out

    def select_source(self, choices: list[SourceChoice]) -> str:
        if self.source_choices_out is not None:
            self.source_choices_out.append(list(choices))
        if self.cancel_on == "source":
            raise PickerCancelled
        assert self.source is not None
        return self.source

    def select_skills_to_enable(self, choices: list[SkillChoice]) -> list[str]:
        if self.enable_choices_out is not None:
            self.enable_choices_out.append(list(choices))
        if self.cancel_on == "enable":
            raise PickerCancelled
        return list(self.enable_names or [])

    def select_skills_to_disable(self, names: list[str]) -> list[str]:
        if self.cancel_on == "disable":
            raise PickerCancelled
        return list(self.disable_names if self.disable_names is not None else names)


def test_enable_no_cached_source_errors(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _write_config(project, [])
    with pytest.raises(NotFoundError, match="No cached repos"):
        run_enable(
            project / ".skill-manager.json",
            tmp_path / "cfg.json",
            tmp_path / "empty-cache",
            project / ".agents" / "skills",
            picker=FakePicker(source="x"),
        )


def test_enable_source_cancel_no_writes(tmp_path: Path, make_source_repo) -> None:
    project, cache, gconfig, skills_dir, upstream = _env(tmp_path, make_source_repo)
    _write_config(project, [])
    before = (project / ".skill-manager.json").read_text()
    with pytest.raises(PickerCancelled):
        run_enable(
            project / ".skill-manager.json",
            gconfig,
            cache,
            skills_dir,
            url_resolver=lambda _r: f"file://{upstream}",
            picker=FakePicker(cancel_on="source"),
        )
    assert (project / ".skill-manager.json").read_text() == before


def test_enable_zero_skill_source_errors(tmp_path: Path, make_source_repo) -> None:
    # Source with only unqualified content still appears, but selecting it fails.
    project, cache, gconfig, skills_dir, upstream = _env(
        tmp_path, make_source_repo, {"skills/plain": "# no fm\n"}
    )
    _write_config(project, [])
    seen: list = []
    with pytest.raises(NotFoundError, match="No qualified skills"):
        run_enable(
            project / ".skill-manager.json",
            gconfig,
            cache,
            skills_dir,
            url_resolver=lambda _r: f"file://{upstream}",
            picker=FakePicker(source="tw93/Waza", source_choices_out=seen),
        )
    assert seen and seen[0][0].skill_count == 0


def test_enable_locked_and_empty_submit(tmp_path: Path, make_source_repo) -> None:
    project, cache, gconfig, skills_dir, upstream = _env(tmp_path, make_source_repo)
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    before = (project / ".skill-manager.json").read_text()
    choices_out: list = []
    messages: list[str] = []
    result = run_enable(
        project / ".skill-manager.json",
        gconfig,
        cache,
        skills_dir,
        url_resolver=lambda _r: f"file://{upstream}",
        emit=messages.append,
        picker=FakePicker(
            source="tw93/Waza",
            enable_names=[],  # empty submit
            enable_choices_out=choices_out,
        ),
    )
    assert result.outcomes == []
    assert result.sync is None
    assert (project / ".skill-manager.json").read_text() == before
    assert any("Nothing to enable" in m for m in messages)
    locked = {c.name: c.locked for c in choices_out[0]}
    assert locked["read"] is True
    assert locked["write"] is False


def test_enable_selects_and_syncs_once(tmp_path: Path, make_source_repo) -> None:
    project, cache, gconfig, skills_dir, upstream = _env(tmp_path, make_source_repo)
    _write_config(project, [])
    result = run_enable(
        project / ".skill-manager.json",
        gconfig,
        cache,
        skills_dir,
        url_resolver=lambda _r: f"file://{upstream}",
        picker=FakePicker(source="tw93/Waza", enable_names=["write", "read"]),
    )
    assert [o.action for o in result.outcomes] == ["enabled", "enabled"]
    assert [o.skill["name"] for o in result.outcomes] == ["write", "read"]
    assert result.sync is not None
    proj = load_skill_declarations(project / ".skill-manager.json")
    assert {s.name for s in proj.skills} == {"read", "write"}
    assert (skills_dir / "read").is_symlink()
    assert (skills_dir / "write").is_symlink()


def test_enable_unqualified_enabled_absent_from_list(tmp_path: Path, make_source_repo) -> None:
    """Enabled-but-no-longer-qualified skills stay off the enable list."""
    project, cache, gconfig, skills_dir, upstream = _env(
        tmp_path,
        make_source_repo,
        {
            "skills/read": skill_md("read"),
            "skills/stale": "# lost qualification\n",
        },
    )
    _write_config(
        project,
        [
            {"name": "read", "repo": "tw93/Waza", "path": "skills/read"},
            {"name": "stale", "repo": "tw93/Waza", "path": "skills/stale"},
        ],
    )
    choices_out: list = []
    run_enable(
        project / ".skill-manager.json",
        gconfig,
        cache,
        skills_dir,
        url_resolver=lambda _r: f"file://{upstream}",
        picker=FakePicker(
            source="tw93/Waza",
            enable_names=[],
            enable_choices_out=choices_out,
        ),
    )
    names = {c.name for c in choices_out[0]}
    assert "read" in names
    assert "stale" not in names


def test_enable_same_name_dedupe_first_path(tmp_path: Path, make_source_repo) -> None:
    project, cache, gconfig, skills_dir, upstream = _env(
        tmp_path,
        make_source_repo,
        {
            "a/dup": skill_md("dup", "first"),
            "b/dup": skill_md("dup", "second"),
        },
    )
    _write_config(project, [])
    result = run_enable(
        project / ".skill-manager.json",
        gconfig,
        cache,
        skills_dir,
        url_resolver=lambda _r: f"file://{upstream}",
        picker=FakePicker(source="tw93/Waza", enable_names=["dup", "dup"]),
    )
    assert len(result.outcomes) == 1
    assert result.outcomes[0].skill["path"] == "a/dup"


def test_disable_no_enabled_exit_ok(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _write_config(project, [])
    messages: list[str] = []
    result = run_disable(
        project / ".skill-manager.json",
        tmp_path / "cfg.json",
        tmp_path / "cache",
        project / ".agents" / "skills",
        emit=messages.append,
        picker=FakePicker(),  # must not be called
    )
    assert result.outcomes == []
    assert any("No enabled skills to disable" in m for m in messages)


def test_disable_cancel_no_writes(tmp_path: Path, make_source_repo) -> None:
    project, cache, gconfig, skills_dir, upstream = _env(tmp_path, make_source_repo)
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    run_sync(
        project / ".skill-manager.json",
        gconfig,
        cache,
        skills_dir,
        url_resolver=lambda _r: f"file://{upstream}",
    )
    before = (project / ".skill-manager.json").read_text()
    with pytest.raises(PickerCancelled):
        run_disable(
            project / ".skill-manager.json",
            gconfig,
            cache,
            skills_dir,
            picker=FakePicker(cancel_on="disable"),
        )
    assert (project / ".skill-manager.json").read_text() == before
    assert (skills_dir / "read").is_symlink()


def test_disable_empty_submit_no_writes(tmp_path: Path, make_source_repo) -> None:
    project, cache, gconfig, skills_dir, _upstream = _env(tmp_path, make_source_repo)
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    before = (project / ".skill-manager.json").read_text()
    messages: list[str] = []
    result = run_disable(
        project / ".skill-manager.json",
        gconfig,
        cache,
        skills_dir,
        emit=messages.append,
        picker=FakePicker(disable_names=[]),
    )
    assert result.outcomes == []
    assert (project / ".skill-manager.json").read_text() == before
    assert any("Nothing to disable" in m for m in messages)


def test_disable_selects_and_cleans(tmp_path: Path, make_source_repo) -> None:
    project, cache, gconfig, skills_dir, upstream = _env(tmp_path, make_source_repo)
    _write_config(
        project,
        [
            {"name": "read", "repo": "tw93/Waza", "path": "skills/read"},
            {"name": "write", "repo": "tw93/Waza", "path": "skills/write"},
        ],
    )
    run_sync(
        project / ".skill-manager.json",
        gconfig,
        cache,
        skills_dir,
        url_resolver=lambda _r: f"file://{upstream}",
    )
    result = run_disable(
        project / ".skill-manager.json",
        gconfig,
        cache,
        skills_dir,
        picker=FakePicker(disable_names=["write"]),
    )
    assert [o.action for o in result.outcomes] == ["disabled"]
    assert result.outcomes[0].link_removed is True
    proj = load_skill_declarations(project / ".skill-manager.json")
    assert [s.name for s in proj.skills] == ["read"]
    assert not (skills_dir / "write").exists()
    assert (skills_dir / "read").is_symlink()


def test_disable_includes_stale_unqualified_declaration(tmp_path: Path, make_source_repo) -> None:
    """Disable lists declarations even when cache skill is no longer qualified."""
    project, cache, gconfig, skills_dir, _upstream = _env(
        tmp_path, make_source_repo, {"skills/stale": "# no fm\n"}
    )
    _write_config(project, [{"name": "stale", "repo": "tw93/Waza", "path": "skills/stale"}])
    seen: list[str] = []

    class Capture(FakePicker):
        def select_skills_to_disable(self, names):
            seen.extend(names)
            return []

    run_disable(
        project / ".skill-manager.json",
        gconfig,
        cache,
        skills_dir,
        picker=Capture(disable_names=[]),
    )
    assert seen == ["stale"]
