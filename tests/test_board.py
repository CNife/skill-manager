"""The skill board: one screen that both enables and disables (ADR 0013).

Two layers are covered here:

a. the pure plan — what a set of checked rows commits, and which selections it
   refuses;
b. the commit through ``run_board`` — declarations on disk, symlinks, atomic
   rejection and cancel, all asserted through the files the command writes.

Oracle: ``.skill-manager.json`` and the ``.agents/skills/`` entries are the
contract (ADR 0001 links instead of copies; one declaration per name), so every
expectation is read back from disk rather than from the board's own state.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pytest
from helpers import skill_md
from rich.console import Console
from typer.testing import CliRunner

import skill_manager.cli as cli
from skill_manager.board import Board, BoardError, BoardGroup, BoardRow, counts, plan
from skill_manager.cli import NotFoundError, app, run_board, run_sync
from skill_manager.config import load_global_config, load_skill_declarations, save_global_config
from skill_manager.picker import PickerCancelled, board_row_description, board_row_title
from skill_manager.render import render_board
from skill_manager.results import BoardResult, DisableOutcome, EnableOutcome, SyncResult
from skill_manager.sources import clone_source

runner = CliRunner()


def _write_config(project: Path, skills: list[dict]) -> None:
    (project / ".skill-manager.json").write_text(json.dumps({"skills": skills}), encoding="utf-8")


def _declared(project: Path) -> list[tuple[str, str, str]]:
    path = project / ".skill-manager.json"
    if not path.is_file():
        return []
    return sorted((s.name, s.repo, s.path) for s in load_skill_declarations(path).skills)


def _link_target(skills_dir: Path, name: str) -> Path | None:
    link = skills_dir / name
    return link.resolve() if link.is_symlink() else None


def _env(tmp_path: Path, make_source_repo, skills: dict[str, str] | None = None):
    """A cached ``tw93/Waza`` source plus an empty project."""
    skills = skills or {
        "skills/read": skill_md("read", "Read things"),
        "skills/write": skill_md("write", "Write things"),
        "skills/kami": skill_md("kami", "Kami skill"),
    }
    upstream = make_source_repo("waza", skills)
    project = tmp_path / "proj"
    project.mkdir()
    cache = tmp_path / "repos"
    gconfig = tmp_path / "config.json"
    skills_dir = project / ".agents" / "skills"
    cfg = load_global_config(gconfig)
    clone_source("tw93/Waza", cfg, cache, url=f"file://{upstream}")
    save_global_config(gconfig, cfg)
    return project, cache, gconfig, skills_dir


class ScriptedBoard:
    """Board adapter: returns the keys of rows whose name is in ``wanted``.

    ``wanted=None`` means "leave the board untouched" — every declared row
    checked, nothing else. ``cancel`` raises before returning.
    """

    def __init__(self, wanted: set[str] | None = None, *, cancel: bool = False) -> None:
        self.wanted = wanted
        self.cancel = cancel
        self.board: Board | None = None

    def select(self, board: Board) -> list[str]:
        self.board = board
        if self.cancel:
            raise PickerCancelled
        if self.wanted is None:
            return [row.key for row in board.rows if row.declared]
        return [row.key for row in board.rows if row.name in self.wanted]


class KeysBoard(ScriptedBoard):
    """Board adapter that returns exactly the keys a test names."""

    def __init__(self, keys: list[str]) -> None:
        super().__init__()
        self.keys = keys

    def select(self, board: Board) -> list[str]:
        self.board = board
        return list(self.keys)


# ── pure plan ────────────────────────────────────────────────────────────────


def _board() -> Board:
    return Board(
        scope="project",
        groups=(
            BoardGroup(
                repo="a/one",
                rows=(
                    BoardRow("read", "a/one", "skills/read", declared=True, link="linked"),
                    BoardRow("write", "a/one", "skills/write", declared=True, link="linked"),
                    BoardRow("new", "a/one", "skills/new"),
                ),
            ),
            BoardGroup(
                repo=None,
                rows=(
                    BoardRow(
                        "legacy", "gone/repo", "skills/legacy", declared=True, available=False
                    ),
                ),
            ),
        ),
    )


def test_plan_keeps_checked_declarations_and_adds_new_ones() -> None:
    """Checked is the wanted state: new rows get enabled, dropped ones disabled."""
    result = plan(
        _board(),
        ["a/one:skills/read", "a/one:skills/new", "gone/repo:skills/legacy"],
    )
    assert [(s.name, s.repo, s.path) for s in result.enable] == [("new", "a/one", "skills/new")]
    assert [(s.name, s.repo) for s in result.disable] == [("write", "a/one")]


def test_plan_swaps_a_name_between_sources() -> None:
    """Checking another source's row for a declared name replaces it in one plan."""
    board = Board(
        scope="project",
        groups=(
            BoardGroup("a/one", (BoardRow("read", "a/one", "skills/read", declared=True),)),
            BoardGroup("b/two", (BoardRow("read", "b/two", "skills/read"),)),
        ),
    )
    result = plan(board, ["b/two:skills/read"])
    assert [(s.repo, s.path) for s in result.enable] == [("b/two", "skills/read")]
    assert [(s.repo, s.path) for s in result.disable] == [("a/one", "skills/read")]


