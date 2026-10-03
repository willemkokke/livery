"""The one requirement grammar: refusals first, then the parse and the scope."""

from __future__ import annotations

import pytest

from livery.toolroom.store.api import (
    HOSTS,
    LockError,
    Requirement,
    Scope,
    Spec,
    SpecError,
)

# The refusals first.


def test_an_unknown_scope_token_refuses_naming_the_tokens() -> None:
    with pytest.raises(SpecError) as caught:
        Spec.parse("tea@!windwos", where="here")
    text = str(caught.value)
    assert text.startswith("here: 'windwos' in 'tea@!windwos' is neither a platform")
    assert "(windows, macos, linux)" in text and "windows-arm" in text
    with pytest.raises(SpecError, match=r"here: 'tea@' names no host after @"):
        Spec.parse("tea@", where="here")


def test_a_spelling_outside_the_grammar_refuses() -> None:
    for text in ("ruff==1", "ruff>=", "[slim]", "ruff[slim"):
        with pytest.raises(SpecError, match="is not a requirement"):
            Spec.parse(text, where="here")
    with pytest.raises(SpecError, match=r"names an option that is not a name: ''"):
        Spec.parse("llvm[]", where="here")
    with pytest.raises(SpecError, match=r"names an option that is not a name: 'a b'"):
        Spec.parse("llvm[a b]", where="here")


def test_a_requirement_refuses_what_its_sites_do_not_take_yet() -> None:
    # Until a site takes options, `?` or `!`, a tool requirement refuses
    # them as it always did.
    for text in ("llvm[slim]", "docker?", "tea@!windows"):
        with pytest.raises(LockError, match="is not a requirement"):
            Requirement.parse(text, site="here")


# Then the parse.


def test_every_part_of_the_grammar_parses_and_spells_back() -> None:
    spec = Spec.parse(" llvm[slim, debug]?>=20.1 @ linux, !linux-arm ")
    assert spec == Spec(
        "llvm", ("slim", "debug"), True, "20.1", Scope(("linux",), ("linux-arm",))
    )
    assert str(spec) == "llvm[slim,debug]?>=20.1@linux,!linux-arm"
    assert Spec.parse("ruff") == Spec("ruff")
    assert str(Spec.parse("ruff")) == "ruff"


def test_a_scope_of_exclusions_starts_from_every_supported_host() -> None:
    supported = ("linux-x64", "linux-arm", "macos-arm", "windows-x64")
    assert Scope().hosts(supported) == supported
    assert not Scope()
    assert Scope.parse("!windows").hosts(supported) == (
        "linux-x64",
        "linux-arm",
        "macos-arm",
    )
    # A platform and a host key, included then excluded.
    assert Scope.parse("linux,!linux-arm").hosts(supported) == ("linux-x64",)
    # A scope reaching no supported host is empty, never an error here:
    # whether that refuses is the site's rule.
    assert Scope.parse("macos-x64").hosts(supported) == ()
    assert set(Scope.parse("!linux").hosts(HOSTS)) == {
        host for host in HOSTS if not host.startswith("linux")
    }


def test_a_requirement_resolves_its_hosts_through_the_scope() -> None:
    requirement = Requirement.parse("tea>=1.1@linux,macos-arm", site="here")
    assert requirement == Requirement("tea", "1.1", "here", ("linux", "macos-arm"))
    assert requirement.on(("linux-x64", "macos-arm", "windows-x64")) == (
        "linux-x64",
        "macos-arm",
    )
