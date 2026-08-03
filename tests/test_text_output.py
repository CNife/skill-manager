"""Text output layer (issue #49): colors, single status column, relative paths.

Covers the three text-output improvements:
a. human colors gated on TTY / NO_COLOR / --json (JSON never colored),
b. ``list`` single actionable status column (no present/absent),
c. display paths relativized to cwd, else ``~``-abbreviated, else absolute.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from helpers import skill_md
from typer.testing import CliRunner

from skill_manager import cli, paths
from skill_manager.cli import (
    _STATUS_COLORS,
    _color,
    _color_enabled,
    _display_path,
    app,
)

runner = CliRunner()

GREEN = "\x1b[32m"
RED = "\x1b[31m"
YELLOW = "\x1b[33m"
RESET = "\x1b[0m"


class _FakeStream:
    """Minimal stream stand-in whose only job is reporting TTY-ness."""

    def __init__(self, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def _force_color(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate a TTY so ANSI codes are emitted (decision point is patched)."""
    monkeypatch.setattr(cli, "_color_enabled", lambda **kw: True)


def _write_config(project: Path, skills: list[dict]) -> None:
    (project / ".skill-manager.json").write_text(json.dumps({"skills": skills}), encoding="utf-8")


def _seed_cached_source(
    tmp_path: Path, make_source_repo, skills: dict[str, str] | None = None
) -> tuple[Path, str]:
    """Clone a file:// source into the isolated XDG cache; return (project, head)."""
    from skill_manager.config import GlobalConfig, save_global_config
    from skill_manager.sources import clone_source

    skills = skills or {"skills/read": skill_md("read")}
    upstream = make_source_repo("waza", skills)
    url = f"file://{upstream}"
    cfg = GlobalConfig()
    head = clone_source("tw93/Waza", cfg, paths.repos_cache_dir(), url=url)
    save_global_config(paths.config_file(), cfg)
    project = tmp_path / "proj"
    # exist_ok: when XDG_CACHE_HOME lives under the project, cloning may
    # already have created it.
    project.mkdir(exist_ok=True)
    return project, head


def _seed_synced_project(tmp_path: Path, make_source_repo, monkeypatch) -> Path:
    """Seed cache + declaration, chdir into the project, run sync once."""
    project, _head = _seed_cached_source(tmp_path, make_source_repo)
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0, result.output
    return project


# ── a. color rendering (pure) ─────────────────────────────────────────────────


