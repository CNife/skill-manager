"""Batch enable/disable tests (ADR 0011): variadic non-interactive, atomic
fail-fast for enable, lenient disable, interactive multi-select, and the
uniform ``results`` JSON wrapper.

Seams under test (all pre-existing public boundaries):
- ``run_enable`` / ``run_disable`` core runners (real filesystem)
- typer CLI via ``CliRunner`` (arg parsing, exit codes, text, interactive input)
- ``--json`` output shape
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from helpers import skill_md
from typer.testing import CliRunner

from skill_manager.cli import app, run_sync
from skill_manager.config import (
    GlobalConfig,
    load_global_config,
    load_skill_declarations,
    save_global_config,
)

runner = CliRunner()

SKILLS = {
    "skills/read": skill_md("read"),
    "skills/write": skill_md("write"),
    "skills/kami": skill_md("kami"),
}


def _write_config(project: Path, skills: list[dict]) -> None:
    (project / ".skill-manager.json").write_text(json.dumps({"skills": skills}), encoding="utf-8")


def _parse_json(result) -> dict:
    assert result.stdout.strip(), f"empty stdout; output={result.output!r}"
    return json.loads(result.stdout)


# ── run_* seam helpers (unit, url_resolver injected) ──────────────────────────


def _enable_env(tmp_path: Path, make_source_repo, skills: dict[str, str] | None = None):
    """Seed cache + global config; return (project, cache, gconfig, skills_dir, upstream)."""
    from skill_manager.sources import clone_source

    skills = skills or SKILLS
    upstream = make_source_repo("waza", skills)
    project = tmp_path / "proj"
    project.mkdir()
    cache = tmp_path / "repos"
    gconfig = tmp_path / "config.json"
    skills_dir = project / ".agents" / "skills"
    cfg = load_global_config(gconfig)
    clone_source("tw93/Waza", cfg, cache, url=f"file://{upstream}")
    save_global_config(gconfig, cfg)
    return project, cache, gconfig, skills_dir, upstream


# ── run_enable batch ──────────────────────────────────────────────────────────


def test_run_enable_batch_multiple(tmp_path: Path, make_source_repo) -> None:
    project, cache, gconfig, skills_dir, upstream = _enable_env(tmp_path, make_source_repo)
    _write_config(project, [])
    from skill_manager.cli import run_enable

    result = run_enable(
        project / ".skill-manager.json",
        gconfig,
        cache,
        skills_dir,
        repo="tw93/Waza",
        names=["read", "write"],
        url_resolver=lambda _r: f"file://{upstream}",
    )
    assert [o.action for o in result.outcomes] == ["enabled", "enabled"]
    assert [o.skill["name"] for o in result.outcomes] == ["read", "write"]
    assert result.sync is not None
    proj = load_skill_declarations(project / ".skill-manager.json")
    assert {s.name for s in proj.skills} == {"read", "write"}
    assert (skills_dir / "read").is_symlink()
    assert (skills_dir / "write").is_symlink()


def test_run_enable_batch_mixed_already_enabled(tmp_path: Path, make_source_repo) -> None:
    project, cache, gconfig, skills_dir, upstream = _enable_env(tmp_path, make_source_repo)
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    from skill_manager.cli import run_enable

    result = run_enable(
        project / ".skill-manager.json",
        gconfig,
        cache,
        skills_dir,
        repo="tw93/Waza",
        names=["read", "write"],
        url_resolver=lambda _r: f"file://{upstream}",
    )
    assert [o.action for o in result.outcomes] == ["already_enabled", "enabled"]
    assert result.sync is not None  # a new skill was added
    proj = load_skill_declarations(project / ".skill-manager.json")
    assert {s.name for s in proj.skills} == {"read", "write"}


def test_run_enable_batch_all_already_enabled_no_sync(tmp_path: Path, make_source_repo) -> None:
    project, cache, gconfig, skills_dir, upstream = _enable_env(tmp_path, make_source_repo)
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    before = (project / ".skill-manager.json").read_text()
    from skill_manager.cli import run_enable

    result = run_enable(
        project / ".skill-manager.json",
        gconfig,
        cache,
        skills_dir,
        repo="tw93/Waza",
        names=["read"],
        url_resolver=lambda _r: f"file://{upstream}",
    )
    assert [o.action for o in result.outcomes] == ["already_enabled"]
    assert result.sync is None
    assert (project / ".skill-manager.json").read_text() == before


def test_run_enable_batch_atomic_abort_reports_all(tmp_path: Path, make_source_repo) -> None:
    project, cache, gconfig, skills_dir, upstream = _enable_env(tmp_path, make_source_repo)
    _write_config(project, [])
    from skill_manager.cli import NotFoundError, run_enable

    with pytest.raises(NotFoundError) as exc:
        run_enable(
            project / ".skill-manager.json",
            gconfig,
            cache,
            skills_dir,
            repo="tw93/Waza",
            names=["typoX", "typoY"],
            url_resolver=lambda _r: f"file://{upstream}",
        )
    assert "typoX" in str(exc.value)
    assert "typoY" in str(exc.value)
    # Nothing was written.
    assert load_skill_declarations(project / ".skill-manager.json").skills == []
    assert not (skills_dir / "typoX").exists()


def test_run_enable_batch_partial_invalid_aborts_whole(tmp_path: Path, make_source_repo) -> None:
    """Atomic: one valid + one invalid name applies nothing."""
    project, cache, gconfig, skills_dir, upstream = _enable_env(tmp_path, make_source_repo)
    _write_config(project, [])
    from skill_manager.cli import NotFoundError, run_enable

    with pytest.raises(NotFoundError):
        run_enable(
            project / ".skill-manager.json",
            gconfig,
            cache,
            skills_dir,
            repo="tw93/Waza",
            names=["read", "typoX"],
            url_resolver=lambda _r: f"file://{upstream}",
        )
    proj = load_skill_declarations(project / ".skill-manager.json")
    assert proj.skills == []  # the valid 'read' was NOT applied
    assert not (skills_dir / "read").exists()


def test_run_enable_batch_dedupe(tmp_path: Path, make_source_repo) -> None:
    project, cache, gconfig, skills_dir, upstream = _enable_env(tmp_path, make_source_repo)
    _write_config(project, [])
    from skill_manager.cli import run_enable

    result = run_enable(
        project / ".skill-manager.json",
        gconfig,
        cache,
        skills_dir,
        repo="tw93/Waza",
        names=["read", "read", "write"],
        url_resolver=lambda _r: f"file://{upstream}",
    )
    assert [o.skill["name"] for o in result.outcomes] == ["read", "write"]
    proj = load_skill_declarations(project / ".skill-manager.json")
    assert [s.name for s in proj.skills] == ["read", "write"]


def test_run_enable_batch_name_and_path_forms_same_skill_dedupe(
    tmp_path: Path, make_source_repo
) -> None:
    """F1: name form + path form resolving to the same skill write one declaration."""
    project, cache, gconfig, skills_dir, upstream = _enable_env(tmp_path, make_source_repo)
    _write_config(project, [])
    from skill_manager.cli import run_enable

    result = run_enable(
        project / ".skill-manager.json",
        gconfig,
        cache,
        skills_dir,
        repo="tw93/Waza",
        names=["read", "skills/read"],
        url_resolver=lambda _r: f"file://{upstream}",
    )
    assert [o.skill["name"] for o in result.outcomes] == ["read"]
    proj = load_skill_declarations(project / ".skill-manager.json")
    assert [(s.name, s.repo, s.path) for s in proj.skills] == [("read", "tw93/Waza", "skills/read")]
    assert (skills_dir / "read").is_symlink()


def test_run_enable_batch_same_name_different_paths_atomic_error(
    tmp_path: Path, make_source_repo
) -> None:
    """F1: two args resolving to the same name at different paths abort atomically."""
    project, cache, gconfig, skills_dir, upstream = _enable_env(
        tmp_path,
        make_source_repo,
        {
            "a/dup": skill_md("dup", "first"),
            "b/dup": skill_md("dup", "second"),
        },
    )
    _write_config(project, [])
    from skill_manager.cli import NotFoundError, run_enable

    with pytest.raises(NotFoundError) as exc:
        run_enable(
            project / ".skill-manager.json",
            gconfig,
            cache,
            skills_dir,
            repo="tw93/Waza",
            names=["a/dup", "b/dup"],
            url_resolver=lambda _r: f"file://{upstream}",
        )
    msg = str(exc.value)
    assert "dup" in msg
    assert "a/dup" in msg and "b/dup" in msg
    # Atomic: nothing was written.
    assert load_skill_declarations(project / ".skill-manager.json").skills == []
    assert not (skills_dir / "dup").exists()


# ── run_disable batch ─────────────────────────────────────────────────────────


def _disable_env(tmp_path: Path, make_source_repo, enabled: list[str]):
    project, cache, gconfig, skills_dir, upstream = _enable_env(tmp_path, make_source_repo)
    _write_config(
        project,
        [{"name": n, "repo": "tw93/Waza", "path": f"skills/{n}"} for n in enabled],
    )
    run_sync(
        project / ".skill-manager.json",
        gconfig,
        cache,
        skills_dir,
        url_resolver=lambda _r: f"file://{upstream}",
    )
    return project, cache, gconfig, skills_dir


# ── repo validation + clone registration (issues F3/F4) ─────────────────────────


def test_run_enable_malicious_repo_rejected(tmp_path: Path) -> None:
    """F3: a repo identifier escaping the cache root is rejected before cloning."""
    from skill_manager.cli import run_enable
    from skill_manager.config import ConfigError

    project = tmp_path / "proj"
    project.mkdir()
    cache = tmp_path / "repos"
    escape_target = (cache / "../../tmp" / "x").resolve()
    with pytest.raises(ConfigError, match="invalid repo"):
        run_enable(
            project / ".skill-manager.json",
            tmp_path / "config.json",
            cache,
            project / ".agents" / "skills",
            repo="../../tmp/x",
            names=["w"],
            url_resolver=lambda _r: "file:///nonexistent",
        )
    assert not escape_target.exists()


def test_run_enable_batch_failure_registers_cloned_source(tmp_path: Path, make_source_repo) -> None:
    """F4: a failed batch keeps the clone's global-config registration (no orphan)."""
    from skill_manager.cli import NotFoundError, run_enable

    upstream = make_source_repo("waza", {"skills/read": skill_md("read")})
    project = tmp_path / "proj"
    project.mkdir()
    cache = tmp_path / "repos"
    gconfig = tmp_path / "config.json"
    skills_dir = project / ".agents" / "skills"
    _write_config(project, [])
    with pytest.raises(NotFoundError, match="typoX"):
        run_enable(
            project / ".skill-manager.json",
            gconfig,
            cache,
            skills_dir,
            repo="tw93/Waza",
            names=["read", "typoX"],
            url_resolver=lambda _r: f"file://{upstream}",
        )
    # Atomic for declarations: nothing enabled.
    assert load_skill_declarations(project / ".skill-manager.json").skills == []
    # But the clone is registered, so it is visible/removable via source commands.
    cfg = load_global_config(gconfig)
    assert "tw93/Waza" in cfg.sources
    assert (cache / "tw93" / "Waza" / ".git").is_dir()


