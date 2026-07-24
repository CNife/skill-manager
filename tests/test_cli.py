import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from skill_manager.cli import app, run_list, run_sync
from skill_manager.config import (
    ConfigError,
    SkillRef,
    load_global_config,
    load_skill_declarations,
    save_global_config,
)
from skill_manager.links import LinkError

runner = CliRunner()


def _write_config(project: Path, skills: list[dict]) -> None:
    (project / ".skill-manager.json").write_text(json.dumps({"skills": skills}), encoding="utf-8")


def test_version_option() -> None:
    from skill_manager import __version__

    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "skill-manager" in result.stdout
    assert __version__ in result.stdout


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


def test_run_list(tmp_path: Path, make_source_repo) -> None:
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
    result = run_list(project / ".skill-manager.json", gconfig, cache, skills_dir)
    assert any(row[0] == "tw93/Waza" for row in result.source_rows)
    assert len(result.skills) == 1
    assert result.skills[0].name == "read"
    assert result.skills[0].link == "linked"


def test_run_list_external_symlink(tmp_path: Path, make_source_repo) -> None:
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
    result = run_list(project / ".skill-manager.json", gconfig, cache, skills_dir)
    assert result.skills[0].link == "external"


def test_run_list_broken(tmp_path: Path, make_source_repo) -> None:
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
    result = run_list(project / ".skill-manager.json", gconfig, cache, skills_dir)
    assert result.skills[0].link == "broken"


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

    proj = load_skill_declarations(project / ".skill-manager.json")
    assert len(proj.skills) == 1
    assert proj.skills[0] == SkillRef("read", "tw93/Waza", "skills/read")
    assert (skills_dir / "read").is_symlink()


