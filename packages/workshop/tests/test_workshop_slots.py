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


def test_a_compose_reference_refuses_with_value_error_and_the_slot_is_named_once(
    scratch_slots,
) -> None:
    def picky(values: list[object]) -> object:
        if any(not isinstance(value, str) for value in values):
            raise ValueError("every contribution is a string")
        return ",".join(str(value) for value in values)

    def reads_another(values: list[object]) -> object:
        return composed("acme.none")

    register_slot("acme.scalar", compose=picky, extension="acme.brand")
    contribute("acme.scalar", 3, extension="acme.brand", by="acme.brand")
    with pytest.raises(
        SlotError, match=r"^slot 'acme\.scalar': every contribution is a string$"
    ):
        composed("acme.scalar")
    # A reference that reads another slot gets that slot's refusal,
    # which names it already, and it passes through as it is.
    register_slot("acme.scalar", compose=reads_another, extension="acme.brand")
    with pytest.raises(SlotError, match=r"^slot 'acme\.none' is not declared"):
        composed("acme.scalar")


def test_the_public_slot_answers_the_composed_value_and_refuses_as_value_error(
    scratch_slots,
) -> None:
    from livery.workshop import slot

    with pytest.raises(ValueError, match=r"slot 'acme\.none' is not declared"):
        slot("acme.none")
    register_slot("acme.list", extension="acme.brand")
    contribute("acme.list", ["a"], extension="acme.brand", by="acme.brand")
    assert slot("acme.list") == composed("acme.list") == ["a"]


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


def _noop(ctx: object) -> None:
    del ctx


def test_the_records_fill_the_dev_group_and_addopts_and_a_withdrawn_check_leaves(
    scratch_slots,
) -> None:
    from livery.workshop._checks import (
        CheckRecord,
        register_check,
        restore,
        snapshot,
        unregister_check,
    )

    state = snapshot()
    try:
        record = CheckRecord(
            "acme",
            "test",
            _noop,
            extension="acme.runner",
            contributions=(
                ("python.dev-group", "acme-runner>=1"),
                ("python.test.addopts", "--acme"),
            ),
        )
        register_check(record)
        assert "acme-runner>=1" in _list("python.dev-group")
        assert "--acme" in _list("python.test.addopts")
        # An extension's contribution lands beside the records', and
        # leaves with it.
        contribute(
            "python.dev-group", "hypothesis>=6", extension="acme.brand", by="acme.brand"
        )
        assert "hypothesis>=6" in _list("python.dev-group")
        withdraw("python.dev-group", by="acme.brand")
        assert "hypothesis>=6" not in _list("python.dev-group")
        # Unregistering the check that contributes withdraws its lines;
        # registering it again restores them.
        unregister_check("test.acme", by="acme.brand")
        assert "acme-runner>=1" not in _list("python.dev-group")
        assert "--acme" not in _list("python.test.addopts")
        register_check(record)
        assert "acme-runner>=1" in _list("python.dev-group")
    finally:
        restore(state)


def test_this_workspace_renders_its_dev_group_from_the_slot() -> None:
    from livery.workshop._identity import project_facts
    from livery.workshop._templates import render_injections

    root = Path(__file__).resolve().parents[3]
    injected = render_injections(root, project_facts(root))
    slots = injected["slots"]
    text = (root / "pyproject.toml").read_text()
    for requirement in slots["python.dev-group"]:
        assert f'    "{requirement}",\n' in text
    # The template spells no tool's line: the records' contributions do.
    template = (
        root / "packages/workshop/src/livery/workshop/content/root/pyproject.toml.jinja"
    ).read_text()
    assert '"pytest-xdist>=3.6",' not in template
    assert "addopts" not in template


def test_a_union_composes_the_same_whatever_order_its_checks_registered() -> None:
    """A check registered again does not move its lines in the union."""
    from livery.workshop import _checks
    from livery.workshop._slots import all_composed

    state = _checks.snapshot()
    try:
        for name in ("one", "two"):
            _checks.register_check(
                _checks.CheckRecord(
                    name,
                    "test",
                    _noop,
                    extension="acme.runner",
                    contributions=(("python.dev-group", f"acme-{name}>=1"),),
                )
            )
        before = all_composed()["python.dev-group"]
        record = _checks.checks_by_name()["test.one"]
        _checks.unregister_check("test.one", by="a test")
        _checks.register_check(record)
        assert all_composed()["python.dev-group"] == before
    finally:
        _checks.restore(state)
