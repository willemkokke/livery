# The merge queue: main's tip is always a proved tree

Status: written 2026-10-09 as a future plan; not started, no issue
filed. Phases 1 to 3 build a serial queue the workshop drives on
every forge and prove it in the local loop. Phase 4 adds GitHub's
native queue as a probed capability, taken when the metrics record
shows queue latency on the busy days.

## The prompt (Willem)

> What kind of merge queue setup would give livery the most benefit?
> Willem, 2026-10-09

> Write it down as a future plan. Willem, 2026-10-09, after the
> answer below was given in the session.

The answer this plan records: make "main's tip is always a tree the
gate proved before it landed" the invariant. Get it first with a
serial queue the workshop drives itself, which works on all three
forges and the local loop can prove. Add GitHub's native queue as a
probed capability on top, for batching on busy days. The serial
queue delivers most of the benefit on its own.

## What exists

Read in the code and the records on 2026-10-09.

- `fm submit --armed` opens a squash pull request and arms the
  forge's auto-merge. Branch protection on main does not require an
  up-to-date head. That is the contract
  `packages/workshop/docs/development-patterns.md` states under
  "What the forge does with several green pull requests": a pull
  request lands on its own green, onto whatever main is at that
  moment, and the combination of two is proved by the next push of
  anything.
- The gate point runs on `pull_request`: three check legs
  (ubuntu-latest, macos-latest, windows-latest, Python 3.14), the
  docs job, and the gate job, the one required context. The gate job
  stamps the tree a green run proved. On a merge checkout its row
  names the head and the base it merged, and a later reader accepts
  the row only when the rebuilt merge yields the row's tree
  (`leg_plan` in `livery.workshop._gate_record`).
- The merge point runs on `push` to main. It inherits the gate, then
  governs (`fm workflow.configure --if-changed`) and dispatches the
  release wave (`fm workflow.release.dispatch`) after main's own
  verdict.
- The forge protocol has `arm`, `disarm`, `is_armed` and
  `merge_now`. `livery.forge.Protection.block_on_outdated` is read
  from GitHub (`strict`) and Gitea (`block_on_outdated_branch`) and
  is inert on GitLab. `livery.forge.RepoConfig` has no write side
  for it, and no backend has an update-branch verb. The GitHub
  backend writes one ruleset, for protected tags, and legacy branch
  protection for main.
- `fm submit.merge` refuses a behind-base branch at once. The
  release engine merges main into its branch when behind
  (`MERGE_DEFAULT`) and re-derives a set the base moved under
  (`REPREPARE`), both before it arms.
- The loop, `fm ci.e2e --scenario=<name>`, runs on the local Gitea
  with the sets `develop`, `release`, `points`, `extension` and
  `all`.
- The repository is public on GitHub, so GitHub's merge queue is
  available on the current plan. Gitea has no queue. gitlab-ce has
  no merge trains.

Measured from `refs/workshop-origin/metrics`, 300 runs between
2026-10-03 and 2026-10-09, and from `origin/main`:

| What | Value |
| --- | --- |
| Merges to main per day, last 21 days | median about 20, peak 37 |
| Pull request runs / push runs in the window | 165 / 135 |
| Pull request run, wall | p50 11.2 min, p90 15.6 min |
| Push run on main, wall | p50 5.1 min, p90 15.0 min |
| Longest leg, windows-latest | p50 7.7 min, p90 14.2 min |
| docs job / gate job / govern job | p50 3.6 / 1.1 / 0.5 min |

A push run's median is the docs, gate and govern jobs with no leg:
the squash's tree was proved by the pull request's run because main
had not moved under it. Its p90 is a full leg re-run: main had
moved, the squash's tree was never proved, and the legs run after
the merge. A conflict between two green pull requests is found
there, on main, after it landed.

## Ground-truth contracts (do not violate)

1. **Nothing on the merge path waits on a person.** A queue step
   that cannot act prints why and exits; the next event runs it
   again.
2. **Every queue verb is idempotent.** Re-running `fm queue.advance`
   is the recovery procedure, and a second run changes nothing.
3. **No logic in YAML.** The merge-group event is emitted plumbing;
   the decisions live in `fm submit`, `fm queue.advance` and
   `fm ci.run`.
4. **The forge is spoken to through `livery.forge` alone**, and a
   feature a forge lacks is probed with `supports()`, never declined
   by a constant.
