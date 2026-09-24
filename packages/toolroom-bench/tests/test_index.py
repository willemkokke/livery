"""The published index: the refusals first, then the layout, replay, and reuse."""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from livery.strongroom import Digest, Entry, ManifestError, Tree
from livery.toolroom.bench import _index, _surfaces
from livery.toolroom.store import (
    Artifact,
    Layout,
    Record,
    RecordDelta,
    RecordError,
    resolve,
)
from toolroom_bench_readings import history, reading, with_flags

SHA = "d8b96221828ad6f97ac7ac0ab7e95872341af763001e8803e8267652c2652620"


def _installable() -> Record:
    """A tool with two versions: the first read and installable, the second installed only."""
    read = history("tool", ("1.0.0", "2026-01-01", reading("quiet", "fork"), ["Linux"]))
    return Record(
        "tool",
        description="A tool.",
        kind="archive",
        hosts=("linux-x64", "windows-x64"),
        layout=Layout(entry_points=("tool",), paths=(".",)),
        host_layouts={"windows-x64": Layout(entry_points=("tool.exe",))},
        deltas=(
            RecordDelta(
                1,
                "1.0.0",
                "2026-01-01",
                {"linux-x64": Artifact("https://x/1/linux", SHA)},
                surface=read.deltas[0].surface,
            ),
            RecordDelta(
                2,
                "1.1.0",
                "2026-02-01",
                {
                    "linux-x64": Artifact("https://x/1.1/linux", SHA),
                    "windows-x64": Artifact("https://x/1.1/win", SHA),
                },
            ),
        ),
    )


def _records(tmp_path: pathlib.Path, *records: Record) -> pathlib.Path:
    root = tmp_path / "records"
    for record in records:
        record.save(root)
    return root


def _tree(store: Any, digest: Digest) -> Tree:
    return Tree.decode(store.read(digest))


def _entry(store: Any, tree: Tree, *path: str) -> Entry:
    """The entry at *path* under *tree*, walking subtrees."""
    found = tree
    for name in path[:-1]:
        entry = next(e for e in found.entries if e.name == name)
        assert isinstance(entry, Entry)
        found = _tree(store, entry.digest)
    last = next(e for e in found.entries if e.name == path[-1])
    assert isinstance(last, Entry)
    return last


def _read(store: Any, tree: Tree, *path: str) -> Any:
    return json.loads(store.read(_entry(store, tree, *path).digest))


# --- the refusals ---------------------------------------------------------------


def test_a_record_that_does_not_validate_refuses_the_build(tmp_path):
    root = _records(tmp_path, _installable())
    path = root / "tool.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8")
    with pytest.raises(RecordError, match=r"tool\.jsonl line \d+: not JSON"):
        _index.build(root, tmp_path / "index")
    assert not (tmp_path / "index" / _index.POINTER).exists()


def test_a_pointer_that_is_not_one_is_refused_naming_the_file(tmp_path):
    root = _records(tmp_path, _installable())
    into = tmp_path / "index"
    into.mkdir()
    (into / _index.POINTER).write_text("{not json")
    with pytest.raises(ValueError, match=r"pointer\.json: not JSON"):
        _index.build(root, into)
    (into / _index.POINTER).write_text('{"schema": 99, "tools": {}}')
    with pytest.raises(ValueError, match=r"not a pointer document of schema 1"):
        _index.build(root, into)
    (into / _index.POINTER).write_text('{"schema": 1, "tools": []}')
    with pytest.raises(ValueError, match=r"names no tools object"):
        _index.build(root, into)
    # ...and from genesis the pointer is not read at all.
    built = _index.build(root, into, from_genesis=True)
    assert built.rebuilt == ("tool",)


def test_a_directory_holding_another_layout_is_refused(tmp_path):
    root = _records(tmp_path, _installable())
    into = tmp_path / "index"
    into.mkdir()
    (into / "strongroom.json").write_text('{"layout": 99, "algorithm": "sha256"}')
    with pytest.raises(ManifestError):
        _index.build(root, into)


# --- the layout ------------------------------------------------------------------


