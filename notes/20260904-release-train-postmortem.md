# Post-mortem: the first real release met fifteen findings

Status: 2026-09-04. Closed: the release landed on GitHub the same
day. Receipt tags packages/forge/v0.2.0 and packages/workshop/v0.1.0,
livery-forge 0.2.0 and livery-workshop 0.1.0 on pypi.org through
trusted publishing, the template artifact v0.1.0 on
workshop-templates. The publish wave's first successful GitHub run
took 38 seconds, on PR #160's squash. Requested by Willem: "the
basic release machinery was supposed to be done and tested" — this
note establishes, with commits and dates, in what sense that was
true and in what sense it was not.

## What happened

On 2026-09-04 the first real release since 2026-08-31 was attempted:
a two-member set, livery-forge 0.2.0 and livery-workshop 0.1.0. The
train refused or derailed repeatedly; each failure was a real defect,
fixed through the normal flow (issue, gate, PR), and the next attempt
found the next one. Fifteen findings, in the order met:

| # | finding | born | in | fixed |
| --- | --- | --- | --- | --- |
| 1 | released-ness judged by the stamped pyproject, not tags | 2026-09-01 `936c7cb` | PR #64 | PR #126 |
| 2 | the same guard, duplicated in `derive_plans` | 2026-09-01 `9a9cc14` | PR #68 | PR #128 |
| 3 | per-member builds against set-wide find-links | 2026-09-01 `9a9cc14` | PR #68 | PR #130 |
| 4 | stranded-entry regeneration missed the driver's path | 2026-09-04 (fix 1's gap) | PR #126 | PR #132 |
| 5 | the toolchain pins export workspace members as relative paths | 2026-09-02 `64d89bc` | PR #75 | PR #134 |
| 6 | version tests hardcode their version literal | 2026-08-31 `f4e5246` | PR #2/#5 | PR #136 |
| 7 | `pyyaml>=6` floors at a release whose sdist no longer builds | 2026-08-31 `4821332` | PR #20 | PR #138 |
| 8 | floors below the toolchain's pins are untestable claims | 2026-09-01/02 | PRs #68/#75 | PR #140 |
| 9 | the dogfood sync test mutates the real repo from the isolated leg | 2026-08-31 `f35b8c2` | PR #17 | PR #142 |
| 10 | the release commit stamps pyproject but not the workspace lock | 2026-09-01 `9a9cc14` | PR #68 | rode PR #145; durable fix PR #166 |
| 11 | the emitted release.yml is invalid YAML, so every run died at startup | 2026-09-01 `994d77f` | PR #69 | PR #147 |
| 12 | a rider commit with the reserved `chore(release):` prefix enters the manifest, which the publisher then refuses | 2026-09-01 `994d77f` | PR #69 | PR #173 |
| 13 | the receipt probe reads only PEP 691 JSON; Gitea's index serves PEP 503 HTML | 2026-09-01 `994d77f` | PR #69 | PR #152 |
| 14 | the member-keys test is shadowed by the CI rung's shared-file override | rung emission | — | PR #151 |
| 15 | the merged-PR guard walls off every later release of a member set | 2026-09-01 | PR #69 | PR #159 |

Finding 4 is this session's own: an incomplete fix, caught by the
next attempt. Findings 7 and 8 are the floor and movement guards
working exactly as designed, on their first real run, against floors
that had aged.

Finding 9 crossed a line the others did not: it wrote to the working
tree. The isolated leg runs the package suite against the installed
wheel, and the dogfood check (`test_the_monorepo_is_in_sync`) synced
the monorepo from the scratch venv, re-pointing the repository's
real `.claude/hooks/fm-hook.sh` link at a temp directory. The test
was correct in the one environment it had ever run in, the editable
checkout, and wrong in the environment the train built for it. Same
shape as cause 1: the isolated leg had never run this suite before
2026-09-04.

