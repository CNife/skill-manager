"""Two-track output layer (issue #57): track decision, human rendering, progress.

stdout decides the track: a TTY gets Human output (Rich), anything else gets
JSON. This module owns the Human track and the decision itself:

a. track decision (``render.is_tty`` / ``render.make_console``),
b. Human rendering — asserted through an injected fixed-width ``Console``, so
   no real terminal is needed and the layout is deterministic,
c. the progress sink contract (``start``/``done``, no committed residue),
d. the decision proven end-to-end: the same command run with stdout on a real
   pty (Human) and with stdout on a pipe (JSON).

JSON-track behavior (envelopes, compactness, exit codes) lives in
``test_json_cli.py``; this module only asserts that the JSON track stays free of
human artifacts.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from helpers import skill_md
from rich.console import Console
from rich.text import Text

from skill_manager import paths, render
from skill_manager.cli import run_enable
from skill_manager.config import GlobalConfig, save_global_config
from skill_manager.render import Outcome, Progress
from skill_manager.results import (
    AvailableSkillsResult,
    DisableOutcome,
    DisableResult,
    DoctorProblem,
    DoctorResult,
    EnableOutcome,
    EnableResult,
    LinkDone,
    ListResult,
    SkillStatus,
    SourceEnsured,
    SourceListResult,
    SourceStatus,
    SourceUpdateResult,
    SyncResult,
)
from skill_manager.sources import clone_source

ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
# Color SGR only (30-37 fg, 90-97 bright fg, in a compound like "1;36"): Rich
# keeps bold/dim attributes under NO_COLOR, it drops color.
ANSI_COLOR = re.compile(r"\x1b\[[0-9;]*(3[0-9]|9[0-9])m")
GREEN = "\x1b[32m"
RED = "\x1b[31m"

BOX_CHARS = "╭─╮│╰╯├┼┤┬┴"
LONG_PATH = "skills/engineering/domain-modeling"


# ── helpers ──────────────────────────────────────────────────────────────────


@dataclass
class Table:
    """One rendered table: header cells plus data rows, folded cells merged."""

    header: list[str]
    rows: list[list[str]]


@dataclass
class Rendered:
    """One render call's output: raw bytes plus the ANSI-free text."""

    raw: str
    plain: str

    @property
    def lines(self) -> list[str]:
        return self.plain.splitlines()

    def tables(self) -> list[Table]:
        return parse_tables(self.lines)


def parse_tables(lines: list[str]) -> list[Table]:
    """Parse box-drawn tables, re-joining cells that folded onto extra lines.

    A folded row continues on following lines whose primary-key cell is empty;
    those cells are concatenated back so a test can assert the *value* a person
    reads, however the terminal wrapped it.
    """
    tables: list[Table] = []
    current: Table | None = None
    expect_header = False
    for line in lines:
        if line.startswith("╭"):
            current = Table(header=[], rows=[])
            tables.append(current)
            expect_header = True
            continue
        if line.startswith("├"):
            continue  # header/data separator: the table continues
        if not line.startswith("│"):
            current = None
            continue
        if current is None:
            continue
        cells = _cells(line)
        if expect_header:
            current.header = cells
            expect_header = False
            continue
        last = current.rows[-1] if current.rows else None
        if last is not None and not cells[0] and len(cells) == len(last):
            current.rows[-1] = [a + b for a, b in zip(last, cells, strict=True)]
        else:
            current.rows.append(cells)
    return tables


def _flat(rendered: Rendered) -> str:
    """Rendered text as one line: soft wraps and cell padding collapsed."""
    return re.sub(r"\s+", " ", rendered.plain).strip()


def _cells(line: str) -> list[str]:
    """Cells of one table line: ANSI-free, borders dropped, each cell stripped."""
    return [cell.strip() for cell in ANSI.sub("", line).strip("│").split("│")]


