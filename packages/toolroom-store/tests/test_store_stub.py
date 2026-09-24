"""The renderer's own rules, at the seams a stub's text depends on."""

from __future__ import annotations

from livery.toolroom.store import Option, Verb
from livery.toolroom.store._stub import (
    _annotation,
    _arg_lines,
    _argv_property,
    _esc,
    _md_safe,
    _quoted,
    _verbs,
    platforms_phrase,
)


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


def test_a_choice_set_without_an_alias_is_spelled_inline():
    option = Option("color", ("--color",), choices=("auto", "never"))
    inline = 'Literal["auto", "never"]'
    assert _annotation(option) == f"{inline} | Sequence[{inline}] | None"
    hoisted: dict[tuple[str, tuple[str, ...]], str] = {
        ("color", ("auto", "never")): "Color"
    }
    assert _annotation(option, hoisted) == "Color | Sequence[Color] | None"


def test_the_verb_walk_skips_a_node_that_is_neither_group_nor_verb():
    verb = Verb("check")
    tree: dict[str, object] = {"check": verb, "compose": {"up": verb}, "": "root"}
    assert list(_verbs(tree)) == [verb, verb]


def test_a_long_argv_return_type_wraps_the_signature():
    short = _argv_property(("Ruff",), 0)
    assert short == "    @property\n    def argv(self) -> Ruff[Argv]: ..."
    path = ("Docker", *(f"VeryLongSubcommandName{i}" for i in range(3)))
    long = _argv_property(path, 3)
    assert long.startswith("    @property\n    def argv(\n        self,\n    ) -> ")
    assert long.endswith("[Argv]: ...")


def test_the_platforms_phrase_names_none_one_and_many():
    assert platforms_phrase([]) == "this machine"
    assert platforms_phrase(["Linux"]) == "Linux"
    assert platforms_phrase(["Linux", "Windows", "macOS"]) == "Linux, Windows and macOS"
