"""The published index: every record materialised into a store served as static files.

A client never replays a record. The build resolves every version of
every tool and lands what it resolves to as content-addressed objects,
one tree per tool:

    tool                        the tool axis, `tool.json` as canonical JSON
    versions                    every version tracked, oldest first
    <version>/observation       who read the version; its help, extractor, absences
    <version>/hosts/<host>      the deployment resolved for that host
    <version>/surface           the version's verbs whole, each with its options

Adjacent versions share every unchanged blob by content address, and
any version is addressable without replay. Refs are local, so beside
the store the build writes one pointer document naming each tool's
current tree and the digest of the record it was built from.
Everything under a tree is immutable and cacheable for as long as
anyone likes; only the pointer needs a short lifetime. The index holds
no stubs: a consumer renders the stub of the version it locks from that
version's surface, through the store.

The build replays to identical digests: two builds from genesis land
equal objects and equal pointers. A build into a directory holding an
earlier build reads that pointer and reuses every tool whose record
digest did not move, and a build whose records directory is unmoved
since the last, by the stat fingerprint the build keeps in `build.json`
beside the pointer, reads no record at all and answers from the
pointer, an optimisation and never authority, since `--from-genesis`
rebuilds every tool from the records alone.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from livery.strongroom import (
    Digest,
    Entry,
    Namespace,
    Store,
    Subject,
    Tree,
    canonical,
    digest_of,
)
from livery.toolroom.store import (
    BUILD_FILE,
    TOOL_FILE,
    Record,
    build_current,
    observations,
    resolve,
    tree_fingerprint,
)

INDEX = "index"
"""The namespace of the index store: `index/<tool>` names the tool's tree; volatile."""

POINTER = "pointer.json"
"""The pointer document, beside the store's layout at the index root."""

SCHEMA = 1
"""The pointer document's shape. Bumped when a reader must know."""

BY = Subject("call", "tools.index.build")
"""Who moves the index refs: the build, named by its verb."""


@dataclass(frozen=True)
class Built:
    """What one build did, as data.

    Attributes:
        into: The index root, holding the store's layout and the pointer.
        tools: Tool name to the digest of its current tree, every tool.
        rebuilt: The tools materialised on this build.
        reused: The tools whose earlier tree stood, their record unmoved.
        dropped: The tools the earlier pointer named and no record has.
    """

    into: str
    tools: dict[str, str]
    rebuilt: tuple[str, ...]
    reused: tuple[str, ...]
    dropped: tuple[str, ...]


def record_digest(directory: Path) -> Digest:
    """The digest of the record under *directory* as authored: its files' bytes.

    `tool.json` and every delta file, each named by its path under the
    directory and hashed as written, never re-serialised: a record moved
    anywhere digests the same, one edited anywhere does not, and the
    cost is a read of the bytes rather than a parse and a canonical dump.
    """
    parts = sorted(p for p in directory.rglob("*.json") if p.is_file())
    payload = b"".join(
        f"{p.relative_to(directory).as_posix()}\0".encode() + p.read_bytes() + b"\0"
        for p in parts
    )
    return digest_of(payload)


def open_index(into: Path) -> Store:
    """The index store at *into*, created on first use.

    Raises:
        ManifestError: when *into* holds a store of another layout.
    """
    namespaces = (Namespace(INDEX, "volatile"),)
    if (into / "strongroom.json").is_file():
        return Store.open(into, namespaces=namespaces)
    return Store.create(into, namespaces=namespaces)


def read_pointer(into: Path) -> dict[str, Any] | None:
    """The pointer document at *into*, or `None` when there is none.

    Raises:
        ValueError: when the file is there and is not a pointer this
            build understands, naming the file.
    """
    path = into / POINTER
    if not path.is_file():
        return None
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise ValueError(f"{path}: not JSON ({error})") from None
    if not isinstance(found, dict) or found.get("schema") != SCHEMA:
        raise ValueError(f"{path}: not a pointer document of schema {SCHEMA}")
    tools = found.get("tools")
    if not isinstance(tools, dict):
        raise ValueError(f"{path}: the pointer names no tools object")
    return found


def load_records(records: Path) -> list[Record]:
    """Every record under *records*, by name.

    Raises:
        RecordError: for a record that does not validate, as the store
            refuses it.
    """
    return [
        Record.load(path)
        for path in sorted(records.iterdir())
        if path.is_dir() and (path / TOOL_FILE).is_file()
    ]


