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


def main() -> None:
    """Entry point for ``python -m skill_manager``."""
    app()


if __name__ == "__main__":
    main()
