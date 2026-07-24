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