def test_run_disable_batch_multiple(tmp_path: Path, make_source_repo) -> None:
    project, cache, gconfig, skills_dir = _disable_env(
        tmp_path, make_source_repo, ["read", "write"]
    )
    from skill_manager.cli import run_disable

    result = run_disable(
        project / ".skill-manager.json", gconfig, cache, skills_dir, names=["read", "write"]
    )
    assert [o.action for o in result.outcomes] == ["disabled", "disabled"]
    assert all(o.link_removed for o in result.outcomes)
    assert load_skill_declarations(project / ".skill-manager.json").skills == []
    assert not (skills_dir / "read").exists()
    assert not (skills_dir / "write").exists()


def test_run_disable_batch_lenient_not_enabled(tmp_path: Path, make_source_repo) -> None:
    project, cache, gconfig, skills_dir = _disable_env(tmp_path, make_source_repo, ["read"])
    from skill_manager.cli import run_disable

    result = run_disable(
        project / ".skill-manager.json", gconfig, cache, skills_dir, names=["read", "ghost"]
    )
    assert [o.action for o in result.outcomes] == ["disabled", "not_enabled"]
    assert result.outcomes[1].skill == {"name": "ghost"}
    assert load_skill_declarations(project / ".skill-manager.json").skills == []


def test_run_disable_batch_dedupe(tmp_path: Path, make_source_repo) -> None:
    project, cache, gconfig, skills_dir = _disable_env(tmp_path, make_source_repo, ["read"])
    from skill_manager.cli import run_disable

    result = run_disable(
        project / ".skill-manager.json", gconfig, cache, skills_dir, names=["read", "read"]
    )
    assert [o.action for o in result.outcomes] == ["disabled"]