def _render(
    view: Callable[[Console, object], None],
    result: object,
    *,
    width: int = 100,
    no_color: bool = False,
) -> Rendered:
    """Render ``result`` through ``view`` on a fixed-size captured console.

    Both dimensions and ``TERM`` are pinned: Rich only honours ``width`` when
    ``height`` is set too, and it reads ``TERM`` to decide between in-place and
    plain output, so an unpinned console renders differently on a CI runner
    (``TERM=dumb``) than on a developer's terminal.
    """
    stream = io.StringIO()
    console = Console(
        file=stream,
        width=width,
        height=40,
        force_terminal=True,
        color_system="standard",
        no_color=no_color,
        _environ=_RENDER_ENVIRON(width),
    )
    view(console, result)
    raw = stream.getvalue()
    return Rendered(raw=raw, plain=ANSI.sub("", raw))


def _RENDER_ENVIRON(width: int) -> dict[str, str]:
    """Environment for a deterministic rendering console (see :func:`_render`)."""
    return {"TERM": "xterm-256color", "COLUMNS": str(width), "LINES": "40"}


class _PrintSpy(Console):
    """Console that records durable *text* writes, so a test can prove there were none.

    Rich's own live-line teardown prints an empty ``NewLine`` to land the cursor
    on a fresh row; that is not text, so only strings and ``Text`` are recorded.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.printed: list[str] = []

    def print(self, *objects, **kwargs) -> None:
        self.printed.extend(str(obj) for obj in objects if isinstance(obj, (str, Text)))
        super().print(*objects, **kwargs)


class RecordingSink:
    """Duck-typed ProgressSink that records the event sequence it receives."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def start(self, text: str) -> None:
        self.events.append(("start", text))

    def done(self, text: str) -> None:
        self.events.append(("done", text))


def _list_result() -> ListResult:
    """Two skills: one plain, one also enabled globally from another source."""
    return ListResult(
        skills=[
            SkillStatus(
                name="read",
                repo="tw93/Waza",
                path="skills/read",
                link="linked",
                enabled_globally=False,
            ),
            SkillStatus(
                name="domain-modeling",
                repo="mattpocock/skills",
                path=LONG_PATH,
                link="unlinked",
                enabled_globally=True,
                global_conflict=True,
            ),
        ],
        source_rows=[
            ("mattpocock/skills", "c55ee460", "cached"),
            ("tw93/Waza", "2d2784e2", "cached"),
        ],
    )


def _doctor_problems() -> list[DoctorProblem]:
    return [
        DoctorProblem(
            code="unlinked",
            scope="project",
            message="skill 'read' is not linked",
            fix="skill-manager sync",
            name="read",
        ),
        DoctorProblem(
            code="unlinked",
            scope="project",
            message="skill 'research' is not linked",
            fix="skill-manager sync",
            name="research",
        ),
        DoctorProblem(
            code="orphan_link",
            scope="project",
            message="link 'gone' has no declaration",
            fix="remove the link",
            name="gone",
        ),
        DoctorProblem(
            code="cross_scope_conflict",
            scope="cross-scope",
            message="skill 'read' declared in both scopes with different sources",
            fix="disable one side",
            name="read",
        ),
    ]


def _seed_project(
    tmp_path: Path,
    make_source_repo,
    monkeypatch: pytest.MonkeyPatch,
    *,
    skills: dict[str, str] | None = None,
    declared: dict[str, str] | None = None,
) -> tuple[Path, Path]:
    """Cached source + declaration, cwd inside the project.

    Returns ``(project, source)``; ``declared`` defaults to every available skill.
    """
    if skills is None:
        skills = {"read": "skills/read", "research": "skills/research"}
    if declared is None:
        declared = skills
    repo = "tw93/Waza"
    source = make_source_repo(repo, {path: skill_md(name) for name, path in skills.items()})
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    global_cfg = GlobalConfig()
    clone_source(repo, global_cfg, paths.repos_cache_dir(), url=f"file://{source}")
    save_global_config(paths.config_file(), global_cfg)
    (project / ".skill-manager.json").write_text(
        json.dumps(
            {
                "skills": [
                    {"name": name, "repo": repo, "path": path} for name, path in declared.items()
                ]
            }
        ),
        encoding="utf-8",
    )
    return project, source


