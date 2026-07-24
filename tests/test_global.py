"""Global-scope skill management tests (--global flag, ~/.skill-manager.json, ~/.agents/skills/).

The user's home is treated as a project: global declarations live at
``~/.skill-manager.json`` and links land in ``~/.agents/skills/``. The source
registry (``~/.config/skill-manager/config.json``) and cache are shared across
project and global scopes. HOME is redirected by the autouse ``isolated_xdg``
fixture, so these tests never touch the real ``~``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from skill_manager import paths
from skill_manager.cli import app, run_sync
from skill_manager.config import GlobalConfig, load_skill_declarations, save_global_config
from skill_manager.sources import ensure_source

runner = CliRunner()


def _write_decls(path: Path, skills: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"skills": skills}), encoding="utf-8")


def _parse_json(result) -> dict:
    assert result.stdout.strip(), f"empty stdout; stderr={result.stderr!r} output={result.output!r}"
    return json.loads(result.stdout)


def _seed_source(tmp_path: Path, make_source_repo, skills: dict[str, str] | None = None) -> str:
    """Clone a file:// source into the isolated cache; return full HEAD commit."""
    skills = skills or {"skills/read": "# read\n"}
    upstream = make_source_repo("waza", skills)
    url = f"file://{upstream}"
    cfg = GlobalConfig()
    head = ensure_source("tw93/Waza", cfg, paths.repos_cache_dir(), url=url)
    save_global_config(paths.config_file(), cfg)
    return head


# ── sync ──────────────────────────────────────────────────────────────────────


def test_global_run_sync_creates_link(tmp_path: Path, make_source_repo) -> None:
    """run_sync with global paths links into ~/.agents/skills/ and records the source."""
    _seed_source(tmp_path, make_source_repo)
    decl = paths.global_skills_config_path()
    _write_decls(decl, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    skills_dir = paths.global_skills_dir()

    result = run_sync(decl, paths.config_file(), paths.repos_cache_dir(), skills_dir)

    link = skills_dir / "read"
    assert link.is_symlink()
    assert (link / "SKILL.md").is_file()
    assert any(ln.name == "read" and ln.action == "created" for ln in result.links)
    # source registry updated (shared across scopes)
    gcfg = json.loads(paths.config_file().read_text())
    assert "tw93/Waza" in gcfg["sources"]


def test_global_cli_sync(tmp_path: Path, make_source_repo) -> None:
    _seed_source(tmp_path, make_source_repo)
    _write_decls(
        paths.global_skills_config_path(),
        [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}],
    )
    result = runner.invoke(app, ["--global", "sync"])
    assert result.exit_code == 0, result.output
    link = paths.global_skills_dir() / "read"
    assert link.is_symlink()
    assert (link / "SKILL.md").is_file()


def test_global_sync_skips_foreign_entry(tmp_path: Path, make_source_repo) -> None:
    """A pre-existing foreign entry in ~/.agents/skills/ is never clobbered."""
    _seed_source(tmp_path, make_source_repo)
    gdir = paths.global_skills_dir()
    gdir.mkdir(parents=True)
    (gdir / "read").mkdir()  # foreign real directory
    (gdir / "read" / "SKILL.md").write_text("# foreign\n", encoding="utf-8")
    _write_decls(
        paths.global_skills_config_path(),
        [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}],
    )
    result = runner.invoke(app, ["--json", "--global", "sync"])
    assert result.exit_code == 0
    links = _parse_json(result)["data"]["links"]
    assert links[0]["name"] == "read"
    assert links[0]["action"] == "skipped"
    # foreign content untouched, still a real dir (not a symlink)
    assert (gdir / "read" / "SKILL.md").read_text() == "# foreign\n"
    assert not (gdir / "read").is_symlink()


# ── list ──────────────────────────────────────────────────────────────────────


def test_global_list_json(tmp_path: Path, make_source_repo) -> None:
    _seed_source(tmp_path, make_source_repo)
    _write_decls(
        paths.global_skills_config_path(),
        [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}],
    )
    runner.invoke(app, ["--global", "sync"])
    result = runner.invoke(app, ["--json", "--global", "list"])
    assert result.exit_code == 0
    body = _parse_json(result)
    assert body["ok"] is True
    skills = body["data"]["skills"]
    assert [s["name"] for s in skills] == ["read"]
    assert skills[0]["link"] == "linked"


def test_json_global_option_ordering(tmp_path: Path, make_source_repo) -> None:
    """--json and --global compose in either order (both are root options)."""
    _seed_source(tmp_path, make_source_repo)
    _write_decls(paths.global_skills_config_path(), [])
    r1 = runner.invoke(app, ["--json", "--global", "list"])
    r2 = runner.invoke(app, ["--global", "--json", "list"])
    assert r1.exit_code == 0 and r2.exit_code == 0
    assert _parse_json(r1)["ok"] is True
    assert _parse_json(r2)["ok"] is True


# ── enable / disable ──────────────────────────────────────────────────────────


def test_global_enable_noninteractive(tmp_path: Path, make_source_repo) -> None:
    _seed_source(tmp_path, make_source_repo)
    result = runner.invoke(app, ["--json", "--global", "enable", "tw93/Waza", "read"])
    assert result.exit_code == 0, result.output
    body = _parse_json(result)
    assert body["data"]["results"][0]["action"] == "enabled"
    decl = load_skill_declarations(paths.global_skills_config_path())
    assert [s.name for s in decl.skills] == ["read"]
    assert (paths.global_skills_dir() / "read" / "SKILL.md").is_file()


