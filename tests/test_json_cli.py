"""CLI process-boundary tests for --json, enable/disable non-interactive, available-skills."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from skill_manager.cli import app, run_sync
from skill_manager.config import load_skill_declarations

runner = CliRunner()


def _write_config(project: Path, skills: list[dict]) -> None:
    (project / ".skill-manager.json").write_text(json.dumps({"skills": skills}), encoding="utf-8")


def _parse_json(result) -> dict:
    assert result.stdout.strip(), f"empty stdout; stderr={result.stderr!r} output={result.output!r}"
    return json.loads(result.stdout)


def _seed_cached_source(
    tmp_path: Path, make_source_repo, skills: dict[str, str] | None = None
) -> tuple[Path, str]:
    """Clone a file:// source into the isolated XDG cache; return (project, full_commit)."""
    from skill_manager import paths
    from skill_manager.config import GlobalConfig, save_global_config
    from skill_manager.sources import ensure_source

    skills = skills or {"skills/read": "# read\n"}
    upstream = make_source_repo("waza", skills)
    url = f"file://{upstream}"
    cfg = GlobalConfig()
    head = ensure_source("tw93/Waza", cfg, paths.repos_cache_dir(), url=url)
    save_global_config(paths.config_file(), cfg)
    project = tmp_path / "proj"
    project.mkdir()
    return project, head


# ── root --json plumbing ──────────────────────────────────────────────────────


def test_json_sync_missing_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["--json", "sync"])
    assert result.exit_code == 1
    body = _parse_json(result)
    assert body == {
        "ok": False,
        "error": {
            "code": "config_error",
            "message": body["error"]["message"],
        },
    }
    assert "not found" in body["error"]["message"]


def test_json_flag_on_subcommand_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path, [])
    result = runner.invoke(app, ["sync", "--json"])
    assert result.exit_code == 2
    # Root --json was not set: usage text, not a JSON envelope.
    assert not result.stdout.lstrip().startswith("{")


