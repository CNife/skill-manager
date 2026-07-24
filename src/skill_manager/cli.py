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
from skill_manager.sources import SourceError

# ── result types ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SourceEnsured:
    repo: str
    commit: str


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
            "sources": [{"repo": s.repo, "commit": s.commit} for s in self.sources],
            "links": [{"name": link.name, "action": link.action} for link in self.links],
        }


@dataclass(frozen=True)
class SkillStatus:
    name: str
    repo: str
    path: str
    link: str  # linked | broken | external | unlinked


@dataclass
class ListResult:
    skills: list[SkillStatus]
    # Human-only extras (not serialized to JSON data):
    source_rows: list[tuple[str, str, str]] = field(default_factory=list)
    # (repo, head8_or_dash, "cached"|"missing")


@dataclass
class EnableResult:
    action: str  # enabled | already_enabled
    skill: dict[str, str]
    sync: SyncResult | None = None

    def to_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {"action": self.action, "skill": self.skill}
        if self.sync is not None:
            data["sync"] = self.sync.to_data()
        return data


@dataclass
class DisableResult:
    action: str  # disabled | not_enabled
    skill: dict[str, str]
    link_removed: bool | None = None

    def to_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {"action": self.action, "skill": self.skill}
        if self.link_removed is not None:
            data["link_removed"] = self.link_removed
        return data


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


# ── JSON-aware Typer group (usage errors become JSON when --json is set) ──────


def _root_json_requested(argv: list[str]) -> bool:
    """True only when ``--json`` appears among root options (before the subcommand)."""
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
        want_json = _root_json_requested(argv)
        if not (want_json and standalone_mode):
            return super().main(args=args, standalone_mode=standalone_mode, **kwargs)
        try:
            # standalone_mode=False: click turns Exit into a returned int exit code
            # (does not raise), and propagates ClickException for us to format.
            result = super().main(args=args, standalone_mode=False, **kwargs)
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
    """Load declarations for ``enable``, bootstrapping only the global file.

    A missing ``~/.skill-manager.json`` is treated as empty so the first
    ``--global enable`` works without a hand-edited file. Project configs stay
    strict: a missing ``./.skill-manager.json`` still means "not in a project".
    When the project cwd is the user's home, the project path coincides with
    the global path and is treated as global.
    """
    if path.resolve() == paths.global_skills_config_path().resolve():
        try:
            return config.load_skill_declarations(path)
        except ConfigError:
            return config.SkillDeclarations(skills=[])
    return config.load_skill_declarations(path)


def _emit_json(payload: dict[str, Any]) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False))


