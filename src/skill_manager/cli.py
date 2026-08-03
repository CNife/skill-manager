"""skill-manager CLI entry point.

Provides the Typer app, command orchestration, text/JSON rendering, and
console_scripts target.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any

import typer
from typer import _click as click
from typer.core import TyperGroup

from skill_manager import __version__, config, links, paths, sources
from skill_manager.config import ConfigError, SkillRef
from skill_manager.links import LinkError
from skill_manager.picker import (
    Picker,
    PickerCancelled,
    QuestionaryPicker,
    SkillChoice,
    SourceChoice,
    stdin_stdout_are_tty,
)
from skill_manager.sources import SourceError

# ── result types ──────────────────────────────────────────────────────────────


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


@dataclass
class ListResult:
    skills: list[SkillStatus]
    # Human-only extras (not serialized to JSON data):
    source_rows: list[tuple[str, str, str]] = field(default_factory=list)
    # (repo, head8_or_dash, "cached"|"missing")
    # Soft warnings for the JSON/human envelope (e.g. unreadable global declaration).
    warnings: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class EnableOutcome:
    action: str  # enabled | already_enabled
    skill: dict[str, str]
    # None = omit key (global scope); bool = project-scope cross-hint.
    enabled_globally: bool | None = None

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


@dataclass
class AvailableSkillsResult:
    skills: list[dict[str, str]]  # {name, repo, path}

    def to_data(self) -> dict[str, Any]:
        return {"skills": self.skills}


# ── errors raised inside run_* for command-layer mapping ──────────────────────


class NotFoundError(Exception):
    """Object not found / no match / ambiguous name."""


class UsageError(Exception):
    """CLI usage error (missing/partial args)."""


@dataclass(frozen=True)
class ScannedSkill:
    """A qualified skill discovered under a cached Source."""

    name: str
    path: str
    description: str


# ── JSON-aware Typer group (usage errors become JSON when --json is set) ──────


_ROOT_HOISTABLE = ("--global", "--json")


def _normalize_argv(argv: list[str]) -> list[str]:
    """Hoist root-only bool flags that appear after the subcommand token.

    ``--global`` and ``--json`` are root options, so ``sync --global`` and
    ``list --json`` would otherwise fail with "No such option". Move every
    occurrence found before the ``--`` separator ahead of the subcommand,
    preserving the relative order of the moved tokens; everything else
    (including all tokens after ``--``) stays exactly where it was.
    """
    hoisted: list[str] = []
    rest: list[str] = []
    after_double_dash = False
    for arg in argv:
        if arg == "--":
            after_double_dash = True
            rest.append(arg)
        elif not after_double_dash and arg in _ROOT_HOISTABLE:
            hoisted.append(arg)
        else:
            rest.append(arg)
    return [*hoisted, *rest]


def _root_json_requested(argv: list[str]) -> bool:
    """True only when ``--json`` appears among root options (before the subcommand).

    Called on already-normalized argv, so any ``--json`` before the ``--``
    separator has been hoisted ahead of the subcommand by ``_normalize_argv``.
    """
    for arg in argv:
        if arg == "--":
            return False
        if arg == "--json":
            return True
        if arg.startswith("-"):
            # Other root flags (--version, --help, ...); keep scanning.
            continue
        # First non-option token is the subcommand (or a bare arg).
        return False
    return False


class SkillManagerGroup(TyperGroup):
    """Typer group that emits JSON usage errors when ``--json`` is on argv."""

    def main(self, args: list[str] | None = None, standalone_mode: bool = True, **kwargs: Any):
        argv = list(args) if args is not None else sys.argv[1:]
        # Hoist --global/--json found after the subcommand so both orders parse.
        normalized = _normalize_argv(argv)
        want_json = _root_json_requested(normalized)
        if not (want_json and standalone_mode):
            return super().main(args=normalized, standalone_mode=standalone_mode, **kwargs)
        try:
            # standalone_mode=False: click turns Exit into a returned int exit code
            # (does not raise), and propagates ClickException for us to format.
            result = super().main(args=normalized, standalone_mode=False, **kwargs)
        except click.ClickException as e:
            click.echo(
                json.dumps(
                    {
                        "ok": False,
                        "error": {
                            "code": "usage_error",
                            "message": e.format_message(),
                        },
                    },
                    ensure_ascii=False,
                )
            )
            raise SystemExit(e.exit_code) from e
        if isinstance(result, int):
            raise SystemExit(result)
        return result


app = typer.Typer(
    name="skill-manager",
    help="Project-scoped declarative skill manager for agent skills.",
    no_args_is_help=True,
    cls=SkillManagerGroup,
)

source_app = typer.Typer(
    help="Manage source repositories (list, add, remove, update, available-skills).",
    no_args_is_help=True,
)
app.add_typer(source_app, name="source")


# ── rendering helpers ─────────────────────────────────────────────────────────


def _is_json(ctx: typer.Context) -> bool:
    return bool(ctx.obj and ctx.obj.get("json"))


def _scope_paths(ctx: typer.Context) -> tuple[Path, Path]:
    """Return (declaration_path, skills_dir) for the active scope.

    ``--global`` targets the user's home as a project: ``~/.skill-manager.json``
    and ``~/.agents/skills/``. The source registry and cache are shared across
    scopes and resolved directly by each command.
    """
    if ctx.obj and ctx.obj.get("global"):
        return paths.global_skills_config_path(), paths.global_skills_dir()
    return paths.project_config_path(), paths.project_skills_dir()


def _referencing_scopes(repo: str) -> list[str]:
    """Scope labels whose skill declarations reference ``repo``.

    Checks the project declaration (cwd) and the global declaration (``~``),
    deduping by resolved path: when the project cwd is the user's home the two
    files coincide and are reported once, labelled ``global``.
    """
    scopes: list[str] = []
    seen: set[Path] = set()
    global_path = paths.global_skills_config_path().resolve()
    for path in (paths.project_config_path(), global_path):
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            decl = config.load_skill_declarations(path)
        except ConfigError:
            continue
        if any(s.repo == repo for s in decl.skills):
            scopes.append("global" if resolved == global_path else "project")
    return scopes


def _load_declarations_for_enable(path: Path) -> config.SkillDeclarations:
    """Load declarations for ``enable``, bootstrapping missing/empty files.

    ``enable`` is the command that introduces skills into a scope, so a
    missing or zero-byte declaration file is treated as an empty config rather
    than an error. Global and project scopes behave the same here; when the
    project cwd is the user's home the two paths coincide and are handled once.
    Malformed JSON is still reported as a config error.
    """
    if not path.is_file():
        return config.SkillDeclarations(skills=[])
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return config.SkillDeclarations(skills=[])
    return config.load_skill_declarations(path)


def _emit_json(payload: dict[str, Any]) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False))


def _success(
    ctx: typer.Context,
    data: dict[str, Any],
    *,
    warnings: list[dict[str, str]] | None = None,
) -> None:
    if _is_json(ctx):
        payload: dict[str, Any] = {"ok": True, "data": data}
        if warnings:
            payload["warnings"] = warnings
        _emit_json(payload)


def _fail(ctx: typer.Context, code: str, message: str, exit_code: int = 1) -> None:
    if _is_json(ctx):
        _emit_json({"ok": False, "error": {"code": code, "message": message}})
    else:
        typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(exit_code)


def _error_code(exc: Exception) -> str:
    if isinstance(exc, UsageError):
        return "usage_error"
    if isinstance(exc, NotFoundError):
        return "not_found"
    if isinstance(exc, ConfigError):
        return "config_error"
    if isinstance(exc, SourceError):
        return "source_error"
    if isinstance(exc, LinkError):
        return "config_error"
    raise TypeError(f"unmapped exception type: {type(exc).__name__}") from exc


def _handle_command_error(ctx: typer.Context, exc: Exception) -> None:
    code = _error_code(exc)
    exit_code = 2 if code == "usage_error" else 1
    _fail(ctx, code, str(exc), exit_code=exit_code)


# ── root callback ─────────────────────────────────────────────────────────────


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"skill-manager {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Show version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
    json_mode: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Emit a single JSON object on stdout (implies non-interactive).",
        ),
    ] = False,
    global_scope: Annotated[
        bool,
        typer.Option(
            "--global",
            help="Operate on user-global skills (~/.skill-manager.json, ~/.agents/skills/).",
        ),
    ] = False,
) -> None:
    """Project-scoped declarative skill manager for agent skills."""
    ctx.ensure_object(dict)
    ctx.obj["json"] = json_mode
    ctx.obj["global"] = global_scope


# ── domain runners ────────────────────────────────────────────────────────────


def run_sync(
    project_config: Path,
    global_config_path: Path,
    cache_root: Path,
    skills_dir: Path,
    *,
    url_resolver: Callable[[str], str] | None = None,
    emit: Callable[[str], None] | None = None,
) -> SyncResult:
    """Orchestrate sync: pull (or clone) every declared source, link skills.

    Sync is the only command that updates already cached content: each repo is
    pulled with ``--ff-only``; a missing cache is cloned instead. A start line
    (``pulling <repo>...``) is emitted before every pull and a result line
    (``pulled <old8> → <new8>`` / ``up-to-date <head8>``) after.

    ``url_resolver`` defaults to ``sources.repo_url`` (GitHub HTTPS); tests pass
    a ``file://`` resolver to use local repos as offline GitHub stand-ins.

    When ``emit`` is provided, human-readable progress lines are streamed.
    Always returns a ``SyncResult`` for structured consumers.
    """
    resolver = url_resolver or sources.repo_url
    proj = config.load_skill_declarations(project_config)
    repos = config.derived_sources(proj)
    global_cfg = config.load_global_config(global_config_path)
    result = SyncResult()
    for repo in repos:
        if (cache_root / repo).is_dir():
            if emit is not None:
                emit(f"pulling {repo}...")
            old, new = sources.pull_source(repo, global_cfg, cache_root)
            if old != new:
                result.sources.append(
                    SourceEnsured(repo=repo, commit=new, action="updated", old_commit=old)
                )
                if emit is not None:
                    emit(f"pulled {repo} ({old[:8]} → {new[:8]})")
            else:
                result.sources.append(SourceEnsured(repo=repo, commit=new, action="up_to_date"))
                if emit is not None:
                    emit(f"up-to-date {repo} ({new[:8]})")
        else:
            if emit is not None:
                emit(f"cloning {repo}...")
            head = sources.clone_source(repo, global_cfg, cache_root, url=resolver(repo))
            result.sources.append(SourceEnsured(repo=repo, commit=head, action="cloned"))
            if emit is not None:
                emit(f"cloned {repo} ({head[:8]})")
    config.save_global_config(global_config_path, global_cfg)
    for skill in proj.skills:
        link_result = links.ensure_link(skill, cache_root, skills_dir)
        result.links.append(LinkDone(name=skill.name, action=link_result.action))
        if emit is not None:
            emit(f"{link_result.action} {skill.name} -> {link_result.target}")
    return result


def _link_status(skill: SkillRef, cache_root: Path, skills_dir: Path) -> str:
    link = skills_dir / skill.name
    target_path = (cache_root / skill.repo / skill.path).resolve()
    if link.is_symlink():
        points_to_declared = links.link_points_to(link, target_path)
        if points_to_declared and link.exists():
            return "linked"
        if points_to_declared:
            return "broken"
        if link.exists():
            return "external"
        return "broken"
    return "unlinked"


def _is_global_scope(declaration_path: Path) -> bool:
    """True when the active declaration is the user-global skills file.

    Uses resolved paths so cwd==$HOME (home-as-project) is treated as global
    scope even without ``--global``: there is no separate "other" scope to hint.
    """
    return declaration_path.resolve() == paths.global_skills_config_path().resolve()


def _load_global_enabled_hint(
    declaration_path: Path,
) -> tuple[frozenset[str] | None, list[dict[str, str]]]:
    """Load global skill names for the project→global non-blocking hint.

    Returns ``(None, [])`` when the active scope *is* global (omit the field).
    For project scope, returns ``(names, warnings)`` where missing file ≡ empty
    names, and an unreadable/malformed global declaration yields empty names plus
    a soft ``global_config_error`` warning (command continues).
    """
    if _is_global_scope(declaration_path):
        return None, []
    global_decl = paths.global_skills_config_path()
    if not global_decl.is_file():
        return frozenset(), []
    try:
        decl = config.load_skill_declarations(global_decl)
    except (ConfigError, OSError, UnicodeDecodeError) as exc:
        return frozenset(), [{"code": "global_config_error", "message": str(exc)}]
    return frozenset(s.name for s in decl.skills), []


def run_list(
    project_config: Path,
    global_config_path: Path,
    cache_root: Path,
    skills_dir: Path,
) -> ListResult:
    """Collect declared skills with link status (and human-only source rows)."""
    proj = config.load_skill_declarations(project_config)
    global_cfg = config.load_global_config(global_config_path)
    source_rows: list[tuple[str, str, str]] = []
    for repo in config.derived_sources(proj):
        cached = (cache_root / repo).is_dir()
        src = global_cfg.sources.get(repo)
        head = src.commit[:8] if src else "-"
        source_rows.append((repo, head, "cached" if cached else "missing"))
    global_names, warnings = _load_global_enabled_hint(project_config)
    skill_statuses = [
        SkillStatus(
            name=skill.name,
            repo=skill.repo,
            path=skill.path,
            link=_link_status(skill, cache_root, skills_dir),
            enabled_globally=(skill.name in global_names) if global_names is not None else None,
        )
        for skill in proj.skills
    ]
    return ListResult(skills=skill_statuses, source_rows=source_rows, warnings=warnings)


def _list_cached_repos(cache_root: Path) -> list[str]:
    """List cached source repos (owner/repo) in the cache directory."""
    if not cache_root.is_dir():
        return []
    repos: list[str] = []
    for owner_dir in sorted(cache_root.iterdir()):
        if not owner_dir.is_dir():
            continue
        for repo_dir in sorted(owner_dir.iterdir()):
            if repo_dir.is_dir():
                repos.append(f"{owner_dir.name}/{repo_dir.name}")
    return repos


# Directories skipped during default Source skill discovery (noise trees).
_SKIP_DIR_NAMES = frozenset({"node_modules", "dist", "build", "__pycache__"})

_ALL_HINT = " (hint: use --all to include hidden or internal skills)"


def _frontmatter_lines(text: str) -> list[str] | None:
    """Return YAML frontmatter body lines, or None if missing/unclosed."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return None
    return lines[1:end_idx]