def test_plan_rejects_two_checked_rows_with_the_same_name() -> None:
    """One scope holds one declaration per name, so two checked rows cannot commit."""
    board = Board(
        scope="project",
        groups=(
            BoardGroup(
                "a/one",
                (BoardRow("read", "a/one", "x/read"), BoardRow("read", "a/one", "y/read")),
            ),
        ),
    )
    with pytest.raises(BoardError) as excinfo:
        plan(board, ["a/one:x/read", "a/one:y/read"])
    message = str(excinfo.value)
    assert "'read'" in message
    assert "x/read" in message and "y/read" in message


def test_plan_rejects_a_stale_row_key() -> None:
    """A key that is not on the board never becomes a silent no-op."""
    with pytest.raises(BoardError, match="no longer listed"):
        plan(_board(), ["a/one:skills/vanished"])


def test_plan_of_an_unchanged_selection_is_empty() -> None:
    """Opening the board and pressing Enter writes nothing."""
    board = _board()
    assert plan(board, [row.key for row in board.rows if row.declared]).is_empty


def test_counts_report_added_and_removed_rows() -> None:
    """The prompt counter tracks checkboxes, before the plan validates them."""
    board = _board()
    declared = [row.key for row in board.rows if row.declared]
    assert counts(board, [*declared, "a/one:skills/new"]) == (1, 0)
    assert counts(board, declared[:-1]) == (0, 1)
    assert counts(board, declared) == (0, 0)


# ── row model ────────────────────────────────────────────────────────────────


def _capture(tmp_path: Path, make_source_repo) -> Board:
    """Run the board once and capture the rows it showed, writing nothing."""
    project, cache, gconfig, skills_dir = _env(tmp_path, make_source_repo)
    _write_config(
        project,
        [
            {"name": "read", "repo": "tw93/Waza", "path": "skills/read"},
            {"name": "legacy", "repo": "gone/removed", "path": "skills/legacy"},
        ],
    )
    ui = ScriptedBoard(cancel=True)
    with pytest.raises(PickerCancelled):
        run_board(project / ".skill-manager.json", gconfig, cache, skills_dir, ui=ui)
    assert ui.board is not None
    return ui.board


def test_rows_group_by_source_with_link_state_spelled_out(tmp_path: Path, make_source_repo) -> None:
    """Declared rows carry their link state as words; headings are the sources."""
    board = _capture(tmp_path, make_source_repo)
    assert [group.repo for group in board.groups] == ["tw93/Waza", None]
    by_name = {row.name: row for row in board.rows}
    assert by_name["read"].declared
    assert "not linked" in board_row_title(by_name["read"])
    assert not by_name["write"].declared
    assert board_row_title(by_name["write"]) == "write"
    assert by_name["legacy"].available is False
    assert "not in cache" in board_row_description(by_name["legacy"])


