"""The contract loader: kebab-case keys enforced, and the migration that gets there."""

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


def test_an_underscore_key_refuses_naming_the_kebab_spelling_and_the_verb() -> None:
    message = _refusal(
        lambda: _contract.parse_contract(
            '[ci]\nrequired_context = "gate"\n', where="workshop.toml"
        )
    )
    assert message.startswith("workshop.toml: contract keys are kebab-case")
    assert "ci.required_context (spell it required-context)" in message
    assert "template.apply" in message


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


def test_a_key_the_rewrite_cannot_reach_refuses_by_name(tmp_path: Path) -> None:
    contract = tmp_path / "workshop.toml"
    contract.write_text('[docs]\ncoverage = [{ label_text = "x", path = "y" }]\n')
    message = _refusal(lambda: _contract.migrate_contracts(tmp_path))
    assert "docs.coverage.label_text" in message
    assert "by hand" in message
    # Nothing was written: a contract is never half-migrated.
    assert "label_text" in contract.read_text()


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


def test_normalise_keys_reads_history_as_if_migrated() -> None:
    tree = {"ci": {"required_context": "gate"}, "depends": [{"floor_kind": "x"}]}
    assert _contract.normalise_keys(tree) == {
        "ci": {"required-context": "gate"},
        "depends": [{"floor-kind": "x"}],
    }


def test_migrate_keys_moves_keys_and_nothing_else() -> None:
    text = (
        "# coverage_floor is the high-water mark\n"
        "[qa]\n"
        "coverage_floor = 90  # raise_it deliberately\n"
        'description = "a_value_with_underscores"\n'
        "\n[docs]\n"
        "extra_css = [\n"
        '    "a_b.css",\n'
        "]\n"
    )
    rewritten, moved = _contract.migrate_keys(text)
    assert moved == ["coverage_floor -> coverage-floor", "extra_css -> extra-css"]
    assert "# coverage_floor is the high-water mark" in rewritten
    assert "coverage-floor = 90  # raise_it deliberately" in rewritten
    assert 'description = "a_value_with_underscores"' in rewritten
    assert '    "a_b.css",' in rewritten
    again, moved_again = _contract.migrate_keys(rewritten)
    assert again == rewritten and moved_again == []


def test_migrate_contracts_rewrites_the_root_and_every_member(tmp_path: Path) -> None:
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\ntemplates_artifact = ""\n\n[ci]\nrequired_context = "gate"\n'
    )
    member = tmp_path / "packages" / "thing"
    member.mkdir(parents=True)
    (member / "workshop.toml").write_text(
        'type = "python"\n[qa]\ncoverage_floor = 90\n'
    )
    (tmp_path / "packages" / "no-contract").mkdir()
    notes = _contract.migrate_contracts(tmp_path)
    assert notes == [
        "workshop.toml: templates_artifact -> templates-artifact,"
        " required_context -> required-context",
        "packages/thing/workshop.toml: coverage_floor -> coverage-floor",
    ]
    assert _contract.load_contract(tmp_path / "workshop.toml")["ci"] == {
        "required-context": "gate"
    }
    assert _contract.migrate_contracts(tmp_path) == []