def _top_level_string_fields(fm_lines: list[str]) -> dict[str, str]:
    """Extract top-level simple ``key: value`` string scalars from FM lines."""
    fields: dict[str, str] = {}
    for raw in fm_lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        # Only spaces count as YAML indentation here.
        indent = len(raw) - len(raw.lstrip(" "))
        if indent > 0:
            continue
        if ":" not in raw:
            continue
        key, _, val = raw.partition(":")
        key = key.strip()
        if not key:
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        fields[key] = val
    return fields


def _metadata_internal_is_true(fm_lines: list[str]) -> bool:
    """Parse a minimal YAML subset: top-level metadata.internal == true."""
    in_metadata = False
    metadata_indent: int | None = None
    for raw in fm_lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        # Only spaces count as YAML indentation here.
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()
        if in_metadata and metadata_indent is not None:
            if indent <= metadata_indent:
                in_metadata = False
            else:
                if stripped.startswith("internal:"):
                    value = stripped[len("internal:") :].strip()
                    # YAML 1.2 boolean true only (true/True/TRUE).
                    return value in {"true", "True", "TRUE"}
                continue
        if stripped == "metadata:" or stripped.startswith("metadata:"):
            rest = stripped[len("metadata:") :].strip()
            if rest:
                # Inline / non-mapping forms are not treated as internal.
                return False
            in_metadata = True
            metadata_indent = indent
    return False


