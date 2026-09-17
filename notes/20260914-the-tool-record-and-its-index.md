# The tool record and its index

Status: ruled 2026-09-16 as drafted. Phases 1 to 4 landed 2026-09-16
(livery#620, livery#622, livery#625, livery#627); phase 5's index half
landed 2026-09-17 (livery#629), its wheel half deferred to phase 6 by
the ruling of 2026-09-17. Phase 6 lands in parts: the three sites,
resolution and the lock (livery#631) in progress; receipts,
materialisation and the modes, then the stubs' home, follow.
Runs after [CI declared, contributed, and dispatched on command][ci-plan],
which delivers the mechanism this plan's last phase uses.
Subsumes phase 2 onward of [the tool store over strongroom][store-plan],
whose phases 0 and 1 landed and stand. Its ground-truth contracts 1 to 12
stay in force and are not restated here, with one exception: its contract
4 ends "the JSON is byte-compatible with hse's, so hse's files move
without edits", and that clause is dropped. The half of it that stands is
restated as contract 14 below. Where a contract here narrows another of
its twelve, the narrowing is said where it happens.

## What this is

A cacheable index and a resolver for versioned portable command-line
utilities, with typed stubs. What PyPI and uv are for Python
distributions, this is for the tools a project runs.

The parts line up:

| PyPI and uv | here |
| --- | --- |
| the simple index, static files | the index, a strongroom store served static |
| a file, immutable once published | an object, immutable by its digest |
| a wheel tag per platform | a host's artifact and deployment |
| `uv.lock`, cross-platform, one move | the repository's lock, one version per tool |
| the shared cache under `~` | the machine-wide store, one copy per version |
| a yanked release | a tombstone on a withdrawn version |
| type stubs on the index | the stub beside the surface it came from |

Two differences decide real things, so they are stated rather than left
to be discovered.

**Tools do not depend on each other.** There is no transitive graph, so
resolution is per tool and independent. A lock "like uv's" means uv's
semantics, not uv's resolver: the hard part of a Python resolver does not
arrive here, and open question 3 should be read with that in mind.

**A wheel installs into an environment; a tool is a tree on PATH.** That
has no PyPI analogue, and it is where the materialisation modes and the
entry-point annotation come from.

## What changes, and why

Two datasets describe each curated tool today, both keyed by version, both
filled by walking the same forge release listings: the CLI surface under
`packages/toolroom-bench/src/livery/toolroom/bench/_history/` (31 files,
1.3 MB) and the artifact spec the store reads. They answer one question
each about the same release, and a release moves both.

They become **one record per tool**, holding every version tracked and,
per version and host, the artifact, its deployment, and the CLI surface.
The record is authored in git as forward deltas and published as a
content-addressed index a client reads by digest. What a consumer
installs shrinks to the handles, a reader and strongroom: the stubs stop
riding in the wheel and are fetched instead.

## Where things stand

- `packages/toolroom/` is the handles, `livery.toolroom.tools`: standard
  library only, no dependency, no HTTP. It ships `_stubs/` today, 732 KB
  across 38 files, and `testing.py` as a public submodule.
- `packages/toolroom-store/` is the install engine, landed:
  `_spec.py` (the spec model), `_home.py` (the namespaces and the home),
  `_engine.py` (`ensure`, `link`, `fetch`, `_unpack`, `_apply_shims`). It
  depends on `livery-toolroom` and `livery-strongroom`.
- `packages/toolroom-bench/` is the stub machinery, and where the forges
  are: `_toolhistory.py` (the backward chain), `_toolspec.py` (Verb,
  Option), `_stubgen.py` (`render`), `_toolfetch.py` and `_provision.py`
  (release walking), `_drivers.py` (the drivers table), `_tasks.py` (the
  `fm tools.*` verbs). It depends on `livery-toolroom` and footman, and
  not on the store.
- `packages/strongroom/` is the content-addressed store underneath:
  `_store.py` (`land`, `view`, `collect`, refs, `fetch`), `_tree.py`
  (Entry, Link), `_sources.py` (FolderSource, HttpSource, OriginHint).
  No dependency.
- `packages/workshop/` carries the kind seam (`_kinds.py`, with
  `kind_tools` and `kind_chain`) and the derived tool profile
  (`_env_tasks.py`, `tool_profile`).

Related open issues: #381 (kindcheck retires into the check registry),
#523 (a removed package leaves residue), #571 (uv cache invalidation,
deferred until the store needs caching in CI), #574 (strongroom's
conformance harness leaves the eager import, independent of this plan).

## Sequencing

Phases 1 to 7 depend on nothing in the [CI plan][ci-plan] and can start as
soon as this one is ruled. Only phase 8 waits, on that plan's phase 5.

## Ground-truth contracts (do not violate)

1. **One record per tool.** Parallel work on two tools never touches one
   file. A record holds every version tracked, and per version and host
   the artifact, the deployment and the surface.
2. **No layout field is derivable in code.** Every field resolves from
   data at tool, host or version, most specific winning. A pattern (a
   root template, a Windows suffix convention) may fill a field in while
   authoring and never overrides what the data says. A rule moved into
   code turns a future data edit into a future release.
3. **Resolution is total and checked.** Every host of every version
   tracked resolves to a complete deployment, and an override that
   shadows nothing refuses.
4. **Entry points are annotated, never discovered.** What goes on PATH is
   declared per deployment. Sweeping a directory would put a bundle's own
   interpreter on PATH ahead of the system one. Restructuring an archive
   stays a rare escape hatch.
5. **The annotation is what makes a tree portable.** The same entry points
   feed `Store.collect(executable=...)`, so one archive lands one tree
   digest on every platform by construction, not by the file modes the
   extractor happened to produce.
6. **The index is append-only.** A publish adds objects; nothing
   published is rewritten. Content addressing gives this for free: a
   changed input lands as a new object under a new digest and the pointer
   moves, so every earlier object stays valid.
7. **The authored half replays to identical digests; the derived half does
   not have to.** A replay from genesis reproduces the record and the
   surfaces byte for byte, because those digests are what a lock and a
   receipt name. A stub is rendered from them, so its digest moves when
   the renderer improves, and that is the point of regenerating it. The
   record's `extractor` generation stays what it is: a fact about the
   reading, on the authored side, where replay determinism lives.
8. **The published snapshot is an optimisation, never authority.** A build
   reads the last published state and applies new deltas; the git deltas
   remain the only source that can be replayed.
9. **The client never replays a delta stream.** The publish materialises
   every version, because installing an old version needs that version's
   resolved deployment.
10. **A consumer installs no publisher.** The ingest, the delta replay and
    the publish live where no consumer of tools reaches them.
11. **Nothing on the merge path waits on anything outside the repository.**
    The six-host verification is a scheduled point, never a gate leg. The
    contracts governing a contributed point are the CI plan's.
12. **Strongroom knows no tool.** Any gap found there is an issue there.
    livery#574 (the conformance harness leaving the eager import) is filed
    and is not part of this plan.
13. **A record is inert data.** It names a kind, the artifact per host
    with a mandatory digest, and the layout; a digest missing is a
    validation error, and no record can run a command.
14. **The record is shaped by what resolution needs.** Field names,
    ordering and substitutions are decided here on their merits. No other
    project's file format constrains them, and nothing reads a record but
    the code in this workspace.

## The design

### The record

Authored in git, one directory per tool:

```
records/<tool>/tool.json                    the tool axis
records/<tool>/deltas/<nnnn>-<version>.json forward, append-only
```

`tool.json` carries what does not move with a version: the name, the
description, the kind, the version floor for a `system-check` tool, the
hosts the tool has, the tool-level deployment defaults and the per-host
overrides of them.

A delta is one version's arrival: its date, per host the artifact (URL and
digest) and whatever deployment fields that version or that version and
host override, and the surface read for it. Forward, because a new release
is then a one-entry append and the whole of the review diff. Priming
backwards is an occasional deepening and pays the rewrite instead.