def test_run_enable_duplicate(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_enable with an already-enabled skill name is idempotent success."""
    project, cache, gconfig, skills_dir = _enable_test_env(tmp_path, make_source_repo)
    # Keep config as-is so the skill is already enabled

    from skill_manager.cli import run_enable

    monkeypatch.setattr("builtins.input", lambda prompt="": next(iter(["1", "1"])))
    result = run_enable(project / ".skill-manager.json", gconfig, cache, skills_dir)
    assert result.action == "already_enabled"
    assert result.skill["name"] == "read"
    assert result.sync is None


def _seed_cached_source_direct(
    tmp_path: Path, make_source_repo, skills: dict[str, str] | None = None
) -> tuple[Path, Path, Path, Path, Path, str]:
    """Seed global config and cache for enable tests; do not write project config."""
    skills = skills or {"skills/read": "# read\n"}
    upstream = make_source_repo("waza", skills)
    url = f"file://{upstream}"
    project = tmp_path / "proj"
    project.mkdir()
    cache = tmp_path / "repos"
    gconfig = tmp_path / "config.json"
    skills_dir = project / ".agents" / "skills"
    cfg = load_global_config(gconfig)
    from skill_manager.sources import ensure_source

    head = ensure_source("tw93/Waza", cfg, cache, url=url)
    save_global_config(gconfig, cfg)
    return project, cache, gconfig, skills_dir, upstream, head


def test_run_enable_bootstraps_missing_project_config(tmp_path: Path, make_source_repo) -> None:
    """run_enable creates a valid project config when none exists."""
    project, cache, gconfig, skills_dir, _upstream, _head = _seed_cached_source_direct(
        tmp_path, make_source_repo
    )
    from skill_manager.cli import run_enable

    result = run_enable(
        project / ".skill-manager.json",
        gconfig,
        cache,
        skills_dir,
        repo="tw93/Waza",
        name="read",
        url_resolver=lambda _r: f"file://{_upstream}",
    )
    assert result.action == "enabled"
    assert (project / ".skill-manager.json").is_file()
    proj = load_skill_declarations(project / ".skill-manager.json")
    assert proj.skills == [SkillRef("read", "tw93/Waza", "skills/read")]
    assert (skills_dir / "read").is_symlink()


def test_run_enable_bootstraps_empty_project_config(tmp_path: Path, make_source_repo) -> None:
    """run_enable treats a zero-byte project config as an empty skill list."""
    project, cache, gconfig, skills_dir, _upstream, _head = _seed_cached_source_direct(
        tmp_path, make_source_repo
    )
    (project / ".skill-manager.json").write_text("", encoding="utf-8")
    from skill_manager.cli import run_enable

    result = run_enable(
        project / ".skill-manager.json",
        gconfig,
        cache,
        skills_dir,
        repo="tw93/Waza",
        name="read",
        url_resolver=lambda _r: f"file://{_upstream}",
    )
    assert result.action == "enabled"
    proj = load_skill_declarations(project / ".skill-manager.json")
    assert proj.skills == [SkillRef("read", "tw93/Waza", "skills/read")]


def test_run_enable_rejects_invalid_project_config(tmp_path: Path, make_source_repo) -> None:
    """run_enable still surfaces malformed JSON as a config error."""
    project, cache, gconfig, skills_dir, _upstream, _head = _seed_cached_source_direct(
        tmp_path, make_source_repo
    )
    (project / ".skill-manager.json").write_text("{not json", encoding="utf-8")
    from skill_manager.cli import run_enable

    with pytest.raises(ConfigError, match="invalid JSON"):
        run_enable(
            project / ".skill-manager.json",
            gconfig,
            cache,
            skills_dir,
            repo="tw93/Waza",
            name="read",
        )


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
    from skill_manager.config import load_skill_declarations

    proj = load_skill_declarations(project / ".skill-manager.json")
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


# ── source list / add / remove / update ────────────────────────────────────────


def test_source_list_help() -> None:
    """``skill-manager source --help`` shows subcommands."""
    result = runner.invoke(app, ["source", "--help"])
    assert result.exit_code == 0
    for cmd in ("list", "add", "remove", "update", "available-skills"):
        assert cmd in result.stdout


def test_source_list_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """source list with no registered sources outputs nothing."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["source", "list"])
    assert result.exit_code == 0
    assert result.stdout.strip() == ""


def test_source_add_invalid_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """source add with bad repo format exits 1."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["source", "add", "not-owner-repo"])
    assert result.exit_code == 1
    assert "invalid repo" in result.output


def test_source_remove_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """source remove non-existent source exits 1."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["source", "remove", "x/y"])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_source_list_shows_registered(tmp_path: Path) -> None:
    """source list shows registered sources with cached status."""
    from skill_manager import paths
    from skill_manager.config import GlobalConfig, Source, save_global_config

    (paths.repos_cache_dir() / "tw93" / "Waza").mkdir(parents=True)
    save_global_config(
        paths.config_file(),
        GlobalConfig(
            sources={
                "tw93/Waza": Source("tw93/Waza", "abc1234", "https://github.com/tw93/Waza.git"),
            }
        ),
    )
    result = runner.invoke(app, ["source", "list"])
    assert result.exit_code == 0
    assert "tw93/Waza" in result.stdout
    assert "abc1234" in result.stdout
    assert "cached" in result.stdout
    assert "github.com/tw93/Waza" in result.stdout


def test_source_add_duplicate(tmp_path: Path) -> None:
    """source add on already-registered + cached source says already exists."""
    from skill_manager import paths
    from skill_manager.config import GlobalConfig, Source, save_global_config

    (paths.repos_cache_dir() / "tw93" / "Waza").mkdir(parents=True)
    save_global_config(
        paths.config_file(),
        GlobalConfig(
            sources={
                "tw93/Waza": Source("tw93/Waza", "abc1234", "https://github.com/tw93/Waza.git"),
            }
        ),
    )
    result = runner.invoke(app, ["source", "add", "tw93/Waza"])
    assert result.exit_code == 0
    assert "already exists" in result.stdout


def test_source_remove_existing(tmp_path: Path) -> None:
    """source remove deletes cache and config entry."""
    from skill_manager import paths
    from skill_manager.config import GlobalConfig, Source, save_global_config

    (paths.repos_cache_dir() / "tw93" / "Waza").mkdir(parents=True)
    save_global_config(
        paths.config_file(),
        GlobalConfig(
            sources={
                "tw93/Waza": Source("tw93/Waza", "abc1234", "https://github.com/tw93/Waza.git"),
            }
        ),
    )
    result = runner.invoke(app, ["source", "remove", "tw93/Waza"])
    assert result.exit_code == 0
    assert "removed" in result.stdout
    assert not (paths.repos_cache_dir() / "tw93" / "Waza").exists()
    cfg = load_global_config(paths.config_file())
    assert "tw93/Waza" not in cfg.sources


def test_source_remove_warns_on_project_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """source remove warns if project skills still reference the repo."""
    from skill_manager import paths
    from skill_manager.config import GlobalConfig, Source, save_global_config

    (paths.repos_cache_dir() / "tw93" / "Waza").mkdir(parents=True)
    save_global_config(
        paths.config_file(),
        GlobalConfig(
            sources={
                "tw93/Waza": Source("tw93/Waza", "abc1234", "https://github.com/tw93/Waza.git"),
            }
        ),
    )
    monkeypatch.chdir(tmp_path)
    _write_config(Path.cwd(), [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    result = runner.invoke(app, ["source", "remove", "tw93/Waza"])
    assert result.exit_code == 0
    assert "warning" in result.output
    assert "removed" in result.stdout


@pytest.mark.network
def test_source_add_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """source add with a real (local) repo via mocked url."""
    # Integration-level: uses actual git via file:// URL
    import subprocess

    from skill_manager import paths

    repo_dir = tmp_path / "upstream"
    repo_dir.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t"], cwd=repo_dir, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "T"], cwd=repo_dir, check=True, capture_output=True
    )
    (repo_dir / "SKILL.md").write_text("# test\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True, capture_output=True)
    url = f"file://{repo_dir}"

    from skill_manager.config import GlobalConfig, save_global_config
    from skill_manager.sources import ensure_source

    cfg = GlobalConfig()
    head = ensure_source("test/foo", cfg, paths.repos_cache_dir(), url=url)
    save_global_config(paths.config_file(), cfg)

    result = runner.invoke(app, ["source", "list"])
    assert result.exit_code == 0
    assert "test/foo" in result.stdout
    assert "cached" in result.stdout
    assert head[:8] in result.stdout


def test_source_update_help() -> None:
    """``skill-manager source update --help`` shows optional repo arg."""
    result = runner.invoke(app, ["source", "update", "--help"])
    assert result.exit_code == 0
    assert "REPO" in result.stdout  # optional argument shown
