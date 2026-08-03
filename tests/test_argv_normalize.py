"""Argv normalization for root flags written after the subcommand (issue #45).

``--global`` / ``--json`` are root-only options. SkillManagerGroup.main hoists
them ahead of the subcommand token, so ``sync --global`` and ``list --json``
behave exactly like the root-first forms. Tokens after ``--`` are never
rewritten; other flags (``--all`` etc.) are left in place.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from helpers import skill_md
from typer.testing import CliRunner

from skill_manager import paths
from skill_manager.cli import _normalize_argv, app
from skill_manager.config import load_skill_declarations

runner = CliRunner()


def _write_decls(path: Path, skills: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"skills": skills}), encoding="utf-8")


def _parse_json(result) -> dict:
    assert result.stdout.strip(), f"empty stdout; stderr={result.stderr!r} output={result.output!r}"
    return json.loads(result.stdout)


def _seed_source(tmp_path: Path, make_source_repo, skills: dict[str, str] | None = None) -> str:
    """Clone a file:// source into the isolated XDG cache; return full HEAD commit."""
    from skill_manager.config import GlobalConfig, save_global_config
    from skill_manager.sources import clone_source

    skills = skills or {"skills/read": skill_md("read")}
    upstream = make_source_repo("waza", skills)
    cfg = GlobalConfig()
    head = clone_source("tw93/Waza", cfg, paths.repos_cache_dir(), url=f"file://{upstream}")
    save_global_config(paths.config_file(), cfg)
    return head


# ── sync --global ≡ --global sync ────────────────────────────────────────────


def test_sync_global_after_subcommand_uses_global_scope(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``sync --global`` links into ~/.agents/skills/, never the project."""
    _seed_source(tmp_path, make_source_repo)
    _write_decls(
        paths.global_skills_config_path(),
        [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}],
    )
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = runner.invoke(app, ["sync", "--global"])
    assert result.exit_code == 0, result.output
    link = paths.global_skills_dir() / "read"
    assert link.is_symlink()
    assert (link / "SKILL.md").is_file()
    # Project scope untouched: no project-level link.
    assert not (proj / ".agents" / "skills" / "read").exists()


def test_global_flag_after_subcommand_matches_root_first(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both orderings target the global declaration; JSON output identical."""
    _seed_source(tmp_path, make_source_repo)
    _write_decls(
        paths.global_skills_config_path(),
        [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}],
    )
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)

    root_first = runner.invoke(app, ["--global", "list", "--json"])
    after = runner.invoke(app, ["list", "--json", "--global"])
    assert root_first.exit_code == 0, root_first.output
    assert after.exit_code == 0, after.output
    assert _parse_json(root_first) == _parse_json(after)
    assert _parse_json(after)["ok"] is True


# ── list --json ≡ --json list ────────────────────────────────────────────────


def test_list_json_after_subcommand_matches_root_first(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``list --json`` produces the same JSON structure as ``--json list``."""
    _seed_source(tmp_path, make_source_repo)
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_decls(
        proj / ".skill-manager.json",
        [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}],
    )
    monkeypatch.chdir(proj)

    root_first = runner.invoke(app, ["--json", "list"])
    after = runner.invoke(app, ["list", "--json"])
    assert root_first.exit_code == 0, root_first.output
    assert after.exit_code == 0, after.output
    body = _parse_json(after)
    assert body == _parse_json(root_first)
    assert body["ok"] is True
    assert {"name", "repo", "path", "link"} <= set(body["data"]["skills"][0])


# ── want_json: JSON error envelope with --json after the subcommand ──────────


def test_json_error_envelope_when_json_after_subcommand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``sync --json`` (missing config) emits the JSON error envelope."""
    monkeypatch.chdir(tmp_path)

    root_first = runner.invoke(app, ["--json", "sync"])
    after = runner.invoke(app, ["sync", "--json"])
    assert root_first.exit_code == 1, root_first.output
    assert after.exit_code == 1, after.output
    assert _parse_json(after) == _parse_json(root_first)
    body = _parse_json(after)
    assert body["ok"] is False
    assert body["error"]["code"] == "config_error"
    assert "not found" in body["error"]["message"]


# ── -- separator: tokens after it are never rewritten ────────────────────────


def test_global_token_after_double_dash_not_hoisted(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``sync -- --global`` is a parse error, not a global-scope sync."""
    _seed_source(tmp_path, make_source_repo)
    _write_decls(
        paths.global_skills_config_path(),
        [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}],
    )
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)

    result = runner.invoke(app, ["sync", "--", "--global"])
    assert result.exit_code == 2
    assert "--global" in result.output
    assert not (paths.global_skills_dir() / "read").exists()


def test_json_token_after_double_dash_not_hoisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``sync -- --json`` stays a plain parse error: no JSON envelope."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["sync", "--", "--json"])
    assert result.exit_code == 2
    assert "--json" in result.output
    assert not result.stdout.lstrip().startswith("{")


# ── non-hoistable flags stay put (enable --all regression) ───────────────────


def test_enable_all_not_hoisted(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--all`` is a subcommand flag: it must stay with enable in any position."""
    _seed_source(tmp_path, make_source_repo, {".archive/old": skill_md("old", "archived skill")})
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)

    for args in (
        ["enable", "--all", "tw93/Waza", "old"],
        ["enable", "tw93/Waza", "old", "--all"],
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output
        decl = load_skill_declarations(proj / ".skill-manager.json")
        assert [(s.name, s.path) for s in decl.skills] == [("old", ".archive/old")]
        (proj / ".skill-manager.json").unlink()


# ── _normalize_argv (pure) ───────────────────────────────────────────────────


def test_normalize_argv_hoists_only_root_bool_flags() -> None:
    assert _normalize_argv(["sync", "--global"]) == ["--global", "sync"]
    assert _normalize_argv(["list", "--json"]) == ["--json", "list"]
    assert _normalize_argv(["--global", "sync", "--json"]) == ["--global", "--json", "sync"]
    # Multiple occurrences keep their relative order; each token moves once.
    assert _normalize_argv(["sync", "--json", "--global", "--json"]) == [
        "--json",
        "--global",
        "--json",
        "sync",
    ]
    # Other root flags (--version) stay in place relative to the rest.
    assert _normalize_argv(["--version", "sync", "--json"]) == ["--json", "--version", "sync"]


def test_normalize_argv_leaves_other_flags_and_double_dash_alone() -> None:
    # Subcommand flags are never hoisted.
    assert _normalize_argv(["enable", "--all"]) == ["enable", "--all"]
    assert _normalize_argv(["source", "available-skills", "--all"]) == [
        "source",
        "available-skills",
        "--all",
    ]
    # Everything after -- is verbatim, even --global/--json.
    assert _normalize_argv(["sync", "--", "--global", "--json"]) == [
        "sync",
        "--",
        "--global",
        "--json",
    ]
    assert _normalize_argv(["--global", "--", "--global"]) == ["--global", "--", "--global"]
    assert _normalize_argv(["--", "--json"]) == ["--", "--json"]
    assert _normalize_argv([]) == []