5. **The squash takes the pull request's title and the changelog
   reads it.** The queue rewrites no title and no history; an update
   merges main into the branch, and fix commits ride.
6. **The gate's record keeps its shape.** A row names the tree a
   green run proved and, on a merge checkout, the two commits it
   merged. A reader accepts a merge row only when the rebuilt merge
   yields the tree. The queue adds rows of this shape and changes
   no reader's rule.
7. **The invariant holds on every forge by the workshop's
   ordering**, not by a protection flag. A flag a forge cannot
   express reads inert, and the flag where it exists is a guard
   against a merge made outside the queue.
8. **Windows is in the gate.** What the pull request point runs
   stays as it is until Willem rules on open item 1.
9. **Fallbacks before happy paths.** A lost update, a lost arm, a
   red head, a conflicting update and an empty queue are tested
   before the green path, through fault modes on the fake and the
   loop's scenario.
10. **A stacked child stays unqueued while its parent is open**, as
    it stays unarmed today.
11. **The loop never runs from main.**

## The design

### The invariant

Main's tip is always a tree the gate proved before it landed. Two
consequences follow. Main is never red. A push run's gate finds its
tree proved and runs no leg, so the push run is the docs, gate and
govern jobs on every merge, not only when main stood still.

### The queue, one path on every forge

Membership is a label, `queued`, on the pull request. `fm submit
--armed` applies it. A merge, `fm workflow.abort`, or a red verdict
at the head removes it. The order is the pull request number, lowest
first.

Only the head is armed. `fm submit --armed` arms at once when the
pull request is the head and its branch is up to date with main.
Otherwise it labels, leaves the pull request unarmed, and prints
`queued behind N pull request(s)`. The forge's own auto-merge lands
the head on its green. Only the head is ever armed, so what lands is
always an up-to-date head whose run proved the squash's tree.

`fm queue.advance`, a verb hidden from `--help`, promotes the head:

- An ordinary branch behind main gets `update_branch`, which merges
  main in; its run restarts on the new merge checkout; the step arms
  it. One that is up to date is armed at once.
- A `workflow/` branch is handed to the workflow engine's decision
  instead: `MERGE_DEFAULT` integrates, `REPREPARE` re-derives a set
  the base moved under, and `ARM` arms. The engine already owns
  these actions; the queue only chooses the moment.
- A head whose verdict is red leaves the queue: label removed,
  disarmed, the next head promoted. The agent's `fm submit --armed`
  after the fix queues it again.
- An update that conflicts drops the head the same way and prints
  the conflict, naming `fm sync` for a person; the queue moves on.
- An empty queue prints `queue empty` and exits 0.

The step runs at the merge point, after
`fm workflow.configure --if-changed` in the govern job, and at the
gate verdict when the verdict at the head is red. It runs by hand
too, and twice in a row is a no-op.

Governance writes `block_on_outdated` where the forge can express it
(GitHub, Gitea). It is a guard, not the mechanism: a merge made by
hand on the forge's own page from a stale head is refused there.
GitLab reads inert and the probe says so.

`fm status` prints the queue: position, head, and how far each entry
is behind main.

### Why only the head is armed

Instead of asking, this plan decides it: one path on every forge.
GitLab CE cannot block an outdated merge, so a design that arms every
queued pull request and relies on the up-to-date rule leaves the
invariant unproved on one forge. Arming only the head needs no flag.
Its cost is that a queued pull request on GitHub shows no auto-merge
until it is the head; the label shows its state instead.

### The record and the push run

The head's run proves the merge checkout of its head onto main's
tip. The squash lands the same tree. The push run's `leg_plan` finds
that tree in the record, and the run proves nothing new: docs, gate,
govern. This is today's behaviour for a pull request main did not
move under, which the loop's `verified-skip` scenario already pins;
the queue makes it every merge.

### What the serial queue costs

A head that was behind pays one more full run, about 11 minutes at
today's p50. Five pull requests queued at once land the last one
about an hour later. The agents do not wait, `fm submit --armed`
returns as it does today, so the cost is runner time. At twenty
merges a day this is enough. At the scale of a large monorepo, with
hundreds of merges a day, the serial queue is the floor for a forge
without a native queue, and the native queue or speculative chains
carry the load; phase 4 is the first of those.

### GitHub's queue as a capability