# ── CLI seam helpers (offline sync via git remote rewrite) ────────────────────


def _seed_cli(tmp_path: Path, make_source_repo, skills: dict[str, str] | None = None) -> Path:
    """Seed isolated cache and point the cached origin at the file:// upstream."""
    from skill_manager import paths
    from skill_manager.sources import clone_source

    skills = skills or SKILLS
    upstream = make_source_repo("waza", skills)
    cfg = GlobalConfig()
    clone_source("tw93/Waza", cfg, paths.repos_cache_dir(), url=f"file://{upstream}")
    save_global_config(paths.config_file(), cfg)
    cache_repo = paths.repos_cache_dir() / "tw93" / "Waza"
    subprocess.run(
        ["git", "remote", "set-url", "origin", f"file://{upstream}"],
        cwd=cache_repo,
        check=True,
        capture_output=True,
    )
    project = tmp_path / "proj"
    project.mkdir()
    return project


def test_cli_enable_batch_variadic(tmp_path: Path, make_source_repo, monkeypatch) -> None:
    project = _seed_cli(tmp_path, make_source_repo)
    _write_config(project, [])
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["enable", "tw93/Waza", "read", "write"])
    assert result.exit_code == 0, result.output
    proj = load_skill_declarations(project / ".skill-manager.json")
    assert {s.name for s in proj.skills} == {"read", "write"}
    assert (project / ".agents" / "skills" / "read").is_symlink()
    assert (project / ".agents" / "skills" / "write").is_symlink()