### Resolution

A deployment resolves through four layers, most specific winning:

```
tool defaults  <  host override  <  version override  <  version-host override
```

The fields are the layout: `root`, the annotated `entry_points`, `paths`,
`env`, `shims`, and the exclusion patterns applied before import. An
override refuses at load when it names a host or a version the record does
not carry, and when it restates the value it inherits, which is dead data
that later reads as intent. This reading of "shadows nothing" is the
agent's; open question 9 asks for the ruling.

### The surface

A version's surface is a tree with one blob per verb, so adjacent versions
share every unchanged verb by content address and any version is
addressable with no replay. Per verb is the granularity: per option, a
manifest entry costs about what the option costs.

### The published index

A strongroom store served as static files. Object paths are
`objects/<algorithm>/<fanout>/<rest>`, which any static host serves, so
GitHub Pages to start and one string to move later.

```
<tool>/<version>/hosts/<host>   the resolved artifact and deployment
<tool>/<version>/surface/<verb> one blob per verb
stubs/<surface-digest>          the rendered stub
```

The first two are authored and replay to the same digests forever. The
third is derived: a publish after the renderer improves lands new blobs,
names them in a new tree, and moves the pointer. The blobs it replaced
stay reachable by digest and nothing needs them.

Refs are local, so a client cannot learn which tree is current for a tool.
One published pointer document names each tool's current tree digest.
Everything under it is immutable and cacheable for as long as anyone
likes; only the pointer needs a short lifetime.

