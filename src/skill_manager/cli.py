"""skill-manager CLI entry point.

Command orchestration plus the JSON envelope. Everything a human reads is
rendered by :mod:`skill_manager.render`; stdout decides which of the two
tracks runs (see ``render.is_tty``).
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Any

import typer
from rich.console import Console
from typer import _click as click
from typer.core import TyperGroup

from skill_manager import __version__, config, links, paths, render, sources
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
from skill_manager.render import ProgressSink
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
from skill_manager.sources import SourceError

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


# ── two-track Typer group (non-TTY stdout ⇒ JSON, usage errors included) ──────


_ROOT_HOISTABLE = ("--global",)


def _normalize_argv(argv: list[str]) -> list[str]:
    """Hoist root-only bool flags that appear after the subcommand token.

    ``--global`` is a root option, so ``sync --global`` would otherwise fail
    with "No such option". Move every occurrence found before the ``--``
    separator ahead of the subcommand, preserving the relative order of the
    moved tokens; everything else (including all tokens after ``--``) stays
    exactly where it was.
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


class SkillManagerGroup(TyperGroup):
    """Typer group whose own errors follow the track stdout selects.

    A usage error on a non-TTY stdout must be the same single JSON object as
    every other failure, so parsing it never needs a second, ASCII-text
    aware reader. ``--help`` / ``--version`` / ``--show-completion`` stay
    click-native in both tracks: they are CLI metadata, not command data.
    """

    def main(self, args: list[str] | None = None, standalone_mode: bool = True, **kwargs: Any):
        argv = list(args) if args is not None else sys.argv[1:]
        # Hoist --global found after the subcommand so both orders parse.
        normalized = _normalize_argv(argv)
        if not (standalone_mode and not render.is_tty()):
            return super().main(args=normalized, standalone_mode=standalone_mode, **kwargs)
        try:
            # standalone_mode=False: click turns Exit into a returned int exit
            # code (does not raise) and propagates ClickException for us.
            result = super().main(args=normalized, standalone_mode=False, **kwargs)
        except click.ClickException as e:
            _emit_json(
                {
                    "ok": False,
                    "error": {"code": "usage_error", "message": e.format_message()},
                }
            )
            raise SystemExit(e.exit_code) from e
        except click.Abort as e:
            # Ctrl-C is never data: keep it out of stdout entirely.
            click.echo("Aborted!", err=True)
            raise SystemExit(1) from e
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

# ── track helpers ─────────────────────────────────────────────────────────────


def _json_track(ctx: typer.Context) -> bool:
    """True when this invocation writes the JSON track (see ``render.is_tty``)."""
    return bool(ctx.obj and ctx.obj.get("json"))


def _error_console() -> Console:
    """Console for human warnings and errors: always the error stream."""
    return render.make_console(stderr=True)


def _human_console(ctx: typer.Context) -> Console | None:
    """Console for the human track, or ``None`` when this run writes JSON."""
    return None if _json_track(ctx) else render.make_console()


def _cancelled_exit(ctx: typer.Context) -> None:
    """Interactive cancel: exit 1, still one JSON object on the JSON track."""
    if _json_track(ctx):
        _emit_json({"ok": False, "error": {"code": "cancelled", "message": "Cancelled."}})
    else:
        render.render_note(_error_console(), "Cancelled.")
    raise typer.Exit(1) from None


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


def _declaration_label(path: Path) -> str:
    """User-facing name of the active declaration scope, for error wording."""
    return "global skills config" if _is_global_scope(path) else "project config"


def _load_declarations_for_enable(path: Path) -> config.SkillDeclarations:
    """Load declarations for ``enable``, bootstrapping missing/empty files.

    ``enable`` is the command that introduces skills into a scope, so a
    missing or zero-byte declaration file is treated as an empty config rather
    than an error. Global and project scopes behave the same here; when the
    project cwd is the user's home the two paths coincide and are handled once.
    Malformed JSON is still reported as a config error, worded for the active
    scope (never "project config" under ``--global``).
    """
    if not path.is_file():
        return config.SkillDeclarations(skills=[])
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return config.SkillDeclarations(skills=[])
    return config.load_skill_declarations(path, label=_declaration_label(path))


def _load_declarations_for_list_sync(path: Path) -> config.SkillDeclarations:
    """Load declarations for ``list``/``sync``: a missing file is empty.

    Mirrors ``enable``'s bootstrap philosophy for the read-only commands: a
    fresh checkout (or ``--global`` before any enable) must not hard-fail on a
    missing declaration file. Only *missing* is tolerated here, not zero-byte
    or malformed content — those still raise ConfigError, worded for the
    active scope.
    """
    if not path.is_file():
        return config.SkillDeclarations(skills=[])
    return config.load_skill_declarations(path, label=_declaration_label(path))


