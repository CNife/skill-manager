"""CLI process-boundary tests for source scan filtering and --all."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from skill_manager.cli import app
from skill_manager.config import load_skill_declarations

runner = CliRunner()

ACTIVE = "---\nname: active\ndescription: active skill\n---\n# active\n"
INTERNAL = "---\nname: secret\nmetadata:\n  internal: true\n---\n# secret\n"
INTERNAL_STRING = '---\nname: maybe\nmetadata:\n  internal: "true"\n---\n# maybe\n'
INTERNAL_YES = "---\nname: maybe\nmetadata:\n  internal: yes\n---\n# maybe\n"
NO_FM = "# plain skill without frontmatter\n"
BAIT = "# nested bait should never appear\n"
ARCHIVED = "# archived skill\n"
NOISE = "# noise under skip dir\n"
ROOT_SKILL = "---\nname: root-skill\ndescription: repo root\n---\n# root\n"

FILTERED_LAYOUT = {
    "skills/active": ACTIVE,
    "skills/secret": INTERNAL,
    ".archive/old": ARCHIVED,
    "node_modules/pkg/fake": NOISE,
    "dist/built": NOISE,
    "build/out": NOISE,
    "__pycache__/cached": NOISE,
    "skills/active/scripts/bait": BAIT,
    "category/nested": ACTIVE,
}


def _write_config(project: Path, skills: list[dict]) -> None:
    (project / ".skill-manager.json").write_text(json.dumps({"skills": skills}), encoding="utf-8")


def _parse_json(result) -> dict:
    assert result.stdout.strip(), f"empty stdout; stderr={result.stderr!r} output={result.output!r}"
    return json.loads(result.stdout)


def _seed_cached_source(
    tmp_path: Path,
    make_source_repo,
    skills: dict[str, str] | None = None,
    *,
    repo_name: str = "waza",
    owner_repo: str = "tw93/Waza",
) -> tuple[Path, str]:
    """Clone a file:// source into the isolated XDG cache; return (project, full_commit)."""
    from skill_manager import paths
    from skill_manager.config import GlobalConfig, save_global_config
    from skill_manager.sources import ensure_source

    skills = skills or {"skills/read": "# read\n"}
    upstream = make_source_repo(repo_name, skills)
    url = f"file://{upstream}"
    cfg = GlobalConfig()
    head = ensure_source(owner_repo, cfg, paths.repos_cache_dir(), url=url)
    save_global_config(paths.config_file(), cfg)
    project = tmp_path / "proj"
    project.mkdir(exist_ok=True)
    return project, head


def _skill_keys(skills: list[dict]) -> set[tuple[str, str]]:
    return {(s["name"], s["path"]) for s in skills}


# ── available-skills default filter ───────────────────────────────────────────


def test_json_available_skills_default_filters_noise(tmp_path: Path, make_source_repo) -> None:
    _seed_cached_source(tmp_path, make_source_repo, FILTERED_LAYOUT)
    result = runner.invoke(app, ["--json", "source", "available-skills"])
    assert result.exit_code == 0, result.output
    body = _parse_json(result)
    assert body["ok"] is True
    keys = _skill_keys(body["data"]["skills"])
    assert ("active", "skills/active") in keys
    assert ("nested", "category/nested") in keys
    assert ("secret", "skills/secret") not in keys
    assert ("old", ".archive/old") not in keys
    assert ("fake", "node_modules/pkg/fake") not in keys
    assert ("built", "dist/built") not in keys
    assert ("out", "build/out") not in keys
    assert ("cached", "__pycache__/cached") not in keys
    assert ("bait", "skills/active/scripts/bait") not in keys


def test_json_available_skills_all_includes_filtered(tmp_path: Path, make_source_repo) -> None:
    _seed_cached_source(tmp_path, make_source_repo, FILTERED_LAYOUT)
    result = runner.invoke(app, ["--json", "source", "available-skills", "--all"])
    assert result.exit_code == 0, result.output
    body = _parse_json(result)
    keys = _skill_keys(body["data"]["skills"])
    assert ("active", "skills/active") in keys
    assert ("secret", "skills/secret") in keys
    assert ("old", ".archive/old") in keys
    assert ("fake", "node_modules/pkg/fake") in keys
    assert ("built", "dist/built") in keys
    assert ("out", "build/out") in keys
    assert ("cached", "__pycache__/cached") in keys
    # skill-root truncation still applies under --all
    assert ("bait", "skills/active/scripts/bait") not in keys


