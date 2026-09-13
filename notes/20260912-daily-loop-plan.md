# The daily loop: one reflex, one proof, one teardown

Ruled by Willem on 2026-09-12, in the discussion that started from
#518: the core verbs of daily development sit at the top level; the
flow that makes a thing removes it, and the janitor is the net for
what slipped through; a finding met while addressing a ticket is dealt
with in that ticket, not filed as a new one; and `fm check --affected
--fix` is meant to be run "without having to think about it". This
note is the plan for that: the loop as it is practised today, measured,
the shape it becomes, the slices that get there, and the rulings still
open. Sibling of `20260907-ci-quality-plan.md` (the CI side of the same
time) and `20260910-state-store-plan.md` (the store the proof chain
lives in). It closes #518 and #515 in its first slice and folds #383,
#230, #356, and the open half of #454 into later ones.

## What exists today, measured 2026-09-12

### The loop as practised

Counted from the Bash commands the agent sessions on this repository
ran (17 sessions, 1,201 `fm` invocations) and from Willem's own shell
history (81 `fm` invocations, 40 of them `fm check`).

| phase | verb | runs | beside it, by hand |
| --- | --- | --- | --- |
| start | `issue.start` | 73 | `issue.create` first (74), or a raw branch when there is no issue |
| work | `lint`, `format` | 336 | the pre-pass, because the gate costs minutes |
| work | `test` | 82 | one file or package at a time |
| work | `typecheck` | 32 | |
| gate | `check` | 95 | `--affected --fix`, and `VIRTUAL_ENV`/`PATH` re-exported in a worktree |
| ship | `submit` | 86 | `--fix --armed`, followed to the merge |
| ship | `ci.logs`, `ci.status` | 50 | reading a red leg |
| ship | `ci.e2e` | 52 | CI-mechanism changes, the local Gitea loop |
| land | `issue.close` | 71 | `--discard` on every one |
| land | `sync` | 39 | `git pull` before it |
| land | `integrate` | 13 | |

Raw git filled the gaps: 2,657 invocations, of which `add` and `commit`
786 (no verb, on purpose), `fetch` and `pull` 269 (confirming a merge,
updating main), `checkout`, `branch`, and `switch` 214 (branches
without an issue, switching back to main), `worktree` 46 (listing and
removing trees by hand). The 1,466 `log`, `status`, `diff`, and `show`
are reads and stay raw.

### What the gate costs

On the desk, `fm check --affected --fix` for a workshop change takes
2m16s to 2m49s; a change to a root file (`uv.lock`, `pyproject.toml`,
`tasks.py`, `workshop.toml`) fails open to everything and took 5m05s.
On the ubuntu check leg, p50 of the last twenty runs:

| task | p50 |
| --- | --- |
| tests, workshop | 232 s |
| tests, footman | 210 s |
| tests, toolroom | 33 s |
| tests, forge and strongroom | 12 s |
| typecheck | 65 s |
| typecomplete | 30 s |
| lint and format | 0.8 s |

The narrowing stops at the package: one edited module runs the
package's whole suite. And the narrowing is branch-scoped, not
edit-scoped: `changed_paths` unites every commit since the merge base
with the dirty files, so the tenth commit of a branch gates what the
first one touched.

The gate is paid twice. The gate record lets `fm submit` skip a gate
that already proved HEAD's tree, but a row is written only for a clean
committed tree. The reflexive use is on a dirty tree before the commit,
so nothing is recorded and the submit runs the same gate again: #512
paid 2m16s and then 2m41s for one tree.

In a worktree the shell's `PATH` leads with the main checkout's
`.venv/bin`, so `fm` is the main checkout's code and the checkers
resolve its installed set. Nothing refuses; every worktree command in
these sessions carried an `export VIRTUAL_ENV=... PATH=...` prefix.

