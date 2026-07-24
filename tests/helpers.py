"""Shared test helpers (importable without pytest fixtures)."""

from __future__ import annotations


def skill_md(
    name: str,
    description: str | None = None,
    *,
    body: str | None = None,
    extra_fm: str = "",
) -> str:
    """Build a qualified SKILL.md (UTF-8 FM name + description)."""
    desc = f"{name} skill" if description is None else description
    fm = f"---\nname: {name}\ndescription: {desc}\n"
    if extra_fm:
        fm += extra_fm if extra_fm.endswith("\n") else extra_fm + "\n"
    fm += "---\n"
    return fm + (f"# {name}\n" if body is None else body)
