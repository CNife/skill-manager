import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from skill_manager.cli import app, run_list, run_sync
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