def _run_piped(project: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run the CLI with stdout on a pipe: the JSON track."""
    return subprocess.run(
        [sys.executable, "-m", "skill_manager", *args],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )


# ── a. track decision ────────────────────────────────────────────────────────


class _Stream:
    """Minimal stream stand-in whose only job is reporting TTY-ness."""

    def __init__(self, tty: bool | Exception) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        if isinstance(self._tty, Exception):
            raise self._tty
        return self._tty


def test_is_tty_true_for_terminal_stream() -> None:
    assert render.is_tty(_Stream(True)) is True


@pytest.mark.parametrize(
    "stream",
    [_Stream(False), _Stream(ValueError("closed")), _Stream(OSError("no tty"))],
)
def test_is_tty_false_for_pipe_or_unreadable_stream(stream: _Stream) -> None:
    """The JSON track is the safe default: a stream that cannot answer is not a TTY."""
    assert render.is_tty(stream) is False


def test_make_console_follows_stdout_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Human-vs-JSON is decided by stdout only, never by stderr or CI."""
    monkeypatch.setattr(sys, "stdout", _Stream(True))
    assert render.make_console().is_terminal is True
    monkeypatch.setattr(sys, "stdout", _Stream(False))
    assert render.make_console().is_terminal is False


def test_make_console_for_stderr_keeps_its_own_stream() -> None:
    console = render.make_console(stderr=True)
    assert console.file is sys.stderr
    assert console.stderr is True


# ── b. list table ────────────────────────────────────────────────────────────


def test_list_sections_and_column_order_are_fixed() -> None:
    rendered = _render(render.render_list, _list_result())
    sources, skills = rendered.tables()
    assert sources.header == ["repo", "HEAD", "status"]
    assert skills.header == ["name", "status", "global", "repo", "path"]


def test_list_rows_carry_bare_name_and_own_mark_column() -> None:
    """The name cell is a bare skill name; ⊕/⚠ live in their own column."""
    rendered = _render(render.render_list, _list_result())
    assert rendered.tables()[1].rows == [
        ["read", "linked", "", "tw93/Waza", "skills/read"],
        ["domain-modeling", "unlinked", "⚠", "mattpocock/skills", LONG_PATH],
    ]


def test_list_annotates_the_mark_column_with_a_legend() -> None:
    rendered = _render(render.render_list, _list_result())
    assert "⊕ = also enabled globally, same source" in _flat(rendered)
    assert "⚠ = enabled globally from a different source" in _flat(rendered)


def test_list_benign_overlap_uses_oplus_and_its_own_legend() -> None:
    result = ListResult(
        skills=[
            SkillStatus(
                name="read",
                repo="tw93/Waza",
                path="skills/read",
                link="linked",
                enabled_globally=True,
            )
        ],
        source_rows=[],
    )
    rendered = _render(render.render_list, result)
    assert rendered.tables()[0].rows[0][2] == "⊕"
    assert "⚠" not in rendered.plain


def test_list_sources_section_precedes_skills() -> None:
    rendered = _render(render.render_list, _list_result())
    assert rendered.plain.index("Sources") < rendered.plain.index("Skills")
    assert "c55ee460" in rendered.plain


def test_list_colors_status_by_semantics() -> None:
    rendered = _render(render.render_list, _list_result())
    assert f"{GREEN}linked" in rendered.raw


def test_list_empty_state_is_one_sentence_without_a_table() -> None:
    rendered = _render(render.render_list, ListResult(skills=[], source_rows=[]))
    assert _flat(rendered) == (
        "No skills enabled yet. Try 'skill-manager enable' or "
        "'skill-manager source available-skills' to discover and enable skills."
    )


def test_a_narrow_console_never_clips_the_primary_key() -> None:
    """No column is ellipsized: the key columns survive even when width is tight."""
    rendered = _render(render.render_list, _list_result(), width=60)
    assert "…" not in rendered.plain
    assert [row[0] for row in rendered.tables()[1].rows] == ["read", "domain-modeling"]
    assert [row[1] for row in rendered.tables()[1].rows] == ["linked", "unlinked"]
    assert [row[3] for row in rendered.tables()[1].rows] == ["tw93/Waza", "mattpocock/skills"]


def test_a_wide_column_folds_without_losing_characters() -> None:
    """The wide path column folds across lines and re-assembles to the full value."""
    rendered = _render(render.render_list, _list_result(), width=77)
    assert "…" not in rendered.plain
    assert rendered.tables()[1].rows[1][4] == LONG_PATH


def test_no_color_keeps_structure_and_drops_color() -> None:
    rendered = _render(render.render_list, _list_result(), no_color=True)
    assert ANSI_COLOR.search(rendered.raw) is None
    assert GREEN not in rendered.raw
    assert any(char in rendered.plain for char in BOX_CHARS)
    assert "name" in rendered.plain


def test_a_non_terminal_console_renders_without_ansi() -> None:
    stream = io.StringIO()
    render.render_list(Console(file=stream, width=100), _list_result())
    assert "\x1b[" not in stream.getvalue()


# ── c. doctor sections ───────────────────────────────────────────────────────


def test_doctor_sections_by_code_with_volume_first() -> None:
    rendered = _render(render.render_doctor, DoctorResult(problems=_doctor_problems()))
    titles = [
        line for line in rendered.lines if line.split()[:1] in (["unlinked"], ["orphan_link"])
    ]
    assert titles == ["unlinked  (2)", "orphan_link  (1)"]


def test_doctor_repeats_a_shared_fix_once_per_section() -> None:
    rendered = _render(render.render_doctor, DoctorResult(problems=_doctor_problems()))
    assert rendered.plain.count("fix: skill-manager sync") == 1
    assert "fix: remove the link" in rendered.plain


def test_doctor_detail_column_shows_the_problem_message() -> None:
    """A named problem keeps its message: the name alone loses the diagnosis."""
    # Wide enough that no cell folds: this asserts values, not wrapping.
    rendered = _render(render.render_doctor, DoctorResult(problems=_doctor_problems()), width=200)
    rows = {row[1] for table in rendered.tables() for row in table.rows}
    assert "skill 'read' declared in both scopes with different sources" in rows
    dirty = _render(
        render.render_doctor,
        DoctorResult(
            problems=[
                DoctorProblem(
                    code="cache_dirty",
                    scope="cache",
                    message="cached source 'tw93/Waza' has local changes",
                    fix="git -C cache clean -fd",
                )
            ]
        ),
        width=200,
    )
    assert dirty.tables()[0].rows[0][1] == "cached source 'tw93/Waza' has local changes"


def test_doctor_keeps_distinct_fixes_as_a_column() -> None:
    """Only an identical fix collapses into the section note; distinct ones stay."""
    result = DoctorResult(
        problems=[
            DoctorProblem(
                code="orphan_source_registered",
                scope="config",
                message="source 'tw93/Waza' is registered but not declared",
                fix="skill-manager source remove tw93/Waza",
            ),
            DoctorProblem(
                code="orphan_source_registered",
                scope="config",
                message="source 'other/repo' is registered but not declared",
                fix="skill-manager source remove other/repo",
            ),
        ]
    )
    rendered = _render(render.render_doctor, result, width=200)
    table = rendered.tables()[0]
    assert table.header == ["scope", "detail", "fix"]
    assert [row[2] for row in table.rows] == [
        "skill-manager source remove tw93/Waza",
        "skill-manager source remove other/repo",
    ]
    assert "fix:" not in rendered.plain


def test_doctor_healthy_state_is_a_single_sentence() -> None:
    rendered = _render(render.render_doctor, DoctorResult(problems=[]))
    assert rendered.lines == ["No problems found."]


def test_doctor_drops_the_category_layer() -> None:
    rendered = _render(render.render_doctor, DoctorResult(problems=_doctor_problems()))
    for category in ("Config:", "Source:", "Link:", "Conflict:", "Cache:", "Environment:"):
        assert category not in rendered.plain


# ── d. line stream (results, errors, empty states) ───────────────────────────


def test_outcome_stream_has_no_table_frame_or_header() -> None:
    rendered = _render(
        render.render_outcomes,
        [
            Outcome("tw93/Waza", "up-to-date", "2d2784e2"),
            Outcome("read", "linked", ".agents/skills/read"),
        ],
    )
    assert not any(char in rendered.plain for char in BOX_CHARS)
    assert rendered.lines == [
        "  ✓  tw93/Waza  up-to-date  2d2784e2",
        "  ✓  read       linked      .agents/skills/read",
    ]


def test_outcome_columns_align_by_cell_width_not_code_points() -> None:
    """A CJK label takes two cells: counting code points would push the next column out."""
    rendered = _render(
        render.render_outcomes,
        [Outcome("研究", "linked", "a"), Outcome("read", "linked", "b")],
    )
    assert rendered.lines == [
        "  ✓  研究  linked  a",
        "  ✓  read  linked  b",
    ]


def test_outcome_noop_is_dotted_not_checked() -> None:
    rendered = _render(render.render_outcomes, [Outcome("read", "already enabled", ok=False)])
    assert rendered.lines == ["  ·  read  already enabled"]


def test_error_line_is_a_red_prefixed_sentence() -> None:
    rendered = _render(render.render_error, "source 'tw93/Waza' not found")
    assert rendered.lines == ["✗  Error: source 'tw93/Waza' not found"]
    assert f"{RED}✗" in rendered.raw
    assert not any(char in rendered.plain for char in BOX_CHARS)


def test_warning_line_stays_a_plain_sentence() -> None:
    rendered = _render(render.render_warning, "'tw93/Waza' still referenced by: project")
    assert rendered.lines == ["Warning: 'tw93/Waza' still referenced by: project"]


def test_sync_outcomes_list_sources_then_links() -> None:
    result = SyncResult(
        sources=[SourceEnsured(repo="tw93/Waza", commit="2d2784e2", action="cloned")],
        links=[LinkDone(name="read", action="created", target=Path("/tmp/x/skills/read"))],
    )
    rendered = _render(render.render_sync, result)
    assert [line.split()[1] for line in rendered.lines] == ["tw93/Waza", "read"]


def test_sync_empty_state_is_one_sentence() -> None:
    rendered = _render(render.render_sync, SyncResult())
    assert rendered.lines == ["Nothing to sync."]


def test_enable_marks_cross_scope_overlap_and_keeps_detail() -> None:
    result = EnableResult(
        outcomes=[
            EnableOutcome(
                action="enabled",
                skill={"name": "read", "repo": "tw93/Waza", "path": "skills/read"},
                enabled_globally=True,
            )
        ]
    )
    rendered = _render(render.render_enable, result)
    assert "✓  read  enabled  tw93/Waza:skills/read  ⊕" in rendered.plain
    assert "⊕ = also enabled globally, same source" in rendered.plain


def test_disable_reports_no_ops_without_a_check() -> None:
    result = DisableResult(
        outcomes=[
            DisableOutcome(
                action="disabled",
                skill={"name": "read", "repo": "tw93/Waza", "path": "skills/read"},
                link_removed=True,
                link_note="removed .agents/skills/read",
            ),
            DisableOutcome(action="not_enabled", skill={"name": "ghost"}),
        ]
    )
    rendered = _render(render.render_disable, result)
    assert rendered.lines == [
        "  ✓  read   disabled     removed .agents/skills/read",
        "  ·  ghost  not enabled",
    ]


def test_disable_empty_state() -> None:
    rendered = _render(render.render_disable, DisableResult(outcomes=[]))
    assert rendered.lines == ["Nothing to disable."]


def test_available_skills_keeps_one_section_per_repo() -> None:
    result = AvailableSkillsResult(
        skills=[
            {"name": "read", "repo": "tw93/Waza", "path": "skills/read"},
            {"name": "research", "repo": "tw93/Waza", "path": "skills/research"},
            {"name": "wayfinder", "repo": "ogulcancelik/herdr", "path": "skills/wayfinder"},
        ]
    )
    rendered = _render(render.render_available_skills, result)
    assert "tw93/Waza  (2)" in rendered.plain
    assert "ogulcancelik/herdr  (1)" in rendered.plain
    assert [table.header for table in rendered.tables()] == [
        ["skill", "path"],
        ["skill", "path"],
    ]
    assert [row for table in rendered.tables() for row in table.rows] == [
        ["wayfinder", "skills/wayfinder"],  # repos are visited in sorted order
        ["read", "skills/read"],
        ["research", "skills/research"],
    ]


def test_source_list_shows_head_cache_state_and_url() -> None:
    result = SourceListResult(
        sources=[
            SourceStatus(repo="tw93/Waza", commit="2d2784e2", url="https://x/y", cached=True),
            SourceStatus(
                repo="mattpocock/skills", commit="c55ee460", url="https://a/b", cached=False
            ),
        ]
    )
    rendered = _render(render.render_source_list, result)
    assert rendered.tables()[0].rows == [
        ["tw93/Waza", "2d2784e2", "cached", "https://x/y"],
        ["mattpocock/skills", "c55ee460", "missing", "https://a/b"],
    ]


def test_source_update_reports_old_and_new_commit() -> None:
    result = SourceUpdateResult(
        updates=[
            SourceEnsured(
                repo="tw93/Waza", commit="beef0000", action="updated", old_commit="cafe0000"
            )
        ]
    )
    rendered = _render(render.render_source_update, result)
    assert rendered.lines == ["  ✓  tw93/Waza  updated  cafe0000 → beef0000"]


def test_source_update_empty_state() -> None:
    rendered = _render(render.render_source_update, SourceUpdateResult(updates=[]))
    assert rendered.lines == ["No sources registered (use 'source add' first)"]


def test_source_action_line_reports_the_action() -> None:
    rendered = _render(
        lambda console, args: render.render_source_action(console, args[0], args[1], args[2]),
        ("tw93/Waza", "added", "2d2784e2"),
    )
    assert rendered.lines == ["  ✓  tw93/Waza  added  2d2784e2"]


# ── e. display path (Human-track detail) ─────────────────────────────────────


def test_display_path_relative_to_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    assert render.display_path(project / ".agents" / "skills" / "read") == ".agents/skills/read"


def test_display_path_home_abbreviated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"  # created by the isolated_xdg fixture
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project)
    assert render.display_path(home / ".agents" / "skills" / "read") == "~/.agents/skills/read"


