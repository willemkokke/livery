# toolroom bench

The bench is where toolroom's typed tool handles are made and kept
current. It enumerates each tool's releases at its forge or index,
installs a release in a throwaway to read its help on the platform,
writes what each version accepted into the tool's record under
`records/<tool>/` as that version's surface, generates the checked-in
stubs of `livery.toolroom.tools` from the records, and renders the
per-tool reference pages of toolroom's docs.

A record is the store's: `livery.toolroom.store` reads and validates
it, and the bench adds to it. Each version's surface is carried one
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
- `fm tools.docs` writes the per-tool pages into toolroom's docs
  tree; toolroom declares it as its docs generator.

The bench depends on `livery-toolroom`, `livery-toolroom-store` and
`livery-footman` and loads only through footman's plugin entry.
