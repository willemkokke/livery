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
    for name in ("acme.list", "acme.scalar"):
        unregister_slot(name)
    withdraw("python.dev-group", by="acme.brand")


def test_a_contribution_to_an_undeclared_slot_refuses_naming_the_layer() -> None:
    with pytest.raises(
        SlotError, match=r"acme\.brand contributes to slot 'acme\.none', which no layer"
    ):
        contribute("acme.none", "x", layer="acme.brand")
    with pytest.raises(SlotError, match="not declared"):
        composed("acme.none")


def test_two_claims_at_one_level_on_a_scalar_refuse_naming_both(scratch_slots) -> None:
    register_slot("acme.scalar", compose=NEAREST, default="Inter")
    contribute("acme.scalar", "Fira", layer="acme.brand", by="acme.brand:theme")
    contribute("acme.scalar", "Lato", layer="acme.brand", by="acme.brand:other")
    with pytest.raises(SlotError, match="two claims at one level") as caught:
        composed("acme.scalar")
    assert "acme.brand:theme" in str(caught.value) and "acme.brand:other" in str(
        caught.value
    )


def test_a_bad_compose_rule_refuses() -> None:
    with pytest.raises(SlotError, match="compose is"):
        register_slot("acme.bad", compose="merge")


# Then the composition.


def test_a_list_slot_is_the_union_in_order_and_a_scalar_the_nearest(
    scratch_slots,
) -> None:
    register_slot("acme.list")
    assert _list("acme.list") == []
    contribute("acme.list", ["a", "b"], layer="livery.workshop", by="one")
    contribute("acme.list", "b", layer="acme.brand", by="two")
    contribute("acme.list", ["c"], layer="acme.brand", by="three")
    assert _list("acme.list") == ["a", "b", "c"]
    withdraw("acme.list", by="three")
    assert _list("acme.list") == ["a", "b"]
    register_slot("acme.scalar", compose=NEAREST, default="Inter")
    assert composed("acme.scalar") == "Inter"
    contribute("acme.scalar", "Fira", layer="livery.workshop", by="base")
    contribute("acme.scalar", "Lato", layer="acme.brand", by="brand")
    assert composed("acme.scalar") == "Lato"


def test_the_records_fill_the_dev_group_and_addopts_and_a_withdrawn_check_leaves(
    scratch_slots,
) -> None:
    from livery.workshop._checks import check_for, register_check, unregister_check

    group = _list("python.dev-group")
    assert "pytest>=8.0" in group and "mypy>=1.14" in group
    assert _list("python.test.addopts") == [
        "-q",
        "-n auto",
        "--dist=worksteal",
        "--import-mode=importlib",
    ]
    # A layer's contribution lands beside the records', and leaves with it.
    contribute("python.dev-group", "hypothesis>=6", layer="acme.brand", by="acme.brand")
    assert "hypothesis>=6" in _list("python.dev-group")
    withdraw("python.dev-group", by="acme.brand")
    assert "hypothesis>=6" not in _list("python.dev-group")
    # Unregistering the check that contributes withdraws its lines;
    # registering it again restores them.
    record = check_for("typecheck")
    unregister_check("typecheck", by="acme.brand")
    assert "mypy>=1.14" not in _list("python.dev-group")
    register_check(record)
    assert "mypy>=1.14" in _list("python.dev-group")


def test_this_workspace_renders_its_dev_group_from_the_slot() -> None:
    from livery.workshop._templates import read_answers, render_injections

    root = Path(__file__).resolve().parents[3]
    injected = render_injections(root, read_answers(root / ".copier-answers.yml"))
    slots = injected["slots"]
    assert "pytest-xdist>=3.6" in slots["python.dev-group"]
    text = (root / "pyproject.toml").read_text()
    for requirement in slots["python.dev-group"]:
        assert f'    "{requirement}",\n' in text
    assert 'addopts = "-q -n auto --dist=worksteal --import-mode=importlib"' in text
    template = (
        root
        / "packages/workshop/src/livery/workshop/templates/project/pyproject.toml.jinja"
    ).read_text()
    assert '"pytest-xdist>=3.6",' not in template
    assert 'addopts = "-q' not in template
