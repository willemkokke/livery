# toolroom bench

The bench is where toolroom's typed tool handles are made and kept
current. It enumerates each tool's releases at its forge or index,
installs a release in a throwaway to read its help on the platform,
keeps the history of what each release accepted, generates the
checked-in stubs of `livery.toolroom.tools` from that history, and
renders the per-tool reference pages of toolroom's docs.

A consumer of the handles installs `livery-toolroom` alone. A
workspace that keeps the handles current names the bench as a layer,
and its verbs mount under `fm tools.*`:

- `fm tools.refresh` observes what is new on this platform and folds
  it into the history and the stubs; with `--submit` it commits what
  moved on a branch and opens the pull request, armed when every
  change only added to a surface.
- `fm tools.gather`, `fm tools.assemble` and `fm tools.observe` are
  the refresh's halves, for a matrix that observes on several
  machines and assembles once.
- `fm tools.audit`, `fm tools.list` and `fm tools.spec` read the
  history and the stubs back.
- `fm tools.docs` writes the per-tool pages into toolroom's docs
  tree; toolroom declares it as its docs generator.

The bench depends on `livery-toolroom` and `livery-footman` and loads
only through footman's plugin entry.
