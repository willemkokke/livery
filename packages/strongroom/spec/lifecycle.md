# The lifecycle

Roots, the sweep, the pending publish, and erasure. Behaviour, pinned
by the scenarios in `conformance/lifecycle.json`.

## Roots

Every ref on disk is a root, in every namespace, whoever declared it:
a consumer's refs root its objects whether or not the process that
sweeps knows the consumer. Pins (`pins/<name>`) are explicit roots.
Pending refs (`pending/<id>`) are the roots of publishes in flight.
The materialiser's live views are roots too, recorded in the local
index while the view lives.

## Marking

Reachability is computed by format-aware walkers, one per structured
format, reading the object by shape: a version yields its tree, its
parents, its receipt and its attachments; a tree yields its entries;
anything else is a leaf. No namespace's meaning is read. A blob whose
bytes happen to be a tree is walked as one, which keeps more than
needed and never less: the safe direction. An absent or erased digest
is reached and not walked; a tree still names an erased entry, and
availability is a fact separate from reachability.

## The sweep

Under an exclusive maintenance lease, mark from every root, then read
the pending refs again immediately before deleting and mark from
every one of them, then remove every object marking did not reach,
with its marks. A publish that began after the roots were read is
rooted by its pending ref, which is how a dedup skip never races the
sweep: `publish.begin` requires the target present and roots it
before anything else, so every object it names is reachable from a
root the moment begin returns.

Tombstones are never removed. The one age rule is orphaned scratch:
a `.part` file older than the age bound is removed.

Dropping a pin runs under the same maintenance lease as the sweep,
never beside one. Pruning history under a retention class, when it
exists, does the same.

## The pending publish

1. `publish.begin(target)` requires the target object present here
   and writes `refs/pending/<id>` naming it, with the lease's length
   in the record's `meta`. The ref is a root while it exists.
2. The bytes the target names land, here or in the tier the publish
   fills.
3. `publish.commit(id, namespace, path)` moves the real ref under its
   namespace's mutation class, then drops the pending ref.

A refused commit leaves the pending ref standing for a retry. A
crashed publish leaves a pending ref that a person, or a configured
timeout acting on the recorded lease, retires deliberately with
`retire(id)`. Nothing removes a pending ref on a clock.

## Dropping refs

Only a volatile ref may be dropped, by compare-and-swap on its
current digest: the ref file and its record go together. A write-once
or monotone ref is never dropped; history under those classes goes by
pruning under a retention class.

## Erasure

Erasure wins and immutability keeps the fact. Erasing writes the
tombstone ([records.md](records.md)) under the object's path, in
every tier that erases, and then deletes the bytes and the object's
marks. Trees and versions are untouched: their digests still name
what was published, history still verifies structurally, and a path
to the erased object fails naming the tombstone's instant, authority
and reason instead of silently serving content with holes. Landing
the bytes again is refused while the tombstone stands. An absent
object may be erased, which refuses its future landing.

## The three object states

| State | On disk | `path` |
| --- | --- | --- |
| present | the object file | the path, verified |
| absent | nothing | missing, so the sources are consulted |
| erased | the tombstone, no object file | refused, with the reason |
