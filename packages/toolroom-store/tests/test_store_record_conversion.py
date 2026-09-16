"""The one-time proof: every spec converts to a record and resolves back whole."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.toolroom.store import Record, resolve
from livery.toolroom.store._spec import Spec
from toolroom_store_conversion import convert

SPECS = Path(__file__).resolve().parent / "specs"
RECORDS = Path(__file__).resolve().parents[3] / "records"
NAMES = sorted(p.stem for p in SPECS.glob("*.json") if p.name != "package.schema.json")


@pytest.mark.parametrize("name", NAMES)
def test_a_spec_converts_to_a_record_that_resolves_every_definition_back(
    name: str, tmp_path: Path
) -> None:
    spec = Spec.load(SPECS / f"{name}.json")
    record = convert(spec)
    for version in spec.versions.values():
        for host, definition in version.definitions.items():
            deployment = resolve(record, version.version, host)
            assert (
                deployment.url,
                deployment.sha256,
                deployment.root,
                deployment.exe,
                deployment.paths,
                deployment.env,
                deployment.shims,
            ) == (
                definition.url,
                definition.sha256,
                definition.root,
                definition.exe,
                definition.paths,
                definition.env,
                definition.shims,
            )
    record.save(tmp_path / name)
    assert Record.load(tmp_path / name) == record
    # The committed record is this conversion, byte for byte.
    committed = Record.load(RECORDS / name)
    assert committed == record