def test_global_enable_interactive(tmp_path: Path, make_source_repo) -> None:
    _seed_source(tmp_path, make_source_repo)
    result = runner.invoke(app, ["--global", "enable"], input="1\n1\n")
    assert result.exit_code == 0, result.output
    decl = load_skill_declarations(paths.global_skills_config_path())
    assert [s.name for s in decl.skills] == ["read"]
    assert (paths.global_skills_dir() / "read" / "SKILL.md").is_file()


def test_global_enable_idempotent(tmp_path: Path, make_source_repo) -> None:
    _seed_source(tmp_path, make_source_repo)
    runner.invoke(app, ["--json", "--global", "enable", "tw93/Waza", "read"])
    result = runner.invoke(app, ["--json", "--global", "enable", "tw93/Waza", "read"])
    assert result.exit_code == 0
    assert _parse_json(result)["data"]["results"][0]["action"] == "already_enabled"
    decl = load_skill_declarations(paths.global_skills_config_path())
    assert len(decl.skills) == 1


def test_global_disable_noninteractive(tmp_path: Path, make_source_repo) -> None:
    _seed_source(tmp_path, make_source_repo)
    runner.invoke(app, ["--json", "--global", "enable", "tw93/Waza", "read"])
    result = runner.invoke(app, ["--json", "--global", "disable", "read"])
    assert result.exit_code == 0, result.output
    body = _parse_json(result)
    assert body["data"]["results"][0]["action"] == "disabled"
    assert body["data"]["results"][0]["link_removed"] is True
    assert load_skill_declarations(paths.global_skills_config_path()).skills == []
    assert not (paths.global_skills_dir() / "read").exists()


# ── cross-scope independence ───────────────────────────────────────────────────


def test_cross_scope_independence(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same-named skill enabled in project and global lives in separate dirs."""
    _seed_source(tmp_path, make_source_repo)
    project = tmp_path / "proj"
    project.mkdir()
    _write_decls(
        project / ".skill-manager.json",
        [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}],
    )
    _write_decls(
        paths.global_skills_config_path(),
        [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}],
    )
    monkeypatch.chdir(project)
    runner.invoke(app, ["sync"])
    runner.invoke(app, ["--global", "sync"])

    proj_link = project / ".agents" / "skills" / "read"
    glob_link = paths.global_skills_dir() / "read"
    assert (proj_link / "SKILL.md").is_file()
    assert (glob_link / "SKILL.md").is_file()

    proj_list = _parse_json(runner.invoke(app, ["--json", "list"]))
    glob_list = _parse_json(runner.invoke(app, ["--json", "--global", "list"]))
    assert [s["name"] for s in proj_list["data"]["skills"]] == ["read"]
    assert [s["name"] for s in glob_list["data"]["skills"]] == ["read"]

    # disabling global must not touch the project link
    runner.invoke(app, ["--json", "--global", "disable", "read"])
    assert proj_link.is_symlink()
    assert not glob_link.exists()


# ── home-as-project edge case ──────────────────────────────────────────────────


def test_home_cwd_targets_global_declarations(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Running from ~ without --global reads ~/.skill-manager.json (home is a project)."""
    _seed_source(tmp_path, make_source_repo)
    _write_decls(
        paths.global_skills_config_path(),
        [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}],
    )
    monkeypatch.chdir(paths.global_skills_config_path().parent)  # cwd = HOME
    result = runner.invoke(app, ["sync"])  # no --global
    assert result.exit_code == 0, result.output
    assert (paths.global_skills_dir() / "read" / "SKILL.md").is_file()


# ── source remove: dual-scope warning ─────────────────────────────────────────


def test_source_remove_warns_both_scopes(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_source(tmp_path, make_source_repo)
    project = tmp_path / "proj"
    project.mkdir()
    _write_decls(
        project / ".skill-manager.json",
        [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}],
    )
    _write_decls(
        paths.global_skills_config_path(),
        [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}],
    )
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["source", "remove", "tw93/Waza"])
    assert result.exit_code == 0, result.output
    assert "still referenced" in result.output
    assert "project" in result.output
    assert "global" in result.output
    assert not (paths.repos_cache_dir() / "tw93" / "Waza").exists()


def test_source_remove_json_no_warning(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_source(tmp_path, make_source_repo)
    project = tmp_path / "proj"
    project.mkdir()
    _write_decls(
        project / ".skill-manager.json",
        [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}],
    )
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["--json", "source", "remove", "tw93/Waza"])
    assert result.exit_code == 0
    assert "warning" not in result.output.lower()
    assert _parse_json(result)["data"]["action"] == "removed"


# ── --global is a no-op on source subcommands ──────────────────────────────────


def test_global_flag_ignored_on_source_subcommand(tmp_path: Path, make_source_repo) -> None:
    _seed_source(tmp_path, make_source_repo)
    result = runner.invoke(app, ["--global", "source", "list"])
    assert result.exit_code == 0, result.output
    assert "tw93/Waza" in result.output


# ── source remove from ~: no double-count ─────────────────────────────────────


def test_source_remove_from_home_no_double_count(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """From ~ the project and global declaration files coincide; warn once as global."""
    _seed_source(tmp_path, make_source_repo)
    _write_decls(
        paths.global_skills_config_path(),
        [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}],
    )
    monkeypatch.chdir(paths.global_skills_config_path().parent)  # cwd = HOME
    result = runner.invoke(app, ["source", "remove", "tw93/Waza"])
    assert result.exit_code == 0, result.output
    assert "still referenced" in result.output
    assert "global" in result.output
    # the home/global declaration is one file, not double-counted as project + global
    assert "project, global" not in result.output
