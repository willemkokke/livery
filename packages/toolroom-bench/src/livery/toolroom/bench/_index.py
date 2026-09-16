"""The published index: every record materialised into a store served as static files.

A client never replays a record. The build resolves every version of
every tool and lands what it resolves to as content-addressed objects,
one tree per tool:

    tool                        the tool axis, `tool.json` as canonical JSON
    <version>/observation       who read the version; its help, extractor, absences
    <version>/hosts/<host>      the deployment resolved for that host
    <version>/surface/<verb>    one blob per verb, the tool's own options under `_`

Adjacent versions share every unchanged verb and deployment by content
address, and any version is addressable without replay. Refs are local,
so beside the store the build writes one pointer document naming each
tool's current tree and the digest of the record it was built from.
Everything under a tree is immutable and cacheable for as long as anyone
likes; only the pointer needs a short lifetime.

The authored half replays to identical digests: two builds from genesis
land equal objects and equal pointers. A build into a directory holding
an earlier build reads that pointer and reuses every tool whose record
digest did not move, an optimisation and never authority, since
`--from-genesis` rebuilds every tool from the records alone.
"""

from __future__ import annotations

import dataclasses
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
from livery.toolroom.store import TOOL_FILE, Record, observations, resolve

INDEX = "index"
"""The namespace of the index store: `index/<tool>` names the tool's tree; volatile."""

POINTER = "pointer.json"
"""The pointer document, beside the store's layout at the index root."""

SCHEMA = 1
"""The pointer document's shape. Bumped when a reader must know."""

ROOT_VERB = "_"
"""The entry name of the tool's own options: a verb named `""` in the record."""

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


def record_digest(record: Record) -> Digest:
    """The digest of *record* as authored: its tool axis and every delta, canonical."""
    return digest_of(
        canonical(
            {
                "tool": record.to_json(),
                "deltas": [delta.to_json() for delta in record.deltas],
            }
        )
    )


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
    its ref moved. A tool the pointer names and no record has is dropped.
    *from_genesis* ignores the pointer and materialises every tool.

    Returns:
        What the build did.

    Raises:
        RecordError: for a record that does not validate.
        ValueError: for a pointer that is not one, or a verb whose name
            the index cannot carry.
    """
    loaded = load_records(records)
    store = open_index(into)
    previous: dict[str, Any] = {}
    if not from_genesis:
        pointer = read_pointer(into)
        previous = dict(pointer["tools"]) if pointer else {}
    tools: dict[str, str] = {}
    rebuilt: list[str] = []
    reused: list[str] = []
    for record in loaded:
        fingerprint = str(record_digest(record))
        current = store.ref(INDEX, record.name)
        held = previous.get(record.name)
        if (
            isinstance(held, dict)
            and held.get("record") == fingerprint
            and current is not None
            and str(current) == held.get("tree")
            and store.state(current) == "present"
        ):
            tools[record.name] = str(current)
            reused.append(record.name)
            continue
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
            record.name: {
                "tree": tools[record.name],
                "record": str(record_digest(record)),
            }
            for record in loaded
        },
    }
    (into / POINTER).write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return Built(str(into), tools, tuple(rebuilt), tuple(reused), tuple(dropped))


def materialise(store: Store, record: Record) -> Digest:
    """Land *record* whole into *store* and return its tree's digest.

    Raises:
        ValueError: for a verb the index cannot carry: one named
            `ROOT_VERB`, which the tool's own options take.
    """
    entries: list[Entry] = [_blob(store, "tool", record.to_json())]
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
            verbs: list[Entry] = []
            for name, verb in found.verbs.items():
                if name == ROOT_VERB:
                    raise ValueError(
                        f"{record.name} {delta.version}: a verb named {ROOT_VERB!r}"
                        " cannot be indexed; that name carries the tool's own"
                        " options"
                    )
                verbs.append(_blob(store, name or ROOT_VERB, verb))
            if verbs:
                version.append(_tree(store, "surface", verbs))
        entries.append(_tree(store, delta.version, version))
    return _tree(store, record.name, entries).digest


def _deployment(record: Record, version: str, host: str) -> dict[str, Any]:
    fields = dataclasses.asdict(resolve(record, version, host))
    return {
        key: list(value) if isinstance(value, tuple) else value
        for key, value in fields.items()
    }


def _blob(store: Store, name: str, value: Any) -> Entry:
    data = canonical(value)
    return Entry(name, "blob", store.put(data), len(data))


def _tree(store: Store, name: str, entries: Iterable[Entry]) -> Entry:
    data = Tree.of(entries).encode()
    return Entry(name, "tree", store.put(data), len(data))