def _qualify_skill(skill_md: Path) -> tuple[str, str, bool] | None:
    """Return ``(name, description, is_internal)`` if qualified, else None.

    Qualification: UTF-8 decodable SKILL.md with YAML frontmatter containing
    non-empty string ``name`` and ``description``. Discovery name is FM ``name``.
    ``is_internal`` reflects metadata.internal YAML bool true.
    """
    try:
        text = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    fm_lines = _frontmatter_lines(text)
    if fm_lines is None:
        return None
    fields = _top_level_string_fields(fm_lines)
    name = fields.get("name", "").strip()
    description = fields.get("description", "").strip()
    if not name or not description:
        return None
    return name, description, _metadata_internal_is_true(fm_lines)


def _scan_skills(repo_dir: Path, repo: str, *, include_all: bool = False) -> list[ScannedSkill]:
    """Discover qualified skills under a cached Source checkout.

    Default (include_all=False): skip noise dir names, dot-directories, and
    skills with metadata.internal: true. Always stop recursion at a skill root
    (directory containing SKILL.md). Qualification (UTF-8 + FM name/description)
    always applies; ``--all`` only widens noise/internal filters.
    """
    del repo  # discovery name comes from FM, not the repo id
    skills: list[ScannedSkill] = []

    def visit(current: Path) -> None:
        skill_md = current / "SKILL.md"
        if skill_md.is_file():
            qualified = _qualify_skill(skill_md)
            if qualified is not None:
                name, description, is_internal = qualified
                if not include_all and is_internal:
                    return  # skill-root truncation still applies
                rel = "." if current == repo_dir else str(current.relative_to(repo_dir))
                skills.append(ScannedSkill(name=name, path=rel, description=description))
            return  # skill-root truncation (always)

        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name)
        except OSError:
            return
        for entry in entries:
            if not entry.is_dir():
                continue
            if not include_all and (entry.name in _SKIP_DIR_NAMES or entry.name.startswith(".")):
                continue
            visit(entry)

    visit(repo_dir)
    return skills