def _success(ctx: typer.Context, data: dict[str, Any]) -> None:
    if _is_json(ctx):
        _emit_json({"ok": True, "data": data})


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
    """Orchestrate sync: ensure sources, record HEADs, link skills.

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
        head = sources.ensure_source(repo, global_cfg, cache_root, url=resolver(repo))
        result.sources.append(SourceEnsured(repo=repo, commit=head))
        if emit is not None:
            emit(f"ensured {repo} ({head[:8]})")
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
    skill_statuses = [
        SkillStatus(
            name=skill.name,
            repo=skill.repo,
            path=skill.path,
            link=_link_status(skill, cache_root, skills_dir),
        )
        for skill in proj.skills
    ]
    return ListResult(skills=skill_statuses, source_rows=source_rows)


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


def _is_internal_skill(skill_md: Path) -> bool:
    """True only when SKILL.md frontmatter has metadata.internal as YAML bool true."""
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return False
    if not text.startswith("---"):
        return False
    # Frontmatter ends at the next line that is exactly ---
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return False
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return False
    return _metadata_internal_is_true(lines[1:end_idx])


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


def _scan_skills(repo_dir: Path, repo: str, *, include_all: bool = False) -> list[tuple[str, str]]:
    """Discover skills under a cached Source checkout.

    Default (include_all=False): skip noise dir names, dot-directories, and
    skills with metadata.internal: true. Always stop recursion at a skill root
    (directory containing SKILL.md). Returns list of (name, path) tuples.
    """
    skills: list[tuple[str, str]] = []

    def visit(current: Path) -> None:
        skill_md = current / "SKILL.md"
        if skill_md.is_file():
            if include_all or not _is_internal_skill(skill_md):
                if current == repo_dir:
                    name = Path(repo).name.lower()
                    rel = "."
                else:
                    name = current.name
                    rel = str(current.relative_to(repo_dir))
                skills.append((name, rel))
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


def _numbered_select(items: list[str], prompt: str) -> int:
    """Display a numbered menu and return the selected index."""
    for i, item in enumerate(items, 1):
        typer.echo(f"  {i:>3}. {item}")
    while True:
        try:
            raw = input(f"{prompt} ")
            idx = int(raw.strip()) - 1
            if 0 <= idx < len(items):
                return idx
            typer.echo(f"Please enter a number between 1 and {len(items)}", err=True)
        except (ValueError, EOFError):
            typer.echo("Invalid input", err=True)
        except KeyboardInterrupt:
            raise typer.Exit(code=1) from None


def run_enable(
    project_config: Path,
    global_config_path: Path,
    cache_root: Path,
    skills_dir: Path,
    *,
    repo: str | None = None,
    name: str | None = None,
    include_all: bool = False,
    url_resolver: Callable[[str], str] | None = None,
    emit: Callable[[str], None] | None = None,
) -> EnableResult:
    """Enable a skill interactively or non-interactively.

    Non-interactive when both ``repo`` and ``name`` are provided. Interactive
    when both are omitted. Partial args are a usage error (raised by caller).
    ``include_all`` widens Source skill discovery (hidden / internal).
    """
    if repo is None and name is None:
        return _enable_interactive(
            project_config,
            global_config_path,
            cache_root,
            skills_dir,
            include_all=include_all,
            emit=emit,
        )
    if repo is None or name is None:
        raise click.exceptions.UsageError(
            "enable requires both REPO and NAME, or neither for interactive mode"
        )
    return _enable_noninteractive(
        project_config,
        global_config_path,
        cache_root,
        skills_dir,
        repo=repo,
        name=name,
        include_all=include_all,
        url_resolver=url_resolver,
        emit=emit,
    )


def _emit(emit: Callable[[str], None] | None, message: str) -> None:
    """Send a human progress line when an emit callback is provided."""
    if emit is not None:
        emit(message)


def _enable_interactive(
    project_config: Path,
    global_config_path: Path,
    cache_root: Path,
    skills_dir: Path,
    *,
    include_all: bool,
    emit: Callable[[str], None] | None,
) -> EnableResult:
    repos = _list_cached_repos(cache_root)
    if not repos:
        raise NotFoundError(
            "No cached repos found. Run 'skill-manager sync' first to populate the cache."
        )

    typer.echo("\nCached repos:")
    repo_idx = _numbered_select(repos, "Select repo (number):")
    selected_repo = repos[repo_idx]

    repo_dir = cache_root / selected_repo
    skills = _scan_skills(repo_dir, selected_repo, include_all=include_all)
    if not skills:
        raise NotFoundError(f"No skills (directories containing SKILL.md) found in {selected_repo}")

    typer.echo(f"\nSkills in {selected_repo}:")
    skill_labels = [f"{n}  ({p})" for n, p in skills]
    skill_idx = _numbered_select(skill_labels, "Select skill (number):")
    selected_name, skill_path = skills[skill_idx]

    return _enable_apply(
        project_config,
        global_config_path,
        cache_root,
        skills_dir,
        repo=selected_repo,
        name=selected_name,
        skill_path=skill_path,
        url_resolver=None,
        emit=emit,
    )


def _enable_noninteractive(
    project_config: Path,
    global_config_path: Path,
    cache_root: Path,
    skills_dir: Path,
    *,
    repo: str,
    name: str,
    include_all: bool,
    url_resolver: Callable[[str], str] | None,
    emit: Callable[[str], None] | None,
) -> EnableResult:
    proj = _load_declarations_for_enable(project_config)
    existing = next((s for s in proj.skills if s.name == name), None)
    if existing is not None:
        # Idempotent early return: no sync, no cache validation.
        _emit(emit, f"Skill {name!r} already enabled")
        return EnableResult(
            action="already_enabled",
            skill={"name": existing.name, "repo": existing.repo, "path": existing.path},
        )

    repo_dir = cache_root / repo
    if not repo_dir.is_dir():
        raise NotFoundError(f"source repo {repo!r} is not cached")

    matches = [
        (n, p) for n, p in _scan_skills(repo_dir, repo, include_all=include_all) if n == name
    ]
    if not matches:
        msg = f"skill {name!r} not found in cached repo {repo!r}"
        if not include_all:
            msg += _ALL_HINT
        raise NotFoundError(msg)
    if len(matches) > 1:
        paths_list = ", ".join(p for _, p in matches)
        raise NotFoundError(
            f"skill {name!r} is ambiguous in {repo!r}; matching paths: {paths_list}"
        )
    _matched_name, skill_path = matches[0]
    return _enable_apply(
        project_config,
        global_config_path,
        cache_root,
        skills_dir,
        repo=repo,
        name=name,
        skill_path=skill_path,
        url_resolver=url_resolver,
        emit=emit,
    )


def _enable_apply(
    project_config: Path,
    global_config_path: Path,
    cache_root: Path,
    skills_dir: Path,
    *,
    repo: str,
    name: str,
    skill_path: str,
    url_resolver: Callable[[str], str] | None,
    emit: Callable[[str], None] | None,
) -> EnableResult:
    proj = _load_declarations_for_enable(project_config)
    existing = next((s for s in proj.skills if s.name == name), None)
    if existing is not None:
        _emit(emit, f"Skill {name!r} already enabled")
        return EnableResult(
            action="already_enabled",
            skill={"name": existing.name, "repo": existing.repo, "path": existing.path},
        )

    proj.skills.append(SkillRef(name=name, repo=repo, path=skill_path))
    config.save_skill_declarations(project_config, proj)
    _emit(emit, f"Added {name} ({repo}:{skill_path}) to {project_config}")

    sync_result = run_sync(
        project_config,
        global_config_path,
        cache_root,
        skills_dir,
        url_resolver=url_resolver,
        emit=emit,
    )
    return EnableResult(
        action="enabled",
        skill={"name": name, "repo": repo, "path": skill_path},
        sync=sync_result,
    )


def run_disable(
    project_config: Path,
    global_config_path: Path,
    cache_root: Path,
    skills_dir: Path,
    *,
    name: str | None = None,
    emit: Callable[[str], None] | None = None,
) -> DisableResult:
    """Disable a skill interactively (name is None) or non-interactively."""
    proj = config.load_skill_declarations(project_config)

    if name is None:
        if not proj.skills:
            _emit(emit, "No enabled skills to disable.")
            # Interactive empty: success-like no-op without a requested name.
            return DisableResult(action="not_enabled", skill={"name": ""})
        typer.echo("Enabled skills:")
        skill_labels = [f"{s.name}  ({s.repo}:{s.path})" for s in proj.skills]
        idx = _numbered_select(skill_labels, "Select skill to disable (number):")
        skill = proj.skills[idx]
        return _disable_apply(project_config, cache_root, skills_dir, skill, emit=emit)

    existing = next((s for s in proj.skills if s.name == name), None)
    if existing is None:
        _emit(emit, f"Skill {name!r} not enabled")
        return DisableResult(action="not_enabled", skill={"name": name})
    return _disable_apply(project_config, cache_root, skills_dir, existing, emit=emit)


def _disable_apply(
    project_config: Path,
    cache_root: Path,
    skills_dir: Path,
    skill: SkillRef,
    *,
    emit: Callable[[str], None] | None,
) -> DisableResult:
    proj = config.load_skill_declarations(project_config)
    proj.skills = [s for s in proj.skills if s.name != skill.name]
    config.save_skill_declarations(project_config, proj)
    _emit(emit, f"Removed {skill.name} from {project_config}")

    link = skills_dir / skill.name
    target_path = (cache_root / skill.repo / skill.path).resolve()
    link_removed = False
    if link.is_symlink() and links.link_points_to(link, target_path):
        link.unlink()
        link_removed = True
        _emit(emit, f"Removed symlink {link}")
    elif link.is_symlink():
        _emit(emit, f"Skipped {link}: points elsewhere (not managed by skill-manager)")
    elif link.exists():
        _emit(emit, f"Skipped {link}: not a symlink")

    return DisableResult(
        action="disabled",
        skill={"name": skill.name, "repo": skill.repo, "path": skill.path},
        link_removed=link_removed,
    )


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
            {"name": n, "repo": repo, "path": p}
            for n, p in _scan_skills(repo_dir, repo, include_all=include_all)
        ]
        return AvailableSkillsResult(skills=skills)

    skills: list[dict[str, str]] = []
    for cached_repo in _list_cached_repos(cache_root):
        repo_dir = cache_root / cached_repo
        for n, p in _scan_skills(repo_dir, cached_repo, include_all=include_all):
            skills.append({"name": n, "repo": cached_repo, "path": p})
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
            _success(
                ctx,
                {
                    "skills": [
                        {
                            "name": s.name,
                            "repo": s.repo,
                            "path": s.path,
                            "link": s.link,
                        }
                        for s in result.skills
                    ]
                },
            )
        else:
            typer.echo("Sources:")
            for repo, head, status in result.source_rows:
                typer.echo(f"  {repo}  {head}  {status}")
            typer.echo("Skills:")
            for s in result.skills:
                # Human view keeps present/absent of source path (JSON omits it).
                target_path = (paths.repos_cache_dir() / s.repo / s.path).resolve()
                repo_present = target_path.is_dir()
                typer.echo(
                    f"  {s.name}  {s.repo}:{s.path}  "
                    f"{s.link}  {'present' if repo_present else 'absent'}"
                )
    except (ConfigError, SourceError, LinkError, NotFoundError, UsageError) as e:
        _handle_command_error(ctx, e)


@app.command()
def enable(
    ctx: typer.Context,
    repo: Annotated[str | None, typer.Argument(help="Source repo (owner/repo).")] = None,
    name: Annotated[str | None, typer.Argument(help="Skill name within the repo.")] = None,
    include_all: Annotated[
        bool,
        typer.Option(
            "--all",
            help="Include hidden (dot-dir) and internal skills.",
        ),
    ] = False,
) -> None:
    """Enable a skill from a cached repo (interactive if no args)."""
    try:
        if _is_json(ctx) and (repo is None or name is None):
            raise click.exceptions.UsageError(
                "enable requires REPO and NAME in --json mode (non-interactive)"
            )
        if (repo is None) ^ (name is None):
            raise click.exceptions.UsageError(
                "enable requires both REPO and NAME, or neither for interactive mode"
            )
        emit = None if _is_json(ctx) else typer.echo
        # Interactive path still needs menus on stdout even when emit is typer.echo;
        # pass emit only for structured progress in non-json mode. For interactive
        # selection, run_enable uses typer.echo/input directly for menus.
        decl_path, skills_dir = _scope_paths(ctx)
        result = run_enable(
            decl_path,
            paths.config_file(),
            paths.repos_cache_dir(),
            skills_dir,
            repo=repo,
            name=name,
            include_all=include_all,
            emit=emit,
        )
        if _is_json(ctx):
            _success(ctx, result.to_data())
        # Text mode: progress / status lines already streamed via emit.
    except (ConfigError, SourceError, LinkError, NotFoundError, UsageError) as e:
        _handle_command_error(ctx, e)


@app.command()
def disable(
    ctx: typer.Context,
    name: Annotated[str | None, typer.Argument(help="Enabled skill name.")] = None,
) -> None:
    """Disable an enabled skill (interactive if no args)."""
    try:
        if _is_json(ctx) and name is None:
            raise click.exceptions.UsageError(
                "disable requires NAME in --json mode (non-interactive)"
            )
        emit = None if _is_json(ctx) else typer.echo
        decl_path, skills_dir = _scope_paths(ctx)
        result = run_disable(
            decl_path,
            paths.config_file(),
            paths.repos_cache_dir(),
            skills_dir,
            name=name,
            emit=emit,
        )
        if _is_json(ctx):
            _success(ctx, result.to_data())
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
        head = sources.ensure_source(repo, global_cfg, cache_root)
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
            head = sources.ensure_source(r, global_cfg, cache_root)
            config.save_global_config(paths.config_file(), global_cfg)
            updates.append({"action": "updated", "repo": r, "commit": head})
            if not _is_json(ctx):
                typer.echo(f"updated {r} (HEAD {head[:8]})")

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