def test_color_status_words_match_palette(monkeypatch: pytest.MonkeyPatch) -> None:
    """linked green, broken red, external/unlinked yellow (picker-family palette)."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    tty = _FakeStream(True)
    assert _color("linked", "green", stream=tty) == f"{GREEN}linked{RESET}"
    assert _color("broken", "red", stream=tty) == f"{RED}broken{RESET}"
    assert _color("external", "yellow", stream=tty) == f"{YELLOW}external{RESET}"
    assert _color("unlinked", "yellow", stream=tty) == f"{YELLOW}unlinked{RESET}"


def test_status_color_map_exact() -> None:
    """The single source of truth maps every status word to its color kind."""
    assert _STATUS_COLORS == {
        "linked": "green",
        "broken": "red",
        "external": "yellow",
        "unlinked": "yellow",
    }


def test_color_error_warning_prefixes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    tty = _FakeStream(True)
    assert _color("Error:", "red", stream=tty) == f"{RED}Error:{RESET}"
    assert _color("Warning:", "yellow", stream=tty) == f"{YELLOW}Warning:{RESET}"


def test_color_unknown_kind_stays_plain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert _color("mystery", "magenta", stream=_FakeStream(True)) == "mystery"


def test_color_non_tty_stream_plain() -> None:
    assert _color("linked", "green", stream=_FakeStream(False)) == "linked"
    assert _color("Error:", "red", stream=_FakeStream(False)) == "Error:"


def test_color_no_color_env_disables_when_nonempty(monkeypatch: pytest.MonkeyPatch) -> None:
    """no-color.org: NO_COLOR present and non-empty kills ANSI."""
    monkeypatch.setenv("NO_COLOR", "1")
    assert _color("linked", "green", stream=_FakeStream(True)) == "linked"
    assert _color("Warning:", "yellow", stream=_FakeStream(True)) == "Warning:"


def test_color_empty_no_color_keeps_tty_color(monkeypatch: pytest.MonkeyPatch) -> None:
    """no-color.org: NO_COLOR='' (present but empty) does NOT disable ANSI."""
    monkeypatch.setenv("NO_COLOR", "")
    assert _color("linked", "green", stream=_FakeStream(True)) == "\x1b[32mlinked\x1b[0m"


def test_color_enabled_honors_stream_and_no_color(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert _color_enabled(stream=_FakeStream(True)) is True
    assert _color_enabled(stream=_FakeStream(False)) is False
    monkeypatch.setenv("NO_COLOR", "1")
    assert _color_enabled(stream=_FakeStream(True)) is False
    monkeypatch.setenv("NO_COLOR", "")
    assert _color_enabled(stream=_FakeStream(True)) is True


# ── c. display path (pure) ────────────────────────────────────────────────────


def test_display_path_relative_to_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    assert _display_path(proj / ".skill-manager.json") == ".skill-manager.json"
    assert _display_path(proj / ".agents" / "skills" / "grilling") == ".agents/skills/grilling"


def test_display_path_home_abbreviated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    home = Path.home()  # isolated by the autouse fixture
    assert _display_path(home / ".skill-manager.json") == "~/.skill-manager.json"
    assert _display_path(home / ".agents" / "skills" / "read") == "~/.agents/skills/read"


def test_display_path_absolute_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    outside = tmp_path / "elsewhere" / "file"
    assert _display_path(outside) == str(outside.resolve())


def test_display_path_symlink_shows_link_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F6: a symlink under the project displays as the link, not its target."""
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    elsewhere = proj / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "SKILL.md").write_text("# other\n", encoding="utf-8")
    link = proj / ".agents" / "skills" / "read"
    link.parent.mkdir(parents=True)
    link.symlink_to(elsewhere)
    assert _display_path(link) == ".agents/skills/read"


