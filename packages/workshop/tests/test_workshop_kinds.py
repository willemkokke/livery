"""The kind registry: the seed trees a birth writes, their chain, and its guards."""

from __future__ import annotations

import pytest

from livery.workshop._kinds import (
    KindRecord,
    kind_chain,
    kind_for,
    kind_names,
    register_kind,
)

_FAILURES = (BaseException,)


@pytest.fixture
def restored_registry():
    from livery.workshop import _kinds

    before = dict(_kinds._KINDS)
    yield
    _kinds._KINDS.clear()
    _kinds._KINDS.update(before)


# The refusals first.


def test_an_unknown_kind_refuses_naming_the_kinds() -> None:
    with pytest.raises(
        _FAILURES, match="not a registered package kind; kinds: cpp-conan, python"
    ):
        kind_for("carrier-pigeon")


def test_a_child_of_an_unregistered_parent_refuses(restored_registry) -> None:
    with pytest.raises(_FAILURES, match="register the parent first"):
        register_kind(KindRecord(name="python-orphan", parent="cpp"))


def test_a_chain_cycle_refuses_naming_it(restored_registry) -> None:
    from livery.workshop import _kinds

    register_kind(KindRecord(name="a"))
    register_kind(KindRecord(name="b", parent="a"))
    _kinds._KINDS["a"] = KindRecord(name="a", parent="b")
    with pytest.raises(_FAILURES, match="cycles"):
        kind_chain("b")


# The registry's records and their chains.


def test_the_base_kind_is_abstract_and_heads_every_chain() -> None:
    assert "base" not in kind_names()
    for name in ("python", "python-nanobind", "cpp-conan"):
        assert kind_chain(name)[0].name == "base", name


def test_a_registered_kind_chains_under_its_parent(restored_registry) -> None:
    register_kind(
        KindRecord(name="python-fake", template="package-python-fake", parent="python")
    )
    assert "python-fake" in kind_names()
    chain = kind_chain("python-fake")
    assert [record.name for record in chain] == ["base", "python", "python-fake"]
    # An abstract record is in no refusal's list.
    register_kind(KindRecord(name="hollow", abstract=True))
    assert "hollow" not in kind_names()


def test_template_chain_orders_parent_first(restored_registry) -> None:
    from livery.workshop._kinds import template_chain

    register_kind(
        KindRecord(name="python-fake", template="package-python-fake", parent="python")
    )
    assert template_chain("package-python-fake") == (
        "package-base",
        "package-python",
        "package-python-fake",
    )
    # A template variant the registry does not map still renders
    # over the shared base, so its package ships the docs seeds.
    assert template_chain("package-extension") == (
        "package-base",
        "package-extension",
    )
    # The base itself renders alone: no recursion, no doubling.
    assert template_chain("package-base") == ("package-base",)
