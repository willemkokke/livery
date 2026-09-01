"""The commit grammar: what submit accepts as a title, and what it refuses."""

from __future__ import annotations

from livery.workshop._conventional import TITLE_RE, TYPES


def test_the_grammar_refuses_what_is_not_a_convention() -> None:
    # The refusals first: these are the titles a mis-typed submit
    # produces, and each must fail before the accepting cases matter.
    for title in (
        "Merge branch 'main'",
        "feat missing the colon",
        "feature: not one of the types",
        "feat:",
        " feat: leading space",
        "feat(scope) missing colon",
    ):
        assert TITLE_RE.match(title) is None, title


def test_the_grammar_accepts_every_type_scope_and_break() -> None:
    for kind in TYPES:
        assert TITLE_RE.match(f"{kind}: a subject")
    assert TITLE_RE.match("feat(workshop): a scoped subject")
    assert TITLE_RE.match("fix!: a breaking subject")
    assert TITLE_RE.match("refactor(forge)!: scoped and breaking")
