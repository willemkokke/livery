"""The base's own contract keys, read from its contract.toml: refusals first."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from livery.workshop import _contract_keys
from livery.workshop._contract_keys import base_keys, extension_keys
from livery.workshop._declaration import DeclarationError, contract_keys_of


@pytest.fixture
def base_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """The base's file at a path the test writes, read afresh."""
    path = tmp_path / "contract.toml"
    monkeypatch.setattr(_contract_keys, "BASE_FILE", path)
    base_keys.cache_clear()
    extension_keys.cache_clear()
    yield path
    base_keys.cache_clear()
    extension_keys.cache_clear()


# The refusals first.


def test_a_key_of_an_unknown_type_in_the_base_file_refuses_naming_both(
    base_file: Path,
) -> None:
    base_file.write_text('[contract.root.forge.kind]\ntypes = ["strings"]\n')
    with pytest.raises(DeclarationError) as caught:
        base_keys()
    assert str(caught.value).startswith(f"{base_file}: [contract.root.forge.kind]")


def test_only_the_base_declares_the_extension_contract(tmp_path: Path) -> None:
    data = {"contract": {"extension": {"plugin": {"types": ["str"]}}}}
    with pytest.raises(DeclarationError, match="declares keys in root, package"):
        contract_keys_of(data, tmp_path / "extension.toml")
    (found,) = contract_keys_of(
        data, tmp_path / "contract.toml", contracts=("root", "package", "extension")
    )
    assert (found.contract, found.path) == ("extension", "plugin")


def test_a_key_named_like_a_declarations_own_is_a_key_when_it_is_a_table(
    tmp_path: Path,
) -> None:
    data = {
        "contract": {
            "package": {
                "acme": {
                    "types": ["table"],
                    "doc": "the acme table",
                    "values": {"types": ["strs"], "doc": "what acme takes"},
                    "doc-site": {"types": ["str"]},
                }
            }
        }
    }
    found = {item.path: item for item in contract_keys_of(data, tmp_path / "x.toml")}
    assert found["acme"].doc == "the acme table"
    assert found["acme"].values == ()
    assert found["acme.values"].types == ("strs",)
    assert found["acme.values"].doc == "what acme takes"
    assert "acme.doc-site" in found


# Then what the file declares: the values held to the code that reads them,
# and the keys [for.<target>] takes again.


def test_the_values_the_file_writes_are_the_ones_the_code_reads() -> None:
    from livery.toolroom.store import MODES
    from livery.workshop._lfs import KEY
    from livery.workshop._points import CADENCES
    from livery.workshop._registries import (
        _ECOSYSTEM,  # pyright: ignore[reportPrivateUsage]
        _ENV_VARS,  # pyright: ignore[reportPrivateUsage]
    )

    keys = {(item.contract, item.path): item for item in base_keys()}
    assert keys[("root", "tools.modes.*")].values == tuple(MODES)
    assert keys[("root", "ci.schedule[].every")].values == tuple(CADENCES)
    assert keys[("package", "ci.point[].every")].values == tuple(CADENCES)
    registries = {
        path.split(".")[1]
        for contract, path in keys
        if contract == "root"
        and path.startswith("registries.")
        and path.count(".") == 1
    }
    assert registries == set(_ENV_VARS)
    prerelease = {
        path.split(".")[1]
        for contract, path in keys
        if path.startswith("registries.") and path.endswith(".prerelease")
    }
    assert prerelease == set(_ECOSYSTEM)
    assert ("root", f"workspace.{KEY}") in keys


def test_for_a_target_takes_the_checks_jobs_and_contributions_again() -> None:
    keys = {item.path: item for item in extension_keys()}
    mirrored = [
        path for path in keys if path.split(".")[0] in ("checks", "ci", "contributions")
    ]
    assert mirrored
    for path in mirrored:
        twin = keys[f"for.*.{path}"]
        assert (twin.types, twin.values, twin.doc) == (
            keys[path].types,
            keys[path].values,
            keys[path].doc,
        ), path


def test_every_base_key_carries_a_doc() -> None:
    # A key whose table declares a child named doc, an option's own,
    # holds no string doc of its own: TOML has one value per name.
    allowed = {
        ("extension", "checks.*.*.options.*"),
        ("extension", "for.*.checks.*.*.options.*"),
    }
    missing = [
        (item.contract, item.path)
        for item in base_keys()
        if not item.doc and (item.contract, item.path) not in allowed
    ]
    assert missing == []