def test_a_tool_lands_as_one_tree_of_its_versions_hosts_and_verbs(tmp_path):
    record = _installable()
    root = _records(tmp_path, record)
    built = _index.build(root, tmp_path / "index")
    assert built.rebuilt == ("tool",) and built.reused == () and built.dropped == ()
    store = _index.open_index(tmp_path / "index")
    tree = _tree(store, Digest.parse(built.tools["tool"]))
    assert [e.name for e in tree.entries] == ["1.0.0", "1.1.0", "tool", "versions"]
    assert _read(store, tree, "tool") == record.to_json()
    assert _read(store, tree, "versions") == ["1.0.0", "1.1.0"]

    # The read version: its observation, its one host, its surface whole.
    one = _tree(store, _entry(store, tree, "1.0.0").digest)
    assert [e.name for e in one.entries] == ["hosts", "observation", "surface"]
    assert _read(store, tree, "1.0.0", "observation") == {
        "date": "2026-01-01",
        "help": "A demo tool.",
        "platforms": ["Linux"],
        "extractor": _surfaces.EXTRACTOR,
        "absent": {},
    }
    assert [
        e.name
        for e in _tree(store, _entry(store, tree, "1.0.0", "hosts").digest).entries
    ] == ["linux-x64"]
    deployment = _read(store, tree, "1.0.0", "hosts", "linux-x64")
    resolved = resolve(record, "1.0.0", "linux-x64")
    assert deployment["url"] == resolved.url
    assert deployment["entry_points"] == ["tool"]
    assert deployment["paths"] == ["."]
    surface = _read(store, tree, "1.0.0", "surface")
    assert list(surface) == [""]  # the tool's own options, under the root verb
    assert sorted(surface[""]["options"]) == ["fork", "quiet"]
    assert surface == (_surfaces.at(record, "1.0.0") or {})["verbs"]

    # The installed-only version: hosts alone, one per host, no reading.
    two = _tree(store, _entry(store, tree, "1.1.0").digest)
    assert [e.name for e in two.entries] == ["hosts"]
    assert _read(store, tree, "1.1.0", "hosts", "windows-x64")["entry_points"] == [
        "tool.exe"
    ]
    assert store.ref(_index.INDEX, "tool") == Digest.parse(built.tools["tool"])


def test_the_pointer_names_every_tool_and_the_record_it_was_built_from(tmp_path):
    a, b = _installable(), history("other", ("2.0.0", "2026-03-01", with_flags("x")))
    root = _records(tmp_path, a, b)
    built = _index.build(root, tmp_path / "index")
    pointer = _index.read_pointer(tmp_path / "index")
    assert pointer is not None
    assert pointer["schema"] == _index.SCHEMA and pointer["algorithm"] == "sha256"
    assert list(pointer["tools"]) == ["other", "tool"]
    assert pointer["tools"]["tool"] == {
        "tree": built.tools["tool"],
        "record": str(_index.record_digest(root / "tool.jsonl")),
    }
    assert _index.read_pointer(tmp_path / "nowhere") is None


# --- replay -------------------------------------------------------------------


def test_two_builds_from_genesis_land_equal_objects_and_equal_pointers(tmp_path):
    root = _records(
        tmp_path,
        _installable(),
        history("other", ("2.0.0", "2026-03-01", with_flags("x", "y"), ["macOS"])),
    )
    first = _index.build(root, tmp_path / "one", from_genesis=True)
    second = _index.build(root, tmp_path / "two", from_genesis=True)
    assert first.tools == second.tools
    assert (tmp_path / "one" / _index.POINTER).read_text() == (
        tmp_path / "two" / _index.POINTER
    ).read_text()
    one = set(_index.open_index(tmp_path / "one").objects())
    two = set(_index.open_index(tmp_path / "two").objects())
    assert one == two and one


def test_a_new_delta_writes_only_the_objects_it_reaches(tmp_path):
    a = _installable()
    b = history("other", ("2.0.0", "2026-03-01", with_flags("x")))
    root = _records(tmp_path, a, b)
    into = tmp_path / "index"
    before = _index.build(root, into)
    store = _index.open_index(into)
    was = set(store.objects())
    old_tree = Digest.parse(before.tools["other"])

    # One new version of `other`, its verb widened; `tool` did not move.
    grown = _surfaces.place(
        b,
        version="2.1.0",
        date="2026-04-01",
        surface=with_flags("x", "z"),
        platforms=["Linux"],
    )
    assert grown is not None
    grown.save(root)

    after = _index.build(root, into)
    assert after.rebuilt == ("other",) and after.reused == ("tool",)
    assert after.tools["tool"] == before.tools["tool"]
    new_tree = Digest.parse(after.tools["other"])
    written = set(store.objects()) - was
    assert written == set(store.reachable(new_tree)) - set(store.reachable(old_tree))
    # The unchanged version's subtree is shared: the new tree reaches it
    # as it was, and none of its blobs was written again.
    shared = _entry(store, _tree(store, old_tree), "2.0.0").digest
    assert shared in set(store.reachable(new_tree))
    assert shared == _entry(store, _tree(store, new_tree), "2.0.0").digest
    assert store.ref(_index.INDEX, "other") == new_tree

    # From genesis lands the same result, every tool rebuilt.
    again = _index.build(root, into, from_genesis=True)
    assert again.rebuilt == ("other", "tool") and again.tools == after.tools
    assert set(store.objects()) == was | written


def test_a_reused_tool_is_rebuilt_when_its_tree_left_the_store(tmp_path):
    root = _records(tmp_path, _installable())
    into = tmp_path / "index"
    built = _index.build(root, into)
    store = _index.open_index(into)
    store.drop_ref(_index.INDEX, "tool", previous=Digest.parse(built.tools["tool"]))
    again = _index.build(root, into)
    assert again.rebuilt == ("tool",)
    assert again.tools == built.tools