def test_cli_enable_repo_only_usage_error(tmp_path: Path, make_source_repo, monkeypatch) -> None:
    project = _seed_cli(tmp_path, make_source_repo)
    _write_config(project, [])
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["enable", "tw93/Waza"])
    assert result.exit_code == 2


def test_cli_enable_batch_atomic_exit1(tmp_path: Path, make_source_repo, monkeypatch) -> None:
    project = _seed_cli(tmp_path, make_source_repo)
    _write_config(project, [])
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["enable", "tw93/Waza", "read", "typoX"])
    assert result.exit_code == 1
    assert "typoX" in result.output
    # Atomic: the valid 'read' was not applied.
    assert load_skill_declarations(project / ".skill-manager.json").skills == []


def test_cli_disable_batch(tmp_path: Path, make_source_repo, monkeypatch) -> None:
    project = _seed_cli(tmp_path, make_source_repo)
    _write_config(
        project,
        [
            {"name": "read", "repo": "tw93/Waza", "path": "skills/read"},
            {"name": "write", "repo": "tw93/Waza", "path": "skills/write"},
        ],
    )
    monkeypatch.chdir(project)
    runner.invoke(app, ["sync"])
    result = runner.invoke(app, ["disable", "read", "write"])
    assert result.exit_code == 0, result.output
    assert load_skill_declarations(project / ".skill-manager.json").skills == []


