"""The record model: the refusals first, then resolution, then this repository's records."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from livery.toolroom.store import (
    HOSTS,
    OPTION_KEYS,
    VERB_KEYS,
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


def _lines(path: Path) -> list[str]:
    return path.read_text("utf-8").splitlines()


def _record(**overrides: object) -> Record:
    fields: dict[str, object] = {
        "name": "tool",
        "kind": "download",
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
    with pytest.raises(RecordError, match=r"an entry point is declared with no paths"):
        _record(layout=Layout(entry_points=("tool",)))
    # What goes on PATH is declared, never discovered: a path directory
    # comes with its entry points, an entry point with a directory.
    with pytest.raises(RecordError, match=r"no entry point is declared"):
        _record(layout=Layout(paths=("bin",)))
    # A download nothing reaches is refused; env under $package reaches it.
    with pytest.raises(RecordError, match=r"nothing reaches it"):
        _record(layout=Layout(file="f", format="file"))
    _record(layout=Layout(file="f", format="file", env={"F": "$package/f"}))
    # A bare download names the file it lands as, and a bare program's
    # file is among its entry points.
    with pytest.raises(RecordError, match=r"a bare download names no file"):
        _record(layout=Layout(format="file", entry_points=("tool",), paths=(".",)))
    with pytest.raises(RecordError, match=r"format 'iso' is not one of"):
        _record(layout=Layout(file="f", format="iso", env={"F": "$package/f"}))
    with pytest.raises(RecordError, match=r"entry points are its file 'tool' alone"):
        _record(
            layout=Layout(
                file="tool", format="file", entry_points=("other",), paths=(".",)
            ),
        )
    with pytest.raises(RecordError, match=r"alone, not tool, tool2"):
        _record(
            layout=Layout(
                file="tool", format="file", entry_points=("tool", "tool2"), paths=(".",)
            ),
        )
    with pytest.raises(
        RecordError, match=r"a version with neither an artifact nor a surface"
    ):
        _record(deltas=(_delta(hosts=()),))
    with pytest.raises(RecordError, match=r"a layout for a version with no host"):
        _record(
            kind="pypi",
            layout=Layout(),
            deltas=(_delta(hosts=(), layout=Layout(root="r")),),
        )


def test_a_mode_outside_the_three_is_refused_and_the_kinds_default_by_shape(
    tmp_path: Path,
) -> None:
    from livery.toolroom.store import MODES, default_mode

    with pytest.raises(RecordError, match=r"tool: mode 'float' is not one of link"):
        _record(mode="float")
    assert MODES == ("link", "path", "none")
    # A download with paths goes on PATH; one reached through env alone
    # takes none; a system tool is on PATH already.
    assert default_mode("download", ("bin",)) == "path"
    assert default_mode("download") == "none"
    assert default_mode("system-check") == "none"
    assert {default_mode(k) for k in ("pypi", "npm")} == {"path"}
    # A runtime is an npm record's, one of the two, written after the package.
    npm: dict[str, object] = {"kind": "npm", "hosts": (), "deltas": ()}
    with pytest.raises(RecordError, match=r"runtime 'deno' is not one of node, bun"):
        _record(**npm, runtime="deno")
    with pytest.raises(RecordError, match=r"a runtime is named by an npm record"):
        _record(runtime="bun")
    assert list(_record(**npm, runtime="bun").to_json())[:5] == [
        "name",
        "description",
        "kind",
        "runtime",
        "min_version",
    ]
    # The package and the mode ride the tool axis, written only when set.
    bare = _record()
    assert "package" not in bare.to_json() and "mode" not in bare.to_json()
    told = _record(kind="pypi", package="the-dist", mode="link")
    told.save(tmp_path)
    loaded = Record.load(tmp_path / "tool.jsonl")
    assert (loaded.package, loaded.mode) == ("the-dist", "link")
    written = json.loads(_lines(tmp_path / "tool.jsonl")[0])
    assert list(written)[:5] == ["name", "description", "kind", "package", "mode"]


def test_a_delta_out_of_sequence_or_repeating_a_version_is_refused() -> None:
    with pytest.raises(RecordError, match=r"version 1: out of sequence; expected 1"):
        _record(deltas=(_delta(sequence=2),))
    with pytest.raises(RecordError, match=r"version 3: out of sequence; expected 2"):
        _record(deltas=(_delta(1, "1"), _delta(3, "3")))
    with pytest.raises(RecordError, match=r"version 1 was added before"):
        _record(deltas=(_delta(1, "1"), _delta(2, "1")))


def _parsed(*lines: object, where: str = "d") -> Record:
    return Record.parse("\n".join(json.dumps(line) for line in lines), where=where)


def test_json_that_is_not_a_record_is_refused_naming_where(tmp_path: Path) -> None:
    with pytest.raises(RecordError, match=r"line 1: unknown keys pinned"):
        Record.from_json({"name": "t", "hosts": [], "pinned": "1"})
    with pytest.raises(RecordError, match=r"line 1: no name"):
        Record.from_json({"hosts": []})
    axis = {"name": "t", "hosts": ["macos-arm"]}
    with pytest.raises(
        RecordError, match=r"d line 2 artifacts\[macos-arm\]: no sha256"
    ):
        _parsed(axis, {"version": "1", "artifacts": {"macos-arm": {"url": "u"}}})
    with pytest.raises(
        RecordError, match=r"d line 2 layout paths: not a list of strings"
    ):
        _parsed(axis, {"version": "1", "artifacts": {}, "layout": {"paths": "bin"}})
    with pytest.raises(RecordError, match=r"missing: no such record"):
        Record.load(tmp_path / "missing")
    (tmp_path / "t").mkdir()
    with pytest.raises(RecordError, match=r"a directory, not a record file"):
        Record.load(tmp_path / "t")
    (tmp_path / "t.jsonl").write_text("{", encoding="utf-8")
    with pytest.raises(RecordError, match=r"t.jsonl line 1: not JSON"):
        Record.load(tmp_path / "t.jsonl")
    (tmp_path / "e.jsonl").write_text("\n", encoding="utf-8")
    with pytest.raises(RecordError, match=r"e.jsonl: empty"):
        Record.load(tmp_path / "e.jsonl")


def test_every_json_shape_outside_the_model_is_refused_naming_where() -> None:
    with pytest.raises(RecordError, match=r"line 1: not a JSON object"):
        Record.from_json(["not", "an", "object"])
    with pytest.raises(RecordError, match=r"line 1 name: not a string"):
        Record.from_json({"name": 7, "hosts": []})
    with pytest.raises(RecordError, match=r"line 1: host_layouts is not an object"):
        Record.from_json({"name": "t", "hosts": [], "host_layouts": []})
    with pytest.raises(
        RecordError, match=r"line 1 layout env: not an object of strings"
    ):
        Record.from_json({"name": "t", "hosts": [], "layout": {"env": {"A": 1}}})
    axis = {"name": "t", "hosts": ["macos-arm"]}
    with pytest.raises(RecordError, match=r"d line 2: not a JSON object"):
        _parsed(axis, [])
    with pytest.raises(RecordError, match=r"d line 2: neither a version line nor"):
        _parsed(axis, {"artifacts": {}})
    with pytest.raises(RecordError, match=r"d line 2: artifacts is not an object"):
        _parsed(axis, {"version": "1", "artifacts": []})
    with pytest.raises(RecordError, match=r"d line 2: host_layouts is not an object"):
        _parsed(axis, {"version": "1", "artifacts": {}, "host_layouts": 3})
    with pytest.raises(
        RecordError, match=r"artifacts\[macos-arm\]: artifact sha256 'xyz'"
    ):
        _parsed(
            axis,
            {"version": "1", "artifacts": {"macos-arm": {"url": "u", "sha256": "xyz"}}},
        )
    with pytest.raises(RecordError, match=r"a record needs a name"):
        Record("", hosts=())


def test_a_statement_line_off_its_place_or_shape_is_refused_naming_the_line() -> None:
    """The refusals of the line form: place, shape, and a thing stated twice."""
    axis = {"name": "t", "kind": "pypi", "hosts": []}
    read = {
        "version": "1",
        "read": {"platforms": ["Linux"], "extractor": 1, "help": "T"},
    }
    unread = {"version": "0"}
    verb = {"verb": "", "help": "", "wraps": False, "positional": "any", "lead": ""}
    option = {
        "verb": "",
        "option": "q",
        "flags": ["-q"],
        "negation": "",
        "help": "",
        "type": "bool",
        "default": None,
        "choices": [],
    }
    with pytest.raises(RecordError, match=r"d line 2: a statement before any version"):
        _parsed(axis, verb)
    with pytest.raises(
        RecordError, match=r"d line 3: a statement under version 0, which"
    ):
        _parsed(axis, unread, verb)
    with pytest.raises(RecordError, match=r"d line 3: unknown keys since"):
        _parsed(axis, read, {**verb, "since": "1"})
    with pytest.raises(RecordError, match=r"d line 3: a verb line sets nothing"):
        _parsed(axis, read, {"verb": ""})
    with pytest.raises(RecordError, match=r"d line 3: gone is not true"):
        _parsed(axis, read, {"verb": "", "gone": False})
    with pytest.raises(RecordError, match=r"d line 4: help of verb '' stated twice"):
        _parsed(axis, read, verb, {"verb": "", "help": "again"})
    with pytest.raises(
        RecordError, match=r"d line 5: option 'q' of verb '' stated twice"
    ):
        _parsed(axis, read, verb, option, option)
    with pytest.raises(RecordError, match=r"d line 4: verb '' stated twice"):
        _parsed(axis, read, {"verb": "", "gone": True}, {"verb": "", "gone": True})
    with pytest.raises(RecordError, match=r"d line 4: verb '' was withdrawn above"):
        _parsed(axis, read, {"verb": "", "gone": True}, option)
    with pytest.raises(RecordError, match=r"d line 4: a withdrawal carries no flags"):
        _parsed(
            axis, read, verb, {"verb": "", "option": "q", "gone": True, "flags": []}
        )
    with pytest.raises(RecordError, match=r"d line 4: an absence carries no help"):
        _parsed(axis, read, verb, {"verb": "", "absent": ["Linux"], "help": "x"})
    with pytest.raises(RecordError, match=r"d line 5: an absence stated twice"):
        _parsed(
            axis,
            read,
            verb,
            {"verb": "", "absent": ["Linux"]},
            {"verb": "", "absent": ["Linux"]},
        )
    with pytest.raises(RecordError, match=r"d line 4: gone is not true"):
        _parsed(axis, read, verb, {**option, "gone": None})
    with pytest.raises(RecordError, match=r"d line 4: an option line carries no lead"):
        _parsed(axis, read, verb, {**option, "lead": "x"})
    with pytest.raises(
        RecordError, match=r"d line 4: option 'q' carries exactly flags"
    ):
        _parsed(axis, read, verb, {"verb": "", "option": "q", "flags": ["-q"]})
    with pytest.raises(
        RecordError, match=r"d line 4: option 'q': help is not a string"
    ):
        _parsed(axis, read, verb, {**option, "help": 1})
    with pytest.raises(RecordError, match=r"d line 3: a verb line carries no flags"):
        _parsed(axis, read, {**verb, "flags": []})
    with pytest.raises(RecordError, match=r"d line 2 read: no platforms"):
        _parsed(axis, {"version": "1", "read": {"extractor": 1}})
    with pytest.raises(
        RecordError, match=r"d line 2 read: extractor is not a positive"
    ):
        _parsed(
            axis, {"version": "1", "read": {"platforms": ["Linux"], "extractor": True}}
        )
    with pytest.raises(RecordError, match=r"d line 2 read help: not a string"):
        _parsed(
            axis,
            {
                "version": "1",
                "read": {"platforms": ["Linux"], "extractor": 1, "help": 1},
            },
        )
    with pytest.raises(RecordError, match=r"d line 3 absent: not a list of strings"):
        _parsed(axis, read, {"verb": "", "absent": "Linux"})
    # A clean file parses, and the statements land as patches.
    record = _parsed(
        axis, read, verb, option, {"verb": "", "option": "q", "absent": ["Linux"]}
    )
    assert record.versions == ("1",)
    (delta,) = record.deltas
    assert delta.surface is not None
    assert delta.surface.verbs[""] == {
        "help": "",
        "wraps": False,
        "positional": "any",
        "lead": "",
        "options": {"q": {k: option[k] for k in OPTION_KEYS}},
    }
    assert delta.surface.absent == {"": {"q": ("Linux",)}}


def test_a_record_with_no_version_yet_is_validated_on_its_own(tmp_path: Path) -> None:
    # Nothing reads its layout yet, so only a restatement of the
    # built-in default is a refusal; a clean one saves and loads whole.
    with pytest.raises(RecordError, match=r"the tool's layout restates file"):
        Record("young", hosts=("macos-arm",), layout=Layout(file=""))
    young = Record("young", hosts=("macos-arm",))
    young.save(tmp_path)
    assert Record.load(tmp_path / "young.jsonl") == young
    lines = _lines(tmp_path / "young.jsonl")
    assert len(lines) == 1 and "layout" not in json.loads(lines[0])
    # A version-level layout writes under its own key and reads back.
    grown = _record(deltas=(_delta(layout=Layout(root="v1")),))
    grown.save(tmp_path)
    version = json.loads(_lines(tmp_path / "tool.jsonl")[1])
    assert version["layout"] == {"root": "v1"}
    assert Record.load(tmp_path / "tool.jsonl") == grown


def test_a_record_must_be_named_as_its_file(tmp_path: Path) -> None:
    _record().save(tmp_path)
    (tmp_path / "tool.jsonl").rename(tmp_path / "other.jsonl")
    with pytest.raises(RecordError, match=r"named 'tool', its file 'other'"):
        Record.load(tmp_path / "other.jsonl")
    from livery.toolroom.store import records_in

    (tmp_path / "notes").mkdir()
    (tmp_path / "README.md").write_text("records")
    assert records_in(tmp_path) == [tmp_path / "other.jsonl"]
    assert records_in(tmp_path / "missing") == []


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


def test_a_root_carrying_the_version_token_resolves_to_the_version() -> None:
    """One `root` on the tool serves every version of an archive named after it."""
    record = _record(
        layout=Layout(root="tool-{version}", entry_points=("tool",), paths=(".",)),
        deltas=(_delta(1, "1.0.0"), _delta(2, "1.1.0")),
    )
    assert resolve(record, "1.0.0", "macos-arm").root == "tool-1.0.0"
    assert resolve(record, "1.1.0", "linux-x64").root == "tool-1.1.0"
    # The record keeps the token; only the deployment is concrete.
    assert record.layout.root == "tool-{version}"


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
    record.save(tmp_path)
    lines = [json.loads(line) for line in _lines(tmp_path / "tool.jsonl")]
    assert len(lines) == 3  # the axis and two versions, no reading
    loaded = Record.load(tmp_path / "tool.jsonl")
    assert loaded == record
    assert list(lines[0]) == [
        "name",
        "description",
        "kind",
        "min_version",
        "hosts",
        "layout",
        "host_layouts",
    ]
    assert lines[0]["host_layouts"] == {"linux-x64": {"root": "l"}}
    assert list(lines[2]) == ["version", "date", "artifacts", "host_layouts"]
    assert lines[2]["host_layouts"]["macos-arm"] == {"root": "m", "exclude": ["*.txt"]}
    # prime rides the axis when set.
    assert _record(prime="1").to_json()["prime"] == "1"


def test_every_host_key_has_a_platform_and_arch_pair() -> None:
    assert host_key("Darwin", "arm64") == "macos-arm"
    assert host_key("Windows", "AMD64") == "windows-x64"
    assert host_key("Linux", "aarch64") == "linux-arm"
    for key in HOSTS:
        platform, arch = key.split("-")
        assert platform in ("windows", "macos", "linux") and arch in ("x64", "arm")


def test_the_schema_names_the_three_lines_and_exports(tmp_path: Path) -> None:
    shape = schema()
    assert [ref["$ref"] for ref in shape["oneOf"]] == [
        "#/$defs/Tool",
        "#/$defs/Version",
        "#/$defs/Statement",
    ]
    assert shape["$defs"]["Tool"]["required"] == ["name", "hosts"]
    assert "prime" in shape["$defs"]["Tool"]["properties"]
    assert shape["$defs"]["Version"]["required"] == ["version"]
    assert shape["$defs"]["Version"]["properties"]["read"]["required"] == [
        "platforms",
        "extractor",
    ]
    assert shape["$defs"]["Statement"]["required"] == ["verb"]
    assert set(OPTION_KEYS) | {"verb", "option", "gone", "absent", "wraps"} <= set(
        shape["$defs"]["Statement"]["properties"]
    )
    assert set(VERB_KEYS) - {"options"} <= set(
        shape["$defs"]["Statement"]["properties"]
    )
    assert set(shape["$defs"]["Layout"]["properties"]) == {
        "root",
        "file",
        "format",
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
    if not RECORDS.is_dir():
        pytest.skip("the checked-in records are a checkout fact")
    from livery.toolroom.store import records_in

    paths = records_in(RECORDS)
    names = [path.stem for path in paths]
    assert {"bun", "eclint", "git", "prek", "tea", "ty", "uv"} <= set(names)
    for name, path in zip(names, paths, strict=True):
        record = Record.load(path)
        assert record.versions, name
        for version in record.versions:
            for host in record.hosts_of(version):
                deployment = resolve(record, version, host)
                assert deployment.url and deployment.sha256, (name, version, host)
                if record.kind == "download" and not deployment.entry_points:
                    # Reached through env alone: nothing on PATH, nothing run.
                    assert deployment.env and not deployment.paths, (name, version)
                    continue
                assert deployment.paths, (name, version, host)
                if deployment.file:
                    assert deployment.file in deployment.entry_points, (
                        name,
                        version,
                        host,
                    )
