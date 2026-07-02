import json
import shutil
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from skill_manager.cli import app, run_list, run_sync
from skill_manager.config import SkillRef, load_project_config
from skill_manager.links import LinkError

runner = CliRunner()


def _write_config(project: Path, skills: list[dict]) -> None:
    (project / ".skill-manager.json").write_text(json.dumps({"skills": skills}), encoding="utf-8")


def test_sync_help() -> None:
    result = runner.invoke(app, ["sync", "--help"])
    assert result.exit_code == 0
    assert "sync" in result.stdout.lower()


def test_list_help() -> None:
    result = runner.invoke(app, ["list", "--help"])
    assert result.exit_code == 0
    assert "list" in result.stdout.lower()


def test_sync_missing_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_sync_bad_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".skill-manager.json").write_text("{bad", encoding="utf-8")
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 1
    assert "invalid JSON" in result.output


def test_run_sync_end_to_end(tmp_path: Path, make_source_repo) -> None:
    repo = make_source_repo("waza", {"skills/read": "# read\n"})
    url = f"file://{repo}"
    project = tmp_path / "proj"
    project.mkdir()
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    cache = tmp_path / "repos"
    gconfig = tmp_path / "config.json"
    skills_dir = project / ".agents" / "skills"
    run_sync(
        project / ".skill-manager.json", gconfig, cache, skills_dir, url_resolver=lambda r: url
    )
    assert (skills_dir / "read").is_symlink()
    assert (skills_dir / "read" / "SKILL.md").read_text() == "# read\n"
    gdata = json.loads(gconfig.read_text())
    assert "tw93/Waza" in gdata["sources"]
    assert len(gdata["sources"]["tw93/Waza"]["commit"]) == 40


def test_run_sync_idempotent(tmp_path: Path, make_source_repo) -> None:
    repo = make_source_repo("waza", {"skills/read": "# read\n"})
    url = f"file://{repo}"
    project = tmp_path / "proj"
    project.mkdir()
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    cache = tmp_path / "repos"
    gconfig = tmp_path / "config.json"
    skills_dir = project / ".agents" / "skills"
    run_sync(
        project / ".skill-manager.json", gconfig, cache, skills_dir, url_resolver=lambda r: url
    )
    run_sync(
        project / ".skill-manager.json", gconfig, cache, skills_dir, url_resolver=lambda r: url
    )
    assert (skills_dir / "read").is_symlink()


def test_run_sync_path_not_found(tmp_path: Path, make_source_repo) -> None:
    repo = make_source_repo("waza", {"skills/read": "# read\n"})
    url = f"file://{repo}"
    project = tmp_path / "proj"
    project.mkdir()
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/missing"}])
    cache = tmp_path / "repos"
    gconfig = tmp_path / "config.json"
    skills_dir = project / ".agents" / "skills"
    with pytest.raises(LinkError, match="not found"):
        run_sync(
            project / ".skill-manager.json", gconfig, cache, skills_dir, url_resolver=lambda r: url
        )


def test_run_list(tmp_path: Path, make_source_repo, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_source_repo("waza", {"skills/read": "# read\n"})
    url = f"file://{repo}"
    project = tmp_path / "proj"
    project.mkdir()
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    cache = tmp_path / "repos"
    gconfig = tmp_path / "config.json"
    skills_dir = project / ".agents" / "skills"
    run_sync(
        project / ".skill-manager.json", gconfig, cache, skills_dir, url_resolver=lambda r: url
    )
    run_list(project / ".skill-manager.json", gconfig, cache, skills_dir)
    captured = capsys.readouterr()
    assert "tw93/Waza" in captured.out
    assert "read" in captured.out
    assert "linked" in captured.out


def test_run_list_external_symlink(
    tmp_path: Path,
    make_source_repo,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = make_source_repo("waza", {"skills/read": "# read\n"})
    url = f"file://{repo}"
    project = tmp_path / "proj"
    project.mkdir()
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    cache = tmp_path / "repos"
    gconfig = tmp_path / "config.json"
    skills_dir = project / ".agents" / "skills"
    skills_dir.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "SKILL.md").write_text("# other\n", encoding="utf-8")
    (skills_dir / "read").symlink_to(elsewhere)
    run_sync(
        project / ".skill-manager.json", gconfig, cache, skills_dir, url_resolver=lambda r: url
    )
    run_list(project / ".skill-manager.json", gconfig, cache, skills_dir)
    captured = capsys.readouterr()
    assert "read  tw93/Waza:skills/read  external" in captured.out


def test_run_list_broken(
    tmp_path: Path, make_source_repo, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_source_repo("waza", {"skills/read": "# read\n"})
    url = f"file://{repo}"
    project = tmp_path / "proj"
    project.mkdir()
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    cache = tmp_path / "repos"
    gconfig = tmp_path / "config.json"
    skills_dir = project / ".agents" / "skills"
    run_sync(
        project / ".skill-manager.json", gconfig, cache, skills_dir, url_resolver=lambda r: url
    )
    # remove the cached skill target -> symlink now dangles but still points at the declared path
    shutil.rmtree(cache / "tw93" / "Waza" / "skills" / "read")
    run_list(project / ".skill-manager.json", gconfig, cache, skills_dir)
    captured = capsys.readouterr()
    assert "read  tw93/Waza:skills/read  broken" in captured.out


# ── enable / disable ──────────────────────────────────────────────────────────


def test_enable_help() -> None:
    """``skill-manager enable --help`` shows help."""
    result = runner.invoke(app, ["enable", "--help"])
    assert result.exit_code == 0
    assert "enable" in result.stdout.lower()


def test_disable_help() -> None:
    """``skill-manager disable --help`` shows help."""
    result = runner.invoke(app, ["disable", "--help"])
    assert result.exit_code == 0
    assert "disable" in result.stdout.lower()


def test_enable_no_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """enable with empty cache prints error and exits non-zero."""
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path, [])
    result = runner.invoke(app, ["enable"])
    assert result.exit_code == 1
    assert "No cached repos found" in result.output


def test_disable_no_skills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """disable with no enabled skills prints message."""
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path, [])
    result = runner.invoke(app, ["disable"])
    assert result.exit_code == 0
    assert "No enabled skills to disable" in result.output


