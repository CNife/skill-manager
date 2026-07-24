# ruff: noqa: RUF001
"""Interactive picker adapter for enable/disable (questionary-backed).

Production UI is a thin customization of questionary/prompt_toolkit:
type-to-filter, Esc clears then cancels, locked rows, and dim description
to the right of the highlighted name. Tests inject a fake ``Picker``.
"""

from __future__ import annotations

import re
import shutil
import string
import sys
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol


class PickerCancelled(Exception):
    """User cancelled the picker (Esc on empty filter or Ctrl-C)."""


@dataclass(frozen=True)
class SourceChoice:
    """One cached Source row for enable step 1."""

    repo: str
    skill_count: int

    @property
    def count_label(self) -> str:
        n = self.skill_count
        return "1 skill" if n == 1 else f"{n} skills"


@dataclass(frozen=True)
class SkillChoice:
    """One qualified Skill row for enable step 2."""

    name: str
    path: str
    description: str
    locked: bool = False


class Picker(Protocol):
    """Injectable interactive picker used only by enable/disable interactive paths."""

    def select_source(self, choices: Sequence[SourceChoice]) -> str:
        """Single-select a Source repo. Raises ``PickerCancelled`` on cancel."""
        ...

    def select_skills_to_enable(self, choices: Sequence[SkillChoice]) -> list[str]:
        """Multi-select skills to enable.

        Returns names of checked, non-locked skills (may be empty = empty submit).
        Raises ``PickerCancelled`` on cancel.
        """
        ...

    def select_skills_to_disable(self, names: Sequence[str]) -> list[str]:
        """Multi-select declaration names to disable.

        Returns selected names (may be empty = empty submit).
        Raises ``PickerCancelled`` on cancel.
        """
        ...


# ── display helpers (pure; unit-tested) ───────────────────────────────────────

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_description(text: str) -> str:
    """Strip ANSI/controls and collapse whitespace to a single line."""
    t = _ANSI_RE.sub("", text)
    t = _CONTROL_RE.sub("", t)
    return " ".join(t.split())


def display_width(text: str) -> int:
    """Terminal display columns (fullwidth / wide East Asian = 2)."""
    width = 0
    for ch in text:
        if unicodedata.combining(ch):
            continue
        ea = unicodedata.east_asian_width(ch)
        width += 2 if ea in {"F", "W"} else 1
    return width


def truncate_description(text: str, available_cols: int) -> str:
    """Clean + truncate ``text`` to ``available_cols`` display columns.

    Appends ``…`` when truncated. Returns ``""`` when fewer than 2 columns remain.
    """
    if available_cols < 2:
        return ""
    cleaned = clean_description(text)
    if display_width(cleaned) <= available_cols:
        return cleaned
    # Leave room for the ellipsis character (1 column).
    budget = available_cols - 1
    if budget < 1:
        return ""
    out: list[str] = []
    used = 0
    for ch in cleaned:
        if unicodedata.combining(ch):
            out.append(ch)
            continue
        w = 2 if unicodedata.east_asian_width(ch) in {"F", "W"} else 1
        if used + w > budget:
            break
        out.append(ch)
        used += w
    return "".join(out).rstrip() + "…"


# ── questionary implementation ────────────────────────────────────────────────


def _q_style():
    from questionary import Style

    return Style(
        [
            ("qmark", "fg:cyan bold"),
            ("question", "bold"),
            ("answer", "fg:cyan"),
            ("pointer", "fg:cyan bold"),
            ("highlighted", "fg:cyan bold"),
            ("selected", "fg:green"),
            ("separator", "fg:ansibrightblack"),
            ("instruction", "fg:ansibrightblack"),
            ("text", ""),
            ("disabled", "fg:ansibrightblack italic"),
            ("description", "fg:ansibrightblack"),
            ("search_success", "fg:cyan"),
            ("search_none", "fg:red"),
        ]
    )


