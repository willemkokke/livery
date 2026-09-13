"""The spec model: the refusals first, then hse's files byte for byte."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from livery.toolroom.store import (
    HOSTS,
    Definition,
    Spec,
    SpecError,
    Version,
    export_schema,
    host_key,
    schema,
)

SPECS = Path(__file__).resolve().parent / "specs"
SHA = "d8b96221828ad6f97ac7ac0ab7e95872341af763001e8803e8267652c2652620"


def _definition(**overrides: object) -> Definition:
    fields: dict[str, object] = {
        "platform": "macos",
        "arch": "arm",
        "url": "https://x/a.zip",
        "sha256": SHA,
    }
    fields.update(overrides)
    return Definition(**fields)  # type: ignore[arg-type]


# --- refusals first ------------------------------------------------------------


def test_a_url_without_a_sha256_is_refused() -> None:
    with pytest.raises(SpecError, match="definition macos-arm: url without sha256"):
        _definition(sha256=None)


def test_a_sha256_that_is_not_hex_is_refused() -> None:
    with pytest.raises(SpecError, match="is not 64 lowercase hex digits"):
        _definition(sha256="ABC")


def test_an_unknown_platform_or_arch_is_refused() -> None:
    with pytest.raises(SpecError, match="platform 'plan9' is not one of"):
        _definition(platform="plan9")
    with pytest.raises(SpecError, match="arch 'mips' is not one of"):
        _definition(arch="mips")


def test_a_definition_key_must_be_its_host() -> None:
    with pytest.raises(
        SpecError, match="definition key 'linux-x64' is not its host 'macos-arm'"
    ):
        Version("1.0", {"linux-x64": _definition()})


def test_a_version_key_must_be_its_version_and_the_pin_must_exist() -> None:
    with pytest.raises(
        SpecError, match=r"version key '2\.0' is not its version '1\.0'"
    ):
        Spec("tool", versions={"2.0": Version("1.0")})
    with pytest.raises(SpecError, match=r"pinned version '9\.9' is not among 1\.0"):
        Spec("tool", pinned="9.9", versions={"1.0": Version("1.0")})


def test_a_download_kind_needs_a_url_and_a_kind_must_be_known() -> None:
    bare = Definition("macos", "arm")
    with pytest.raises(SpecError, match="archive definitions need a url"):
        Spec("tool", versions={"1.0": Version("1.0", {"macos-arm": bare})})
    Spec("tool", kind="uv-tool", versions={"1.0": Version("1.0", {"macos-arm": bare})})
    with pytest.raises(SpecError, match="kind 'magic' is not one of"):
        Spec("tool", kind="magic")
    with pytest.raises(SpecError, match="a spec needs a name"):
        Spec("")


def test_json_that_is_not_a_spec_is_refused_naming_where() -> None:
    with pytest.raises(SpecError, match=r"x\.json: not JSON"):
        Spec.loads("{", where="x.json")
    with pytest.raises(SpecError, match="spec: not a JSON object"):
        Spec.from_json([])
    with pytest.raises(SpecError, match="spec: unknown keys homepage"):
        Spec.from_json({"name": "t", "homepage": "x"})
    with pytest.raises(SpecError, match="spec: no name"):
        Spec.from_json({})
    with pytest.raises(SpecError, match="spec name: not a string"):
        Spec.from_json({"name": 5})
    with pytest.raises(SpecError, match=r"spec versions\[1.0\]: no version"):
        Spec.from_json({"name": "t", "versions": {"1.0": {}}})
    with pytest.raises(SpecError, match=r"definitions\[macos-arm\]: no arch"):
        Spec.from_json(
            {
                "name": "t",
                "versions": {
                    "1.0": {
                        "version": "1.0",
                        "definitions": {"macos-arm": {"platform": "macos"}},
                    }
                },
            }
        )
    with pytest.raises(SpecError, match="paths: not a list of strings"):
        Definition.from_json(
            {"platform": "macos", "arch": "arm", "paths": "bin"}, where="d"
        )
    with pytest.raises(SpecError, match="env: not an object of strings"):
        Definition.from_json(
            {"platform": "macos", "arch": "arm", "env": {"A": 1}}, where="d"
        )
    with pytest.raises(SpecError, match="sha256 is not a string"):
        Definition.from_json(
            {"platform": "macos", "arch": "arm", "sha256": 5}, where="d"
        )
    with pytest.raises(SpecError, match="versions is not an object"):
        Spec.from_json({"name": "t", "versions": []})
    with pytest.raises(SpecError, match="definitions is not an object"):
        Version.from_json({"version": "1", "definitions": []}, where="v")


def test_a_host_the_spec_lacks_is_refused_naming_the_hosts_it_has() -> None:
    spec = Spec.load(SPECS / "bun.json")
    with pytest.raises(
        SpecError,
        match="no definition for windows-arm; the spec carries macos-arm, macos-x64, linux-x64, linux-arm, windows-x64",
    ):
        spec.definition_for("windows-arm")
    with pytest.raises(SpecError, match=r"no version '0\.0\.1'; the spec knows"):
        spec.definition_for("macos-arm", "0.0.1")
    with pytest.raises(SpecError, match="no pinned version"):
        Spec("tool").pinned_version()
    with pytest.raises(SpecError, match="no spec names the host Plan9/mips"):
        host_key("Plan9", "mips")


# --- the shapes -----------------------------------------------------------------


def _key_order(value: object) -> object:
    """The value with every object replaced by its key list, nested."""
    if isinstance(value, dict):
        return [(k, _key_order(v)) for k, v in value.items()]
    if isinstance(value, list):
        return [_key_order(v) for v in value]
    return value


@pytest.mark.parametrize("path", sorted(SPECS.glob("*.json")))
def test_hses_specs_load_and_write_back_the_same_keys_in_the_same_order(
    path: Path,
) -> None:
    if path.name == "package.schema.json":
        return
    original = json.loads(path.read_text("utf-8"))
    spec = Spec.load(path)
    assert spec.name == path.stem
    written = json.loads(spec.dumps())
    assert written == original
    assert _key_order(written) == _key_order(original)


def test_the_pinned_definition_answers_for_the_host() -> None:
    spec = Spec.load(SPECS / "bun.json")
    definition = spec.definition_for(host_key("Darwin", "arm64"))
    assert definition.key == "macos-arm"
    assert definition.url.endswith("bun-darwin-aarch64.zip")
    assert definition.sha256 == SHA
    assert definition.shims == {"node": "bun"}
    assert spec.pinned_version().version == spec.pinned
    assert spec.definition_for("linux-x64", spec.pinned).key == "linux-x64"


def test_every_host_key_has_a_platform_and_arch_pair() -> None:
    assert host_key("Windows", "ARM64") == "windows-arm"
    assert host_key("Windows", "AMD64") == "windows-x64"
    assert host_key("Linux", "x86_64") == "linux-x64"
    assert host_key("Linux", "aarch64") == "linux-arm"
    assert host_key("Darwin", "x86_64") == "macos-x64"
    assert set(HOSTS) == {
        f"{p}-{a}" for p in ("macos", "linux", "windows") for a in ("arm", "x64")
    }


def test_the_schema_mirrors_the_model_and_exports(tmp_path: Path) -> None:
    exported = schema()
    assert exported["additionalProperties"] is False
    assert set(exported["properties"]) == {
        "name",
        "description",
        "kind",
        "pinned",
        "min_version",
        "versions",
    }
    assert set(exported["$defs"]["Definition"]["properties"]) == set(
        Definition("macos", "arm").to_json()
    )
    assert set(exported["$defs"]["Version"]["properties"]) == set(
        Version("1").to_json()
    )
    export_schema(tmp_path / "spec.schema.json")
    assert json.loads((tmp_path / "spec.schema.json").read_text("utf-8")) == exported


def test_a_spec_round_trips_through_save(tmp_path: Path) -> None:
    spec = Spec.load(SPECS / "uv.json")
    spec.save(tmp_path / "uv.json")
    assert Spec.load(tmp_path / "uv.json") == spec