Two findings are outside the affected gate's sight by construction.
Two test files with one basename in different packages break
collection only in the full gate (#454), because pytest's default
import mode names a test module by its bare basename. A test that
reads the runner's own environment is green on the desk and red on
the runner (three red runs on 2026-09-10 and 2026-09-11).

### What a merge leaves behind

`_only_local_work`, the rule `fm issue.close` and the janitor use to
decide whether a branch holds work that exists nowhere else, counts the
commits the remote does not have. After a squash merge the branch's
commits are never ancestors of main, and once the forge deletes the
remote branch the count runs against `origin/main`. A rig with one
branch, one commit, squash-merged and deleted on the origin:

```text
verdict on the merged branch: '1 commit(s) not on any remote'
content difference against origin/main: ''
```

So every squash-merged worktree reads as holding unique work: the
janitor keeps it, `fm issue.close` keeps it unless `--discard` is
passed, and `--discard` is passed every time. That is the mechanism
behind the trees swept by hand (65 on 2026-09-09) and behind #515.

### The release act (#518)

`fm workflow.release strongroom --armed` on 2026-09-12 left the
checkout on `workflow/release/strongroom` at 3151663, the branch's own
stamp commit. `run_workflow` submits with `follow_to_verdict=False`
and returns after arming; the failure path of `prepare` switches back
to main and deletes the branch, the success path has no counterpart.
`fm sync` returns at once on any `workflow/` branch. After the squash
45707f8 had merged, `fm workflow.release.dispatch` read the manifest
at HEAD, took the last commit touching it (3151663) as the stamping
commit, and dispatched run 34674876050 there. The wave refused,
correctly: the `Mined-At` line lives in the pull request body and
reaches git only as the squash's message, so a branch commit can never
pass the movement backstop. `pending_release_waves` has the same HEAD
blindness: from the release branch it cannot see the squash, so a
re-run there would enter branch recovery and refuse with "main has
moved past this prepared release".

## The shape

### The verbs

The loop is seven top-level verbs, and nothing in the loop is under a
family:

| verb | does |
| --- | --- |
| `start <issue \| "title" \| kind/slug>` | opens the work: a worktree on the branch from a fetched `origin/main`, its own environment, and the shell entered; an issue number assigns, a title files and starts, a plain slug does neither |
| `check` | the reflex: proves the working tree against the newest proved tree it descends from, at the scope that delta affects; `--full` runs everything |
| `commit <type> "<subject>"` | stages, derives the scope from the changed paths, runs the reflex, validates the subject, commits; refined over time |
| `submit` | pushes, opens or reuses the pull request, arms, follows; skips a gate the chain already proves; tears its own worktree down when the follow sees the merge |
| `sync` | brings a checkout current and matches the lock; steps off a merged reserved branch |
| `abandon [branch]` | gives a branch up, this one or a named one |
| `integrate` | brings `origin/main` in by merge, then matches the lock when the merge touched it (#356) |

`status` stays as it is. `fm issue.*` is for issues: list, search,
create, update, close, reopen (#383), stop. `issue.start` folds into
`start`. `git commit` keeps working; the verb is the spelling that
never mistypes the scope and never commits an unproved tree.

### `start`, by example

Every form opens a worktree under the runner's home from a fetched
`origin/main`, syncs its environment, and puts the caller in it.
"Entered" is two mechanisms, because a child process cannot change
its parent shell's directory or environment. With the shell hook
installed (the same generated snippet `fm --install-completion`
evaluates at shell start), `fm start` is a shell function that runs
the verb and then changes the current shell's directory into the
worktree and loads its environment: no nesting, nothing to leave.
Without the hook, `start` execs a child shell in the worktree the way
`fm shell` does today, and `exit` returns to the parent shell; that
is the `(exit to leave)` in the examples. A non-interactive caller (an
agent) gets the path printed and relies on the environment guard,
which makes `fm` in that directory run from that directory's own
`.venv`. Re-running any form on started work re-enters it.

```console
$ fm start 518
  issue #518: assigned to you
  branch fix/518-the-release-act-leaves-the-checkout, from origin/main at e29417f
  worktree ~/.local/share/footman/worktrees/livery/518-the-release-act-leaves-the-checkout
  environment: synced (uv)
  entered: fix/518-the-release-act-leaves-the-checkout (exit to leave)
```

```console
$ fm start "the janitor drops merged local branches" --body="$(cat body.md)"
  issue #519 filed: the janitor drops merged local branches
  issue #519: assigned to you
  branch feat/519-the-janitor-drops-merged-local-branches, from origin/main at e29417f
  worktree ~/.local/share/footman/worktrees/livery/519-the-janitor-drops-merged-local-branches
  ...
```

```console
$ fm start docs/daily-loop-plan
  branch docs/daily-loop-plan, from origin/main at e29417f (no issue; submit closes nothing)
  worktree ~/.local/share/footman/worktrees/livery/docs-daily-loop-plan
  environment: synced (uv)
  entered: docs/daily-loop-plan (exit to leave)
```

The plain form spells the kind in the branch, `docs/`, `chore/`,
`feat/`, because the branch is what gets named and the kind is what
the release train reads; a bare slug refuses naming the kinds.
`--type` overrides the kind the issue forms derive, `--no-worktree`
reuses the checkout as `issue.start` does today (a dirty tree refuses
naming `--wip`), and `--agent` hands the worktree to a coding agent.
The counterparts: `fm abandon` inside the worktree, or
`fm abandon docs/daily-loop-plan` from the main checkout, close the
pull request if one is open and remove the worktree and both branches.

### `commit`, by example

```console
$ fm commit feat "the store's remaining round trips"
  scope: workshop (from the changed paths)
  check: tree a1b2c3d4 proved by the reflex; recorded
  committed 9f8e7d6: feat(workshop): the store's remaining round trips
```

It stages everything (`--only <paths>` narrows), derives the scope
from the affected packages so `type(scope): subject` is never
mistyped for the release train, runs the reflex first so every commit
is on a proved tree (`--no-check` skips), takes `--body` or opens the
editor, validates the subject the way `submit` validates a title, and
refuses on main, on a reserved branch, and `--amend` of a pushed
commit. For an agent the gain is the scope and the validation at
commit time instead of at submit; for a person it is also the subject
format and the "did I check" question. The first form lands in slice
2 and is refined as it is used.

### The proof chain

A gate-record row today is (tree, scope, packages, base tree) where the
base tree is the merge base with `origin/main`, and only a clean HEAD is
recorded. The generalisation: a row proves its tree against any earlier
proved tree, and the chain's root is a full local gate or CI's
`workshop/verified` stamp for the merge base, which the snapshot already
reads. One rule then answers both questions.

- What to run now: the newest proved tree the working tree descends
  from, the delta between the two, and `affected(delta)`. Edit-scoped.
- What the submit needs: a chain of rows from a root to HEAD's tree.
  Each row re-proved the closure of its own delta, and a package
  untouched by a step keeps the earlier step's proof, because
  `affected` is closed under dependents; the union of the steps covers
  `affected(merge base..HEAD)` by construction.

A dirty tree is recorded by its working tree id, `git write-tree` on a
temporary index after `add -A`. A commit that takes everything has the
same id and the submit skips; a partial commit has another id and gates
again. A root-file change still fails open to a full step, which
re-roots the chain. `MAX_AGE` and `KEEP` apply per row; a broken chain
means a full step.

### What runs inside a step

- A delta confined to test files of a package, on a proved base, runs
  those files. The floor is no obstacle locally: the local number is a
  printed preview, and CI's union judges the floor on the pull
  request's run. A changed file under `tests/` that is not a test (a
  conftest, a helper) reaches every test of the package and widens to
  the suite.
- A delta touching a package's source runs the package's suite. The
  per-test contexts the legs record are first-hit under the sysmon
  tracer, so a test-to-file map from them misses every later test, and a
  static import graph misses the `fm` children the workshop tests
  spawn. The package is the sound unit. Finer selection (a Pants-style
  file-level inference with per-test-file proofs keyed by the closure's
  digest, and conservative widening for spawners) is held open until the
  package-level reflex is measured.
- Every step runs the two checks the affected gate cannot see: the
  tests run once with the runner's variables set and once scrubbed.
  The basename collision check is not needed once tests are namespaced
  (below).

### The kind seam

The backend protocol has `build`, `publish_artifact`, versions,
requirements, and `check`, which for cpp-conan configures, builds, and
runs ctest in one call; python runs its verbs at workspace scope. Three
additions, names to rule on:

- `classify(path)`: source, test, test support, or configuration; what
  a test file is belongs to the kind.
- `test(package, root, selection)`: all tests, or the named ones.
- `tests_need_build`: python tests run on source, cpp tests run on a
  build. For a kind with it set every test step is preceded by the
  kind's incremental gate build, so a test-only change rebuilds and
  runs only its tests, and the gate treats both kinds through one path.

### Namespaced tests

`--import-mode=importlib` in the project template's pytest section
names each test module by its path from the root
(`packages.strongroom.tests.test_lifecycle`), so basenames may repeat
and the collision class is gone rather than checked. Test directories
are then off `sys.path`: helper modules need a `pythonpath` entry per
package's tests directory in the rendered configuration, and a naming
rule that a helper in `packages/X/tests/` starts with `X_`, checked
per package. xdist and the one conftest work unchanged.

### The teardown

A worktree has outlived its usefulness the moment its pull request
merged, or its issue closed, and the tree holds nothing only there.
"Nothing only there" is the test `abort_if_merged` already uses: the
branch's tip is the head the merged pull request took, and the tree is
clean. Applied in `_only_local_work` it corrects `issue.close`, whose
`--discard` becomes what it says, and the janitor. `submit` tears its
own linked worktree down when the follow sees the merge, the way
`issue.close` does today, and prints the directory to go to. The
janitor judges a tree by its branch's pull request when the name
carries no issue number, and drops merged local branches in the main
checkout by the same rule (#515). It is the net, and nothing else.

### The release act

`run_workflow` records the branch it started from and, after the
submit returns, switches back and deletes the local reserved branch;
the remote branch is the recovery copy, `prepare` already fetches it,
and `active_workflow_names` unites local and remote branches so the
workflow stays visible while its pull request is open. `dispatch_flow`
refuses a stamping commit without a `Mined-At` line, through the same
helper the wave uses, and names the newest release squash on
`origin/main`. `pending_release_waves` walks `origin/main` instead of
HEAD. `sync` steps off a merged reserved branch for checkouts today's
code left there.

### The environment guard

`fm` refuses, or re-executes itself, when the checkout's `.venv` is not
the one it runs from. `start` hands over a shell that is already right.
A verb that moves HEAD across a change to the lock, the root manifest,
or a member's pyproject ends by matching the lock and says so (#356).

## The slices

Each lands alone, gate-green, with its refusal tests first, and updates
this note in the same change.

1. **The teardown and the release act** (landed 2026-09-12).
   `only_local_work` by the merged head; `submit` tears its worktree
   down at the merge; the
   janitor judges by pull request and drops merged local branches; the
   release act switches back and drops its local branch; the dispatch
   guard and the base walk; `sync` steps off a merged reserved branch.
   Closes #518 and #515. Acceptance: the loop's release proof and its
   member-only proof pass; a rig shows a squash-merged tree removed by
   the submit and none left for the janitor.
2. **The top-level verbs** (landed 2026-09-13). `start` with its three forms and the
   entered shell; `issue.start` folds in; `abandon [branch]`;
   `commit` in its first form (the sketch under the verbs, refined
   over time); `submit --closes=none` and `issue.reopen` (#383: a
   branch started from an issue carried only a plan edit, the merge
   closed the issue before its work began, and the recovery was a raw
   `gh issue reopen`); a pull request already green when `--armed`
   asks merges directly, since merged-when-green was the intent
   (#230: GitHub refuses to enable auto-merge on a pull request
   already in clean status, and `fm submit --armed` exited 1 with a
   traceback on PR #229 where `fm submit.merge` finished it).
   Acceptance: a notes-only change and an issue's change both run
   start, check, commit, submit, sync with no raw git but the commit.
3. **Namespaced tests** (landed 2026-09-13). importlib mode, `pythonpath`, the helper
   naming rule; the open half of #454 closes as unnecessary.
   Acceptance: two packages each with `tests/test_lifecycle.py` collect
   and pass in one session.
4. **The proof chain** (landed 2026-09-13). Working-tree ids, rows against any proved tree,
   the chain walk in `submit`, `check` edit-scoped by default with
   `--full`. Acceptance: one tree pays one gate; the tenth commit of a
   branch gates only what it touched; the loop's scoped-leg proof
   unchanged.
5. **The kind seam** (landed 2026-09-13). `classify`, `test(selection)`, `tests_need_build`
   in the python and cpp-conan backends; test-only steps; the two
   environment runs inside every step. Acceptance: a test-only edit in
   workshop runs its file and records the step; a cpp package rebuilds
   and runs its tests through the same path.
6. **The environment guard** (landed 2026-09-13, reshaped). The venv refusal, `start`'s shell,
   `integrate` and `sync` matching the lock (#356: a merge of main
   brought a new `pytest11` entry point, the worktree's venv kept the
   pre-merge metadata, and the next gate went red until `fm sync`;
   any verb that moves HEAD across a change to the lock, the root
   manifest, or a member's pyproject ends by matching the lock and
   says so). Acceptance: a
   worktree gate with the main checkout's `PATH` refuses with the
   right words; #356's reproduction is green.
7. **Held open: finer selection.** File-level inference for python and
   per-test-file proofs, only if slice 4 and 5 leave the reflex above
   the target for a one-module edit.

## Acceptance

- A one-module edit in workshop is proved by `fm check` in under a
  minute on the desk; a test-only edit in seconds.
- One tree pays one gate: the submit repeats no gate the chain proves.
- The loop of a change is start, check, commit, submit, sync, with the
  commit the only raw git.
- No worktree and no local branch is left by a merge on the normal path;
  the janitor's dry run after a week of work names nothing merged.
- No cross-package check runs on any gate.
- `fm ci.e2e` stays whole: gate, merge, scoped, prose and tests legs,
  release, receipt, nightly.

## Open rulings

None. The shell-hook form of `start` above is the design taken
without asking; the child shell stays as the fallback.

## Decision record

- 2026-09-12, Willem ("I'd like the core tasks / verbs to be top
  level"): `start`, `check`, `submit`, `sync` at the top level as the
  loop; `issue.start` folds into `start`; `fm issue.*` is for
  interacting with issues, not part of the loop.
- 2026-09-12, Willem ("The janitor is meant to cleanup things that
  slipped through"): the flow that makes a thing removes it; the janitor
  is the net, never the mechanism. Leftover worktrees are the standing
  annoyance this rules against.
- 2026-09-12, Willem ("When I'm looking at fixing/addressing a ticket, I
  rarely will want a new ticket created"): findings met while addressing
  a ticket are dealt with in it; #515 closes with #518.
- 2026-09-12, Willem ("we want to apply the same gate proof tracking
  that we use to prevent submit from duplicating the gate run. Or even
  better, I think that case falls out cleanly if we just generalise
  it"): the proof chain of this note; test-only changes run their
  tests on a proved base; source changes are not selected below the
  package; the kind seam with a build property.
- 2026-09-12, Willem ("should we change the tests to be namespaced?
  Checking all test files among all packages scales really badly in
  large monorepos"): tests are namespaced through importlib mode, and
  the cross-package collection check is not built.
- 2026-09-12, Willem ("it sounds like we need to put all of this in a
  comprehensive plan, but it might be the thing that really cuts down
  iteration / CI time"): this note.
- 2026-09-12, Willem (rulings on the note's open list): `check` is
  edit-scoped by default with `--full` as the opt-in and `--affected`
  retired; the proof chain is rooted at CI's `workshop/verified` stamp
  for the merge base; the seam's names are `classify`, `test`, and
  `tests_need_build`; a plain `start` gets a worktree by default, as
  an issue does.
- 2026-09-12, Willem ("lets add the commit verb and refine it over
  time"): `commit` is the seventh top-level verb, first form in slice
  2. #383 folds into slice 2; #230 too, with the ruling "automatically
  merge manually as it's ready and that's the intent": a pull request
  already green when `--armed` asks is merged directly; #356 folds into
  slice 6.
- 2026-09-12, slice 1 landed (#518, #515). `only_local_work` in
  `_submit` is the one keep-or-drop rule, judging by the merged pull
  request's head: a tip the merge took holds nothing only here, a tip
  past it holds the commits after the merge, and a tip the merge does
  not reach falls to the ancestry rule. `fm issue.close` and
  `fm issue.stop` pass the merged head; the sweep judges a worktree by
  it, a tree named after no issue by its pull request alone, and the
  checkout's local branches by the same rule, naming the checked-out
  one for `fm sync`. `fm submit` removes the linked worktree it stood
  in when the follow sees the merge and keeps a tree with something
  the merge did not take, named; the main checkout keeps its branch
  for the run's logs. The workflow engine returns to the branch the
  act started from and drops the local reserved branch, and both
  drivers resume from origin's copy. The dispatch refuses a stamping
  commit without a `Mined-At` line and names the newest squash on
  `origin/main`, which the recovery now walks. `fm sync` steps off a
  merged reserved branch through the shared teardown. Verified by the
  suites and the loop, whose first pass caught a fault of #512's
  snapshot instead (a ref the remote moved past the listing failed
  its read); fixed in the same change, recorded in the state store
  plan. The fresh pass, not run since the union and the metering
  landed, was broken in five places of its own and fixed here too:
  the repository delete outran the client, a package-less leg read
  as a dead meter, that leg's unit rows lacked their closure
  identity, a skipped leg named the tests unit so the union neither
  collected nor carried it, and the verified-skip proof named members
  and units a fresh birth does not have yet. One anomaly stayed open
  here: on one pass main's check leg reported its per-run ref put and
  the gate job's listing thirty seconds later lacked it. It is
  explained under slice 4: a first gate attempt had already collected
  and dropped the ref.
- 2026-09-12, after slice 1 landed (#530, then #532). The first live
  teardown at a merge, #530's own submit, stopped on footman's refusal
  of a `chdir` inside a parallel task, after the merge had landed and
  before the worktree was removed. The move is required: Windows
  refuses to remove a directory a process stands in, and footman lets
  only a serial task move the real directory. `submit` and `abandon`
  are declared serial since #532, `issue.close` already owns the
  terminal, and a test pins the three declarations; #532's own merge
  tore its worktree down through the fixed path. Two more facts for
  the record: the unexplained anomaly showed a second time on the
  whole fresh pass (main's run 1502 red on its first attempt, green on
  the re-run), and `fm janitor` asks the forge only in an attended run
  (a terminal on stdin, without `--no-input`), so the branch rule's
  first live run over the checkout's merged local branches is a
  person's, not an agent's.
- 2026-09-13, slice 2 landed (#383, #230). `fm start` opens work from
  an issue number, a quoted title that files one, or `<kind>/<slug>`
  for a branch that belongs to no issue, always into a worktree under
  the runner's home, entered in a shell when a person is at the
  terminal; `issue.start` folded into it. `fm commit <type> "<subject>"`
  stages, derives the scope from the packages the change touches,
  runs the affected gate in its fix mode first, validates the subject,
  and commits; it refuses on main and on a reserved branch. `fm
  abandon [branch]` gives a named branch up from anywhere, its
  worktree with it, and a branch that is nowhere is nothing to undo.
  `fm submit --no-close` leaves the branch's issue open, and a pull
  request already green when `--armed` asks is merged directly, since
  GitHub arms only a blocked one. `Issues.reopen` joined the forge
  protocol on every backend and the fake, with a conformance scenario
  recorded for Gitea and GitLab (GitHub's cassette is Willem's to
  record, since recording creates scratch repositories under his
  account), and `fm issue.reopen` reopens an issue with start's
  assignment. The shell-hook form of `start` (a wrapper function in
  the completion snippet, no nested shell) is still to do; the child
  shell is the form that landed. The loop was not run: the slice
  touches no CI mechanism.
- 2026-09-13, slice 3 landed. The project template sets pytest's
  importlib mode and puts every package's tests directory on
  `pythonpath`; a layout plugin on the `pytest11` entry point refuses
  a session whose helper modules lack their package's name. The rule
  found three kinds of leftover in the suites: the forge's conformance
  drivers and the footman and toolroom typing samples renamed, the
  release-driver tests' member helper moved into the workshop's seeds
  module (a test module may not import another in importlib mode),
  and the worker a footman test spawns moved into a helper, since a
  spawned process cannot import a module named by its path. The full
  gate ran every suite under the new mode. The cross-package
  collection check the plan once proposed, and #454's open half, are
  not built: the collision class is gone. Not run: the loop, since
  nothing here touches a CI mechanism.
- 2026-09-13, slice 4 landed. The gate record is a chain: a row
  proves its tree against the proved tree its delta was taken from,
  rooted at a full gate on the machine or at a tree CI's record holds,
  and the rows key the working tree's id (a scratch index, `add -A`,
  `write-tree`), which a commit that takes everything shares. `fm
  check` is edit-scoped by default: the plan proves the working tree
  by its chain, else gates the delta from the proved tree with the
  fewest changed paths, among HEAD's first-parent history (fifty deep)
  and the newest twenty rows' trees, so a green check of a dirty tree
  is the next check's base after one more edit, else runs everything
  and roots a chain; `--full` runs everything, and
  `--affected` is retired. `fm submit` walks the chain from HEAD's
  tree and skips its gate when it reaches a root, naming the chain,
  and runs the reflex otherwise, whatever the contract's affected-legs
  key says, since the chain proves the same set by composition. Inside
  CI nothing changed: the legs gate the pull request's changes against
  its base or the whole workspace, and the local record is never read
  or written there. The commit verb runs the reflex too. A fix run
  records the tree the rewriters left, measured before the judges
  read it: the first run recorded the tree the plan measured, one
  reformat behind the commit that followed, and the submit found no
  chain for it.
- 2026-09-13, the reflex's first run rooted 13 commits back. Main's
  tip ran red in CI on a render drift: the toolroom-store birth was
  gated on a branch cut before slice 3 rendered the pytest pythonpath,
  and the two green merges composed a `pyproject.toml` that differs
  from its render, so CI's record lacked the tip's tree. The roots
  were the merge base's tree alone, so the chain fell back to a local
  full row 13 commits back and gated 405 paths. The roots are now every
  tree CI's record holds among the merge base and HEAD's first-parent
  history, fifty deep, in one read of the record: a branch cut while
  main's own run is red or still running steps from the parent's tree
  and pays for that merge's changes once. The render lands with this
  slice.
- 2026-09-13, the loop's red main runs explained (runs 1471, 1502 and
  1515). The loop runner's docs job fails now and then on a build that
  left nothing, which is why the loop re-runs a red run once. The gate
  job runs whatever its needed jobs did, to report the verdict: on
  1515 its union read the check leg's row, its collection put the
  run's metrics and dropped the per-run refs, and its verdict went red
  over the docs job. The forge's re-run of the failed jobs ran docs
  and the gate again, without the check leg, and the second union
  found no leg row: red twice, as on 1471. The collection now keeps
  the per-run refs while any completed job of the run is red, so the
  re-run's union reads what the first attempt left; the janitor in the
  merge gate sweeps per-run refs older than six hours. The same shape
  held on every forge: a "re-run failed jobs" after any red sibling
  job left the gate unable to union.
- 2026-09-13, slice 5 landed. The backend protocol gained `classify`
  (source, test, test support, configuration), `gate_build`, and
  `test(selection)`, and the kind record `tests_need_build`
  (cpp-conan true: cmake configures and ninja builds, incrementally,
  before ctest; python false). The affected computation classifies
  each changed path by its package's kind: a test file reaches its
  package alone, since nothing imports a test, and when a package's
  changed files are tests and nothing else the reflex runs those
  files, python through pytest on the files and cpp-conan through
  ctest by the file's stem after the gate build; a conftest, a
  helper, a source or a configuration file widens to the suite and
  the dependents. CI's legs keep package granularity, since the union
  records a package's suite. A machine's test run sets the runner's
  variables (CI, GITHUB_ACTIONS), so a test that reads them is judged
  here as on the legs. The scrubbed second run the design named is
  not added: it would double every local gate's test time (the full
  gate's 4m49s of tests, twice) to guard a pytest run outside the
  gate; one word here adds it. A test-only step whose tests reach no
  source measures no coverage, and the local preview says so instead
  of failing, which the loop's tests-leg proof found. Open: the
  python-nanobind kind's
  extension is built by `fm sync` alone, so a C++ source edit there
  needs a sync before the reflex sees it, and its `tests_need_build`
  stays false until the kind gains an incremental gate build. Slice 6
  (the environment guard) is next.
- 2026-09-13, the working tree's id read a rewrite as no change, about
  one run in ten. The id is taken through a scratch copy of the index,
  and the copy was made with `shutil.copy`, which stamps it with the
  time of the copy. git trusts a cached stat only while the entry is
  older than the index file; an entry as new as the index is racily
  clean, and its content is read again. A copy stamped now looks newer
  than every entry, so that rule never fired, and a rewrite of the same
  size in the same second as the cached stat read as no change (git
  compares whole seconds where it is built without nanosecond stat, as
  on macOS). It surfaced as a flaky gate-record assertion on the pull
  request, and it would have let a `--fix` run record a tree the
  rewriters had already moved past. The copy keeps the index's
  timestamps now, which restores git's own rule: 40 rounds of the
  gate's own sequence missed nothing, where the old copy missed one,
  and the previously flaky test passed 20 runs out of 20. A test forces
  the shape directly, with the cached stat and the rewrite made
  identical.
- 2026-09-13, slice 6 landed, without the venv refusal. What landed:
  a second receipt beside the lock's, a digest of the root manifest
  and every member's, so a HEAD move that changes a member's
  `pyproject.toml` without moving the lock is drift and the venv is
  synced (#356's shape: a merge brought a new `pytest11` entry point,
  the lock never moved, and the venv kept metadata that made the next
  gate red). A venv with no such receipt adopts what is on disk, so a
  venv built before this, or by the emitted setup script, syncs once
  and no more. `fm integrate` ends by matching the lock when the merge
  touched the lock, the root manifest, or a member's, and says which.
  `fm start` enters the worktree in the caller's own shell where the
  shell hook is installed: the verb writes the path to the file the
  emitted function names, and the function changes directory and
  evaluates the environment; without the hook it still opens a child
  shell, and an agent still gets the path printed.
- 2026-09-13, the venv refusal is unnecessary, and the code for it was
  removed after it was written. footman's own uv handoff already moves
  a command into the environment of the workspace it runs in, before
  the workshop's pre-tasks hook runs. Three probes said so: the main
  checkout's `fm` run inside a worktree arrived as that worktree's own
  `fm` at that worktree's prefix; the worktree's `fm` run inside the
  main checkout never reached the workshop's hook at all, having been
  relocated to the main checkout's environment; and with `VIRTUAL_ENV`
  and `PATH` exported to another checkout, the process still ran the
  worktree's own venv. A refusal there could never fire, and a guard
  that cannot fire is worse than none: it reads as protection. No `fm` verb needs an environment prefix, from
  any directory and whatever the shell exports, and the loop needs no
  other tool of its own: the gate is the reflex, so a bare
  `python -m pytest` has no place in it.
- 2026-09-13, the plan is landed through slice 6, and its acceptance
  stands as follows. One tree pays one gate: every submit of the last
  day skipped its own gate and named the chain that proved the tree.
  The loop of a change is start, check, commit, submit, sync. No
  worktree and no local branch outlived a merge: #557, #563 and #564
  each tore their own down. No cross-package check runs on any gate.
  `fm ci.e2e` stays whole, at 6m22s and 7m32s on the day's passes.
  The first line is the one that fails. A one-module edit in workshop
  is not proved in under a minute: five runs measured 2m24s to 2m33s,
  because the package is the unit and workshop's suite is the largest
  here. A test-only edit is proved in 27s, a prose-only edit in 5.8s,
  and a tree the record already proves runs nothing at all.
- 2026-09-13, slice 7 is the open question, on the condition the plan
  set for it: finer selection than the package opens only if slices 4
  and 5 leave the reflex above the target for a one-module edit, and
  they do, by a factor of two and a half. The choice is a ruling, not
  a finding: file-level inference for python with per-test-file proofs
  keyed by the closure's digest, and conservative widening for the
  tests that spawn `fm` children, against leaving the package as the
  sound unit and paying 2m30s for a source edit. This line stays open
  until that ruling lands.
- 2026-09-13, the janitor's attended gate is removed, and with it a
  second implementation of a concept footman already had. The sweep
  asked the forge only when stdin was a terminal, which meant an agent
  could never sweep and the checkout's merged branches piled up until
  a person ran it by hand: 89 of them, by the time it did. Willem:
  "this is MEANT to clean up things that are safe to clean up", and
  the gate was never protecting judgement, since nothing in the sweep
  prompts and every line is printed only after the last removal has
  already happened. Safety comes from the keep-or-drop rule alone: a
  branch or worktree whose pull request merged and whose tip the merge
  took holds nothing that exists nowhere else, and everything else is
  kept and named. Asking the forge is a read, and it is what proves
  the removal safe, so gating it inverted the point. The flag leaves
  the sweeper contract entirely. A sweeper that wants a quieter voice
  calls `livery.footman.attended`, which is public, documented for
  exactly this, and says what it means: whether a prompt would reach a
  human. An agent is not attended by that test and should not be: it
  cannot answer a question, though it reads every line and every exit
  code. One concept, one implementation.
- 2026-09-13, the terminal is asked in one place. Four verbs decided
  whether a person was there with their own `sys.stdin.isatty()`, so
  none of them honoured `--no-input` or `--dry-run`: `fm sync`, the
  update driver, the workflow abort policy, and `start`'s open mode.
  All four now ask `livery.footman.attended()`. The history is worth
  recording: every one was written on 1 or 2 September, four to five
  days before `attended()` was reachable here, so they were artefacts
  of ordering rather than of carelessness. The janitor's own copy was
  not: it was written on 10 September with the helper already
  available, and it is the copy that produced a sweep no agent could
  run. Remembering the rule failed once already, so the layering lint
  enforces it now, beside the rule that keeps `livery.forge` on the
  standard library: a module that imports the runner may not call
  `sys.stdin.isatty` or `sys.stdout.isatty`, and asks
  `livery.footman.attended` instead. The runner's own sources are
  exempt, since they implement the answer. Two earlier placements were
  wrong and are worth recording. A ban in the project template reached
  every consumer's application code, which is none of the workshop's
  business. Scoping it by decorator would have missed half of what we
  just fixed: of the four sites, only `sync` and `workflow.abort` sit
  in a decorated function, while the update driver's and `start`'s
  live in plain helpers a task calls. The module's imports are the
  honest scope, and they carry to third-party tasks for free, since
  the shipped workspace test calls the same lint.