def test_disable_skip_external_symlink_shows_link_path(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F6: disable skip message names the link itself, not its resolved target."""
    from skill_manager.cli import run_disable

    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    cache = tmp_path / "repos"
    gconfig = tmp_path / "config.json"
    skills_dir = project / ".agents" / "skills"
    skills_dir.mkdir(parents=True)
    elsewhere = project / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "SKILL.md").write_text("# other\n", encoding="utf-8")
    (skills_dir / "read").symlink_to(elsewhere)
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    messages: list[str] = []
    run_disable(
        project / ".skill-manager.json",
        gconfig,
        cache,
        skills_dir,
        names=["read"],
        emit=messages.append,
    )
    skip = [m for m in messages if m.startswith("Skipped")]
    assert skip, messages
    assert skip[0].startswith("Skipped .agents/skills/read")
    assert not skip[0].startswith("Skipped elsewhere")


# ── b. list single status column ──────────────────────────────────────────────


def test_list_single_status_column_no_present_absent(
    tmp_path: Path, make_source_repo, monkeypatch
) -> None:
    _seed_synced_project(tmp_path, make_source_repo, monkeypatch)
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0, result.output
    assert "present" not in result.output
    assert "absent" not in result.output
    skill_rows = [ln for ln in result.output.splitlines() if ":skills/read" in ln]
    assert skill_rows == ["  read  tw93/Waza:skills/read  linked"]


def test_list_single_status_column_broken(tmp_path: Path, make_source_repo, monkeypatch) -> None:
    project = _seed_synced_project(tmp_path, make_source_repo, monkeypatch)
    del project
    # Remove the cached target: the link dangles → the one status word is broken.
    shutil.rmtree(paths.repos_cache_dir() / "tw93" / "Waza" / "skills" / "read")
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0, result.output
    assert "  read  tw93/Waza:skills/read  broken" in result.output
    assert "present" not in result.output
    assert "absent" not in result.output


# ── a. color wiring end-to-end ────────────────────────────────────────────────


def test_list_captured_output_has_no_ansi(tmp_path: Path, make_source_repo, monkeypatch) -> None:
    """CliRunner captures are non-TTY: nothing is colored (stdout or stderr)."""
    _seed_synced_project(tmp_path, make_source_repo, monkeypatch)
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0, result.output
    assert "\x1b[" not in result.output
    assert "\x1b[" not in result.stderr


def test_list_tty_colors_status_word(tmp_path: Path, make_source_repo, monkeypatch) -> None:
    """On a TTY the status word is colored (linked green here)."""
    _force_color(monkeypatch)
    _seed_synced_project(tmp_path, make_source_repo, monkeypatch)
    result = runner.invoke(app, ["list"], color=True)
    assert result.exit_code == 0, result.output
    assert f"{GREEN}linked{RESET}" in result.output
    assert RED not in result.output
    assert YELLOW not in result.output


def test_sync_progress_lines_stay_colorless(tmp_path: Path, make_source_repo, monkeypatch) -> None:
    """Even on a TTY, pulling/up-to-date/cloned/link progress lines are plain."""
    _force_color(monkeypatch)
    project, _head = _seed_cached_source(tmp_path, make_source_repo)
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["sync"], color=True)
    assert result.exit_code == 0, result.output
    assert "pulling tw93/Waza..." in result.output
    assert "created read -> " in result.output
    assert "\x1b[" not in result.output


def test_error_prefix_red_on_tty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _force_color(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".skill-manager.json").write_text("{bad", encoding="utf-8")
    result = runner.invoke(app, ["sync"], color=True)
    assert result.exit_code == 1
    assert f"{RED}Error:{RESET}" in result.stderr
    assert "invalid JSON" in result.stderr


def test_error_prefix_plain_when_captured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".skill-manager.json").write_text("{bad", encoding="utf-8")
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 1
    assert "Error: invalid JSON" in result.stderr
    assert "\x1b[" not in result.stderr


def test_warning_prefix_yellow_on_tty(tmp_path: Path, make_source_repo, monkeypatch) -> None:
    """source remove warns (yellow prefix) when a declaration still references it."""
    _force_color(monkeypatch)
    project, _head = _seed_cached_source(tmp_path, make_source_repo)
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["source", "remove", "tw93/Waza"], color=True)
    assert result.exit_code == 0, result.output
    assert f"{YELLOW}warning:{RESET}" in result.stderr
    assert "still referenced" in result.stderr


def test_enable_cross_scope_warning_yellow(tmp_path: Path, make_source_repo, monkeypatch) -> None:
    """already_enabled + same name in global scope from another source → yellow Warning."""
    from skill_manager.cli import run_enable

    _force_color(monkeypatch)
    project, _head = _seed_cached_source(tmp_path, make_source_repo)
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    paths.global_skills_config_path().parent.mkdir(parents=True, exist_ok=True)
    paths.global_skills_config_path().write_text(
        json.dumps({"skills": [{"name": "read", "repo": "other/Repo", "path": "skills/read"}]}),
        encoding="utf-8",
    )
    lines: list[str] = []
    result = run_enable(
        project / ".skill-manager.json",
        paths.config_file(),
        paths.repos_cache_dir(),
        project / ".agents" / "skills",
        repo="tw93/Waza",
        names=["read"],
        emit=lines.append,
    )
    assert [o.action for o in result.outcomes] == ["already_enabled"]
    assert any(ln.startswith(f"{YELLOW}Warning:{RESET}") for ln in lines)


def test_no_color_env_output_plain(tmp_path: Path, make_source_repo, monkeypatch) -> None:
    """NO_COLOR set → plain output even where color would otherwise apply."""
    monkeypatch.setenv("NO_COLOR", "1")
    _seed_synced_project(tmp_path, make_source_repo, monkeypatch)
    result = runner.invoke(app, ["list"], color=True)
    assert result.exit_code == 0, result.output
    assert "\x1b[" not in result.output
    assert "\x1b[" not in result.stderr


def test_json_output_never_ansi(tmp_path: Path, make_source_repo, monkeypatch) -> None:
    """--json stays machine-readable: no ANSI even with a forced-color TTY."""
    _force_color(monkeypatch)
    project, _head = _seed_cached_source(tmp_path, make_source_repo)
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    monkeypatch.chdir(project)
    for args in (
        ["--json", "list"],
        ["--json", "sync"],
        ["--json", "enable", "tw93/Waza", "read"],
        ["--json", "disable", "read"],
    ):
        result = runner.invoke(app, args, color=True)
        assert result.exit_code == 0, (args, result.output)
        assert "\x1b[" not in result.output, args
        assert "\x1b[" not in result.stderr, args
        json.loads(result.stdout)  # still valid JSON


def test_json_error_envelope_never_ansi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _force_color(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".skill-manager.json").write_text("{bad", encoding="utf-8")
    result = runner.invoke(app, ["--json", "sync"], color=True)
    assert result.exit_code == 1
    assert "\x1b[" not in result.output
    assert "\x1b[" not in result.stderr
    json.loads(result.stdout)


# ── c. display path end-to-end ────────────────────────────────────────────────


def test_enable_project_scope_relative_paths(tmp_path: Path, make_source_repo, monkeypatch) -> None:
    project, _head = _seed_cached_source(tmp_path, make_source_repo)
    _write_config(project, [])
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["enable", "tw93/Waza", "read"])
    assert result.exit_code == 0, result.output
    assert "Added read (tw93/Waza:skills/read) to .skill-manager.json" in result.output
    assert "created read -> " in result.output
    assert str(project) not in result.output  # no absolute project paths in enable output


def test_disable_project_scope_relative_paths(
    tmp_path: Path, make_source_repo, monkeypatch
) -> None:
    project, _head = _seed_cached_source(tmp_path, make_source_repo)
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    monkeypatch.chdir(project)
    runner.invoke(app, ["sync"])
    result = runner.invoke(app, ["disable", "read"])
    assert result.exit_code == 0, result.output
    assert "Removed read from .skill-manager.json" in result.output
    assert "Removed symlink .agents/skills/read" in result.output
    assert str(project) not in result.output


def test_global_scope_tilde_paths(tmp_path: Path, make_source_repo, monkeypatch) -> None:
    """Global scope renders ~/.skill-manager.json and ~/.agents/skills/... paths."""
    project, _head = _seed_cached_source(tmp_path, make_source_repo)
    _write_config(project, [])
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["--global", "enable", "tw93/Waza", "read"])
    assert result.exit_code == 0, result.output
    assert "Added read (tw93/Waza:skills/read) to ~/.skill-manager.json" in result.output
    result = runner.invoke(app, ["--global", "disable", "read"])
    assert result.exit_code == 0, result.output
    assert "Removed read from ~/.skill-manager.json" in result.output
    assert "Removed symlink ~/.agents/skills/read" in result.output


def test_sync_link_target_relative_when_cache_under_cwd(
    tmp_path: Path, make_source_repo, monkeypatch
) -> None:
    """sync's link line relativizes the target when the cache lives under cwd."""
    project = tmp_path / "proj"
    monkeypatch.setenv("XDG_CACHE_HOME", str(project / ".cache"))
    _seed_cached_source(tmp_path, make_source_repo)  # creates proj/, clones into proj/.cache
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0, result.output
    assert "created read -> .cache/skill-manager/repos/tw93/Waza/skills/read" in result.output
