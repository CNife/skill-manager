"""Issue #46: enable/sync network-behavior split + real update feedback.

Invariant under test: only ``sync`` and ``source update`` may update already
cached content. ``enable`` only declares + clones when missing + links.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from helpers import skill_md
from typer.testing import CliRunner

from skill_manager import paths
from skill_manager.cli import app, run_enable
from skill_manager.config import GlobalConfig, load_skill_declarations, save_global_config
from skill_manager.sources import clone_source

runner = CliRunner()


def _write_config(project: Path, skills: list[dict]) -> None:
    (project / ".skill-manager.json").write_text(json.dumps({"skills": skills}), encoding="utf-8")


def _parse_json(result) -> dict:
    assert result.stdout.strip(), f"empty stdout; stderr={result.stderr!r} output={result.output!r}"
    return json.loads(result.stdout)


def _seed_cached(
    tmp_path: Path,
    make_source_repo,
    skills: dict[str, str] | None = None,
    *,
    repo: str = "tw93/Waza",
    source_name: str = "waza",
) -> tuple[Path, Path, str]:
    """Clone a file:// source into the isolated XDG cache; return (upstream, cache_repo, head)."""
    skills = skills or {"skills/read": skill_md("read")}
    upstream = make_source_repo(source_name, skills)
    cfg = GlobalConfig()
    head = clone_source(repo, cfg, paths.repos_cache_dir(), url=f"file://{upstream}")
    save_global_config(paths.config_file(), cfg)
    return upstream, paths.repos_cache_dir() / repo, head


def _break_remote(cache_repo: Path) -> None:
    """Point origin at a nonexistent path so any pull fails (offline stand-in)."""
    subprocess.run(
        ["git", "remote", "set-url", "origin", "file:///nonexistent/offline"],
        cwd=cache_repo,
        check=True,
        capture_output=True,
    )


def _advance_upstream(upstream: Path, git, marker: str = "advance") -> str:
    (upstream / "note.txt").write_text(marker, encoding="utf-8")
    git(["add", "."], upstream)
    git(["commit", "-m", marker], upstream)
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=upstream, check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


def _cached_head(owner_repo: str) -> str:
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=paths.repos_cache_dir() / owner_repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


# ── offline behavior: enable works from cache, sync must reach the remote ────


