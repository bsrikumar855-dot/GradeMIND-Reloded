"""Line correction helpers (3.4): validation, edit operations (replaying them reproduces the correction), chain heads."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from grademind_core.line_corrections import MAX_LINE_CHARS, InvalidLineTextError, apply_ops, clean_line_text, edit_ops

CP = chr  # readability: build special characters from code points, never type them


def test_text_is_kept_literally() -> None:
    assert clean_line_text("diversty of lif [?] e") == "diversty of lif [?] e"  # misspellings and [?] are the label
    assert clean_line_text("  padded  ") == "padded"
    assert clean_line_text("") == ""  # the machine invented a line that is not there
    assert clean_line_text("6CO" + CP(0x2082) + " → " + CP(0x092A)) == "6CO" + CP(0x2082) + " → " + CP(0x092A)


@pytest.mark.parametrize(
    ("bad", "code"),
    [
        ("one\ntwo", "invalid_characters"),
        ("tab\there", "invalid_characters"),
        ("pay" + CP(0x202E) + "txt", "invalid_characters"),  # direction override
        ("a" + CP(0x200B) + "b", "invalid_characters"),  # zero width space
        ("a" + CP(0) + "b", "invalid_characters"),
        ("a" + CP(0x2028) + "b", "invalid_characters"),  # line separator
        ("x" * (MAX_LINE_CHARS + 1), "too_long"),
    ],
)
def test_text_that_would_look_different_from_what_it_is_is_refused(bad: str, code: str) -> None:
    with pytest.raises(InvalidLineTextError) as e:
        clean_line_text(bad)
    assert e.value.code == code and e.value.message


def test_edit_ops_examples() -> None:
    assert edit_ops("same", "same") == []
    assert edit_ops("divesity", "diversity") == [{"op": "insert", "at": 4, "old": "", "new": "r"}]
    assert edit_ops("cat", "cut") == [{"op": "replace", "at": 1, "old": "a", "new": "u"}]
    assert edit_ops("abc", "ac") == [{"op": "delete", "at": 1, "old": "b", "new": ""}]
    assert edit_ops("noise", "") == [{"op": "delete", "at": 0, "old": "noise", "new": ""}]


@settings(max_examples=300, deadline=None)
@given(st.text(max_size=60), st.text(max_size=60))
def test_property_replaying_the_edit_operations_reproduces_the_correction(old: str, new: str) -> None:
    assert apply_ops(old, edit_ops(old, new)) == new


@settings(max_examples=100, deadline=None)
@given(st.text(max_size=40))
def test_property_no_change_means_no_operations(t: str) -> None:
    assert edit_ops(t, t) == []