### Stubs

Rendered by the publish, not by the consumer. `_stubgen.render()` is
already a pure function of a `ToolSpec` and three strings, so the job that
materialises a version renders its stub in the same pass.

A stub carries no generation number and a consumer chooses no generation.
It fetches the stub the pointer names for the version it has. A renderer
that improves re-renders every tracked version on the next publish, which
costs about 19 KB per stub, and every consumer picks the new one up.

A golden test keeps a render change deliberate: golden surfaces render to
checked-in golden stubs, and a diff fails the gate until the goldens are
updated in the same change. That is the whole guard. It catches an
accidental change to the output and says nothing about generations.

The publish records the renderer's own version in the pointer document, as
a fact rather than an address, so a published stub can be traced to the
code that wrote it.

### The consuming side

A tool installed into a workspace leaves a receipt carrying the exact
version and the hosts it is installed for. Entering the environment
materialises the bundle of receipts that package set needs, not every
receipt in the workspace. Resolution needs no network, because the record
holds every version tracked.

Three sites declare tool requirements, and each declares a constraint
rather than a version:

- **a package kind**, in the kind record, for the tools its checks run.
  A kind is layer content, so this is how a layer contributes tools,
  beside the checks it registers through the kind seam and the points it
  contributes under the [CI plan][ci-plan]. `kind_tools()` unions them
  across the kinds present already, and `kind_chain()` inherits a parent
  kind's.
- **a package instance**, in `packages/<name>/workshop.toml`, for what
  its kind cannot know.
- **the project**, in the root `workshop.toml`, for what belongs to the
  repository rather than to any one package.

`tool_profile()` does the first of these today, but the python kind's
tools are a hardcoded branch in it rather than the kind's own data, which
is the rule-in-code contract 2 forbids. Moving that list into the python
kind record is a deliverable, not a refactor left for later.

The whole repository locks one version per tool, and it moves as one.
Constraints from the three sites union, and resolution picks a single
version per tool for the repository, so two packages can never run their
checks on two versions of one checker. An upgrade is an act on the
repository, not on a package. Constraints that cannot all be satisfied
refuse, naming the tool and each constraint with the site that declared
it.

The lock is cross-platform. A version is eligible only when the record
resolves it on every host the repository locks for, so a tool that gained
a host late cannot be locked at a version predating that host's first
build. The refusal names the host and the first version that has it.

What stays per package set is which locked tools get materialised, never
which version. The bundle is resolved fresh from the kinds in use and the
declarations present, so dropping a kind drops its tools the same way
removing a package removes its workflow.

A requirement naming a tool with no record refuses at load, naming the
tool, because a requirement that cannot resolve offline is not a
requirement.

This is what hse inherits. It becomes a layer over the workshop, so the
kinds it uses bring their tools, it declares its own beside them at
whichever of the three sites owns them, and it fetches every one from the
index like any other consumer. There is no hse path and nothing it
provisions for itself.

Materialisation mode is a per-tool default, overridable: link into the
checkout's bin directory, put the shared view directory on PATH, or no
PATH at all for a tool reached only through a typed handle. A bundle is a
tree, not one executable, so the link mode works only where the
application resolves its own real path before looking for its data. The
shared-directory mode is the normal case for a bundle, at the cost of PATH
churn when a pin changes, since the directory name carries the version.

Disk is not at stake. A view at `tools/<name>@<version>` under the store
home is shared by every checkout on the machine, filled by clone then
hardlink before copy, with only executables forced onto their own inode.

### Ingest verification

A new version is tested against the deployment already known, and anything
unresolved is named in the pull request and blocks the merge. The refresh
arms its own pull request only when the change is additions-only today;
that predicate widens to "every ingest check passed".

Structural checks need no execution and cover all six hosts from any
machine: the declared root exists, every entry point exists and is
executable, every path directory exists, every shim target resolves, every
env value naming a path points at something present, no entry point
disappeared, no host that had a build lost one, every exclusion pattern
still matches something, and no executable appears in a declared path
directory without being an annotated entry point.

Adjacent versions are content-addressed trees, so the structural diff of
added and removed paths is free and is the reviewer's summary.

Executable checks need a matching host: the entry point runs, and the
surface extracts. The second is the strong one and exists already. A CLI
that can be interrogated and parsed has a deployment good enough to run
it, which is why ingest and validation are one act.

### The verification point

The six-host verification runs on a scheduled point owned by
toolroom-bench, fortnightly, declared through the mechanism the CI plan
delivers. All six host keys have free runners on a public repository:
`ubuntu-latest`, `ubuntu-24.04-arm`, `macos-latest`, `macos-15-intel`,
`windows-latest`, `windows-11-arm`. The gate keeps its three.

## Phases

