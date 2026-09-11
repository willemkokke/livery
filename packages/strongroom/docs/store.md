# Using the store

A walk through the API in the order a consumer meets it. Every call
is a method of `Store`; every refusal is a class under `StoreError`
whose message names what was found and what to do.

## Open

```python
from pathlib import Path

from livery.strongroom import FolderSource, Namespace, Store

store = Store.create(
    Path("~/.cache/mystore").expanduser(),
    namespaces=[Namespace("tools", "write-once")],
    sources=[FolderSource(Path("/mnt/mirror"))],
)
```

`create` writes the root manifest and opens; `open` opens an existing
store and refuses another layout version or algorithm. Every
namespace the caller will write is declared with its mutation class;
`pins` and `pending` are the store's own. Sources are consulted in
order for an object the store lacks: a folder in the same layout with
a `copy` or `reference` fill policy, an `HttpSource` serving the
layout, or an `OriginHint` naming one URL and the digest it yields.
`offline=True` never consults an origin.

## Objects

```python
digest = store.put(b"bytes")
landed = store.land(stream, expected=digest)
path = store.path(digest)
data = store.read(digest)
where = store.fetch(digest)
```

`land` streams bytes through the digest into a scratch file and moves
them into place; a mismatch against `expected` keeps nothing; an
object that already exists is success without a write. `path` hands
over a read-only path, checking size on every access and hashing an
object this store never verified. `fetch` answers locally or consults
the sources, verifying every hit on arrival. `scrub` hashes everything
and removes what does not match its name. `prefetch` fetches
everything one digest reaches, and `shed` evicts local copies a named
source holds, except what a live view depends on.

## Refs

```python
from livery.strongroom import Subject

me = Subject("person", "willem")
record = store.set_ref("tools", "bun@1.3", tree, previous=None, by=me)
current = store.ref("tools", "bun@1.3")
```

A ref moves by compare-and-swap on `previous`, under a per-ref lock,
with a record written beside it. The namespace's class is enforced:
write-once refuses a second digest, monotone requires a version
whose parents include the current target, volatile needs only the
compare-and-swap. A ref whose record disagrees is reported as
tampered.

## Publishing

```python
pending = store.publish_begin(tree, by=me)
# land what the tree names, here or in the tier the publish fills
store.publish_commit(pending.id, "tools", "bun@1.3", previous=None, by=me)
```

The pending ref roots the target from `begin` to `commit`, so a sweep
during the publish keeps every object it names. A refused commit
leaves the pending ref for a retry; `retire` drops one deliberately.

## Views

```python
record = store.view(tree, Path("work/bun"))
outputs = store.collect(Path("work/out"), ["result.bin", "logs"])
store.drop_view(record.id)
```

`view` fills a directory by the cheapest safe rung per entry and
records which one it used; the view is a root while its directory
exists. `collect` reads declared outputs back into a tree, landing
every object. `drop_view` removes only what the record lists and
names what it leaves.

## Maintenance

```python
report = store.sweep()
stone = store.erase(digest, by=me, reason="erasure request 42")
```

`sweep` marks from every ref and every live view, reads pending refs
again before deleting, and removes the unreached and old scratch.
`erase` writes a tombstone and deletes the bytes; the name stays valid
in every tree that carries it, a path to it fails naming the reason,
and landing it again is refused.
