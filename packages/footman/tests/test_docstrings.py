"""The standalone docstring parser, tested as a unit — not through callers."""

from __future__ import annotations

from livery.footman.docstrings import Docstring, parse

# --- shape and degenerate inputs ---------------------------------------------


def test_none_and_empty_are_empty():
    assert parse(None) == Docstring()
    assert parse("") == Docstring()
    assert parse("   \n  \n") == Docstring()


def test_summary_only():
    assert parse("Do the thing.") == Docstring(summary="Do the thing.")


def test_summary_and_long_no_params():
    d = parse(
        """Do the thing.

        Carefully, and twice on Sundays.

        With paragraphs kept apart.
        """
    )
    assert d.summary == "Do the thing."
    assert d.long == "Carefully, and twice on Sundays.\n\nWith paragraphs kept apart."
    assert d.params == {}


def test_result_is_frozen():
    import dataclasses

    import pytest

    with pytest.raises(dataclasses.FrozenInstanceError):
        parse("x").summary = "y"  # type: ignore[misc]


# --- Google -------------------------------------------------------------------


def test_google_basic():
    d = parse(
        """Deploy the thing.

        The long part.

        Args:
            target: where to deploy
            fix (bool): apply fixes
                in place
            *extras: passthrough bits
        """
    )
    assert d.summary == "Deploy the thing."
    assert d.long == "The long part."
    assert d.params == {
        "target": "where to deploy",
        "fix": "apply fixes in place",
        "extras": "passthrough bits",
    }


def test_google_arguments_and_parameters_aliases():
    for header in ("Arguments:", "Parameters:", "args:"):
        d = parse(f"S.\n\n{header}\n    a: first\n")
        assert d.params == {"a": "first"}, header


def test_google_section_ends_at_dedent():
    d = parse(
        """S.

        Args:
            a: first

        Returns:
            Nothing at all.
        """
    )
    assert d.params == {"a": "first"}  # "Nothing at all." never leaks in


def test_google_no_blank_after_summary():
    d = parse("S.\nArgs:\n    a: first\n")
    assert d.summary == "S."
    assert d.long == ""
    assert d.params == {"a": "first"}


def test_google_empty_entry_text_fills_from_continuation():
    d = parse("S.\n\nArgs:\n    a:\n        described below the name\n")
    assert d.params == {"a": "described below the name"}


def test_google_uneven_indentation():
    d = parse("S.\n\nArgs:\n   a: first\n     wrapped tail\n    b: second\n")
    assert d.params == {"a": "first wrapped tail", "b": "second"}


def test_google_blank_lines_between_entries():
    d = parse("S.\n\nArgs:\n    a: first\n\n    b: second\n")
    assert d.params == {"a": "first", "b": "second"}


def test_google_args_after_returns_still_found():
    d = parse(
        """S.

        Returns:
            A value.

        Args:
            a: first
        """
    )
    assert d.params == {"a": "first"}
    assert d.long == ""  # Returns: is a section too — the long part ends there


def test_google_duplicate_name_first_wins():
    d = parse("S.\n\nArgs:\n    a: first\n    a: second\n")
    assert d.params == {"a": "first"}


def test_google_double_star_kwargs():
    d = parse("S.\n\nArgs:\n    **flags: extra flags\n")
    assert d.params == {"flags": "extra flags"}


def test_docstring_opening_with_args_has_no_summary_and_keeps_params():
    d = parse("Args:\n    fix: apply safe fixes\n    target: where to deploy\n")
    assert d.summary == ""
    assert d.long == ""
    assert d.params == {"fix": "apply safe fixes", "target": "where to deploy"}


def test_docstring_opening_with_sphinx_field_has_no_summary():
    d = parse(":param fix: apply safe fixes\n:returns: a report\n")
    assert d.summary == ""
    assert d.params == {"fix": "apply safe fixes"}
    assert d.returns == "a report"


def test_docstring_opening_with_numpy_header_has_no_summary():
    d = parse("Parameters\n----------\nfix : bool\n    apply safe fixes\n")
    assert d.summary == ""
    assert d.params == {"fix": "apply safe fixes"}


def test_docstring_opening_with_returns_header_has_no_summary():
    d = parse("Returns:\n    A report.\n")
    assert d.summary == ""
    assert d.returns == "A report."


def test_google_note_continuation_is_not_a_parameter():
    d = parse(
        """S.

        Args:
            fix: apply safe fixes.
                Note: this rewrites files in place.
            target: where to deploy
                Example: fm deploy --target=prod
        """
    )
    assert d.params == {
        "fix": "apply safe fixes. Note: this rewrites files in place.",
        "target": "where to deploy Example: fm deploy --target=prod",
    }


def test_google_note_after_wrapped_continuation_still_joins():
    d = parse(
        "S.\n\nArgs:\n    a: first\n        wrapped\n        Note: caveat\n"
        "    b: second\n"
    )
    assert d.params == {"a": "first wrapped Note: caveat", "b": "second"}


# --- NumPy --------------------------------------------------------------------


