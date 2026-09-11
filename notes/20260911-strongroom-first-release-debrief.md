# Strongroom's first release: the debrief

Written 2026-09-11 at Willem's request, after the receipt was cut.
What was decided, why, and what each decision implies for the train.
The strongroom plan itself is
`packages/strongroom/notes/20260911-strongroom-plan.md`.

## The short account

Strongroom landed in six phases on 2026-09-11 and its first release
met one defect after another in the release train, none of them in
strongroom. Each got an issue an outsider can read and a fix on
main, eleven workshop changes in all. The wave that finally published
`livery-strongroom 0.0.0` ran at the original squash, fd7bc7e,
driven by the workshop released that evening, and its receipt was
cut from a machine with the train's own publish verb.

## What broke, in order, and what was decided

1. **A test basename collision** (#454). Footman and strongroom both
   had `tests/test_lifecycle.py`; the affected gate ran strongroom
   alone through six pull requests, and the release PR ran everything
   and met it. Renamed. Implication: a new package's first submit
   should collect every package's tests together; the workspace rule
   that refuses it before submit is the issue's open half.
2. **The ecosystem rung's empty upload address** (#457). A refusal
   that landed on 2026-09-08 refuses an empty publish address, and
   the nothing-declared rung had one, so no github-kind release could
   publish after that date. The rung now names PyPI's upload
   endpoint. Implication: every release after 2026-09-08 would have
   died here; strongroom's was the first.
3. **A died wave whose own tree was the fault could never recover**
   (#458). The wave checks out the squash and runs the squash's
   workshop, so a fix on main never reached it, and every re-run
   re-dispatched the same tree. Willem's design: the workflow gains a
   `workshop` input and a pin step that installs a released workshop
   over the squash's; `fm workflow.release <pkg> --workshop=<v>` and
   `fm workflow.release.dispatch --at --workshop` pass it through,
   explicit only. Implication: reproducible by default, recoverable
   by choice, one tree; consumers get it on the next workshop release.
   Recovery also reaches older squashes, so a later release never
   strands an earlier died wave.
4. **Recovery blocked every other release** (#462). With any receipt
   uncut the train re-dispatched that wave instead of preparing the
   requested set. Now it recovers only for the set that names the
   uncut member. Left between #460 and #462: the selection picks the
   oldest uncut squash overall, so an uncut receipt outside the set
   hides the set's own died wave (#479, open); the forge and workshop
   wave had to be dispatched by hand with `fm workflow.release.dispatch`.
5. **The workshop could not release** until forge did: the workshop
   at main imports `Step` from `livery.forge`, which 0.2.0 lacks. The
   set forge plus workshop met three more: the forge dev plugin
   imported the bare `toolroom` shim (#461); a workshop test assumed a
   source checkout, and its first fix looked for site-packages in the
   wrong path (#465); and the co-release plumbing, the workshop's
   floor on forge to 0.3.0 (#466), the lock's copier to 9.18.2 (#471)
   and the floor on copier to 9.18.2 (#473).
6. **The copier floor, what it implies.** The legs install the
   member at its floor and at the latest allowed, then install the
   toolchain from the lock's whole dev group and refuse if that moved
   any resolved version. The dev group carries the members'
   dependencies, so both legs pass only when floor, lock and the
   index's newest agree, and the two legs then test one set. Options
   and a recommendation are in this conversation's record; the
   recommendation is to filter the toolchain pins to what the leg did
   not resolve, so floors mean what they say and a release no longer
   needs a bump per upstream release. Awaiting Willem's ruling.
7. **The speed ratchet** judged the workshop suite over its mark for
   a second run in a row on the release PR; the mark was accepted at
   245s with the reason on the record.
8. **A failed-jobs-only rerun cannot pass the merge point** (#469,
   open): the first attempt's collect drops the legs' rows. A
   whole-run rerun is what a merge-point failure needs; the verb
   re-ran an unrelated release wave too.
9. **The legs can test a stale cached wheel** (#470, open): uv served
   a cached `livery_forge-0.3.0` from the first local act instead of
   the rebuilt file of the same name; three local acts failed on a
   fix the tree already had. Cleared with `uv cache clean`.
10. **The receipt push at a non-tip squash** (#480). The job's
    ambient token is a GitHub App token, and GitHub treats a tag push
    whose commit carries a workflow file that differs from the tip's
    as a workflow update that token may never make. Every earlier
    receipt was at the tip; this one was at a squash two workflow
    changes back. The publish job now checks out with the
    `FORGE_TOKEN` secret, falling back to the job token. Implication:
    a receipt at a non-tip squash needs that secret to exist with the
    workflow scope, which the protocol cannot verify; the re-dispatch
    after #480 still pushed as the App token, so the repository has
    no such secret or it lacks the scope. Willem's to settle.
11. **The wave uploaded before it asked the index** (#482), so the
    publish verb could not cut a receipt from a machine without a
    PyPI credential. It now walks past a served version before any
    upload, and that is how this receipt was cut.

## What stands

- `livery-forge 0.3.0` and `livery-workshop 0.2.0` on PyPI, receipts
  cut by their wave at the tip.
- `livery-strongroom 0.0.0` on PyPI, published by the pinned wave at
  fd7bc7e; receipt `packages/strongroom/v0.0.0` at fd7bc7e, cut by
  `fm workflow.release.publish --ref` from a machine.
- Open: #435, #441, #469, #470, #479, the `FORGE_TOKEN` secret, the
  toolchain-pin ruling, and phase 7 of the strongroom plan.

## What to take from it

- The affected gate is the daily loop and is right for what it
  covers; a package's first release runs the full gate for the first
  time, so run it once by hand before the release, and collect every
  package's tests together before a new package's first submit.
- A release that touches the train's own code should be rehearsed
  with the local act on a clean cache, and the receipt path checked
  at a non-tip squash, because that path had never run.
- Every defect above was found by a guard doing its job. The guards
  are worth keeping; three of them refused things that were not
  errors, and those three are the open issues.
