"""The catalogue and the lock: the refusals first, then resolution and the round trip."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from livery.strongroom import Digest, Entry
from livery.toolroom.store import (
    Artifact,
    Catalogue,
    CatalogueError,
    Deployment,
    Layout,
    Lock,
    Locked,
    LockError,
    Record,
    RecordDelta,
    RecordError,
    Requirement,
    Surface,
    resolve,
    resolve_lock,
    version_key,
)

SHA = "d8b96221828ad6f97ac7ac0ab7e95872341af763001e8803e8267652c2652620"
THREE = ("linux-x64", "macos-arm", "windows-x64")


def _surface() -> Surface:
    return Surface(
        ("Linux",),
        1,
        "A tool.",
        {
            "": {
                "help": "A tool.",
                "wraps": False,
                "positional": "any",
                "lead": "",
                "options": {},
            }
        },
    )


def _archive(name: str, *versions: tuple[str, tuple[str, ...]]) -> Record:
    """An archive tool whose versions each have the hosts named."""
    return Record(
        name,
        kind="archive",
        hosts=THREE,
        layout=Layout(entry_points=(name,), paths=(".",)),
        deltas=tuple(
            RecordDelta(
                n,
                version,
                f"2026-01-{n:02d}",
                {
                    host: Artifact(f"https://x/{name}/{version}/{host}", SHA)
                    for host in hosts
                },
            )
            for n, (version, hosts) in enumerate(versions, start=1)
        ),
    )


def _delegated(name: str, *versions: str) -> Record:
    """A uv-tool whose versions were read, never downloaded."""
    return Record(
        name,
        kind="uv-tool",
        deltas=tuple(
            RecordDelta(
                n, version, "", surface=_surface() if n == 1 else Surface(("Linux",), 1)
            )
            for n, version in enumerate(versions, start=1)
        ),
    )


def _records(tmp_path: Path, *records: Record) -> Path:
    root = tmp_path / "records"
    for record in records:
        record.save(root)
    return root


# --- the refusals ---------------------------------------------------------------


def test_a_requirement_is_a_name_or_a_name_with_a_floor():
    assert Requirement.parse("ruff") == Requirement("ruff", "", "")
    assert Requirement.parse("ruff >= 0.16", site="here") == Requirement(
        "ruff", "0.16", "here"
    )
    assert str(Requirement.parse("git-cliff>=2.0")) == "git-cliff>=2.0"
    with pytest.raises(
        LockError, match=r"here: 'ruff==1' is not a requirement; spell it"
    ):
        Requirement.parse("ruff==1", site="here")
    with pytest.raises(LockError, match=r"a requirement: 'ruff<2'"):
        Requirement.parse("ruff<2")


def test_a_tool_the_catalogue_does_not_list_refuses_naming_the_site(tmp_path):
    catalogue = Catalogue.of_records(_records(tmp_path, _delegated("ruff", "1.0")))
    with pytest.raises(
        LockError,
        match=r"no record of black; the catalogue lists ruff; required by a/b",
    ):
        resolve_lock(catalogue, [Requirement("black", site="a/b")], hosts=THREE)
    with pytest.raises(CatalogueError, match=r"no record of black"):
        catalogue.listed("black")


def test_floors_no_version_reaches_refuse_naming_each_floor_and_its_site(tmp_path):
    catalogue = Catalogue.of_records(
        _records(tmp_path, _delegated("ruff", "0.15.0", "0.16.4"))
    )
    with pytest.raises(
        LockError,
        match=r"ruff: no version satisfies ruff>=0.16 \(packages/a\); ruff>=9.0"
        r" \(workshop.toml\); the newest version listed is 0.16.4",
    ):
        resolve_lock(
            catalogue,
            [
                Requirement("ruff", "0.16", "packages/a"),
                Requirement("ruff", "9.0", "workshop.toml"),
            ],
            hosts=THREE,
        )


def test_a_host_no_eligible_version_has_refuses_naming_the_first_that_has_it(
    tmp_path,
):
    lost = _archive(
        "tea",
        ("1.0.0", THREE),
        ("1.1.0", ("linux-x64", "macos-arm")),  # Windows lost from 1.1.0 on
        ("1.2.0", ("linux-x64", "macos-arm")),
    )
    catalogue = Catalogue.of_records(_records(tmp_path, lost))
    with pytest.raises(
        LockError,
        match=r"tea: no version satisfying tea>=1.1 has host windows-x64; the first"
        r" version that has it is 1.0.0",
    ):
        resolve_lock(catalogue, [Requirement("tea", "1.1", "s")], hosts=THREE)
    # A host no version has at all is named as such...
    with pytest.raises(LockError, match=r"tea: no version has host linux-arm"):
        resolve_lock(catalogue, [Requirement("tea")], hosts=("linux-arm",))
    # ...and a host outside the six is refused before anything resolves.
    with pytest.raises(LockError, match=r"host 'plan9' is not one of"):
        resolve_lock(catalogue, [Requirement("tea")], hosts=("plan9",))
    # Without the floor the lock steps back to the last version every host has.
    assert (
        resolve_lock(catalogue, [Requirement("tea")], hosts=THREE).tools["tea"].version
        == "1.0.0"
    )


def test_a_lock_file_off_the_shape_is_refused_naming_the_file(tmp_path):
    path = tmp_path / "tools.lock"
    path.write_text("{not json")
    with pytest.raises(LockError, match=r"tools\.lock: not a lock \("):
        Lock.load(path)
    path.write_text('{"schema": 9}')
    with pytest.raises(LockError, match=r"not a lock of schema 1"):
        Lock.load(path)
    path.write_text('{"schema": 1, "hosts": ["plan9"], "tools": {}}')
    with pytest.raises(LockError, match=r"hosts outside the six"):
        Lock.load(path)
    path.write_text('{"schema": 1, "hosts": [], "tools": {"ruff": {"version": 1}}}')
    with pytest.raises(LockError, match=r"the entry for ruff is not one"):
        Lock.load(path)
    path.write_text(
        '{"schema": 1, "hosts": [], "tools": {"ruff": {"version": "1", "hosts": {"linux-x64": "x"}}}}'
    )
    with pytest.raises(LockError, match=r"tools\.lock: ruff:"):
        Lock.load(path)


def test_an_index_that_is_not_one_is_refused_naming_the_source(tmp_path):
    from livery.toolroom.store import Home

    home = Home(tmp_path / "home")
    with pytest.raises(CatalogueError, match=r"no pointer can be read"):
        Catalogue.of_index(str(tmp_path / "nowhere"), home=home)
    index = tmp_path / "index"
    index.mkdir()
    (index / "pointer.json").write_text('{"schema": 1, "tools": {"ruff": "x"}}')
    with pytest.raises(
        CatalogueError, match=r"the pointer's entry for ruff is not one"
    ):
        Catalogue.of_index(str(index), home=home)
    (index / "pointer.json").write_text('{"schema": 2, "tools": {}}')
    with pytest.raises(CatalogueError, match=r"not a pointer document of schema 1"):
        Catalogue.of_index(str(index), home=home)


# --- resolution -------------------------------------------------------------------


def test_the_lock_takes_the_newest_version_that_satisfies_and_resolves_everywhere(
    tmp_path,
):
    tea = _archive(
        "tea",
        ("0.9.0", THREE),
        ("1.0.0", THREE),
        ("1.1.0", ("linux-x64", "macos-arm")),  # Windows lost at 1.1.0
    )
    catalogue = Catalogue.of_records(
        _records(tmp_path, tea, _delegated("ruff", "0.15.0", "0.16.4"))
    )
    lock = resolve_lock(
        catalogue,
        [
            Requirement("ruff", "0.15", "kind python"),
            Requirement("tea", site="workshop.toml"),
        ],
        hosts=THREE,
    )
    assert lock.hosts == THREE
    assert lock.tools["ruff"] == Locked("0.16.4")  # delegated: no host digests
    assert lock.tools["tea"].version == "1.0.0"  # the newest on every locked host
    assert set(lock.tools["tea"].hosts) == set(THREE)
    assert (
        lock.tools["tea"].hosts["linux-x64"]
        == resolve(tea, "1.0.0", "linux-x64").digest()
    )
    # Locking for two hosts lets 1.1.0 through.
    two = resolve_lock(
        catalogue, [Requirement("tea")], hosts=("linux-x64", "macos-arm")
    )
    assert two.tools["tea"].version == "1.1.0"


def test_a_kept_entry_stands_until_upgraded_and_leaves_when_nothing_requires_it(
    tmp_path,
):
    catalogue = Catalogue.of_records(
        _records(
            tmp_path,
            _delegated("ruff", "0.15.0", "0.16.0", "0.16.4"),
            _delegated("ty", "0.0.7"),
        )
    )
    held = Lock(THREE, {"ruff": Locked("0.16.0"), "ty": Locked("0.0.7")})
    kept = resolve_lock(
        catalogue, [Requirement("ruff", "0.15")], hosts=THREE, keep=held
    )
    assert kept.tools["ruff"].version == "0.16.0"  # stands
    assert "ty" not in kept.tools  # nothing requires it any more
    moved = resolve_lock(
        catalogue,
        [Requirement("ruff", "0.15")],
        hosts=THREE,
        keep=held,
        upgrade=("ruff",),
    )
    assert moved.tools["ruff"].version == "0.16.4"
    # A kept version below a new floor moves without being asked.
    raised = resolve_lock(
        catalogue, [Requirement("ruff", "0.16.4")], hosts=THREE, keep=held
    )
    assert raised.tools["ruff"].version == "0.16.4"


def test_the_lock_round_trips_through_its_file(tmp_path):
    lock = Lock(
        THREE,
        {
            "tea": Locked(
                "1.0.0", {host: Digest.parse(f"sha256:{SHA}") for host in THREE}
            ),
            "ruff": Locked("0.16.4"),
        },
    )
    path = tmp_path / "tools.lock"
    lock.save(path)
    assert Lock.load(path) == lock
    written = json.loads(path.read_text())
    assert list(written) == ["schema", "hosts", "tools"]
    assert list(written["tools"]) == ["ruff", "tea"]  # name order
    assert written["tools"]["ruff"] == {"version": "0.16.4", "hosts": {}}


# --- the catalogue from the index ----------------------------------------------


def test_the_catalogue_reads_the_same_deployments_from_the_records_and_the_index(
    tmp_path,
):
    """The authoring site's lock names what a consumer fetches from the index.

    The deployment digests agree by construction.
    """
    from livery.strongroom import Entry, Store, Tree, canonical
    from livery.toolroom.store import Home

    tea = _archive("tea", ("1.0.0", THREE), ("1.1.0", ("linux-x64",)))
    root = _records(tmp_path, tea, _delegated("ruff", "0.16.4"))
    from_records = Catalogue.of_records(root)

    # An index laid out as the build lays it out: tool, versions, hosts.
    index = tmp_path / "index"
    store = Store.create(index)

    def blob(name: str, value: Any) -> Entry:
        data = canonical(value)
        return Entry(name, "blob", store.put(data), len(data))

    def tree(name: str, entries: list[Entry]) -> Entry:
        data = Tree.of(entries).encode()
        return Entry(name, "tree", store.put(data), len(data))

    pointer: dict[str, object] = {}
    for record in (tea, _delegated("ruff", "0.16.4")):
        entries = [
            blob("tool", record.to_json()),
            blob("versions", list(record.versions)),
        ]
        for delta in record.deltas:
            parts = []
            if delta.hosts:
                parts.append(
                    tree(
                        "hosts",
                        [
                            blob(host, resolve(record, delta.version, host).to_json())
                            for host in delta.hosts
                        ],
                    )
                )
            entries.append(tree(delta.version, parts))
        top = tree(record.name, entries)
        pointer[record.name] = {"tree": str(top.digest), "record": "x"}
    (index / "pointer.json").write_text(json.dumps({"schema": 1, "tools": pointer}))

    from_index = Catalogue.of_index(str(index), home=Home(tmp_path / "home"))
    assert set(from_index.tools) == {"ruff", "tea"}
    assert from_index.tools["tea"].versions == ("1.0.0", "1.1.0")
    assert from_index.tools["tea"].hosts == from_records.tools["tea"].hosts
    assert from_index.tools["ruff"].kind == "uv-tool" and from_index.tools[
        "ruff"
    ].hosts == {"0.16.4": {}}
    fetched = from_index.deployment("tea", "1.0.0", "windows-x64")
    assert fetched == from_records.deployment("tea", "1.0.0", "windows-x64")
    assert fetched == from_index.deployment("tea", "1.0.0", "windows-x64")  # held
    with pytest.raises(
        CatalogueError,
        match=r"tea 1\.1\.0: no host macos-arm; the version has linux-x64",
    ):
        from_index.deployment("tea", "1.1.0", "macos-arm")
    # ...and the lock resolves the same from either.
    wants = [Requirement("tea"), Requirement("ruff")]
    assert resolve_lock(from_index, wants, hosts=THREE) == resolve_lock(
        from_records, wants, hosts=THREE
    )


def test_a_deployment_round_trips_through_json_and_names_itself():
    deployment = Deployment(
        "https://x",
        SHA,
        "r",
        "",
        ("bin/t",),
        ("bin",),
        {"A": "b"},
        {"n": "t"},
        ("*.md",),
    )
    back = Deployment.from_json(deployment.to_json(), where="d")
    assert back == deployment and back.digest() == deployment.digest()
    with pytest.raises(RecordError, match=r"d: no exclude"):
        Deployment.from_json(
            {k: v for k, v in deployment.to_json().items() if k != "exclude"}, where="d"
        )


def test_versions_order_by_number_then_patchlevel_then_date():
    assert sorted(["9.9p2", "10.0p1", "9.9p1"], key=version_key) == [
        "9.9p1",
        "9.9p2",
        "10.0p1",
    ]
    assert version_key("2.55.0.windows.2", "") < version_key("2.55.0", "2026-01-01")


# --- an index off its shape -----------------------------------------------------


def _index_store(tmp_path: Path) -> Any:
    from livery.strongroom import Store

    return Store.create(tmp_path / "index")


def _blob(store: Any, name: str, value: Any) -> Any:
    from livery.strongroom import Entry, canonical

    data = canonical(value)
    return Entry(name, "blob", store.put(data), len(data))


def _tree_entry(store: Any, name: str, entries: list[Any]) -> Any:
    from livery.strongroom import Entry, Tree

    data = Tree.of(entries).encode()
    return Entry(name, "tree", store.put(data), len(data))


def _pointer(index: Path, tools: dict[str, Any]) -> None:
    (index / "pointer.json").write_text(json.dumps({"schema": 1, "tools": tools}))


def test_an_index_version_whose_reading_is_off_its_shape_is_refused(tmp_path):
    """A version listed as read still refuses when its blobs are not a reading.

    The tree may lack the version's entry, a blob may not parse, or it
    may parse to something other than an object; each names the tool,
    the version and the fault.
    """
    from livery.toolroom.store import Home

    store = _index_store(tmp_path)
    index = tmp_path / "index"
    axis = _delegated("ruff", "1.0.0").to_json()
    raw = b"not json"
    broken = Entry("surface", "blob", store.put(raw), len(raw))
    about = {"help": "", "platforms": ["Linux"], "extractor": 1, "absent": {}}
    top = _tree_entry(
        store,
        "ruff",
        [
            _blob(store, "tool", axis),
            _blob(store, "versions", ["1.0.0", "1.1.0", "1.2.0"]),
            _tree_entry(store, "1.0.0", [_blob(store, "observation", about), broken]),
            _tree_entry(
                store,
                "1.1.0",
                [_blob(store, "observation", about), _blob(store, "surface", [])],
            ),
        ],
    )
    _pointer(index, {"ruff": {"tree": str(top.digest), "record": "x"}})
    catalogue = Catalogue.of_index(str(index), home=Home(tmp_path / "home"))
    with pytest.raises(CatalogueError, match=r"ruff 1.0.0: the surface cannot be read"):
        catalogue.stub("ruff", "1.0.0")
    with pytest.raises(CatalogueError, match=r"ruff 1.1.0: the surface is not one"):
        catalogue.stub("ruff", "1.1.0")
    with pytest.raises(CatalogueError, match=r"ruff 1.2.0: the tree has no entry"):
        catalogue.stub("ruff", "1.2.0")


def test_an_index_whose_trees_are_off_their_shape_refuses_naming_the_tool(tmp_path):
    """Each refusal lands when the tool, or its version, is asked for.

    The catalogue reads on demand, and the pointer alone opens it.
    """
    from livery.toolroom.store import Home

    store = _index_store(tmp_path)
    index = tmp_path / "index"
    home = Home(tmp_path / "home")
    missing = "sha256:" + "0" * 64

    # A tree the pointer names and no source holds.
    _pointer(index, {"ruff": {"tree": missing, "record": "x"}})
    with pytest.raises(
        CatalogueError, match=r"index ruff: the tree sha256:0+ cannot be read"
    ):
        Catalogue.of_index(str(index), home=home).listed("ruff")

    # A tool's tree with no tool or versions blob.
    bare = _tree_entry(store, "ruff", [])
    _pointer(index, {"ruff": {"tree": str(bare.digest), "record": "x"}})
    with pytest.raises(CatalogueError, match=r"names no tool or versions"):
        Catalogue.of_index(str(index), home=Home(tmp_path / "home2")).listed("ruff")

    # A tool blob that is not a record's axis.
    odd = _tree_entry(
        store, "ruff", [_blob(store, "tool", {"nope": 1}), _blob(store, "versions", [])]
    )
    _pointer(index, {"ruff": {"tree": str(odd.digest), "record": "x"}})
    with pytest.raises(CatalogueError, match=r"the tool's axis cannot be read"):
        Catalogue.of_index(str(index), home=Home(tmp_path / "home3")).listed("ruff")

    # A versions blob that is not a list of strings.
    axis = _delegated("ruff", "1.0.0").to_json()
    listed = _tree_entry(
        store, "ruff", [_blob(store, "tool", axis), _blob(store, "versions", [1])]
    )
    _pointer(index, {"ruff": {"tree": str(listed.digest), "record": "x"}})
    with pytest.raises(
        CatalogueError, match=r"the versions blob is not a list of strings"
    ):
        Catalogue.of_index(str(index), home=Home(tmp_path / "home4")).listed("ruff")

    # A version the versions blob names and the tree lacks.
    gap = _tree_entry(
        store, "ruff", [_blob(store, "tool", axis), _blob(store, "versions", ["1.0.0"])]
    )
    _pointer(index, {"ruff": {"tree": str(gap.digest), "record": "x"}})
    with pytest.raises(CatalogueError, match=r"the tree has no entry for 1\.0\.0"):
        Catalogue.of_index(str(index), home=Home(tmp_path / "home5")).listed(
            "ruff"
        ).hosts["1.0.0"]


def test_a_deployment_the_source_cannot_serve_refuses_naming_it(tmp_path):
    from livery.strongroom import Digest as _Digest
    from livery.toolroom.store import Home

    store = _index_store(tmp_path)
    index = tmp_path / "index"
    tea = _archive("tea", ("1.0.0", THREE))
    gone = _Digest.parse("sha256:" + "1" * 64)
    hosts = _tree_entry(
        store,
        "hosts",
        [
            _blob(store, "linux-x64", resolve(tea, "1.0.0", "linux-x64").to_json()),
        ],
    )
    # The pointer's tree names a host blob the store never landed.
    from livery.strongroom import Entry

    version = _tree_entry(
        store,
        "1.0.0",
        [
            Entry(
                "hosts",
                "tree",
                store.put(
                    __import__("livery.strongroom", fromlist=["Tree"])
                    .Tree.of([Entry("windows-x64", "blob", gone, 1)])
                    .encode()
                ),
                1,
            )
        ],
    )
    del hosts
    top = _tree_entry(
        store,
        "tea",
        [
            _blob(store, "tool", tea.to_json()),
            _blob(store, "versions", ["1.0.0"]),
            version,
        ],
    )
    _pointer(index, {"tea": {"tree": str(top.digest), "record": "x"}})
    catalogue = Catalogue.of_index(str(index), home=Home(tmp_path / "home"))
    assert catalogue.tools["tea"].hosts == {"1.0.0": {"windows-x64": gone}}
    with pytest.raises(
        CatalogueError,
        match=r"tea 1\.0\.0 windows-x64: the deployment sha256:1+ cannot be read",
    ):
        catalogue.deployment("tea", "1.0.0", "windows-x64")


def test_a_records_directory_skips_what_is_not_a_record(tmp_path):
    root = _records(tmp_path, _delegated("ruff", "1.0.0"))
    (root / "notes").mkdir()
    (root / "README.md").write_text("records")
    assert list(Catalogue.of_records(root).tools) == ["ruff"]


def test_a_published_index_is_read_by_url_through_an_http_source(tmp_path, monkeypatch):
    """The pointer comes over HTTP and the objects through an HTTP source.

    The pointer's fetch is faked and the source's kind asserted, since no
    server serves the layout in a test.
    """
    from livery.strongroom import HttpSource
    from livery.toolroom.store import Home, _catalogue, _engine

    served = json.dumps({"schema": 1, "tools": {}}).encode("utf-8")
    asked: list[str] = []

    def download(url: str) -> bytes:
        asked.append(url)
        return served

    monkeypatch.setattr(_engine, "download", download)
    catalogue = Catalogue.of_index(
        "https://x.test/index/", home=Home(tmp_path / "home")
    )
    assert catalogue.tools == {} and asked == ["https://x.test/index/pointer.json"]
    assert isinstance(_catalogue._source_of("https://x.test/index"), HttpSource)


def test_a_stub_is_rendered_from_the_index_and_refused_for_a_version_never_read(
    tmp_path,
):
    from livery.toolroom.store import Home, class_name

    store = _index_store(tmp_path)
    index = tmp_path / "index"
    axis = _delegated("ruff", "1.0.0").to_json()
    surface = {
        "": {
            "help": "",
            "wraps": False,
            "positional": "any",
            "lead": "",
            "options": {
                "fix": {
                    "flags": ["--fix"],
                    "negation": "",
                    "help": "Apply fixes.",
                    "type": "bool",
                    "default": None,
                    "choices": [],
                }
            },
        }
    }
    about = {
        "help": "Ruff.",
        "platforms": ["Linux", "macOS"],
        "extractor": 3,
        "absent": {},
    }
    read = _tree_entry(
        store,
        "1.0.0",
        [_blob(store, "observation", about), _blob(store, "surface", surface)],
    )
    top = _tree_entry(
        store,
        "ruff",
        [
            _blob(store, "tool", axis),
            _blob(store, "versions", ["1.0.0", "1.1.0"]),
            read,
            _tree_entry(store, "1.1.0", []),  # downloaded, never read
        ],
    )
    _pointer(index, {"ruff": {"tree": str(top.digest), "record": "x"}})
    catalogue = Catalogue.of_index(str(index), home=Home(tmp_path / "home"))
    text = catalogue.stub("ruff", "1.0.0")
    assert "# Read from ruff 1.0.0 on Linux and macOS." in text
    assert "class Ruff(ToolBase[_R]):" in text and "fix: Flag = ...," in text
    with pytest.raises(CatalogueError, match=r"ruff 1.1.0: the version was never read"):
        catalogue.stub("ruff", "1.1.0")
    with pytest.raises(
        CatalogueError, match=r"ruff 9.9.9: never read; the versions read"
    ):
        catalogue.stub("ruff", "9.9.9")
    with pytest.raises(CatalogueError, match=r"no record of black"):
        catalogue.stub("black", "1.0.0")

    # The records render the same text for the same reading.
    records = tmp_path / "records"
    record = Record(
        "ruff",
        kind="uv-tool",
        deltas=(
            RecordDelta(
                1,
                "1.0.0",
                "",
                surface=Surface(("Linux", "macOS"), 3, "Ruff.", surface),
            ),
        ),
    )
    record.save(records)
    assert Catalogue.of_records(records).stub("ruff", "1.0.0") == text
    with pytest.raises(
        CatalogueError, match=r"ruff 2.0.0: never read; the versions read"
    ):
        Catalogue.of_records(records).stub("ruff", "2.0.0")

    assert class_name("ruff_format") == "RuffFormat"
    assert class_name("markdownlint-cli2") == "MarkdownlintCli2"


def test_the_index_is_read_on_demand_a_tool_and_a_version_at_a_time(tmp_path):
    """Opening the catalogue reads the pointer alone.

    A tool's tree is read when the tool is asked for and a version's
    hosts when they are, so a version tree nobody asks for can be
    missing without a refusal.
    """
    from livery.toolroom.store import Home

    store = _index_store(tmp_path)
    index = tmp_path / "index"
    from livery.strongroom import Link
    from livery.toolroom.store import read_pointer

    axis = _delegated("ruff", "1.0.0").to_json()
    missing = "sha256:" + "2" * 64
    # A version tree as the build lays it out, a link among the members: the
    # observation blob is not hosts, and a link is not a host.
    deployed = _tree_entry(
        store,
        "1.0.0",
        [
            _blob(store, "observation", {"who": "x"}),
            _tree_entry(
                store,
                "hosts",
                [_blob(store, "linux-x64", {"d": 1}), Link("aside", "linux-x64")],
            ),
        ],
    )
    top = _tree_entry(
        store,
        "ruff",
        [
            _blob(store, "tool", axis),
            _blob(store, "versions", ["1.0.0", "1.1.0"]),
            deployed,
            Entry("1.1.0", "tree", Digest.parse(missing), 3),
        ],
    )
    stubs = _tree_entry(
        store, "ruff", [_blob(store, "1.0.0", "stub"), Link("latest", "1.0.0")]
    )
    _pointer(
        index,
        {
            "ruff": {
                "tree": str(top.digest),
                "record": "x",
                "stubs": str(stubs.digest),
            },
            "gone": {"tree": missing, "record": "x"},
        },
    )
    assert read_pointer(str(index))["schema"] == 1
    with pytest.raises(CatalogueError, match=r"no pointer can be read"):
        read_pointer(str(tmp_path / "nowhere"))
    catalogue = Catalogue.of_index(str(index), home=Home(tmp_path / "home"))
    assert sorted(catalogue.tools) == ["gone", "ruff"]  # the pointer's names
    listed = catalogue.listed("ruff")
    assert listed.versions == ("1.0.0", "1.1.0")
    assert list(listed.hosts) == ["1.0.0", "1.1.0"] and len(listed.hosts) == 2
    assert list(listed.hosts["1.0.0"]) == ["linux-x64"]  # the link is no host
    with pytest.raises(CatalogueError, match=r"ruff 1.1.0: the tree sha256:2+ cannot"):
        listed.hosts["1.1.0"]
    with pytest.raises(KeyError):
        listed.hosts["9.9.9"]
    assert catalogue.listed("ruff") is listed  # read once
    with pytest.raises(
        CatalogueError, match=r"gone: the tree sha256:2+ cannot be read"
    ):
        catalogue.listed("gone")


def test_the_fingerprint_moves_with_a_file_and_the_build_record_gates_on_it(tmp_path):
    """The stat fingerprint reads no content, and the build record gates on it.

    A rewrite with the same bytes and a later mtime moves the
    fingerprint, an absent path is a fingerprint of its own, and one
    file fingerprints alone.
    `build_current` is false without a record, with one off its shape,
    and with a moved records tree, and true when the tree stands.
    """
    import os
    import time

    from livery.toolroom.store import BUILD_FILE, build_current, tree_fingerprint

    records = tmp_path / "records"
    records.mkdir(parents=True)
    (records / "ruff.jsonl").write_text("{}")
    first = tree_fingerprint([records])
    assert first == tree_fingerprint([records])
    assert tree_fingerprint([tmp_path / "nowhere"]) != tree_fingerprint(
        [tmp_path / "elsewhere"]
    )
    stamp = time.time_ns() + 2_000_000_000
    os.utime(records / "ruff.jsonl", ns=(stamp, stamp))
    assert tree_fingerprint([records]) != first
    # One file names itself, so a file and a directory of it differ.
    one = tree_fingerprint([records / "ruff.jsonl"])
    assert one == tree_fingerprint([records / "ruff.jsonl"])
    assert one != tree_fingerprint([records])

    index = tmp_path / "index"
    index.mkdir()
    assert build_current(index) is False  # no record, no pointer
    (index / BUILD_FILE).write_text('{"schema": 1}')
    assert build_current(index) is False  # a record, no pointer
    (index / "pointer.json").write_text('{"schema": 1, "tools": {}}')
    for off_shape in ("not json", "[]", '{"schema": 1}', '{"records": {"path": 3}}'):
        (index / BUILD_FILE).write_text(off_shape)
        assert build_current(index) is False, off_shape
    document = {
        "schema": 1,
        "records": {"path": str(records), "fingerprint": tree_fingerprint([records])},
    }
    (index / BUILD_FILE).write_text(json.dumps(document))
    assert build_current(index) is True
    (records / "extra.jsonl").write_text("{}")
    assert build_current(index) is False
