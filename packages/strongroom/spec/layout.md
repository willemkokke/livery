# The layout

One layout for every tier. A local objects directory, a read-only
share, a static HTTP server over a folder, and an object-storage bucket
with keys in the same shape are all the same layout, consulted in
order and never trusted. `cp -r` populates a mirror; a static file
server is a tier; the layout is the read protocol.

## The root manifest

`strongroom.json` at the store root, canonical JSON:

```json
{"layout": 1, "algorithm": "sha256"}
```

`layout` is the layout version this document describes. `algorithm`
is the one algorithm of this address space
([digest.md](digest.md)). An implementation that speaks another
layout version or algorithm refuses to open the store, naming both.

## Objects

```text
objects/<algorithm>/<xx>/<rest>
```

The digest's algorithm, its first two encoded characters, and the
rest. Every object is a blob; a tree or a version is a blob in a
specified format, and only the referrer says which.

Objects are immutable once landed and never compressed at rest. How
bytes are held (whole file, packed, chunked, encrypted) is a backend
property and never in the name; this layout version stores whole
files only.

Landing: the bytes stream through the digest into a scratch file and
are moved into place with an atomic replace. When the digest is known
in advance the scratch sits beside the destination; otherwise it sits
under `objects/<algorithm>/`. A scratch name ends in `.part` and
carries a token unique to its writer, so two writers of the same
digest never share one scratch. Both land identical bytes; the second
replace is harmless, so object writes need no lock. A landing whose
destination already exists with the right digest succeeds without
writing. A replace that fails because a reader holds the destination
open is success when the destination verifies.

Landed and verified are two facts. Which objects a tier has verified
itself is the implementation's local state, not a format. Size is
checked on every access; the full digest on fill, on demand, and in a
scrub.

A tombstone ([records.md](records.md)) replaces the object's file at
the same path under the name `<rest>.tombstone`, in every tier that
erases.

## Refs

```text
refs/<namespace>/<path...>
refs/<namespace>/<path...>.record
```

A ref file holds one digest string and a trailing line feed. Its
record ([records.md](records.md)) sits beside it with the `.record`
suffix. Namespaces are hierarchical, so write capability scopes per
subtree; the store owns `pins/` and `pending/` and interprets no other
([namespaces.md](namespaces.md)).

A ref update writes the record and then the ref, each through scratch
and replace, under a per-ref lock, `<path...>.lock`, created
exclusively. A lock is broken only on provable staleness (its holder
has exited, or it is older than the stale bound, or its content is
torn), and the compare-and-swap still decides the winner afterwards,
so a wrongly broken lock costs a retry and never a torn record. A
reader that finds the ref and its record disagreeing reports the pair
as tampered: an out-of-band edit, or an update that died between the
two replaces, and either way not a value to trust.

A ref path's components are portable names ([tree.md](tree.md)), and
none ends in `.record`, `.lock`, `.tombstone` or `.part`.

## The local index

`index/` is the implementation's own and not a format. This
implementation keeps a verified mark per object under
`index/verified/<algorithm>/<xx>/<rest>` holding the size it hashed,
so an access checks size cheaply and an object landed by copy is
hashed in full on first access.

## Scratch

`.part` files are the only thing in the layout that an age rule may
remove. Everything else is reachable or garbage by reference, never by
age.

## The HTTP mapping

A read-only tier over HTTP is the layout served as files: `GET
<base>/objects/sha256/ab/cdef...` returns the object's bytes, `GET
<base>/refs/<namespace>/<path>` the ref file, and the record and the
tombstone at their paths. No code stands between the layout and the
client. Writes are never HTTP puts: across a trust boundary a ref
moves only by a call, and objects land under a lease that a call
minted.
