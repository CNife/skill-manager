# ruff: noqa: RUF001
"""Pure display helpers for picker inline descriptions."""

from __future__ import annotations

from skill_manager.picker import clean_description, display_width, truncate_description


def test_clean_description_strips_ansi_and_collapses_ws() -> None:
    raw = "  hello\x1b[0m\t\nworld\x07  "
    assert clean_description(raw) == "hello world"


def test_display_width_counts_fullwidth_as_two() -> None:
    assert display_width("ab") == 2
    assert display_width("中文") == 4
    assert display_width("a中b") == 4


def test_truncate_description_no_op_when_fits() -> None:
    assert truncate_description("short", 20) == "short"


def test_truncate_description_appends_ellipsis() -> None:
    assert truncate_description("abcdefghij", 5) == "abcd…"


def test_truncate_description_fullwidth_aware() -> None:
    # Each CJK char = 2 cols; avail 5 → content budget 4 → two chars + …
    assert truncate_description("中文词", 5) == "中文…"
    assert truncate_description("中文词", 3) == "中…"


def test_truncate_description_avail_lt_2_is_empty() -> None:
    assert truncate_description("hello", 1) == ""
    assert truncate_description("hello", 0) == ""


def test_inline_control_constructs_when_all_choices_locked() -> None:
    """All-locked enable lists must open (spec: locked rows stay highlightable).

    questionary's InquirerControl leaves ``pointed_at`` unset when every Choice
    is disabled; our control must still construct and point at the first row.
    """
    from questionary import Choice

    from skill_manager.picker import _make_inline_control

    control_cls = _make_inline_control()
    ic = control_cls(
        [
            Choice(
                title="alpha",
                value="alpha",
                disabled=True,
                checked=True,
                description="First",
            ),
            Choice(
                title="bravo",
                value="bravo",
                disabled=True,
                checked=True,
                description="Second",
            ),
        ],
        pointer="❯",
        use_indicator=True,
        use_shortcuts=False,
        show_description=True,
        show_selected=False,
    )
    assert ic.pointed_at == 0
    tokens = ic._get_choice_tokens()
    assert any("alpha" in (t[1] if isinstance(t, tuple) else "") for t in tokens)


def test_qa_checkbox_builds_question_when_all_locked() -> None:
    """Checkbox prompt construction must not crash on an all-locked skill list."""
    from questionary import Choice

    from skill_manager.picker import _q_style, _qa_checkbox

    q = _qa_checkbox(
        "Enable skills",
        [
            Choice(
                title="only",
                value="only",
                disabled=True,
                checked=True,
                description="Already on",
            ),
        ],
        instruction="(type · ↑↓ · space · enter · Esc/Ctrl-C) ",
        style=_q_style(),
        show_description=True,
    )
    assert q is not None


def test_skill_enable_title_prefix() -> None:
    from skill_manager.picker import skill_enable_title

    assert skill_enable_title("tdd") == "tdd"
    assert skill_enable_title("tdd", enabled_globally=False) == "tdd"
    assert skill_enable_title("grilling", enabled_globally=True) == "⊕ grilling"


def test_locked_row_tokens_use_green_check_and_optional_global() -> None:
    """Locked rows render ✓ (proj-locked) then title; title may carry ⊕."""
    from questionary import Choice

    from skill_manager.picker import _make_inline_control, skill_enable_title

    control_cls = _make_inline_control()
    ic = control_cls(
        [
            Choice(
                title=skill_enable_title("grilling", enabled_globally=True),
                value="grilling",
                disabled=True,
                checked=True,
            ),
            Choice(
                title=skill_enable_title("research", enabled_globally=False),
                value="research",
                disabled=True,
                checked=True,
            ),
        ],
        pointer="❯",
        use_indicator=True,
        use_shortcuts=False,
        show_description=False,
        show_selected=False,
    )
    tokens = ic._get_choice_tokens()
    joined = "".join(s for cls, s in tokens if isinstance(s, str))
    assert "✓" in joined
    assert "⊕ grilling" in joined
    assert "research" in joined
    # proj-locked style used for the check mark
    assert any(cls == "class:proj-locked" and "✓" in s for cls, s in tokens if isinstance(s, str))
    # No old prose reason
    assert "already enabled" not in joined


def test_unlocked_global_title_carries_oplus_only() -> None:
    from questionary import Choice

    from skill_manager.picker import _make_inline_control, skill_enable_title

    control_cls = _make_inline_control()
    ic = control_cls(
        [
            Choice(
                title=skill_enable_title("domain-modeling", enabled_globally=True),
                value="domain-modeling",
            ),
        ],
        pointer="❯",
        use_indicator=True,
        use_shortcuts=False,
        show_description=False,
        show_selected=False,
    )
    joined = "".join(s for cls, s in ic._get_choice_tokens() if isinstance(s, str))
    assert "⊕ domain-modeling" in joined
    assert "✓" not in joined