def test_enable_offline_with_cache_succeeds(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cached source + unreachable remote: enable still succeeds (no pull)."""
    _upstream, cache_repo, _head = _seed_cached(tmp_path, make_source_repo)
    _break_remote(cache_repo)
    project = tmp_path / "proj"
    project.mkdir()
    _write_config(project, [])
    monkeypatch.chdir(project)

    result = runner.invoke(app, ["enable", "tw93/Waza", "read"])
    assert result.exit_code == 0, result.output
    proj = load_skill_declarations(project / ".skill-manager.json")
    assert [s.name for s in proj.skills] == ["read"]
    assert (project / ".agents" / "skills" / "read").is_symlink()


def test_sync_offline_raises(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cached source + unreachable remote: sync fails loudly (sync's job is pulling)."""
    _upstream, cache_repo, _head = _seed_cached(tmp_path, make_source_repo)
    _break_remote(cache_repo)
    project = tmp_path / "proj"
    project.mkdir()
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    monkeypatch.chdir(project)

    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 1
    assert "failed" in result.output


# ── enable clones missing sources, never pulls ───────────────────────────────


def test_enable_uncached_clones_and_registers(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """enable on an uncached repo clones it and registers it in the global config."""
    upstream = make_source_repo("waza", {"skills/read": skill_md("read")})
    project = tmp_path / "proj"
    project.mkdir()
    _write_config(project, [])
    monkeypatch.chdir(project)

    result = run_enable(
        project / ".skill-manager.json",
        paths.config_file(),
        paths.repos_cache_dir(),
        project / ".agents" / "skills",
        repo="tw93/Waza",
        names=["read"],
        url_resolver=lambda _r: f"file://{upstream}",
    )
    assert result.outcomes[0].action == "enabled"
    assert (paths.repos_cache_dir() / "tw93" / "Waza" / ".git").is_dir()
    gcfg = json.loads(paths.config_file().read_text())
    assert "tw93/Waza" in gcfg["sources"]
    assert (project / ".agents" / "skills" / "read").is_symlink()
    assert result.sync is not None
    assert result.sync.sources[0].action == "cloned"


def test_enable_uncached_repo_visible_in_source_list(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After auto-cloning, ``source list`` shows the registered repo."""
    upstream = make_source_repo("waza", {"skills/read": skill_md("read")})
    project = tmp_path / "proj"
    project.mkdir()
    _write_config(project, [])
    monkeypatch.chdir(project)
    monkeypatch.setattr("skill_manager.sources.repo_url", lambda r: f"file://{upstream}")

    result = runner.invoke(app, ["enable", "tw93/Waza", "read"])
    assert result.exit_code == 0, result.output
    listed = runner.invoke(app, ["source", "list"])
    assert listed.exit_code == 0, listed.output
    assert "tw93/Waza" in listed.output


def test_enable_does_not_pull_cached_repo(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch, git
) -> None:
    """enable leaves a cached repo's HEAD untouched even when upstream moved."""
    upstream, _cache_repo, head = _seed_cached(tmp_path, make_source_repo)
    _advance_upstream(upstream, git)
    project = tmp_path / "proj"
    project.mkdir()
    _write_config(project, [])
    monkeypatch.chdir(project)

    emit_lines: list[str] = []
    result = run_enable(
        project / ".skill-manager.json",
        paths.config_file(),
        paths.repos_cache_dir(),
        project / ".agents" / "skills",
        repo="tw93/Waza",
        names=["read"],
        emit=emit_lines.append,
    )
    assert result.outcomes[0].action == "enabled"
    assert _cached_head("tw93/Waza") == head
    assert not any("pulling" in ln or "pulled" in ln for ln in emit_lines)


def test_enable_does_not_update_other_declared_source(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch, git
) -> None:
    """enable only touches the target repo; other declared sources keep their HEAD."""
    upstream_a = make_source_repo("alpha", {"skills/a": skill_md("a")})
    upstream_b = make_source_repo("beta", {"skills/b": skill_md("b")})
    cfg = GlobalConfig()
    head_a = clone_source("al/Alpha", cfg, paths.repos_cache_dir(), url=f"file://{upstream_a}")
    head_b = clone_source("be/Beta", cfg, paths.repos_cache_dir(), url=f"file://{upstream_b}")
    save_global_config(paths.config_file(), cfg)
    project = tmp_path / "proj"
    project.mkdir()
    _write_config(project, [{"name": "a", "repo": "al/Alpha", "path": "skills/a"}])
    _advance_upstream(upstream_a, git)  # a pull would move Alpha's cached HEAD
    monkeypatch.chdir(project)

    result = run_enable(
        project / ".skill-manager.json",
        paths.config_file(),
        paths.repos_cache_dir(),
        project / ".agents" / "skills",
        repo="be/Beta",
        names=["b"],
    )
    assert result.outcomes[0].action == "enabled"
    assert _cached_head("al/Alpha") == head_a
    assert _cached_head("be/Beta") == head_b


# ── source update: honest up-to-date vs old→new feedback ─────────────────────


def test_source_update_noop_reports_up_to_date(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty pull reports up-to-date (text and JSON)."""
    _upstream, _cache_repo, head = _seed_cached(tmp_path, make_source_repo)
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)

    result = runner.invoke(app, ["source", "update"])
    assert result.exit_code == 0, result.output
    assert f"up-to-date tw93/Waza ({head[:8]})" in result.output

    body = _parse_json(runner.invoke(app, ["--json", "source", "update"]))
    assert body["data"]["updates"] == [
        {"action": "up_to_date", "repo": "tw93/Waza", "commit": head}
    ]


def test_source_update_pulled_reports_old_new(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch, git
) -> None:
    """A real pull reports old → new (text and JSON with old_commit/new_commit)."""
    upstream, _cache_repo, old = _seed_cached(tmp_path, make_source_repo)
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)

    new = _advance_upstream(upstream, git)
    body = _parse_json(runner.invoke(app, ["--json", "source", "update"]))
    assert body["data"]["updates"] == [
        {
            "action": "updated",
            "repo": "tw93/Waza",
            "commit": new,
            "old_commit": old,
            "new_commit": new,
        }
    ]

    new2 = _advance_upstream(upstream, git, marker="advance2")
    result = runner.invoke(app, ["source", "update"])
    assert result.exit_code == 0, result.output
    assert f"updated tw93/Waza ({new[:8]} → {new2[:8]})" in result.output


# ── sync: start + result lines per pull, JSON carries the same distinction ────


def test_sync_pull_start_and_result_lines(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch, git
) -> None:
    """sync prints a pulling start line before each pull and a result line after."""
    upstream, _cache_repo, head = _seed_cached(tmp_path, make_source_repo)
    project = tmp_path / "proj"
    project.mkdir()
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    monkeypatch.chdir(project)

    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0, result.output
    assert "pulling tw93/Waza..." in result.output
    assert f"up-to-date tw93/Waza ({head[:8]})" in result.output

    new = _advance_upstream(upstream, git)
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0, result.output
    assert "pulling tw93/Waza..." in result.output
    assert f"pulled tw93/Waza ({head[:8]} → {new[:8]})" in result.output


def test_sync_json_reports_action_and_commits(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch, git
) -> None:
    """sync JSON sources entries distinguish up_to_date vs updated with old/new commits."""
    upstream, _cache_repo, head = _seed_cached(tmp_path, make_source_repo)
    project = tmp_path / "proj"
    project.mkdir()
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    monkeypatch.chdir(project)

    body = _parse_json(runner.invoke(app, ["--json", "sync"]))
    assert body["data"]["sources"] == [
        {"repo": "tw93/Waza", "commit": head, "action": "up_to_date"}
    ]

    new = _advance_upstream(upstream, git)
    body = _parse_json(runner.invoke(app, ["--json", "sync"]))
    assert body["data"]["sources"] == [
        {
            "repo": "tw93/Waza",
            "commit": new,
            "action": "updated",
            "old_commit": head,
            "new_commit": new,
        }
    ]