The plan assumes the record's reader lives in `livery.toolroom.store` and
the bench depends on the store. Open question 1 rules it. If it goes the
other way, phases 1, 2 and 3 change their target package and nothing else
in this plan moves.

### Phase 1: the record, and total resolution

Deliverables:

- `_record.py` in `livery.toolroom.store`: the tool axis, the version
  deltas, the four override layers, `resolve(record, version, host)`.
- Refusals: an override naming a host or version the record lacks, an
  override restating what it inherits, a version whose host resolves
  incomplete, a delta out of sequence.
- A JSON schema exported beside the records, as `_spec.export_schema`
  does today.
- `records/` for the six tools this repository pins, converted from their
  specs. The conversion is a one-time proof that nothing was lost, run by
  a test in this phase and then deleted with the specs.
- `_spec.py` deleted, not kept beside the record, and `specs/` with it.
  Nothing reads the spec format outside this workspace, so there is no
  period where both are read and no other project to keep in step.

Acceptance:

- `uv run fm check` exits 0.
- `uv run python -m pytest packages/toolroom-store/tests/test_record.py`
  passes, refusal cases first.
- A test resolves every host of every version of every record under
  `records/` and asserts each deployment complete.
- `grep -rn "_spec\|specs/" packages/toolroom-store/src` returns nothing.

### Phase 2: annotated entry points, and one tree digest everywhere

Deliverables:

- `entry_points` in the resolved deployment, fed to
  `Store.collect(executable=...)` in `_engine.py`, replacing
  `executable=(definition.exe,) if definition.exe else ()`.
- Exclusion patterns applied before collect, separable from the
  annotation and justified by size alone.
- The refusal when a declared entry point is absent from the extracted
  tree, naming the tool, the version, the host and the path.

Acceptance:

- `uv run fm check` exits 0 on every gated runner, windows-latest
  included.
- `uv run python -m pytest packages/toolroom-store/tests/test_engine_tree.py`
  lands one archive twice, once through a POSIX-mode extraction and once
  through a Windows-mode one, and asserts the two tree digests equal.

### Phase 3: the surface as a tree, and the record absorbs the history

Deliverables:

- The surface written as one blob per verb, with the merged-platform
  reading and `extractor` keeping their present meaning.
- A converter from `_history/<tool>.json` to the record's deltas, and a
  test that each tool's union through the new form equals its union
  through `_toolhistory.union` today.
- All 31 tools converted, and `_toolhistory.py` deleted with `_history/`.
  The converter is the one-time proof and goes in the same change.

Acceptance:

- `uv run fm check` exits 0.
- `uv run python -m pytest packages/toolroom-bench/tests/test_record_conversion.py`
  passes for all 31 tools.
- `uv run fm tools.restub` produces stubs byte-identical to the checked-in
  ones for every tool. This proves the conversion, not a promise about
  stub output; the goldens move whenever the renderer improves.

### Phase 4: the build, the publish, and the pointer

Deliverables:

- The replay: genesis to head, materialising every version, in the bench.
- The publish into a strongroom store, the pointer document, and the
  Pages job that serves it.
- The snapshot read as an optimisation, with a `--from-genesis` that
  ignores it.

Acceptance:

- `uv run fm tools.index.build --from-genesis --into <dir>` twice into two
  directories produces equal pointer documents and equal object sets.
- `uv run fm tools.index.build --into <dir>` after one new delta writes
  only objects that delta reaches, proven by comparing the object sets.
- `uv run fm check` exits 0.

### Phase 5: stubs in the index, and out of the wheel

Deliverables:

- The publish renders a stub for every tracked version and names them in
  the derived tree; the pointer records the renderer's version.
- `_stubs/` leaves the toolroom wheel; a workspace materialises the stubs
  for the tools it uses, from the pointer.
- The golden test: golden surfaces render to checked-in golden stubs, and
  a diff fails the gate until the goldens move in the same change.

Acceptance:

- `uv run python -m zipfile -l dist/livery_toolroom-*.whl` lists no
  `_stubs/` entry.
- `uv run python -m pytest packages/toolroom-bench/tests/test_stub_golden.py`
  fails on an undeclared render change and passes when the goldens move
  with it.
- `uv run fm tools.restub` against a folder source, with the network
  unreachable, writes every stub a workspace uses.

Open, by the ruling of 2026-09-17: the first and third items are phase
6's, with the lock that names the tools a workspace uses; they stay open
here until that evidence lands. The second item is met.

### Phase 6: receipts, per-package requirements, the lock

Deliverables:

- The receipt, carrying the exact version and the hosts installed, and
  replacing `pinned` in the record. The word already means the release
  train's annotated tag, so it is explained where introduced or renamed
  under open question 8.
- The python kind's tools moved from the hardcoded branch in
  `tool_profile()` into the kind record, and the branch deleted.
