"""Empty-state and actionable-error-hint acceptance tests (issue #48).

Covers the decided model: a *missing* declaration file is an empty declaration
for list/sync (enable already bootstraps), ``source list`` has an empty state,
and every user-facing error carries an actionable next step embedded in the
message text (the JSON error envelope stays ``{code, message}`` — no hint field).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from helpers import skill_md
from typer.testing import CliRunner

from skill_manager import paths
from skill_manager.cli import NotFoundError, app, run_enable
from skill_manager.config import GlobalConfig, save_global_config
from skill_manager.picker import SkillChoice, SourceChoice
from skill_manager.sources import clone_source

runner = CliRunner()


def _write_config(project: Path, skills: list[dict]) -> None:
    (project / ".skill-manager.json").write_text(json.dumps({"skills": skills}), encoding="utf-8")


def _parse_json(result) -> dict:
    assert result.stdout.strip(), f"empty stdout; stderr={result.stderr!r}"
    return json.loads(result.stdout)


def _seed_cached_source(tmp_path: Path, make_source_repo, skills: dict[str, str] | None = None):
    """Clone a file:// source into the isolated XDG cache; return the project dir."""
    skills = skills or {"skills/read": skill_md("read", "Read things")}
    upstream = make_source_repo("waza", skills)
    cfg = GlobalConfig()
    clone_source("tw93/Waza", cfg, paths.repos_cache_dir(), url=f"file://{upstream}")
    save_global_config(paths.config_file(), cfg)
    project = tmp_path / "proj"
    project.mkdir()
    return project


class FakePicker:
    """Scripted picker: enough of the Picker protocol for enable interactive tests."""

    def __init__(self, *, source: str, enable_names: list[str] | None = None) -> None:
        self.source = source
        self.enable_names = enable_names or []

    def select_source(self, choices: list[SourceChoice]) -> str:
        return self.source

    def select_skills_to_enable(self, choices: list[SkillChoice]) -> list[str]:
        return self.enable_names

    def select_skills_to_disable(self, names: list[str]) -> list[str]:
        return names


# ── empty states: missing declaration = empty config (list / sync) ────────────


def test_list_missing_config_empty_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """First-ever ``list`` on a fresh checkout must not error (exit 0 + guidance)."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0, result.output
    assert "No skills enabled yet" in result.stdout
    assert "skill-manager enable" in result.stdout
    assert "skill-manager source available-skills" in result.stdout
    assert "Error" not in result.stdout


def test_list_global_missing_config_empty_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--global list`` with no global declaration file: empty state, no error."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["--global", "list"])
    assert result.exit_code == 0, result.output
    assert "No skills enabled yet" in result.stdout
    assert "project config" not in result.output


def test_list_json_missing_config_empty_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["--json", "list"])
    assert result.exit_code == 0, result.output
    assert _parse_json(result) == {"ok": True, "data": {"skills": []}}


def test_sync_missing_config_nothing_to_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fresh-env sync reports nothing to sync and exits 0 (no error)."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0, result.output
    assert "Nothing to sync." in result.stdout


def test_sync_global_missing_config_nothing_to_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["--global", "sync"])
    assert result.exit_code == 0, result.output
    assert "Nothing to sync." in result.stdout
    assert "project config" not in result.output


def test_sync_json_missing_config_empty_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """JSON sync on fresh env: normal empty success result, exit 0."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["--json", "sync"])
    assert result.exit_code == 0, result.output
    assert _parse_json(result) == {"ok": True, "data": {"sources": [], "links": []}}


def test_list_zero_byte_config_still_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """list tolerates only a *missing* file: zero-byte content is still an error."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".skill-manager.json").write_text("", encoding="utf-8")
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 1
    assert "invalid JSON" in result.output


def test_sync_zero_byte_config_still_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".skill-manager.json").write_text("", encoding="utf-8")
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 1
    assert "invalid JSON" in result.output


def test_list_global_bad_json_no_project_config_wording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Global-scope error wording names the global config, never 'project config'."""
    monkeypatch.chdir(tmp_path)
    paths.global_skills_config_path().write_text(json.dumps({"skills": 42}), encoding="utf-8")
    result = runner.invoke(app, ["--global", "list"])
    assert result.exit_code == 1
    assert "global skills config" in result.output
    assert "project config" not in result.output


# ── empty states: missing declaration = empty config (disable) ────────────────


def test_disable_global_missing_config_empty_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--global disable`` with no global declaration file: idempotent no-op."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["--global", "disable", "read"])
    assert result.exit_code == 0, result.output
    assert "not enabled" in result.output
    assert "project config" not in result.output
    jresult = runner.invoke(app, ["--json", "--global", "disable", "read"])
    assert jresult.exit_code == 0, jresult.output
    assert _parse_json(jresult) == {
        "ok": True,
        "data": {"results": [{"action": "not_enabled", "skill": {"name": "read"}}]},
    }