def test_display_path_absolute_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "proj"
    outside = tmp_path / "elsewhere" / "read"
    project.mkdir()
    monkeypatch.chdir(project)
    assert render.display_path(outside) == str(outside)


def test_display_path_shows_the_link_not_its_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A symlink displays as the link itself, never resolved to its target."""
    project = tmp_path / "proj"
    target = tmp_path / "cache" / "read"
    target.mkdir(parents=True)
    (project / ".agents" / "skills").mkdir(parents=True)
    monkeypatch.chdir(project)
    link = project / ".agents" / "skills" / "read"
    link.symlink_to(target)
    assert render.display_path(link) == ".agents/skills/read"


# ── f. progress sink ─────────────────────────────────────────────────────────


def test_progress_live_line_never_commits_its_text() -> None:
    """The live line is in-place: progress text never reaches a durable write.

    Rich decides in-place vs plain from ``TERM``, so the environment is pinned:
    on a dumb terminal the sink degrades to writing nothing at all, which would
    let this pass for the wrong reason (see the sibling test below).
    """
    console = _PrintSpy(
        file=io.StringIO(),
        width=100,
        height=40,
        force_terminal=True,
        _environ=_RENDER_ENVIRON(100),
    )
    progress = Progress(console)
    progress.start("pulling tw93/Waza...")
    progress.done("pulled tw93/Waza (cafe0000 → beef0000)")
    assert console.printed == []


def test_progress_is_silent_without_an_interactive_terminal() -> None:
    """A dumb terminal cannot do in-place, so the sink writes nothing — not text."""
    stream = io.StringIO()
    console = Console(
        file=stream,
        width=100,
        height=40,
        force_terminal=True,
        _environ={"TERM": "dumb", "COLUMNS": "100", "LINES": "40"},
    )
    progress = Progress(console)
    progress.start("pulling tw93/Waza...")
    progress.done("pulled tw93/Waza (cafe0000 → beef0000)")
    assert stream.getvalue() == ""


def test_progress_tolerates_repeated_start_and_bare_done() -> None:
    """A ``done`` without a live ``start`` is harmless (no status to stop)."""
    console = Console(file=io.StringIO(), width=100, height=40, force_terminal=True)
    progress = Progress(console)
    progress.done("nothing was running")
    progress.start("pulling x...")
    progress.start("cloning y...")
    progress.done("cloned y")


def test_run_enable_reports_one_start_done_pair_per_operation(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, source = _seed_project(
        tmp_path, make_source_repo, monkeypatch, skills={"read": "skills/read"}, declared={}
    )
    sink = RecordingSink()
    result = run_enable(
        project / ".skill-manager.json",
        paths.config_file(),
        paths.repos_cache_dir(),
        project / ".agents" / "skills",
        repo="tw93/Waza",
        names=["read"],
        progress=sink,
        url_resolver=lambda repo: f"file://{source}",
    )
    assert [event for event, _text in sink.events] == ["start", "done", "start", "done"]
    assert sink.events[0][1] == "preparing source tw93/Waza..."
    assert sink.events[1][1].startswith("prepared source tw93/Waza (")
    assert sink.events[2][1] == "linking read..."
    assert result.outcomes[0].action == "enabled"
    assert result.sync is not None and result.sync.links[0].action == "created"


# ── g. the two tracks, end to end ────────────────────────────────────────────


def test_sync_reports_finished_work_when_a_later_source_fails(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch, run_in_pty
) -> None:
    """A sync that dies on the second source still leaves the first one's line."""
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    global_cfg = GlobalConfig()
    upstreams: dict[str, Path] = {}
    for repo in ("first/repo", "second/repo"):
        upstreams[repo] = make_source_repo(repo, {"read": "skills/read"})
        clone_source(repo, global_cfg, paths.repos_cache_dir(), url=f"file://{upstreams[repo]}")
    save_global_config(paths.config_file(), global_cfg)
    (project / ".skill-manager.json").write_text(
        json.dumps(
            {
                "skills": [
                    {"name": "read", "repo": "first/repo", "path": "skills/read"},
                    {"name": "other", "repo": "second/repo", "path": "skills/read"},
                ]
            }
        ),
        encoding="utf-8",
    )
    upstreams["second/repo"].rename(tmp_path / "gone")

    run = run_in_pty(["sync"], cwd=project)
    assert run.exit_code == 1
    assert "Error:" in run.err
    lines = [line.strip() for line in ANSI.sub("", run.text).replace("\r", "\n").splitlines()]
    finished = [line for line in lines if line.startswith("✓") and "first/repo" in line]
    assert finished, run.text
    assert "up-to-date" in finished[0]


