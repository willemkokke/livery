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


def _workspace(tmp_path: Path, root: str, package: str = "") -> Path:
    (tmp_path / "workshop.toml").write_text("[workspace]\nextensions = []\n" + root)
    if package:
        member = tmp_path / "packages" / "member"
        member.mkdir(parents=True)
        (member / "workshop.toml").write_text(package)
        return member / "workshop.toml"
    return tmp_path / "workshop.toml"


def test_an_unknown_key_refuses_naming_the_known_ones(tmp_path: Path) -> None:
    path = _workspace(tmp_path, '\n[ci]\nrunner = ["ubuntu-latest"]\n')
    message = _refusal(lambda: _contract.load_contract(path))
    assert message.startswith(f"{path}:\n")
    assert "[ci] has no key 'runner': it takes affected-legs, automerge" in message
    assert "; did you mean 'runners'?" in message
    # A package's dead table refuses the same way, at its top level.
    member = _workspace(tmp_path, "", 'kind = "python"\nname = "m"\n[verbs]\n')
    assert "the top level has no key 'verbs': it takes categories" in _refusal(
        lambda: _contract.load_contract(member)
    )


def test_a_table_of_an_unlisted_extension_refuses_naming_the_extension(
    tmp_path: Path,
) -> None:
    path = _workspace(tmp_path, '\n[docs]\ntitle = "Site"\n')
    assert (
        "docs.title is a key of docs, which [workspace]"
        " extensions does not list; list the extension, or remove the key"
    ) in _refusal(lambda: _contract.load_contract(path))
    # A package's key of that extension refuses against its root's list.
    member = _workspace(
        tmp_path, "", 'kind = "python"\nname = "m"\n[docs]\nextra-css = []\n'
    )
    assert "docs.extra-css is a key of docs" in _refusal(
        lambda: _contract.load_contract(member)
    )
    # Listed, the same keys load.
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nextensions = ["docs"]\n\n[docs]\ntitle = "Site"\n'
    )
    assert _contract.load_contract(path)["docs"] == {"title": "Site"}
    assert _contract.load_contract(member)["docs"] == {"extra-css": []}


def test_a_wrong_type_refuses_naming_the_allowed_values(tmp_path: Path) -> None:
    path = _workspace(
        tmp_path,
        '\n[forge]\nkind = "gitub"\nowner = 3\n\n[ci]\nrunners = "ubuntu"\n',
    )
    message = _refusal(lambda: _contract.load_contract(path))
    # Every problem in one refusal, in the contract's order.
    assert message.splitlines()[1:] == [
        "  forge.kind is 'gitub'; it takes one of github, gitea, gitlab;"
        " did you mean 'github'?",
        "  forge.owner is an integer (3); it takes a string",
        "  ci.runners is a string ('ubuntu'); it takes a list of strings",
    ]


def test_a_key_of_a_user_named_table_is_judged_by_its_value(tmp_path: Path) -> None:
    path = _workspace(tmp_path, '\n[tools.modes]\nruff = "link"\nmypy = "copy"\n')
    assert "tools.modes.mypy is 'copy'; it takes one of link, path, none" in (
        _refusal(lambda: _contract.load_contract(path))
    )


def test_two_owners_declaring_one_key_refuse_naming_both(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from livery.workshop import _contract_keys

    class _Entry:
        name = "acme.extension"
        group = "workshop.extensions"

        @staticmethod
        def load() -> object:
            from types import SimpleNamespace

            return SimpleNamespace(
                CONTRACT_KEYS=(_contract_keys.Declared("root", "forge.kind", ("str",)),)
            )

    from livery.footman import _entries  # pyright: ignore[reportPrivateUsage]

    monkeypatch.setattr(_entries, "_SCAN", (_Entry(),))
    _contract_keys.declarations.cache_clear()
    try:
        assert (
            "the root contract key forge.kind is declared by both livery.workshop"
            " and acme.extension"
        ) in _refusal(_contract_keys.declarations)
    finally:
        _contract_keys.declarations.cache_clear()


def test_every_contract_of_this_repository_loads() -> None:
    root = Path(__file__).resolve().parents[3]
    for path in _contract.contract_paths(root):
        _contract.load_contract(path)


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
