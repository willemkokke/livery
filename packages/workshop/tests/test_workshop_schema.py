"""Each contract's JSON Schema and its judge: the refusals first."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any, cast

import pytest

from livery.workshop._contract_keys import (
    BASE,
    EXTENSION,
    ContractKind,
    declarations,
)
from livery.workshop._schema import (
    DIRECTORY,
    FILES,
    OWNER,
    compose,
    composed,
    problems,
    schema_files,
)

#: How a refusal names each declared type, as the judge always has.
_WORDS = {
    "str": "a string",
    "int": "an integer",
    "number": "a number",
    "bool": "true or false",
    "strs": "a list of strings",
    "list": "a list",
    "table": "a table",
    "any": "any value",
}


# The refusals first: an unlisted extension's key, a key the author
# names, a list, and the words each refusal uses.


def test_an_unlisted_extensions_key_is_refused_naming_its_owner() -> None:
    alone = compose("root", frozenset({BASE}))
    assert problems(alone, {"docs": {"title": "Site"}}) == [
        "docs.title is a key of docs, which [workspace] extensions does not list;"
        " list the extension, or remove the key"
    ]
    # The editor refuses it too, and its hover names the extension.
    title = alone["properties"]["docs"]["properties"]["title"]
    assert title["not"] == {} and title[OWNER] == "docs"
    assert "docs" in title["description"]
    listed = compose("root", frozenset({BASE, "docs"}))
    assert problems(listed, {"docs": {"title": "Site"}}) == []


def test_a_key_the_author_names_is_no_fixed_property() -> None:
    schema = compose("extension", None)
    options = schema["properties"]["options"]
    assert "properties" not in options
    assert options["additionalProperties"] == {"type": "string"}
    assert problems(schema, {"options": {"deep": "judges deeper"}}) == []
    assert problems(schema, {"options": {"deep": 3}}) == [
        "options.deep is an integer (3); it takes a string"
    ]
    # Two named levels: a check's tool, then its role.
    checks = schema["properties"]["checks"]
    assert "properties" not in checks
    role = checks["additionalProperties"]["additionalProperties"]
    assert "run" in role["properties"]


def test_a_list_of_strings_is_refused_whole_and_a_lists_entries_one_by_one() -> None:
    schema = compose("extension", None)
    assert problems(schema, {"extension": {"requires": ["a", 3]}}) == [
        "extension.requires is a list (['a', 3]); it takes a list of strings"
    ]
    assert problems(schema, {"extension": {"levels": ["workspace", "house"]}}) == [
        "extension.levels[] is 'house'; it takes one of workspace, package"
    ]
    # A documented list of strings is refused whole as well.
    package = compose("package", None)
    generators = {"docs": {"generators": [{"verb": "v", "requires": ["zsh", 3]}]}}
    assert problems(package, generators) == [
        "docs.generators[].requires is a list (['zsh', 3]); it takes a list of strings"
    ]


def _keys(contract: ContractKind) -> list[tuple[str, tuple[str, ...]]]:
    if contract == "extension":
        return [(item.path, tuple(item.types)) for item in EXTENSION]
    return [
        (path, tuple(owned.declared.types))
        for (kind, path), owned in declarations().items()
        if kind == contract
    ]


def _branch(node: dict[str, Any], kind: str) -> dict[str, Any] | None:
    """*node*'s branch of JSON Schema type *kind*, among its choices or alone."""
    branches = cast("list[dict[str, Any]]", node.get("anyOf", [node]))
    return next((branch for branch in branches if branch.get("type") == kind), None)


def _at(schema: dict[str, Any], path: str) -> dict[str, Any] | None:
    """The schema a declared *path* composes to; None beneath a key taking any value.

    The judge looks at nothing beneath such a key, its reader does, so
    the schema declares nothing there either.
    """
    node = schema
    for segment in path.split("."):
        name = segment.removesuffix("[]")
        table = _branch(node, "object")
        if table is None:
            return None
        properties = cast("dict[str, dict[str, Any]]", table.get("properties", {}))
        found = properties.get(name, table.get("additionalProperties"))
        assert isinstance(found, dict), path
        node = cast("dict[str, Any]", found)
        if segment.endswith("[]"):
            array = _branch(node, "array")
            assert array is not None, path
            node = cast("dict[str, Any]", array["items"])
    return node


@pytest.mark.parametrize("contract", ["root", "package", "extension"])
def test_every_declared_keys_refusal_names_what_it_takes_in_the_judges_words(
    contract: ContractKind,
) -> None:
    from livery.workshop._schema import _spoken

    schema = compose(contract, None)
    for path, types in _keys(contract):
        node = _at(schema, path)
        if node is None:
            continue
        branches = cast("list[dict[str, Any]]", node.get("anyOf", [node]))
        spoken = " or ".join(_spoken(branch) for branch in branches)
        assert spoken == " or ".join(_WORDS[kind] for kind in types), path


# Then the one statement: the file the editor reads is what the judge
# validates against, written for this checkout alone.


def test_the_schema_file_is_what_the_judge_validates_against(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "workshop.toml").write_text('[workspace]\nextensions = ["docs"]\n')
    files = schema_files(root)
    listed = frozenset({BASE, "docs"})
    for contract, name in FILES.items():
        written = json.loads(files[f"{DIRECTORY}/{name}"])
        assert written == composed(contract, listed), name


def test_the_sync_writes_each_schema_for_this_checkout_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._shipped_files import _schema_outputs, deliver

    root = tmp_path / "ws"
    root.mkdir()
    (root / "workshop.toml").write_text('[workspace]\nextensions = ["docs"]\n')
    outputs = _schema_outputs(root)
    assert [output.path for output in outputs] == [
        f"{DIRECTORY}/{name}" for name in FILES.values()
    ]
    assert all(output.local for output in outputs)
    monkeypatch.setattr(
        "livery.workshop._extensions.workspace_root", lambda start=None: root
    )
    deliver(root)
    written = json.loads((root / DIRECTORY / FILES["root"]).read_text())
    assert "title" in written["properties"]["docs"]["properties"]
    # The rules point each contract at its own schema, the root's and a
    # package's apart.
    rules = tomllib.loads((root / ".taplo.toml").read_text())["rule"]
    assert [(rule["include"], rule["schema"]["path"]) for rule in rules] == [
        (["workshop.toml"], f"{DIRECTORY}/{FILES['root']}"),
        (["packages/**/workshop.toml"], f"{DIRECTORY}/{FILES['package']}"),
        (["**/extension.toml"], f"{DIRECTORY}/{FILES['extension']}"),
    ]