def test_rows_mark_global_overlap_and_conflict(tmp_path: Path, make_source_repo) -> None:
    """⊕ is the same source in both scopes; ⚠ is a different source (a conflict)."""
    project, cache, gconfig, skills_dir = _env(tmp_path, make_source_repo)
    (Path.home() / ".skill-manager.json").write_text(
        json.dumps(
            {
                "skills": [
                    {"name": "read", "repo": "tw93/Waza", "path": "skills/read"},
                    {"name": "kami", "repo": "other/repo", "path": "skills/kami"},
                ]
            }
        ),
        encoding="utf-8",
    )
    ui = ScriptedBoard(cancel=True)
    with pytest.raises(PickerCancelled):
        run_board(project / ".skill-manager.json", gconfig, cache, skills_dir, ui=ui)
    rows = {row.name: row for row in ui.board.rows}
    assert rows["read"].enabled_globally and not rows["read"].global_conflict
    assert board_row_title(rows["read"]) == "⊕ read"
    assert rows["kami"].global_conflict
    assert board_row_title(rows["kami"]).startswith("⚠ kami")


def test_rows_mark_project_overlap_on_the_global_board(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⊕/⚠ work on the global board too, pointing at the cwd project."""
    project, cache, gconfig, skills_dir = _env(tmp_path, make_source_repo)
    (Path.home() / ".skill-manager.json").write_text(
        json.dumps(
            {
                "skills": [
                    {"name": "read", "repo": "tw93/Waza", "path": "skills/read"},
                    {"name": "write", "repo": "tw93/Waza", "path": "skills/write"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (project / ".skill-manager.json").write_text(
        json.dumps(
            {
                "skills": [
                    {"name": "read", "repo": "tw93/Waza", "path": "skills/read"},
                    {"name": "write", "repo": "cnife/other", "path": "skills/write"},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    ui = ScriptedBoard(cancel=True)
    with pytest.raises(PickerCancelled):
        run_board(Path.home() / ".skill-manager.json", gconfig, cache, skills_dir, ui=ui)
    rows = {(row.repo, row.path): row for row in ui.board.rows}
    read_row = rows[("tw93/Waza", "skills/read")]
    assert read_row.enabled_globally and not read_row.global_conflict
    assert board_row_title(read_row).startswith("⊕ ")
    assert "in this project (same source)" in board_row_description(read_row)
    write_row = rows[("tw93/Waza", "skills/write")]
    assert write_row.enabled_globally and write_row.global_conflict
    assert board_row_title(write_row).startswith("⚠ ")
    assert "in this project from a different source" in board_row_description(write_row)
    assert "globally" not in board_row_description(write_row)


# ── commit ───────────────────────────────────────────────────────────────────


def test_board_enables_checked_skill(tmp_path: Path, make_source_repo) -> None:
    """A checked undeclared row is declared and linked."""
    project, cache, gconfig, skills_dir = _env(tmp_path, make_source_repo)
    result = run_board(
        project / ".skill-manager.json",
        gconfig,
        cache,
        skills_dir,
        ui=ScriptedBoard({"read"}),
    )
    assert [(o.action, o.skill["name"]) for o in result.enabled] == [("enabled", "read")]
    assert _declared(project) == [("read", "tw93/Waza", "skills/read")]
    assert _link_target(skills_dir, "read") == (cache / "tw93/Waza/skills/read").resolve()


def test_board_disables_unchecked_declaration(tmp_path: Path, make_source_repo) -> None:
    """Unchecking a declared row removes its declaration and managed link."""
    project, cache, gconfig, skills_dir = _env(tmp_path, make_source_repo)
    run_board(
        project / ".skill-manager.json", gconfig, cache, skills_dir, ui=ScriptedBoard({"read"})
    )
    result = run_board(
        project / ".skill-manager.json", gconfig, cache, skills_dir, ui=ScriptedBoard(set())
    )
    assert [o.skill["name"] for o in result.disabled] == ["read"]
    assert result.enabled == []
    assert _declared(project) == []
    assert not (skills_dir / "read").exists()
    assert not (skills_dir / "read").is_symlink()


def test_board_unchecks_every_name_at_one_path(tmp_path: Path, make_source_repo) -> None:
    """Two hand-written names on one path share the row: unchecking lifts both."""
    project, cache, gconfig, skills_dir = _env(tmp_path, make_source_repo)
    _write_config(
        project,
        [
            {"name": "read", "repo": "tw93/Waza", "path": "skills/read"},
            {"name": "alias", "repo": "tw93/Waza", "path": "skills/read"},
        ],
    )
    run_sync(project / ".skill-manager.json", gconfig, cache, skills_dir, result=SyncResult())
    assert (skills_dir / "read").is_symlink() and (skills_dir / "alias").is_symlink()

    result = run_board(
        project / ".skill-manager.json", gconfig, cache, skills_dir, ui=ScriptedBoard(set())
    )
    assert [o.skill["name"] for o in result.disabled] == ["read", "alias"]
    assert _declared(project) == []
    assert not (skills_dir / "read").exists() and not (skills_dir / "alias").exists()


def test_board_swaps_a_name_to_another_source(tmp_path: Path, make_source_repo) -> None:
    """One commit moves a name to a second source and points its link there."""
    project, cache, gconfig, skills_dir = _env(tmp_path, make_source_repo)
    other = make_source_repo("other", {"skills/read": skill_md("read", "Read elsewhere")})
    cfg = load_global_config(gconfig)
    clone_source("cnife/other", cfg, cache, url=f"file://{other}")
    save_global_config(gconfig, cfg)
    run_board(
        project / ".skill-manager.json",
        gconfig,
        cache,
        skills_dir,
        ui=KeysBoard(["tw93/Waza:skills/read"]),
    )

    result = run_board(
        project / ".skill-manager.json",
        gconfig,
        cache,
        skills_dir,
        ui=KeysBoard(["cnife/other:skills/read"]),
    )

    assert [(o.skill["repo"], o.skill["path"]) for o in result.disabled] == [
        ("tw93/Waza", "skills/read")
    ]
    assert [(o.skill["repo"], o.skill["path"]) for o in result.enabled] == [
        ("cnife/other", "skills/read")
    ]
    assert _declared(project) == [("read", "cnife/other", "skills/read")]
    assert _link_target(skills_dir, "read") == (cache / "cnife/other/skills/read").resolve()


def test_board_all_lists_internal_skills_and_enables_them(tmp_path: Path, make_source_repo) -> None:
    """--all widens the board's scan like enable: internal rows may exist and enable."""
    project, cache, gconfig, skills_dir = _env(
        tmp_path,
        make_source_repo,
        skills={
            "skills/read": skill_md("read", "Read things"),
            "skills/secret": skill_md(
                "secret", "Internal skill", extra_fm="metadata:\n  internal: true\n"
            ),
        },
    )
    default_rows = ScriptedBoard(cancel=True)
    with pytest.raises(PickerCancelled):
        run_board(project / ".skill-manager.json", gconfig, cache, skills_dir, ui=default_rows)
    assert default_rows.board is not None
    assert not any(row.name == "secret" for row in default_rows.board.rows)

    all_rows = ScriptedBoard(cancel=True)
    with pytest.raises(PickerCancelled):
        run_board(
            project / ".skill-manager.json",
            gconfig,
            cache,
            skills_dir,
            include_all=True,
            ui=all_rows,
        )
    assert all_rows.board is not None
    secret = next(row for row in all_rows.board.rows if row.name == "secret")
    assert not secret.declared

    result = run_board(
        project / ".skill-manager.json",
        gconfig,
        cache,
        skills_dir,
        include_all=True,
        ui=ScriptedBoard({"secret"}),
    )
    assert [(o.action, o.skill["name"]) for o in result.enabled] == [("enabled", "secret")]
    assert _declared(project) == [("secret", "tw93/Waza", "skills/secret")]
    assert _link_target(skills_dir, "secret") == (cache / "tw93/Waza/skills/secret").resolve()


def test_board_cancel_writes_nothing(tmp_path: Path, make_source_repo) -> None:
    """Esc is a cancel: no declarations, no links, no cache work."""
    project, cache, gconfig, skills_dir = _env(tmp_path, make_source_repo)
    config = project / ".skill-manager.json"
    config.write_text('{"skills": []}\n', encoding="utf-8")
    text = config.read_text(encoding="utf-8")
    with pytest.raises(PickerCancelled):
        run_board(
            config,
            gconfig,
            cache,
            skills_dir,
            ui=ScriptedBoard(cancel=True),
        )
    assert config.read_text(encoding="utf-8") == text
    assert not skills_dir.exists()


def test_board_rejects_a_cross_scope_conflict_atomically(tmp_path: Path, make_source_repo) -> None:
    """Enabling a name the other scope holds from elsewhere fails before any write."""
    project, cache, gconfig, skills_dir = _env(tmp_path, make_source_repo)
    (Path.home() / ".skill-manager.json").write_text(
        json.dumps({"skills": [{"name": "read", "repo": "other/repo", "path": "skills/read"}]}),
        encoding="utf-8",
    )
    config = project / ".skill-manager.json"
    config.write_text('{"skills": []}\n', encoding="utf-8")
    text = config.read_text(encoding="utf-8")
    with pytest.raises(NotFoundError) as excinfo:
        run_board(config, gconfig, cache, skills_dir, ui=ScriptedBoard({"read"}))
    message = str(excinfo.value)
    assert "cross-scope name conflict" in message
    assert "skill-manager --global disable read" in message
    assert config.read_text(encoding="utf-8") == text
    assert not skills_dir.exists()


def test_board_rejects_a_row_checked_twice_before_writing(tmp_path: Path, make_source_repo) -> None:
    """Two rows with one name cannot commit, even if an adapter returns both."""
    project, cache, gconfig, skills_dir = _env(
        tmp_path,
        make_source_repo,
        skills={"skills/read": skill_md("read"), "other/read": skill_md("read")},
    )
    with pytest.raises(BoardError, match="checked from both"):
        run_board(
            project / ".skill-manager.json",
            gconfig,
            cache,
            skills_dir,
            ui=KeysBoard(["tw93/Waza:skills/read", "tw93/Waza:other/read"]),
        )
    assert _declared(project) == []
    assert not skills_dir.exists()


def test_board_empty_scope_points_at_source_add(tmp_path: Path, make_source_repo) -> None:
    """No cached source and no declaration: the board names the command that starts one."""
    project = tmp_path / "proj"
    project.mkdir()
    _write_config(project, [])
    with pytest.raises(NotFoundError) as excinfo:
        run_board(
            project / ".skill-manager.json",
            tmp_path / "config.json",
            tmp_path / "empty-cache",
            project / ".agents" / "skills",
            ui=ScriptedBoard(),
        )
    message = str(excinfo.value)
    assert "No cached repos found" in message
    assert "source add" in message


def test_board_requires_a_terminal(tmp_path: Path) -> None:
    """Without an injected adapter the board refuses a non-TTY invocation."""
    project = tmp_path / "proj"
    project.mkdir()
    with pytest.raises(NotFoundError, match="needs a terminal"):
        run_board(
            project / ".skill-manager.json",
            tmp_path / "config.json",
            tmp_path / "empty-cache",
            project / ".agents" / "skills",
        )


def test_board_offers_a_declared_skill_the_scan_hides(tmp_path: Path, make_source_repo) -> None:
    """A declared internal skill stays toggleable instead of looking missing."""
    project, cache, gconfig, skills_dir = _env(
        tmp_path,
        make_source_repo,
        skills={
            "skills/read": skill_md("read"),
            "skills/hidden": skill_md("hidden", extra_fm="metadata:\n  internal: true"),
        },
    )
    _write_config(project, [{"name": "hidden", "repo": "tw93/Waza", "path": "skills/hidden"}])
    ui = ScriptedBoard(cancel=True)
    with pytest.raises(PickerCancelled):
        run_board(project / ".skill-manager.json", gconfig, cache, skills_dir, ui=ui)
    rows = {row.name: row for row in ui.board.rows}
    assert rows["hidden"].declared and rows["hidden"].available
    assert all(group.repo is not None for group in ui.board.groups)


def test_bare_invocation_refuses_a_pipe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A piped bare invocation gets one JSON error, not a half-drawn screen."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, [])
    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["ok"] is False
    assert body["error"]["code"] == "not_found"
    assert "needs a terminal" in body["error"]["message"]
    assert result.stderr == ""


def test_bare_invocation_opens_the_board(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`skill-manager` with no command is the board: it commits like the screen."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    gconfig = tmp_path / "xdg-config" / "skill-manager" / "config.json"
    cache = tmp_path / "xdg-cache" / "skill-manager" / "repos"
    upstream = make_source_repo(
        "waza",
        {
            "skills/read": skill_md("read", "Read things"),
            "skills/write": skill_md("write", "Write things"),
        },
    )
    project = tmp_path / "proj"
    project.mkdir()
    skills_dir = project / ".agents" / "skills"
    cfg = load_global_config(gconfig)
    clone_source("tw93/Waza", cfg, cache, url=f"file://{upstream}")
    save_global_config(gconfig, cfg)
    _write_config(project, [{"name": "read", "repo": "tw93/Waza", "path": "skills/read"}])
    run_sync(project / ".skill-manager.json", gconfig, cache, skills_dir, result=SyncResult())
    assert (skills_dir / "read").is_symlink()

    monkeypatch.chdir(project)
    monkeypatch.setattr(cli, "stdin_stdout_are_tty", lambda: True)
    monkeypatch.setattr(cli, "QuestionaryBoard", lambda: ScriptedBoard(set()))
    result = runner.invoke(app, [])
    assert result.exit_code == 0, result.stdout
    assert _declared(project) == []
    assert not (skills_dir / "read").exists()


def test_all_belongs_to_the_board_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--all` with a subcommand is a usage error, not a silently ignored flag."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["--all", "list"])
    assert result.exit_code == 2
    body = json.loads(result.stdout)
    assert body["error"]["code"] == "usage_error"
    assert "--all belongs to the skill board" in body["error"]["message"]


def test_board_subcommand_is_gone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The screen is the bare invocation; `board` is not a command."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["board"])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "usage_error"


# ── rendering ────────────────────────────────────────────────────────────────


ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def _render(result: BoardResult) -> list[str]:
    stream = io.StringIO()
    console = Console(
        file=stream, width=100, height=40, force_terminal=True, color_system="standard"
    )
    render_board(console, result)
    plain = ANSI.sub("", stream.getvalue())
    return [line.rstrip() for line in plain.splitlines() if line.strip()]


def test_render_board_lists_disabled_then_enabled() -> None:
    """One line per committed change, disable first (the order it was applied)."""
    result = BoardResult(
        disabled=[
            DisableOutcome(
                action="disabled",
                skill={"name": "old", "repo": "a/one", "path": "skills/old"},
                link_removed=True,
                link_note="removed .agents/skills/old",
            )
        ],
        enabled=[
            EnableOutcome(
                action="enabled",
                skill={"name": "new", "repo": "b/two", "path": "skills/new"},
                enabled_globally=True,
            ),
            EnableOutcome(
                action="enabled",
                skill={"name": "warn", "repo": "b/two", "path": "skills/warn"},
                global_conflict=True,
            ),
        ],
        sync=SyncResult(),
    )
    lines = _render(result)
    assert "disabled" in lines[0] and "old" in lines[0]
    assert "new" in lines[1] and "b/two:skills/new" in lines[1]
    assert lines[1].index("new") < lines[1].rindex("b/two")
    assert lines[2].endswith("⚠")
    assert "different source" in lines[3]


def test_render_board_says_no_changes() -> None:
    """An unchanged board is one sentence, not an empty table."""
    assert _render(BoardResult()) == ["No changes."]