def _make_inline_control():
    """InquirerControl with dim description to the right of the highlighted name."""
    from questionary.constants import INDICATOR_SELECTED, INDICATOR_UNSELECTED
    from questionary.prompts.common import Choice, InquirerControl, Separator

    class InlineDescControl(InquirerControl):
        def _init_choices(self, choices, pointed_at):
            super()._init_choices(choices, pointed_at)
            # questionary only assigns pointed_at when it finds a non-disabled
            # choice. All-locked enable lists are valid UI (rows stay
            # highlightable; Enter = empty submit) — default to the first row.
            if not hasattr(self, "pointed_at") and self.choices:
                self.pointed_at = 0

        def __init__(self, choices, **kwargs):
            try:
                super().__init__(choices, **kwargs)
            except ValueError:
                # Parent rejects a pointer that lands on disabled; accept when
                # every real choice is disabled (locked-only list).
                if not getattr(self, "choices", None) or not hasattr(self, "pointed_at"):
                    raise
                if any(not isinstance(c, Separator) and not c.disabled for c in self.choices):
                    raise

        def _get_choice_tokens(self):
            tokens: list = []
            term_cols = shutil.get_terminal_size(fallback=(80, 24)).columns

            def append(index: int, choice: Choice) -> None:
                selected = choice.value in self.selected_options
                pointed = index == self.pointed_at
                line_used = 0

                if pointed:
                    if self.pointer is not None:
                        ptr = f" {self.pointer} "
                        tokens.append(("class:pointer", ptr))
                        line_used += display_width(ptr)
                    else:
                        tokens.append(("class:text", "   "))
                        line_used += 3
                    tokens.append(("[SetCursorPosition]", ""))
                else:
                    pointer_length = len(self.pointer) if self.pointer is not None else 1
                    pad = " " * (2 + pointer_length)
                    tokens.append(("class:text", pad))
                    line_used += display_width(pad)

                if isinstance(choice, Separator):
                    tokens.append(("class:separator", f"{choice.title}"))
                    line_used += display_width(str(choice.title))
                elif choice.disabled:
                    reason = "" if isinstance(choice.disabled, bool) else f" ({choice.disabled})"
                    title = choice.title if isinstance(choice.title, str) else str(choice.title)
                    body = f"- {title}{reason}"
                    tokens.append(("class:disabled", body))
                    line_used += display_width(body)
                else:
                    shortcut = choice.get_shortcut_title() if self.use_shortcuts else ""
                    if self.use_indicator:
                        ind = INDICATOR_SELECTED if selected else INDICATOR_UNSELECTED
                        ind_cls = "class:selected" if selected else "class:text"
                        ind_s = f"{ind} "
                        tokens.append((ind_cls, ind_s))
                        line_used += display_width(ind_s)
                    name = f"{shortcut}{choice.title}"
                    if selected:
                        tokens.append(("class:selected", name))
                    elif pointed:
                        tokens.append(("class:highlighted", name))
                    else:
                        tokens.append(("class:text", name))
                    line_used += display_width(name)

                # Dim description to the right of the name, highlighted row only.
                if (
                    pointed
                    and self.show_description
                    and not isinstance(choice, Separator)
                    and choice.description
                ):
                    # Two spaces before description.
                    gap = 2
                    avail = term_cols - line_used - gap
                    desc = truncate_description(str(choice.description), avail)
                    if desc:
                        tokens.append(("class:description", f"{' ' * gap}{desc}"))

                tokens.append(("", "\n"))

            for i, c in enumerate(self.filtered_choices):
                append(i, c)

            if tokens:
                tokens.pop()  # trailing newline
            return tokens

    return InlineDescControl


def _bind_filter_and_escape(bindings, ic, *, on_cancel) -> None:
    """Type-to-filter; Esc clears non-empty filter, else cancels."""
    from prompt_toolkit.keys import Keys

    def search_filter(event) -> None:
        ic.add_search_character(event.key_sequence[0].key)

    for character in string.printable:
        if character in string.whitespace:
            continue
        bindings.add(character, eager=True)(search_filter)
    bindings.add(Keys.Backspace, eager=True)(search_filter)

    @bindings.add(Keys.Escape, eager=True)
    def escape(_event) -> None:
        if ic.search_filter:
            ic.search_filter = None
            ic.pointed_at = 0
            return
        on_cancel(_event)