def test_available_skills_skill_root_truncation_default_and_all(
    tmp_path: Path, make_source_repo
) -> None:
    layout = {
        "skills/read": "# read\n",
        "skills/read/references/nested": BAIT,
    }
    _seed_cached_source(tmp_path, make_source_repo, layout)
    for args in (
        ["--json", "source", "available-skills"],
        ["--json", "source", "available-skills", "--all"],
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output
        keys = _skill_keys(_parse_json(result)["data"]["skills"])
        assert keys == {("read", "skills/read")}


def test_json_available_skills_root_skill_layout(tmp_path: Path, make_source_repo) -> None:
    _seed_cached_source(tmp_path, make_source_repo, {".": ROOT_SKILL})
    result = runner.invoke(app, ["--json", "source", "available-skills", "tw93/Waza"])
    assert result.exit_code == 0, result.output
    body = _parse_json(result)
    assert body["data"]["skills"] == [{"name": "waza", "repo": "tw93/Waza", "path": "."}]


def test_json_available_skills_default_empty_all_nonempty(tmp_path: Path, make_source_repo) -> None:
    """Only filtered skills present: default success empty; --all success non-empty."""
    _seed_cached_source(
        tmp_path,
        make_source_repo,
        {".archive/old": ARCHIVED, "skills/secret": INTERNAL},
    )
    default = runner.invoke(app, ["--json", "source", "available-skills"])
    assert default.exit_code == 0, default.output
    assert _parse_json(default)["data"]["skills"] == []

    full = runner.invoke(app, ["--json", "source", "available-skills", "--all"])
    assert full.exit_code == 0, full.output
    keys = _skill_keys(_parse_json(full)["data"]["skills"])
    assert ("old", ".archive/old") in keys
    assert ("secret", "skills/secret") in keys


def test_json_available_skills_not_cached_unchanged(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--json", "source", "available-skills", "no/such"])
    assert result.exit_code == 1
    body = _parse_json(result)
    assert body["error"]["code"] == "not_found"
    assert "--all" not in body["error"]["message"]


def test_json_available_skills_internal_non_bool_still_listed(
    tmp_path: Path, make_source_repo
) -> None:
    _seed_cached_source(
        tmp_path,
        make_source_repo,
        {
            "skills/stringy": INTERNAL_STRING,
            "skills/yesish": INTERNAL_YES,
            "skills/plain": NO_FM,
            "skills/broken": "---\nmetadata: [not, a, mapping\n---\n# broken\n",
        },
    )
    result = runner.invoke(app, ["--json", "source", "available-skills"])
    assert result.exit_code == 0, result.output
    keys = _skill_keys(_parse_json(result)["data"]["skills"])
    assert ("stringy", "skills/stringy") in keys
    assert ("yesish", "skills/yesish") in keys
    assert ("plain", "skills/plain") in keys
    assert ("broken", "skills/broken") in keys


def test_available_skills_human_default_omits_archive(tmp_path: Path, make_source_repo) -> None:
    _seed_cached_source(
        tmp_path,
        make_source_repo,
        {"skills/active": ACTIVE, ".archive/old": ARCHIVED},
    )
    result = runner.invoke(app, ["source", "available-skills"])
    assert result.exit_code == 0
    assert "active" in result.stdout
    assert "old" not in result.stdout


def test_available_skills_human_all_includes_archive(tmp_path: Path, make_source_repo) -> None:
    _seed_cached_source(
        tmp_path,
        make_source_repo,
        {"skills/active": ACTIVE, ".archive/old": ARCHIVED},
    )
    result = runner.invoke(app, ["source", "available-skills", "--all"])
    assert result.exit_code == 0
    assert "active" in result.stdout
    assert "old" in result.stdout


# ── enable resolution ─────────────────────────────────────────────────────────


def test_json_enable_filtered_name_not_found_hints_all(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _ = _seed_cached_source(
        tmp_path,
        make_source_repo,
        {"skills/active": ACTIVE, ".archive/old": ARCHIVED, "skills/secret": INTERNAL},
    )
    _write_config(project, [])
    monkeypatch.chdir(project)

    for name in ("old", "secret"):
        result = runner.invoke(app, ["--json", "enable", "tw93/Waza", name])
        assert result.exit_code == 1, result.output
        body = _parse_json(result)
        assert body["error"]["code"] == "not_found"
        assert "--all" in body["error"]["message"]


def test_enable_text_filtered_name_hints_all(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _ = _seed_cached_source(tmp_path, make_source_repo, {".archive/old": ARCHIVED})
    _write_config(project, [])
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["enable", "tw93/Waza", "old"])
    assert result.exit_code == 1
    assert "--all" in result.output


def test_json_enable_all_resolves_filtered_skill(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, head = _seed_cached_source(
        tmp_path, make_source_repo, {".archive/old": ARCHIVED, "skills/active": ACTIVE}
    )
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

    result = runner.invoke(app, ["--json", "enable", "--all", "tw93/Waza", "old"])
    assert result.exit_code == 0, result.output
    body = _parse_json(result)
    assert body["ok"] is True
    assert body["data"]["results"][0]["action"] == "enabled"
    assert body["data"]["results"][0]["skill"] == {
        "name": "old",
        "repo": "tw93/Waza",
        "path": ".archive/old",
    }
    assert body["data"]["sync"]["sources"] == [{"repo": "tw93/Waza", "commit": head}]
    proj = load_skill_declarations(project / ".skill-manager.json")
    assert len(proj.skills) == 1
    assert proj.skills[0].path == ".archive/old"
    assert (project / ".agents" / "skills" / "old").is_symlink()


def test_json_enable_all_resolves_internal(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _ = _seed_cached_source(tmp_path, make_source_repo, {"skills/secret": INTERNAL})
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

    result = runner.invoke(app, ["--json", "enable", "--all", "tw93/Waza", "secret"])
    assert result.exit_code == 0, result.output
    body = _parse_json(result)
    assert body["data"]["results"][0]["skill"]["path"] == "skills/secret"


def test_json_enable_missing_name_still_hints_when_filtered(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any skill-name miss under default filter gets the --all hint."""
    project, _ = _seed_cached_source(tmp_path, make_source_repo, {"skills/active": ACTIVE})
    _write_config(project, [])
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["--json", "enable", "tw93/Waza", "missing"])
    assert result.exit_code == 1
    body = _parse_json(result)
    assert body["error"]["code"] == "not_found"
    assert "--all" in body["error"]["message"]


def test_json_enable_all_missing_name_no_hint(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _ = _seed_cached_source(tmp_path, make_source_repo, {"skills/active": ACTIVE})
    _write_config(project, [])
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["--json", "enable", "--all", "tw93/Waza", "missing"])
    assert result.exit_code == 1
    body = _parse_json(result)
    assert body["error"]["code"] == "not_found"
    assert "--all" not in body["error"]["message"]


def test_json_enable_repo_not_cached_no_all_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path, [])
    result = runner.invoke(app, ["--json", "enable", "no/such", "read"])
    assert result.exit_code == 1
    body = _parse_json(result)
    assert body["error"]["code"] == "not_found"
    assert "--all" not in body["error"]["message"]


def test_json_enable_without_args_still_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path, [])
    result = runner.invoke(app, ["--json", "enable", "--all"])
    assert result.exit_code == 2
    body = _parse_json(result)
    assert body["error"]["code"] == "usage_error"


def test_enable_interactive_all_menu_includes_archive(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _ = _seed_cached_source(
        tmp_path, make_source_repo, {".archive/old": ARCHIVED, "skills/active": ACTIVE}
    )
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

    # Menu order is sorted by path via walk; select repo 1, then the archived skill.
    # Discover labels from a dry scan via available-skills --all.
    listed = runner.invoke(app, ["--json", "source", "available-skills", "--all"])
    skills = _parse_json(listed)["data"]["skills"]
    labels = [f"{s['name']}  ({s['path']})" for s in skills]
    old_idx = next(i for i, lab in enumerate(labels, 1) if "old" in lab)

    answers = iter(["1", str(old_idx)])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    result = runner.invoke(app, ["enable", "--all"])
    assert result.exit_code == 0, result.output
    proj = load_skill_declarations(project / ".skill-manager.json")
    assert any(s.name == "old" and s.path == ".archive/old" for s in proj.skills)


# ── declared path in filtered zone still works ────────────────────────────────


def test_sync_and_list_honor_declared_filtered_path(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _ = _seed_cached_source(
        tmp_path, make_source_repo, {".archive/old": ARCHIVED, "skills/active": ACTIVE}
    )
    _write_config(
        project,
        [{"name": "old", "repo": "tw93/Waza", "path": ".archive/old"}],
    )
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

    sync_result = runner.invoke(app, ["--json", "sync"])
    assert sync_result.exit_code == 0, sync_result.output
    assert (project / ".agents" / "skills" / "old").is_symlink()

    list_result = runner.invoke(app, ["--json", "list"])
    assert list_result.exit_code == 0, list_result.output
    body = _parse_json(list_result)
    assert body["data"]["skills"] == [
        {
            "name": "old",
            "repo": "tw93/Waza",
            "path": ".archive/old",
            "link": "linked",
        }
    ]


def test_enable_already_enabled_filtered_path_idempotent(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _ = _seed_cached_source(tmp_path, make_source_repo, {".archive/old": ARCHIVED})
    _write_config(
        project,
        [{"name": "old", "repo": "tw93/Waza", "path": ".archive/old"}],
    )
    monkeypatch.chdir(project)
    before = (project / ".skill-manager.json").read_text()
    # Without --all, name is not in default scan — but already_enabled short-circuits.
    result = runner.invoke(app, ["--json", "enable", "tw93/Waza", "old"])
    assert result.exit_code == 0, result.output
    body = _parse_json(result)
    assert body["data"]["results"][0]["action"] == "already_enabled"
    assert (project / ".skill-manager.json").read_text() == before


# ── regression: simple happy path still green ─────────────────────────────────


def test_json_available_skills_simple_layout_unchanged(tmp_path: Path, make_source_repo) -> None:
    _seed_cached_source(
        tmp_path,
        make_source_repo,
        {"skills/read": "# r\n", "skills/write": "# w\n"},
    )
    result = runner.invoke(app, ["--json", "source", "available-skills"])
    assert result.exit_code == 0, result.output
    keys = _skill_keys(_parse_json(result)["data"]["skills"])
    assert keys == {("read", "skills/read"), ("write", "skills/write")}