def run_enable(
    project_config: Path,
    global_config_path: Path,
    cache_root: Path,
    skills_dir: Path,
    *,
    repo: str | None = None,
    names: list[str] | None = None,
    include_all: bool = False,
    url_resolver: Callable[[str], str] | None = None,
    emit: Callable[[str], None] | None = None,
    picker: Picker | None = None,
) -> EnableResult:
    """Enable one or more skills interactively or non-interactively.

    Non-interactive (batch) when ``repo`` and at least one name are provided.
    Interactive when both are omitted. ``repo`` without names is a usage error.
    ``include_all`` widens Source skill discovery (hidden / internal). The batch
    is atomic: if any requested name is invalid, nothing is applied.

    ``picker`` is the interactive adapter (default: questionary UI). Tests inject
    a fake. Cancel raises ``PickerCancelled``.
    """
    names = list(names or [])
    if repo is None and not names:
        return _enable_interactive(
            project_config,
            global_config_path,
            cache_root,
            skills_dir,
            include_all=include_all,
            url_resolver=url_resolver,
            emit=emit,
            picker=picker,
        )
    if repo is None or not names:
        raise click.exceptions.UsageError(
            "enable requires REPO and at least one NAME, or neither for interactive mode"
        )
    return _enable_noninteractive(
        project_config,
        global_config_path,
        cache_root,
        skills_dir,
        repo=repo,
        names=names,
        include_all=include_all,
        url_resolver=url_resolver,
        emit=emit,
    )


def _emit(emit: Callable[[str], None] | None, message: str) -> None:
    """Send a human progress line when an emit callback is provided."""
    if emit is not None:
        emit(message)


def _require_interactive_tty(*, command: str) -> None:
    """Fail interactive path when stdin/stdout are not TTYs (exit 1)."""
    if not stdin_stdout_are_tty():
        if command == "enable":
            msg = "interactive enable requires a TTY; pass REPO and NAME(s), or run in a terminal."
        else:
            msg = "interactive disable requires a TTY; pass NAME(s), or run in a terminal."
        raise NotFoundError(msg)


def _resolve_picker(picker: Picker | None, *, command: str) -> Picker:
    if picker is not None:
        return picker
    _require_interactive_tty(command=command)
    return QuestionaryPicker()


def _enable_interactive(
    project_config: Path,
    global_config_path: Path,
    cache_root: Path,
    skills_dir: Path,
    *,
    include_all: bool,
    url_resolver: Callable[[str], str] | None,
    emit: Callable[[str], None] | None,
    picker: Picker | None,
) -> EnableResult:
    # Guard TTY before any cache scan (injected picker skips the check).
    ui = _resolve_picker(picker, command="enable")

    repos = _list_cached_repos(cache_root)
    if not repos:
        raise NotFoundError(
            "No cached repos found. Run 'skill-manager sync' first to populate the cache."
        )

    # Build source choices with qualified skill counts (0-skill sources listed).
    source_choices: list[SourceChoice] = []
    skills_by_repo: dict[str, list[ScannedSkill]] = {}
    for r in repos:
        scanned = _scan_skills(cache_root / r, r, include_all=include_all)
        skills_by_repo[r] = scanned
        source_choices.append(SourceChoice(repo=r, skill_count=len(scanned)))

    selected_repo = ui.select_source(source_choices)
    skills = skills_by_repo.get(selected_repo)
    if skills is None:
        # Defensive: picker returned an unknown repo.
        raise NotFoundError(f"source repo {selected_repo!r} is not cached")
    if not skills:
        raise NotFoundError(f"No qualified skills found in {selected_repo}")

    proj = _load_declarations_for_enable(project_config)
    locked = {s.name for s in proj.skills}
    cross_hint = _load_global_enabled_hint(project_config)
    global_names, warnings = cross_hint
    if warnings and emit is not None:
        for w in warnings:
            emit(f"Warning: {w['message']}")
    global_set = global_names or frozenset()
    skill_choices = sorted(
        [
            SkillChoice(
                name=s.name,
                path=s.path,
                description=s.description,
                locked=s.name in locked,
                enabled_globally=s.name in global_set,
            )
            for s in skills
        ],
        key=lambda c: c.name,
    )
    selected_names = ui.select_skills_to_enable(skill_choices)

    # Map selected names to first matching path in scan order; skip locked;
    # dedupe by name keeping first-in-list order.
    path_by_name: dict[str, str] = {}
    for s in skills:
        path_by_name.setdefault(s.name, s.path)

    resolved: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name in selected_names:
        if name in locked or name in seen:
            continue
        if name not in path_by_name:
            continue
        seen.add(name)
        resolved.append((name, path_by_name[name]))

    if not resolved:
        _emit(emit, "Nothing to enable.")
        return EnableResult(outcomes=[], sync=None, warnings=warnings)

    return _enable_apply_batch(
        project_config,
        global_config_path,
        cache_root,
        skills_dir,
        repo=selected_repo,
        resolved=resolved,
        url_resolver=url_resolver,
        emit=emit,
        cross_hint=cross_hint,
        emit_hint_warnings=False,
    )