Finding 10 surfaced after validation passed: the release commits
stamp the member's pyproject and `__init__`, but the workspace
`uv.lock` still records the old version. The first `uv sync` after
stamping rewrites the lock, and the train's own dirty-tree refusal
then blocks its re-run. For this release the lock line rode the
release PR as a hand commit; the durable fix (prepare refreshes the
lock inside the commit that stamps the version) is PR #166.

Finding 11 is cause 2 made flesh. The emitter that writes
release.yml shipped in PR #69, the phase whose live round-trip
acceptance was deferred in the commit body and never closed. An
f-string resolved the `printf '%s\n'` escape, the emitted run block
carried a real newline at column 1, and the workflow was invalid
YAML from the day it was written. GitHub reports an unparseable
workflow as a zero-second startup failure that no required check
surfaces, and the drift gate compares the committed file to the
emitter byte for byte, so both were wrong in perfect agreement. The
publish wave and the receipt tags had never run, on any push, since
2026-09-01. The forcing test now parses every generated workflow
for all three forge kinds.

The train also stopped once for a cause that was not a defect:
upstream released copier 9.18.1 mid-session, the highest leg
resolved it, and the movement guard refused the toolchain's older
pin. That is cause 7 live, in the other direction: the lock ages
against upstream just as floors do. PR #144 moved the floor and pin
together.

## The decisive fact

Every tag in the repository was cut on 2026-08-31. The entire real
release arm — the driver, the two-leg validation, the toolchain
pins, the movement guard — was built or rebuilt on 2026-09-01 and
2026-09-02, after the last tag. Between the v0.0.2 tag and the first
fix, the three release-machinery files took 24 commits across 17 PRs
with zero releases. The machinery the first real release ran was
machinery no real release had ever run.

"Done and tested" was true at the level the acceptances operated:
every phase's edge tables passed, the engine's action table passed
with no I/O, a two-member set released end to end on the fake with
the topological commit order pinned. It was false at the only level
a release train ultimately answers to, and nothing in the process
made that gap visible.

## Root causes, ranked

**1. The real arm never ran between rebuild and use.** The one live
release evidence in the 0901 plan is phase 6's dev release: one
member, a dev version, no reserved branch, no tags, on the branch
arm that bypasses prepare, derivation, and receipts by construction.
The real arm's first execution was production, four days and 24
commits after its last companion tag.

**2. A deferred acceptance silently became a met one.** Phase 4's
acceptance required "a full release round-trip on the local Gitea
and GitLab containers". The shipping commit (PR #69) says the paths
"await live verification with the next release-legs dispatch"; the
plan's record says "Phase 4 shipped" with no deferral. The deferral
was honest in the commit and invisible in the record, and the record
is what the next reader trusts.

**3. A refactor regressed a contract-level property with no pin.**
PR #58 judged released-ness by tags, correctly, per contract 9. PR #64 replaced the hand-written miner with git-cliff the same evening
and dropped the tag test in passing, leaving the stamp as the sole
judge. No test pinned the property "the tag is the judge", so the
regression was invisible; the property lived only in the contract's
prose. It now has pins on both paths.

**4. The guard was duplicated, so one wrong assumption lived twice**
(findings 1 and 2; the drift contract 14 exists to prevent). Fixing
`prepare` did not fix the train, which is how the duplication was
found.

**5. A named warning was patched only where it was noticed.** PR #72
(2026-09-01, 23:41) says "found live: the monorepo's 0.1.0 stamp
sits ahead of workshop's v0.0.2 tag" — the exact trigger of findings
1 and 2 — and fixed the unchanged-refusal in the dev arm it was
touching. The real arm kept reading the stamp for three more days.

**6. Stubbed integration tests read as coverage.** The driver suite
releases a two-member set end to end and pins the topological PR
title — with `validate_member` replaced by a lambda in every
multi-member test. `test_local_release_reports_builds_and_restores`
has "builds" in its name and proves a stub was called twice. The one
real-legs test is single-member, dependency-free, with a
single-element find-links tuple; the movement guard only ever saw a
fabricated pins file, never the real `uv export` output. The names
promised more than the tests proved.