def test_disable_global_bad_json_no_project_config_wording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Global-scope disable error wording names the global config, never 'project config'."""
    monkeypatch.chdir(tmp_path)
    paths.global_skills_config_path().write_text(json.dumps({"skills": 42}), encoding="utf-8")
    result = runner.invoke(app, ["--global", "disable", "read"])
    assert result.exit_code == 1
    assert "global skills config" in result.output
    assert "project config" not in result.output
    jresult = runner.invoke(app, ["--json", "--global", "disable", "read"])
    assert jresult.exit_code == 1
    body = _parse_json(jresult)
    assert body["ok"] is False
    assert body["error"]["code"] == "config_error"
    assert "global skills config" in body["error"]["message"]
    assert "project config" not in body["error"]["message"]


# ── source list empty state ───────────────────────────────────────────────────


def test_source_list_empty_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """source list with no registered sources says so instead of printing nothing."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["source", "list"])
    assert result.exit_code == 0, result.output
    assert "No sources registered (use 'source add' first)" in result.stdout


def test_source_list_json_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["--json", "source", "list"])
    assert result.exit_code == 0, result.output
    assert _parse_json(result) == {"ok": True, "data": {"sources": []}}


# ── every error carries an actionable next step ───────────────────────────────


def test_enable_repo_not_found_has_available_skills_hint(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repo-mode enable not-found names the listing command and keeps the --all hint."""
    project = _seed_cached_source(tmp_path, make_source_repo)
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["enable", "tw93/Waza", "missing-skill"])
    assert result.exit_code == 1
    assert "not found in cached repo 'tw93/Waza'" in result.output
    assert (
        "run 'skill-manager source available-skills tw93/Waza' to list available skills"
        in result.output
    )
    assert "--all" in result.output


def test_available_skills_uncached_repo_hint(tmp_path: Path, monkeypatch) -> None:
    """available-skills on an uncached repo points at source add."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["source", "available-skills", "no/such"])
    assert result.exit_code == 1
    assert "source repo 'no/such' is not cached" in result.output
    assert "use 'source add no/such' first" in result.output


def test_enable_interactive_no_cached_repos_guidance(tmp_path: Path) -> None:
    """Empty-cache guidance points at source add / direct enable — never sync."""
    project = tmp_path / "proj"
    project.mkdir()
    _write_config(project, [])
    with pytest.raises(NotFoundError) as excinfo:
        run_enable(
            project / ".skill-manager.json",
            tmp_path / "cfg.json",
            tmp_path / "empty-cache",
            project / ".agents" / "skills",
            picker=FakePicker(source="x/y"),
        )
    msg = str(excinfo.value)
    assert "No cached repos found" in msg
    assert "source add" in msg
    assert "enable <owner/repo> <name>" in msg
    assert "sync" not in msg


def test_enable_interactive_no_qualified_hint(tmp_path: Path, make_source_repo) -> None:
    """Zero-qualified-skill source names the --all escape hatch."""
    project = tmp_path / "proj"
    project.mkdir()
    _write_config(project, [])
    cache = tmp_path / "repos"
    gconfig = tmp_path / "cfg.json"
    upstream = make_source_repo("waza", {"skills/plain": "# no frontmatter\n"})
    cfg = GlobalConfig()
    clone_source("tw93/Waza", cfg, cache, url=f"file://{upstream}")
    save_global_config(gconfig, cfg)
    with pytest.raises(NotFoundError) as excinfo:
        run_enable(
            project / ".skill-manager.json",
            gconfig,
            cache,
            project / ".agents" / "skills",
            url_resolver=lambda _r: f"file://{upstream}",
            picker=FakePicker(source="tw93/Waza"),
        )
    msg = str(excinfo.value)
    assert "No qualified skills found in tw93/Waza" in msg
    assert "--all" in msg


def test_json_error_envelope_no_hint_field(tmp_path: Path, monkeypatch) -> None:
    """JSON error envelope stays {code, message} — hints live in message text only."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["--json", "source", "available-skills", "no/such"])
    assert result.exit_code == 1
    body = _parse_json(result)
    assert body["ok"] is False
    assert set(body["error"].keys()) == {"code", "message"}
    assert body["error"]["code"] == "not_found"
    assert "use 'source add no/such' first" in body["error"]["message"]


def test_source_remove_warning_visibility_boundary(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Remove warning names the checked scopes and the boundary (other projects)."""
    _seed_cached_source(tmp_path, make_source_repo)
    project = tmp_path / "proj"
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["source", "remove", "tw93/Waza"])
    assert result.exit_code == 0, result.output
    assert "still referenced" in result.output
    assert "project" in result.output
    assert "(other projects not checked)" in result.output