def test_numpy_basic():
    d = parse(
        """Sum things.

        Prose.

        Parameters
        ----------
        a : int
            The first.
        b : int
            The second,
            wrapped.

        Returns
        -------
        int
            The sum.
        """
    )
    assert d.summary == "Sum things."
    assert d.long == "Prose."
    assert d.params == {"a": "The first.", "b": "The second, wrapped."}


def test_numpy_shared_description_names():
    d = parse(
        """S.

        Parameters
        ----------
        x, y : float
            A coordinate.
        """
    )
    assert d.params == {"x": "A coordinate.", "y": "A coordinate."}


def test_numpy_untyped_and_star_names():
    d = parse(
        """S.

        Parameters
        ----------
        flag
            Bare, no type.
        *rest : str
            Variadic tail.
        """
    )
    assert d.params == {"flag": "Bare, no type.", "rest": "Variadic tail."}


def test_numpy_other_parameters_keeps_collecting():
    d = parse(
        """S.

        Parameters
        ----------
        a : int
            First.

        Other Parameters
        ----------------
        b : int
            Rare knob.

        Notes
        -----
        Unrelated.
        """
    )
    assert d.params == {"a": "First.", "b": "Rare knob."}


def test_numpy_header_with_colon_is_still_numpy():
    d = parse("S.\n\nParameters:\n----------\na : int\n    First.\n")
    assert d.params == {"a": "First."}


# --- Sphinx -------------------------------------------------------------------


def test_sphinx_basic():
    d = parse(
        """Fetch a URL.

        The long part.

        :param url: the URL
            to fetch
        :param str timeout: seconds to wait
        :type url: str
        :returns: the body
        :arg extra: one more
        """
    )
    assert d.summary == "Fetch a URL."
    assert d.long == "The long part."
    assert d.params == {
        "url": "the URL to fetch",
        "timeout": "seconds to wait",
        "extra": "one more",
    }


def test_sphinx_long_stops_at_first_field_of_any_kind():
    d = parse("S.\n\nProse.\n\n:returns: body\n:param a: first\n")
    assert d.long == "Prose."
    assert d.params == {"a": "first"}


def test_sphinx_star_name_and_parameter_alias():
    d = parse("S.\n\n:parameter *args: the tail\n")
    assert d.params == {"args": "the tail"}


# --- format detection ---------------------------------------------------------


def test_first_format_wins_no_mixing():
    d = parse(
        """S.

        Args:
            a: from google

        :param b: from sphinx
        """
    )
    assert d.params == {"a": "from google"}


def test_sphinx_before_google_wins():
    d = parse("S.\n\n:param b: from sphinx\n\nArgs:\n    a: nope\n")
    assert d.params == {"b": "from sphinx"}


# --- real-world tolerance -----------------------------------------------------


def test_crlf_line_endings():
    d = parse("S.\r\n\r\nArgs:\r\n    a: first\r\n        wrapped\r\n")
    assert d.summary == "S."
    assert d.params == {"a": "first wrapped"}


def test_tabs_are_normalised():
    d = parse("S.\n\nArgs:\n\ta: first\n")
    assert d.params == {"a": "first"}


def test_already_cleaned_text_is_fine():
    import inspect

    raw = """Do.

        Args:
            a: first
        """
    assert parse(inspect.cleandoc(raw)) == parse(raw)


def test_prose_with_colons_is_not_params():
    d = parse("S.\n\nNote: this matters.\nAlso: that.\n")
    assert d.params == {}
    assert "Note: this matters." in d.long


# --- Returns ------------------------------------------------------------------


def test_google_returns_joins_to_one_line():
    d = parse(
        """Report the change.

        Args:
            since: the base commit

        Returns:
            Which tasks the change reaches,
            and why.
        """
    )
    assert d.returns == "Which tasks the change reaches, and why."
    assert d.params == {"since": "the base commit"}


def test_google_returns_ends_at_dedent():
    d = parse("S.\n\nReturns:\n    the report\nNotes:\n    unrelated\n")
    assert d.returns == "the report"


def test_numpy_returns_takes_type_line_and_description():
    d = parse(
        """Report.

        Parameters
        ----------
        since : str
            the base commit

        Returns
        -------
        PytestReport
            The parsed report.

        Notes
        -----
        Unrelated.
        """
    )
    assert d.returns == "PytestReport The parsed report."
    assert d.params == {"since": "the base commit"}


def test_sphinx_returns_field_with_continuation():
    d = parse(
        """Report.

        :param since: the base commit
        :returns: the parsed report,
            one dict per failure
        :rtype: PytestReport
        """
    )
    assert d.returns == "the parsed report, one dict per failure"
    assert d.params == {"since": "the base commit"}


def test_return_singular_spellings():
    assert parse("S.\n\nReturn:\n    it\n").returns == "it"
    assert parse("S.\n\n:return: it\n").returns == "it"


def test_no_returns_section_is_empty():
    assert parse("S.\n\nArgs:\n    a: first\n").returns == ""