def _enable_test_env(tmp_path: Path, make_source_repo):
    repo = make_source_repo("waza", {"skills/read": "# read\n"})
    url = f"file://{repo}"
    project = tmp_path / "proj"
    project.mkdir()
    cache = tmp_path / "repos"
    gconfig = tmp_path / "config.json"
    skills_dir = project / ".agents" / "skills"
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    run_sync(
        project / ".skill-manager.json", gconfig, cache, skills_dir, url_resolver=lambda r: url
    )
    return project, cache, gconfig, skills_dir


def test_run_enable_adds_and_syncs(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_enable scans cached repo, adds skill to config, and syncs."""
    project, cache, gconfig, skills_dir = _enable_test_env(tmp_path, make_source_repo)
    _write_config(project, [])  # clear config so we can re-add via enable

    from skill_manager.cli import run_enable

    monkeypatch.setattr("builtins.input", lambda prompt="": next(iter(["1", "1"])))
    run_enable(project / ".skill-manager.json", gconfig, cache, skills_dir)

    proj = load_project_config(project / ".skill-manager.json")
    assert len(proj.skills) == 1
    assert proj.skills[0] == SkillRef("read", "tw93/Waza", "skills/read")
    assert (skills_dir / "read").is_symlink()


def test_run_enable_duplicate(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_enable with an already-enabled skill name errors."""
    project, cache, gconfig, skills_dir = _enable_test_env(tmp_path, make_source_repo)
    # Keep config as-is so the skill is already enabled

    from skill_manager.cli import run_enable

    monkeypatch.setattr("builtins.input", lambda prompt="": next(iter(["1", "1"])))
    with pytest.raises(typer.Exit):
        run_enable(project / ".skill-manager.json", gconfig, cache, skills_dir)


def test_run_disable_removes_and_cleans(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_disable removes config entry and deletes matching symlink."""
    repo = make_source_repo("waza", {"skills/read": "# read\n"})
    url = f"file://{repo}"
    project = tmp_path / "proj"
    project.mkdir()
    cache = tmp_path / "repos"
    gconfig = tmp_path / "config.json"
    skills_dir = project / ".agents" / "skills"

    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    run_sync(
        project / ".skill-manager.json", gconfig, cache, skills_dir, url_resolver=lambda r: url
    )
    assert (skills_dir / "read").is_symlink()

    # Run disable: select skill #1
    monkeypatch.setattr("builtins.input", lambda prompt="": "1")

    from skill_manager.cli import run_disable

    run_disable(project / ".skill-manager.json", gconfig, cache, skills_dir)

    # Verify config entry removed
    from skill_manager.config import load_project_config

    proj = load_project_config(project / ".skill-manager.json")
    assert len(proj.skills) == 0
    # Verify symlink removed
    assert not (skills_dir / "read").exists()


def test_run_disable_skips_external_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_disable leaves an external (non-matching) symlink untouched."""
    project = tmp_path / "proj"
    project.mkdir()
    cache = tmp_path / "repos"
    gconfig = tmp_path / "config.json"
    skills_dir = project / ".agents" / "skills"
    skills_dir.mkdir(parents=True)

    # Create an external symlink that doesn't match any declared target
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "SKILL.md").write_text("# other\n", encoding="utf-8")
    (skills_dir / "read").symlink_to(elsewhere)

    # Config declares a skill but the symlink points elsewhere
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])

    monkeypatch.setattr("builtins.input", lambda prompt="": "1")

    from skill_manager.cli import run_disable

    run_disable(project / ".skill-manager.json", gconfig, cache, skills_dir)

    # Symlink still exists because it pointed elsewhere
    assert (skills_dir / "read").is_symlink()