`livery.forge.Capability` gains `"merge_queue"`. GitHub answers true
when a ruleset with a `merge_queue` rule targets the default branch,
read through `/rulesets`; Gitea answers false; GitLab answers false
on CE (open item 4 for Premium's merge trains). The write side is
`RepoConfig.merge_queue`, declared by `[ci] merge-queue = true` in
`workshop.toml`; governance writes the ruleset
`workshop-merge-queue`: squash, group size 1 to 5, every entry must
pass, check timeout above the recorded p90.

With the capability present, `fm submit --armed` arms every queued
pull request, because arming is entering the forge's queue, and
`fm queue.advance` updates no branch. The gate point fires on
`merge_group` as well as `pull_request`; `fm ci.run` on that event
stamps the group checkout, naming the group's base and head, so the
squash of a batch lands as a proved tree and the push run proves
nothing, as before.

The loop cannot exercise it: Gitea has no queue. It is proved by
conformance cassettes recorded on the scratch GitHub repository and
by this repository's first merge-group runs, and what the queue does
differently gets its rows in `packages/forge/docs/quirks.md`.

### An option, not taken until ruled

Run only the ubuntu and macos legs and the docs job at the pull
request point, and leave windows-latest to the head's run. Pull
request feedback drops from about 11 minutes to about 5. Windows
still gates before main. The cost is that a Windows-only failure
shows at the head, not on the pull request. Contract 8 holds until
open item 1 is ruled.

## Phases

### Phase 1: the update-branch verb and the up-to-date rule's write side

Deliverables: `livery.forge.PullRequests.update_branch(number)`,
answering the head sha after the update and raising
`livery.forge.ForgeError` naming the conflict when the forge refuses
one. GitHub `PUT pulls/{n}/update-branch` (merge), Gitea
`POST pulls/{index}/update?style=merge`, GitLab
`PUT merge_requests/{iid}/rebase` (rebase; the protocol page says
which style each forge uses, and the squash collapses both).
`RepoConfig.block_on_outdated`, written on GitHub (`strict`) and
Gitea (`block_on_outdated_branch`); GitLab declines with
`livery.forge.Unsupported` behind a new capability
`"block_on_outdated"`. The fake: `update_branch`, and a fault mode
`Faults.lose_update` (the update is accepted and nothing moves).
Conformance scenario `update-branch`: a behind head is updated and
reads up to date; a conflicting head raises naming the conflict; the
flag round-trips where expressible and declines where not. Tests,
refusals first. Docs: `packages/forge/docs/protocol.md`, the verb
under `pr` and the capability under "Capabilities".

**Acceptance**

- `fm forge.dev.up`, then
  `fm forge.fixtures.record --scenario=update-branch` records the
  three cassette sets, and
  `fm forge.conformance --scenario=update-branch` replays green on
  the fake and all three backends.
- `fm check` green.

### Phase 2: the queue in the workshop

Deliverables: the `queued` label, declared through
`RepoConfig.labels` by governance. `fm submit --armed` labels, arms
the head only, and prints `queued behind N pull request(s)` for the
rest; a child with an open parent stays unlabelled.
`fm queue.advance` as designed above, called from the govern job
after `fm workflow.configure --if-changed` (a new `Entry` at the
merge point) and from the gate verdict on a red head. `fm status`
prints the queue. Governance writes `block_on_outdated` where
expressible. Tests, refusals and fallbacks first: a second armed
pull request is queued and not armed; a lost arm is re-armed
(`Faults.lose_arm_schedule`); a lost update is re-done
(`Faults.lose_update`); a conflicting head is dropped and the
conflict printed; a red head is dropped and the next promoted; a
`workflow/` head whose set went stale is re-derived before it arms;
an empty queue prints `queue empty`; `advance` twice changes
nothing. Docs: `packages/workshop/docs/development-patterns.md`,
"What the forge does with several green pull requests" rewritten to
the queue, and "Submit and move on" naming the queue line.

**Acceptance**

- With one pull request queued, `fm submit --armed` from a second
  branch prints `queued behind 1 pull request`, and the forge shows
  the second labelled and unarmed.
- `fm queue.advance` run twice prints the same queue; the second run
  changes nothing on the forge.
- `fm check` green.

### Phase 3: the loop proves the queue

Deliverables: scenario `queue` in `livery.workshop._e2e`, in the
`all` set and runnable by its own name; `develop` stays as it is.
The scenario on the local Gitea: three branches from the loop
repository's feature branch; the first and second queued with the
second behind; the third carries a failing test and queues behind
them. Pinned lines: the first lands on its green; `advance` promotes
the second, updating it `behind 1 commit`; its run proves the merged
tree; it lands; the third is promoted, its verdict is red, it is
dropped naming the verdict, and the queue reads empty; every push
run's gate prints `proved by run N` and runs no leg. The metrics rows
of the pass show two merges and push runs with no leg.

**Acceptance**

- `fm ci.e2e --scenario=queue` passes on the local rig with the
  pinned lines in the pass's log.
- `fm store.show metrics` shows the pass's push runs with no leg.
- `fm check` green.

### Phase 4: GitHub's queue as a capability

Deliverables: `"merge_queue"` in `livery.forge.Capability`, probed
on GitHub through `/rulesets`, declined on Gitea and GitLab after the
probe. `RepoConfig.merge_queue` and `[ci] merge-queue` in
`workshop.toml`; governance writes the ruleset
`workshop-merge-queue` (squash, group size 1 to 5, every entry must
pass, check timeout above the recorded p90). The gate point's events
gain `merge_group`, emitted as `merge_group:` with
`branches: [main]`; `fm ci.run` on the event stamps the group
checkout naming its base and head. With the capability,
`fm submit --armed` arms every queued pull request and
`fm queue.advance` updates nothing. Conformance scenario
`merge-queue` recorded on the scratch GitHub repository with the
ruleset in place; the quirks table gains what the queue does
differently. Tests, refusals first: the capability declined on
Gitea's and GitLab's cassettes; a ruleset write the token cannot
make refuses naming the grant; `merge-queue = true` on a forge
without the capability refuses at `fm workflow.configure`, naming
the forge. Docs: `packages/forge/docs/protocol.md`, and the
development patterns page's queue section gains the GitHub
paragraph.

**Acceptance**

- `fm forge.fixtures.record --backend=github --scenario=merge-queue`
  records on the scratch repository and
  `fm forge.conformance --backend=github --scenario=merge-queue`
  replays green.
- On this repository, after `fm workflow.configure` with
  `merge-queue = true`: the next `fm submit --armed` enters the
  queue, its merge-group run is green, the pull request merges by
  the queue, and the push run's gate prints `proved by run N` with
  no leg. `fm ci.timings` lists the merge-group runs.
- `fm check` green.

## Temporary, replaced by

| Temporary | Replaced by |
| --- | --- |
| The head-only arm on GitHub (phase 2) | the forge's queue, where the ruleset exists (phase 4); the label stays as the state `fm status` reads |
| `block_on_outdated` as a guard set by governance (phase 2) | stays; with the ruleset in place, open item 3 decides whether the flag stays on |

## Decision record

- 2026-10-09: Willem asked which merge queue setup gives livery the
  most benefit, and after the answer asked for it as a future plan.
  The plan records the answer: a serial queue the workshop drives on
  every forge first, GitHub's native queue second as a probed
  capability. Not started; no issue filed.
- 2026-10-09: the facts under "What exists" were read in the code
  and measured from `refs/workshop-origin/metrics` (300 runs,
  2026-10-03 to 2026-10-09) and `origin/main` (21 days) that day.
- 2026-10-09, instead of asking: only the head of the queue is
  armed, membership is a label, and the order is the pull request
  number. One path on every forge, because GitLab CE cannot block an
  outdated merge. The verb is `fm queue.advance` and the label
  `queued`; both names are open item 6.
- 2026-10-09, instead of asking: the `queue` scenario joins the `all`
  set and not `develop`, which stays the fast default; it is also
  runnable by name.

## Open

1. Willem: may the pull request point run only the ubuntu and macos
   legs and the docs job, leaving windows-latest to the head's run?
   Feedback drops from about 11 to about 5 minutes; a Windows-only
   failure then shows at the head. Contract 8 holds until ruled.
2. Willem: when phase 4 is taken. Proposed trigger: the metrics
   record shows queue latency on days above thirty merges.
3. Agent, phase 4: whether GitHub's queue needs `block_on_outdated`
   off, or lives with it on. Verified on the scratch repository
   before the ruleset is written here.
4. Agent, phase 4: GitLab Premium's merge trains as the same
   capability. The e2e lane is gitlab-ce, so the probe is tested as
   a decline only, and the Premium path is a cassette from a live
   instance when one exists.
5. Agent, phase 2: the permission the gate job's token needs on
   GitHub to arm and label another pull request
   (`pull-requests: write` on the generated workflow), and the Gitea
   runner token's rights. The step names the permission it lacks.
6. Willem: the names `fm queue.advance` and `queued`.