# ── JSON seam: uniform results wrapper ────────────────────────────────────────


def test_json_enable_batch_results_shape(tmp_path: Path, make_source_repo, monkeypatch) -> None:
    project = _seed_cli(tmp_path, make_source_repo)
    _write_config(project, [])
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["--json", "enable", "tw93/Waza", "read", "write"])
    assert result.exit_code == 0, result.output
    data = _parse_json(result)["data"]
    assert [r["action"] for r in data["results"]] == ["enabled", "enabled"]
    assert [r["skill"]["name"] for r in data["results"]] == ["read", "write"]
    assert data["sync"]["links"] == [
        {"name": "read", "action": "created"},
        {"name": "write", "action": "created"},
    ]


def test_json_enable_single_uses_results_wrapper(
    tmp_path: Path, make_source_repo, monkeypatch
) -> None:
    """Breaking change: single enable now returns a 1-element results array."""
    project = _seed_cli(tmp_path, make_source_repo)
    _write_config(project, [])
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["--json", "enable", "tw93/Waza", "read"])
    assert result.exit_code == 0, result.output
    data = _parse_json(result)["data"]
    assert data["results"] == [
        {
            "action": "enabled",
            "skill": {"name": "read", "repo": "tw93/Waza", "path": "skills/read"},
            "enabled_globally": False,
        }
    ]
    assert "sync" in data


def test_json_disable_batch_results_shape(tmp_path: Path, make_source_repo, monkeypatch) -> None:
    project = _seed_cli(tmp_path, make_source_repo)
    _write_config(
        project,
        [
            {"name": "read", "repo": "tw93/Waza", "path": "skills/read"},
            {"name": "write", "repo": "tw93/Waza", "path": "skills/write"},
        ],
    )
    monkeypatch.chdir(project)
    runner.invoke(app, ["sync"])
    result = runner.invoke(app, ["--json", "disable", "read", "write"])
    assert result.exit_code == 0, result.output
    data = _parse_json(result)["data"]
    assert [r["action"] for r in data["results"]] == ["disabled", "disabled"]
    assert all(r["link_removed"] is True for r in data["results"])


def test_json_disable_not_enabled_results_shape(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path, [])
    result = runner.invoke(app, ["--json", "disable", "read"])
    assert result.exit_code == 0, result.output
    data = _parse_json(result)["data"]
    assert data == {"results": [{"action": "not_enabled", "skill": {"name": "read"}}]}


# ── interactive CLI guards (no TTY / no numbered menus) ───────────────────────


def test_cli_interactive_enable_requires_tty(tmp_path: Path, make_source_repo, monkeypatch) -> None:
    project = _seed_cli(tmp_path, make_source_repo)
    _write_config(project, [])
    monkeypatch.chdir(project)
    # CliRunner is non-TTY; interactive enable must fail clearly, not hang.
    result = runner.invoke(app, ["enable"])
    assert result.exit_code == 1, result.output
    assert "TTY" in result.output
    assert load_skill_declarations(project / ".skill-manager.json").skills == []


def test_cli_interactive_disable_requires_tty(
    tmp_path: Path, make_source_repo, monkeypatch
) -> None:
    project = _seed_cli(tmp_path, make_source_repo)
    _write_config(
        project,
        [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}],
    )
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["disable"])
    assert result.exit_code == 1, result.output
    assert "TTY" in result.output
    assert len(load_skill_declarations(project / ".skill-manager.json").skills) == 1