def _enable_noninteractive(
    project_config: Path,
    global_config_path: Path,
    cache_root: Path,
    skills_dir: Path,
    *,
    repo: str,
    names: list[str],
    include_all: bool,
    url_resolver: Callable[[str], str] | None,
    emit: Callable[[str], None] | None,
) -> EnableResult:
    proj = _load_declarations_for_enable(project_config)
    enabled = {s.name for s in proj.skills}
    # Dedupe requested names, preserving first-seen order.
    unique_names = list(dict.fromkeys(names))
    needs_resolution = [n for n in unique_names if n not in enabled]

    # Resolve only the not-yet-enabled names against the cached Source. Atomic:
    # collect every failure and abort the whole batch before any write.
    resolved_paths: dict[str, str] = {}
    failures: list[str] = []
    has_not_found = False
    if needs_resolution:
        repo_dir = cache_root / repo
        if not repo_dir.is_dir():
            # enable may clone a missing source (never pull). Registration in
            # the global config happens later in _enable_apply_batch, which
            # reloads from disk; this in-memory registration is only for
            # clone_source's contract.
            global_cfg = config.load_global_config(global_config_path)
            resolver = url_resolver or sources.repo_url
            _emit(emit, f"cloning {repo}...")
            head = sources.clone_source(repo, global_cfg, cache_root, url=resolver(repo))
            _emit(emit, f"cloned {repo} ({head[:8]})")
            repo_dir = cache_root / repo
        available = _scan_skills(repo_dir, repo, include_all=include_all)
        for skill_name in needs_resolution:
            matches = [s for s in available if s.name == skill_name]
            if not matches:
                has_not_found = True
                failures.append(f"skill {skill_name!r} not found in cached repo {repo!r}")
            elif len(matches) > 1:
                paths_list = ", ".join(s.path for s in matches)
                failures.append(
                    f"skill {skill_name!r} is ambiguous in {repo!r}; matching paths: {paths_list}"
                )
            else:
                resolved_paths[skill_name] = matches[0].path
        if failures:
            msg = "; ".join(failures)
            if has_not_found and not include_all:
                msg += _ALL_HINT
            raise NotFoundError(msg)

    # Rebuild request order; already-enabled names carry an empty path (the
    # apply phase reads their existing declaration instead).
    resolved = [(n, "" if n in enabled else resolved_paths[n]) for n in unique_names]
    return _enable_apply_batch(
        project_config,
        global_config_path,
        cache_root,
        skills_dir,
        repo=repo,
        resolved=resolved,
        url_resolver=url_resolver,
        emit=emit,
    )


def _enable_apply_batch(
    project_config: Path,
    global_config_path: Path,
    cache_root: Path,
    skills_dir: Path,
    *,
    repo: str,
    resolved: list[tuple[str, str]],
    url_resolver: Callable[[str], str] | None,
    emit: Callable[[str], None] | None,
    cross_hint: tuple[frozenset[str] | None, list[dict[str, str]]] | None = None,
    emit_hint_warnings: bool = True,
) -> EnableResult:
    """Commit a validated, deduped batch of (name, path) skills.

    Already-enabled names are idempotent successes (their path entry is
    ignored); new names are appended. When at least one skill was added, the
    target repo is ensured cloned (never pulled — only sync and source update
    update cached content) and the new skills are linked.

    ``cross_hint`` is the optional preloaded ``_load_global_enabled_hint`` result
    so interactive enable can reuse one load for picker rows and apply.
    """
    proj = _load_declarations_for_enable(project_config)
    enabled = {s.name: s for s in proj.skills}
    if cross_hint is None:
        global_names, warnings = _load_global_enabled_hint(project_config)
    else:
        global_names, warnings = cross_hint
    if emit_hint_warnings and warnings and emit is not None:
        for w in warnings:
            emit(f"Warning: {w['message']}")
    outcomes: list[EnableOutcome] = []
    added = 0
    added_refs: list[SkillRef] = []
    for skill_name, skill_path in resolved:
        hint = (skill_name in global_names) if global_names is not None else None
        existing = enabled.get(skill_name)
        if existing is not None:
            _emit(emit, f"Skill {skill_name!r} already enabled")
            if hint:
                _emit(emit, f"Skill {skill_name!r} also enabled globally")
            outcomes.append(
                EnableOutcome(
                    action="already_enabled",
                    skill={"name": existing.name, "repo": existing.repo, "path": existing.path},
                    enabled_globally=hint,
                )
            )
            continue
        skill_ref = SkillRef(name=skill_name, repo=repo, path=skill_path)
        proj.skills.append(skill_ref)
        added_refs.append(skill_ref)
        _emit(emit, f"Added {skill_name} ({repo}:{skill_path}) to {project_config}")
        if hint:
            _emit(emit, f"Skill {skill_name!r} also enabled globally")
        outcomes.append(
            EnableOutcome(
                action="enabled",
                skill={"name": skill_name, "repo": repo, "path": skill_path},
                enabled_globally=hint,
            )
        )
        added += 1

    sync_result = None
    if added:
        config.save_skill_declarations(project_config, proj)
        global_cfg = config.load_global_config(global_config_path)
        was_registered = repo in global_cfg.sources
        resolver = url_resolver or sources.repo_url
        head = sources.clone_source(repo, global_cfg, cache_root, url=resolver(repo))
        config.save_global_config(global_config_path, global_cfg)
        sync_result = SyncResult(
            sources=[
                SourceEnsured(
                    repo=repo,
                    commit=head,
                    action="up_to_date" if was_registered else "cloned",
                )
            ]
        )
        for skill_ref in added_refs:
            link_result = links.ensure_link(skill_ref, cache_root, skills_dir)
            sync_result.links.append(LinkDone(name=skill_ref.name, action=link_result.action))
            _emit(emit, f"{link_result.action} {skill_ref.name} -> {link_result.target}")
    return EnableResult(outcomes=outcomes, sync=sync_result, warnings=warnings)


