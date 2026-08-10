"""Tests for the doctor command (issue #51).

Single seam: CliRunner.invoke(app, ["doctor"]) / ["--json", "doctor"].
Each test constructs one inconsistent state and asserts the corresponding
problem code appears in the output. Healthy state asserts no problems.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from helpers import skill_md
from typer.testing import CliRunner

from skill_manager import paths
from skill_manager.cli import app

runner = CliRunner()


# ── helpers ───────────────────────────────────────────────────────────────────


def _write_config(project: Path, skills: list[dict]) -> None:
    (project / ".skill-manager.json").write_text(json.dumps({"skills": skills}), encoding="utf-8")


def _write_global_skills(skills: list[dict]) -> None:
    path = paths.global_skills_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"skills": skills}), encoding="utf-8")


def _seed_cached_source(
    tmp_path: Path, make_source_repo, skills: dict[str, str] | None = None
) -> tuple[Path, str]:
    """Clone a file:// source into the isolated XDG cache; return (project, head)."""
    from skill_manager.config import GlobalConfig, save_global_config
    from skill_manager.sources import clone_source

    skills = skills or {"skills/read": skill_md("read")}
    upstream = make_source_repo("waza", skills)
    url = f"file://{upstream}"
    cfg = GlobalConfig()
    head = clone_source("tw93/Waza", cfg, paths.repos_cache_dir(), url=url)
    save_global_config(paths.config_file(), cfg)
    project = tmp_path / "proj"
    project.mkdir(exist_ok=True)
    return project, head


def _seed_synced_project(tmp_path, make_source_repo, monkeypatch) -> Path:
    """Seed cache + declaration, chdir into the project, run sync once."""
    project, _ = _seed_cached_source(tmp_path, make_source_repo)
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0, result.output
    return project


def _parse_json(result) -> dict:
    assert result.stdout.strip(), f"empty stdout; stderr={result.stderr!r}"
    return json.loads(result.stdout)


def _codes(body: dict) -> list[str]:
    return [p["code"] for p in body["data"]["problems"]]


def _find(body: dict, code: str) -> dict:
    return next(p for p in body["data"]["problems"] if p["code"] == code)


# ── healthy state ─────────────────────────────────────────────────────────────


def test_doctor_healthy_state(tmp_path, make_source_repo, monkeypatch) -> None:
    """A fully synced project with no issues reports 'No problems found.'"""
    _seed_synced_project(tmp_path, make_source_repo, monkeypatch)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "No problems found." in result.output


def test_doctor_json_healthy(tmp_path, make_source_repo, monkeypatch) -> None:
    """Healthy state returns ok=true with an empty problems array."""
    _seed_synced_project(tmp_path, make_source_repo, monkeypatch)
    result = runner.invoke(app, ["--json", "doctor"])
    assert result.exit_code == 0
    body = _parse_json(result)
    assert body["ok"] is True
    assert body["data"]["problems"] == []


# ── config parse errors ───────────────────────────────────────────────────────