- Constraints at the three sites: the kind record, the package instance's
  `workshop.toml`, and the project's.
- Repository-wide resolution: one version per tool, eligible only where
  the record resolves it on every locked host; refusals naming the tool,
  each constraint and its site, and for a host gained late the first
  version that has it.
- The bundle a package set materialises, which selects from the lock and
  never picks a version; offline resolution against the record; the lock
  and its upgrade as acts on the repository.
- The three materialisation modes, defaulted per tool and overridable.

Acceptance:

- `uv run fm tools.add ruff` writes a receipt and a lock entry with no
  network, proven by running it with the sources set to a folder.
- A package declaring `type = "python"` and no tool of its own resolves
  the python kind's tools, proven by
  `uv run python -m pytest packages/workshop/tests/test_kind_tools.py`,
  with the refusals forced first: two constraints that cannot both be
  satisfied, a requirement with no record, and a version that resolves on
  five hosts of six.
- `grep -n 'ruff", "pytest"' packages/workshop/src/livery/workshop/_env_tasks.py`
  returns nothing.
- `uv run fm tools.upgrade ruff` moves the one lock entry and every
  package with it, proven by the lock's diff naming one version.
- `uv run fm env.check` names each tool's receipt version and the drift
  when the resolved deployment has moved under it.
- `uv run fm check` exits 0 on every gated runner.

### Phase 7: ingest verification

Deliverables:

- The nine structural checks, run for all six hosts from any machine.
- The structural diff of added and removed paths as the pull request's
  summary.
- The refresh's arming predicate widened from additions-only to every
  ingest check passed.

Acceptance:

- `uv run python -m pytest packages/toolroom-bench/tests/test_ingest_checks.py`
  passes, one forced failure per check before the passing case.
- `uv run fm tools.refresh --dry-run` against a fixture whose new version
  drops an entry point reports the failure and does not arm.

### Phase 8: the six-host verification point

Depends on the CI plan's phase 5, which is what lets a package declare a
point at all.

Deliverables:

- The point declared by `packages/toolroom-bench`, fortnightly, six
  runners, running the executable checks: the entry point runs and the
  surface extracts.
- The gate unchanged, still three runners.

Acceptance:

- `uv run fm template.apply` writes the point's workflow with the six
  runners, and `uv run fm template.check` exits 0 after it.
- One dispatched run is green on all six legs.
- `uv run fm check` exits 0 and its runner list is unchanged, proven by
  `grep runners workshop.toml`.

## Temporary, replaced by

| Temporary | Replaced by |
| --- | --- |
| `_history/<tool>.json` in the bench's wheel, 1.3 MB, 31 files | phase 3 |
| `_stubs/` shipped in the toolroom wheel, 732 KB, 38 files | phase 6, by the ruling of 2026-09-17 |
| `executable=(definition.exe,)` in `_engine.py` | phase 2 |
| `Spec.pinned` as the per-project version | phase 6 |
| `specs/<name>.json` and `_spec.py` | phase 1, deleted |
| `_toolhistory.py`, the backward chain | phase 3, deleted |
| `fm tools.pin` writing a spec | phase 1 |

## Decision record

- 2026-09-13 and 2026-09-14, Willem: the surface history and the artifact
  spec become one record per tool, authored as forward deltas in git and
  published as a content-addressed index. The decisions are the 36 items
  of the handover this plan was written from, carried into the contracts
  and design above.
- 2026-09-14, Willem: the stubs leave the toolroom wheel.
- 2026-09-14, the agent proposed and Willem accepted: rendered stubs are
  published in the index rather than generated on a consumer's machine.
  Generating on demand would put `_stubgen.py` and `_toolspec.py` plus the
  surface model on the path of anyone wanting a typed handle, a regression
  against a wheel that carries rendered stubs and needs no dependency.
- 2026-09-14, Willem asked how an immutable index survives regenerating
  stubs with typing features that do not exist yet. Tombstones stay for a
  withdrawn upstream release and are not the instrument here.
- 2026-09-14, Willem: nothing needs backwards compatibility. The only end
  user product today is stub files, and a stub carries no such concern.
  Applied: the stub generation number and the compatibility ladder that
  needed it are gone, `_spec.py` and `_toolhistory.py` are deleted in the
  phase that replaces each rather than kept beside it, and the
  conversions become one-time proofs that go with the code they prove.
- 2026-09-14, Willem: compatibility with hse is not wanted. hse becomes a
  layer on top of the workshop, so it consumes the record through the same
  index as every other workspace rather than authoring specs of its own.
  There is one authoring site, and the record's shape answers to
  resolution alone.
