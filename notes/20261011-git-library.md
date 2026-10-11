# One home for every git access: the git library

Status: planned and ruled, 2026-10-11, no phase started. The destination note
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
pinned git handle, depending on nothing of the workshop, with a memory
of the branch and head behind a `cached=True` flag; the state
store `livery.workshop._state`, JSON rows on git refs with its own
one-door `_git`, remote series under `refs/workshop/` and local ones
under `refs/workshop-local/`; and direct calls of the handle in twelve
other modules, 31 of them in the loop.

The library takes the helper's methods as module functions, each
taking the repository's `root: Path` as its first argument, with no
class, no memory and no `cached` flag: the memory saved a few
`rev-parse` processes per flow and measured no change, and phase 5
makes those reads a file read. The library takes the store whole: the series
model (`Series`, `Keyed`, `Row`, `Rows`, `Skipped`, `slug`), the
snapshots (`Read`, `Fetched`), the reads, the compare-and-swap writes,
the fetch and the sweep with the family rules it applies. All of it is
the library's public interface, exported from `livery.git`; nothing a
workshop module needs is an underscore name of the library.

The store's CI policy is handed to the library once, as the arguments
of a `StateStore`, named for what the docs already call it and not
`Store`, which `livery.toolroom.store` and `livery.strongroom` both
export. The policy is three values: whether a CI run is on, which a
`put` to a `ci_only` series asks before it starts ("only a CI run
writes this series; local runs read"); the committer's name and email,
`<prog> ci state` and `ci-state@<prog>.invalid` from footman's program
name; and the name of the variable a parent publishes its ref listing
in, `WORKSHOP_SNAPSHOT`, so a child reads one listing instead of
fetching again. The workshop builds the `StateStore` in one place and
declares its series beside it: `livery.workshop._records` holds the
series (metrics, verified, the coverage record, marks, the gate
record, diagnostics, traces, the loop's rows) and `state_store(root)`,
which binds the three from the run context and footman. Every importer
calls the library's own names on that store; nothing wraps a library
call to keep an old name alive.

The workshop also keeps the CI run context (`run_context`, `ci_run`,
the event payload, the dispatch inputs, the leg and point variables)
and the stack recorded as `branch.<name>.workshop-parent` config keys.

## Ground-truth contracts (do not violate)

1. One door. After phase 4, no module under `packages/workshop/src`
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
   parameter. The library's functions import nothing of footman
   either; the one module that does is its footman plugin,
   `livery.git._plugin`, loaded only through footman's own `plugin()`,
   the shape `livery.forge._dev` has.
4. Porcelain and the network stay with the git binary from the
   toolroom's pinned store: merge, rebase, stash, worktrees, fetch, push.
   The leased push is never emulated in-process: libgit2 has no lease
   (libgit2#4286), dulwich none, and a client-side check before a force
   is not the server's atomic compare.
5. Bytes end to end. A blob never crosses a text pipe; the archive is
   read from disk as bytes, the import stream goes through the binary
   lane of `run()` and the handle, and an in-process read returns the
   bytes git holds, with no end-of-line conversion.
6. The library holds no state between calls. A function takes the root
   and returns a fact; the one value type, `StateStore`, is frozen
   and carries the root and the policy it was built with, nothing else.
7. Every behaviour pin the state and helper tests hold today holds
   after each phase; the process-count pins move to the new counts, and
   in phase 5 to zero where a read is in-process.
8. The library never edits a user's ssh configuration, and a user's
   own `GIT_SSH_COMMAND` or `core.sshCommand` wins over the library's
   multiplexing, which is the only case where a user meets the
   setting at all. Whether multiplexing works is decided by a
   behaviour probe, never by the platform's name.

## Phase 1: the package, and the helper moves

A new python package `packages/git`, distribution `livery-git`, import
`livery.git`, born with `fm new.package git`, depending on
`livery-toolroom` for the pinned handle and on nothing else. The
helper's 63 methods become functions of `livery.git`, each taking
`root: Path` first (`current_branch(root)`, `head_sha(root)`,
`push_with_lease(root, branch, expected)`), with `GitError` and the
move classifier beside them; the class, its memory and the `cached`
flag are deleted, and a caller that passed a `GitOps` down passes the
root. The public names are what the package's `__init__` exports. The
stack stays behind: `livery.workshop._stack` holds `stack`,
`record_stack` and `forget_stack` over a generic `config_regexp`
reader the library gains. The 60 import sites in the workshop's source
and tests are rewritten; the helper's own tests move to
`packages/git/tests`, the flow tests stay and fake module functions
where they faked methods. The layering graph gains the edge workshop
to git to toolroom.

Deliverables: `packages/git/` with `workshop.toml`, `pyproject.toml`,
`src/livery/git/__init__.py`, `_ops.py`, `docs/`, `tests/`;
`livery.workshop._stack`; no `_git_ops`, `GitOps` or `cached=` left in
the workshop.

**Acceptance**

- `fm check` is green.
- `fm layering.graph` shows `workshop -> git -> toolroom` and no edge
  from git to the workshop.
- `grep -rn "_git_ops\|GitOps\|cached=" packages/workshop` prints
  nothing.
- `fm ci.e2e --scenario=develop` is green on the local Gitea.

## Phase 2: the plugin, the environment every git spawn runs with

Every network git call opens a fresh ssh connection, and the handshake
is about half of a quiet fetch. Measured on 2026-10-11 on a macOS
checkout against github.com, `git fetch origin` with nothing to fetch:

| connection                            | time           |
| ------------------------------------- | -------------- |
| a fresh ssh handshake each call       | 2.1 s to 3.0 s |
| multiplexed over one open connection  | 1.0 s to 1.3 s |

A sync makes one round trip, a start three, submit and the release
driver more. With a master kept open for the idle window, every verb
inside that window, and every process of the same user, reuses one
connection per remote host. The local loop is not affected: its remotes are
`http://` with a token (`_e2e.py:591`, `:1295`).

The library gains one function, `ssh_environment(socket_dir, idle)`,
which returns a mapping to merge into a spawned git's environment,
holding `GIT_SSH_COMMAND`, or an empty mapping with a printed reason.
Its rules, in order:

1. `GIT_SSH_COMMAND` set in the environment to a command that is not
   the library's own, or `core.sshCommand` set in git's config: empty,
   no reason printed. The user has an opinion and keeps it.
2. The socket path would exceed the unix socket limit, 104 bytes on
   macOS and 108 on Linux: empty, the reason naming the directory and
   the limit. The socket's name is `%r@%h:%p`, 17 bytes for github.
3. The probe failed: empty, with ssh's own words. The probe opens no
   connection of its own: after the first call through the built
   environment, `ssh -O check` on the socket says whether a master
   exists, and the verdict is written to a file in the socket
   directory, so every later process in every verb reads a file. A
   per-process memory would violate contract 6 and forget at every
   verb.
4. Otherwise `GIT_SSH_COMMAND` is `ssh -o ControlMaster=auto -o
   ControlPath=<quoted path> -o ControlPersist=<idle> -o
   ServerAliveInterval=15 -o ServerAliveCountMax=2`. The path is
   shell-quoted: git hands the command to a shell, and footman's data
   directory on macOS is under `Application Support`, with a space.

Why each option: `ControlMaster=auto` reuses a master when one is up
and opens one otherwise, and replaces a stale socket from an unclean
exit; two spawns racing to open a master is allowed, one wins and the
other connects directly. `ControlPersist=<idle>` keeps the master a
few minutes after the verb exits so the next verb reuses it; on CI it
dies with the job. The two `ServerAlive` options make a master whose
TCP connection died (sleep, a network change) exit within 30 s, so the
next call opens fresh instead of hanging to the TCP timeout; the first
call after a wake may still fail once, every workflow verb is
idempotent, and the docs say so.

The policy is a footman plugin the library ships, `livery.git._plugin`,
registered as `"livery.git"` under the `footman.tasks` entry point the
way `livery.forge._dev` is. The ssh connection is one of the things
the plugin puts in the environment; the others are below. A project lists `plugin("livery.git")` in
its `tasks.py` to turn it on and drops the line to turn it off; the
workshop's generated tasks list it; a project on bare footman gets it
the same way. The plugin registers a `pre_task` hook: footman gives
each task its own complete environment, `task.env`, which the task's
subprocesses see, so the hook writes the mapping into every task's
`task.env`, computing it once per process from `footman.data_dir()
/ "ssh"` and the idle window. No call site changes, and nothing in the
workshop knows the mechanism exists. The hook must write per task: a
write into one task's environment does not reach the next task's. The
`core.sshCommand` read is one git process per fm run until phase 5
reads the config in-process.

Facts that constrain it: the ssh agent is unaffected, since the
program is still `ssh` reading `~/.ssh/config`, so an `IdentityAgent`
entry applies and the master signs once per window; any process of
the same user may reuse the authenticated connection for the window,
the same trust boundary as `SSH_AUTH_SOCK`, and the docs state it.
On Windows the tool store pins PortableGit, whose bundled MSYS2 ssh
accepts `ControlMaster`, while Windows' own OpenSSH does not; a
backgrounded `ControlPersist` master on the MSYS2 build has been
reported to keep git's pipes open so the fetch never returns, which
`ssh -O check` would not reveal. The phase's own test on the Windows
leg runs a fetch through the built environment under a timeout; the
probe is per socket directory, so a Windows host that fails it runs
the normal path. `https` remotes need no special case.

### The identity, the editor, and the decorators

The workshop sets a git identity in three places with three names: the
release driver writes "livery release" into a fresh checkout's config
(`_publish.py:175`), the loop rides "livery loop" in the environment
(`_e2e.py:190`), the store commits as "<prog> ci state"
(`_state.py:691`); the scale fixture and toolroom-bench's colour probe
set a fourth and a fifth in throwaway repositories. Ruled 2026-10-12:
one identity for every commit the tools make, `<prog>` with the
address `<prog>@<prog>.invalid`, which `livery.git.identity(prog)`
returns and the store takes as its `identity` argument. The plugin
writes it into `task.env` as `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`,
`GIT_COMMITTER_NAME` and `GIT_COMMITTER_EMAIL` when `user.email`
resolves to nothing, the same config read that decides the ssh
command, so a person's own identity always stands and no verb writes
a person's config. `ensure_git_identity` and the loop's identity
environment go.

The plugin exports task decorators for the cases where a task knows
better than the default rule, each a marker on the task the way
`@task(serial=True)` leaves `_footman_serial`, read by the plugin's
one `pre_task` hook from the handle it receives:

- `@git.identity(name, email)`: this task commits as the given
  identity whatever the config says.
- `@git.unsigned`: `commit.gpgsign=false` and `tag.gpgsign=false` for
  this task's spawns, as `GIT_CONFIG_COUNT` pairs; the loop, the
  scale fixture and the colour probe each set this by hand today.
- `@git.no_editor`: `GIT_EDITOR=true`, so a rebase continue or a merge
  commit never opens an editor; `_sync.py:117` and `:1012` pass
  `-c core.editor=true` by hand today.

The decorators stack. Footman gives a plugin hook the task's handle
with its name, its environment and its per-plugin state; reading a
marker off the handle needs one small public read on footman, since
the handle's function is private.

Tests, fallbacks first: `GIT_SSH_COMMAND` already set leaves it
untouched; a person's `user.email` set leaves the identity variables
unset; a task without a marker gets the default rule; then a marked
task gets its marker's environment, and two markers stack; `core.sshCommand` set in the repository config the same; a
directory past the limit gives the reason with the directory and the
limit; a failed probe gives ssh's words, with a fake `ssh` first on
`PATH` that exits non-zero on `-O check`; a probe already written runs
no ssh; then the happy path, the command with the five options and
the quoted path.

**Acceptance**

- `fm check` is green on every runner, the Windows leg's fetch under
  the built environment included.
- Two `git fetch origin` through the built environment against
  github.com, the second a channel open, not a handshake, and
  `ssh -O check` on the socket showing the master; the two times are
  in the PR body and in the decision record.
- `fm sync` on a quiet checkout, timed before and after, in the
  decision record.
- `fm doctor` names an existing `ControlMaster` entry in the user's
  ssh config and does nothing more.
- `grep -rn "ensure_git_identity\|LOOP_IDENTITY\|core.editor=true"
  packages/workshop/src` prints nothing.
- `git log --format=%an origin/main` on the local loop's repository
  after `fm ci.e2e --scenario=release` shows `<prog>` as the author of
  the release commits.

## Phase 3: the store's transport and series model move

`livery.git` exports the store as a public interface:
`StateStore(root, in_ci=..., identity=..., snapshot_variable=...)`
with the methods `read`, `list_refs`, `put`, `drop`,
`remote_snapshot`, `fetched_snapshot`, `fetch` and `sweep`, and the
types `Series`, `Keyed`, `Row`, `Rows`, `Read`, `Fetched`, `Skipped`
beside it. The policy is construction data: `in_ci` is what a `put` to
a `ci_only` series asks, `identity` the committer's name and email,
`snapshot_variable` the name a published listing travels in.
`_state` is deleted. `livery.workshop._records` holds the series
declarations and `state_store(root)`, which builds the `StateStore`
from the run context and footman's program name; the CI run context moves to
`livery.workshop._ci_context`. Each of the 40 importers is rewritten
in this phase to import the types from `livery.git` and call the store
it gets from `state_store(root)`; no name is kept for compatibility. The
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

## Phase 4: the direct calls converge

The loop's 31 direct calls (`fetch`, `switch -C`, `clean`, `reset`,
listings), `_new_project`'s five, the release driver's four and the
rest go through the library, which gains what they need as named
functions: a pruning fetch, a switch that recreates, a clean, a hard
reset named as such. The loop is no exception: a function it alone
calls still lives in the library. Then the one-door test: a scan of
`packages/workshop/src` refuses any call of the git handle, with no
allowance table.

**Acceptance**

- `grep -rn "tools\.git\.\|toolroom\.git\." packages/workshop/src`
  prints nothing.
- `packages/workshop/tests/test_workshop_vocabulary.py` or its sibling
  refuses a direct call, with no allowance.
- `fm ci.e2e --scenario=release` is green.

## Phase 5: in-process reads

The library takes dulwich as a dependency, ruled over pygit2 on
2026-10-11, and answers reads without a
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
- 2026-10-11, Willem: the package is `git`: `packages/git`,
  distribution `livery-git`, import `livery.git`. The theme names
  (muniments, ledger, clerk) were weighed and the plain word won.
- 2026-10-11, Willem: dulwich, not pygit2, for phase 5.
- 2026-10-11, Willem: no class and no memory. The helper's methods
  become functions taking the root; the `cached` flag and its memory
  go in phase 1, since they measured no gain and dulwich removes the
  cost they were for. The store stays a frozen value because forty
  callers must not each pass the policy.
- 2026-10-11, Willem: the phases that rewrite imports, 1, 3 and 4,
  land between the #1377 slices;
  sessions that would conflict are paused for them.
- 2026-10-11, Willem: the store's value type is `StateStore`, the
  docs' own name for it; `Store` is taken twice already, by the tool
  store and the strongroom.
- 2026-10-11, Willem: all git access goes through `livery.git`, the
  loop's included. A function only the loop calls lives in the library
  all the same; the one-door test carries no allowance table.
- 2026-10-11, Willem: ssh connection multiplexing for every network git
  call the library spawns, on by default, with no setting for a user to
  find; the library owns the mechanism, the workshop the policy and
  the socket directory; never an edit of the user's ssh config;
  support decided by a behaviour probe. It is phase 2, since it needs
  only the package and pays at once.
- 2026-10-12, Willem: one identity for every commit the tools make,
  `<prog>` at `<prog>@<prog>.invalid`, in place of the five the
  repository invents; set through the environment when a person's is
  absent, never by writing a person's config. The plugin exports task
  decorators that force an identity, unsigned commits, or no editor
  for one task.
- 2026-10-11, Willem: the ssh policy is a footman plugin shipped by
  `livery.git`, on by listing `plugin("livery.git")` and off by not,
  so it works on bare footman too. The plugin's `pre_task` hook writes
  `GIT_SSH_COMMAND` into every task's own environment, the moment
  footman already gives the workshop's `sync_first` hook. The plugin
  module is the library's one footman import, the forge's `_dev`
  shape; the functions stay footman-free.

## Open

1. The probe's verdict file against a per-process memory. Owner:
   Willem. Recommendation: the file, for contract 6 and so a verdict
   outlives the verb.
2. Whether the plugin turns signing off by itself when no person is
   there (`footman.attended()` false), beside the `@git.unsigned`
   marker. The cost: `fm commit --no-input` on a desk with signing on
   would commit unsigned where today it waits on the signer. Owner:
   Willem. Recommendation: the marker alone; an unattended rule only
   where a runner's variables say it is CI.
3. Whether the workshop's 35 test files that each configure a
   "tester" identity in the repositories they create move to one
   conftest fixture that sets the identity variables for every test.
   Test code only; nothing to do with the plugin. Owner: Willem.
