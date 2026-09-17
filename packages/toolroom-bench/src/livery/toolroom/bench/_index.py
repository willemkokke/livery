"""The published index: every record materialised into a store served as static files.

A client never replays a record. The build resolves every version of
every tool and lands what it resolves to as content-addressed objects,
one tree per tool:

    tool                        the tool axis, `tool.json` as canonical JSON
    versions                    every version tracked, oldest first
    <version>/observation       who read the version; its help, extractor, absences
    <version>/hosts/<host>      the deployment resolved for that host
    <version>/surface/<verb>    one blob per verb, the tool's own options under `_`

Adjacent versions share every unchanged verb and deployment by content
address, and any version is addressable without replay. Refs are local,
so beside the store the build writes one pointer document naming each
tool's current tree and the digest of the record it was built from.
Everything under a tree is immutable and cacheable for as long as anyone
likes; only the pointer needs a short lifetime.

Beside the authored trees the build lands one derived tree, the stubs:

    stubs/<tool>/<version>      the stub a reader at that version gets

A stub is rendered from the union of every version read up to that one,
so a flag the tool later dropped stays completable and its docstring
says when it went. The pointer names the derived tree and records the
renderer's identity, the digest of the code that renders, as a fact
rather than an address: a publish after the renderer moved lands new
blobs, names them in a new tree and moves the pointer, and the blobs it
replaced stay reachable by digest.

The authored half replays to identical digests: two builds from genesis
land equal objects and equal pointers. A build into a directory holding
an earlier build reads that pointer and reuses every tool whose record
digest did not move, and every tool's stubs when the renderer did not
move either, an optimisation and never authority, since
`--from-genesis` rebuilds every tool from the records alone.
"""

from __future__ import annotations

import inspect
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
from livery.toolroom.bench import _drivers, _stubgen, _surfaces, _toolspec
from livery.toolroom.store import TOOL_FILE, Record, observations, resolve

INDEX = "index"
"""The namespace of the index store: `index/<tool>` names the tool's tree; volatile."""

DERIVED = "derived"
"""The namespace of what the index renders; `derived/stubs` names the stubs' tree."""

STUBS = "stubs"
"""The derived tree of stubs, and its ref under `DERIVED`."""

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
        stubs: The digest of the derived tree of stubs.
        rendered: The tools whose stubs were rendered on this build.
    """

    into: str
    tools: dict[str, str]
    rebuilt: tuple[str, ...]
    reused: tuple[str, ...]
    dropped: tuple[str, ...]
    stubs: str = ""
    rendered: tuple[str, ...] = ()


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
    namespaces = (Namespace(INDEX, "volatile"), Namespace(DERIVED, "volatile"))
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
    its ref moved. A tool's stubs are reused when the tool is and the
    renderer did not move, and rendered otherwise. A tool the pointer
    names and no record has is dropped. *from_genesis* ignores the
    pointer and materialises every tool.

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
    same_renderer = False
    if not from_genesis:
        pointer = read_pointer(into)
        previous = dict(pointer["tools"]) if pointer else {}
        same_renderer = pointer is not None and pointer.get("renderer") == renderer()
    tools: dict[str, str] = {}
    stubs: dict[str, Entry] = {}
    rebuilt: list[str] = []
    reused: list[str] = []
    rendered: list[str] = []
    for record in loaded:
        fingerprint = str(record_digest(record))
        current = store.ref(INDEX, record.name)
        held = previous.get(record.name)
        stood = (
            isinstance(held, dict)
            and held.get("record") == fingerprint
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
        driver = _drivers.find(record.name)
        if driver is None:
            continue
        kept = held.get("stubs") if stood and same_renderer and held else None
        if isinstance(kept, str) and store.state(Digest.parse(kept)) == "present":
            data = store.read(Digest.parse(kept))
            stubs[record.name] = Entry(
                record.name, "tree", Digest.parse(kept), len(data)
            )
            continue
        stubs[record.name] = _tree(
            store,
            record.name,
            [
                Entry(
                    version,
                    "blob",
                    store.put(text),
                    len(text),
                )
                for version in _surfaces.versions(record)
                for text in (stub_for(record, version, driver=driver).encode("utf-8"),)
            ],
        )
        rendered.append(record.name)
    derived = _tree(store, STUBS, stubs.values()).digest
    standing = store.ref(DERIVED, STUBS)
    if standing != derived:
        store.set_ref(DERIVED, STUBS, derived, previous=standing, by=BY)
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
        "renderer": renderer(),
        "stubs": str(derived),
        "tools": {
            record.name: {
                "tree": tools[record.name],
                "record": str(record_digest(record)),
                **(
                    {"stubs": str(stubs[record.name].digest)}
                    if record.name in stubs
                    else {}
                ),
            }
            for record in loaded
        },
    }
    (into / POINTER).write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return Built(
        str(into),
        tools,
        tuple(rebuilt),
        tuple(reused),
        tuple(dropped),
        str(derived),
        tuple(rendered),
    )


def renderer() -> dict[str, str]:
    """The renderer's identity: the digest of the code that renders a stub.

    A fact rather than an address, so a published stub can be traced to
    the code that wrote it, and a build can tell whether the stubs it
    holds were rendered by the code it runs. The digest is over the
    sources of the renderer, the union and the spec model.
    """
    code = "".join(
        inspect.getsource(module) for module in (_stubgen, _surfaces, _toolspec)
    ).encode("utf-8")
    return {"code": str(digest_of(code))}


def stub_for(record: Record, version: str, *, driver: _drivers.Driver) -> str:
    """The stub a reader at *version* gets: the union up to that version, rendered.

    The header names the platforms that read *version* and how the tool
    runs in footman's process, as the checked-in stubs' headers do.

    Raises:
        ValueError: when *version* is not one the record has read.
    """
    chain = _surfaces.Chain.of(record, upto=version)
    spec = _surfaces.union_of(chain, name=driver.name)
    return _stubgen.render(
        spec,
        platform=_stubgen._listed(tuple(chain.platforms(version))),
        class_name=_stubgen._class_name(record.name),
        in_process=driver.mode(spec.in_process),
    )


def materialise(store: Store, record: Record) -> Digest:
    """Land *record* whole into *store* and return its tree's digest.

    Raises:
        ValueError: for a verb the index cannot carry: one named
            `ROOT_VERB`, which the tool's own options take.
    """
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
    return resolve(record, version, host).to_json()


def _blob(store: Store, name: str, value: Any) -> Entry:
    data = canonical(value)
    return Entry(name, "blob", store.put(data), len(data))


def _tree(store: Store, name: str, entries: Iterable[Entry]) -> Entry:
    data = Tree.of(entries).encode()
    return Entry(name, "tree", store.put(data), len(data))