- 2026-09-14, Willem: a package inherits the tool requirements of the
  workshop package kinds it uses, as well as declaring its own, and gets
  the tools from the index like every other consumer. So the kind seam
  carries the tools its checks run, a layer contributes tools through its
  kinds, and hse inherits both halves with no path of its own. This does
  not answer which checkers can leave the venv (the superseded note's open
  item 1); it says where that answer gets written down, which is the
  python kind.
- 2026-09-14, Willem: a package kind, a package instance and a project
  each declare tool requirements, and the whole repository locks one
  version per tool, cross-platform, moving as one. So the three sites
  declare constraints rather than versions, resolution is repository-wide
  and a version is eligible only where it resolves on every locked host,
  an upgrade acts on the repository, and what stays per package set is
  which locked tools get materialised. This answers the question of
  per-package divergence: there is none.
- 2026-09-14, Willem: the CI work leaves this plan and becomes
  [its own][ci-plan], which runs first. The full gate dispatched on
  command, workflows declared rather than hand-written, and a package
  contributing a point are one subject; what stays here is the point this
  plan declares and the checks it runs.
- 2026-09-14, Willem, the pitch: a cacheable PyPI plus uv for versioned
  portable utilities, plus stubs. Recorded at the head of this note as
  what the work is, with the two places the analogy stops: tools have no
  transitive dependencies, so the resolver is per tool, and a tool is a
  tree on PATH rather than an install into an environment.
- 2026-09-14, the agent, extending the ruling on replay: content
  addressing already makes the index append-only, since a changed input
  lands under a new digest and the pointer moves. So replay determinism
  is worth demanding of the authored half, which a lock and a receipt
  name, and not of the rendered stubs, whose digests are meant to move
  when the renderer improves. A golden test keeps a render change
  deliberate and the pointer records the renderer's version for tracing.
- 2026-09-14, the agent, for the ruling: a separate library for the record
  format and the index was argued down. The reader and the install engine
  always install together, so a package between them never arrives alone;
  cutting the other way, one library used by both the bench and the store
  puts the publisher on a consumer's disk. The recommendation is the
  reader in `livery.toolroom.store` with the bench depending on it, and an
  eighth distribution only when a third consumer wants the format without
  the installer, or the build half must ship where the bench cannot go.
- 2026-09-14, Willem: `livery.strongroom.conformance` and the conformance
  scenarios in the wheel are livery#574, independent of this plan.
- 2026-09-14, the agent: a consumer of strongroom is not read-only. It
  calls `land`, `set_ref`, `ref`, `view`, `views` and `state`, and never
  operates a published store. The axis is client against operator, so no
  read-only tier is planned; splitting `Store` would revise the published
  protocol `StoreLike` and no consumer needs it.

- 2026-09-16, Willem: the plan starts as drafted. The agent read that as
  the ruling on the two open questions phase 1 needs, and took the plan's
  own proposals: the reader lives in `livery.toolroom.store` with the
  bench to depend on it (open question 1), and an override that shadows
  nothing is both an override naming a host or version the record lacks
  and one restating the value it inherits (open question 6).
- 2026-09-16, the agent, at phase 3, the surface's form in git: a delta
  carries a `surface` naming the verbs the version changed, each whole,
  and the tool's description when it changed; every other verb is
  inherited from the version read before it, a verb set to `null` is
  withdrawn, and a verb or description restating what it inherits is
  refused at load, the layout's rule applied to the surface. So a new
  release is one delta and the whole of the review diff, and the index
  of phase 4 materialises each version by resolving the inheritance,
  which is the replay contract 8 allows the publisher and contract 9
  denies the client. Beside the surface the delta carries the facts of
  the observation as the history did, `platforms`, `extractor` and the
  `absent` sidecar, never inherited. A delta carries an artifact, a
  surface, or both: a version with a surface and no artifact is tracked
  for its stub and installs nowhere, which is what the 24 tools that had
  no spec and the older versions of the 7 that had one are.
- 2026-09-16, the agent, at phase 3, writing the record: every write
  resolves each version whole, changes one version's reading, and
  derives every delta's sparse surface again in sequence. So placing a
  version between two, or a platform widening an older version, rewrites
  that version's delta and re-anchors the one after it on the value it
  had, and nothing any other version resolves to moves. The history's
  three write paths (`extend`, `promote`, `insert`) become one, `place`,
  and its deltas are renumbered when an older version arrives, the
  rewrite the design accepted for priming.
- 2026-09-16, the agent, at phase 3, the proof: a one-time converter
  wrote the 31 histories into records, merging the 7 that had artifacts,
  and a proof script compared each tool through both forms: every
  version's surface, platforms, absences, dates and extractor, the
  union, the changelog span between neighbours and the release decision.
  31 of 31 agreed, `fm tools.restub` re-rendered 31 stubs and changed
  none, and the converter, the proof and `_toolhistory.py` went in the
  same change. A record is named by the curated key (`ruff_format`,
  `ssh_keygen`), the name its stub and its driver carry; its kind comes
  from the driver's provision tier for a tool read for the first time,
  and a record that exists keeps its own. The bench now depends on the
  store, the graph the plan names, and the standalone repository's
  release roll (`fm tools.prepare-release`) went with the history it
  anchored on, since the release train owns versions here.