def _emit_json(payload: dict[str, Any]) -> None:
    """Write one compact JSON object to stdout — the whole JSON track."""
    typer.echo(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _success(
    ctx: typer.Context,
    data: dict[str, Any],
    *,
    warnings: list[dict[str, str]] | None = None,
) -> None:
    if not _json_track(ctx):
        return
    payload: dict[str, Any] = {"ok": True, "data": data}
    if warnings:
        payload["warnings"] = warnings
    _emit_json(payload)


def _fail(ctx: typer.Context, code: str, message: str, exit_code: int = 1) -> None:
    if _json_track(ctx):
        _emit_json({"ok": False, "error": {"code": code, "message": message}})
    else:
        render.render_error(_error_console(), message)
    raise typer.Exit(exit_code)


def _warn(message: str) -> None:
    """Human-track soft warning on stderr (the JSON track carries them in data)."""
    render.render_warning(_error_console(), message)


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
    # stdout decides the track: a TTY gets Human output, anything else JSON.
    ctx.obj["json"] = not render.is_tty()
    ctx.obj["global"] = global_scope


# ── domain runners ────────────────────────────────────────────────────────────


def _progress_start(progress: ProgressSink | None, text: str) -> None:
    if progress is not None:
        progress.start(text)


def _progress_done(progress: ProgressSink | None, text: str) -> None:
    if progress is not None:
        progress.done(text)


def run_sync(
    project_config: Path,
    global_config_path: Path,
    cache_root: Path,
    skills_dir: Path,
    *,
    url_resolver: Callable[[str], str] | None = None,
    progress: ProgressSink | None = None,
    result: SyncResult | None = None,
) -> SyncResult:
    """Orchestrate sync: pull (or clone) every declared source, link skills.

    Sync is the only command that updates already cached content: each repo is
    pulled with ``--ff-only``; a missing cache is cloned instead. Every
    operation reports one ``progress.start`` / ``progress.done`` pair; the
    permanent per-operation line is rendered from the returned ``SyncResult``
    (the human track) or not at all (the JSON track passes ``None``).

    ``url_resolver`` defaults to ``sources.repo_url`` (GitHub HTTPS); tests pass
    a ``file://`` resolver to use local repos as offline GitHub stand-ins.
    Callers that want to see work done before a failure may pass their own
    ``result``: it is filled as the sync progresses, so a raised error leaves
    the finished operations behind.
    """
    resolver = url_resolver or sources.repo_url
    proj = _load_declarations_for_list_sync(project_config)
    repos = config.derived_sources(proj)
    global_cfg = config.load_global_config(global_config_path)
    if result is None:
        result = SyncResult()
    for repo in repos:
        if (cache_root / repo).is_dir():
            _progress_start(progress, f"pulling {repo}...")
            old, new = sources.pull_source(repo, global_cfg, cache_root)
            if old != new:
                result.sources.append(
                    SourceEnsured(repo=repo, commit=new, action="updated", old_commit=old)
                )
                _progress_done(progress, f"pulled {repo} ({old[:8]} → {new[:8]})")
            else:
                result.sources.append(SourceEnsured(repo=repo, commit=new, action="up_to_date"))
                _progress_done(progress, f"up-to-date {repo} ({new[:8]})")
        else:
            _progress_start(progress, f"cloning {repo}...")
            head = sources.clone_source(repo, global_cfg, cache_root, url=resolver(repo))
            result.sources.append(SourceEnsured(repo=repo, commit=head, action="cloned"))
            _progress_done(progress, f"cloned {repo} ({head[:8]})")
    config.save_global_config(global_config_path, global_cfg)
    for skill in proj.skills:
        _progress_start(progress, f"linking {skill.name}...")
        link_result = links.ensure_link(skill, cache_root, skills_dir)
        result.links.append(
            LinkDone(name=skill.name, action=link_result.action, target=link_result.target)
        )
        _progress_done(
            progress,
            f"{link_result.action} {skill.name} -> {render.display_path(link_result.target)}",
        )
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
) -> tuple[dict[str, tuple[str, str]] | None, list[dict[str, str]]]:
    """Load global skill declarations as ``name -> (repo, path)``.

    Returns ``(None, [])`` when the active scope *is* global (omit the field).
    For project scope: missing global file ≡ empty mapping, and an
    unreadable/malformed global declaration yields an empty mapping plus a
    soft ``global_config_error`` warning (command continues). Carrying the
    repo lets callers tell same-source overlaps from cross-source conflicts.
    """
    if _is_global_scope(declaration_path):
        return None, []
    global_decl = paths.global_skills_config_path()
    if not global_decl.is_file():
        return {}, []
    try:
        decl = config.load_skill_declarations(global_decl)
    except (ConfigError, OSError, UnicodeDecodeError) as exc:
        return {}, [{"code": "global_config_error", "message": str(exc)}]
    mapping: dict[str, tuple[str, str]] = {}
    for skill in decl.skills:
        mapping.setdefault(skill.name, (skill.repo, skill.path))
    return mapping, []


def _load_project_hint(declaration_path: Path) -> dict[str, tuple[str, str]] | None:
    """Reverse-direction hint for ``--global`` enable (boundary: cwd only).

    When the active declaration is the user-global file, the *other* scope is
    the cwd project: returns its skills as ``name -> (repo, path)``. Returns
    ``None`` when the active scope is not global, or when cwd is the user's
    home (the two declaration files coincide — there is no separate project
    scope). A missing or unreadable cwd project file yields an empty mapping:
    the reverse check degrades to none. Only the cwd project is ever
    inspected; other projects on disk are out of scope by design (documented
    boundary for the --global reverse check).
    """
    if not _is_global_scope(declaration_path):
        return None
    other = paths.project_config_path()
    if other.resolve() == paths.global_skills_config_path().resolve():
        return None
    if not other.is_file():
        return {}
    try:
        decl = config.load_skill_declarations(other)
    except (ConfigError, OSError, UnicodeDecodeError):
        return {}
    mapping: dict[str, tuple[str, str]] = {}
    for skill in decl.skills:
        mapping.setdefault(skill.name, (skill.repo, skill.path))
    return mapping


def run_list(
    project_config: Path,
    global_config_path: Path,
    cache_root: Path,
    skills_dir: Path,
) -> ListResult:
    """Collect declared skills with link status (and human-only source rows)."""
    proj = _load_declarations_for_list_sync(project_config)
    global_cfg = config.load_global_config(global_config_path)
    source_rows: list[tuple[str, str, str]] = []
    for repo in config.derived_sources(proj):
        cached = (cache_root / repo).is_dir()
        src = global_cfg.sources.get(repo)
        head = src.commit[:8] if src else "-"
        source_rows.append((repo, head, "cached" if cached else "missing"))
    global_names, warnings = _load_global_enabled_hint(project_config)
    skill_statuses: list[SkillStatus] = []
    for skill in proj.skills:
        global_hit = global_names.get(skill.name) if global_names is not None else None
        conflict = global_hit is not None and global_hit[0] != skill.repo
        skill_statuses.append(
            SkillStatus(
                name=skill.name,
                repo=skill.repo,
                path=skill.path,
                link=_link_status(skill, cache_root, skills_dir),
                enabled_globally=(global_hit is not None if global_names is not None else None),
                global_conflict=conflict,
            )
        )
        if conflict:
            # Pre-existing cross-scope conflicts warn but never hard-fail
            # (migration-friendly: list/sync stay usable).
            warnings.append(
                {
                    "code": "global_conflict",
                    "message": (
                        f"skill {skill.name!r} is enabled in the global scope from a different "
                        f"source ({global_hit[0]}:{global_hit[1]}); disable one side to "
                        "resolve the conflict"
                    ),
                }
            )
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