def test_version_with_json_still_eager() -> None:
    from skill_manager import __version__

    result = runner.invoke(app, ["--json", "--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout
    # must not be wrapped in envelope
    assert "skill-manager" in result.stdout


def test_help_with_json_still_eager() -> None:
    result = runner.invoke(app, ["--json", "--help"])
    assert result.exit_code == 0
    assert "Usage" in result.stdout or "usage" in result.stdout.lower()


# ── sync --json ───────────────────────────────────────────────────────────────


def test_json_sync_success(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, head = _seed_cached_source(tmp_path, make_source_repo)
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    monkeypatch.chdir(project)

    # Re-point ensure to file:// by patching repo_url used inside sync path —
    # source is already cached so pull uses origin; set origin to file://.
    cache_repo = tmp_path / "cache" / "skill-manager" / "repos" / "tw93" / "Waza"
    # isolated_xdg puts cache under tmp_path/cache
    from skill_manager import paths

    cache_repo = paths.repos_cache_dir() / "tw93" / "Waza"
    upstream = tmp_path / "sources" / "waza"
    subprocess.run(
        ["git", "remote", "set-url", "origin", f"file://{upstream}"],
        cwd=cache_repo,
        check=True,
        capture_output=True,
    )

    result = runner.invoke(app, ["--json", "sync"])
    assert result.exit_code == 0, result.output
    body = _parse_json(result)
    assert body["ok"] is True
    data = body["data"]
    assert data["sources"] == [{"repo": "tw93/Waza", "commit": head}]
    assert data["links"] == [{"name": "read", "action": "created"}]
    assert "target" not in data["links"][0]
    assert (project / ".agents" / "skills" / "read").is_symlink()


def test_json_sync_config_error_envelope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".skill-manager.json").write_text("{bad", encoding="utf-8")
    result = runner.invoke(app, ["--json", "sync"])
    assert result.exit_code == 1
    body = _parse_json(result)
    assert body["ok"] is False
    assert body["error"]["code"] == "config_error"
    assert "invalid JSON" in body["error"]["message"]


# ── list --json ───────────────────────────────────────────────────────────────


def test_json_list_skills_shape(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _head = _seed_cached_source(tmp_path, make_source_repo)
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    from skill_manager import paths

    run_sync(
        project / ".skill-manager.json",
        paths.config_file(),
        paths.repos_cache_dir(),
        project / ".agents" / "skills",
        url_resolver=lambda r: f"file://{tmp_path / 'sources' / 'waza'}",
    )
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["--json", "list"])
    assert result.exit_code == 0, result.output
    body = _parse_json(result)
    assert body["ok"] is True
    assert "sources" not in body["data"]
    assert body["data"]["skills"] == [
        {
            "name": "read",
            "repo": "tw93/Waza",
            "path": "skills/read",
            "link": "linked",
        }
    ]


def test_list_human_keeps_sources_section(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _head = _seed_cached_source(tmp_path, make_source_repo)
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    from skill_manager import paths

    run_sync(
        project / ".skill-manager.json",
        paths.config_file(),
        paths.repos_cache_dir(),
        project / ".agents" / "skills",
        url_resolver=lambda r: f"file://{tmp_path / 'sources' / 'waza'}",
    )
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "Sources:" in result.stdout
    assert "cached" in result.stdout
    assert "Skills:" in result.stdout
    assert "linked" in result.stdout


# ── source list/add/remove/update --json ──────────────────────────────────────


def test_json_source_list(tmp_path: Path, make_source_repo) -> None:
    _project, head = _seed_cached_source(tmp_path, make_source_repo)
    result = runner.invoke(app, ["--json", "source", "list"])
    assert result.exit_code == 0, result.output
    body = _parse_json(result)
    assert body["ok"] is True
    sources = body["data"]["sources"]
    assert len(sources) == 1
    assert sources[0]["repo"] == "tw93/Waza"
    assert sources[0]["commit"] == head
    assert sources[0]["url"].startswith("file://")
    assert "status" not in sources[0]
    assert "cached" not in sources[0]


def test_json_source_add_already_exists(tmp_path: Path, make_source_repo) -> None:
    _project, head = _seed_cached_source(tmp_path, make_source_repo)
    result = runner.invoke(app, ["--json", "source", "add", "tw93/Waza"])
    assert result.exit_code == 0, result.output
    body = _parse_json(result)
    assert body["ok"] is True
    assert body["data"]["action"] == "already_exists"
    assert body["data"]["repo"] == "tw93/Waza"
    assert body["data"]["commit"] == head


def test_json_source_remove_success(tmp_path: Path, make_source_repo) -> None:
    _seed_cached_source(tmp_path, make_source_repo)
    result = runner.invoke(app, ["--json", "source", "remove", "tw93/Waza"])
    assert result.exit_code == 0, result.output
    body = _parse_json(result)
    assert body["ok"] is True
    assert body["data"] == {"action": "removed", "repo": "tw93/Waza"}


def test_json_source_remove_not_found(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--json", "source", "remove", "no/such"])
    assert result.exit_code == 1
    body = _parse_json(result)
    assert body["ok"] is False
    assert body["error"]["code"] == "not_found"


def test_json_source_update_empty_ledger(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--json", "source", "update"])
    assert result.exit_code == 0, result.output
    body = _parse_json(result)
    assert body["ok"] is True
    assert body["data"] == {"updates": []}


def test_json_source_update_not_registered(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--json", "source", "update", "no/such"])
    assert result.exit_code == 1
    body = _parse_json(result)
    assert body["ok"] is False
    assert body["error"]["code"] == "not_found"


def test_json_source_update_one(tmp_path: Path, make_source_repo) -> None:
    _project, head = _seed_cached_source(tmp_path, make_source_repo)
    from skill_manager import paths

    cache_repo = paths.repos_cache_dir() / "tw93" / "Waza"
    upstream = tmp_path / "sources" / "waza"
    subprocess.run(
        ["git", "remote", "set-url", "origin", f"file://{upstream}"],
        cwd=cache_repo,
        check=True,
        capture_output=True,
    )
    result = runner.invoke(app, ["--json", "source", "update", "tw93/Waza"])
    assert result.exit_code == 0, result.output
    body = _parse_json(result)
    assert body["ok"] is True
    assert body["data"]["updates"] == [{"action": "updated", "repo": "tw93/Waza", "commit": head}]


# ── source available-skills ───────────────────────────────────────────────────


def test_json_available_skills_all(tmp_path: Path, make_source_repo) -> None:
    _seed_cached_source(
        tmp_path,
        make_source_repo,
        {"skills/read": "# r\n", "skills/write": "# w\n"},
    )
    result = runner.invoke(app, ["--json", "source", "available-skills"])
    assert result.exit_code == 0, result.output
    body = _parse_json(result)
    assert body["ok"] is True
    skills = body["data"]["skills"]
    assert {"name": "read", "repo": "tw93/Waza", "path": "skills/read"} in skills
    assert {"name": "write", "repo": "tw93/Waza", "path": "skills/write"} in skills


def test_json_available_skills_named_repo(tmp_path: Path, make_source_repo) -> None:
    _seed_cached_source(tmp_path, make_source_repo)
    result = runner.invoke(app, ["--json", "source", "available-skills", "tw93/Waza"])
    assert result.exit_code == 0, result.output
    body = _parse_json(result)
    assert body["data"]["skills"] == [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}]


def test_json_available_skills_not_cached(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--json", "source", "available-skills", "no/such"])
    assert result.exit_code == 1
    body = _parse_json(result)
    assert body["error"]["code"] == "not_found"


def test_json_available_skills_empty(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--json", "source", "available-skills"])
    assert result.exit_code == 0, result.output
    body = _parse_json(result)
    assert body["data"]["skills"] == []


def test_available_skills_human_empty(tmp_path: Path) -> None:
    result = runner.invoke(app, ["source", "available-skills"])
    assert result.exit_code == 0
    assert "No skills found" in result.stdout


def test_available_skills_human_grouped(tmp_path: Path, make_source_repo) -> None:
    _seed_cached_source(
        tmp_path, make_source_repo, {"skills/read": "# r\n", "skills/write": "# w\n"}
    )
    result = runner.invoke(app, ["source", "available-skills"])
    assert result.exit_code == 0
    assert "tw93/Waza" in result.stdout
    assert "read" in result.stdout
    assert "write" in result.stdout


# ── enable / disable non-interactive ──────────────────────────────────────────


def test_json_enable_success(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, head = _seed_cached_source(tmp_path, make_source_repo)
    _write_config(project, [])
    monkeypatch.chdir(project)
    from skill_manager import paths

    cache_repo = paths.repos_cache_dir() / "tw93" / "Waza"
    upstream = tmp_path / "sources" / "waza"
    subprocess.run(
        ["git", "remote", "set-url", "origin", f"file://{upstream}"],
        cwd=cache_repo,
        check=True,
        capture_output=True,
    )

    result = runner.invoke(app, ["--json", "enable", "tw93/Waza", "read"])
    assert result.exit_code == 0, result.output
    body = _parse_json(result)
    assert body["ok"] is True
    data = body["data"]
    assert data["results"] == [
        {"action": "enabled", "skill": {"name": "read", "repo": "tw93/Waza", "path": "skills/read"}}
    ]
    assert data["sync"]["sources"] == [{"repo": "tw93/Waza", "commit": head}]
    assert data["sync"]["links"] == [{"name": "read", "action": "created"}]
    proj = load_skill_declarations(project / ".skill-manager.json")
    assert len(proj.skills) == 1
    assert (project / ".agents" / "skills" / "read").is_symlink()


def test_json_enable_already_enabled(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _head = _seed_cached_source(tmp_path, make_source_repo)
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    monkeypatch.chdir(project)
    before = (project / ".skill-manager.json").read_text()
    result = runner.invoke(app, ["--json", "enable", "tw93/Waza", "read"])
    assert result.exit_code == 0, result.output
    body = _parse_json(result)
    assert body["data"]["results"] == [
        {
            "action": "already_enabled",
            "skill": {"name": "read", "repo": "tw93/Waza", "path": "skills/read"},
        }
    ]
    assert "sync" not in body["data"]
    assert (project / ".skill-manager.json").read_text() == before


def test_json_enable_bootstraps_missing_project_config(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """enable --json creates a valid project config when none exists."""
    project, head = _seed_cached_source(tmp_path, make_source_repo)
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["--json", "enable", "tw93/Waza", "read"])
    assert result.exit_code == 0, result.output
    body = _parse_json(result)
    assert body["ok"] is True
    data = body["data"]
    assert data["results"] == [
        {"action": "enabled", "skill": {"name": "read", "repo": "tw93/Waza", "path": "skills/read"}}
    ]
    assert data["sync"]["sources"] == [{"repo": "tw93/Waza", "commit": head}]
    assert data["sync"]["links"] == [{"name": "read", "action": "created"}]
    proj = load_skill_declarations(project / ".skill-manager.json")
    assert len(proj.skills) == 1
    assert (project / ".agents" / "skills" / "read").is_symlink()


def test_json_enable_bootstraps_empty_project_config(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """enable --json treats a zero-byte project config as an empty skill list."""
    project, head = _seed_cached_source(tmp_path, make_source_repo)
    (project / ".skill-manager.json").write_text("", encoding="utf-8")
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["--json", "enable", "tw93/Waza", "read"])
    assert result.exit_code == 0, result.output
    body = _parse_json(result)
    assert body["ok"] is True
    data = body["data"]
    assert data["results"] == [
        {"action": "enabled", "skill": {"name": "read", "repo": "tw93/Waza", "path": "skills/read"}}
    ]
    assert data["sync"]["sources"] == [{"repo": "tw93/Waza", "commit": head}]
    proj = load_skill_declarations(project / ".skill-manager.json")
    assert len(proj.skills) == 1


def test_json_enable_invalid_project_config(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """enable --json still surfaces malformed project JSON as a config error."""
    project, _ = _seed_cached_source(tmp_path, make_source_repo)
    (project / ".skill-manager.json").write_text("{not json", encoding="utf-8")
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["--json", "enable", "tw93/Waza", "read"])
    assert result.exit_code == 1, result.output
    body = _parse_json(result)
    assert body["ok"] is False
    assert body["error"]["code"] == "config_error"
    assert "invalid JSON" in body["error"]["message"]


def test_json_enable_repo_not_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path, [])
    result = runner.invoke(app, ["--json", "enable", "no/such", "read"])
    assert result.exit_code == 1
    body = _parse_json(result)
    assert body["error"]["code"] == "not_found"


def test_json_enable_name_not_found(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _ = _seed_cached_source(tmp_path, make_source_repo)
    _write_config(project, [])
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["--json", "enable", "tw93/Waza", "missing"])
    assert result.exit_code == 1
    body = _parse_json(result)
    assert body["error"]["code"] == "not_found"


def test_json_enable_ambiguous_name(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _ = _seed_cached_source(
        tmp_path,
        make_source_repo,
        {"a/dup": "# a\n", "b/dup": "# b\n"},
    )
    _write_config(project, [])
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["--json", "enable", "tw93/Waza", "dup"])
    assert result.exit_code == 1
    body = _parse_json(result)
    assert body["error"]["code"] == "not_found"
    assert "a/dup" in body["error"]["message"]
    assert "b/dup" in body["error"]["message"]


def test_json_enable_missing_args_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path, [])
    result = runner.invoke(app, ["--json", "enable"])
    assert result.exit_code == 2
    body = _parse_json(result)
    assert body["ok"] is False
    assert body["error"]["code"] == "usage_error"


def test_json_enable_one_arg_usage_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path, [])
    result = runner.invoke(app, ["--json", "enable", "tw93/Waza"])
    assert result.exit_code == 2
    body = _parse_json(result)
    assert body["error"]["code"] == "usage_error"


def test_enable_one_arg_text_usage_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path, [])
    result = runner.invoke(app, ["enable", "tw93/Waza"])
    assert result.exit_code == 2


def test_json_disable_success(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _ = _seed_cached_source(tmp_path, make_source_repo)
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    from skill_manager import paths

    run_sync(
        project / ".skill-manager.json",
        paths.config_file(),
        paths.repos_cache_dir(),
        project / ".agents" / "skills",
        url_resolver=lambda r: f"file://{tmp_path / 'sources' / 'waza'}",
    )
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["--json", "disable", "read"])
    assert result.exit_code == 0, result.output
    body = _parse_json(result)
    assert body["ok"] is True
    assert body["data"]["results"][0]["action"] == "disabled"
    assert body["data"]["results"][0]["skill"] == {
        "name": "read",
        "repo": "tw93/Waza",
        "path": "skills/read",
    }
    assert body["data"]["results"][0]["link_removed"] is True
    proj = load_skill_declarations(project / ".skill-manager.json")
    assert proj.skills == []
    assert not (project / ".agents" / "skills" / "read").exists()


def test_json_disable_not_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path, [])
    result = runner.invoke(app, ["--json", "disable", "read"])
    assert result.exit_code == 0, result.output
    body = _parse_json(result)
    assert body["data"] == {"results": [{"action": "not_enabled", "skill": {"name": "read"}}]}


def test_json_disable_missing_args_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path, [])
    result = runner.invoke(app, ["--json", "disable"])
    assert result.exit_code == 2
    body = _parse_json(result)
    assert body["error"]["code"] == "usage_error"


def test_json_disable_external_link_not_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    skills_dir = tmp_path / ".agents" / "skills"
    skills_dir.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "SKILL.md").write_text("# x\n", encoding="utf-8")
    (skills_dir / "read").symlink_to(elsewhere)
    result = runner.invoke(app, ["--json", "disable", "read"])
    assert result.exit_code == 0, result.output
    body = _parse_json(result)
    assert body["data"]["results"][0]["action"] == "disabled"
    assert body["data"]["results"][0]["link_removed"] is False
    assert (skills_dir / "read").is_symlink()
