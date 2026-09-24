# toolroom bench

The bench is where toolroom's typed tool handles are made and kept
current. It enumerates each tool's releases at its forge or index,
installs a release in a throwaway to read its help on the platform,
writes what each version accepted into the tool's record,
`records/<tool>.jsonl`, as that version's surface, generates the checked-in
stubs of `livery.toolroom.tools` from the records, and renders the
per-tool reference pages of toolroom's docs.

A new version is checked before its pull request merges. The refresh
stages every host's artifact through the store, from whichever machine
runs it, and runs nine structural checks against the version before:
the declared root is in the archive, every entry point is a file,
every path directory and every shim target is there, every env value
naming a path points at something present, no entry point and no host
the previous version had is gone, every exclusion pattern matches
something, and no executable sits in a path directory without an
annotation. The paths each host gained and lost are the pull request's
summary, and the refresh arms its pull request only when every change
is an addition and every check passed; `fm tools.verify <tool>` runs
the same checks by hand. The executable checks, that the entry point
runs and the surface extracts, need a matching host and belong to the
six-host verification point.

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
  it into the records; with `--submit` it commits what moved on a
  branch and opens the pull request, armed when every change only
  added to a surface.
- `fm tools.gather`, `fm tools.assemble` and `fm tools.observe` are
  the refresh's halves, for a matrix that observes on several
  machines and assembles once.
- `fm tools.prime` reads older releases below a record's floor.
- `fm tools.audit`, `fm tools.list` and `fm tools.spec` read the
  tools and the records back.
- `fm tools.index.build` materialises every record into the index, a
  strongroom store served as static files with a pointer document
  naming each tool's current tree, each version's surface one blob; the bench declares it as a docs
  generator, so the site's build writes it under
  `docs/_generated/index` and the site's deploy serves it.
- `fm tools.docs` writes the per-tool pages into toolroom's docs
  tree, with the stubs they point at rendered beside them; toolroom
  declares it as its docs generator.

A stub is a rendering of a record, and the store renders it from a
version's own surface: a workspace renders the versions it locks with
the workshop's `fm tools.restub`, the docs pages render the union of
every version read, and nothing, the index included, holds a stub.

The bench depends on `livery-toolroom`, `livery-toolroom-store` and
`livery-footman` and loads only through footman's plugin entry.

## The index

The index is one strongroom store, `objects/<algorithm>/<fanout>/<rest>`
beside `refs/` and the manifest, which any static host serves, and
`pointer.json` beside it. Per tool the build lands one tree:

```text
tool                        the tool axis, the record's first line as canonical JSON
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

The build replays to identical digests: two builds from genesis
land equal objects and an equal pointer. A build into a directory that
holds an earlier build reads its pointer and reuses every tool whose
record did not move, and a build whose records directory is unmoved
since the last, by the stat fingerprint the build keeps in `build.json`
beside the pointer, reads no record at all and answers from the
pointer, which is an optimisation and never authority;
`--from-genesis` ignores the pointer and materialises every tool from
the records alone. A tool the pointer named and no record has is
dropped from the pointer and its ref.