def run_disable(
    project_config: Path,
    global_config_path: Path,
    cache_root: Path,
    skills_dir: Path,
    *,
    names: list[str] | None = None,
    emit: Callable[[str], None] | None = None,
    picker: Picker | None = None,
) -> DisableResult:
    """Disable one or more skills (interactive when ``names`` is empty).

    Lenient: disabling a name that is not enabled is an idempotent no-op
    reported per name, never an error. Declarations are removed and the config
    saved once for the whole batch.

    ``picker`` is the interactive adapter (default: questionary UI). Cancel
    raises ``PickerCancelled``.
    """
    del global_config_path  # shared signature with other runners; unused here
    names = list(names or [])
    proj = config.load_skill_declarations(project_config)

    if not names:
        if not proj.skills:
            _emit(emit, "No enabled skills to disable.")
            return DisableResult(outcomes=[])
        ui = _resolve_picker(picker, command="disable")
        ordered_names = ui.select_skills_to_disable(sorted(s.name for s in proj.skills))
        if not ordered_names:
            _emit(emit, "Nothing to disable.")
            return DisableResult(outcomes=[])
        by_name = {s.name: s for s in proj.skills}
        return _disable_apply_batch(
            project_config, cache_root, skills_dir, proj, ordered_names, by_name, emit=emit
        )

    by_name = {s.name: s for s in proj.skills}
    ordered_names = list(dict.fromkeys(names))
    return _disable_apply_batch(
        project_config, cache_root, skills_dir, proj, ordered_names, by_name, emit=emit
    )


def _disable_apply_batch(
    project_config: Path,
    cache_root: Path,
    skills_dir: Path,
    proj: config.SkillDeclarations,
    ordered_names: list[str],
    by_name: dict[str, SkillRef],
    *,
    emit: Callable[[str], None] | None,
) -> DisableResult:
    """Disable the given names in order; report each as disabled or not_enabled.

    Removes all disabled declarations and saves the config once at the end.
    """
    outcomes: list[DisableOutcome] = []
    disabled_names: set[str] = set()
    for name in ordered_names:
        skill = by_name.get(name)
        if skill is None:
            _emit(emit, f"Skill {name!r} not enabled")
            outcomes.append(DisableOutcome(action="not_enabled", skill={"name": name}))
            continue
        _emit(emit, f"Removed {skill.name} from {project_config}")
        link_removed = _clean_link(cache_root, skills_dir, skill, emit)
        outcomes.append(
            DisableOutcome(
                action="disabled",
                skill={"name": skill.name, "repo": skill.repo, "path": skill.path},
                link_removed=link_removed,
            )
        )
        disabled_names.add(name)
    if disabled_names:
        proj.skills = [s for s in proj.skills if s.name not in disabled_names]
        config.save_skill_declarations(project_config, proj)
    return DisableResult(outcomes=outcomes)


def _clean_link(
    cache_root: Path,
    skills_dir: Path,
    skill: SkillRef,
    emit: Callable[[str], None] | None,
) -> bool:
    """Remove the managed symlink for ``skill`` if it points at the cached source.

    Returns True when a link was removed. External symlinks and non-symlink
    entries are left untouched (reported, not an error).
    """
    link = skills_dir / skill.name
    target_path = (cache_root / skill.repo / skill.path).resolve()
    if link.is_symlink() and links.link_points_to(link, target_path):
        link.unlink()
        _emit(emit, f"Removed symlink {link}")
        return True
    if link.is_symlink():
        _emit(emit, f"Skipped {link}: points elsewhere (not managed by skill-manager)")
    elif link.exists():
        _emit(emit, f"Skipped {link}: not a symlink")
    return False


def run_available_skills(
    cache_root: Path,
    *,
    repo: str | None = None,
    include_all: bool = False,
) -> AvailableSkillsResult:
    """List skills found in cached source repos (does not read project config)."""
    if repo is not None:
        config.validate_repo(repo, "source available-skills")
        repo_dir = cache_root / repo
        if not repo_dir.is_dir():
            raise NotFoundError(f"source repo {repo!r} is not cached")
        skills = [
            {"name": s.name, "repo": repo, "path": s.path}
            for s in _scan_skills(repo_dir, repo, include_all=include_all)
        ]
        return AvailableSkillsResult(skills=skills)

    skills: list[dict[str, str]] = []
    for cached_repo in _list_cached_repos(cache_root):
        repo_dir = cache_root / cached_repo
        for s in _scan_skills(repo_dir, cached_repo, include_all=include_all):
            skills.append({"name": s.name, "repo": cached_repo, "path": s.path})
    return AvailableSkillsResult(skills=skills)


# ── top-level commands ────────────────────────────────────────────────────────


@app.command()
def sync(ctx: typer.Context) -> None:
    """Sync declared skills into the scope's .agents/skills/ dir."""
    try:
        emit = None if _is_json(ctx) else typer.echo
        decl_path, skills_dir = _scope_paths(ctx)
        result = run_sync(
            decl_path,
            paths.config_file(),
            paths.repos_cache_dir(),
            skills_dir,
            emit=emit,
        )
        _success(ctx, result.to_data())
    except (ConfigError, SourceError, LinkError, NotFoundError, UsageError) as e:
        _handle_command_error(ctx, e)


