"""skill-manager CLI entry point.

Provides the Typer app, sync/list commands, and console_scripts target.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import typer

from skill_manager import config, links, paths, sources
from skill_manager.config import ConfigError
from skill_manager.links import LinkError
from skill_manager.sources import SourceError

app = typer.Typer(
    name="skill-manager",
    help="Project-scoped declarative skill manager for pi agent skills.",
    no_args_is_help=True,
)


def run_sync(
    project_config: Path,
    global_config_path: Path,
    cache_root: Path,
    skills_dir: Path,
    *,
    url_resolver: Callable[[str], str] | None = None,
) -> None:
    """Orchestrate sync: ensure sources, record HEADs, link skills.

    ``url_resolver`` defaults to ``sources.repo_url`` (GitHub HTTPS); tests pass
    a ``file://`` resolver to use local repos as offline GitHub stand-ins.
    """
    resolver = url_resolver or sources.repo_url
    proj = config.load_project_config(project_config)
    repos = config.derived_sources(proj)
    global_cfg = config.load_global_config(global_config_path)
    for repo in repos:
        head = sources.ensure_source(repo, global_cfg, cache_root, url=resolver(repo))
        typer.echo(f"ensured {repo} ({head[:8]})")
    config.save_global_config(global_config_path, global_cfg)
    for skill in proj.skills:
        result = links.ensure_link(skill, cache_root, skills_dir)
        typer.echo(f"{result.action} {skill.name} -> {result.target}")


def run_list(
    project_config: Path,
    global_config_path: Path,
    cache_root: Path,
    skills_dir: Path,
) -> None:
    """List declared sources and enabled skills with status."""
    proj = config.load_project_config(project_config)
    global_cfg = config.load_global_config(global_config_path)
    typer.echo("Sources:")
    for repo in config.derived_sources(proj):
        cached = (cache_root / repo).is_dir()
        src = global_cfg.sources.get(repo)
        head = src.commit[:8] if src else "-"
        typer.echo(f"  {repo}  {head}  {'cached' if cached else 'missing'}")
    typer.echo("Skills:")
    for skill in proj.skills:
        link = skills_dir / skill.name
        target_path = (cache_root / skill.repo / skill.path).resolve()
        repo_present = target_path.is_dir()
        if link.is_symlink():
            points_to_declared = links.link_points_to(link, target_path)
            if points_to_declared and link.exists():
                link_status = "linked"
            elif points_to_declared:
                link_status = "broken"
            elif link.exists():
                link_status = "external"
            else:
                link_status = "broken"
        else:
            link_status = "unlinked"
        typer.echo(
            f"  {skill.name}  {skill.repo}:{skill.path}  "
            f"{link_status}  {'present' if repo_present else 'absent'}"
        )


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


def _scan_skills(repo_dir: Path, repo: str) -> list[tuple[str, str]]:
    """Scan a repo directory for skill directories (containing SKILL.md).

    Returns list of (name, path) tuples.
    """
    skills: list[tuple[str, str]] = []
    for skmd in sorted(repo_dir.rglob("SKILL.md")):
        skill_dir = skmd.parent
        if skill_dir == repo_dir:
            name = Path(repo).name.lower()
            path = "."
        else:
            name = skill_dir.name
            path = str(skill_dir.relative_to(repo_dir))
        skills.append((name, path))
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
) -> None:
    """Interactively enable a skill: pick a cached repo, pick a skill, add to config, sync."""
    repos = _list_cached_repos(cache_root)
    if not repos:
        typer.echo(
            "No cached repos found. Run 'skill-manager sync' first to populate the cache.",
            err=True,
        )
        raise typer.Exit(code=1)

    typer.echo("\nCached repos:")
    repo_idx = _numbered_select(repos, "Select repo (number):")
    repo = repos[repo_idx]

    repo_dir = cache_root / repo
    skills = _scan_skills(repo_dir, repo)
    if not skills:
        typer.echo(f"No skills (directories containing SKILL.md) found in {repo}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"\nSkills in {repo}:")
    skill_labels = [f"{name}  ({path})" for name, path in skills]
    skill_idx = _numbered_select(skill_labels, "Select skill (number):")
    name, skill_path = skills[skill_idx]

    proj = config.load_project_config(project_config)
    existing_names = {s.name for s in proj.skills}
    if name in existing_names:
        typer.echo(f"Skill {name!r} is already enabled in {project_config}", err=True)
        raise typer.Exit(code=1)

    proj.skills.append(config.SkillRef(name=name, repo=repo, path=skill_path))
    config.save_project_config(project_config, proj)
    typer.echo(f"Added {name} ({repo}:{skill_path}) to {project_config}")

    run_sync(project_config, global_config_path, cache_root, skills_dir)


def run_disable(
    project_config: Path,
    global_config_path: Path,
    cache_root: Path,
    skills_dir: Path,
) -> None:
    """Interactively disable a skill: pick an enabled skill, remove from config, clean up."""
    proj = config.load_project_config(project_config)
    if not proj.skills:
        typer.echo("No enabled skills to disable.")
        return

    typer.echo("Enabled skills:")
    skill_labels = [f"{s.name}  ({s.repo}:{s.path})" for s in proj.skills]
    idx = _numbered_select(skill_labels, "Select skill to disable (number):")
    skill = proj.skills[idx]

    proj.skills = [s for s in proj.skills if s.name != skill.name]
    config.save_project_config(project_config, proj)
    typer.echo(f"Removed {skill.name} from {project_config}")

    link = skills_dir / skill.name
    target_path = (cache_root / skill.repo / skill.path).resolve()
    if link.is_symlink() and links.link_points_to(link, target_path):
        link.unlink()
        typer.echo(f"Removed symlink {link}")
    elif link.is_symlink():
        typer.echo(f"Skipped {link}: points elsewhere (not managed by skill-manager)", err=True)
    elif link.exists():
        typer.echo(f"Skipped {link}: not a symlink", err=True)


@app.command()
def sync() -> None:
    """Sync declared skills into ./.agents/skills/."""
    try:
        run_sync(
            paths.project_config_path(),
            paths.config_file(),
            paths.repos_cache_dir(),
            paths.project_skills_dir(),
        )
    except (ConfigError, SourceError, LinkError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1) from e


@app.command(name="list")
def list_() -> None:
    """List declared sources and skills with status."""
    try:
        run_list(
            paths.project_config_path(),
            paths.config_file(),
            paths.repos_cache_dir(),
            paths.project_skills_dir(),
        )
    except (ConfigError, SourceError, LinkError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1) from e


@app.command()
def enable() -> None:
    """Interactively enable a skill from cached repos."""
    try:
        run_enable(
            paths.project_config_path(),
            paths.config_file(),
            paths.repos_cache_dir(),
            paths.project_skills_dir(),
        )
    except (ConfigError, SourceError, LinkError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1) from e


@app.command()
def disable() -> None:
    """Interactively disable an enabled skill."""
    try:
        run_disable(
            paths.project_config_path(),
            paths.config_file(),
            paths.repos_cache_dir(),
            paths.project_skills_dir(),
        )
    except (ConfigError, SourceError, LinkError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1) from e


def main() -> None:
    """Entry point for ``python -m skill_manager``."""
    app()


if __name__ == "__main__":
    main()
