"""The skill board: one scope's skills as toggleable rows.

Pure data plus the commit plan — no CLI, no terminal UI. The CLI builds a
:class:`Board` from the scope's declarations and a cache scan; the picker
turns it into rows; :func:`plan` is the only place that decides what a commit
means.

Row identity is ``repo:path``. A scope may hold only one declaration per
*name* (see ``config._check_duplicate_names``), while the same name can sit at
several rows — different sources, or duplicate paths inside one source.
Selecting two rows that carry the same name is therefore not a plan at all,
and :func:`plan` rejects it instead of silently picking a winner.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from skill_manager.config import SkillRef


class BoardError(Exception):
    """A selection that cannot be committed (conflict, stale row)."""


@dataclass(frozen=True)
class BoardRow:
    """One toggleable skill row.

    ``declared`` is the checkbox state on open; ``available`` is False only for
    a declared skill whose source cache no longer holds it (its only action is
    unchecking, i.e. dropping the declaration).
    """

    name: str
    repo: str
    path: str
    description: str = ""
    declared: bool = False
    link: str = "unlinked"  # linked | broken | external | unlinked
    enabled_globally: bool = False  # ⊕ same source, other scope
    global_conflict: bool = False  # ⚠ different source, other scope
    available: bool = True

    @property
    def key(self) -> str:
        """Stable row identity: the declaration it would write."""
        return f"{self.repo}:{self.path}"


@dataclass(frozen=True)
class BoardGroup:
    """Rows under one heading. ``repo=None`` is the declared-but-not-cached group."""

    repo: str | None
    rows: tuple[BoardRow, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Board:
    """Every row of one scope, grouped and ordered for display."""

    scope: str  # project | global
    groups: tuple[BoardGroup, ...] = field(default_factory=tuple)

    @property
    def rows(self) -> tuple[BoardRow, ...]:
        return tuple(row for group in self.groups for row in group.rows)

    @property
    def is_empty(self) -> bool:
        return not self.groups


@dataclass(frozen=True)
class BoardPlan:
    """The batch a board selection commits: drop these, add those."""

    enable: tuple[SkillRef, ...] = field(default_factory=tuple)
    disable: tuple[SkillRef, ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        return not self.enable and not self.disable


def counts(board: Board, selected_keys: Iterable[str]) -> tuple[int, int]:
    """``(added, removed)`` for the live "unsaved" counter.

    Lenient by design: an unknown key is ignored, and two rows sharing a name
    still count twice — the counter mirrors the checkboxes, not the plan.
    """
    chosen = set(selected_keys)
    added = sum(1 for row in board.rows if row.key in chosen and not row.declared)
    removed = sum(1 for row in board.rows if row.key not in chosen and row.declared)
    return added, removed


def plan(board: Board, selected_keys: Sequence[str]) -> BoardPlan:
    """Turn checked row keys into the enable/disable batch.

    Raises :class:`BoardError` when two checked rows carry the same name (the
    declaration file cannot hold both), or when a key is not on this board.
    """
    selected = list(dict.fromkeys(selected_keys))
    by_key = {row.key: row for row in board.rows}
    for key in selected:
        if key not in by_key:
            raise BoardError(f"selection refers to a row that is no longer listed: {key}")
    chosen = set(selected)

    seen: dict[str, BoardRow] = {}
    for row in board.rows:
        if row.key not in chosen:
            continue
        previous = seen.get(row.name)
        if previous is not None and previous.key != row.key:
            raise BoardError(
                f"skill {row.name!r} is checked from both {previous.repo}:{previous.path} and "
                f"{row.repo}:{row.path}; check one of them"
            )
        seen[row.name] = row

    enable: list[SkillRef] = []
    disable: list[SkillRef] = []
    for row in board.rows:
        picked = row.key in chosen
        ref = SkillRef(name=row.name, repo=row.repo, path=row.path)
        if row.declared and not picked:
            disable.append(ref)
        elif not row.declared and picked and row.available:
            enable.append(ref)
    return BoardPlan(enable=tuple(enable), disable=tuple(disable))
