# One home for every git access: the git library

Status: planned, 2026-10-11, no phase started. The destination note
(`notes/20261006-workshop-destination.md`) records the measurements
this plan stands on and the rulings of 2026-10-11 on narrowing and on
the git helper's memory.

## The prompt (Willem)

> Let's create a plan to split out all git access into a separate
> library, including state. That will also take a bunch of git testing
> out of workshop changes. Willem, 2026-10-11

Read with the exchange before it: the state store proves local trees
as well as remote ones, so it is one mechanism with two homes and
belongs in the split; an in-process git library is the one change
that makes a fact a function call instead of a process; porcelain and
the network stay with the git binary, because that is where the work
outweighs the process start.

## Why

Measured on 2026-10-11 over the seven slowest workshop test files,
single process: 5820 git processes at the start of the day, 4813
after the slices of #1432, #1456 and the helper's memory. The remainder
is 1278 `rev-parse` and the store's reads and writes, one process per
fact, each costing tens of milliseconds on the Windows runner where it
costs one or two on Linux: the Windows check leg is 843 s, 660 of them
one pytest run, and the git-driven files pay 8 to 13 times what they
pay on ubuntu. A library turns those facts into function calls.

The split also moves tests. The narrowed gate runs a change's own
package and its dependents; the library is a dependency of the
workshop, so a workshop change no longer runs the store's and the
helper's own suites. The flow tests that drive git through the helper,
submit, update, issue, currency, stay with the workshop, since they
test the flows.

## What moves and what stays

Today the workshop has two git seams and a scatter: the helper
`livery.workshop._git_ops.GitOps`, 63 methods over the toolroom's
pinned git handle, depending on nothing of the workshop; the state
store `livery.workshop._state`, JSON rows on git refs with its own
one-door `_git`, remote series under `refs/workshop/` and local ones
under `refs/workshop-local/`; and direct calls of the handle in twelve
other modules, 31 of them in the loop.

The library takes the helper whole and the store whole: the series
model (`Series`, `Keyed`, `Row`, `Rows`, `Skipped`, `slug`), the
snapshots (`Read`, `Fetched`), the reads, the compare-and-swap writes,
the fetch and the sweep with the family rules it applies. All of it is
the library's public interface, exported from `livery.git`; nothing a
workshop module needs is an underscore name of the library.