def _qa_select(message: str, choices: Sequence[Any], *, instruction: str, style) -> Any:
    """Filterable single-select with Esc-clear and inline dim description."""
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.styles import Style as PtStyle
    from questionary import utils
    from questionary.prompts import common
    from questionary.question import Question
    from questionary.styles import merge_styles_default

    InlineDescControl = _make_inline_control()
    ic = InlineDescControl(
        choices,
        pointer="❯",
        use_indicator=False,
        use_shortcuts=False,
        show_description=True,
        show_selected=False,
    )

    def get_prompt_tokens():
        tokens = [
            ("class:qmark", "?"),
            ("class:question", f" {message} "),
        ]
        if ic.is_answered:
            ans = ic.get_pointed_at()
            title = ans.title if isinstance(ans.title, str) else str(ans.title)
            tokens.append(("class:answer", title))
        else:
            tokens.append(("class:instruction", instruction))
        return tokens

    layout = common.create_inquirer_layout(ic, get_prompt_tokens)
    bindings = KeyBindings()

    def cancel(event) -> None:
        event.app.exit(exception=KeyboardInterrupt, style="class:aborting")

    @bindings.add(Keys.ControlC, eager=True)
    def _ctrlc(event):
        cancel(event)

    def move_down(_event) -> None:
        ic.select_next()
        while not ic.is_selection_valid():
            ic.select_next()

    def move_up(_event) -> None:
        ic.select_previous()
        while not ic.is_selection_valid():
            ic.select_previous()

    bindings.add(Keys.Down, eager=True)(move_down)
    bindings.add(Keys.Up, eager=True)(move_up)
    bindings.add(Keys.ControlN, eager=True)(move_down)
    bindings.add(Keys.ControlP, eager=True)(move_up)
    _bind_filter_and_escape(bindings, ic, on_cancel=cancel)

    @bindings.add(Keys.ControlM, eager=True)
    def submit(event) -> None:
        ic.is_answered = True
        event.app.exit(result=ic.get_pointed_at().value)

    @bindings.add(Keys.Any)
    def _other(_event) -> None:
        return

    merged = merge_styles_default([PtStyle([("bottom-toolbar", "noreverse")]), style])
    return Question(
        Application(
            layout=layout,
            key_bindings=bindings,
            style=merged,
            **utils.used_kwargs({}, Application.__init__),
        )
    )


