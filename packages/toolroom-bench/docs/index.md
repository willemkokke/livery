# toolroom bench

The bench is where toolroom's typed tool handles are made and kept
current. It enumerates each tool's releases at its forge or index,
installs a release in a throwaway to read its help on the platform,
writes what each version accepted into the tool's record under
`records/<tool>/` as that version's surface, generates the checked-in
stubs of `livery.toolroom.tools` from the records, and renders the
per-tool reference pages of toolroom's docs.

A record is the store's: `livery.toolroom.store` reads and validates
it, and the bench adds to it and publishes it. Each version's surface is carried one
verb at a time, inherited forward from the version read before it, and
the record keeps beside it who read the version and which options a
platform that read it did not find. A stub renders the union of every
version read, so a flag the tool has dropped stays completable and its
docstring says when it went; the standing claims, since, until and
which platform lacks an option, are derived at render time from those
observations and never written down.

A consumer of the handles installs `livery-toolroom` alone. A
workspace that keeps the handles current names the bench as a layer,
and its verbs mount under `fm tools.*`:

- `fm tools.refresh` observes what is new on this platform and folds
  it into the records and the stubs; with `--submit` it commits what
  moved on a branch and opens the pull request, armed when every
  change only added to a surface.
- `fm tools.gather`, `fm tools.assemble` and `fm tools.observe` are
  the refresh's halves, for a matrix that observes on several
  machines and assembles once.
- `fm tools.prime` reads older releases below a record's floor, and
  `fm tools.restub` re-renders every stub from the records with no
  tool and no network.
- `fm tools.audit`, `fm tools.list` and `fm tools.spec` read the
  tools and the stubs back.
- `fm tools.index.build` materialises every record into the index, a
  strongroom store served as static files with a pointer document
  naming each tool's current tree; the bench declares it as a docs
  generator, so the site's build writes it under
  `docs/_generated/index` and the site's deploy serves it.
- `fm tools.docs` writes the per-tool pages into toolroom's docs
  tree; toolroom declares it as its docs generator.

The bench depends on `livery-toolroom`, `livery-toolroom-store` and
`livery-footman` and loads only through footman's plugin entry.

## The index

The index is one strongroom store, `objects/<algorithm>/<fanout>/<rest>`
beside `refs/` and the manifest, which any static host serves, and
`pointer.json` beside it. Per tool the build lands one tree:

```text
tool                        the tool axis, tool.json as canonical JSON
versions                    every version tracked, oldest first
<version>/observation       who read the version: its help, platforms, extractor and absences
<version>/hosts/<host>      the deployment resolved for that host
<version>/surface/<verb>    one blob per verb; the tool's own options under `_`
```

A version read but never installed has no `hosts`; a version installed
but never read has no `observation` and no `surface`. Adjacent versions
share every unchanged verb and deployment by content address, so any
version is addressable with no replay. The pointer names each tool's
current tree and the digest of the record it was built from; everything
under a tree is immutable, and only the pointer needs a short lifetime.

Beside the authored trees the build lands one derived tree,
`stubs/<tool>/<version>`: the stub a reader at that version gets,
rendered from the union of every version read up to that one, so a
flag the tool later dropped stays completable and its docstring says
when it went. The pointer names the derived tree and records the
renderer's identity, the digest of the code that renders, as a fact
rather than an address; a build after the renderer moved renders every
stub again and the blobs it replaced stay reachable by digest.
`fm tools.goldens` keeps a render change deliberate: two golden records
beside the bench's tests render to checked-in golden stubs, the gate
compares each render byte for byte, and a change to the renderer fails
until that verb moves the goldens in the same change.

The authored half replays to identical digests: two builds from genesis
land equal objects and an equal pointer. A build into a directory that
holds an earlier build reads its pointer and reuses every tool whose
record did not move, which is an optimisation and never authority;
`--from-genesis` ignores the pointer and materialises every tool from
the records alone. A tool the pointer named and no record has is
dropped from the pointer and its ref.
