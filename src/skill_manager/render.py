"""Human output track: track decision, Console factory, Rich rendering.

``skill-manager`` writes for two readers, and stdout decides which one it is
writing for:

* **Human output** — stdout is a TTY. Structured data becomes a sectioned table
  (boxed, colored); progress, empty states and errors stay a plain line stream
  that never pretends to be a table.
* **JSON output** — stdout is anything else (pipe, redirect, CI). One compact
  JSON object, no color, no progress lines (see ``cli._emit_json``).

:func:`is_tty` is the single decision point and looks at stdout only — never at
stderr, ``CI`` or ``TERM``. The rendering entry points take the domain result
objects from :mod:`skill_manager.results` plus a ``Console``, so tests can
inject a fixed-width console and assert the layout without a real terminal.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TextIO

from rich import box
from rich.cells import cell_len
from rich.console import Console
from rich.status import Status
from rich.table import Table
from rich.text import Text

from skill_manager.results import (
    AvailableSkillsResult,
    DisableResult,
    DoctorProblem,
    DoctorResult,
    EnableResult,
    ListResult,
    SourceEnsured,
    SourceListResult,
    SourceUpdateResult,
    SyncResult,
)

# ── track decision + console factory ──────────────────────────────────────────


def is_tty(stream: TextIO | None = None) -> bool:
    """True when ``stream`` (stdout by default) is a terminal.

    The one and only track decision: a TTY gets Human output, everything else
    gets JSON output.
    """
    stream = sys.stdout if stream is None else stream
    try:
        return bool(stream.isatty())
    except (AttributeError, OSError, ValueError):
        return False


def make_console(*, stderr: bool = False) -> Console:
    """Console for one output stream, with the track's TTY decision applied.

    stdout is forced terminal exactly when :func:`is_tty` says so. Color is left
    to Rich, which honors ``NO_COLOR`` (structure survives, color does not) and
    ``TERM=dumb``; color-capability probing is never hand-rolled here.
    stderr keeps Rich's own detection: warnings and errors stay plain when
    stderr is a pipe.
    """
    if stderr:
        return Console(stderr=True)
    return Console(file=sys.stdout, force_terminal=is_tty())


def display_path(path: Path) -> str:
    """Render a filesystem path for human output, shortest stable form.

    Prefers a path relative to the current working directory
    (``.skill-manager.json``, ``.agents/skills/read``), then ``~``
    abbreviation under the home directory (``~/.skill-manager.json``), and
    falls back to the absolute path. The path is shown as given — a symlink
    displays as the link itself, never resolved to its target.
    """
    cwd = Path.cwd()
    try:
        return str(path.relative_to(cwd))
    except ValueError:
        pass
    home = Path.home()
    try:
        return f"~/{path.relative_to(home)}"
    except ValueError:
        return str(path)


# ── progress protocol ─────────────────────────────────────────────────────────


class ProgressSink(Protocol):
    """Two-event progress protocol for long-running runners.

    ``start(text)`` announces an operation, ``done(text)`` reports it has
    finished. The human implementation shows ``start`` as one in-place live
    line and drops it at ``done`` — the permanent per-operation result line is
    rendered from the returned domain object, never from this text. The JSON
    track passes ``None`` for the whole sink: nothing transient may reach a
    machine reader.
    """

    def start(self, text: str) -> None: ...

    def done(self, text: str) -> None: ...


class Progress:
    """Human progress sink: one in-place live line, no residue."""

    def __init__(self, console: Console) -> None:
        self._console = console
        self._status: Status | None = None

    def start(self, text: str) -> None:
        self._stop()
        self._status = self._console.status(Text(f"  {text}", style="cyan"), spinner="dots")
        self._status.start()

    def done(self, text: str) -> None:
        self._stop()

    def _stop(self) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None


# ── line stream ───────────────────────────────────────────────────────────────

# Single actionable status word per link -> style.
_STATUS_STYLE: dict[str, str] = {
    "linked": "green",
    "broken": "red",
    "external": "yellow",
    "unlinked": "yellow",
}

# A cached Source is healthy; a missing one is fixed by sync / source add.
_SOURCE_STATUS_STYLE: dict[str, str] = {
    "cached": "green",
    "missing": "yellow",
}

# Trailing mark cell on a skill row (see the legend below).
_MARK_STYLE: dict[str, str] = {
    "⊕": "cyan",
    "⚠": "red",
}

_GLOBAL_LEGEND = (
    "⊕ = also enabled globally, same source   ·   ⚠ = enabled globally from a different source"
)
_GLOBAL_MARK_LEGEND = "⊕ = also enabled globally, same source"

_NO_SKILLS = (
    "No skills enabled yet. Try 'skill-manager enable' or "
    "'skill-manager source available-skills' to discover and enable skills."
)

_SOURCE_ACTION_WORDS = {
    "updated": "updated",
    "up_to_date": "up-to-date",
    "cloned": "cloned",
}
_LINK_ACTION_WORDS = {
    "created": "linked",
    "exists": "already linked",
    "skipped": "skipped",
}


@dataclass(frozen=True)
class Outcome:
    """One line of result feedback.

    ``ok=False`` marks a no-op outcome (already enabled, not enabled …): it is
    drawn with a neutral dot instead of a green check. ``mark`` is a trailing
    glyph cell (⊕ / ⚠), explained by a legend line.
    """

    label: str
    action: str
    detail: str = ""
    mark: str = ""
    ok: bool = True


def _pad_cell(text: str, width: int) -> str:
    """Left-align ``text`` in a ``width``-cell column.

    ``str.ljust`` counts code points, so a CJK label would under-pad and push
    the next column out of line; skill names are user data and may be either.
    """
    return text + " " * (width - cell_len(text))


def render_outcomes(console: Console, outcomes: list[Outcome]) -> None:
    """Print result feedback as an aligned line stream — no table, no frame."""
    if not outcomes:
        return
    label_width = max(cell_len(o.label) for o in outcomes)
    action_width = max(cell_len(o.action) for o in outcomes)
    for outcome in outcomes:
        line = Text("  ")
        if outcome.ok:
            line.append("✓", style="green")
        else:
            line.append("·", style="dim")
        line.append("  ")
        line.append(_pad_cell(outcome.label, label_width))
        if outcome.action:
            line.append("  ")
            line.append(_pad_cell(outcome.action, action_width))
        if outcome.detail:
            line.append("  ")
            line.append(outcome.detail, style="dim")
        if outcome.mark:
            line.append("  ")
            line.append(outcome.mark, style=_MARK_STYLE.get(outcome.mark, ""))
        console.print(line)


def render_note(console: Console, message: str) -> None:
    """Print a one-sentence state line (empty state, cancel)."""
    console.print(Text(message))


def render_error(console: Console, message: str) -> None:
    """Print ``✗  Error: <message>`` on the error stream."""
    line = Text("✗  ", style="red")
    line.append("Error:", style="red")
    line.append(f" {message}")
    console.print(line)


def render_warning(console: Console, message: str) -> None:
    """Print ``Warning: <message>`` on the error stream."""
    line = Text("Warning:", style="yellow")
    line.append(f" {message}")
    console.print(line)


def _legend(console: Console, message: str) -> None:
    console.print(Text(message, style="dim"))


# ── tables ────────────────────────────────────────────────────────────────────

# Columns wider than this hold free-form content (paths, messages): they fold
# instead of being clipped. Narrow columns are primary keys and never wrap.
_FOLD_OVER = 24


def _cell_len(cell: Text | str) -> int:
    return cell.cell_len if isinstance(cell, Text) else len(cell)


def _overlap_mark(enabled_globally: bool | None, global_conflict: bool = False) -> str:
    """Trailing glyph for a cross-scope overlap: ⚠ conflict beats ⊕ benign."""
    if global_conflict:
        return "⚠"
    return "⊕" if enabled_globally else ""


def _section(
    console: Console,
    title: str,
    columns: list[str],
    rows: list[list[Text | str]],
    note: str = "",
) -> None:
    """Print one section: a bold subtitle above a boxed table of ``rows``.

    Rich defaults every column to ``ellipsis``, which clips a primary key such
    as ``name`` first on a narrow terminal. Measured widths therefore split the
    columns: wide ones fold (nothing is lost), narrow ones never wrap.
    """
    console.print(Text(title, style="bold"))
    table = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan", pad_edge=False)
    for index, name in enumerate(columns):
        widest = max([len(name), *(_cell_len(row[index]) for row in rows)])
        if widest > _FOLD_OVER:
            table.add_column(name, overflow="fold", min_width=16)
        else:
            table.add_column(name, no_wrap=True)
    for row in rows:
        table.add_row(*row)
    console.print(table)
    if note:
        _legend(console, note)
    console.print()


def _status_cell(word: str, styles: dict[str, str]) -> Text:
    return Text(word, style=styles.get(word, ""))


# ── views ─────────────────────────────────────────────────────────────────────


def render_list(console: Console, result: ListResult) -> None:
    """``list``: Sources and Skills sections (bare name, mark in its own column)."""
    if not result.skills and not result.source_rows:
        render_note(console, _NO_SKILLS)
        return
    if result.source_rows:
        _section(
            console,
            "Sources",
            ["repo", "HEAD", "status"],
            [
                [repo, head, _status_cell(status, _SOURCE_STATUS_STYLE)]
                for repo, head, status in result.source_rows
            ],
        )
    rows: list[list[Text | str]] = []
    for skill in result.skills:
        mark = _overlap_mark(skill.enabled_globally, skill.global_conflict)
        rows.append(
            [
                skill.name,
                _status_cell(skill.link, _STATUS_STYLE),
                _status_cell(mark, _MARK_STYLE),
                skill.repo,
                skill.path,
            ]
        )
    has_marks = any(s.enabled_globally or s.global_conflict for s in result.skills)
    has_conflict = any(s.global_conflict for s in result.skills)
    _section(
        console,
        "Skills",
        ["name", "status", "global", "repo", "path"],
        rows,
        note=_GLOBAL_LEGEND if has_conflict else (_GLOBAL_MARK_LEGEND if has_marks else ""),
    )


def render_doctor(console: Console, result: DoctorResult) -> None:
    """``doctor``: one section per problem code, repeated fixes collapsed.

    A code whose problems all carry the same fix states it once as the section
    note; a code whose fixes differ (an orphaned source names its own repo)
    keeps a ``fix`` column so no suggestion is silently dropped.
    """
    if not result.problems:
        render_note(console, "No problems found.")
        return
    grouped: dict[str, list[DoctorProblem]] = {}
    for problem in result.problems:
        grouped.setdefault(problem.code, []).append(problem)
    # Codes with more problems first: one section per code explains the code
    # once, so the section order is by volume, then name.
    for code in sorted(grouped, key=lambda c: (-len(grouped[c]), c)):
        group = grouped[code]
        fixes = sorted({p.fix for p in group if p.fix})
        title = f"{code}  ({len(group)})"
        if len(fixes) > 1:
            _section(
                console,
                title,
                ["scope", "detail", "fix"],
                [[p.scope, p.message, p.fix] for p in group],
            )
        else:
            _section(
                console,
                title,
                ["scope", "detail"],
                [[p.scope, p.message] for p in group],
                note=f"fix: {fixes[0]}" if fixes else "",
            )


def _source_detail(source: SourceEnsured) -> str:
    if source.action == "updated" and source.old_commit:
        return f"{source.old_commit[:8]} → {source.commit[:8]}"
    return source.commit[:8]


def _source_outcome(source: SourceEnsured) -> Outcome:
    return Outcome(
        source.repo,
        _SOURCE_ACTION_WORDS.get(source.action or "", ""),
        _source_detail(source),
    )


def _sync_outcomes(result: SyncResult) -> list[Outcome]:
    outcomes = [_source_outcome(source) for source in result.sources]
    outcomes.extend(
        Outcome(
            link.name,
            _LINK_ACTION_WORDS.get(link.action, link.action),
            display_path(link.target) if link.target is not None else "",
        )
        for link in result.links
    )
    return outcomes


def render_sync(console: Console, result: SyncResult) -> None:
    """``sync``: one result line per operation, aligned as one block."""
    if not result.sources and not result.links:
        render_note(console, "Nothing to sync.")
        return
    render_outcomes(console, _sync_outcomes(result))


def render_enable(console: Console, result: EnableResult) -> None:
    """``enable``: one line per outcome, then the sync results it triggered."""
    outcomes = [
        Outcome(
            outcome.skill["name"],
            "enabled" if outcome.action == "enabled" else "already enabled",
            f"{outcome.skill['repo']}:{outcome.skill['path']}",
            mark=_overlap_mark(outcome.enabled_globally, outcome.global_conflict),
            ok=outcome.action == "enabled",
        )
        for outcome in result.outcomes
    ]
    if outcomes:
        render_outcomes(console, outcomes)
        if any(o.mark == "⚠" for o in outcomes):
            _legend(console, _GLOBAL_LEGEND)
        elif any(o.mark for o in outcomes):
            _legend(console, _GLOBAL_MARK_LEGEND)
    if result.sync is not None:
        render_sync(console, result.sync)
    if not outcomes and result.sync is None:
        render_note(console, "Nothing to enable.")


def render_disable(console: Console, result: DisableResult) -> None:
    """``disable``: one line per outcome (lenient — no-ops are dotted)."""
    if not result.outcomes:
        render_note(console, "Nothing to disable.")
        return
    outcomes: list[Outcome] = []
    for outcome in result.outcomes:
        if outcome.action == "disabled":
            outcomes.append(Outcome(outcome.skill["name"], "disabled", outcome.link_note))
        else:
            outcomes.append(Outcome(outcome.skill["name"], "not enabled", ok=False))
    render_outcomes(console, outcomes)


def render_available_skills(console: Console, result: AvailableSkillsResult) -> None:
    """``source available-skills``: one section per repo, skill and path."""
    if not result.skills:
        render_note(console, "No skills found in cached sources.")
        return
    by_repo: dict[str, list[list[Text | str]]] = {}
    for skill in result.skills:
        by_repo.setdefault(skill["repo"], []).append([skill["name"], skill["path"]])
    for repo in sorted(by_repo):
        rows = by_repo[repo]
        _section(console, f"{repo}  ({len(rows)})", ["skill", "path"], rows)


def render_source_list(console: Console, result: SourceListResult) -> None:
    """``source list``: registered sources with HEAD, cache status and URL."""
    if not result.sources:
        render_note(console, "No sources registered (use 'source add' first)")
        return
    rows: list[list[Text | str]] = [
        [
            source.repo,
            source.commit[:8] if source.commit else "-",
            _status_cell("cached" if source.cached else "missing", _SOURCE_STATUS_STYLE),
            Text(source.url, style="dim"),
        ]
        for source in result.sources
    ]
    _section(console, "registered sources", ["repo", "HEAD", "status", "url"], rows)


def render_source_action(
    console: Console, repo: str, action: str, detail: str = "", *, ok: bool = True
) -> None:
    """``source add`` / ``source remove``: a single result line."""
    render_outcomes(console, [Outcome(repo, action, detail, ok=ok)])


def render_source_update(console: Console, result: SourceUpdateResult) -> None:
    """``source update``: one result line per repo, or the empty state."""
    if not result.updates:
        render_note(console, "No sources registered (use 'source add' first)")
        return
    render_outcomes(console, [_source_outcome(update) for update in result.updates])


__all__ = [
    "Outcome",
    "Progress",
    "ProgressSink",
    "display_path",
    "is_tty",
    "make_console",
    "render_available_skills",
    "render_disable",
    "render_doctor",
    "render_enable",
    "render_error",
    "render_list",
    "render_note",
    "render_outcomes",
    "render_source_action",
    "render_source_list",
    "render_source_update",
    "render_sync",
    "render_warning",
]
