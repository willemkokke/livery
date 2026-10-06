"""Slots: the refusals first, then what the records fill and the render carries."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.workshop._slots import (
    NEAREST,
    SlotError,
    composed,
    contribute,
    register_slot,
    unregister_slot,
    withdraw,
)


def _list(name: str) -> list[object]:
    """The list a union slot composes to, typed for the assertions."""
    value = composed(name)
    assert isinstance(value, list)
    return value


@pytest.fixture
def scratch_slots():
    yield
    for name in ("acme.list", "acme.scalar", "acme.enum"):
        unregister_slot(name)
    withdraw("python.dev-group", by="acme.brand")


def test_a_contribution_to_an_undeclared_slot_refuses_naming_the_extension() -> None:
    with pytest.raises(
        SlotError,
        match=r"acme\.brand contributes to slot 'acme\.none', which no extension",
    ):
        contribute("acme.none", "x", extension="acme.brand")
    with pytest.raises(SlotError, match="not declared"):
        composed("acme.none")


def test_two_claims_at_one_level_on_a_scalar_refuse_naming_both(scratch_slots) -> None:
    register_slot("acme.scalar", compose=NEAREST, default="Inter")
    contribute("acme.scalar", "Fira", extension="acme.brand", by="acme.brand:theme")
    contribute("acme.scalar", "Lato", extension="acme.brand", by="acme.brand:other")
    with pytest.raises(SlotError, match="two claims at one level") as caught:
        composed("acme.scalar")
    assert "acme.brand:theme" in str(caught.value) and "acme.brand:other" in str(
        caught.value
    )


def test_a_bad_compose_rule_refuses() -> None:
    with pytest.raises(SlotError, match="compose is"):
        register_slot("acme.bad", compose="merge")


def test_a_value_outside_the_declared_values_refuses_naming_them(
    scratch_slots,
) -> None:
    register_slot("acme.enum", compose=NEAREST, default="on", values=("on", "off"))
    with pytest.raises(
        SlotError,
        match=r"acme\.brand:switch contributes 'dim' to slot 'acme\.enum', whose"
        r" values are 'on', 'off'",
    ):
        contribute("acme.enum", "dim", extension="acme.brand", by="acme.brand:switch")
    assert composed("acme.enum") == "on"
    contribute("acme.enum", "off", extension="acme.brand", by="acme.brand:switch")
    assert composed("acme.enum") == "off"


# Then the composition.


def test_a_list_slot_is_the_union_in_order_and_a_scalar_the_nearest(
    scratch_slots,
) -> None:
    register_slot("acme.list")
    assert _list("acme.list") == []
    contribute("acme.list", ["a", "b"], extension="livery.workshop", by="one")
    contribute("acme.list", "b", extension="acme.brand", by="two")
    contribute("acme.list", ["c"], extension="acme.brand", by="three")
    assert _list("acme.list") == ["a", "b", "c"]
    withdraw("acme.list", by="three")
    assert _list("acme.list") == ["a", "b"]
    register_slot("acme.scalar", compose=NEAREST, default="Inter")
    assert composed("acme.scalar") == "Inter"
    contribute("acme.scalar", "Fira", extension="livery.workshop", by="base")
    contribute("acme.scalar", "Lato", extension="acme.brand", by="brand")
    assert composed("acme.scalar") == "Lato"


def test_the_records_fill_the_dev_group_and_addopts_and_a_withdrawn_check_leaves(
    scratch_slots,
) -> None:
    from livery.workshop._checks import check_for, register_check, unregister_check

    group = _list("python.dev-group")
    assert "pytest>=8.0" in group and "pytest-xdist>=3.6" in group
    assert _list("python.test.addopts") == [
        "-q",
        "-n auto",
        "--dist=worksteal",
        "--import-mode=importlib",
    ]
    # An extension's contribution lands beside the records', and leaves with it.
    contribute(
        "python.dev-group", "hypothesis>=6", extension="acme.brand", by="acme.brand"
    )
    assert "hypothesis>=6" in _list("python.dev-group")
    withdraw("python.dev-group", by="acme.brand")
    assert "hypothesis>=6" not in _list("python.dev-group")
    # Unregistering the check that contributes withdraws its lines;
    # registering it again restores them.
    record = check_for("test.pytest")
    unregister_check("test.pytest", by="acme.brand")
    assert "pytest-xdist>=3.6" not in _list("python.dev-group")
    register_check(record)
    assert "pytest-xdist>=3.6" in _list("python.dev-group")


def test_this_workspace_renders_its_dev_group_from_the_slot() -> None:
    from livery.workshop._identity import project_facts
    from livery.workshop._templates import render_injections

    root = Path(__file__).resolve().parents[3]
    injected = render_injections(root, project_facts(root))
    slots = injected["slots"]
    assert "pytest-xdist>=3.6" in slots["python.dev-group"]
    text = (root / "pyproject.toml").read_text()
    for requirement in slots["python.dev-group"]:
        assert f'    "{requirement}",\n' in text
    assert 'addopts = "-q -n auto --dist=worksteal --import-mode=importlib"' in text
    template = (
        root / "packages/workshop/src/livery/workshop/content/root/pyproject.toml.jinja"
    ).read_text()
    assert '"pytest-xdist>=3.6",' not in template
    assert 'addopts = "-q' not in template


def test_a_union_composes_the_same_whatever_order_its_checks_registered() -> None:
    """A check registered again does not move its lines in the union."""
    from livery.workshop import _checks
    from livery.workshop._slots import all_composed

    before = all_composed()["python.dev-group"]
    state = _checks.snapshot()
    try:
        record = _checks.checks_by_name()["test.pytest"]
        _checks.unregister_check("test.pytest", by="a test")
        _checks.register_check(record)
        assert all_composed()["python.dev-group"] == before
    finally:
        _checks.restore(state)