def run_doctor(
    project_config: Path,
    global_skills_config: Path,
    global_config_path: Path,
    cache_root: Path,
    project_skills_dir: Path,
    global_skills_dir: Path,
) -> DoctorResult:
    """Diagnose consistency issues across both scopes and infrastructure.

    Read-only and offline: checks declarations, global config, source cache,
    and links in both project and global scopes. Parse failures are recorded
    as problems and the remaining checks continue. Never raises ConfigError.
    """
    problems: list[DoctorProblem] = []
    same_scope = project_config.resolve() == global_skills_config.resolve()

    # ── Load declarations (non-blocking: parse errors become problems) ──
    project_decls: config.SkillDeclarations | None = None
    try:
        project_decls = _load_declarations_for_list_sync(project_config)
    except ConfigError as exc:
        problems.append(
            DoctorProblem(
                code="declaration_parse_error",
                scope="project",
                message=str(exc),
                fix="Fix the declaration file",
            )
        )

    global_decls: config.SkillDeclarations | None = None
    if not same_scope:
        try:
            global_decls = _load_declarations_for_list_sync(global_skills_config)
        except ConfigError as exc:
            problems.append(
                DoctorProblem(
                    code="declaration_parse_error",
                    scope="global",
                    message=str(exc),
                    fix="Fix the declaration file",
                )
            )

    # ── Load global config (non-blocking) ──
    global_cfg: config.GlobalConfig | None = None
    try:
        global_cfg = config.load_global_config(global_config_path)
    except ConfigError as exc:
        problems.append(
            DoctorProblem(
                code="global_config_parse_error",
                scope="config",
                message=str(exc),
                fix="Fix the global config, or delete it and run sync to rebuild",
            )
        )

    # ── XDG environment checks ──
    for var, scope in (("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache")):
        value = os.environ.get(var, "")
        if value and not Path(value).is_absolute():
            problems.append(
                DoctorProblem(
                    code="xdg_path_issue",
                    scope=scope,
                    message=(
                        f"{var} is set to a relative path {value!r} (must be absolute per XDG spec)"
                    ),
                    fix="Check environment variables",
                )
            )

    # ── Cache directory writable check ──
    writable_dir = cache_root if cache_root.exists() else cache_root.parent
    if writable_dir.exists() and not os.access(writable_dir, os.W_OK):
        problems.append(
            DoctorProblem(
                code="cache_dir_not_writable",
                scope="cache",
                message=f"source cache directory is not writable: {writable_dir}",
                fix="Fix directory permissions",
            )
        )

    # ── Source three-way consistency (declarations ↔ global config ↔ cache) ──
    declared_repos: set[str] = set()
    if project_decls is not None:
        declared_repos.update(s.repo for s in project_decls.skills)
    if global_decls is not None:
        declared_repos.update(s.repo for s in global_decls.skills)

    cached_repos = set(_list_cached_repos(cache_root))
    registered_repos: set[str] = set(global_cfg.sources) if global_cfg is not None else set()

    # declared_source_not_registered
    for scope_label, decls in (
        ("project", project_decls),
        ("global", global_decls),
    ):
        if decls is None:
            continue
        sync_cmd = (
            "skill-manager --global sync" if scope_label == "global" else "skill-manager sync"
        )
        for skill in decls.skills:
            if skill.repo not in registered_repos:
                problems.append(
                    DoctorProblem(
                        code="declared_source_not_registered",
                        scope=scope_label,
                        message=(
                            f"skill {skill.name!r} references source {skill.repo!r}"
                            " not registered in global config"
                        ),
                        fix=sync_cmd,
                        name=skill.name,
                        repo=skill.repo,
                    )
                )

    # registered_source_cache_missing
    for repo in sorted(registered_repos):
        if repo not in cached_repos:
            problems.append(
                DoctorProblem(
                    code="registered_source_cache_missing",
                    scope="cache",
                    message=f"source {repo!r} is registered but not cached",
                    fix=f"skill-manager source remove {repo} then source add",
                    repo=repo,
                )
            )

    # orphan_source_registered (registered + cached but no declaration references)
    for repo in sorted(registered_repos & cached_repos):
        if repo not in declared_repos:
            problems.append(
                DoctorProblem(
                    code="orphan_source_registered",
                    scope="config",
                    message=(
                        f"source {repo!r} is registered and cached but no declaration references it"
                    ),
                    fix=f"skill-manager source remove {repo}",
                    repo=repo,
                )
            )

    # orphan_source_cache (cached but not registered)
    for repo in sorted(cached_repos):
        if repo not in registered_repos:
            problems.append(
                DoctorProblem(
                    code="orphan_source_cache",
                    scope="cache",
                    message=f"source {repo!r} is cached but not registered in global config",
                    fix=(
                        f"Remove the cache directory manually, or skill-manager source add {repo}"
                    ),
                    repo=repo,
                )
            )

    # head_drift
    for repo in sorted(registered_repos & cached_repos):
        recorded = global_cfg.sources[repo].commit
        actual = sources.current_head(cache_root, repo)
        if actual is not None and actual != recorded:
            problems.append(
                DoctorProblem(
                    code="head_drift",
                    scope="cache",
                    message=(
                        f"source {repo!r} HEAD drift:"
                        f" global config records {recorded[:8]},"
                        f" cache has {actual[:8]}"
                    ),
                    fix=f"skill-manager source update {repo}",
                    repo=repo,
                )
            )

    # ── Per-scope link and path checks ──
    scope_entries: list[tuple[str, config.SkillDeclarations, Path]] = []
    if project_decls is not None:
        scope_entries.append(("project", project_decls, project_skills_dir))
    if global_decls is not None and not same_scope:
        scope_entries.append(("global", global_decls, global_skills_dir))

    for scope_label, decls, skills_dir in scope_entries:
        sync_cmd = (
            "skill-manager --global sync" if scope_label == "global" else "skill-manager sync"
        )
        declared_names: set[str] = set()

        for skill in decls.skills:
            declared_names.add(skill.name)
            status = _link_status(skill, cache_root, skills_dir)
            if status == "unlinked":
                problems.append(
                    DoctorProblem(
                        code="unlinked",
                        scope=scope_label,
                        message=f"skill {skill.name!r} is declared but not linked",
                        fix=sync_cmd,
                        name=skill.name,
                        repo=skill.repo,
                        path=skill.path,
                    )
                )
            elif status == "broken":
                problems.append(
                    DoctorProblem(
                        code="broken_link",
                        scope=scope_label,
                        message=f"link for skill {skill.name!r} is broken (target missing)",
                        fix=sync_cmd,
                        name=skill.name,
                        repo=skill.repo,
                        path=skill.path,
                    )
                )
            elif status == "external":
                problems.append(
                    DoctorProblem(
                        code="external_link",
                        scope=scope_label,
                        message=(
                            f"link for skill {skill.name!r}"
                            " points to a different target than declared"
                        ),
                        fix=f"Remove the link manually, then {sync_cmd}",
                        name=skill.name,
                        repo=skill.repo,
                        path=skill.path,
                    )
                )

            # declared_path_invalid (only when repo is cached)
            if (cache_root / skill.repo).is_dir():
                target = cache_root / skill.repo / skill.path
                if not target.is_dir() or not (target / "SKILL.md").is_file():
                    problems.append(
                        DoctorProblem(
                            code="declared_path_invalid",
                            scope=scope_label,
                            message=(
                                f"skill {skill.name!r} path {skill.path!r}"
                                f" not found or missing SKILL.md in {skill.repo}"
                            ),
                            fix="Fix the path, or skill-manager source update",
                            name=skill.name,
                            repo=skill.repo,
                            path=skill.path,
                        )
                    )

        # orphan_link (symlinks in skills_dir not in declarations)
        if skills_dir.is_dir():
            for entry in sorted(skills_dir.iterdir()):
                if entry.is_symlink() and entry.name not in declared_names:
                    problems.append(
                        DoctorProblem(
                            code="orphan_link",
                            scope=scope_label,
                            message=(
                                f"link {entry.name!r} exists in {scope_label} skills dir"
                                " but no declaration references it"
                            ),
                            fix="Remove the link manually, or add a declaration with enable",
                            name=entry.name,
                        )
                    )

    # ── Cross-scope conflict ──
    if project_decls is not None and global_decls is not None:
        proj_map: dict[str, tuple[str, str]] = {}
        for s in project_decls.skills:
            proj_map.setdefault(s.name, (s.repo, s.path))
        for s in global_decls.skills:
            if s.name in proj_map:
                p_repo, p_path = proj_map[s.name]
                if p_repo != s.repo or p_path != s.path:
                    problems.append(
                        DoctorProblem(
                            code="cross_scope_conflict",
                            scope="cross-scope",
                            message=(
                                f"skill {s.name!r} declared in both scopes"
                                " with different sources:"
                                f" project={p_repo}:{p_path},"
                                f" global={s.repo}:{s.path}"
                            ),
                            fix=(
                                f"skill-manager disable {s.name} on one side, or align the source"
                            ),
                            name=s.name,
                        )
                    )

    # ── Cache git state ──
    for repo in sorted(cached_repos):
        if sources.detached_head(cache_root, repo):
            problems.append(
                DoctorProblem(
                    code="cache_detached_head",
                    scope="cache",
                    message=f"source {repo!r} is in detached HEAD state",
                    fix="git checkout <branch> or skill-manager source update",
                    repo=repo,
                )
            )
        if sources.is_dirty(cache_root, repo):
            problems.append(
                DoctorProblem(
                    code="cache_dirty",
                    scope="cache",
                    message=f"source {repo!r} has uncommitted changes",
                    fix="git stash or skill-manager source update",
                    repo=repo,
                )
            )

    return DoctorResult(problems=problems)


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
    # Shape guard (same rule as config): the discovery name must be a single
    # path component, never a path like ``../../evil`` that would escape the
    # skills directory when linked.
    if PurePosixPath(name).name != name or name in (".", ".."):
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
    progress: ProgressSink | None = None,
    picker: Picker | None = None,
) -> EnableResult:
    """Enable one or more skills interactively or non-interactively.

    Non-interactive (batch) when ``repo`` and at least one name are provided.
    Interactive when both are omitted. ``repo`` without names is a usage error.

    Repo-less form: a repo identifier is always ``owner/repo`` (contains
    ``/``), so a slash-less first positional argument is treated as the first
    skill name and every name is resolved across all cached sources.
    ``include_all`` widens Source skill discovery (hidden / internal). The
    batch is atomic: if any requested name is invalid, nothing is applied.

    ``picker`` is the interactive adapter (default: questionary UI). Tests inject
    a fake. Cancel raises ``PickerCancelled``.
    """
    names = list(names or [])
    if repo is not None and "/" not in repo:
        # Repo-less mode: the first positional argument is the first name.
        names = [repo, *names]
        repo = None
    if repo is None and not names:
        return _enable_interactive(
            project_config,
            global_config_path,
            cache_root,
            skills_dir,
            include_all=include_all,
            url_resolver=url_resolver,
            progress=progress,
            picker=picker,
        )
    if repo is None:
        if any("/" in name for name in names):
            raise click.exceptions.UsageError(
                "enable in name-resolution mode takes skill names only (no '/' arguments); "
                "use 'enable <repo> <name>' to target a specific source"
            )
        return _enable_noninteractive(
            project_config,
            global_config_path,
            cache_root,
            skills_dir,
            repo=None,
            names=names,
            include_all=include_all,
            url_resolver=url_resolver,
            progress=progress,
        )
    if not names:
        raise click.exceptions.UsageError(
            "enable requires REPO and at least one NAME, or neither for interactive mode"
        )
    config.validate_repo(repo, "enable")
    return _enable_noninteractive(
        project_config,
        global_config_path,
        cache_root,
        skills_dir,
        repo=repo,
        names=names,
        include_all=include_all,
        url_resolver=url_resolver,
        progress=progress,
    )


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
    progress: ProgressSink | None,
    picker: Picker | None,
) -> EnableResult:
    # Guard TTY before any cache scan (injected picker skips the check).
    ui = _resolve_picker(picker, command="enable")

    repos = _list_cached_repos(cache_root)
    if not repos:
        # sync with an empty declaration set is a no-op, so it can no longer
        # populate the cache: point at source add or the auto-cloning enable.
        raise NotFoundError(
            "No cached repos found. Use 'skill-manager source add <repo>' to register a "
            "source, or run 'skill-manager enable <owner/repo> <name>' to clone and "
            "enable a skill directly."
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
        hint = "" if include_all else _ALL_HINT
        raise NotFoundError(f"No qualified skills found in {selected_repo}{hint}")

    proj = _load_declarations_for_enable(project_config)
    locked = {s.name for s in proj.skills}
    cross_hint = _load_global_enabled_hint(project_config)
    global_set = set(cross_hint[0] or {})
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
    selected_paths = ui.select_skills_to_enable(skill_choices)

    # Selection carries path identity: each picked path maps to exactly one
    # row (same-named rows are distinct). Skip locked and unknown paths,
    # dedupe by path keeping first-pick order. Same-name rows at different
    # paths checked together are rejected by _enable_apply_batch (F1 rules).
    by_path: dict[str, SkillChoice] = {c.path: c for c in skill_choices}
    resolved: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for path in selected_paths:
        choice = by_path.get(path)
        if choice is None or choice.locked:
            continue
        if path in seen:
            continue
        seen.add(path)
        resolved.append((choice.name, selected_repo, choice.path))

    if not resolved:
        return EnableResult(outcomes=[], sync=None, warnings=cross_hint[1])

    return _enable_apply_batch(
        project_config,
        global_config_path,
        cache_root,
        skills_dir,
        resolved=resolved,
        url_resolver=url_resolver,
        progress=progress,
        cross_hint=cross_hint,
    )


def _enable_noninteractive(
    project_config: Path,
    global_config_path: Path,
    cache_root: Path,
    skills_dir: Path,
    *,
    repo: str | None,
    names: list[str],
    include_all: bool,
    url_resolver: Callable[[str], str] | None,
    progress: ProgressSink | None,
) -> EnableResult:
    """Resolve and validate a batch of enable requests, then apply it.

    ``repo`` is ``None`` in repo-less mode: every name is resolved against all
    cached sources (never clones). Otherwise arguments resolve inside the one
    repo — cloning a missing source first (never pulling, issue #46).

    Path is the skill identity: an argument containing ``/`` matches a path
    exactly; a pure name matches by FM name first and falls back to a root
    single-segment path. Requests are validated against the current scope's
    declarations: same repo+path as an existing declaration is an idempotent
    already_enabled; the same name from a different source or path is an error
    with a disable-first hint (fixing the silent no-op trap). The batch is
    atomic: every failure is collected and nothing is written.
    """
    proj = _load_declarations_for_enable(project_config)
    enabled_by_name = {s.name: s for s in proj.skills}
    by_repo_path = {(s.repo, s.path): s for s in proj.skills}
    unique_names = list(dict.fromkeys(names))
    resolved: list[tuple[str, str, str]] = []
    failures: list[str] = []
    has_not_found = False
    cloned_repos: set[str] = set()

    if repo is None:
        # Repo-less: scan every cached source once (no cloning).
        scans: list[tuple[str, list[ScannedSkill]]] = [
            (r, _scan_skills(cache_root / r, r, include_all=include_all))
            for r in _list_cached_repos(cache_root)
        ]
        for name in unique_names:
            existing = enabled_by_name.get(name)
            matches = [(r, s.path) for r, scan in scans for s in scan if s.name == name]
            if existing is not None and (not matches or (existing.repo, existing.path) in matches):
                # Already satisfied by the existing declaration (stale cache,
                # or the declaration is one of the candidates): idempotent.
                resolved.append((name, existing.repo, existing.path))
                continue
            if not matches:
                has_not_found = True
                failures.append(
                    f"skill {name!r} not found in any cached source; "
                    "run 'skill-manager source available-skills' to list available skills"
                )
            elif len(matches) > 1:
                candidates = ", ".join(f"{r}:{p}" for r, p in matches)
                failures.append(
                    f"skill {name!r} is ambiguous across sources; matching: {candidates}; "
                    "use 'enable <repo> <name>' or 'enable <repo> <path>' to disambiguate"
                )
            else:
                r, p = matches[0]
                if existing is not None:
                    failures.append(
                        f"skill {name!r} is already enabled from {existing.repo}:{existing.path}; "
                        f"disable it first: skill-manager disable {name}"
                    )
                else:
                    resolved.append((name, r, p))
    else:
        repo_dir = cache_root / repo
        if not repo_dir.is_dir():
            # enable may clone a missing source (never pull). Registration is
            # persisted right after the clone so a later batch failure cannot
            # orphan the cache: the source stays visible to ``source list`` and
            # removable via ``source remove``. _enable_apply_batch reloads from
            # disk and saves again (idempotent, harmless).
            global_cfg = config.load_global_config(global_config_path)
            resolver = url_resolver or sources.repo_url
            _progress_start(progress, f"cloning {repo}...")
            head = sources.clone_source(repo, global_cfg, cache_root, url=resolver(repo))
            _progress_done(progress, f"cloned {repo} ({head[:8]})")
            config.save_global_config(global_config_path, global_cfg)
            cloned_repos.add(repo)
            repo_dir = cache_root / repo
        available = _scan_skills(repo_dir, repo, include_all=include_all)
        by_name_scan: dict[str, list[ScannedSkill]] = {}
        by_path_scan: dict[str, ScannedSkill] = {}
        for skill in available:
            by_name_scan.setdefault(skill.name, []).append(skill)
            by_path_scan.setdefault(skill.path, skill)

        for arg in unique_names:
            if "/" in arg:
                # Path identity: an existing declaration for (repo, path) is
                # idempotent even when the cache went stale.
                hit = by_repo_path.get((repo, arg))
                if hit is not None:
                    resolved.append((hit.name, repo, arg))
                    continue
                skill = by_path_scan.get(arg)
                if skill is None:
                    has_not_found = True
                    failures.append(
                        f"skill path {arg!r} not found in cached repo {repo!r}; "
                        f"run 'skill-manager source available-skills {repo}' to list "
                        "available skills"
                    )
                    continue
                existing = enabled_by_name.get(skill.name)
                if existing is not None:
                    failures.append(
                        f"skill {skill.name!r} is already enabled from "
                        f"{existing.repo}:{existing.path}; disable it first: "
                        f"skill-manager disable {skill.name}"
                    )
                    continue
                resolved.append((skill.name, repo, arg))
                continue
            matches = by_name_scan.get(arg, [])
            if not matches:
                path_skill = by_path_scan.get(arg)
                if path_skill is not None:
                    matches = [path_skill]
            if not matches:
                existing = enabled_by_name.get(arg)
                if existing is not None and existing.repo == repo:
                    # Stale cache: the declaration's repo matches the request;
                    # keep the idempotent no-op (path unverifiable).
                    resolved.append((arg, repo, existing.path))
                    continue
                has_not_found = True
                failures.append(
                    f"skill {arg!r} not found in cached repo {repo!r}; "
                    f"run 'skill-manager source available-skills {repo}' to list "
                    "available skills"
                )
                continue
            if len(matches) > 1:
                paths_list = ", ".join(s.path for s in matches)
                failures.append(
                    f"skill {arg!r} is ambiguous in {repo!r}; matching paths: {paths_list}; "
                    "use the path form: enable <repo> <path>"
                )
                continue
            skill = matches[0]
            existing = enabled_by_name.get(skill.name)
            if existing is not None:
                if existing.repo == repo and existing.path == skill.path:
                    resolved.append((existing.name, repo, skill.path))
                else:
                    failures.append(
                        f"skill {skill.name!r} is already enabled from "
                        f"{existing.repo}:{existing.path}; disable it first: "
                        f"skill-manager disable {skill.name}"
                    )
                continue
            resolved.append((skill.name, repo, skill.path))

    if failures:
        msg = "; ".join(failures)
        if has_not_found and not include_all:
            msg += _ALL_HINT
        raise NotFoundError(msg)
    return _enable_apply_batch(
        project_config,
        global_config_path,
        cache_root,
        skills_dir,
        resolved=resolved,
        url_resolver=url_resolver,
        progress=progress,
        cloned_repos=cloned_repos,
    )


def _enable_apply_batch(
    project_config: Path,
    global_config_path: Path,
    cache_root: Path,
    skills_dir: Path,
    *,
    resolved: list[tuple[str, str, str]],
    url_resolver: Callable[[str], str] | None,
    progress: ProgressSink | None,
    cross_hint: tuple[dict[str, tuple[str, str]] | None, list[dict[str, str]]] | None = None,
    cloned_repos: set[str] | None = None,
) -> EnableResult:
    """Commit a validated, deduped batch of ``(name, repo, path)`` skills.

    Already-enabled names are idempotent successes only when the requested
    (repo, path) matches the existing declaration; a same-name request from a
    different source or path is an error with a disable-first hint. New names
    are checked against the *other* scope — the global declaration for project
    enable, the cwd project for ``--global`` enable: same-source overlaps get a
    neutral hint, different sources are hard conflicts. When at least one skill
    was added, each affected repo is ensured cloned (never pulled — only sync
    and source update update cached content) and the new skills are linked.

    ``cross_hint`` is the optional preloaded ``_load_global_enabled_hint`` result
    so interactive enable can reuse one load for picker rows and apply; the
    warnings it carries are returned in the result for the command to display.
    """
    proj = _load_declarations_for_enable(project_config)
    enabled = {s.name: s for s in proj.skills}
    global_scope = _is_global_scope(project_config)
    if cross_hint is None:
        global_hint, warnings = _load_global_enabled_hint(project_config)
    else:
        global_hint, warnings = cross_hint
    # Conflict checks always look at the *other* scope; outcome hints keep the
    # forward (global) semantics and are omitted in global scope.
    project_hint = _load_project_hint(project_config)
    other_scope = project_hint if global_scope else global_hint

    # Validation pass — atomic: every failure aborts before any write. Also
    # dedupes the batch: a name accepted once (recorded in ``batch_added``)
    # that reappears with the same (repo, path) is silently kept once;
    # reappearing with a different (repo, path) is a scope-internal conflict.
    failures: list[str] = []
    accepted: list[tuple[str, str, str]] = []
    batch_added: dict[str, tuple[str, str]] = {}
    for skill_name, skill_repo, skill_path in resolved:
        existing = enabled.get(skill_name)
        if existing is not None:
            if existing.repo == skill_repo and existing.path == skill_path:
                accepted.append((skill_name, skill_repo, skill_path))
                continue
            failures.append(
                f"skill {skill_name!r} is already enabled from {existing.repo}:{existing.path}; "
                f"disable it first: skill-manager disable {skill_name}"
            )
            continue
        first = batch_added.get(skill_name)
        if first is not None:
            if first == (skill_repo, skill_path):
                continue  # same skill via another arg form: keep the first
            failures.append(
                f"skill {skill_name!r} is requested from both {first[0]}:{first[1]} and "
                f"{skill_repo}:{skill_path} in this batch; enable one form only"
            )
            continue
        g = other_scope.get(skill_name) if other_scope is not None else None
        if g is not None and g[0] != skill_repo:
            if global_scope:
                failures.append(
                    f"skill {skill_name!r} is already enabled in the project (cwd) from "
                    f"{g[0]}:{g[1]}; enabling {skill_repo}:{skill_path} would create a "
                    f"cross-scope name conflict; disable it first: "
                    f"skill-manager disable {skill_name}"
                )
            else:
                failures.append(
                    f"skill {skill_name!r} is already enabled globally from {g[0]}:{g[1]}; "
                    f"enabling {skill_repo}:{skill_path} would create a cross-scope name "
                    f"conflict; disable it first: skill-manager --global disable {skill_name}"
                )
            continue
        batch_added[skill_name] = (skill_repo, skill_path)
        accepted.append((skill_name, skill_repo, skill_path))
    if failures:
        raise NotFoundError("; ".join(failures))

    outcomes: list[EnableOutcome] = []
    added_refs: list[SkillRef] = []
    for skill_name, skill_repo, skill_path in accepted:
        # Conflict checks use the other scope; the outcome hint keeps forward
        # (global) semantics and is omitted in global scope.
        g = other_scope.get(skill_name) if other_scope is not None else None
        hint = (skill_name in global_hint) if global_hint is not None else None
        existing = enabled.get(skill_name)
        if existing is not None:
            # A pre-existing overlap: same source is benign (⊕), a different
            # source is a cross-scope conflict (⚠) validation cannot reject
            # because it was declared before this run.
            conflict = bool(hint and g is not None and g[0] != existing.repo)
            outcomes.append(
                EnableOutcome(
                    action="already_enabled",
                    skill={"name": existing.name, "repo": existing.repo, "path": existing.path},
                    enabled_globally=hint,
                    global_conflict=conflict,
                )
            )
            continue
        skill_ref = SkillRef(name=skill_name, repo=skill_repo, path=skill_path)
        proj.skills.append(skill_ref)
        added_refs.append(skill_ref)
        outcomes.append(
            EnableOutcome(
                action="enabled",
                skill={"name": skill_name, "repo": skill_repo, "path": skill_path},
                enabled_globally=hint,
            )
        )

    sync_result = None
    if added_refs:
        config.save_skill_declarations(project_config, proj)
        global_cfg = config.load_global_config(global_config_path)
        resolver = url_resolver or sources.repo_url
        ensured_sources: list[SourceEnsured] = []
        seen_repos: set[str] = set()
        for skill_ref in added_refs:
            if skill_ref.repo in seen_repos:
                continue
            seen_repos.add(skill_ref.repo)
            # Never pulls: clone_source is a no-op for a cached source, so the
            # live line only appears when this run actually fills the cache.
            _progress_start(progress, f"preparing source {skill_ref.repo}...")
            head = sources.clone_source(
                skill_ref.repo, global_cfg, cache_root, url=resolver(skill_ref.repo)
            )
            _progress_done(progress, f"prepared source {skill_ref.repo} ({head[:8]})")
            # "cloned" reports an actual cache fill by this run (clone_source
            # never pulls, so a source not in cloned_repos was already cached).
            action = "cloned" if (cloned_repos and skill_ref.repo in cloned_repos) else "up_to_date"
            ensured_sources.append(
                SourceEnsured(
                    repo=skill_ref.repo,
                    commit=head,
                    action=action,
                )
            )
        config.save_global_config(global_config_path, global_cfg)
        sync_result = SyncResult(sources=ensured_sources)
        for skill_ref in added_refs:
            _progress_start(progress, f"linking {skill_ref.name}...")
            link_result = links.ensure_link(skill_ref, cache_root, skills_dir)
            sync_result.links.append(
                LinkDone(name=skill_ref.name, action=link_result.action, target=link_result.target)
            )
            _progress_done(
                progress,
                f"{link_result.action} {skill_ref.name} -> "
                f"{render.display_path(link_result.target)}",
            )
    return EnableResult(outcomes=outcomes, sync=sync_result, warnings=warnings)


def run_disable(
    project_config: Path,
    global_config_path: Path,
    cache_root: Path,
    skills_dir: Path,
    *,
    names: list[str] | None = None,
    progress: ProgressSink | None = None,
    picker: Picker | None = None,
) -> DisableResult:
    """Disable one or more skills (interactive when ``names`` is empty).

    Lenient: disabling a name that is not enabled is an idempotent no-op
    reported per name, never an error. A missing declaration file is treated
    as empty (same as ``list``/``sync``), so disabling in a fresh scope is a
    no-op rather than an error; malformed content still raises ConfigError
    worded for the active scope. Declarations are removed and the config
    saved once for the whole batch.

    ``picker`` is the interactive adapter (default: questionary UI). Cancel
    raises ``PickerCancelled``.
    """
    del global_config_path  # shared signature with other runners; unused here
    names = list(names or [])
    proj = _load_declarations_for_list_sync(project_config)

    if not names:
        if not proj.skills:
            return DisableResult(outcomes=[])
        ui = _resolve_picker(picker, command="disable")
        ordered_names = ui.select_skills_to_disable(sorted(s.name for s in proj.skills))
        if not ordered_names:
            return DisableResult(outcomes=[])
        by_name = {s.name: s for s in proj.skills}
        return _disable_apply_batch(
            project_config, cache_root, skills_dir, proj, ordered_names, by_name, progress=progress
        )

    by_name = {s.name: s for s in proj.skills}
    ordered_names = list(dict.fromkeys(names))
    return _disable_apply_batch(
        project_config, cache_root, skills_dir, proj, ordered_names, by_name, progress=progress
    )


def _disable_apply_batch(
    project_config: Path,
    cache_root: Path,
    skills_dir: Path,
    proj: config.SkillDeclarations,
    ordered_names: list[str],
    by_name: dict[str, SkillRef],
    *,
    progress: ProgressSink | None,
) -> DisableResult:
    """Disable the given names in order; report each as disabled or not_enabled.

    Removes all disabled declarations and saves the config once at the end.
    """
    outcomes: list[DisableOutcome] = []
    disabled_names: set[str] = set()
    for name in ordered_names:
        skill = by_name.get(name)
        if skill is None:
            outcomes.append(DisableOutcome(action="not_enabled", skill={"name": name}))
            continue
        link_removed, link_note = _clean_link(cache_root, skills_dir, skill, progress)
        outcomes.append(
            DisableOutcome(
                action="disabled",
                skill={"name": skill.name, "repo": skill.repo, "path": skill.path},
                link_removed=link_removed,
                link_note=link_note,
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
    progress: ProgressSink | None,
) -> tuple[bool, str]:
    """Remove the managed symlink for ``skill`` if it points at the cached source.

    Returns ``(removed, note)``: whether a link was removed, plus the human
    detail — the link path stays visible even when the entry is left alone, so
    a skipped cleanup never hides which link it skipped. External symlinks and
    non-symlink entries are left untouched (reported, not an error).
    """
    link = skills_dir / skill.name
    target_path = (cache_root / skill.repo / skill.path).resolve()
    _progress_start(progress, f"removing link {skill.name}...")
    if link.is_symlink() and links.link_points_to(link, target_path):
        link.unlink()
        removed, note = True, f"removed {render.display_path(link)}"
    elif link.is_symlink():
        removed, note = False, f"kept {render.display_path(link)} (points elsewhere)"
    elif link.exists():
        removed, note = False, f"kept {render.display_path(link)} (not a symlink)"
    else:
        removed, note = False, ""
    _progress_done(progress, note or f"no link for {skill.name}")
    return removed, note


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
            raise NotFoundError(
                f"source repo {repo!r} is not cached (use 'source add {repo}' first)"
            )
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
    partial = SyncResult()
    console: Console | None = None
    try:
        console = _human_console(ctx)
        decl_path, skills_dir = _scope_paths(ctx)
        result = run_sync(
            decl_path,
            paths.config_file(),
            paths.repos_cache_dir(),
            skills_dir,
            result=partial,
            progress=None if console is None else render.Progress(console),
        )
        if console is None:
            _success(ctx, result.to_data())
            return
        render.render_sync(console, result)
    except (ConfigError, SourceError, LinkError, NotFoundError, UsageError) as e:
        # A sync that dies half way still reports what it finished: the human
        # track keeps the result lines for operations already done.
        if console is not None and (partial.sources or partial.links):
            render.render_sync(console, partial)
        _handle_command_error(ctx, e)


@app.command(name="list")
def list_(ctx: typer.Context) -> None:
    """List declared sources and skills with status."""
    try:
        console = _human_console(ctx)
        decl_path, skills_dir = _scope_paths(ctx)
        result = run_list(
            decl_path,
            paths.config_file(),
            paths.repos_cache_dir(),
            skills_dir,
        )
        if console is None:
            _success(ctx, result.to_data(), warnings=result.warnings or None)
            return
        for warning in result.warnings:
            _warn(warning["message"])
        render.render_list(console, result)
    except (ConfigError, SourceError, LinkError, NotFoundError, UsageError) as e:
        _handle_command_error(ctx, e)


@app.command()
def doctor(ctx: typer.Context) -> None:
    """Diagnose consistency issues across all scopes and infrastructure."""
    try:
        console = _human_console(ctx)
        if ctx.obj and ctx.obj.get("global"):
            raise UsageError("doctor is a panoramic command and does not accept --global")
        result = run_doctor(
            paths.project_config_path(),
            paths.global_skills_config_path(),
            paths.config_file(),
            paths.repos_cache_dir(),
            paths.project_skills_dir(),
            paths.global_skills_dir(),
        )
        if console is None:
            _success(ctx, result.to_data())
            return
        render.render_doctor(console, result)
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
        console = _human_console(ctx)
        decl_path, skills_dir = _scope_paths(ctx)
        result = run_enable(
            decl_path,
            paths.config_file(),
            paths.repos_cache_dir(),
            skills_dir,
            repo=repo,
            names=list(names or []),
            include_all=include_all,
            progress=None if console is None else render.Progress(console),
        )
        if console is None:
            _success(ctx, result.to_data(), warnings=result.warnings or None)
            return
        for warning in result.warnings:
            _warn(warning["message"])
        render.render_enable(console, result)
    except PickerCancelled:
        _cancelled_exit(ctx)
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
        console = _human_console(ctx)
        decl_path, skills_dir = _scope_paths(ctx)
        result = run_disable(
            decl_path,
            paths.config_file(),
            paths.repos_cache_dir(),
            skills_dir,
            names=list(names or []),
            progress=None if console is None else render.Progress(console),
        )
        if console is None:
            _success(ctx, result.to_data())
            return
        render.render_disable(console, result)
    except PickerCancelled:
        _cancelled_exit(ctx)
    except (ConfigError, SourceError, LinkError, NotFoundError, UsageError) as e:
        _handle_command_error(ctx, e)


# ── source subcommands ────────────────────────────────────────────────────────


@source_app.command(name="list")
def source_list(ctx: typer.Context) -> None:
    """List registered source repositories with status."""
    try:
        console = _human_console(ctx)
        global_cfg = config.load_global_config(paths.config_file())
        cache_root = paths.repos_cache_dir()
        result = SourceListResult(
            sources=[
                SourceStatus(
                    repo=repo,
                    commit=global_cfg.sources[repo].commit,
                    url=global_cfg.sources[repo].url,
                    cached=(cache_root / repo).is_dir(),
                )
                for repo in sorted(global_cfg.sources)
            ]
        )
        if console is None:
            _success(ctx, result.to_data())
            return
        render.render_source_list(console, result)
    except (ConfigError, SourceError, LinkError, NotFoundError, UsageError) as e:
        _handle_command_error(ctx, e)


@source_app.command(name="add")
def source_add(ctx: typer.Context, repo: str) -> None:
    """Add a source repository (clone to cache, register in global config)."""
    try:
        console = _human_console(ctx)
        config.validate_repo(repo, "source add")
        global_cfg = config.load_global_config(paths.config_file())
        cache_root = paths.repos_cache_dir()
        if repo in global_cfg.sources and (cache_root / repo).is_dir():
            commit = global_cfg.sources[repo].commit
            if console is None:
                _success(ctx, {"action": "already_exists", "repo": repo, "commit": commit})
            else:
                render.render_source_action(console, repo, "already exists", commit[:8], ok=False)
            return
        progress = None if console is None else render.Progress(console)
        _progress_start(progress, f"cloning {repo}...")
        head = sources.clone_source(repo, global_cfg, cache_root)
        _progress_done(progress, f"cloned {repo} ({head[:8]})")
        config.save_global_config(paths.config_file(), global_cfg)
        if console is None:
            _success(ctx, {"action": "added", "repo": repo, "commit": head})
        else:
            render.render_source_action(console, repo, "added", head[:8])
    except (ConfigError, SourceError, LinkError, NotFoundError, UsageError) as e:
        _handle_command_error(ctx, e)


@source_app.command(name="remove")
def source_remove(ctx: typer.Context, repo: str) -> None:
    """Remove a source repository (delete cache + config entry)."""
    try:
        console = _human_console(ctx)
        config.validate_repo(repo, "source remove")
        global_cfg = config.load_global_config(paths.config_file())
        cache_root = paths.repos_cache_dir()
        if repo not in global_cfg.sources:
            raise NotFoundError(f"source {repo!r} not found")
        referenced_scopes = _referencing_scopes(repo)
        if referenced_scopes and console is not None:
            _warn(
                f"{repo!r} still referenced by: {', '.join(referenced_scopes)} "
                "(other projects not checked); links may break"
            )
        sources.remove_source(repo, global_cfg, cache_root)
        config.save_global_config(paths.config_file(), global_cfg)
        if console is None:
            _success(ctx, {"action": "removed", "repo": repo})
        else:
            render.render_source_action(console, repo, "removed")
    except (ConfigError, SourceError, LinkError, NotFoundError, UsageError) as e:
        _handle_command_error(ctx, e)


@source_app.command(name="update")
def source_update(
    ctx: typer.Context,
    repo: Annotated[str | None, typer.Argument(help="Repo to update (default: all)")] = None,
) -> None:
    """Update source repository(ies) to latest (pull --ff-only)."""
    try:
        console = _human_console(ctx)
        progress = None if console is None else render.Progress(console)
        global_cfg = config.load_global_config(paths.config_file())
        cache_root = paths.repos_cache_dir()
        if repo is not None:
            if repo not in global_cfg.sources:
                raise NotFoundError(f"source {repo!r} not registered (use 'source add' first)")
            repos = [repo]
        else:
            repos = sorted(global_cfg.sources)

        result = SourceUpdateResult()
        for r in repos:
            if (cache_root / r).is_dir():
                _progress_start(progress, f"pulling {r}...")
                old, new = sources.pull_source(r, global_cfg, cache_root)
                if old != new:
                    result.updates.append(
                        SourceEnsured(repo=r, commit=new, action="updated", old_commit=old)
                    )
                    _progress_done(progress, f"pulled {r} ({old[:8]} → {new[:8]})")
                else:
                    result.updates.append(SourceEnsured(repo=r, commit=new, action="up_to_date"))
                    _progress_done(progress, f"up-to-date {r} ({new[:8]})")
            else:
                _progress_start(progress, f"cloning {r}...")
                head = sources.clone_source(r, global_cfg, cache_root)
                result.updates.append(SourceEnsured(repo=r, commit=head, action="cloned"))
                _progress_done(progress, f"cloned {r} ({head[:8]})")
            config.save_global_config(paths.config_file(), global_cfg)

        if console is None:
            _success(ctx, result.to_data())
            return
        render.render_source_update(console, result)
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
        console = _human_console(ctx)
        result = run_available_skills(paths.repos_cache_dir(), repo=repo, include_all=include_all)
        if console is None:
            _success(ctx, result.to_data())
            return
        render.render_available_skills(console, result)
    except (ConfigError, SourceError, LinkError, NotFoundError, UsageError) as e:
        _handle_command_error(ctx, e)


def main() -> None:
    """Entry point for ``python -m skill_manager``."""
    app()


if __name__ == "__main__":
    main()