@app.command(name="list")
def list_(ctx: typer.Context) -> None:
    """List declared sources and skills with status."""
    try:
        decl_path, skills_dir = _scope_paths(ctx)
        result = run_list(
            decl_path,
            paths.config_file(),
            paths.repos_cache_dir(),
            skills_dir,
        )
        if _is_json(ctx):
            skills_payload: list[dict[str, Any]] = []
            for s in result.skills:
                row: dict[str, Any] = {
                    "name": s.name,
                    "repo": s.repo,
                    "path": s.path,
                    "link": s.link,
                }
                if s.enabled_globally is not None:
                    row["enabled_globally"] = s.enabled_globally
                skills_payload.append(row)
            _success(ctx, {"skills": skills_payload}, warnings=result.warnings or None)
        else:
            for w in result.warnings:
                typer.echo(f"Warning: {w['message']}", err=True)
            typer.echo("Sources:")
            for repo, head, status in result.source_rows:
                typer.echo(f"  {repo}  {head}  {status}")
            typer.echo("Skills:")
            # Legend + name-column pad only when at least one row is also global.
            show_global_legend = any(s.enabled_globally for s in result.skills)
            if show_global_legend:
                typer.echo("  (⊕ = enabled globally)")
            for s in result.skills:
                # Human view keeps present/absent of source path (JSON omits it).
                target_path = (paths.repos_cache_dir() / s.repo / s.path).resolve()
                repo_present = target_path.is_dir()
                # Project list: ⊕ before name when also global; pad peers for alignment.
                if s.enabled_globally:
                    name_cell = f"⊕ {s.name}"
                elif show_global_legend:
                    name_cell = f"  {s.name}"
                else:
                    name_cell = s.name
                typer.echo(
                    f"  {name_cell}  {s.repo}:{s.path}  "
                    f"{s.link}  {'present' if repo_present else 'absent'}"
                )
    except (ConfigError, SourceError, LinkError, NotFoundError, UsageError) as e:
        _handle_command_error(ctx, e)


@app.command()
def enable(
    ctx: typer.Context,
    repo: Annotated[str | None, typer.Argument(help="Source repo (owner/repo).")] = None,
    names: Annotated[
        list[str] | None,
        typer.Argument(help="Skill name(s) within the repo."),
    ] = None,
    include_all: Annotated[
        bool,
        typer.Option(
            "--all",
            help="Include hidden (dot-dir) and internal skills.",
        ),
    ] = False,
) -> None:
    """Enable one or more skills from a cached repo (interactive if no args)."""
    try:
        name_list = list(names or [])
        interactive = repo is None and not name_list
        if _is_json(ctx) and interactive:
            raise click.exceptions.UsageError(
                "enable requires REPO and NAME(s) in --json mode (non-interactive)"
            )
        emit = None if _is_json(ctx) else typer.echo
        decl_path, skills_dir = _scope_paths(ctx)
        # Interactive path builds the default questionary picker inside the runner
        # (after TTY check). Non-interactive never opens a TUI.
        result = run_enable(
            decl_path,
            paths.config_file(),
            paths.repos_cache_dir(),
            skills_dir,
            repo=repo,
            names=name_list,
            include_all=include_all,
            emit=emit,
        )
        if _is_json(ctx):
            _success(ctx, result.to_data(), warnings=result.warnings or None)
        # Text mode: progress / status lines already streamed via emit.
    except PickerCancelled:
        if not _is_json(ctx):
            typer.echo("Cancelled.", err=True)
        raise typer.Exit(1) from None
    except (ConfigError, SourceError, LinkError, NotFoundError, UsageError) as e:
        _handle_command_error(ctx, e)


@app.command()
def disable(
    ctx: typer.Context,
    names: Annotated[
        list[str] | None,
        typer.Argument(help="Enabled skill name(s)."),
    ] = None,
) -> None:
    """Disable one or more enabled skills (interactive if no args)."""
    try:
        name_list = list(names or [])
        interactive = not name_list
        if _is_json(ctx) and interactive:
            raise click.exceptions.UsageError(
                "disable requires NAME(s) in --json mode (non-interactive)"
            )
        emit = None if _is_json(ctx) else typer.echo
        decl_path, skills_dir = _scope_paths(ctx)
        result = run_disable(
            decl_path,
            paths.config_file(),
            paths.repos_cache_dir(),
            skills_dir,
            names=name_list,
            emit=emit,
        )
        if _is_json(ctx):
            _success(ctx, result.to_data())
    except PickerCancelled:
        if not _is_json(ctx):
            typer.echo("Cancelled.", err=True)
        raise typer.Exit(1) from None
    except (ConfigError, SourceError, LinkError, NotFoundError, UsageError) as e:
        _handle_command_error(ctx, e)


# ── source subcommands ────────────────────────────────────────────────────────


@source_app.command(name="list")
def source_list(ctx: typer.Context) -> None:
    """List registered source repositories with status."""
    try:
        global_cfg = config.load_global_config(paths.config_file())
        cache_root = paths.repos_cache_dir()
        if _is_json(ctx):
            data = {
                "sources": [
                    {
                        "repo": repo,
                        "commit": global_cfg.sources[repo].commit,
                        "url": global_cfg.sources[repo].url,
                    }
                    for repo in sorted(global_cfg.sources)
                ]
            }
            _success(ctx, data)
            return
        for repo in sorted(global_cfg.sources):
            src = global_cfg.sources[repo]
            cached = (cache_root / repo).is_dir()
            head = src.commit[:8] if src.commit else "-"
            status = "cached" if cached else "missing"
            typer.echo(f"  {repo:<20} {head:<9} {status:<8} {src.url}")
    except (ConfigError, SourceError, LinkError, NotFoundError, UsageError) as e:
        _handle_command_error(ctx, e)