def test_human_track_renders_a_table_on_a_real_pty(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch, run_in_pty
) -> None:
    project, _source = _seed_project(tmp_path, make_source_repo, monkeypatch)
    sync = run_in_pty(["sync"], cwd=project)
    assert sync.exit_code == 0, sync.err
    assert "linked" in sync.text
    run = run_in_pty(["list"], cwd=project)
    assert run.exit_code == 0, run.err
    assert run.err == ""
    assert parse_tables(run.lines)[1].header == ["name", "status", "global", "repo", "path"]
    assert any(char in run.text for char in BOX_CHARS)
    assert "\x1b[" in run.text  # colors are on for a real terminal
    assert "linked" in run.text


def test_human_track_keeps_structure_without_color(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch, run_in_pty
) -> None:
    project, _source = _seed_project(tmp_path, make_source_repo, monkeypatch)
    run = run_in_pty(["list"], cwd=project, env={"NO_COLOR": "1"})
    assert run.exit_code == 0, run.err
    assert any(char in run.text for char in BOX_CHARS)
    assert ANSI_COLOR.search(run.text) is None
    assert "name" in run.text


def test_human_track_reports_errors_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_in_pty
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    (project / ".skill-manager.json").write_text("{ not json", encoding="utf-8")
    run = run_in_pty(["list"], cwd=project)
    assert run.exit_code == 1
    assert "Error:" in run.err
    assert "invalid JSON" in run.err
    assert "Error:" not in run.text