The store's CI policy, who may write, under which identity, and the
name of the variable a published snapshot travels in, is handed to the
library once, as the arguments of a `Store`. The workshop builds that
`Store` in one place and declares its series beside it:
`livery.workshop._records` holds the series (metrics, verified, the
coverage record, marks, the gate record, diagnostics, traces, the
loop's rows) and `store(root)`, which binds the run context's identity
and the `ci_only` rule. Every importer calls the library's own names on that
store; nothing wraps a library call to keep an old name alive.

The workshop also keeps the CI run context (`run_context`, `ci_run`,
the event payload, the dispatch inputs, the leg and point variables)
and the stack recorded as `branch.<name>.workshop-parent` config keys.

## Ground-truth contracts (do not violate)

1. One door. After phase 3, no module under `packages/workshop/src`
   calls the git handle; every git access goes through the library. A
   test refuses a call that does, the way the vocabulary test refuses a
   kind's word in the base.
2. The store's rules stand as its module docstring states them: a write
   never starts from a state it could not read; a shared ref is
   written by compare-and-swap, `update-ref` with the old value or
   `push --force-with-lease`, and a lost swap costs a retry, never a
   wrong result; the push's own report is the verdict; a commit carries
   its own identity and `[skip ci]`; the working tree and the index are
   never touched; every failure is a printed reason, never a boolean.
3. The library imports nothing of the workshop and no CI fact. What is
   CI's, allowed writers, identities, variable names, arrives as a
   parameter.
4. Porcelain and the network stay with the git binary from the
   toolroom's pinned store: merge, rebase, stash, worktrees, fetch, push.
   The leased push is never emulated in-process: libgit2 has no lease
   (libgit2#4286), dulwich none, and a client-side check before a force
   is not the server's atomic compare.
5. Bytes end to end. A blob never crosses a text pipe; the archive is
   read from disk as bytes, the import stream goes through the binary
   lane of `run()` and the handle, and an in-process read returns the
   bytes git holds, with no end-of-line conversion.
6. The helper's memory of facts answers a reader called with
   `cached=True` from the last live read and forgets on any call through
   the helper that moves the repository; a move outside the helper is
   invisible, and a caller passes the flag only where nothing else ran.
7. Every behaviour pin the state and helper tests hold today holds
   after each phase; the process-count pins move to the new counts, and
   in phase 4 to zero where a read is in-process.

## Phase 1: the package, and the helper moves

A new python package `packages/git`, distribution `livery-git`, import
`livery.git`, born with `fm new.package git`, depending on
`livery-toolroom` for the pinned handle and on nothing else. The
helper moves whole as `livery.git.GitOps` with `GitError` and the
move classifier; its public names are what the package's `__init__`
exports. The stack stays behind: `livery.workshop._stack` holds
`stack`, `record_stack` and `forget_stack` over a generic
`config_regexp` reader the helper gains. The 60 import sites in the
workshop's source and tests are rewritten; the helper's own tests move
to `packages/git/tests`, the flow tests stay. The layering graph gains
the edge workshop to git to toolroom.

Deliverables: `packages/git/` with `workshop.toml`, `pyproject.toml`,
`src/livery/git/__init__.py`, `_ops.py`, `docs/`, `tests/`;
`livery.workshop._stack`; no `_git_ops` left in the workshop.

**Acceptance**

- `fm check` is green.
- `fm layering.graph` shows `workshop -> git -> toolroom` and no edge
  from git to the workshop.
- `grep -rn "_git_ops" packages/workshop` prints nothing.
- `fm ci.e2e --scenario=develop` is green on the local Gitea.

## Phase 2: the store's transport and series model move

`livery.git` exports the store as a public interface: `Store(root,
identity=..., writes=..., published=...)` with the methods `read`,
`list_refs`, `put`, `drop`, `remote_snapshot`, `fetched_snapshot`,
`fetch` and `sweep`, and the types `Series`, `Keyed`, `Row`, `Rows`,
`Read`, `Fetched`, `Skipped` beside it. The policy is construction
data: `writes` is the rule a `put` asks before it starts (the reason it
refuses, or nothing), `identity` is the committer, `published` the
variable's name. `_state` is deleted. `livery.workshop._records` holds
the series declarations and `store(root)`, which builds the `Store`
from the run context; the CI run context moves to
`livery.workshop._ci_context`. Each of the 40 importers is rewritten
in this phase to import the types from `livery.git` and call the store
it gets from `store(root)`; no name is kept for compatibility. The
store's tests move to the library with their pins on behaviour and
process counts; the workshop's tests on the store shrink to the
policy: a write from outside a run refuses with the run context's
reason, and a published snapshot reaches a child.

**Acceptance**

- `fm check` is green; `packages/git/tests/test_git_store.py` pins the
  counts (a remote write is five processes, a read three, a local
  family's commit times one) and the compare-and-swap refusals.
- `packages/workshop/tests/test_workshop_records.py` holds the policy
  tests alone; `test_workshop_state.py` and `_state.py` are gone.
- `grep -rn "livery.git._" packages/workshop` prints nothing: the
  workshop imports the library's public names alone, and the layering
  lint's private-import check (#1204) refuses otherwise.
- `fm ci.e2e --scenario=release` is green: the gate job's union, stamp,
  metrics row and janitor write the store through the library on the
  real runner.

## Phase 3: the direct calls converge

The loop's 31 direct calls (`fetch`, `switch -C`, `clean`, `reset`,
listings), `_new_project`'s five, the release driver's four and the
rest go through the helper, which gains what they need: a pruning
fetch, a switch that recreates, a clean, a hard reset named as such.
Then the one-door test: a scan of `packages/workshop/src` refuses any
call of the git handle, with an allowance table that must only fall,
the vocabulary test's shape.

**Acceptance**

- `grep -rn "tools\.git\.\|toolroom\.git\." packages/workshop/src`
  prints nothing.
- `packages/workshop/tests/test_workshop_vocabulary.py` or its sibling
  refuses a new direct call.
- `fm ci.e2e --scenario=release` is green.

## Phase 4: in-process reads

The library takes dulwich as a dependency and answers reads without a
process: the position, a branch's local and remote heads, refs and
branches, config keys, log subjects and messages, tree ids, merge
bases and ancestry, the store's tree reads, the janitor's commit times.
Local writes go in-process too: the store's root commit as objects
written through dulwich and its compare-and-swap through the refs
container's set-if-equals. Remote writes keep one `git push
--force-with-lease`; fetch, push, merge, rebase, stash and worktrees
keep the binary. The counting pins drop to zero for every read and
local write; the Windows leg is measured on main before and after,
and the number recorded here.

**Acceptance**

- The library's tests pin zero processes for a read and a local write,
  and one for a remote write.
- `fm ci.e2e --scenario=release` is green.
- Main's push run after the merge is green on every runner, and its
  Windows check leg's time is recorded in the decision record beside
  the 843 s of 2026-10-11.

## Temporary, replaced by

| Temporary | Replaced by | Phase |
| --- | --- | --- |
| nothing scaffolded: each phase rewrites imports in the same change, no re-export shim | | |

## Decision record

- 2026-10-11, Willem: every git access leaves the workshop for one
  library, the state store included, since it proves local trees as
  well as remote ones and is one mechanism with two homes. Why: a
  workshop change then runs none of the git tests that are not the
  flows', and the in-process reads land in one leaf. #1432's slices,
  the store's transport (#1456) and the helper's memory came first and
  measured 5820 to 4813 on the seven git-driven files.
- 2026-10-11, Willem: in-process reads through a library, porcelain and
  the network with the git binary, because that is where the work
  outweighs the process start. The leased push stays a process
  (libgit2#4286). dulwich is the recommendation for reads and the
  store, pygit2 only if porcelain ever moved, which it does not.
- 2026-10-11, Willem: no wrapper. The store moves as the library's
  public interface and every importer is rewritten in the same phase;
  the workshop keeps only its series declarations and the one place
  that binds the CI policy. "It's never cheaper to clean up than now."
  A shim that kept `_state`'s names would have left 40 modules on an
  underscore import of a seam the library now owns.
- 2026-10-11, Willem: no narrowing of a package's tests by reach while
  the workshop changes wholesale; the split takes the git suites out of
  a workshop change by the package rule instead.

## Open

1. The package's name: `git` under `packages/`, distribution
   `livery-git`, import `livery.git`. The alternative `repository`
   says more and collides with the forge's `Repository`. Owner: Willem.
   Recommendation: `git`.
2. The helper's class name on the move: `GitOps` kept, which keeps the
   diff mechanical, or `Repository`, which reads better and collides
   with the forge's. Owner: Willem. Recommendation: keep `GitOps` in
   phase 1, rename in phase 4 if at all.
3. dulwich against pygit2 for phase 4: dulwich is pure Python and
   models exactly what the store needs; pygit2 is faster per call and
   brings libgit2's own filters and config reading, a second
   implementation of the things that bit on Windows. Owner: Willem.
   Recommendation: dulwich.
4. Sequencing against the workshop's refactor slices (#1377): phases 1
   to 3 are mechanical moves that touch many import lines and conflict
   with any slice in flight; each should land in a quiet hour, synced
   just before. Owner: whoever starts phase 1.
