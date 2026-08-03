"""Issue #47: duplicate-name governance (path identity, repo-less enable, scope conflicts).

Coverage map (acceptance criteria):
- path is the identity: ``enable <repo> <path>`` enables the exact path;
  ambiguous names list candidate paths; pure names fall back to a root
  single-segment path.
- repo-less enable (first positional arg has no ``/``): unique name across
  cached sources wins; cross-source collisions list candidate sources; zero
  hits point at ``source available-skills``; arguments must be pure names.
- scope-internal same-name hard ban (different source or different path) with
  a disable-first hint; same repo+path stays an idempotent no-op.
- cross-scope same-name: same source is legal with a neutral hint; different
  sources hard-error in both directions (project enable checks the global
  declaration; ``--global`` enable checks the cwd project).
- list renders benign overlap (⊕) and conflict (⚠) distinctly; existing
  conflicts warn without hard-failing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from helpers import skill_md
from typer import _click as click
from typer.testing import CliRunner

from skill_manager.cli import NotFoundError, app, run_enable, run_list
from skill_manager.config import (
    SkillRef,
    load_global_config,
    load_skill_declarations,
    save_global_config,
)
from skill_manager.sources import clone_source

runner = CliRunner()

DUP_SKILLS = {
    "skills/read": skill_md("read", "first copy"),
    "plugins/waza/skills/read": skill_md("read", "second copy"),
}


def _write_config(project: Path, skills: list[dict]) -> None:
    (project / ".skill-manager.json").write_text(json.dumps({"skills": skills}), encoding="utf-8")


def _seed_source(
    tmp_path: Path,
    make_source_repo,
    repo_id: str,
    skills: dict[str, str],
    *,
    cache_root: Path | None = None,
    gconfig_path: Path | None = None,
) -> None:
    """Clone ``repo_id`` (file:// stand-in) into the cache + global config."""
    upstream = make_source_repo(repo_id.replace("/", "_"), skills)
    cfg = load_global_config(gconfig_path or tmp_path / "config.json")
    clone_source(repo_id, cfg, cache_root or tmp_path / "repos", url=f"file://{upstream}")
    save_global_config(gconfig_path or tmp_path / "config.json", cfg)


def _env(tmp_path: Path, make_source_repo, skills: dict[str, str] | None = None):
    """Seed one cached source; return (project, cache, gconfig, skills_dir)."""
    project = tmp_path / "proj"
    project.mkdir()
    _seed_source(tmp_path, make_source_repo, "tw93/Waza", skills or DUP_SKILLS)
    return project, tmp_path / "repos", tmp_path / "config.json", project / ".agents" / "skills"


def _env_two_sources(tmp_path: Path, make_source_repo):
    """Seed tw93/Waza (read) + other/Repo (write); return the four env paths."""
    project = tmp_path / "proj"
    project.mkdir()
    _seed_source(tmp_path, make_source_repo, "tw93/Waza", {"skills/read": skill_md("read")})
    _seed_source(tmp_path, make_source_repo, "other/Repo", {"skills/write": skill_md("write")})
    return project, tmp_path / "repos", tmp_path / "config.json", project / ".agents" / "skills"


# ── a. path as the identifier (non-interactive enable) ────────────────────────


def test_enable_path_arg_selects_exact_path(tmp_path: Path, make_source_repo) -> None:
    project, cache, gconfig, skills_dir = _env(tmp_path, make_source_repo)
    _write_config(project, [])
    result = run_enable(
        project / ".skill-manager.json",
        gconfig,
        cache,
        skills_dir,
        repo="tw93/Waza",
        names=["plugins/waza/skills/read"],
    )
    assert result.outcomes[0].action == "enabled"
    assert result.outcomes[0].skill == {
        "name": "read",
        "repo": "tw93/Waza",
        "path": "plugins/waza/skills/read",
    }
    proj = load_skill_declarations(project / ".skill-manager.json")
    assert proj.skills == [SkillRef("read", "tw93/Waza", "plugins/waza/skills/read")]


def test_enable_ambiguous_name_lists_candidate_paths(tmp_path: Path, make_source_repo) -> None:
    project, cache, gconfig, skills_dir = _env(tmp_path, make_source_repo)
    _write_config(project, [])
    with pytest.raises(NotFoundError) as exc:
        run_enable(
            project / ".skill-manager.json",
            gconfig,
            cache,
            skills_dir,
            repo="tw93/Waza",
            names=["read"],
        )
    msg = str(exc.value)
    assert "skills/read" in msg
    assert "plugins/waza/skills/read" in msg
    # Atomic: nothing was applied.
    assert load_skill_declarations(project / ".skill-manager.json").skills == []


def test_enable_name_falls_back_to_single_segment_path(tmp_path: Path, make_source_repo) -> None:
    """A pure name with no name match resolves against a root single-segment path."""
    project, cache, gconfig, skills_dir = _env(
        tmp_path, make_source_repo, {"read": skill_md("reader", "at root path read")}
    )
    _write_config(project, [])
    result = run_enable(
        project / ".skill-manager.json",
        gconfig,
        cache,
        skills_dir,
        repo="tw93/Waza",
        names=["read"],
    )
    assert result.outcomes[0].action == "enabled"
    assert result.outcomes[0].skill == {"name": "reader", "repo": "tw93/Waza", "path": "read"}


# ── b. repo-less enable (repo argument omitted) ───────────────────────────────


def test_enable_repo_less_unique_across_sources(tmp_path: Path, make_source_repo) -> None:
    project, cache, gconfig, skills_dir = _env_two_sources(tmp_path, make_source_repo)
    _write_config(project, [])
    result = run_enable(project / ".skill-manager.json", gconfig, cache, skills_dir, repo="write")
    assert [o.action for o in result.outcomes] == ["enabled"]
    assert result.outcomes[0].skill == {
        "name": "write",
        "repo": "other/Repo",
        "path": "skills/write",
    }
    assert (skills_dir / "write").is_symlink()


def test_enable_repo_less_batch_spans_sources(tmp_path: Path, make_source_repo) -> None:
    """Repo-less batch may resolve names from different sources, applied once."""
    project, cache, gconfig, skills_dir = _env_two_sources(tmp_path, make_source_repo)
    _write_config(project, [])
    result = run_enable(
        project / ".skill-manager.json",
        gconfig,
        cache,
        skills_dir,
        repo="read",
        names=["write"],
    )
    assert [o.action for o in result.outcomes] == ["enabled", "enabled"]
    assert [o.skill["repo"] for o in result.outcomes] == ["tw93/Waza", "other/Repo"]
    proj = load_skill_declarations(project / ".skill-manager.json")
    assert {s.name for s in proj.skills} == {"read", "write"}
    assert (skills_dir / "read").is_symlink()
    assert (skills_dir / "write").is_symlink()
    assert result.sync is not None
    assert [s.repo for s in result.sync.sources] == ["tw93/Waza", "other/Repo"]


def test_enable_repo_less_cross_source_collision_lists_sources(
    tmp_path: Path, make_source_repo
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _seed_source(tmp_path, make_source_repo, "tw93/Waza", {"skills/read": skill_md("read")})
    _seed_source(tmp_path, make_source_repo, "other/Repo", {"a/read": skill_md("read")})
    _write_config(project, [])
    with pytest.raises(NotFoundError) as exc:
        run_enable(
            project / ".skill-manager.json",
            tmp_path / "config.json",
            tmp_path / "repos",
            project / ".agents" / "skills",
            repo="read",
        )
    msg = str(exc.value)
    assert "tw93/Waza" in msg
    assert "other/Repo" in msg
    assert load_skill_declarations(project / ".skill-manager.json").skills == []


def test_enable_repo_less_zero_hit_mentions_available_skills(
    tmp_path: Path, make_source_repo
) -> None:
    project, cache, gconfig, skills_dir = _env(tmp_path, make_source_repo)
    _write_config(project, [])
    with pytest.raises(NotFoundError) as exc:
        run_enable(project / ".skill-manager.json", gconfig, cache, skills_dir, repo="nope")
    msg = str(exc.value)
    assert "nope" in msg
    assert "available-skills" in msg


def test_enable_repo_less_rejects_path_args(tmp_path: Path, make_source_repo) -> None:
    project, cache, gconfig, skills_dir = _env_two_sources(tmp_path, make_source_repo)
    _write_config(project, [])
    with pytest.raises(click.exceptions.UsageError):
        run_enable(
            project / ".skill-manager.json",
            gconfig,
            cache,
            skills_dir,
            repo="read",
            names=["other/Repo"],
        )


def test_enable_repo_less_atomic_batch(tmp_path: Path, make_source_repo) -> None:
    """One valid + one unknown name aborts the whole repo-less batch."""
    project, cache, gconfig, skills_dir = _env_two_sources(tmp_path, make_source_repo)
    _write_config(project, [])
    with pytest.raises(NotFoundError):
        run_enable(
            project / ".skill-manager.json",
            gconfig,
            cache,
            skills_dir,
            repo="read",
            names=["nope"],
        )
    assert load_skill_declarations(project / ".skill-manager.json").skills == []


# ── c. scope-internal same-name hard ban ──────────────────────────────────────


def test_enable_same_name_different_source_scope_internal_error(
    tmp_path: Path, make_source_repo
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _seed_source(tmp_path, make_source_repo, "tw93/Waza", {"skills/read": skill_md("read")})
    _seed_source(tmp_path, make_source_repo, "other/Repo", {"skills/read": skill_md("read")})
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    with pytest.raises(NotFoundError) as exc:
        run_enable(
            project / ".skill-manager.json",
            tmp_path / "config.json",
            tmp_path / "repos",
            project / ".agents" / "skills",
            repo="other/Repo",
            names=["read"],
        )
    msg = str(exc.value)
    assert "already enabled" in msg
    assert "tw93/Waza" in msg
    assert "disable" in msg
    assert load_skill_declarations(project / ".skill-manager.json").skills == [
        SkillRef("read", "tw93/Waza", "skills/read")
    ]


def test_enable_same_name_different_path_scope_internal_error(
    tmp_path: Path, make_source_repo
) -> None:
    project, cache, gconfig, skills_dir = _env(tmp_path, make_source_repo)
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    with pytest.raises(NotFoundError) as exc:
        run_enable(
            project / ".skill-manager.json",
            gconfig,
            cache,
            skills_dir,
            repo="tw93/Waza",
            names=["plugins/waza/skills/read"],
        )
    msg = str(exc.value)
    assert "already enabled" in msg
    assert "disable" in msg
    assert load_skill_declarations(project / ".skill-manager.json").skills == [
        SkillRef("read", "tw93/Waza", "skills/read")
    ]


def test_enable_same_repo_same_path_idempotent(tmp_path: Path, make_source_repo) -> None:
    project, cache, gconfig, skills_dir = _env(tmp_path, make_source_repo)
    _write_config(
        project, [{"name": "read", "repo": "tw93/Waza", "path": "plugins/waza/skills/read"}]
    )
    result = run_enable(
        project / ".skill-manager.json",
        gconfig,
        cache,
        skills_dir,
        repo="tw93/Waza",
        names=["plugins/waza/skills/read"],
    )
    assert result.outcomes[0].action == "already_enabled"
    assert result.outcomes[0].skill == {
        "name": "read",
        "repo": "tw93/Waza",
        "path": "plugins/waza/skills/read",
    }
    assert result.sync is None


# ── d. cross-scope same-name ──────────────────────────────────────────────────


def test_enable_cross_scope_same_source_succeeds_neutral_hint(
    tmp_path: Path, make_source_repo
) -> None:
    from skill_manager import paths

    project, cache, gconfig, skills_dir = _env(tmp_path, make_source_repo)
    _write_config(project, [])
    paths.global_skills_config_path().write_text(
        json.dumps(
            {"skills": [{"name": "read", "repo": "tw93/Waza", "path": "plugins/waza/skills/read"}]}
        ),
        encoding="utf-8",
    )
    messages: list[str] = []
    result = run_enable(
        project / ".skill-manager.json",
        gconfig,
        cache,
        skills_dir,
        repo="tw93/Waza",
        names=["plugins/waza/skills/read"],
        emit=messages.append,
    )
    assert result.outcomes[0].action == "enabled"
    assert result.outcomes[0].enabled_globally is True
    assert any("also enabled globally (same source) — no conflict" in m for m in messages)


def test_enable_cross_scope_different_source_errors(tmp_path: Path, make_source_repo) -> None:
    from skill_manager import paths

    project, cache, gconfig, skills_dir = _env(tmp_path, make_source_repo)
    _write_config(project, [])
    paths.global_skills_config_path().write_text(
        json.dumps({"skills": [{"name": "read", "repo": "other/Repo", "path": "skills/read"}]}),
        encoding="utf-8",
    )
    with pytest.raises(NotFoundError) as exc:
        run_enable(
            project / ".skill-manager.json",
            gconfig,
            cache,
            skills_dir,
            repo="tw93/Waza",
            names=["skills/read"],
        )
    msg = str(exc.value)
    assert "already enabled globally" in msg
    assert "other/Repo" in msg
    assert "disable" in msg
    assert load_skill_declarations(project / ".skill-manager.json").skills == []


def test_global_enable_reverse_conflict_with_cwd_project(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    from skill_manager import paths

    project = tmp_path / "proj"
    project.mkdir()
    _seed_source(tmp_path, make_source_repo, "tw93/Waza", {"skills/read": skill_md("read")})
    _write_config(project, [{"name": "read", "repo": "other/Repo", "path": "skills/read"}])
    monkeypatch.chdir(project)
    with pytest.raises(NotFoundError) as exc:
        run_enable(
            paths.global_skills_config_path(),
            tmp_path / "config.json",
            tmp_path / "repos",
            paths.global_skills_dir(),
            repo="tw93/Waza",
            names=["read"],
        )
    msg = str(exc.value)
    assert "project" in msg
    assert "disable" in msg
    # The batch aborted before any write: no global declaration was created.
    assert not paths.global_skills_config_path().is_file()


def test_global_enable_same_source_as_project_ok(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    from skill_manager import paths

    project = tmp_path / "proj"
    project.mkdir()
    _seed_source(tmp_path, make_source_repo, "tw93/Waza", {"skills/read": skill_md("read")})
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    monkeypatch.chdir(project)
    result = run_enable(
        paths.global_skills_config_path(),
        tmp_path / "config.json",
        tmp_path / "repos",
        paths.global_skills_dir(),
        repo="tw93/Waza",
        names=["read"],
    )
    assert result.outcomes[0].action == "enabled"
    assert result.outcomes[0].enabled_globally is None  # global scope omits the field
    decl = load_skill_declarations(paths.global_skills_config_path())
    assert [s.name for s in decl.skills] == ["read"]


# ── e. presentation: list marks / warnings ────────────────────────────────────


def _conflict_env(tmp_path: Path, make_source_repo):
    """Project read@tw93/Waza (global same source) + write@tw93/Waza (global other source)."""
    from skill_manager import paths

    project = tmp_path / "proj"
    project.mkdir()
    _seed_source(
        tmp_path,
        make_source_repo,
        "tw93/Waza",
        {"skills/read": skill_md("read"), "skills/write": skill_md("write")},
    )
    _write_config(
        project,
        [
            {"name": "read", "repo": "tw93/Waza", "path": "skills/read"},
            {"name": "write", "repo": "tw93/Waza", "path": "skills/write"},
        ],
    )
    paths.global_skills_config_path().write_text(
        json.dumps(
            {
                "skills": [
                    {"name": "read", "repo": "tw93/Waza", "path": "skills/read"},  # benign ⊕
                    {"name": "write", "repo": "other/Repo", "path": "x/write"},  # conflict ⚠
                ]
            }
        ),
        encoding="utf-8",
    )
    return project


def test_list_distinguishes_benign_overlap_and_conflict(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _conflict_env(tmp_path, make_source_repo)
    monkeypatch.chdir(project)
    # run_list itself must not hard-fail on existing conflicts (migration-friendly).
    result = run_list(
        project / ".skill-manager.json",
        tmp_path / "config.json",
        tmp_path / "repos",
        project / ".agents" / "skills",
    )
    by_name = {s.name: s for s in result.skills}
    assert by_name["read"].enabled_globally is True
    assert by_name["read"].global_conflict is False
    assert by_name["write"].enabled_globally is True
    assert by_name["write"].global_conflict is True
    assert any(w["code"] == "global_conflict" for w in result.warnings)


def test_list_human_marks_oplus_and_warning(tmp_path: Path, make_source_repo, monkeypatch) -> None:
    project = _conflict_env(tmp_path, make_source_repo)
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0, result.output
    out = result.stdout
    assert "(⊕ = also enabled globally, same source)" in out
    assert "⊕ read" in out
    assert "⚠ write" in out


def test_list_json_conflict_field(tmp_path: Path, make_source_repo, monkeypatch) -> None:
    project = _conflict_env(tmp_path, make_source_repo)
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["--json", "list"])
    assert result.exit_code == 0, result.output
    body = json.loads(result.stdout)
    by_name = {s["name"]: s for s in body["data"]["skills"]}
    assert by_name["read"]["enabled_globally"] is True
    assert "global_conflict" not in by_name["read"]
    assert by_name["write"]["enabled_globally"] is True
    assert by_name["write"]["global_conflict"] is True
    assert any(w["code"] == "global_conflict" for w in body["warnings"])


# ── CLI level: path arg and repo-less forms ───────────────────────────────────


def _seed_cli_source(
    tmp_path: Path, make_source_repo, repo_id: str, skills: dict[str, str]
) -> None:
    from skill_manager import paths

    _seed_source(
        tmp_path,
        make_source_repo,
        repo_id,
        skills,
        cache_root=paths.repos_cache_dir(),
        gconfig_path=paths.config_file(),
    )


def test_cli_enable_path_arg(tmp_path: Path, make_source_repo, monkeypatch) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _seed_cli_source(tmp_path, make_source_repo, "tw93/Waza", DUP_SKILLS)
    _write_config(project, [])
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["enable", "tw93/Waza", "plugins/waza/skills/read"])
    assert result.exit_code == 0, result.output
    proj = load_skill_declarations(project / ".skill-manager.json")
    assert proj.skills == [SkillRef("read", "tw93/Waza", "plugins/waza/skills/read")]
    assert (project / ".agents" / "skills" / "read").is_symlink()


def test_cli_enable_repo_less(tmp_path: Path, make_source_repo, monkeypatch) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _seed_cli_source(tmp_path, make_source_repo, "tw93/Waza", {"skills/read": skill_md("read")})
    _write_config(project, [])
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["enable", "read"])
    assert result.exit_code == 0, result.output
    proj = load_skill_declarations(project / ".skill-manager.json")
    assert proj.skills == [SkillRef("read", "tw93/Waza", "skills/read")]


def test_cli_enable_repo_less_zero_hit(tmp_path: Path, make_source_repo, monkeypatch) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _seed_cli_source(tmp_path, make_source_repo, "tw93/Waza", {"skills/read": skill_md("read")})
    _write_config(project, [])
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["enable", "nope"])
    assert result.exit_code == 1
    assert "available-skills" in result.output
