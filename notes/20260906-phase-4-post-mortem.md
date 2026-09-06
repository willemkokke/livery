# Phase 4 post-mortem: the first train release

Status: written 2026-09-06, the day of the release. The migration
plan's phase 4 shipped livery-toolroom v0.6.2, livery-footman
v0.52.2, and the toolroom and footman shims at the same numbers,
published to PyPI with annotated receipt tags. It took one
afternoon, eleven defects, and roughly a dozen armed attempts.
This note is the ledger Willem asked for: what broke, how it was
fixed, why the local net missed it, and where the hole was.

## The structural finding first

There was one hole, and every defect below lived in it. The
workshop verified the workspace world completely: the checkout,
the venv, the gate, the chain. The release executes in a second
world: installed wheels instead of editable sources, stamped
versions instead of dev versions, emitted CI YAML instead of local
verbs, the index's rules, and recovery states where main already
carries a release that never published. Almost none of that world
executed before the release itself was the test. The isolated-leg
machinery existed, but it inherited the first world's environment
(its PATH, its pytest configuration, its dev-act wheels), so it
validated a hybrid that exists nowhere.

The fixes moved the boundary: the legs are hermetic and
deterministic now, the release set rides an explicit manifest, and
the release act can be rehearsed from main before arming. The
remaining follow-ups are filed (#263 flakes, #267 speed, #270
token publishing, #273 act naming, #258 casts).

## The ledger

Each entry: the defect, the fix, the pin, and why local was blind.

1. **toolroom's floor leg failed wholesale.** Its suite drives
   footman (the hosted seam, the machinery, the playground), and a
   leg installs only the wheel, its floors, and the toolchain. Fix:
   a package may declare a `test` extra and the legs install the
   wheel with it; toolroom names livery-footman, its own shim, the
   footman shim, and mkdocs; footman's extra names the toolroom
   side and its own shim. Pinned in the driver tests. Local was
   blind because the legs had never run against a suite with
   optional-host seams, and the workspace gate always had every
   member importable.

2. **`describe_distance` crashed on a package with no tag.** The
   promised no-tag fallback sat behind a `git describe` that
   refuses rather than answering empty; a first release is exactly
   the no-tag state. Fix: catch the refusal, count the whole
   history. Pinned. The untested-fallback rule named this failure
   mode in advance; the fallback had simply never been forced.

3. **First releases derived v0.0.0.** cliff anchors on
   `initial_tag`, and migrated lines have no tags here. Fix: the
   contract's `[release] baseline`, rendered into the cliff
   config; the first release lands at the baseline. Caught by the
   local rehearsal, to its credit. Re-ruled the same day: the shim
   and its real package are identical releases and share one
   number, so all four baselines name 0.6.2 and 0.52.2, one past
   the numbers the index twins burned.

4. **The legs inherited the workspace.** Three separate leaks: the
   workspace venv on PATH (a click tool looked installed to a leg
   whose python could not introspect it), the ambient coverage
   variables (re-pointing reads at the live gate's data), and
   pytest's rootdir walk finding the workspace pyproject (xdist
   workers and worksteal schedules varying the verdict run to
   run). Fixes: `_leg_env` (own venv leads, workspace venv dropped
   by path segment, coverage scrubbed) and `-o addopts=`. Pinned.
   Local was blind because the leak made local and CI legs agree
   whenever the workspace venv supplied what the leg lacked, and
   the nondeterminism read as unrelated flakes.

5. **tool-history was not in the wheel.** Phase 1's record said
   package data; it sat outside `src/`, and the installed anchor
   pointed at nothing. Fix: the readings live at
   `_machinery/_history` inside the package; the changelog anchor
   stays a checkout fact. The suite in a faithful leg went from
   eleven failures to zero. Local was blind because nothing ran
   the suite against an installed copy.

6. **footman's api generator read a checkout path**, and
   `__version__` plus the json example lagged the stamped version.
   Fix: the generator reads the `__init__` beside itself, true in
   both layouts; the literals stamped. Found by the first true
   release-act rehearsal from main.

7. **The shims declared no literal `__version__`.** The wave's
   verifier demands the released version in the released package.
   Fix: literals in both shims; the shim contract keeps identity
   per name and takes equality for the one literal. Local was
   blind because the verifier runs only in the publish wave.

8. **The emitted title check spoke the wrong grammar.**
   `--title "$TITLE"` with a space reads as a task name in
   footman's grammar; no release PR could merge. Fix: the attached
   `--title=` form, and (ruled by Willem live) the check matrix
   chained behind the 14-second title job so a red title cancels
   the run. A second layer followed: GitHub propagates a skip
   through default conditions transitively, so the docs job needed
   its explicit condition. Local was blind because emitted YAML
   only executes in CI, and this was the first release PR ever to
   reach it.

9. **A re-prepared release vanished from its own discovery.** The
   wave and the title check discovered members from the squash's
   changed changelogs; a recovery re-prepare restamps
   byte-identical content and touches none, and the old squash
   could not publish either, because one refused member fails the
   wave's up-front verify whole. Fix, the structural one: prepare
   writes `.release-manifest.json` naming every member and
   version; discovery and the title check read it at the ref, the
   diff stays as the legacy fallback; and a member whose stamp
   lands clean is recovery, not a fault. Pinned end to end.

10. **The dev act impersonated the release act.** Every early
    "rehearsal" ran `--local` from a fix branch, which is the dev
    act by design (the branch decides the act): dev wheels one past
    the current release, current floors, coherently green, and not
    the release's world. The release act was first executed by the
    armed runs. Process fix: rehearse from main before arming;
    train fix filed (#273): the report should name its act.

11. **Operational faults, mine.** Reading a run that died at
    prepare as "legs green"; twice branching off the release
    branch the armed run leaves the checkout on; a short-sha
    dispatch fired beside the full-sha one; and the session shell's
    VIRTUAL_ENV/PATH resolving checkers against the wrong venv
    until pointed explicitly. The last is a standing trap now
    documented; the rest are wait-amplified versions of not
    checking where a process actually stopped.

## What the afternoon bought

The two-world boundary is now instrumented: hermetic deterministic
legs, test extras as a declared seam, package data honestly in
wheels, a manifest-carried release set, reachable fallbacks, and a
release act that can be rehearsed before it is armed. The second
release through this train inherits all of it.