def test_a_record_that_went_is_dropped_from_the_pointer_and_the_refs(tmp_path):

    root = _records(
        tmp_path,
        _installable(),
        history("other", ("2.0.0", "2026-03-01", with_flags("x"))),
    )
    into = tmp_path / "index"
    _index.build(root, into)
    (root / "other.jsonl").unlink()
    built = _index.build(root, into)
    assert built.dropped == ("other",) and list(built.tools) == ["tool"]
    store = _index.open_index(into)
    assert store.refs(_index.INDEX) == ["tool"]
    pointer = _index.read_pointer(into)
    assert pointer is not None and list(pointer["tools"]) == ["tool"]


# --- the verb --------------------------------------------------------------------


def test_the_verb_builds_into_the_directory_named_and_says_what_it_did(
    tmp_path, monkeypatch
):
    from livery.toolroom.bench import _tasks as tools
    from toolroom_bench_readings import tools_run

    monkeypatch.setattr(tools, "_RECORDS", _records(tmp_path, _installable()))
    into = tmp_path / "index"
    result = tools_run(["index.build", f"--into={into}"])
    assert result.ok, result.stderr
    built = result.results[0].returned
    assert built.rebuilt == ("tool",)
    assert (into / _index.POINTER).is_file()
    assert f"built tool {built.tools['tool']}" in result.stdout
    assert "pointer:" in result.stdout and "(1 tools)" in result.stdout

    again = tools_run(["index.build", f"--into={into}"])
    assert again.ok, again.stderr
    assert "reused 1: tool" in again.stdout
    fresh = tools_run(["index.build", f"--into={into}", "--from-genesis"])
    assert fresh.ok, fresh.stderr
    assert "built tool" in fresh.stdout


def test_the_verb_defaults_to_the_workspaces_generated_index(tmp_path, monkeypatch):
    from livery.toolroom.bench import _tasks as tools
    from toolroom_bench_readings import tools_run

    root = _records(tmp_path, _installable())
    monkeypatch.setattr(tools, "_RECORDS", root)
    result = tools_run("index.build")
    assert result.ok, result.stderr
    assert (tmp_path / "docs" / "_generated" / "index" / _index.POINTER).is_file()


def test_the_checked_in_records_build_and_the_bench_declares_the_generator():
    """Every record the repository carries lands; the build is what the
    site's generator runs, so it must succeed on the checkout as it is.
    """
    import tempfile

    from livery.toolroom.bench import _tasks as tools

    records = pathlib.Path(tools._records_dir())
    if not records.is_dir():
        pytest.skip("the checked-in records are a checkout fact")
    contract = records.parent / "packages" / "toolroom-bench" / "workshop.toml"
    assert 'generators = ["tools.index.build"]' in contract.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as scratch:
        built = _index.build(records, pathlib.Path(scratch) / "index")
    assert len(built.rebuilt) >= 31
    assert "uv" in built.tools and "ruff_format" in built.tools


def _driven(tmp_path: pathlib.Path, monkeypatch: Any) -> pathlib.Path:
    """Records named like curated tools, so the build finds a driver for them."""
    from livery.toolroom.bench import _drivers

    ruff = history(
        "ruff",
        ("1.0.0", "2026-01-01", reading("quiet"), ["Linux"]),
        ("1.1.0", "2026-02-01", reading("quiet", "fix"), ["Linux", "macOS"]),
    )
    assert _drivers.find("ruff") is not None
    return _records(
        tmp_path, ruff, history("other", ("2.0.0", "2026-03-01", with_flags("x")))
    )


def test_a_build_after_which_nothing_moved_reads_no_record(tmp_path, monkeypatch):
    """The nothing-moved gate: the second build answers from the pointer
    without loading a record; a touched record file, a touched renderer
    source, or a tree gone from the store each make the next build real.
    """
    import os
    import time

    from livery.toolroom.store import build_current

    root = _driven(tmp_path, monkeypatch)
    into = tmp_path / "index"
    first = _index.build(root, into)
    assert build_current(into)
    loads: list[str] = []
    real = _index.load_records

    def counted(records):
        loads.append(str(records))
        return real(records)

    monkeypatch.setattr(_index, "load_records", counted)
    again = _index.build(root, into)
    assert loads == [] and again.tools == first.tools
    assert again.reused == ("other", "ruff") and again.rebuilt == ()

    stamp = time.time_ns() + 2_000_000_000
    os.utime(root / "ruff.jsonl", ns=(stamp, stamp))
    assert not build_current(into)
    third = _index.build(root, into)
    assert loads and third.reused == ("other", "ruff")  # same bytes: reused, but read
    assert build_current(into)

    loads.clear()
    _index.build(root, into, from_genesis=True)
    assert loads  # genesis always reads
    loads.clear()
    assert _index.build(root, into).reused == ("other", "ruff") and loads == []
