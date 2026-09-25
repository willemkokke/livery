"""The contract loader: kebab-case keys enforced, the refusal naming the fix."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from livery.workshop import _contract


def _refusal(action: Callable[[], object]) -> str:
    with pytest.raises(BaseException) as caught:
        action()
    return str(caught.value)


# --- refusals first ---------------------------------------------------------


def test_an_underscore_key_refuses_naming_the_kebab_spelling_and_the_fix() -> None:
    message = _refusal(
        lambda: _contract.parse_contract(
            '[ci]\nrequired_context = "gate"\n', where="workshop.toml"
        )
    )
    assert message.startswith("workshop.toml: contract keys are kebab-case")
    assert "ci.required_context (spell it required-context)" in message
    assert message.endswith("Rename each key in workshop.toml.")
    # No rewrite exists any more: the fix is the edit the message names.
    assert not hasattr(_contract, "migrate_contracts")


def test_nested_tables_and_arrays_of_tables_are_judged_too() -> None:
    text = (
        '[docs]\nextra_css = ["a.css"]\n\n'
        '[[depends]]\npath = "packages/x"\nfloor_kind = "runtime"\n'
    )
    message = _refusal(lambda: _contract.parse_contract(text, where="here"))
    assert "docs.extra_css (spell it extra-css)" in message
    assert "depends.floor_kind (spell it floor-kind)" in message


def test_load_contract_names_the_file(tmp_path: Path) -> None:
    contract = tmp_path / "workshop.toml"
    contract.write_text("[qa]\ncoverage_floor = 90\n")
    message = _refusal(lambda: _contract.load_contract(contract))
    assert message.startswith(f"{contract}: ")


# --- the happy paths ---------------------------------------------------------


def test_kebab_and_single_word_keys_parse() -> None:
    data = _contract.parse_contract(
        '[ci]\nrequired-context = "gate"\nrunners = ["ubuntu-latest"]\n', where="x"
    )
    assert data["ci"]["required-context"] == "gate"


def test_underscore_keys_are_listed_with_their_paths() -> None:
    tree = {"ci": {"required_context": "gate", "schedule": [{"point_name": "n"}]}}
    assert _contract.underscore_keys(tree) == [
        "ci.required_context",
        "ci.schedule.point_name",
    ]


def test_normalise_keys_reads_history_as_if_spelled_right() -> None:
    tree = {"ci": {"required_context": "gate"}, "depends": [{"floor_kind": "x"}]}
    assert _contract.normalise_keys(tree) == {
        "ci": {"required-context": "gate"},
        "depends": [{"floor-kind": "x"}],
    }
