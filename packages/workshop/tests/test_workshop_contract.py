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


def test_the_old_tools_table_refuses_naming_toolroom(tmp_path: Path) -> None:
    # The table is toolroom's and carries its name; no reader takes
    # [tools], and the refusal's nearest match names the table to use.
    path = _workspace(tmp_path, '\n[tools]\nindex = "records"\n')
    message = _refusal(lambda: _contract.load_contract(path))
    assert "the top level has no key 'tools'" in message
    assert "; did you mean 'toolroom'?" in message
    # A package's requirements refuse the same way.
    member = _workspace(
        tmp_path, "", 'kind = "python"\nname = "m"\n[tools]\nrequires = ["ruff"]\n'
    )
    assert "; did you mean 'toolroom'?" in _refusal(
        lambda: _contract.load_contract(member)
    )
    # Under its owner's name, with no extension listed, the table loads.
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nextensions = []\n\n[toolroom]\nindex = "records"\n'
    )
    assert _contract.load_contract(path)["toolroom"] == {"index": "records"}


def test_read_contract_judges_the_contract_in_a_directory(tmp_path: Path) -> None:
    from livery.workshop import read_contract

    # The refusal first: the public reader judges as the loader does.
    path = _workspace(tmp_path, '\n[ci]\nrunner = ["ubuntu-latest"]\n')
    message = _refusal(lambda: read_contract(path.parent))
    assert message.startswith(f"{path}:\n") and "no key 'runner'" in message
    # Then a root's keys, and a package's, by the directory each sits in.
    member = _workspace(
        tmp_path, "[ci]\nautomerge = true\n", 'kind = "python"\nname = "m"\n'
    )
    assert read_contract(tmp_path)["ci"] == {"automerge": True}
    assert read_contract(member.parent)["name"] == "m"


def test_a_table_of_an_unlisted_extension_refuses_naming_the_extension(
    tmp_path: Path,
) -> None:
    # The root's [docs] is the extension's whole, so it refuses at the table.
    path = _workspace(tmp_path, '\n[docs]\ntitle = "Site"\n')
    assert (
        "docs is a key of docs, which [workspace]"
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


def test_the_release_resolutions_refuse_another_word_and_one_listed_twice(
    tmp_path: Path,
) -> None:
    path = _workspace(tmp_path, '\n[release]\nprove = ["flor", "head", "head"]\n')
    message = _refusal(lambda: _contract.load_contract(path))
    assert message.splitlines()[1:] == [
        "  release.prove lists 'flor'; it takes floor, latest, head;"
        " did you mean 'floor'?",
        "  release.prove lists 'head' twice; it takes each of floor, latest, head once",
    ]
    # A package's list is judged the same way.
    member = _workspace(tmp_path, "", 'kind = "python"\nname = "m"\n')
    member.write_text('kind = "python"\nname = "m"\n\n[release]\nprove = ["late"]\n')
    assert "release.prove lists 'late'; it takes floor, latest, head" in _refusal(
        lambda: _contract.load_contract(member)
    )
    # Each of the three once, in any order, loads; so does none.
    path.write_text(
        '[workspace]\nextensions = []\n\n[release]\nprove = ["head", "floor"]\n'
    )
    assert _contract.load_contract(path)["release"] == {"prove": ["head", "floor"]}
    path.write_text("[workspace]\nextensions = []\n\n[release]\nprove = []\n")
    assert _contract.load_contract(path)["release"] == {"prove": []}


def test_prove_floors_refuses_naming_the_prove_list(tmp_path: Path) -> None:
    refusal = (
        "  [release] prove-floors is not a key: [release] prove lists the"
        " resolutions a release tests each member at, from floor, latest and"
        ' head; prove = ["latest"] leaves the floor out'
    )
    path = _workspace(tmp_path, "\n[release]\nprove-floors = false\n")
    assert _refusal(lambda: _contract.load_contract(path)) == f"{path}:\n{refusal}"
    member = _workspace(
        tmp_path, "", 'kind = "python"\nname = "m"\n\n[release]\nprove-floors = true\n'
    )
    assert _refusal(lambda: _contract.load_contract(member)) == f"{member}:\n{refusal}"


def test_a_key_of_a_user_named_table_is_judged_by_its_value(tmp_path: Path) -> None:
    path = _workspace(tmp_path, '\n[toolroom.modes]\nruff = "link"\nmypy = "copy"\n')
    assert "toolroom.modes.mypy is 'copy'; it takes one of link, path, none" in (
        _refusal(lambda: _contract.load_contract(path))
    )


def test_two_owners_declaring_one_key_refuse_naming_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _contract_keys

    package = tmp_path / "site" / "acme_extension"
    package.mkdir(parents=True)
    (package / "extension.toml").write_text(
        '[contract.root.forge.kind]\ntypes = ["str"]\n'
    )
    monkeypatch.syspath_prepend(str(tmp_path / "site"))

    class _Entry:
        name = "acme.extension"
        group = "workshop.extensions"
        value = "acme_extension"

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


def test_a_contract_is_parsed_once_until_it_or_a_sibling_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Package discovery loads every member's contract on each call, and
    # a sync discovers many times: one parse and judgement serve the
    # process until a file the judgement read changes.
    import os

    member = _workspace(tmp_path, "", 'kind = "python"\nname = "m"\n')
    root = tmp_path / "workshop.toml"
    parsed: list[str] = []
    real = _contract.parse_contract

    def counted(text: str, *, where: str) -> dict[str, object]:
        parsed.append(where)
        return real(text, where=where)

    monkeypatch.setattr(_contract, "parse_contract", counted)
    _contract.forget_contracts()
    first = _contract.load_contract(root)
    assert _contract.load_contract(root) == first
    assert parsed == [str(root)]
    # The caller gets its own copy: an edit never reaches the next load.
    first["workspace"]["extensions"].append("ruff")
    assert _contract.load_contract(root)["workspace"]["extensions"] == []
    # The member's contract: once too, and the root's list is part of
    # its judgement, so a root rewritten re-judges it.
    assert _contract.load_contract(member)["name"] == "m"
    assert _contract.load_contract(member)["name"] == "m"
    assert parsed == [str(root), str(member)]
    stamp = root.stat().st_mtime_ns + 2_000_000_000
    os.utime(root, ns=(stamp, stamp))
    _contract.load_contract(member)
    _contract.load_contract(root)
    assert parsed == [str(root), str(member), str(member), str(root)]
    # The root's judgement reads every package's set: a member rewritten
    # re-judges the root.
    os.utime(member, ns=(stamp, stamp))
    _contract.load_contract(root)
    assert parsed[-1] == str(root) and len(parsed) == 5
    _contract.forget_contracts()
    _contract.load_contract(root)
    assert len(parsed) == 6
