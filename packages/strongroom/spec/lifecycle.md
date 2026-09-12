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

## Groups of moves

A group moves several refs as a whole or not at all.

1. `begin()` writes `refs/pending/<id>` naming an empty tree, with
   the journal in the record's `meta`: the lease, an empty list of
   moves, and `applied` at zero.
2. `add(id, namespace, path, digest, previous)` requires the target
   present here, appends the move to the journal, and re-points the
   pending ref at a manifest tree with one entry per move, in order,
   each naming that move's target. The pending ref is a root while
   it exists, so the sweep keeps every target from the first move to
   the commit, and removes the manifests the adds superseded.
3. `commit(id)` takes the maintenance lease and every moved ref's
   lock, in sorted ref order, for the checks and the moves only.
   Every move not yet applied is checked first: the compare-and-swap
   against `previous` and the namespace's mutation class, exactly as
   a single move. A ref that already names the move's digest is done
   and is not checked. The first refusal stops the group with
   nothing moved and the pending ref standing. Then the moves apply
   in journal order; after each, the journal's `applied` count
   advances. The pending ref is dropped last.
4. A commit that stops between two applies leaves the journal with
   its count. `commit(id)` again replays it: the moves applied are
   skipped, the rest apply. `retire(id)` refuses such a group, naming
   the moves applied; only a commit finishes it. `add` refuses it
   too.

A group whose journal has no move commits as nothing. One move per
ref: a second move of the same ref in one group is refused at `add`.
The single publish of the previous section is not a group: its
pending ref names the target itself and carries no journal, because
its ref is named at commit.

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