def test_doctor_declaration_parse_error_project(tmp_path, monkeypatch) -> None:
    """Corrupt project declaration JSON is reported as declaration_parse_error."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".skill-manager.json").write_text("{bad json", encoding="utf-8")
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["--json", "doctor"])
    assert result.exit_code == 0
    body = _parse_json(result)
    assert "declaration_parse_error" in _codes(body)
    prob = _find(body, "declaration_parse_error")
    assert prob["scope"] == "project"


def test_doctor_declaration_parse_error_global(tmp_path, monkeypatch) -> None:
    """Corrupt global skills declaration JSON is reported with scope=global."""
    monkeypatch.chdir(tmp_path)
    global_decl = paths.global_skills_config_path()
    global_decl.parent.mkdir(parents=True, exist_ok=True)
    global_decl.write_text("{bad json", encoding="utf-8")
    result = runner.invoke(app, ["--json", "doctor"])
    assert result.exit_code == 0
    body = _parse_json(result)
    assert "declaration_parse_error" in _codes(body)
    prob = _find(body, "declaration_parse_error")
    assert prob["scope"] == "global"


def test_doctor_global_config_parse_error(tmp_path, monkeypatch) -> None:
    """Corrupt global config JSON is reported as global_config_parse_error."""
    monkeypatch.chdir(tmp_path)
    config_file = paths.config_file()
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text("{bad json", encoding="utf-8")
    result = runner.invoke(app, ["--json", "doctor"])
    assert result.exit_code == 0
    body = _parse_json(result)
    assert "global_config_parse_error" in _codes(body)
    prob = _find(body, "global_config_parse_error")
    assert prob["scope"] == "config"


# ── source consistency ────────────────────────────────────────────────────────


def test_doctor_declared_source_not_registered(tmp_path, monkeypatch) -> None:
    """A declaration referencing an unregistered source is reported."""
    project = tmp_path / "proj"
    project.mkdir()
    _write_config(project, [{"name": "read", "repo": "foo/bar", "path": "skills/read"}])
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["--json", "doctor"])
    assert result.exit_code == 0
    body = _parse_json(result)
    assert "declared_source_not_registered" in _codes(body)
    prob = _find(body, "declared_source_not_registered")
    assert prob["scope"] == "project"
    assert prob["repo"] == "foo/bar"
    assert "sync" in prob["fix"]


def test_doctor_registered_source_cache_missing(tmp_path, make_source_repo, monkeypatch) -> None:
    """A registered source whose cache directory was deleted is reported."""
    project, _ = _seed_cached_source(tmp_path, make_source_repo)
    monkeypatch.chdir(project)
    shutil.rmtree(paths.repos_cache_dir() / "tw93/Waza")
    result = runner.invoke(app, ["--json", "doctor"])
    assert result.exit_code == 0
    body = _parse_json(result)
    assert "registered_source_cache_missing" in _codes(body)


def test_doctor_orphan_source_registered(tmp_path, make_source_repo, monkeypatch) -> None:
    """A registered+cached source with no declaration referencing it is reported."""
    project, _ = _seed_cached_source(tmp_path, make_source_repo)
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["--json", "doctor"])
    assert result.exit_code == 0
    body = _parse_json(result)
    assert "orphan_source_registered" in _codes(body)
    prob = _find(body, "orphan_source_registered")
    assert prob["repo"] == "tw93/Waza"
    assert "source remove" in prob["fix"]


def test_doctor_orphan_source_cache(tmp_path, monkeypatch) -> None:
    """A cache directory not registered in global config is reported."""
    monkeypatch.chdir(tmp_path)
    cache_repo = paths.repos_cache_dir() / "foo" / "bar"
    cache_repo.mkdir(parents=True)
    result = runner.invoke(app, ["--json", "doctor"])
    assert result.exit_code == 0
    body = _parse_json(result)
    assert "orphan_source_cache" in _codes(body)
    prob = _find(body, "orphan_source_cache")
    assert prob["repo"] == "foo/bar"


def test_doctor_head_drift(tmp_path, make_source_repo, git, monkeypatch) -> None:
    """HEAD mismatch between global config and cache is reported."""
    project, _ = _seed_cached_source(tmp_path, make_source_repo)
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    monkeypatch.chdir(project)
    cache_repo = paths.repos_cache_dir() / "tw93/Waza"
    (cache_repo / "new_file.txt").write_text("test", encoding="utf-8")
    git(["config", "user.email", "test@example.com"], cache_repo)
    git(["config", "user.name", "Test"], cache_repo)
    git(["add", "new_file.txt"], cache_repo)
    git(["commit", "-m", "drift"], cache_repo)
    result = runner.invoke(app, ["--json", "doctor"])
    assert result.exit_code == 0
    body = _parse_json(result)
    assert "head_drift" in _codes(body)
    prob = _find(body, "head_drift")
    assert "source update" in prob["fix"]


# ── link checks ───────────────────────────────────────────────────────────────


def test_doctor_unlinked(tmp_path, make_source_repo, monkeypatch) -> None:
    """A declared skill with no link is reported as unlinked."""
    project, _ = _seed_cached_source(tmp_path, make_source_repo)
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["--json", "doctor"])
    assert result.exit_code == 0
    body = _parse_json(result)
    assert "unlinked" in _codes(body)
    prob = _find(body, "unlinked")
    assert prob["scope"] == "project"
    assert prob["name"] == "read"
    assert "sync" in prob["fix"]


def test_doctor_unlinked_global_scope_fix(tmp_path, make_source_repo, monkeypatch) -> None:
    """Global-scope unlinked fix includes --global sync."""
    project, _ = _seed_cached_source(tmp_path, make_source_repo)
    _write_global_skills([{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["--json", "doctor"])
    assert result.exit_code == 0
    body = _parse_json(result)
    prob = _find(body, "unlinked")
    assert prob["scope"] == "global"
    assert "--global sync" in prob["fix"]


def test_doctor_broken_link(tmp_path, make_source_repo, monkeypatch) -> None:
    """A link whose target directory was deleted is reported as broken."""
    _seed_synced_project(tmp_path, make_source_repo, monkeypatch)
    shutil.rmtree(paths.repos_cache_dir() / "tw93/Waza" / "skills" / "read")
    result = runner.invoke(app, ["--json", "doctor"])
    assert result.exit_code == 0
    body = _parse_json(result)
    assert "broken_link" in _codes(body)


def test_doctor_external_link(tmp_path, make_source_repo, monkeypatch) -> None:
    """A link pointing to a different target than declared is reported."""
    project, _ = _seed_cached_source(tmp_path, make_source_repo)
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    monkeypatch.chdir(project)
    skills_dir = project / ".agents" / "skills"
    skills_dir.mkdir(parents=True)
    # Point to repo root instead of the declared skills/read path
    wrong_target = paths.repos_cache_dir() / "tw93/Waza"
    (skills_dir / "read").symlink_to(wrong_target)
    result = runner.invoke(app, ["--json", "doctor"])
    assert result.exit_code == 0
    body = _parse_json(result)
    assert "external_link" in _codes(body)


def test_doctor_orphan_link(tmp_path, make_source_repo, monkeypatch) -> None:
    """A symlink in the skills dir with no declaration is reported."""
    project, _ = _seed_cached_source(tmp_path, make_source_repo)
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    monkeypatch.chdir(project)
    skills_dir = project / ".agents" / "skills"
    skills_dir.mkdir(parents=True)
    target = tmp_path / "orphan_target"
    target.mkdir()
    (skills_dir / "orphan").symlink_to(target)
    result = runner.invoke(app, ["--json", "doctor"])
    assert result.exit_code == 0
    body = _parse_json(result)
    assert "orphan_link" in _codes(body)
    prob = _find(body, "orphan_link")
    assert prob["name"] == "orphan"


def test_doctor_declared_path_invalid(tmp_path, make_source_repo, monkeypatch) -> None:
    """A declared path that doesn't exist in the cache is reported."""
    project, _ = _seed_cached_source(tmp_path, make_source_repo)
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/nonexistent"}])
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["--json", "doctor"])
    assert result.exit_code == 0
    body = _parse_json(result)
    assert "declared_path_invalid" in _codes(body)


