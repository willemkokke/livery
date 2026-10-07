"""Each contract's JSON Schema, composed from its declared keys: the fallbacks first."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from livery.workshop._contract_keys import (
    BASE,
    ContractKind,
    judge,
    listed_extensions,
)
from livery.workshop._schema import DIRECTORY, FILES, compose
from workshop_schema import problems

ROOT = Path(__file__).resolve().parents[3]


def _listed() -> frozenset[str]:
    """The extensions this repository's root contract lists, the base among them."""
    return listed_extensions(tomllib.loads((ROOT / "workshop.toml").read_text()))


# The fallbacks first: an extension that is not listed, and a key the
# author names.


def test_an_extension_installed_and_not_listed_adds_nothing() -> None:
    alone = compose("root", frozenset({BASE}))
    assert problems(alone, {"docs": {"title": "Site"}}) == ["docs.title: not declared"]
    listed = compose("root", frozenset({BASE, "docs"}))
    assert problems(listed, {"docs": {"title": "Site"}}) == []


def test_a_key_the_author_names_is_no_fixed_property() -> None:
    schema = compose("extension", None)
    options = schema["properties"]["options"]
    assert "properties" not in options
    assert options["additionalProperties"] == {"type": "string"}
    assert problems(schema, {"options": {"deep": "judges deeper"}}) == []
    assert problems(schema, {"options": {"deep": 3}}) == ["options.deep: is not string"]
    # Two named levels: a check's tool, then its role.
    checks = schema["properties"]["checks"]
    assert "properties" not in checks
    role = checks["additionalProperties"]["additionalProperties"]
    assert "run" in role["properties"]


@pytest.mark.parametrize(
    "wrong",
    [
        {"forge": {"kind": "bitbucket"}},
        {"forge": {"owner": 3}},
        {"workspace": {"extension": []}},
        {"workspace": {"extensions": [3]}},
    ],
)
def test_the_schema_refuses_what_the_judge_refuses(wrong: dict[str, object]) -> None:
    listed = _listed()
    assert judge(wrong, contract="root", where="workshop.toml", listed=listed)
    assert problems(compose("root", listed), wrong)


# Then what the composed files accept, and where the sync puts them.


def test_the_schemas_accept_every_contract_the_judge_accepts() -> None:
    from livery.footman import installed_entry_points
    from livery.workshop._declaration import declaration_file
    from livery.workshop._packages import discover_packages

    listed = _listed()
    contracts: list[tuple[ContractKind, Path]] = [("root", ROOT / "workshop.toml")]
    for package in discover_packages(ROOT):
        contracts.append(("package", package.directory / "workshop.toml"))
    for entry in installed_entry_points("workshop.extensions"):
        found = declaration_file(entry.value.partition(":")[0])
        if found is not None:
            contracts.append(("extension", found))
    assert len(contracts) > 2
    schemas = {name: compose(name, listed) for name in FILES}
    for contract, path in contracts:
        data = tomllib.loads(path.read_text("utf-8"))
        judged = judge(
            data,
            contract=contract,
            where=str(path),
            listed=None if contract == "extension" else listed,
        )
        # A release leg installs this wheel alone, where the judge refuses
        # a key of an extension that is listed and absent: then so does
        # the schema.
        assert bool(problems(schemas[contract], data)) == bool(judged), path


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
