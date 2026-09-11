# Sources and tiers

A store instance is its local objects directory plus an ordered list
of sources. Every tier is the same layout ([layout.md](layout.md)),
consulted in order and never trusted.

## The kinds of source

| Source | What it is | A hit does |
| --- | --- | --- |
| a folder, copy policy | a mirror in the layout on local disk or a mount | lands a local copy, verified on the way in |
| a folder, reference policy | a read-only share where a local copy would be a second copy of everything | answers with a path into the folder, verified by hash on first use and by size after; lands nothing |
| an HTTP base URL | a static file server over a folder in the layout | lands a local copy, verified on the way in |
| an origin hint | one URL with the digest it yields: a tool spec's `{url, sha256}`, a vendor download | lands a local copy, verified on the way in; consulted only for its own digest |

A folder source must hold a root manifest of the same layout version
and algorithm as the store, checked when the store opens. An HTTP
source is checked the first time it is consulted, by reading its
manifest.

## The read path

An object present locally is answered locally. Otherwise each source
is consulted in order:

- a source that does not answer within its connect timeout, or
  answers with a status other than success, is skipped and reported;
  the next source is consulted;
- a source that serves bytes whose digest is not the name is refused
  and reported; the corrupt bytes never land; the next source is
  consulted;
- a source that has the object answers it.

A miss after every source is an error naming the digest. `offline`
means never an origin: an origin hint is passed over, and the miss
names the URL that would have satisfied it, so a disconnected machine
fails closed and says what it needed.

Reports go to a progress sink the caller supplies. The store never
prints.

## Fill

Filling a folder with a set of digests lands each one into the folder
through the store's own read path, creating the folder's manifest
when it has none. The folder is a mirror by construction: it opens as
a folder source and serves every filled digest. This is how a company
tier is populated, by push from a machine that has fetched, never by
a proxy that fetches on demand.

## Fill upward is publication

Only verified content lands in a shared tier. A machine's own cache
can be poisoned by that machine and nothing above it, because every
tier verifies on arrival and none trusts a name it did not check.