# ── cross-scope conflict ──────────────────────────────────────────────────────


def test_doctor_cross_scope_conflict(tmp_path, make_source_repo, monkeypatch) -> None:
    """Same skill name in project and global from different sources is a conflict."""
    from skill_manager.config import GlobalConfig, save_global_config
    from skill_manager.sources import clone_source

    upstream1 = make_source_repo("waza", {"skills/read": skill_md("read")})
    upstream2 = make_source_repo("other", {"skills/read": skill_md("read")})
    cfg = GlobalConfig()
    clone_source("tw93/Waza", cfg, paths.repos_cache_dir(), url=f"file://{upstream1}")
    clone_source("other/Repo", cfg, paths.repos_cache_dir(), url=f"file://{upstream2}")
    save_global_config(paths.config_file(), cfg)

    project = tmp_path / "proj"
    project.mkdir()
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    _write_global_skills([{"name": "read", "repo": "other/Repo", "path": "skills/read"}])
    monkeypatch.chdir(project)

    result = runner.invoke(app, ["--json", "doctor"])
    assert result.exit_code == 0
    body = _parse_json(result)
    assert "cross_scope_conflict" in _codes(body)
    prob = _find(body, "cross_scope_conflict")
    assert prob["scope"] == "cross-scope"
    assert prob["name"] == "read"


