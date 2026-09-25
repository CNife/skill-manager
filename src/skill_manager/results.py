"""Domain result objects returned by the ``run_*`` command runners.

Each command returns one of these; ``to_data()`` builds the ``data`` payload of
the JSON envelope, and the human track renders the same object through
``render``. Keeping them out of ``cli.py`` lets the render layer consume them
without importing the CLI (no cycle) and keeps the CLI module about
orchestration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── sync ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SourceEnsured:
    repo: str
    commit: str
    action: str | None = None  # updated | up_to_date | cloned
    old_commit: str | None = None  # set when action == "updated"

    def to_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {"repo": self.repo, "commit": self.commit}
        if self.action is not None:
            data["action"] = self.action
        if self.action == "updated":
            data["old_commit"] = self.old_commit
            data["new_commit"] = self.commit
        return data


@dataclass(frozen=True)
class LinkDone:
    name: str
    action: str  # created | exists | skipped
    target: Path | None = None  # human-track detail; never serialized


@dataclass
class SyncResult:
    sources: list[SourceEnsured] = field(default_factory=list)
    links: list[LinkDone] = field(default_factory=list)

    def to_data(self) -> dict[str, Any]:
        return {
            "sources": [s.to_data() for s in self.sources],
            "links": [{"name": link.name, "action": link.action} for link in self.links],
        }


@dataclass(frozen=True)
class SkillStatus:
    name: str
    repo: str
    path: str
    link: str  # linked | broken | external | unlinked
    # None = omit key (global scope); bool = project-scope cross-hint.
    enabled_globally: bool | None = None
    # True when the same name is enabled globally from a *different* source.
    global_conflict: bool = False


@dataclass
class ListResult:
    skills: list[SkillStatus]
    # Human-only extras (not serialized to JSON data):
    source_rows: list[tuple[str, str, str]] = field(default_factory=list)
    # (repo, head8_or_dash, "cached"|"missing")
    # Soft warnings for the JSON/human envelope (e.g. unreadable global declaration).
    warnings: list[dict[str, str]] = field(default_factory=list)

    def to_data(self) -> dict[str, Any]:
        """JSON payload — the human-only extras (source rows) stay out of it."""
        skills: list[dict[str, Any]] = []
        for skill in self.skills:
            row: dict[str, Any] = {
                "name": skill.name,
                "repo": skill.repo,
                "path": skill.path,
                "link": skill.link,
            }
            if skill.enabled_globally is not None:
                row["enabled_globally"] = skill.enabled_globally
            if skill.global_conflict:
                row["global_conflict"] = True
            skills.append(row)
        return {"skills": skills}


# ── enable / disable ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EnableOutcome:
    action: str  # enabled | already_enabled
    skill: dict[str, str]
    # None = omit key (global scope); bool = project-scope cross-hint.
    enabled_globally: bool | None = None
    # True when the same name is enabled in the other scope from a different
    # source: a pre-existing conflict, marked ⚠. Never serialized.
    global_conflict: bool = False

    def to_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {"action": self.action, "skill": self.skill}
        if self.enabled_globally is not None:
            data["enabled_globally"] = self.enabled_globally
        return data


@dataclass(frozen=True)
class DisableOutcome:
    action: str  # disabled | not_enabled
    skill: dict[str, str]
    link_removed: bool | None = None
    # Human-track detail naming the link that was removed or left alone.
    link_note: str = ""

    def to_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {"action": self.action, "skill": self.skill}
        if self.link_removed is not None:
            data["link_removed"] = self.link_removed
        return data


@dataclass
class EnableResult:
    outcomes: list[EnableOutcome] = field(default_factory=list)
    sync: SyncResult | None = None
    warnings: list[dict[str, str]] = field(default_factory=list)

    def to_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {"results": [o.to_data() for o in self.outcomes]}
        if self.sync is not None:
            data["sync"] = self.sync.to_data()
        return data


@dataclass
class DisableResult:
    outcomes: list[DisableOutcome] = field(default_factory=list)

    def to_data(self) -> dict[str, Any]:
        return {"results": [o.to_data() for o in self.outcomes]}


# ── source ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SourceStatus:
    repo: str
    commit: str
    url: str
    cached: bool

    def to_data(self) -> dict[str, Any]:
        return {"repo": self.repo, "commit": self.commit, "url": self.url}


@dataclass
class SourceListResult:
    sources: list[SourceStatus] = field(default_factory=list)

    def to_data(self) -> dict[str, Any]:
        return {"sources": [s.to_data() for s in self.sources]}


@dataclass
class SourceUpdateResult:
    updates: list[SourceEnsured] = field(default_factory=list)

    def to_data(self) -> dict[str, Any]:
        return {"updates": [u.to_data() for u in self.updates]}


@dataclass
class AvailableSkillsResult:
    skills: list[dict[str, str]]  # {name, repo, path}

    def to_data(self) -> dict[str, Any]:
        return {"skills": self.skills}


# ── doctor ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DoctorProblem:
    code: str
    scope: str  # project | global | config | cache | cross-scope
    message: str
    fix: str
    name: str | None = None
    repo: str | None = None
    path: str | None = None

    def to_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "code": self.code,
            "scope": self.scope,
            "message": self.message,
            "fix": self.fix,
        }
        if self.name is not None:
            data["name"] = self.name
        if self.repo is not None:
            data["repo"] = self.repo
        if self.path is not None:
            data["path"] = self.path
        return data


@dataclass
class DoctorResult:
    problems: list[DoctorProblem]

    def to_data(self) -> dict[str, Any]:
        return {"problems": [p.to_data() for p in self.problems]}