- 2026-09-16, the agent, at phase 3, the acceptance as met: `fm check`
  exits 0 on the full tree; the conversion's proof ran as a script over
  the 31 histories rather than as the checked-in
  `test_record_conversion.py` the acceptance named, since the phase's
  own text has the converter go with the change and a test of a deleted
  module cannot stay; what stays in the gate is the render check over
  every curated tool, which compares each checked-in stub against a
  render from its record, and `fm tools.restub`, which re-rendered 31
  and changed none. Found live and fixed in place: `fm tools.restub`
  exited 1 on main, since the formatter's raw spawn was a note the
  runner refuses; the spawn now hands over its directory and
  environment on purpose.
- 2026-09-16, the agent, at phase 4, the index's shape: one strongroom
  store at the index root, its layout served as it is, and
  `pointer.json` beside it naming per tool the current tree and the
  digest of the record it was built from, so a build can tell a moved
  record from one that stood. Per tool one tree: `tool` for the axis,
  and per version `observation` (help, platforms, extractor, absences,
  date), `hosts/<host>` with the resolved deployment, and
  `surface/<verb>` one blob per verb with the tool's own options under
  `_`, a name a verb may not take. A version read but never installed
  has no hosts; one installed but never read has no observation and no
  surface. The stubs, the renderer's version in the pointer and the
  derived tree are phase 5's.
- 2026-09-16, the agent, at phase 4, the snapshot: the last published
  state a build reads is the pointer at the directory it builds into,
  and a tool is reused when the pointer's record digest matches and the
  store still names its tree; `--from-genesis` ignores the pointer. The
  Pages job is the site's own deploy: the bench declares
  `tools.index.build` as a docs generator, the site's build writes the
  index under `docs/_generated/index`, gitignored and rebuilt like every
  generated tree, and the deploy serves it under the site's URL. A
  fresh checkout holds no pointer, so a CI build is a build from
  genesis; the reuse pays off where a directory persists, and the
  acceptance proves both forms land the same objects. Refs in the
  published store carry the build's own clock in their records and are
  not part of the equality the acceptance names; the objects and the
  pointer are.
- 2026-09-16, the agent, at phase 4, the acceptance as met: two builds
  from genesis on the 31 records landed 1803 objects each, equal as
  sets, with byte-equal pointers, 16 MB on disk; a third build into the
  first directory reused all 31; one new delta on a fixture record wrote
  exactly the objects its new tree reaches beyond the old one, proven in
  the bench's tests; `fm docs.build` ran the generator and the site
  carries `_generated/index/pointer.json`; `fm check` exits 0.
- 2026-09-17, Willem: phase 5 lands its index half now, and `_stubs/`
  leaves the toolroom wheel with phase 6, beside the lock that says
  which tools a workspace uses. The agent had asked where materialised
  stubs live once they leave the wheel; the proposal carried into phase
  6 is a stub root at `typings/`, which pyright and basedpyright read
  with no configuration and mypy, ty and pyrefly through one path entry
  each in the rendered configs. Phase 5's acceptance item on the wheel
  stays an open line until phase 6 delivers it.
- 2026-09-17, the agent, at phase 5, the derived tree: `stubs/<tool>/
  <version>` in the index, each stub rendered from the union of every
  version read up to that one, with the header naming the platforms that
  read the version; the pointer names the derived tree per tool and
  whole, and records the renderer's identity as the digest over the
  sources of the renderer, the union and the spec model, a fact and not
  a version, since the bench's version is stamped only at a release. A
  tool's stubs are reused with its tree when the renderer's identity
  matches the pointer's and rendered again otherwise. The union resolved
  the record again for every question it asked, 42 s for the 569 stubs
  of the checked-in records; resolved once per union it takes 0.8 s, and
  the whole index builds in 6 s.
- 2026-09-17, the agent, at phase 5, the goldens: two records shaped to
  exercise the renderer live beside the bench's tests with each version's
  stub checked in as `.pyi.golden`, `fm tools.goldens` is the one way
  they move, and the gate compares each render byte for byte.
- 2026-09-17, the agent, at phase 5, the acceptance as met: a byte
  appended to a checked-in golden failed `test_stub_golden.py` naming
  the record, the version and `fm tools.goldens`; the verb moved one
  golden and the test passed; the golden was restored byte for byte.
  The index on the 31 records builds with 569 stubs in 6 s; `fm check`
  exits 0. The wheel item and the folder-source restub stay open above.