def test_doctor_cross_scope_same_source_no_conflict(
    tmp_path, make_source_repo, monkeypatch
) -> None:
    """Same skill name in both scopes from the same source is NOT a conflict."""
    project, _ = _seed_cached_source(tmp_path, make_source_repo)
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    _write_global_skills([{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    monkeypatch.chdir(project)

    result = runner.invoke(app, ["--json", "doctor"])
    assert result.exit_code == 0
    body = _parse_json(result)
    assert "cross_scope_conflict" not in _codes(body)


# ── cache git state ───────────────────────────────────────────────────────────


def test_doctor_cache_detached_head(tmp_path, make_source_repo, git, monkeypatch) -> None:
    """A cached repo in detached HEAD state is reported."""
    project, _ = _seed_cached_source(tmp_path, make_source_repo)
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    monkeypatch.chdir(project)
    cache_repo = paths.repos_cache_dir() / "tw93/Waza"
    git(["checkout", "--detach"], cache_repo)
    result = runner.invoke(app, ["--json", "doctor"])
    assert result.exit_code == 0
    body = _parse_json(result)
    assert "cache_detached_head" in _codes(body)


def test_doctor_cache_dirty(tmp_path, make_source_repo, monkeypatch) -> None:
    """A cached repo with uncommitted tracked-file changes is reported."""
    project, _ = _seed_cached_source(tmp_path, make_source_repo)
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    monkeypatch.chdir(project)
    skill_file = paths.repos_cache_dir() / "tw93/Waza" / "skills" / "read" / "SKILL.md"
    skill_file.write_text("modified content", encoding="utf-8")
    result = runner.invoke(app, ["--json", "doctor"])
    assert result.exit_code == 0
    body = _parse_json(result)
    assert "cache_dirty" in _codes(body)


def test_doctor_cache_dirty_ignores_untracked(tmp_path, make_source_repo, monkeypatch) -> None:
    """Untracked files in the cache do not trigger cache_dirty."""
    project, _ = _seed_cached_source(tmp_path, make_source_repo)
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    monkeypatch.chdir(project)
    cache_repo = paths.repos_cache_dir() / "tw93/Waza"
    (cache_repo / "untracked.txt").write_text("noise", encoding="utf-8")
    result = runner.invoke(app, ["--json", "doctor"])
    assert result.exit_code == 0
    body = _parse_json(result)
    assert "cache_dirty" not in _codes(body)


# ── environment ───────────────────────────────────────────────────────────────


def test_doctor_xdg_path_issue(tmp_path, monkeypatch) -> None:
    """A relative XDG_CACHE_HOME is reported as xdg_path_issue."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CACHE_HOME", "relative/path")
    result = runner.invoke(app, ["--json", "doctor"])
    assert result.exit_code == 0
    body = _parse_json(result)
    assert "xdg_path_issue" in _codes(body)


def test_doctor_cache_dir_not_writable(tmp_path, make_source_repo, monkeypatch) -> None:
    """A read-only cache directory is reported as cache_dir_not_writable."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root bypasses permission checks")
    project, _ = _seed_cached_source(tmp_path, make_source_repo)
    monkeypatch.chdir(project)
    cache_root = paths.repos_cache_dir()
    cache_root.chmod(0o500)
    try:
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "cache_dir_not_writable" in result.output
    finally:
        cache_root.chmod(0o700)


# ── --global rejection ────────────────────────────────────────────────────────


def test_doctor_rejects_global_flag(tmp_path, monkeypatch) -> None:
    """doctor --global is a usage error (exit 2)."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["--global", "doctor"])
    assert result.exit_code == 2
    assert "does not accept --global" in result.output


def test_doctor_json_rejects_global_flag(tmp_path, monkeypatch) -> None:
    """--json --global doctor produces a JSON usage_error envelope."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["--json", "--global", "doctor"])
    assert result.exit_code == 2
    body = _parse_json(result)
    assert body["ok"] is False
    assert body["error"]["code"] == "usage_error"
    assert "does not accept --global" in body["error"]["message"]


# ── JSON structure ────────────────────────────────────────────────────────────


def test_doctor_json_problem_structure(tmp_path, make_source_repo, monkeypatch) -> None:
    """Every JSON problem has code/scope/message/fix; optional fields are strings."""
    project, _ = _seed_cached_source(tmp_path, make_source_repo)
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["--json", "doctor"])
    assert result.exit_code == 0
    body = _parse_json(result)
    assert body["ok"] is True
    assert isinstance(body["data"]["problems"], list)
    assert len(body["data"]["problems"]) > 0
    for prob in body["data"]["problems"]:
        assert isinstance(prob["code"], str)
        assert isinstance(prob["scope"], str)
        assert isinstance(prob["message"], str)
        assert isinstance(prob["fix"], str)
        for opt_field in ("name", "repo", "path"):
            if opt_field in prob:
                assert isinstance(prob[opt_field], str)


def test_doctor_json_flag_after_subcommand(tmp_path, monkeypatch) -> None:
    """--json may follow the subcommand (argv normalization)."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0
    body = _parse_json(result)
    assert body["ok"] is True


# ── non-blocking ──────────────────────────────────────────────────────────────


def test_doctor_non_blocking_multiple_parse_errors(tmp_path, monkeypatch) -> None:
    """Both declaration and global config parse errors are reported together."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".skill-manager.json").write_text("{bad", encoding="utf-8")
    config_file = paths.config_file()
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text("{also bad", encoding="utf-8")
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["--json", "doctor"])
    assert result.exit_code == 0
    body = _parse_json(result)
    codes = _codes(body)
    assert "declaration_parse_error" in codes
    assert "global_config_parse_error" in codes


# ── exit code ─────────────────────────────────────────────────────────────────


def test_doctor_exit_code_zero_with_problems(tmp_path, monkeypatch) -> None:
    """exit code is always 0 even when problems are found."""
    project = tmp_path / "proj"
    project.mkdir()
    _write_config(project, [{"name": "read", "repo": "foo/bar", "path": "skills/read"}])
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0


# ── human output ──────────────────────────────────────────────────────────────


def test_doctor_human_grouped_output(tmp_path, make_source_repo, monkeypatch) -> None:
    """Human output groups problems by category with code: message and -> fix: lines."""
    project, _ = _seed_cached_source(tmp_path, make_source_repo)
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    lines = result.output.splitlines()
    # Category header
    assert any(ln == "Link:" for ln in lines)
    # Problem line: indented "code: message"
    assert any(ln.startswith("  unlinked:") for ln in lines)
    # Fix line: further indented "-> fix: ..."
    assert any(ln.startswith("    -> fix:") for ln in lines)


def test_doctor_human_no_ansi_when_captured(tmp_path, make_source_repo, monkeypatch) -> None:
    """CliRunner captures are non-TTY: no ANSI escape codes in doctor output."""
    project, _ = _seed_cached_source(tmp_path, make_source_repo)
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["doctor"])
    assert "\x1b[" not in result.output
