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
    "skills/read": "# read\n",
    "skills/write": "# write\n",
    "skills/kami": "# kami\n",
}


def _write_config(project: Path, skills: list[dict]) -> None:
    (project / ".skill-manager.json").write_text(json.dumps({"skills": skills}), encoding="utf-8")


def _parse_json(result) -> dict:
    assert result.stdout.strip(), f"empty stdout; output={result.output!r}"
    return json.loads(result.stdout)


# ── run_* seam helpers (unit, url_resolver injected) ──────────────────────────


def _enable_env(tmp_path: Path, make_source_repo, skills: dict[str, str] | None = None):
    """Seed cache + global config; return (project, cache, gconfig, skills_dir, upstream)."""
    from skill_manager.sources import ensure_source

    skills = skills or SKILLS
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
    from skill_manager.sources import ensure_source

    skills = skills or SKILLS
    upstream = make_source_repo("waza", skills)
    cfg = GlobalConfig()
    ensure_source("tw93/Waza", cfg, paths.repos_cache_dir(), url=f"file://{upstream}")
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
        {"action": "enabled", "skill": {"name": "read", "repo": "tw93/Waza", "path": "skills/read"}}
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


# ── interactive multi-select ──────────────────────────────────────────────────


def test_interactive_enable_multi_select(tmp_path: Path, make_source_repo, monkeypatch) -> None:
    project = _seed_cli(tmp_path, make_source_repo)
    _write_config(project, [])
    monkeypatch.chdir(project)
    # Menu order is alphabetical: 1=kami, 2=read, 3=write. Select read+write.
    result = runner.invoke(app, ["enable"], input="1\n2 3\n")
    assert result.exit_code == 0, result.output
    proj = load_skill_declarations(project / ".skill-manager.json")
    assert {s.name for s in proj.skills} == {"read", "write"}


def test_interactive_disable_multi_select(tmp_path: Path, make_source_repo, monkeypatch) -> None:
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
    result = runner.invoke(app, ["disable"], input="1 2\n")
    assert result.exit_code == 0, result.output
    assert load_skill_declarations(project / ".skill-manager.json").skills == []


def test_interactive_multi_select_comma(tmp_path: Path, make_source_repo, monkeypatch) -> None:
    project = _seed_cli(tmp_path, make_source_repo)
    _write_config(project, [])
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["enable"], input="1\n2,3\n")
    assert result.exit_code == 0, result.output
    proj = load_skill_declarations(project / ".skill-manager.json")
    assert {s.name for s in proj.skills} == {"read", "write"}


def test_interactive_multi_select_dedupe_ascending(
    tmp_path: Path, make_source_repo, monkeypatch
) -> None:
    project = _seed_cli(tmp_path, make_source_repo)
    _write_config(project, [])
    monkeypatch.chdir(project)
    # "3 2 3" -> deduped, ascending -> read(2), write(3)
    result = runner.invoke(app, ["enable"], input="1\n3 2 3\n")
    assert result.exit_code == 0, result.output
    proj = load_skill_declarations(project / ".skill-manager.json")
    assert [s.name for s in proj.skills] == ["read", "write"]


def test_interactive_multi_select_invalid_reprompt(
    tmp_path: Path, make_source_repo, monkeypatch
) -> None:
    project = _seed_cli(tmp_path, make_source_repo)
    _write_config(project, [])
    monkeypatch.chdir(project)
    # repo 1; skill prompt: "99" invalid -> re-prompt -> "2" (read)
    result = runner.invoke(app, ["enable"], input="1\n99\n2\n")
    assert result.exit_code == 0, result.output
    proj = load_skill_declarations(project / ".skill-manager.json")
    assert [s.name for s in proj.skills] == ["read"]