def test_json_track_when_stdout_is_not_a_tty(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the decision: a pipe gets JSON, never a table."""
    project, _source = _seed_project(tmp_path, make_source_repo, monkeypatch)
    completed = _run_piped(project, ["list"])
    assert completed.returncode == 0
    assert completed.stdout.count("\n") == 1
    assert json.loads(completed.stdout)["ok"] is True
    assert not any(char in completed.stdout for char in BOX_CHARS)


def test_json_track_has_no_progress_lines(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pipe never sees the transient live line, not even during real work."""
    project, _source = _seed_project(tmp_path, make_source_repo, monkeypatch)
    completed = _run_piped(project, ["sync"])
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["data"]["sources"][0]["action"] == "up_to_date"
    assert [link["action"] for link in payload["data"]["links"]] == ["created", "created"]
    for transient in ("pulling", "cloning", "linking", "\x1b["):
        assert transient not in completed.stdout


def test_metadata_commands_stay_click_native_on_both_tracks(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch, run_in_pty
) -> None:
    project, _source = _seed_project(tmp_path, make_source_repo, monkeypatch)
    piped = _run_piped(project, ["--version"])
    terminal = run_in_pty(["--version"], cwd=project)
    assert piped.returncode == 0 and terminal.exit_code == 0
    assert piped.stdout.startswith("skill-manager ")
    assert terminal.text.startswith("skill-manager ")
    assert "{" not in piped.stdout


def test_narrow_pty_folds_a_long_path_column(
    tmp_path: Path, make_source_repo, monkeypatch: pytest.MonkeyPatch, run_in_pty
) -> None:
    """No information is lost in a narrow terminal: the path folds, it is not clipped."""
    project, _source = _seed_project(
        tmp_path,
        make_source_repo,
        monkeypatch,
        skills={"domain-modeling": LONG_PATH, "read": "skills/read"},
    )
    run = run_in_pty(["list"], cwd=project, width=80)
    assert run.exit_code == 0, run.err
    assert "…" not in run.text
    assert parse_tables(run.lines)[1].rows[0] == [
        "domain-modeling",
        "unlinked",
        "",
        "tw93/Waza",
        LONG_PATH,
    ]


@pytest.mark.parametrize("command", [["list"], ["sync"], ["doctor"]])
def test_exit_code_semantics_hold_on_both_tracks(
    tmp_path: Path,
    make_source_repo,
    monkeypatch: pytest.MonkeyPatch,
    run_in_pty,
    command: list[str],
) -> None:
    project, _source = _seed_project(tmp_path, make_source_repo, monkeypatch)
    assert run_in_pty(command, cwd=project).exit_code == 0
    assert _run_piped(project, command).returncode == 0