def build(records: Path, into: Path, *, from_genesis: bool = False) -> Built:
    """Materialise every record under *records* into the index at *into*.

    A tool whose record digest matches the pointer's, and whose tree the
    store still names, is reused; every other tool is materialised and
    its ref moved. A tool the pointer names and no record has is
    dropped. *from_genesis* ignores the pointer and materialises every
    tool.

    Before any record is read, the build asks whether anything moved:
    `build.json` beside the pointer holds a stat fingerprint of the
    records directory from the last build, and when it stands and every
    tree and ref the pointer names is present, the build answers from
    the pointer alone.

    Returns:
        What the build did.

    Raises:
        RecordError: for a record that does not validate.
        ValueError: for a pointer that is not one.
    """
    fingerprint = tree_fingerprint([str(records.resolve())])
    if not from_genesis and build_current(into):
        answer = _standing(into)
        if answer is not None:
            return answer
    loaded = load_records(records)
    digests = {
        record.name: str(record_digest(records / record.name)) for record in loaded
    }
    store = open_index(into)
    previous: dict[str, Any] = {}
    if not from_genesis:
        pointer = read_pointer(into)
        previous = dict(pointer["tools"]) if pointer else {}
    tools: dict[str, str] = {}
    rebuilt: list[str] = []
    reused: list[str] = []
    for record in loaded:
        current = store.ref(INDEX, record.name)
        held = previous.get(record.name)
        stood = (
            isinstance(held, dict)
            and held.get("record") == digests[record.name]
            and current is not None
            and str(current) == held.get("tree")
            and store.state(current) == "present"
        )
        if stood and current is not None:
            tools[record.name] = str(current)
            reused.append(record.name)
        else:
            tree = materialise(store, record)
            if current != tree:
                store.set_ref(INDEX, record.name, tree, previous=current, by=BY)
            tools[record.name] = str(tree)
            rebuilt.append(record.name)
    dropped: list[str] = []
    named = {record.name for record in loaded}
    for stale in store.refs(INDEX):
        if stale in named:
            continue
        current = store.ref(INDEX, stale)
        if current is not None:
            store.drop_ref(INDEX, stale, previous=current)
        dropped.append(stale)
    document = {
        "schema": SCHEMA,
        "algorithm": store.algorithm.name,
        "tools": {
            record.name: {"tree": tools[record.name], "record": digests[record.name]}
            for record in loaded
        },
    }
    (into / POINTER).write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    built_from = {
        "schema": SCHEMA,
        "records": {"path": str(records.resolve()), "fingerprint": fingerprint},
    }
    (into / BUILD_FILE).write_text(
        json.dumps(built_from, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return Built(str(into), tools, tuple(rebuilt), tuple(reused), tuple(dropped))


def _standing(into: Path) -> Built | None:
    """The last build's answer, when every tree and ref the pointer names stand."""
    pointer = read_pointer(into)
    if pointer is None:
        return None
    store = open_index(into)
    trees = {str(name): entry.get("tree") for name, entry in pointer["tools"].items()}
    if any(not isinstance(tree, str) for tree in trees.values()):
        return None
    for digest in trees.values():
        if (
            not isinstance(digest, str)
            or store.state(Digest.parse(digest)) != "present"
        ):
            return None
    # The refs as well as the objects: a ref dropped or moved under the
    # pointer is what a full build repairs, so it is what this must notice.
    for name, tree in trees.items():
        if store.ref(INDEX, name) != Digest.parse(str(tree)):
            return None
    names = sorted(trees)
    return Built(
        str(into), {name: str(trees[name]) for name in names}, (), tuple(names), ()
    )


def materialise(store: Store, record: Record) -> Digest:
    """Land *record* whole into *store* and return its tree's digest."""
    entries: list[Entry] = [
        _blob(store, "tool", record.to_json()),
        _blob(store, "versions", list(record.versions)),
    ]
    seen = {found.version: found for found in observations(record)}
    for delta in record.deltas:
        version: list[Entry] = []
        if delta.hosts:
            version.append(
                _tree(
                    store,
                    "hosts",
                    [
                        _blob(store, host, _deployment(record, delta.version, host))
                        for host in delta.hosts
                    ],
                )
            )
        found = seen.get(delta.version)
        if found is not None:
            version.append(
                _blob(
                    store,
                    "observation",
                    {
                        "date": found.date,
                        "help": found.help,
                        "platforms": list(found.platforms),
                        "extractor": found.extractor,
                        "absent": {
                            verb: {option: list(who) for option, who in options.items()}
                            for verb, options in found.absent.items()
                        },
                    },
                )
            )
            version.append(_blob(store, "surface", found.verbs))
        entries.append(_tree(store, delta.version, version))
    return _tree(store, record.name, entries).digest


def _deployment(record: Record, version: str, host: str) -> dict[str, Any]:
    return resolve(record, version, host).to_json()


def _blob(store: Store, name: str, value: Any) -> Entry:
    data = canonical(value)
    return Entry(name, "blob", store.put(data), len(data))


def _tree(store: Store, name: str, entries: Iterable[Entry]) -> Entry:
    data = Tree.of(entries).encode()
    return Entry(name, "tree", store.put(data), len(data))