def _qa_checkbox(
    message: str,
    choices: Sequence[Any],
    *,
    instruction: str,
    style,
    show_description: bool = True,
) -> Any:
    """Filterable multi-select; locked rows highlightable; space no-op on locked."""
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.styles import Style as PtStyle
    from questionary import utils
    from questionary.prompts import common
    from questionary.prompts.common import Separator
    from questionary.question import Question
    from questionary.styles import merge_styles_default

    InlineDescControl = _make_inline_control()
    ic = InlineDescControl(
        choices,
        pointer="❯",
        use_indicator=True,
        use_shortcuts=False,
        show_description=show_description,
        show_selected=False,
    )

    def get_prompt_tokens():
        tokens = [
            ("class:qmark", "?"),
            ("class:question", f" {message} "),
        ]
        if ic.is_answered:
            n = len(
                [
                    c
                    for c in ic.choices
                    if (
                        not isinstance(c, Separator)
                        and c.value in ic.selected_options
                        and not c.disabled
                    )
                ]
            )
            tokens.append(("class:answer", "done" if n == 0 else f"done ({n} selections)"))
        else:
            tokens.append(("class:instruction", instruction))
        return tokens

    layout = common.create_inquirer_layout(ic, get_prompt_tokens)
    bindings = KeyBindings()

    def cancel(event) -> None:
        event.app.exit(exception=KeyboardInterrupt, style="class:aborting")

    @bindings.add(Keys.ControlC, eager=True)
    def _ctrlc(event):
        cancel(event)

    @bindings.add(" ", eager=True)
    def toggle(_event) -> None:
        choice = ic.get_pointed_at()
        if choice.disabled:
            return
        val = choice.value
        if val in ic.selected_options:
            ic.selected_options.remove(val)
        else:
            ic.selected_options.append(val)

    def move_down(_event) -> None:
        ic.select_next()
        # Land on locked rows so their description is visible; skip separators only.
        guard = 0
        while isinstance(ic.get_pointed_at(), Separator) and guard < ic.choice_count:
            ic.select_next()
            guard += 1

    def move_up(_event) -> None:
        ic.select_previous()
        guard = 0
        while isinstance(ic.get_pointed_at(), Separator) and guard < ic.choice_count:
            ic.select_previous()
            guard += 1

    bindings.add(Keys.Down, eager=True)(move_down)
    bindings.add(Keys.Up, eager=True)(move_up)
    bindings.add(Keys.ControlN, eager=True)(move_down)
    bindings.add(Keys.ControlP, eager=True)(move_up)
    _bind_filter_and_escape(bindings, ic, on_cancel=cancel)

    @bindings.add(Keys.ControlM, eager=True)
    def submit(event) -> None:
        ic.is_answered = True
        # Checked non-disabled across the full choice list (filter is view-only).
        result = [
            c.value
            for c in ic.choices
            if (not isinstance(c, Separator) and c.value in ic.selected_options and not c.disabled)
        ]
        event.app.exit(result=result)

    @bindings.add(Keys.Any)
    def _other(_event) -> None:
        return

    merged = merge_styles_default([PtStyle([("bottom-toolbar", "noreverse")]), style])
    return Question(
        Application(
            layout=layout,
            key_bindings=bindings,
            style=merged,
            **utils.used_kwargs({}, Application.__init__),
        )
    )


def _ask(question) -> Any:
    """Run a questionary Question; map KeyboardInterrupt to PickerCancelled."""
    try:
        return question.unsafe_ask()
    except KeyboardInterrupt as exc:
        raise PickerCancelled from exc


class QuestionaryPicker:
    """Default terminal picker for enable/disable interactive flows."""

    def select_source(self, choices: Sequence[SourceChoice]) -> str:
        from questionary import Choice

        style = _q_style()
        q_choices = [Choice(title=c.repo, value=c.repo, description=c.count_label) for c in choices]
        return _ask(
            _qa_select(
                "Select source",
                q_choices,
                instruction="(type · ↑↓ · enter · Esc/Ctrl-C) ",
                style=style,
            )
        )

    def select_skills_to_enable(self, choices: Sequence[SkillChoice]) -> list[str]:
        from questionary import Choice

        style = _q_style()
        q_choices = []
        for c in choices:
            if c.locked:
                q_choices.append(
                    Choice(
                        title=c.name,
                        value=c.name,
                        disabled="already enabled",
                        checked=True,
                        description=c.description or None,
                    )
                )
            else:
                q_choices.append(
                    Choice(
                        title=c.name,
                        value=c.name,
                        description=c.description or None,
                    )
                )
        selected = _ask(
            _qa_checkbox(
                "Enable skills",
                q_choices,
                instruction="(type · ↑↓ · space · enter · Esc/Ctrl-C) ",
                style=style,
                show_description=True,
            )
        )
        return list(selected or [])

    def select_skills_to_disable(self, names: Sequence[str]) -> list[str]:
        from questionary import Choice

        style = _q_style()
        q_choices = [Choice(title=n, value=n) for n in names]
        selected = _ask(
            _qa_checkbox(
                "Disable skills",
                q_choices,
                instruction="(type · ↑↓ · space · enter · Esc/Ctrl-C) ",
                style=style,
                show_description=False,
            )
        )
        return list(selected or [])


def stdin_stdout_are_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()
