"""The renderer's own rules, at the seams a stub's text depends on."""

from __future__ import annotations

from livery.toolroom.store import Option
from livery.toolroom.store._stub import _annotation, _arg_lines, _esc, _md_safe, _quoted


def test_an_option_that_takes_a_value_or_stands_alone_is_a_valued_flag():
    assert _annotation(Option("gpg_sign", type_name="optvalue")) == "ValuedFlag"
    assert _annotation(Option("m", type_name="str")) == "Value"
    assert _annotation(Option("f", ("--f",), type_name="bool")) == "Flag"


def test_value_options_accept_a_sequence():
    """`select=["E", "F"]` works at run time, so it must type-check.

    The bridge repeats a flag once per item; whether the tool accepts the
    repetition is the tool's business, not the stub's.
    """
    assert _annotation(Option("select", ("--select",), type_name="str")) == "Value"


def test_the_stub_escapes_backslashes_in_help_text():
    # mypy's `--exclude '/setup\.py$'` must land as a literal in the docstring,
    # not an invalid `\.` escape a compiler warns on.
    assert _esc(r"a \.py$ b") == r"a \\.py$ b"


def test_a_choice_is_spelled_double_quoted_and_escaped():
    assert _quoted("auto") == '"auto"'
    assert _quoted('say "hi"') == '"say \\"hi\\""'


def test_an_args_entry_keeps_the_tools_words_verbatim():
    option = Option(
        "ours",
        ("--ours",),
        type_name="bool",
        help=(
            "When restoring files in the working tree from the index, use stage "
            "#2 (ours) or #3 (theirs) for unmerged paths"
        ),
    )
    (line,) = _arg_lines(option)
    assert line.lstrip().startswith("ours: ")
    assert "#2 (ours)" in line  # verbatim: mid-line needs no escape


def test_md_safe_touches_only_a_leading_header_and_quote():
    safe = _md_safe(
        ["            #2 heading", "            > quote", "            mid # hash"]
    )
    assert safe[0].endswith("\\\\#2 heading")
    assert safe[1].endswith("\\\\> quote")
    assert safe[2].endswith("mid # hash")  # a mid-line hash is not a block