@source_app.command(name="add")
def source_add(ctx: typer.Context, repo: str) -> None:
    """Add a source repository (clone to cache, register in global config)."""
    try:
        config.validate_repo(repo, "source add")
        global_cfg = config.load_global_config(paths.config_file())
        cache_root = paths.repos_cache_dir()
        if repo in global_cfg.sources and (cache_root / repo).is_dir():
            src = global_cfg.sources[repo]
            if _is_json(ctx):
                _success(
                    ctx,
                    {
                        "action": "already_exists",
                        "repo": repo,
                        "commit": src.commit,
                    },
                )
            else:
                typer.echo(f"source {repo} already exists")
            return
        head = sources.clone_source(repo, global_cfg, cache_root)
        config.save_global_config(paths.config_file(), global_cfg)
        if _is_json(ctx):
            _success(ctx, {"action": "added", "repo": repo, "commit": head})
        else:
            typer.echo(f"added {repo} (HEAD {head[:8]})")
    except (ConfigError, SourceError, LinkError, NotFoundError, UsageError) as e:
        _handle_command_error(ctx, e)


@source_app.command(name="remove")
def source_remove(ctx: typer.Context, repo: str) -> None:
    """Remove a source repository (delete cache + config entry)."""
    try:
        config.validate_repo(repo, "source remove")
        global_cfg = config.load_global_config(paths.config_file())
        cache_root = paths.repos_cache_dir()
        if repo not in global_cfg.sources:
            raise NotFoundError(f"source {repo!r} not found")
        referenced_scopes = _referencing_scopes(repo)
        if referenced_scopes and not _is_json(ctx):
            typer.echo(
                f"warning: {repo!r} still referenced by {', '.join(referenced_scopes)} "
                "skills, links may break",
                err=True,
            )
        sources.remove_source(repo, global_cfg, cache_root)
        config.save_global_config(paths.config_file(), global_cfg)
        if _is_json(ctx):
            _success(ctx, {"action": "removed", "repo": repo})
        else:
            typer.echo(f"removed {repo}")
    except (ConfigError, SourceError, LinkError, NotFoundError, UsageError) as e:
        _handle_command_error(ctx, e)


@source_app.command(name="update")
def source_update(
    ctx: typer.Context,
    repo: Annotated[str | None, typer.Argument(help="Repo to update (default: all)")] = None,
) -> None:
    """Update source repository(ies) to latest (pull --ff-only)."""
    try:
        global_cfg = config.load_global_config(paths.config_file())
        cache_root = paths.repos_cache_dir()
        if repo is not None:
            if repo not in global_cfg.sources:
                raise NotFoundError(f"source {repo!r} not registered (use 'source add' first)")
            repos = [repo]
        else:
            repos = sorted(global_cfg.sources)

        updates: list[dict[str, str]] = []
        if not repos:
            if _is_json(ctx):
                _success(ctx, {"updates": []})
            else:
                typer.echo("No sources registered (use 'source add' first)")
            return

        for r in repos:
            if (cache_root / r).is_dir():
                old, new = sources.pull_source(r, global_cfg, cache_root)
                if old != new:
                    updates.append(
                        {
                            "action": "updated",
                            "repo": r,
                            "commit": new,
                            "old_commit": old,
                            "new_commit": new,
                        }
                    )
                    if not _is_json(ctx):
                        typer.echo(f"updated {r} ({old[:8]} → {new[:8]})")
                else:
                    updates.append({"action": "up_to_date", "repo": r, "commit": new})
                    if not _is_json(ctx):
                        typer.echo(f"up-to-date {r} ({new[:8]})")
            else:
                head = sources.clone_source(r, global_cfg, cache_root)
                updates.append({"action": "cloned", "repo": r, "commit": head})
                if not _is_json(ctx):
                    typer.echo(f"cloned {r} ({head[:8]})")
            config.save_global_config(paths.config_file(), global_cfg)

        if _is_json(ctx):
            _success(ctx, {"updates": updates})
    except (ConfigError, SourceError, LinkError, NotFoundError, UsageError) as e:
        _handle_command_error(ctx, e)


@source_app.command(name="available-skills")
def source_available_skills(
    ctx: typer.Context,
    repo: Annotated[
        str | None,
        typer.Argument(help="Limit scan to one cached repo (owner/repo)."),
    ] = None,
    include_all: Annotated[
        bool,
        typer.Option(
            "--all",
            help="Include hidden (dot-dir) and internal skills.",
        ),
    ] = False,
) -> None:
    """List skills available in cached source repos (ignores project config)."""
    try:
        result = run_available_skills(paths.repos_cache_dir(), repo=repo, include_all=include_all)
        if _is_json(ctx):
            _success(ctx, result.to_data())
            return
        if not result.skills:
            typer.echo("No skills found in cached sources.")
            return
        # Group by repo for human output; omit empty repos (already absent).
        by_repo: dict[str, list[dict[str, str]]] = {}
        for skill in result.skills:
            by_repo.setdefault(skill["repo"], []).append(skill)
        for repo_name in sorted(by_repo):
            typer.echo(f"{repo_name}:")
            for skill in by_repo[repo_name]:
                typer.echo(f"  {skill['name']}  ({skill['path']})")
    except (ConfigError, SourceError, LinkError, NotFoundError, UsageError) as e:
        _handle_command_error(ctx, e)


def main() -> None:
    """Entry point for ``python -m skill_manager``."""
    app()


if __name__ == "__main__":
    main()