**7. Floors age, and only a release tests them.** `pyyaml>=6` was
honest on 2026-08-31 and false by 2026-09-04 standards (no wheels
for the newest supported Python; an sdist a current Cython cannot
build). The copier and footman floors sat below the toolchain's own
pins, which the movement guard proves untestable in a shared venv.
Nothing revalidates floors between releases, and releases were rare.

**8. Out-of-band acts created states the invariants did not model.**
The 0.1.0 stamp (PR #42: "stamped 0.1.0 for the tag that follows the
merge" — the tag never followed) and the hand-cut lightweight
v0.0.2 tag on a docs commit both bypassed the train. The machinery's
"stamped means released" assumption was wrong, but the stranded
stamp is what made it load-bearing.

## The rehearsal on the local rig

After finding 12 stalled the GitHub recovery, the whole train ran
against the local Gitea rig (2026-09-04): a clone of main, the
contract pointed at localhost:3000, the emitted .gitea workflows on
act_runner. Outcome: legs green in 2m31s, release PR merged by CI,
the wave published both wheels to the rig's package index, the
receipt tags packages/forge/v0.2.0 and packages/workshop/v0.1.0 cut
after the index confirmed, and the template artifact v0.1.0
published. The mid-wave recovery was exercised for real: the CI wave
died (findings 13 and the address fact below), the local
`fm workflow.release.publish --ref` re-run walked past the
already-published wheel and finished the wave.

Finding 15 stopped the GitHub retry after the rehearsal: the
merged-PR guard refused any branch name that had ever had a merged
pull request, and the train derives its reserved branch name from
the member set, so merged PR #145 blocked every later
forge+workshop release. The guard now refuses only when the merged
head is an ancestor of the local branch; a branch cut fresh off the
merged base is a new cycle and proceeds. The failed run also left
the checkout on the reserved branch with stale prepared commits;
recovery semantics for that state are issue #161.

The rehearsal found findings 13 and 14, plus two environment facts
that are not code defects: the committed .repo.env must name the
index by the runner-reachable host (`gitea:3000`), with
`.repo.env.local` overriding to `localhost:3000` on this machine;
and the rig's act_runner container ships without node, git, or bash,
so every actions/checkout step fails until they are installed (rig
debt, patched live in the running container).

## What is already done

The nine fixes (PRs #126 to #142), each with the forcing test or
guard its defect lacked, and the plan's appended section recording
the real-leg coverage gap with the rehearsal proposal.

## The GitHub release: how it closed

Both asks were met on 2026-09-04: the FORGE_ADMIN_TOKEN secret was
set (the local gh token, one token everywhere) and the PyPI trusted
publishers were confirmed to match the workflow and environment.
The retry was then a fresh `fm workflow.release` run from main: the
stamped-but-unreleased guard covered the already-stamped versions, a
fresh squash left finding 12's polluted title behind, and finding
15's fix let the reserved branch cycle again. PR #160 merged and the
wave published, probed, and tagged both members in one 38-second
run, with the template artifact behind it.

## Corrective candidates, for Willem to rule

1. **The rehearsal** (already appended to the plan): a
   `workflow.release --local` run over the true member graph in the
   armed suite — real builds, both legs, the movement guard,
   publishing nothing. It converts causes 1, 6, and 7 from
   release-day discoveries into suite failures, and stops the
   train's path aging between releases.
2. **Deferrals live in the plan, not the commit body.** A phase
   acceptance that ships deferred keeps an open line in the note
   until the evidence lands; "shipped" with a silent deferral is how
   cause 2 happened.
3. **A contract clause that code enforces gets a pin before its
   implementation is replaced.** Contract 9's judge existed only as
   prose through the PR #64 refactor; the refactor checklist is
   "name the properties the old code enforced, pin them, then
   replace it".
4. **Warnings found live get an issue, not a local patch.** PR #72's
   "found live" line was the whole incident, three days early, and
   it evaporated into one arm's fix.
5. **No hand stamps, no hand tags.** The train owns versions and
   receipts; an out-of-band claim (name-squatting included) is
   recorded as debt in the plan the day it is made.
