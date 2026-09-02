# The workshop: engine, release train, and the road past 0.1.0

Status: approved 2026-09-01 after Willem's review; executing.
Phase 1 shipped 2026-09-01 (PR #66); phase 2 shipped 2026-09-01
(PR #67), its audit clean with the ruled deviations and one new
banked quirk (GitHub's method-named auto-merge events). Phase 3 shipped
2026-09-01 (PR #68). Phase 4 in flight: the wave (publish, probe,
tag receipts), the Mined-At movement backstop, the registry reader,
the merge-triggered generated workflows for all three kinds
(release.yml keeps its filename so the PyPI trusted-publisher
binding stands), the release verbs hidden. The release-PR title CI
job is deferred to phase 8's path-filtered governance jobs, where
the emitters grow conditions anyway. Phase 4 shipped 2026-09-01
(PR #69). Phase 5 shipped 2026-09-01 (PR #70); its audit found five
gaps (named-sibling floor scoping, the gate run, and three untested
paths), closed the same day in a follow-up PR that also scoped the
dirty-tree resume carve-out to the workflow branch. Phase 6 shipped
2026-09-01 (PR #72): dev releases, the branch deciding, verified
live against the local Gitea index; its audit found the routing and
three restore/skip paths unproven plus a detached-HEAD hole, closed
the same day in a follow-up PR. Phase 7 cut 2026-09-02 into 7a and
7b with the issue family ruled in full; the isolated-leg toolchain
follow-up shipped 2026-09-02 (PR #75, which also fixed the floor
leg's aimless starvation). Phase 7a shipped 2026-09-02 (PR #76):
the env engine and group, clean, and the hooks with their shim; its
audit found the shadow warning and the show provenance lying under
the cascade hook's own exports, the stop regex proven only by a
fabricated shape, the uv-exact check missing, and the retired
ruff_fix.py still shipped by sync, all closed the same day in a
follow-up PR. Phase 7b shipped 2026-09-02 (PR #78; one CI red, a
zsh-less runner resolving a real binary a test should have stubbed,
fixed as a ride-along commit). `fm submit --force` (leased) landed
between, completing the pre-bash rebase teaching. The 7b audit
found two destruction-rule holes (close tore down before the
--discard check could see the branch; stop could not see a
worktree's uncommitted changes from the main checkout), closed the
same day in a follow-up PR. Phase 8's cut next. Subsumes
`notes/20260831-workshop-plan.md`,
whose phases 1-10 shipped and whose remaining phases are carried
forward here renumbered. The loop: one PR per phase, merged when
green, forced-fault tests first, the hse audit closing each phase,
and any deviation carrying a tradeoff is ruled before it is built.

This plan is judged against `notes/20260830-development-workflows.md`
and the hse compare-and-contrast of 2026-09-01 (decision record).
The stakes it serves: after this, new development environments are
never free-form again, and starting a new idea must cost minutes,
not a day of setup.

## Ground-truth contracts (do not violate)

1. **hse is the first working reference, not sacred.** Before
   building any area hse covers, its implementation is read (not
   recalled) and its shape and guards ported. Improvements can and
   must be made, and each one is named, justified, and ruled in the
   decision record before it is built. A compare-and-contrast
   against hse closes every phase.
2. **Everything through fm.** A needed exception marks a missing
   verb.
3. **Fallbacks before happy paths.** Every phase ships an edge table
   (edge, guard, test) and every guard has a test that forces it. A
   fallback without a forcing test is untested code.
4. **Errors teach.** A refusal names the thing that stopped it, then
   lists every available option and when each applies. Exit codes
   mark deviations from what the verb was asked to do; the prose
   alone carries the meaning, and a parked green outcome exits 0.
5. **Idempotency everywhere.** Re-running any verb is its recovery
   procedure, and doing the same thing twice never makes things
   worse. Humans and agents act on this without checking first.
6. **Human and agent friendly in the same breath.** Completion
   through footman's suggest machinery wherever values enumerate:
   workflow names, package paths, issue numbers, scenario names.
7. **Gitea and private repositories are tier 1.** Nothing simplifies
   based on public GitHub; every forge-touching behaviour is
   exercised against the local containers before it ships.
8. **Rendered contracts belong to the template channel.** The render
   gate judges what the template keeps owning; a package's seeds are
   its authors' and are never rewritten.
9. **Per-package tags are the release identity and its receipt.** A
   tag is cut only after the index confirms the member's wheel, so
   partial success is legible in the tags and recovery walks past
   what is already tagged. Tags are immutable and pushed by name.
10. **No committed monorepo-level artifact serialises releases.** No
    landmark tags, no committed root changelog: a monorepo-wide view
    is derived at docs build time. Two people releasing two
    independent packages at the same time never block each other;
    only intersecting package sets serialise, and updates park
    unarmed while a release flies.
11. **Main never locks.** No required context, protection rule, or
    verb ever blocks feature branches because a release or update is
    in flight. A release absorbs unrelated movement by construction
    (derivation is path-scoped) and reacts to intersecting movement
    by re-deriving; the publish gate makes a stale release
    unshippable, never the developers' problem. See the movement
    analysis below.
12. **Template updates are workspace-atomic.** One template source
    at one version per workspace; there is no per-package template
    update. In the monorepo the source lives at HEAD, so a template
    change and its re-render are one ordinary feature branch; the
    update workflow exists for instances.
13. **Reading is `status`, acting is `ci`.** A read-only command
    never grows a side effect.
14. **One mechanism per act.** Policy verbs layer over shared
    mechanisms (one teardown, one submit flow, one gate), so no two
    implementations of the same act can drift apart.
15. **No dev wheels to PyPI, ever.** Dev releases publish only to a
    configured custom index; without one a dev release is local, the
    wheel built into `dist/` and the publish skipped with the
    teaching. PyPI is never the fallback.
16. **A design fork gets an explicit ruling before it is built.**
17. **Generate over template.** Forge-dependent mechanical content
    (workflow files and their per-forge directories, CODEOWNERS,
    link bases) is emitted by the backends from data, written as
    managed generated artifacts, and gate-compared offline against
    the same pure functions; templates carry only taste surfaces
    and seeds. Minimising the reasons for a template update is an
    explicit goal: template updates are the expensive maintenance
    path, deep-knowledge debugging multiplied by every instance,
    and each migration to generation removes a class of them
    permanently.

## The movement analysis: why main never locks

hse marked main locked while a release or update was in flight
(`check-main-available` failed every other PR). It bought base
stability for a derivation that read the whole repository, and it
cost exactly what Willem remembers: every feature branch blocked,
and the ones that tried anyway tangled auto-merge. This plan removes
the lock instead of managing it, because our derivation is
path-scoped and the hazard analysis comes out differently. Case by
case, with the guard for each:

1. **A feature merges to main touching none of an in-flight
   release's packages.** Harmless by construction: the squash will
   contain it, but the released wheels' content, versions, and
   entries are functions of the set's paths and are unaffected.
   Guard: none needed; a test pins that such a merge changes
   nothing about the release's re-derivation.
2. **A feature merges touching an in-flight release's package.** The
   hazard is real: the squash would publish code the mined entry
   never saw, possibly under-versioned. Guard: derivation is a pure
   function of a ref, so `verify` at the squash ref re-derives every
   member's version and entry and refuses on any disagreement; the
   publish workflow runs verify before anything uploads. The taught
   remedy is re-running `workflow.release`, which re-prepares on the
   moved base (the engine's normal idempotent retry). Bounded like
   the submit self-heal; a package so busy the release keeps losing
   the race gets a taught stop, not a spin.
3. **The release PR's own merge is attempted while stale.** The
   pre-arm classify already probes behind-and-intersecting and
   routes to re-prepare before arming, so the common case is caught
   before CI is even spent; case 2's verify is the backstop, not the
   first line.
4. **An update is in flight while a release flies.** One of the two
   will make the other redo work, and the choice of which is the
   whole rule. Not by cost, an update's re-run is the dearer retest
   (root-owned files widen the gate to everything, where a release
   re-prepare retests its set), but by direction: an update landing
   mid-release always intersects it (floor bumps write into the
   released packages' pyprojects and become unreleased commits in
   the set's paths), forcing a semantic re-prepare that gains
   nothing, while a release landing mid-update makes the update's
   one re-run productive, it picks up the fresh floors. Guard:
   politeness, not a lock; the update prepares, parks unarmed, and
   waits, watching the in-flight releases; when the last completes
   it re-runs itself and arms, one invocation to done. The waiting
   prose: releases named with their authors, "this update will
   finish automatically once they complete; Ctrl-C is safe; run
   `fm workflow.update` any time to continue later". Re-entry is
   the same state machine, so the interrupt costs nothing. A
   non-interactive run waits bounded, then parks at exit 0 with the
   same prose. If someone arms it mid-release anyway, case 2's
   verify still holds the line.
5. **Two releases with intersecting sets.** Two writers of one
   changelog; the only refusal that remains, naming the in-flight
   workflow. Disjoint sets fly concurrently.
6. **Movement between publish and tag.** The receipt ordering
   (contract 9) covers it: tags are cut per member after the index
   confirms, and a re-run walks past what is tagged.

The invariant the analysis preserves: a developer's mental model
needs no "is main locked?" question, because the state does not
exist. A release that loses a race costs the release one re-prepare
round, never the rest of the team anything.

## Options, not opinions

Adoption cannot assume one size fits all (Willem's ruling), so the
decisions that are ours by taste rather than by machinery become
workspace options with sensible defaults, and the ones machinery
depends on are stated as commitments with the reason.

Options in the root `livery.toml`:

- `[qa] isolated_validation = "both-edges" | "latest"` (default
  both-edges); a package may override in its own contract.
- `[qa] coverage_grace` (default 0.5), replacing the hardcoded
  margin.
- `[workflow] commit_types`, the conventional-type vocabulary
  (default the six): submit's title grammar, the branch-prefix
  grammar, and the template-rendered cliff parsers all read this one
  value, so grammar and changelog cannot disagree.
- Changelog shape and bump rules are already options through the
  template channel and layer overrides of `cliff.toml`; that is the
  intended customisation surface and the docs say so.

Commitments, not options, each because machinery stands on it:
squash-only main (title grammar, mining, PR-title-as-subject, and
the ambiguity guard all depend on it); parked green exits 0; tags as
receipts; no dev wheels to PyPI; errors teach; idempotency.

## Phase 1: pre-engine tidy

The three one-way-of-doing-things repairs, landed before the engine
so nothing entrenches.

Deliverables:

- `fm clean` retires; the cache sweep it performed becomes
  `fm caches.clear` (temporary; see the table). The `clean` name is
  reserved for the ported tree-restore verb in phase 7, hse's
  meaning, so the two toolkits never disagree on the word.
- `fm ci.watch` folds into `fm status --watch`; the `ci.` group
  keeps `rerun`, `cancel`, `logs` (logs stays: reading logs through
  fm is our improvement over hse and the ban depends on it).
- One `uv` invocation helper in the workshop; `new.package` runs
  `uv sync` itself instead of printing an instruction.

Acceptance:

- `fm --tree` shows no `clean`, no `ci.watch`; `fm status --watch`
  watches the current branch to a verdict.
- `fm new.package` leaves a workspace where `uv sync` is a no-op.
- `uv run fm check` green.

## Phase 2: the engine

The reserved-branch lifecycle, ported from hse's
`_workflow_engine.py` / `_workflow_state.py` / `_release_decision.py`
with the ruled deviations.

Deliverables:

- `_workflow_state.py`: state detection as a pure function over
  gathered signals. States NONE, PREPARING, IN_PROGRESS,
  AWAITING_REVIEW, FAILED, SUCCEEDED, UNKNOWN; blockers as data
  beside the state. UNKNOWN is first-class: a forge blip never reads
  as "nothing running", and everything that mutates refuses on it.
  Armed is tri-state; an unreadable arm is never reported absent.
- `_workflow_engine.py`: `run_workflow(driver)` with the driver seam
  (`prepare`, `on_merged`, optional `revalidate_base`); the shared
  middle is `submit_flow`, reused, not copied. Identity is the
  branch: `workflow/release/<members sorted>`, `workflow/update/...`.
  Coexistence: a release whose set intersects an in-flight release
  refuses immediately, before prepare, from read-only state, so
  there is nothing to undo. The taught error names the in-flight
  workflow, its author (the forge's user endpoint, never local git
  config), the intersecting packages, and the options: wait, release
  the disjoint remainder, or `fm workflow.abort <name>` when it is
  yours and dead. An UNKNOWN in-flight state still refuses, absence
  cannot be proven from a blip. An update during any release
  prepares and parks unarmed with the note saying what arms it
  later.
- `_release_decision.py`: the decision layer as a pure function with
  the action vocabulary (START, ARM, RETRY, REOPEN, TIDY_THEN_START,
  MERGE_DEFAULT, STOP) and hse's load-bearing ordering. Every STOP
  message lists the options and when each applies (contract 4).
- `workflow.abort [name]`: policy over the same teardown mechanism
  `abandon` uses (contract 14). Bare abort targets the only workflow
  in flight. With several: an interactive run lists them and asks
  which to abort (footman's prompt, each row naming the workflow,
  its author, and its state); a non-interactive run refuses listing
  the names, silence must never pick a teardown target. Shell
  completion on the name offers the active workflow names, read
  live. UNKNOWN refuses without `--force`; `--force` with several
  in flight refuses (force one by name).
- `status --workflow`: the lifecycle renderer; the watch subject is
  pinned so a coexisting workflow's merge cannot exit another's
  watch 0.
- Diagnostics, split along the facts/artifact line: forge gains the
  granular reads the bundle needs and does not have (branch
  protection, reviews, the merge-schedule event timeline), one
  implementation per backend plus the fake, each a protocol verb
  useful to standalone forge users on its own. Workshop owns the
  bundle: the classifier's raw input vector beside the verdict, the
  schema, storage outside the repo, newest 20 kept. Every non-merged
  watch outcome writes one. Each section individually guarded, so a
  token that cannot read one section still yields the rest, and that
  section records its own error, which for a scope-poor token is
  itself the diagnosis. Structural fields only, shareable.
- Repository configuration as a hidden idempotent verb over
  livery.forge Repository.configure, the same one repo birth will
  use. `workflow.abort` ends by re-asserting it in a fresh process,
  hse's guard: an abandoned update that moved required contexts
  forward otherwise leaves protection demanding contexts that never
  report again, deadlocking every future PR.
- Follower hardening: two-poll confirmation before a blocker ends
  the watch (CLOSED excepted), remedy text withheld where re-arming
  cannot help, per-outcome remedies naming the subject's own driver.
- Staleness routing: classify probes whether a release branch's base
  moved in the set's own paths, and the decision layer routes that to
  re-prepare before arming, so the movement analysis's common case
  (3) is caught before CI is spent.
- Ruled deviation from hse: no worktree check anywhere. The branch
  decides everything (main-family means a real release, anything
  else means dev), so the checkout kind carries no information;
  hse's worktree refusal was a proxy for its implicit dev fork, and
  the proxy retires with the branch check stated directly.

Edge table (each row a forcing test; the phase PR extends it):

| Edge | Guard |
| --- | --- |
| Merged PR's head branch auto-deleted | find by head sha fallback |
| Token without issue-read scope 403s on timeline | armed is tri-state; None watches like armed |
| Colleague's release exists only on the remote | branch listing is the union of local and ls-remote |
| Finished leftover is behind base by construction | tidy ordered before the behind check |
| Forge blip during abort | UNKNOWN excluded from teardown-safe |
| Re-run of a live armed workflow without --armed | refuses; a bare submit would disarm it |
| Tidying under coexistence | tidy target named; own live branch out of bounds |
| Stale remote-tracking ref outlives auto-delete | remote existence asked with ls-remote, never the local ref |

Acceptance:

- The decision layer's full action table passes as table-driven
  tests with no I/O.
- Two workflows coexisting on the fake: the release flies, the
  update parks unarmed, `status --workflow` renders both, aborting
  one leaves the other untouched.
- Every edge-table row's test forces its edge and is named in the PR.
- `uv run fm check` green.

## Phase 3: the release driver

Deliverables:

- The backend seam grows `build` (wheel + sdist at a version,
  `SOURCE_DATE_EPOCH` from the commit) and `run_isolated_test`
  (always rebuild; fresh venv; co-released members from
  `--find-links` limited to the set's `dist/`; everything else from
  the repo's `[[tool.uv.index]]` mirrored into the bare venv). The
  isolated validation runs two legs per member: the floor leg
  (`--resolution lowest-direct`, every direct dependency at its
  declared floor, so a floor lying about compatibility fails here
  with the taught remedies: raise it or restore it) and the latest
  leg (default resolution, the world a fresh consumer gets). Both
  ends of the declared range tested; hse ran only the latest.
- The base-verification gate: the default branch's own push CI must
  be green before prepare (branch protection only ever judged PR
  heads). Nudge commit when no run was created; skip-ci detected;
  fail open when the forge is unreachable; `--force-unverified-base`
  overrides.
- `workflow.release [paths...]`: prepare for a set of N >= 1.
  Per-member versions and entries from git-cliff; intra-set floor
  bumps only; per-member unchanged refusal before the branch is cut;
  one commit per member in topological order; PR titled
  `chore(release): released <pkg> vX[, ...]` with the summary body.
  Failure anywhere in prepare restores exactly the files prepare
  wrote (rollback in a finally), so the base is never left dirty.
- `release.prepare` retires as a human verb (hidden CI entries only,
  phase 4); the human vocabulary is one verb.
- Recovery: an existing local or remote workflow branch routes to
  recovery that reads names and versions from the ref, never the
  working tree, and rebuilds nothing.
- `--local`: everything the release would do that stays on this
  machine, nothing that leaves it. On main, the preflight: derive,
  build, isolated-validate, print the would-be release, then roll
  back the stamps like a failed prepare, leaving `dist/` and the
  report. Never asks for confirmation, publishing is what consent
  guards. One mechanism with the real path, stopped at the machine
  boundary.

Edge table:

| Edge | Guard |
| --- | --- |
| Unchanged member in a busy monorepo | detection is path-scoped, content not position |
| Stale wheel of a non-released package in dist/ | find-links limited to the set |
| Prepare fails at member 3 of 4 | rollback restores the base exactly |
| Recovery from a checkout standing elsewhere | every read is ref-scoped |
| Fresh CI checkout has no git identity | identity set only when user.email is unset |
| git-cliff cannot reach a private remote | offline retry after snapshot-restore of the changelog |

Acceptance:

- A two-member set with a dependency between them: floors bumped to
  the co-released version, commits in topological order.
- The isolated leg red on a solo whose in-tree dependency changed:
  the refusal says release the dependency first or the set.
- The isolated report names what each leg resolved per sibling
  ("floor leg: livery-forge 0.1.0; latest leg: 0.3.0"), and a
  forced floor-lie test pins the floor leg: a package using an API
  newer than its declared floor goes red there, naming the floor
  and both remedies.
- Kill prepare between every pair of steps; re-run recovers each
  time (the interruption points enumerated in the tests).
- `uv run fm check` green.

## Phase 4: publish, receipts, recovery

Deliverables:

- The backend seam grows `publish` (`uv publish`, duplicate is the
  one tolerated failure). Default classic publishing with a token; a
  configured custom index wins; trusted publishing is the opt-in
  capability in the GitHub and GitLab CI templates.
- The publish workflow triggers on the release PR merging (head is
  the workflow branch), not on a tag: verify at the squash ref,
  build byte-identically, then walk the dependency wave. Each
  eligible member runs publish, index probe
  (`Registry.versions` until the version appears, bounded), tag,
  push-by-name, concurrently with its independent siblings; a
  dependent starts only when its in-set dependencies are tagged.
  A failed member stops only its dependents.
- `workflow.release.publish --ref` (hidden): the recovery entry;
  every discovery read scoped to the ref. `verify` and `templates`
  move under `workflow.release` hidden.
- `verify` re-derives at the squash ref: every member's version and
  entry are re-mined at the commit being published and any
  disagreement with the stamped values refuses, naming the commits
  that moved under the release and the remedy (re-run
  `workflow.release`). This is the movement analysis's backstop
  (case 2): the check that lets main never lock.
- `release.verify` gains the index probe: every internal floor must
  resolve against the registry with `--no-sources` from a scratch
  directory, so a floor bump that would strand consumers fails
  before publish.
- The release-PR title job in CI: title matches each member's top
  changelog heading; not a required context.
- The workflow emitters detect LFS in `.gitattributes` and emit the
  checkout flag each forge needs; the dev compose enables LFS on the
  local Gitea so tier-1 testing covers it.
- The publish workflow is born generated (contract 17): the
  backends emit it from data, no jinja per-forge conditionals. The
  existing gate workflows migrate out of the template in the same
  phase, while instances are few: the per-forge directory split and
  each forge's mechanical quirks move into the emitters, and the
  template's CI mass shrinks to nothing.
- Forge release pages per member; a missing token skips them, the
  tag is the source of truth.

Edge table:

| Edge | Guard |
| --- | --- |
| Squash listing is alphabetical, not topological | discovery re-sorts by workspace rank |
| HEAD moved past the squash before recovery | --ref scoping on every read |
| --tags would push private and abandoned tags | tags pushed by name only |
| Upload accepted but index lag | receipt is the probe, not the exit code |
| An intersecting feature landed after prepare | verify's re-derivation refuses; re-prepare is the remedy |
| Member 3 of 4 fails mid-wave | siblings complete and keep tags; re-run skips the tagged |
| Two independent members | publishes observed overlapping (the wave, not a serial walk) |
| Diamond set | the apex waits for both legs' tags |

Acceptance:

- The wave shapes above, forced on the fake with an index seam.
- A full release round-trip on the local Gitea and GitLab
  containers, private repositories, followed by one release-legs
  dispatch before the next forge release.
- `uv run fm check` green.

## Phase 5: updates on the engine

Deliverables:

- The update family is two verbs side by side under one group, and
  a default that does both: `workflow.update.templates` (one
  workspace-atomic act, contract 12: the project render and every
  package's managed files move to the installed workshop's template
  version together) and `workflow.update.dependencies [names...]`,
  which owns both worlds a dependency can live in. External
  dependencies move through the lock (bare: `uv lock --upgrade`;
  named: `--upgrade-package` per name, completing from the lock).
  Workspace siblings never come from an index, they resolve from
  source, so the lock cannot move them: their movement is the floor
  text and the `[[depends]]` edge, and this verb raises them to the
  latest released versions (bare: all; named sibling: that floor).
  Then sync, `check --fix`, one conventional commit. Naming scopes
  which dependencies move, never where: one workspace lock is one
  resolution, so movement is workspace-wide by construction, the
  monorepo's promise, and the root-owned lock correctly widens the
  affected gate to everything.
  Bare `fm workflow.update` runs both. hse's per-package
  update verbs answered separate template repositories; with one
  source at one version they have nothing to mean. In the monorepo
  the templates verb reduces to floors and environment (the source
  is HEAD; template edits are ordinary feature branches).
- Resume: committed-but-unsubmitted work is detected and submitted;
  all-generated commits auto-submit, human commits stop listing the
  options.
- The post-template-update submit re-executes in a fresh
  interpreter, because the update may have rewritten the running
  toolchain (relates to footman#530).
- A dirty tree refuses resume from another branch; a mid-conflict
  resume on the branch itself proceeds.

Edge table:

| Edge | Guard |
| --- | --- |
| Update branch carries a foreign commit | resume refuses, listing the subjects |
| Killed between commit and submit | resume submits, the work is not redone |
| A release outlasts the bounded wait | parks at exit 0 with the teaching |
| The update moved the running workshop | fresh-interpreter resubmit, loop-guarded |
| Dirty checkout elsewhere while an update PREPARES | resume carve-out scoped to the workflow branch |
| A named dependency is a workspace sibling | its floor moves; the lock is never asked |
| The gate is red on the update's changes | stop before the commit, resume taught |

Acceptance:

- An update prepared during a live release parks, waits watching
  the release, and on its completion re-runs itself (fresh floors
  observed in the diff) and arms to merged, in one invocation; the
  waiting prose names the releases and the safe interrupt.
- The interrupt path forced: kill the wait, re-run
  `fm workflow.update`, it resumes from parked and completes.
- A non-interactive update during a release parks at exit 0 with
  the same prose after its bounded wait.
- Kill between commit and submit; re-run submits without redoing the
  work.
- `uv run fm check` green.

## Phase 6: dev releases

Deliverables:

- The branch decides (Willem's ruling): `workflow.release` on a
  main-family branch is the real act; on any other branch it is the
  dev act, wheel straight from the branch, no reserved branch, no
  PR, no tags. A dev release always asks for confirmation; a
  headless run needs the explicit `--yes`, footman's confirm answers
  its default no off a terminal, so silence never publishes.
  Version `{next}-dev.{branch}.{distance}+{sha}.{date}` (`.dirty`
  appended), age readable offline from a lock file.
- The unchanged refusal (content, not position): a dev release of a
  package whose bump equals its released version refuses with the
  taught alternatives.
- Publishing needs the configured custom index. Without one the dev
  release is local: the wheel still builds into `dist/`, and the
  publish step is skipped with the teaching that names where a
  custom index is configured and says it ran as `--local`
  (contract 15). PyPI is never the fallback.
- `--local` on a feature branch: the dev build without the publish
  even when an index is configured, dev-versioned wheels into
  `dist/` for a consumer checkout to `--find-links`. No
  confirmation, nothing leaves the machine.
- The changelog is the unreleased excerpt spliced into the README
  under "What's New" for the build only and restored after; the
  index page is the only place it exists.

Edge table:

| Edge | Guard |
| --- | --- |
| No custom index configured | local degrade with the teaching, exit 0 |
| pyproject stamped ahead of the tag | the refusal judges the tag; the dev act proceeds |
| Headless publish without `--yes` | refusal teaches the flag; silence never publishes |
| Dirty tree | `.dirty` rides the version; restore is snapshot-based, never git |
| `--local` with an index configured | builds only, never asks, nothing leaves |

Acceptance:

- The refusals and degradations first: no custom index builds
  locally and says so, unchanged package refuses, headless without
  `--yes` refuses, each message listing the options.
- A dev wheel on the local Gitea index whose page shows the excerpt;
  the working tree byte-identical after the build.
- `uv run fm check` green.

## Follow-up before phase 7: the isolated leg's toolchain

From the phase 4 review (side conversation, 2026-09-02). Lands as
its own small PR before phase 7a.

- The isolated venv installs the gate's own toolchain at locked
  pins: `uv export --format requirements-txt --only-group dev
  --no-emit-project --no-hashes` into the venv via
  `uv pip install -r`, replacing the bare unpinned pytest install.
  When a package grows a `[dependency-groups] test`,
  `uv export --package <member>` serves it the same way. Rejected:
  retargeting `uv sync` at the venv, whose exact-by-default install
  would remove the floor-installed wheel.
- The floor-honesty probe: after both installs the leg re-reads the
  package's direct dependencies from the venv and fails taught when
  any sits above its floor, because a pip-style second install can
  silently move a floored package up to a locked pin.
- Dev tools never enter `[project]` dependencies. Root dev-group
  floors stay as documentation of the oldest used features; a
  per-package test group carries floors under the same rule (a
  floor is a compatibility claim you are prepared to test), and the
  floor leg starves it too.
- The starvation aimed right: to the resolver a wheel file's own
  dependencies are transitive, so `--resolution=lowest-direct` on
  the wheel alone starved nothing (found by this follow-up's own
  forcing test). The leg lists the package's declared dependencies
  explicitly beside the wheel, making them direct.
- The base gate's timeout tells not-started from hanging: a tip
  that reported a run and never went green sends the reader to the
  runners; a tip with zero contexts throughout names run creation
  and the fresh-push-event remedy.

Acceptance:

- The deliberate-overlap forcing test first: a floored direct
  dependency the toolchain pins higher; the starved install
  resolves the floor, the pins lift it, the probe refuses naming
  the movement.
- `_dev_pins` exports the monorepo lock's pytest pin; a bare rig
  answers None and the leg falls back to bare pytest.
- Both timeout messages forced on the fake: zero contexts
  throughout, and a run that hangs.
- `uv run fm check` green.

## Phase 7: the environment (was phase 11)

Cut 2026-09-02 (decision record). Two PRs: 7a the entered
environment, 7b the entered shell and the issue family. Each
gate-green and mergeable alone; 7b consumes 7a's `env.emit`.

### Phase 7a: the entered environment

Deliverables:

- The env-file engine (from hse's `envfile.py`): parse, substitute,
  cascade, write. Precedence, highest first: the process
  environment always wins; `.repo.env.local` nearest to farthest;
  `$LIVERY_HOME/.repo.shared.env`; committed `.repo.env` nearest to
  farthest. hse's file names verbatim. The shared file's location
  may itself be `${...}`-substituted, so the load is two-pass.
  Substitution iterates to a fixed point with a 16-pass bound; a
  value that does not settle keeps its raw text. Double quotes
  substitute, single quotes are literal, unquoted values lose
  inline comments.
- The cascade applies to every `fm` run (a pre-tasks hook), so a
  cold shell's `fm` still sees the repo's environment; environment
  wins over every file.
- The `env` group, no availability gates (it repairs cold stores):
  - `env.emit [posix|pwsh] [--agent] [--github]`: dialect emission
    with the quoting rules; a value or name that cannot be quoted
    safely (line breaks, invalid names) is refused. One PATH
    prepend line. Bare adds the interactive completion hook.
    `--agent` selects by file membership, secrets included
    (availability is the point), the PATH family excluded (it is
    composed per shell); the structural ``VIRTUAL_ENV`` rides. `--github` writes `GITHUB_ENV`/`GITHUB_PATH`
    only under `GITHUB_ACTIONS`, secret names filtered.
  - `env.show [--full]`: the sources preamble, secret-suffix
    masking, the PATH breakdown, stale rows flagged.
  - `env.set KEY [--value] [--scope=local|shared|repo]`: no value
    deletes behind a confirm; a set warns when a higher-precedence
    source shadows it, naming the winner; the restart notice.
  - `env.check`: the home set, every tool of the derived profile
    resolves and matches. The profile derives from the present
    package types (ruled 2026-09-01), pure python today: uv exact
    from the lock, the venv tools by presence. Red prints the PATH
    breakdown and the remedy naming `fm sync`.
  - Not ported: hse's env schema variable, legacy renames, the
    compiled/node tool lists, copier-cache warnings.
- Hooks `post-edit` (ruff check --fix and format on the edited
  Python file; best-effort, always exit 0: a formatter problem must
  never block an edit) and `stop` (blocks ending a turn whose last
  Bash result is a red fm verdict; a repeated stop passes) join
  `pre-bash`. The shim propagates only the hook's own exit 2: a
  guard that cannot run must not deny.
- `clean [--all]`: plan from a pure read, render the plan, confirm,
  `git checkout -- .` plus per-path `git clean`. `*.env.local` is
  protected at any depth, including inside untracked directories
  (never collapsed into their parent). Pathspecs are POSIX; the
  removal's `-x` mirrors the plan's scope.

Edge table:

| Edge | Guard |
| --- | --- |
| Substitution that never settles | 16-pass bound; raw text kept |
| Secret inside an untracked directory | directory enumerated, the secret kept |
| Non-English locale, non-ASCII paths | `ls-files -z` and POSIX pathspecs, never parsed prose |
| Hook infrastructure failure | exit 0; only the hook's own exit 2 blocks |
| Value with a line break | emit and set refuse; it cannot be stored or quoted |
| Cold checkout | the env group carries no availability gates |
| Hook's own refusal vs infrastructure failure | the shim propagates only exit 2 |

Acceptance:

- The forced fallbacks first: each edge-table row's test.
- A bare shell evaluating `fm env.emit posix` yields a working
  `fm` (`env.check` green in it).
- `uv run fm check` green.

### Phase 7b: the entered shell and the issue family

Deliverables:

- `shell` and hidden `shell.prepare`: bash, zsh, pwsh; cmd is
  refused naming pwsh (it has no startup file to inject, so a
  launched cmd only inherits a cold caller and looks entered
  without being entered). Per-dialect rc injection (bash
  `--rcfile`, zsh a throwaway `ZDOTDIR` chaining to the user's,
  pwsh `-NoExit`); the entered shell evaluates
  `fm -C <root> --quiet env.emit <dialect>` at its own startup;
  stderr stays live so a cold-store refusal teaches. POSIX execs;
  Windows runs and forwards the exit code. `shell.prepare` answers
  argv as JSON, writing the rc files is its job, and a plan that
  needs environment variables is refused rather than half-worked.
- forge: assignment becomes add and self-remove. `Issues.assign`
  adds an assignee; `Issues.unassign(number)` removes the
  authenticated user; per backend plus the fake, with a recorded
  conformance scenario.
- The assignee limit is workspace policy: a config value, default
  1, honoured by the workshop on every forge including ones whose
  native limit is higher. At the limit `issue.start` warns naming
  the holders and continues: assignment documents who is working,
  it never gates the work. `configure` validating the limit against
  the forge (free GitLab caps at one) lands with phase 8's
  governance configure.
- The `issue` group; bare `fm issue` lists open issues:
  - `issue.create <title> [--type]`: file an issue.
  - `issue.list`, `issue.search <text>`: reads, never a side
    effect.
  - `issue.start <issue>`: number or quoted title (title creates
    first). Assignment is documentation, never a gate: at the limit
    (or on a refused write) the start proceeds with a warning that
    others will not see you on the issue, so communicate; branch
    `<kind>/<number>-<slug>` always from a fetched
    `origin/<base>`. A worktree is the default, named
    `<number>-<slug>` under the runner's home
    (`worktrees/<repo>/`; footman will expose the home, until then
    `$LIVERY_HOME`). `--no-worktree` reuses the checkout, where a
    dirty tree refuses naming `--wip` (park as a commit) as the
    escape. `--agent[=name]` hands the issue to a coding agent in
    its worktree and takes an initial prompt to append to the
    minimal briefing; the worktree's CLAUDE.md stays the real
    instructions. `--open=code|shell|none`; an editor opens
    unasked only when interactive.
  - `issue.stop <issue>`: stop working. Unassign self while the
    issue is open; remove the local branch, and the worktree when
    the work started in one (switch to the base first). The remote
    branch is never touched: it stays the pool's copy. One
    destruction rule: work that exists nowhere else is never
    destroyed without `--discard`, with the prose tailored to the
    situation (delta missing from the remote; the remote gone
    while the issue is open, which is exceptional; the issue
    already closed, naming the closure).
  - `issue.close <issue> --reason [--message]`: close the issue
    itself. The first act is the shared teardown's disarm; when
    that finds the PR already merged, the supplied reason no
    longer applies and close degrades to stop-style cleanup,
    "issue already resolved, nothing left to do". Otherwise the
    local and remote branch are removed by default
    (`--keep-branch` retains them) and the removed head sha is
    recorded in the close message. Reuses the one teardown
    mechanism (contract 14).
- Issue-number completion on start/stop/close from the open
  issues, never raising: offline costs the suggestions, not the
  command.
- The CLAUDE.livery worktree line is updated in this PR: issue
  worktrees live under the runner's home; `.claude/worktrees/`
  remains the ad-hoc agent-session convention.

Edge table:

| Edge | Guard |
| --- | --- |
| Issue at the assignee limit | start warns naming the holders, and proceeds |
| Local-only work under stop or close | `--discard` required, taught per situation |
| Remote branch gone while the issue is open | refusal explains the exceptional state |
| close races a merging PR | disarm first; merged degrades to "already resolved" |
| Title that looks like a number | `#`-stripped digits are a number, never a junk title |
| Completion offline | suggestions empty, command intact |

Acceptance:

- The forced refusals first: each edge-table row's test.
- On the fake: the start-at-limit warning, stop with an unpushed delta
  refusing then passing with `--discard`, close recording the sha
  and the merged-PR race degrading cleanly.
- A live conformance recording of assign/unassign on all three
  servers.
- `uv run fm check` green.

## Template composition: the two axes (design, deferred build)

Stated now because `render()`, the answers shape, and the gate's
comparison must not entrench against it; built when each axis gains
its first consumer.

- **Vertical, layer overlays.** The template source becomes a stack
  mirroring the workspace's layers list: the workshop's base
  template, then each layer's overlay, rendered bottom-to-top into
  one destination, so an upper layer adds files to any kind or
  replaces a base file wholesale. The same precedence rule the
  content layers already follow, applied to templates. First
  consumer: the hse brand layer (with phase 8), overriding
  `cliff.toml.jinja` and adding brand files without forking the
  base.
- **Horizontal, kind hierarchy.** A kind declares a parent:
  `package-python-maturin` extends `package-python`, rendering the
  parent then the child over it with the same answers; the child
  adds its build files and overrides the build-backend lines. The
  answers file records the leaf kind; `PACKAGE_MANAGED` is the
  union along the chain. This mirrors the backend seam, where the
  extension backend overrides `build` and `publish` and inherits the
  rest: one tree, two views. Validated by two concrete kinds soon
  after 0.1.0 (phase 10): a nanobind binary-extension package (child
  case: extends `package-python`, overrides the build backend, its
  compiled wheel also proves the isolated leg on native code) and a
  CMake Maya-plugin package (root case: a second package type end to
  end through the backend seam, where the Python gate legs must skip
  honestly rather than pass vacuously).
- The render gate compares against the composed render in both
  axes; drift detection is unchanged.
- **Types contribute tool profiles.** The workspace's required tool
  set is the union of what its present package types (and layers)
  declare, derived by discovery like everything else: no package of
  a type, no provisioning of its tools. A pure-python workspace
  never sees cmake; the first Maya-plugin package pulls cmake and
  the devkit in by existing, and removing the last one shrinks the
  profile again. `sync` provisions the derived profile, `env.check`
  verifies exactly it, and the tool cache stores only what some
  type demands. The type is one declaration surface answering three
  questions: how to build (backend), what to render (template
  kind), what the machine needs (tools).

## Phase 8: repository governance

Deliverables:

- The package contract declares owners: each package's `livery.toml`
  names the users or teams that review it. Completion on the
  declarations reads the forge live; the gate validates only what is
  offline-checkable (declarations parse, paths exist), because the
  merge path never waits on anything outside the repository;
  existence of the declared owners is the configure verb's check,
  made where the forge is already being spoken to, and `doctor`
  reports it.
- forge gains the listings the declarations need and does not have:
  `members(owner)` and `teams(owner)`, per backend plus the fake,
  with a recorded conformance scenario. GitLab's teams are its
  groups and subgroups and its backend answers with group paths; a
  personal namespace has none and says so.
- The CODEOWNERS dialect lives in forge, like the address family:
  `codeowners(entries)` per backend, pure string building from the
  neutral declarations (path, owners, minimum approvals) to the
  forge's canonical file location and syntax. GitLab's sections
  express per-package approval counts in the file itself; GitHub
  and Gitea approximate counts through protection, and each backend
  states what it expressed and what it approximated. Workshop
  drives it: collect the package declarations, call the backend,
  write the file as a managed generated artifact, and the gate
  compares the committed file against the same pure function,
  offline. The template carries no per-forge conditionals for it.
- `RepoConfig` gains minimum approved reviewers and
  require-codeowner-review; each backend asserts what its forge
  can enforce and declines the rest by name through the capability
  vocabulary (GitLab ties required approval rules to paid tiers;
  verified against the real servers, not assumed). The addresses
  and conformance suites grow matching scenarios.
- Who can govern: the workshop has no permission system of its own,
  it consumes the ones beneath it (the forge's model, and the
  capability vocabulary, which is a permission surface stated
  honestly); an ecosystem package may well own one, that is what
  capabilities are. An
  admin is whoever both owns the declarations (codeowners on the
  root contract) and holds a token the forge accepts for applying
  them; livery's whole contribution is translating `livery.toml`
  into each forge's settings, removing the forge-specific
  knowledge. A refused configure teaches what grant it needed, and
  `doctor` reports whether the current token could configure.
  Changing settings: config-as-code guarded by itself, the root
  contract and governance declarations carry their own owners in
  CODEOWNERS, so raising a reviewer count is a reviewed merge like
  any change. Tokens split for least privilege, per kind
  (`GITEA_ADMIN_TOKEN` beside `GITEA_TOKEN`): everyday verbs never
  read the admin variable; admin verbs (configure, the post-abort
  reconcile) resolve admin-first with fallback to the everyday
  token, then refuse taught; CI mounts the admin secret only on the
  post-merge configure job; a non-admin's abort completes its
  teardown and skips the reconcile with the teaching. No protocol
  change, the forge lane binds a second Forge when the verb class
  needs it.
- Governance applies itself on merge (Willem: people update the
  reviewer count just by merging a new livery.toml into main). A
  post-merge configure job on main, spawned only when the governance
  paths changed, the emitters generate forge-native path filters
  (`on.push.paths`, `rules: changes:`), so ordinary merges never
  spawn it. It runs `fm workflow.configure` with the CI-mounted
  admin secret; a failed apply is a visible red job on main. The
  full loop: edit the declaration, its own codeowners review it,
  merge, CI's admin identity applies. The personal admin token
  becomes the out-of-band repair tool only.
- The post-abort reconcile is silent when provably unneeded: an
  offline diff of the config-implying paths (from the PR head sha,
  which persists after branch deletion) skips everything untouched
  with certainty; touched paths read-compare protection against the
  contract where the token can (GitHub gates the read on admin);
  only a refused write mentions the admin token, and an unreadable
  state gets the softened conditional note, never an asserted
  problem.
- The required-context rename transition, the one order-sensitive
  exception: the renaming PR produces the new context while
  protection still demands the old, so it can never go green and the
  post-merge apply never runs. The refusal belongs to submit, not
  the gate: the gate is offline by contract and CI's ambient token
  cannot even read protection, while submit already talks to the
  forge and owns --fix as heal-then-proceed. Submit detects the
  rename offline (the branch diff moves `[ci] required_context` or
  the generated job names; protection matching main's contract is
  guaranteed by the apply job above), refuses teaching that
  protection must move from this branch BEFORE the merge, and
  `submit --fix` with an admin token in reach applies it and
  proceeds, read-compare making re-runs quietly green; without a
  token the refusal names `fm workflow.configure` and who can run
  it. The honest cost stated in the prose: between apply and this
  PR's merge, other armed merges park (never fail) on the old
  context, so the teaching recommends `fm submit --fix --armed` in
  both the heal and the refusal paths, closing the window at CI
  speed, and a minimal rename diff. --fix never implies --armed.
- The approvals-outstanding outcome is not an error (Willem's
  ruling, the parked-green rule extended): armed and green with
  approvals missing stops the watch at exit 0, saying how many
  approvals are still needed, who can give them (owners of the
  touched paths from the package declarations, minus the author,
  minus those who already approved via the reviews read), and that
  the arm survives, so the last approval merges it with nothing to
  re-run. hse's exit 18 error is the ruled deviation. Forcing tests
  against a protection-enabled repo.

Acceptance detailed when the phase is cut; timing relative to 0.1.0
is open item 6.

## Phase 9: the branded runner (was phase 12)

The option to replace `fm` in workflows and docs with a branded
name, footman's `App` branding and `dist=` handoff. Leans directly
on contract 17, and needs no configuration at all: the emitters ask
footman which App is running, so `hse create.repo` generates
workflows that call `hse` and `fm create.repo` generates ones that
call `fm`, the name you used is the name you get. Rebranding an
instance is running the update under the branded CLI, the managed
generated files re-emit. Verify at build time that footman exposes
the running App's prog to plugin code; a one-line footman ask if
not. Gates 0.1.0 together with phases 1-8.

## After 0.1.0

- Phase 10: proving the composition design. The kind hierarchy built,
  with the two validators: `package-python-nanobind` (extends
  `package-python`) and the CMake Maya-plugin kind (a new root kind
  and a new backend). The Maya kind's distribution story is designed
  when the phase is cut (open item 5). One guard changes shape here:
  publish's rebuild-and-compare holds for pure-Python wheels (locked
  backend plus `SOURCE_DATE_EPOCH` is byte-identical) but not for
  compiled artifacts, so native kinds hand the validated wheels
  forward, as CI artifacts or by recorded hashes publish verifies,
  and "what ships is what was validated" holds by identity instead
  of reproduction.
- Phase 11: shared tool cache (awaits toolroom's move and strongroom
  progress); provisions the type-derived tool profile, never a
  workspace-global list (the composition section's third axis). A
  workspace declaring LFS in `.gitattributes` contributes `git-lfs`
  to the profile by existing, and `doctor` names it when missing.
- Phase 12: docs toolchain, including the derived monorepo-wide
  release view built at docs time from tags and per-package
  changelogs (the committed root changelog that would have been a
  choke point, reproduced where it costs nothing).
- Phase 13: sparse checkouts as partial workspaces. A sparse
  checkout is a smaller workspace, absent siblings are consumed as
  released wheels through their floors (the solo-release
  consumption story reapplied); workspace membership is generated
  from discovery (contract 17), so uv syncs what is present. The
  minimum set: root files and governance declarations always, each
  chosen package wholly, nothing of the absent. `graph.affected`
  picks the chosen set; the layering lint validates edges into
  absent members against floors. CI stays full-checkout. The tool
  profile shrinks automatically: it is the union over present
  types, and a sparse checkout has fewer present, so excluding the
  Maya package means never provisioning cmake on that machine, two
  discovery rules composing rather than a feature. The persona this
  serves: a Python developer whose checkout consumes the native
  siblings as prebuilt released wheels, no toolchain, no compile,
  `uv sync` downloads a binary; and symmetrically the native
  developer carrying none of the rest. Two verbs carry it: outside
  any project, `fm clone <url>` on the builtin surface does a
  blobless partial clone (`--filter=blob:none`, full commit and
  tree history so affected and derivation keep working, never a
  depth-shallow clone, which breaks both), discovers the packages,
  and offers a TUI multi-select with dependency closures resolved
  as you pick, then sets the cones, generates membership, syncs,
  and derives the tool profile, one command from URL to the
  lightest working checkout. Inside the project, the set grows and
  shrinks by name (completing against discovery) or through the
  same TUI bare, each change regenerating membership, re-syncing,
  and re-deriving the profile, idempotently. Shrinking guards like
  `abandon`: uncommitted changes refuse naming the files; unpushed
  commits touching a dropped package warn by name and ask (headless
  without `--yes` refuses), the commits survive in history but
  vanish from view, which is how work gets forgotten. Growing gates
  nothing.

## Temporary, replaced by

| Temporary | Replaced by |
| --- | --- |
| `fm caches.clear` (phase 1) | the ported `clean --all` sweep (phase 7) |
| `release.yml` tag trigger | the merge-triggered publish workflow (phase 4) |
| `release.prepare` as a human verb | `workflow.release` (phases 3-4) |
| `notes/20260831-workshop-plan.md` phases 11-14 | phases 7-10 here |

## Decision record

- 2026-09-01: this plan subsumes `notes/20260831-workshop-plan.md`
  (Willem: "we've outgrown our previous plan"). The old note keeps
  its history and points here.
- 2026-09-01, the reference stance (Willem): hse is the first
  reference implementation that worked, not sacred; improvements can
  and have to be made, each named and ruled. Ruled so far: no
  landmark tags; partial success legible in per-member tag receipts;
  two independent releases by two people never block each other; no
  committed root changelog (a choke point), the monorepo view
  derived at docs time instead.
- 2026-09-01, the DNA (Willem): taught errors everywhere that list
  every option and when each applies; human and agent friendly;
  completion where values enumerate; idempotency wherever possible
  so doing the same thing twice never makes things worse.
- 2026-09-01, tags are receipts (Willem's question exposed the
  hole): the shipped train's tag push triggered publishing, so a tag
  marked "requested", not "done". The engine publishes on the
  release PR's merge and cuts each member's tag only after the index
  confirms its wheel; trusted publishing re-binds to the
  merge-triggered workflow.
- 2026-09-01, the publish wave: eligibility by in-set dependency
  tags; independent members publish concurrently; a failure stops
  only its dependents.
- 2026-09-01, the freeze is arm-suppression (from the hse survey):
  updates during a release prepare and park unarmed rather than
  refuse; only intersecting release sets refuse.
- 2026-09-01, ruled deviations from hse in the engine: no worktree
  refusal on `workflow.release` (dev releases are explicit, agents
  live in worktrees).
- 2026-09-01, testing is the majority of the work (Willem confirmed
  the framing): each phase carries an edge table and forced-fault
  tests dominate the acceptance lists; the hse survey's guard
  inventory seeds the matrices.
- 2026-09-01, main never locks (Willem asked for the analysis, and
  for a better way if one existed): hse's lock compensated for
  whole-repository derivation; ours is path-scoped, so the lock is
  replaced by re-derivation agreement. Unrelated movement is
  harmless by construction, intersecting movement makes the release
  re-prepare, and verify's re-derivation at the squash ref makes a
  stale release unshippable. Feature branches always land; hse's
  blocked-main auto-merge tangles cannot occur because nothing
  refuses them.
- 2026-09-01, template updates are workspace-atomic (Willem): hse's
  per-package update verbs answered separate template repositories;
  with one source at one version they have nothing to mean, so the
  update quartet collapses into `workflow.update.templates` beside
  `workflow.update.dependencies`, with bare `workflow.update` doing
  both (Willem's ruling). In the monorepo, template edits are
  ordinary feature branches because the source lives at HEAD.
- 2026-09-01, dependency updates target but never localise (Willem):
  `workflow.update.dependencies` takes optional names
  (`uv lock --upgrade-package`), completing from the lock; movement
  is always workspace-wide because one lock is one resolution.
- 2026-09-01, the parking rule explained by direction, not cost
  (Willem saw the symmetry, then corrected the cost claim: an
  update re-run is the full retest, a release re-prepare is
  scoped): both workflows re-prepare on intersecting movement;
  parking the update routes the redo to where it is productive, a
  post-release re-run raises floors. Fixed default, not an option.
- 2026-09-01, governing the governors (Willem, clarified twice: the
  workshop, specifically, builds no permission system; ecosystem
  packages may, that is what capabilities are): an admin is whoever
  owns the
  declarations and holds a token the forge accepts; livery only
  translates the contract into forge settings. The per-kind admin
  token beside the everyday one is optional least-privilege
  plumbing with a fallback ladder, not a role model; everyday verbs
  never touch the admin variable. The two personas it serves,
  confirmed: the security-conscious admin exports the admin token
  only for the minutes a configure needs it; the lazy admin carries
  the permissions in the everyday token and never learns the second
  variable exists. Neither configures anything to be who they are.
- 2026-09-01, LFS and sparse checkouts (Willem, exploratory): LFS
  needs no structural change, three touches (emitters' checkout
  flag, dev-compose enablement, a tool-profile contribution);
  sparse checkouts are pursued as partial workspaces with absent
  siblings resolved from the index through floors, membership
  generated from discovery, never as metadata skeletons, which uv's
  editable installs cannot satisfy. Scheduled as phase 13.
- 2026-09-01, the sparse verbs (Willem): an initial-checkout TUI on
  the builtin surface (`fm clone`, blobless partial clone, never
  depth-shallow) and an in-project grow/shrink verb; smallest
  possible means no blobs outside the cone, with history metadata
  kept for the machinery.
- 2026-09-01, branding rides generation and needs no config
  (Willem): the emitters take the runner name from the running
  App's own identity, `hse` begets `hse` and `fm` begets `fm`;
  rebranding is re-running generation under the other name.
- 2026-09-01, generate over template is a contract (Willem: the
  more forge-dependent things generated rather than shipped in
  templates the better; template updates are the expensive,
  deep-knowledge maintenance path and minimising their causes is an
  explicit goal): contract 17, with the mechanical/taste dividing
  line (cliff.toml's Tera body stays a template surface, its
  mechanical parts are candidates), and the CI workflows migrate to
  generation in phase 4 while instances are few.
- 2026-09-01, the CODEOWNERS dialect is forge's (Willem: like the
  URLs): a pure per-backend `codeowners(entries)` owns location,
  syntax, and expressiveness (GitLab sections carry counts, GitHub
  and Gitea approximate via protection, stated honestly); workshop
  generates and the gate compares offline against the same
  function.
- 2026-09-01, owner listings join the protocol (Willem asked
  whether validation and completion had a source): `members(owner)`
  and `teams(owner)` on forge, capability-honest per backend;
  owner-existence validation belongs to configure and doctor, never
  the gate, the merge path stays offline.
- 2026-09-01, the parked update finishes itself (Willem): it waits
  watching the in-flight releases and auto-continues, re-run then
  arm, when the last completes; Ctrl-C is safe and re-entry
  resumes; non-interactive waits bounded then parks at exit 0.
- 2026-09-01, options not opinions (Willem: adoption cannot assume
  one size fits all): taste-decisions become workspace options with
  sensible defaults (isolated-validation legs, coverage grace, the
  commit-type vocabulary), machinery-decisions are documented
  commitments (squash-only main above all). The "Options, not
  opinions" section is the registry.
- 2026-09-01, the isolated validation runs both ends (Willem: the
  floor AND the latest released): `--resolution lowest-direct` for
  the floor leg beside the default latest leg, per member, per
  release. Floors become tested facts; lowest-direct rather than
  full lowest keeps third-party transitive noise out. hse's
  single-leg run is the ruled deviation.
- 2026-09-01, sibling floors are deliberate (Willem asked whether a
  release should auto-raise them): a solo release ships the
  committed floor, verify insists only that it names a released
  version, the isolated leg prints what it actually validated
  against, and raising the floor is `workflow.update.dependencies`'
  explicit move, which also owns sibling floors as the in-repo
  analogue of lock upgrades, since `uv lock --upgrade` cannot touch
  source-resolved workspace members.
- 2026-09-01, abort picks interactively (Willem): with several
  workflows in flight an interactive `workflow.abort` lists them and
  asks; non-interactive still refuses by name, and completion offers
  the live workflow names.
- 2026-09-01, `--local` on release (Willem asked its value): one
  meaning on both branch modes, everything that stays on the
  machine and nothing that leaves it; the main-branch preflight
  with rollback, the feature-branch build-only, never a
  confirmation, and the no-index dev degradation names it.
- 2026-09-01, the dev fork is the branch's (Willem): branch decides
  real versus dev, checkout kind is irrelevant and never consulted,
  a dev release always confirms (headless needs `--yes`), and with
  no custom index a dev release is local-only, built into `dist/`
  with the publish skipped. Supersedes the earlier explicit
  `--dev-name` ruling.
- 2026-09-01, approvals outstanding is a clean stop (Willem): armed
  and green with reviews missing exits 0, listing the eligible
  reviewers and the count still needed; the arm survives. Replaces
  hse's exit-18 error.
- 2026-09-01, governance per package (Willem): minimum approved
  reviewers and per-package codeowners, with as much forge support
  as each backend honestly has; owners declared in the package
  contract, CODEOWNERS rendered from their union, enforcement
  asserted through configure and declined by name where a forge or
  tier cannot (GitLab's paid approval rules). configure-repo joins
  phase 2, birth aftercare and the post-abort reconcile.
- 2026-09-01, types contribute tool profiles (Willem: if nothing
  uses cmake, toolroom must not provide cmake): the required tool
  set is the union over present package types and layers, derived
  by discovery; hse's workspace-global pin list is the ruled
  deviation here.
- 2026-09-01/02, governance applies itself (Willem, side
  conversation): the reviewer count changes by merging livery.toml;
  a path-filtered post-merge configure job with the CI admin secret
  applies it, filters generated by the emitters, red on main when
  the apply fails. The personal admin token demotes to out-of-band
  repair.
- 2026-09-01/02, the reconcile silence ladder (Willem): offline
  path pre-filter from the persisting PR head sha, then
  read-compare, then act; the admin token is mentioned only when a
  write is refused, and nothing unobserved is asserted.
- 2026-09-01/02, the rename transition heals at submit (Willem
  proposed check, corrected to submit and accepted): the gate stays
  offline; submit detects the rename from the branch diff, refuses
  teaching pre-merge application, heals under --fix with the admin
  ladder, and recommends `submit --fix --armed` while never implying
  the arm. Parked merges are the stated window cost.
- 2026-09-01, the composition validators (Willem): a nanobind
  binary-extension package and a CMake Maya-plugin package arrive
  soon after 0.1.0 as the composition phase, proving the kind hierarchy's child
  and root cases and the backend seam's second package type.
- 2026-09-01, the intersection refusal's shape (Willem): a second
  intersecting release refuses immediately from read-only state,
  before prepare, nothing to undo; the taught error names the
  in-flight workflow, its author, the conflicting packages, and the
  options.
- 2026-09-01, diagnostics split (Willem asked whose territory):
  forge owns the facts as granular protocol verbs (protection,
  reviews, schedule-event timeline; per backend plus the fake),
  workshop owns the bundle, schema, storage, and retention. Each
  section guards itself, and a section's own read error is recorded
  as data, the scope-poor-token diagnosis.
- 2026-09-01, the phase 5 audit close (audit finding, no ruling
  needed): the parked re-run gets a refresh driver that re-runs the
  work on the existing branch and commits the delta, so fresh
  floors land while the plain killed-run resume still redoes
  nothing; the two resumes differ because only the post-wait re-run
  knows a release just moved the floors. The dirty-tree resume
  carve-out is scoped to the workflow branch: from any other branch
  the dirt is unrelated work and would ride onto the branch.
- 2026-09-01, the phase 6 audit close (audit finding, no ruling
  needed): a detached HEAD is a taught stop, the branch decides the
  act and "" is not a branch; the branch sanitiser is ASCII-only
  because PEP 440's local segment accepts nothing wider; the
  routing, the terminal-no skip, the failing-build snapshot restore
  on a dirty tree, and the task's interactive stamp each gained a
  forcing test.
- 2026-09-02, phase 7 lands as two PRs (Willem): 7a the entered
  environment (env engine, env group, clean, hooks post-edit and
  stop), 7b the entered shell and the issue family. The env file
  names are hse's verbatim. cmd is left out, not worth the effort;
  the refusal names pwsh.
- 2026-09-02, the issue family (Willem): verbs are `issue.create`,
  `issue.list`, `issue.search`, `issue.start`, `issue.stop`,
  `issue.close`; no global `start`; bare `fm issue` lists. All of
  it is the eventual surface and everything built now goes toward
  it.
- 2026-09-02, assignees are a policy limit (Willem): a config
  value, default 1 so free GitLab needs nothing, honoured by the
  workshop on every forge including ones that enforce no limit;
  the protocol's assign becomes add plus self-remove; `configure`
  errors when the forge cannot support the configured limit,
  landing with phase 8's configure.
- 2026-09-02, start's shape (Willem): a worktree is the default,
  named after the issue, under the runner's home (footman will
  expose the home queryably; `$LIVERY_HOME` is the bridge);
  `--agent` takes an initial prompt.
- 2026-09-02, stop and close (Willem, refined together): stop is
  the local act, the remote branch untouched; nothing that exists
  nowhere else is destroyed without `--discard`, prose tailored to
  the situation. close closes the issue with a reason and optional
  message, disarms through the shared teardown first, and a merged
  PR degrades it to cleanup, "issue already resolved"; branches
  removed by default with the removed sha in the close message.
- 2026-09-02, the isolated leg's toolchain (side conversation,
  reconstructed): the leg installs the gate's dev toolchain at the
  lock's pins via `uv export`, never a retargeted sync; a
  floor-honesty probe re-reads direct dependencies after both
  installs and refuses above-floor drift; dev tools never enter
  `[project]`, and a per-package test group carries floors under
  the same tested-claim rule.
- 2026-09-02, the phase 7a audit close (audit finding, no ruling
  needed): the cascade hook's own exports are subtracted before
  ``env.set`` calls a key the shell's and before ``env.show``
  assigns provenance, and show flags a stale shell export whose
  file disagrees; ``env.check`` judges uv exactly against the
  lock's pin; the stop hook's FAIL match follows footman's real
  one-space padding and pre-bash refuses ``|&`` too, both with
  tests against the real shapes; the pwsh emission gets its own
  completion hook and the dialect defaults per platform; the shim
  ships as layer content (one source, materialised by sync) and
  the retired ruff_fix.py is gone; the RST roles left in published
  docstrings are swept.
- 2026-09-02, assignment is documentation (Willem): not being able
  to assign yourself is never a refusal, only a warning that others
  will not see you working on the issue until something lands, so
  communicate. The limit caps what the workshop writes, never
  whether the work starts. Refines the assignees-are-a-policy-limit
  ruling; issue.start is idempotent end to end (an existing branch
  and worktree are reused and reopened).
- 2026-09-02, footman 0.49 at the phase boundary (Willem): migrate
  as its own PR when the current phase's work lands, using as much
  of the new surface as fits: the `[builtins]` table and
  `footman.builtin` entry point for livery-workshop, the `expose=`
  audit, `fm self.*`, and the new location accessors.
- 2026-09-02, the phase 7b audit close (audit finding, no ruling
  needed): the destruction rule runs before any teardown, so a
  refusal checks a branch that still exists; the shared teardown
  gains keep_branches (the PR ends, both branches stay) so
  --keep-branch holds with an open PR; only-local looks for dirt
  where the branch actually lives, the linked worktree included;
  a merged close keeps an only-local delta without --discard
  instead of destroying it; start's re-run resumes instead of
  erroring; the fail-opens, the provision note, the offline
  completion, the missing editor, and the pwsh plan each gained a
  forcing test; the forge issue docs state the add semantics.
- 2026-09-02, submit --force with a lease (Willem): the pre-bash
  push guard's own remedy (rebase onto the base, push) dead-ended
  without it, and a needed raw-git exception marks a missing verb.
  `fm submit --force` pushes ``--force-with-lease``, names the
  commits the force discards before pushing (the flag is consent to
  discard those, not consent in the abstract), is refused on
  ``workflow/`` branches (their commits are the record recovery
  reads; the engine re-prepares), and is never implied. The
  non-fast-forward refusal teaches both arms: a fix commit (the
  default, squash collapses it), or a deliberate rebase then
  --force.
- 2026-09-02, not-started and hanging split (Willem): the base
  gate's timeout distinguishes a run that reported and hung from a
  tip that never got a run; detection of failed run creation is
  uniform across the three forges, the safe remedy is not, and the
  nudge stays a printed teaching. Gitea's wedged actions queue is
  fixed upstream in 1.27.3 (banked with the quirk); the nudge
  candidate becomes the empty-commit PR (open item 7). Building
  the timeout split's forcing test exposed that lowest-direct
  never starved the wheel's own dependencies (transitive to the
  resolver); the leg now lists them explicitly.

## Open

1. footman#536 (stock `fm` mounts `footman.tasks` entry points):
   external dependency of the global-verbs story (`new.project`,
   `new.repo` outside a workspace). Owner: Willem, in footman.
   Bridge until then: the user-rung tasks file.
2. The standalone integrate verb (hse's `hse merge`, our submit
   self-heal as its own spelling): wanted, and `merge` is taken by
   `submit.merge`. Candidate name `fm integrate`. Owner: Willem,
   naming ruling at phase 1 review.
3. The derived monorepo release view's exact shape (docs phase):
   from tags plus per-package changelogs, or one git-cliff run over
   all commits. Owner: docs-phase design.
4. The template composition design (the section above): ruling
   wanted on both axes before phase 8 builds the overlay half.
   Owner: Willem, at this plan's review.
5. Where a built Maya plugin is delivered (no PyPI-shaped index
   applies): an artifact store, a forge release attachment, or
   deferred. Owner: composition-phase design.
6. Whether repository governance (phase 8) gates 0.1.0 or follows
   it. Owner: Willem, at this plan's review.
7. Automating the base-CI nudge if one forge's dropped-run rate
   ever justifies it. The candidate shape (2026-09-02): an
   empty-commit PR merged through the normal path, which needs no
   push permission and whose squash carries the same tree as the
   old tip, so its run vouches the tree the release publishes.
   Limits: it cures a dropped event, not a wedged runner queue
   (its own checks need runs to merge); required approvals tax it
   once governance lands, unless an admin token bypasses them
   where the forge and its protection settings allow; and
   empty-diff squash-merging needs a recorded verification on all
   three servers before anything names it. Owner: whoever hits the
   rate; a direction, not a commitment.