- 2026-09-17, Willem, for phase 6, as recommended: a constraint is a
  floor, `name` or `name>=floor`, and the lock takes the newest known
  version that satisfies (open question 3); a receipt records the
  deployment's digest, the current deployment is materialised, and
  `fm env.check` reports the drift (open question 2); the word receipt
  stays and is explained where introduced (open question 7); macOS x64
  stays a host key in the records, and a repository locks for the
  gated three unless its contract names others (open question 4). Open
  question 5 stays open through phase 6.
- 2026-09-17, the agent, at phase 6, the first part: a consumer resolves
  against a catalogue read from the index, never from a record, since
  "offline against the record" for a consumer is the record's
  materialised form; the authoring site reads its records and gets the
  same catalogue, and a deployment's digest is its canonical JSON's
  either way, so the tool's tree gained a `versions` blob carrying the
  order the tree's names cannot. The three sites are the kind record's
  `tools`, now requirement spellings, a package contract's
  `[tools] requires`, and the root contract's, which also carries
  `[tools] index` and `[tools] hosts`. The lock is `tools.lock` at the
  root, JSON like the records, one version per tool with the deployment
  digest per locked host; an entry stands until `fm tools.upgrade` or a
  floor it no longer satisfies. Found live: pyrefly has no record
  (livery#632), so the python kind requires the six checkers and
  runners that have one and the gate runs pyrefly from the venv until
  it does. This repository's contract names `index = "records"`, and
  its lock holds the six at the versions the records resolve on the
  gated three: ty and uv at the one version each has artifacts for.
- 2026-09-16, the agent, at phase 1: the records live at this
  repository's root, `records/<tool>/`, the authoring site the design
  names; the schema beside them is one document with `Tool` and `Delta`
  under `$defs`. The conversion placed each field at the widest layer a
  majority of the cells share, so a root that differs per host sits on
  the host layer with nothing at the tool level; the seven specs
  converted whole, tea with two versions. A converted delta's date is
  empty, since the specs carried none and a guessed date is a wrong
  fact. `Store.ensure` takes the version it installs: a record pins
  nothing, the lock does (phase 6). The spec model went in the same
  change, its module and files with the proof test that converted them.
- 2026-09-16, the agent, at phase 2: the entry points were authored
  from the artifacts themselves, the Windows and Linux archives listed
  member by member; the macOS artifacts could not be landed through the
  store because strongroom's `fetch_url` refuses the 302 every GitHub
  and GitLab release asset answers (livery#623, filed there per contract
  12), so their names follow the Linux members of the same releases, and
  phase 8's six-host verification proves them. One tree digest
  everywhere is made by the engine rather than a strongroom change: after
  extraction every file but a declared entry point loses the executable
  bit, and `collect` marks the entry points, so the mode ladder reads the
  same on every platform. git, a `system-check` kind the store never
  installs, carries no entry points.

## Open

1. **Where the merged record's reader lives.** Proposal above: the format,
   the resolution and the read path in `livery.toolroom.store`, with the
   bench depending on the store, so the graph is
   `bench -> store -> {toolroom, strongroom}` and no distribution is
   added. The cost is that `fm tools.restub` stops being self-contained.
   Owner: Willem.
2. **Whether a receipt records the deployment's digest**, so a correction
   that changes materialisation under a locked project is visible. The
   suggestion on the table is to record it, materialise the current one,
   and have `fm env.check` report the drift, because refusing would break
   every project on every correction. Owner: Willem.
3. **What a constraint says**: a range needing a resolver, or a floor
   with "newest known that satisfies". The repository locks one version
   per tool either way; this decides what the three sites are allowed to
   write and how much resolver the lock needs. Owner: Willem.
4. **Whether macOS x64 stays a supported host key.** The runner exists and
   is free, so this is about whether Intel Mac users can install tools.
   Owner: Willem.
5. **Items 1, 2, 4, 5 and 6 of the superseded note's Open section stand**:
   which checkers leave the lock, the specs' home in a workspace,
   retention of old tool versions, the CI cache against a company mirror,
   and uv's bootstrap. Owner: Willem.
6. **What "an override that shadows nothing" refuses.** The reading taken
   above is two cases: an override naming a host or version the record
   does not carry, and an override restating the value it inherits. If
   only the first was meant, phase 1 drops the second refusal. Owner:
   Willem.
7. **Whether the receipt keeps that name.** It already means the release
    train's annotated tag. Either the word is explained where introduced
    or another is picked. Owner: Willem.

[store-plan]: ../packages/toolroom/notes/20260912-tool-store-over-strongroom.md
[ci-plan]: 20260914-ci-declared-and-dispatched.md
