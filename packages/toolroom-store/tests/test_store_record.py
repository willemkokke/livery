"""The record model: the refusals first, then resolution, then this repository's records."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from livery.toolroom.store import (
    HOSTS,
    Artifact,
    Deployment,
    Layout,
    Record,
    RecordDelta,
    RecordError,
    export_schema,
    host_key,
    resolve,
    schema,
)

ROOT = Path(__file__).resolve().parents[3]
RECORDS = ROOT / "records"
SHA = "d8b96221828ad6f97ac7ac0ab7e95872341af763001e8803e8267652c2652620"


def _artifact(host: str = "macos-arm", version: str = "1") -> Artifact:
    return Artifact(f"https://x/{version}/{host}.zip", SHA)


def _delta(
    sequence: int = 1,
    version: str = "1",
    hosts: tuple[str, ...] = ("macos-arm", "linux-x64"),
    layout: Layout | None = None,
    host_layouts: dict[str, Layout] | None = None,
) -> RecordDelta:
    return RecordDelta(
        sequence,
        version,
        "",
        {host: _artifact(host, version) for host in hosts},
        layout or Layout(),
        host_layouts or {},
    )


def _record(**overrides: object) -> Record:
    fields: dict[str, object] = {
        "name": "tool",
        "kind": "archive",
        "hosts": ("macos-arm", "linux-x64"),
        "layout": Layout(entry_points=("bin/tool",), paths=("bin",)),
        "deltas": (_delta(),),
    }
    fields.update(overrides)
    return Record(**fields)  # type: ignore[arg-type]


# --- refusals first ------------------------------------------------------------


def test_an_artifact_needs_a_url_and_a_lowercase_hex_sha256() -> None:
    with pytest.raises(RecordError, match=r"not 64 lowercase hex digits"):
        Artifact("https://x/a.zip", "ABC")
    with pytest.raises(RecordError, match=r"without a url"):
        Artifact("", SHA)


def test_a_host_outside_the_six_and_a_kind_outside_the_list_are_refused() -> None:
    with pytest.raises(RecordError, match=r"host 'freebsd-x64' is not one of"):
        _record(hosts=("freebsd-x64",), deltas=())
    with pytest.raises(RecordError, match=r"kind 'brew' is not one of"):
        _record(kind="brew")
    with pytest.raises(RecordError, match=r"listed twice"):
        _record(hosts=("macos-arm", "macos-arm"))


def test_an_override_naming_a_host_or_version_the_record_lacks_is_refused() -> None:
    # The tool's layer for a host the tool does not have.
    with pytest.raises(
        RecordError, match=r"windows-x64 layout names a host the record lacks"
    ):
        _record(host_layouts={"windows-x64": Layout(root="w")})
    # A version's artifact for a host the tool does not have.
    with pytest.raises(
        RecordError, match=r"artifact for windows-x64, a host the record lacks"
    ):
        _record(deltas=(_delta(hosts=("macos-arm", "windows-x64")),))
    # A version's layer for a host that version does not have.
    with pytest.raises(
        RecordError, match=r"linux-x64 layout for a host the version lacks"
    ):
        _record(
            deltas=(
                _delta(
                    hosts=("macos-arm",), host_layouts={"linux-x64": Layout(root="l")}
                ),
            )
        )


def test_an_override_restating_what_it_inherits_is_refused() -> None:
    # The tool's layout restating the built-in default is dead data.
    with pytest.raises(RecordError, match=r"the tool's layout restates root"):
        _record(layout=Layout(root="", paths=("bin",)))
    # A host restating the tool's value.
    with pytest.raises(RecordError, match=r"tool's linux-x64 layout restates paths"):
        _record(host_layouts={"linux-x64": Layout(paths=("bin",))})
    # A version restating the tool's value.
    with pytest.raises(RecordError, match=r"version 1's layout restates paths"):
        _record(deltas=(_delta(layout=Layout(paths=("bin",))),))
    # A version's host restating what the version set.
    with pytest.raises(
        RecordError, match=r"version 1's macos-arm layout restates root"
    ):
        _record(
            deltas=(
                _delta(
                    layout=Layout(root="r"),
                    host_layouts={"macos-arm": Layout(root="r")},
                ),
            )
        )


def test_a_version_whose_host_resolves_incomplete_is_refused() -> None:
    with pytest.raises(RecordError, match=r"resolves incomplete, paths is empty"):
        _record(layout=Layout(entry_points=("tool",)))
    with pytest.raises(RecordError, match=r"a binary names no exe"):
        _record(kind="binary")
    # What goes on PATH is declared, never discovered: a downloaded
    # kind with no entry point, and a binary whose exe is not among
    # them, are incomplete.
    with pytest.raises(RecordError, match=r"no entry point is declared"):
        _record(layout=Layout(paths=("bin",)))
    with pytest.raises(RecordError, match=r"exe 'tool' is not among its entry points"):
        _record(
            kind="binary",
            layout=Layout(exe="tool", entry_points=("other",), paths=(".",)),
        )
    with pytest.raises(RecordError, match=r"a archive version needs an artifact"):
        _record(deltas=(_delta(hosts=()),))
    with pytest.raises(RecordError, match=r"a layout for a version with no host"):
        _record(
            kind="uv-tool",
            layout=Layout(),
            deltas=(_delta(hosts=(), layout=Layout(root="r")),),
        )


def test_a_delta_out_of_sequence_or_repeating_a_version_is_refused() -> None:
    with pytest.raises(
        RecordError, match=r"0002-1\.json: out of sequence; expected 0001"
    ):
        _record(deltas=(_delta(sequence=2),))
    with pytest.raises(
        RecordError, match=r"0003-3\.json: out of sequence; expected 0002"
    ):
        _record(deltas=(_delta(1, "1"), _delta(3, "3")))
    with pytest.raises(RecordError, match=r"version 1 was added before"):
        _record(deltas=(_delta(1, "1"), _delta(2, "1")))
    with pytest.raises(RecordError, match=r"sequence is not a positive integer"):
        RecordDelta.from_json(
            {"sequence": 0, "version": "1", "artifacts": {}}, where="d"
        )


def test_json_that_is_not_a_record_is_refused_naming_where(tmp_path: Path) -> None:
    with pytest.raises(RecordError, match=r"tool.json: unknown keys pinned"):
        Record.from_json({"name": "t", "hosts": [], "pinned": "1"})
    with pytest.raises(RecordError, match=r"tool.json: no name"):
        Record.from_json({"hosts": []})
    with pytest.raises(RecordError, match=r"d artifacts\[macos-arm\]: no sha256"):
        RecordDelta.from_json(
            {"sequence": 1, "version": "1", "artifacts": {"macos-arm": {"url": "u"}}},
            where="d",
        )
    with pytest.raises(RecordError, match=r"d layout paths: not a list of strings"):
        RecordDelta.from_json(
            {
                "sequence": 1,
                "version": "1",
                "artifacts": {},
                "layout": {"paths": "bin"},
            },
            where="d",
        )
    with pytest.raises(RecordError, match=r"no tool.json"):
        Record.load(tmp_path / "missing")
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "tool.json").write_text("{", encoding="utf-8")
    with pytest.raises(RecordError, match=r"t/tool.json: not JSON"):
        Record.load(tmp_path / "t")


def test_every_json_shape_outside_the_model_is_refused_naming_where() -> None:
    with pytest.raises(RecordError, match=r"tool.json: not a JSON object"):
        Record.from_json(["not", "an", "object"])
    with pytest.raises(RecordError, match=r"tool.json name: not a string"):
        Record.from_json({"name": 7, "hosts": []})
    with pytest.raises(RecordError, match=r"tool.json: host_layouts is not an object"):
        Record.from_json({"name": "t", "hosts": [], "host_layouts": []})
    with pytest.raises(
        RecordError, match=r"tool.json layout env: not an object of strings"
    ):
        Record.from_json({"name": "t", "hosts": [], "layout": {"env": {"A": 1}}})
    with pytest.raises(RecordError, match=r"d: no version"):
        RecordDelta.from_json({"sequence": 1, "artifacts": {}}, where="d")
    with pytest.raises(RecordError, match=r"d: artifacts is not an object"):
        RecordDelta.from_json(
            {"sequence": 1, "version": "1", "artifacts": []}, where="d"
        )
    with pytest.raises(RecordError, match=r"d: host_layouts is not an object"):
        RecordDelta.from_json(
            {"sequence": 1, "version": "1", "artifacts": {}, "host_layouts": 3},
            where="d",
        )
    with pytest.raises(
        RecordError, match=r"d artifacts\[macos-arm\]: artifact sha256 'xyz'"
    ):
        RecordDelta.from_json(
            {
                "sequence": 1,
                "version": "1",
                "artifacts": {"macos-arm": {"url": "u", "sha256": "xyz"}},
            },
            where="d",
        )
    with pytest.raises(RecordError, match=r"a record needs a name"):
        Record("", hosts=())


def test_a_record_with_no_version_yet_is_validated_on_its_own(tmp_path: Path) -> None:
    # Nothing reads its layout yet, so only a restatement of the
    # built-in default is a refusal; a clean one saves and loads whole.
    with pytest.raises(RecordError, match=r"the tool's layout restates exe"):
        Record("young", hosts=("macos-arm",), layout=Layout(exe=""))
    young = Record("young", hosts=("macos-arm",))
    young.save(tmp_path / "young")
    assert Record.load(tmp_path / "young") == young
    assert (tmp_path / "young" / "deltas").is_dir()
    written = json.loads((tmp_path / "young" / "tool.json").read_text("utf-8"))
    assert "layout" not in written
    # A version-level layout writes under its own key and reads back.
    grown = _record(deltas=(_delta(layout=Layout(root="v1")),))
    grown.save(tmp_path / "tool")
    delta = json.loads(
        (tmp_path / "tool" / "deltas" / "0001-1.json").read_text("utf-8")
    )
    assert delta["layout"] == {"root": "v1"}
    assert Record.load(tmp_path / "tool") == grown


def test_a_delta_file_must_be_named_by_its_sequence_and_version(tmp_path: Path) -> None:
    record = _record()
    record.save(tmp_path / "tool")
    deltas = tmp_path / "tool" / "deltas"
    (deltas / "0001-1.json").rename(deltas / "0001-2.json")
    with pytest.raises(RecordError, match=r"0001-2\.json: the file says 0001-1\.json"):
        Record.load(tmp_path / "tool")
    (deltas / "0001-2.json").rename(deltas / "first.json")
    with pytest.raises(
        RecordError, match=r"first.json: not named <nnnn>-<version>.json"
    ):
        Record.load(tmp_path / "tool")


def test_a_record_must_be_named_as_its_directory(tmp_path: Path) -> None:
    _record().save(tmp_path / "other")
    with pytest.raises(RecordError, match=r"named 'tool', its directory 'other'"):
        Record.load(tmp_path / "other")


def test_resolution_refuses_a_version_or_host_the_record_lacks_naming_what_it_has() -> (
    None
):
    record = _record()
    with pytest.raises(RecordError, match=r"no version '2'; the record tracks 1"):
        resolve(record, "2", "macos-arm")
    with pytest.raises(
        RecordError, match=r"no host windows-x64; the version has macos-arm, linux-x64"
    ):
        resolve(record, "1", "windows-x64")


def test_a_host_no_record_can_name_is_refused() -> None:
    with pytest.raises(RecordError, match=r"no record names the host Plan9/mips"):
        host_key("Plan9", "mips")


# --- resolution ----------------------------------------------------------------


def test_the_four_layers_resolve_most_specific_winning() -> None:
    record = _record(
        hosts=("macos-arm", "linux-x64", "windows-x64"),
        layout=Layout(entry_points=("bin/tool",), paths=("bin",), env={"A": "tool"}),
        host_layouts={
            "windows-x64": Layout(entry_points=("cmd/tool.exe",), paths=("cmd",))
        },
        deltas=(
            _delta(1, "1", hosts=("macos-arm", "linux-x64", "windows-x64")),
            _delta(
                2,
                "2",
                hosts=("macos-arm", "linux-x64", "windows-x64"),
                layout=Layout(root="v2"),
                host_layouts={"linux-x64": Layout(root="v2-linux", env={"A": "linux"})},
            ),
        ),
    )
    assert resolve(record, "1", "macos-arm") == Deployment(
        "https://x/1/macos-arm.zip",
        SHA,
        "",
        "",
        ("bin/tool",),
        ("bin",),
        {"A": "tool"},
        {},
        (),
    )
    assert resolve(record, "1", "windows-x64").paths == ("cmd",)
    assert resolve(record, "1", "windows-x64").entry_points == ("cmd/tool.exe",)
    two_mac = resolve(record, "2", "macos-arm")
    assert (two_mac.root, two_mac.paths, two_mac.env) == ("v2", ("bin",), {"A": "tool"})
    two_win = resolve(record, "2", "windows-x64")
    assert (two_win.root, two_win.paths) == ("v2", ("cmd",))
    two_linux = resolve(record, "2", "linux-x64")
    assert (two_linux.root, two_linux.env) == ("v2-linux", {"A": "linux"})
    assert record.versions == ("1", "2")
    assert record.hosts_of("2") == ("macos-arm", "linux-x64", "windows-x64")


def test_a_record_round_trips_through_save_and_load(tmp_path: Path) -> None:
    record = _record(
        host_layouts={"linux-x64": Layout(root="l")},
        deltas=(
            _delta(1, "1"),
            _delta(
                2, "2", host_layouts={"macos-arm": Layout(root="m", exclude=("*.txt",))}
            ),
        ),
    )
    record.save(tmp_path / "tool")
    assert sorted(p.name for p in (tmp_path / "tool" / "deltas").iterdir()) == [
        "0001-1.json",
        "0002-2.json",
    ]
    loaded = Record.load(tmp_path / "tool")
    assert loaded == record
    written = json.loads((tmp_path / "tool" / "tool.json").read_text("utf-8"))
    assert list(written) == [
        "name",
        "description",
        "kind",
        "min_version",
        "hosts",
        "layout",
        "host_layouts",
    ]
    assert written["host_layouts"] == {"linux-x64": {"root": "l"}}
    delta = json.loads(
        (tmp_path / "tool" / "deltas" / "0002-2.json").read_text("utf-8")
    )
    assert list(delta) == ["sequence", "version", "date", "artifacts", "host_layouts"]
    assert delta["host_layouts"]["macos-arm"] == {"root": "m", "exclude": ["*.txt"]}


def test_every_host_key_has_a_platform_and_arch_pair() -> None:
    assert host_key("Darwin", "arm64") == "macos-arm"
    assert host_key("Windows", "AMD64") == "windows-x64"
    assert host_key("Linux", "aarch64") == "linux-arm"
    for key in HOSTS:
        platform, arch = key.split("-")
        assert platform in ("windows", "macos", "linux") and arch in ("x64", "arm")


def test_the_schema_names_both_documents_and_exports(tmp_path: Path) -> None:
    shape = schema()
    assert [ref["$ref"] for ref in shape["oneOf"]] == ["#/$defs/Tool", "#/$defs/Delta"]
    assert shape["$defs"]["Tool"]["required"] == ["name", "hosts"]
    assert shape["$defs"]["Delta"]["required"] == ["sequence", "version", "artifacts"]
    assert set(shape["$defs"]["Layout"]["properties"]) == {
        "root",
        "exe",
        "entry_points",
        "paths",
        "env",
        "shims",
        "exclude",
    }
    export_schema(tmp_path / "record.schema.json")
    assert json.loads((tmp_path / "record.schema.json").read_text("utf-8")) == shape
    assert json.loads((RECORDS / "record.schema.json").read_text("utf-8")) == shape


# --- this repository's records ------------------------------------------------


def test_every_host_of_every_version_of_every_record_resolves_whole() -> None:
    names = sorted(p.name for p in RECORDS.iterdir() if p.is_dir())
    assert names == ["bun", "eclint", "git", "prek", "tea", "ty", "uv"]
    for name in names:
        record = Record.load(RECORDS / name)
        assert record.versions, name
        for version in record.versions:
            for host in record.hosts_of(version):
                deployment = resolve(record, version, host)
                assert deployment.url and deployment.sha256, (name, version, host)
                assert deployment.paths, (name, version, host)
                if record.kind == "binary":
                    assert deployment.exe in deployment.entry_points, (
                        name,
                        version,
                        host,
                    )
                if record.kind in ("archive", "binary"):
                    assert deployment.entry_points, (name, version, host)
