# The GitHub lane: the example fleet as the hosted forges' proof

Status: written 2026-10-17. Nothing is built. Willem ruled the same
day that the GitHub lane is the third piece of the current run, after
one follow() for every wait on the forge (#1738, merged as
`9639c2716`) and the loop proving its scenarios from rows (#1740), and
that work merges on green and releases where progress needs it. Every
design choice below that he has not ruled is marked as mine, for him
to rule phase by phase. This note takes the fleet of #811 as its
subject and narrows it to github.com first; the other two hosted
forges follow with the same verbs in phase 6.

## The prompt (Willem)

> How would you propose we improve the github lane? There is a plan to
> create a bunch of example projects, with their own ci, that
> continuously update to the latest release. Willem, 2026-10-17

> Keep going until told otherwise, you can merge on green with good
> coverage, and release if it's required to progress. Willem,
> 2026-10-17

The intent, from #811 (Willem, 2026-10-06): example repositories born
from `fm new.project`, one per shape, on each hosted forge, kept
current by a scheduled `fm workflow.update`, releasing to their own
repository's releases, so the workshop is proved not tied to one forge
and a consumer installing what an index serves is proved to work, cold
and for real.

## What exists

Measured on `origin/main` at `849b3d388`, 2026-10-17.

- The local loop (`fm ci.e2e`) proves Gitea and GitLab against the
  containers and the host rig; it never runs GitHub, since no GitHub
  runs locally. GitHub is proved by this repository's own CI alone.
- `fm new.project --forge=github --owner=<org>` is W1 on GitHub: it
  creates the repository, configures protection and the merge policy,
  pushes, opens the unarmed setup pull request, and `--resume` finishes
  a birth that stopped. `fm workflow.update` is W7: the lock and the
  floors brought current, the files the new wheels compose written,
  one pull request, armed on `--armed`.
- A contract's `[[ci.schedule]]` entry attaches a task to a point's job
  (`point`, `task`, `job`, `args`, `every`, `once`). The nightly point
  runs the schedule on the forge's clock.
- A package's `[release] publish = false` keeps the train from
  uploading it: the package is still versioned, built and tagged.
- The forge's job token starts no workflow for a pull request it opens;
  the rendered nightly carries `FORGE_TOKEN: ${{ secrets.FORGE_TOKEN ||
  secrets.GITHUB_TOKEN }}` for that reason, and the token ladder reads
  `FORGE_TOKEN__GITHUB_COM` then `FORGE_TOKEN`
  (`livery.workshop._tokens.forge_token`).
- The accounts of the runbook (`notes/20260831-e2e-accounts-runbook.md`)
  exist: the GitHub organisation `livery-forge-e2e` with a classic PAT
  (`repo`, `workflow`, `delete_repo`) stored on this repository as
  `LIVERY_E2E_GITHUB_TOKEN`, with `LIVERY_E2E_GITHUB_OWNER`; a
  gitlab.com group and a gitea.com organisation with theirs. Nothing
  reads them. #811 rules that the permanent examples live in their own
  organisation, never in `livery-forge-e2e`, which is scratch.
- The loop's proofs read the rows the legs write (#1740): a run's
  metrics row, the verified series, a base's coverage record, the
  marks. The readers (`livery.workshop._e2e_rows`) are forge-neutral.
- Releases as a target (#810, `notes/20260927-releases-as-a-target.md`)
  is not started; #811's first shape, a python package published to
  its repository's releases, waits on its phase 2.
- The CI render pins every action it uses to a commit, by forge
  (`livery.workshop._ci_generate.ACTIONS`), and hands the declared
  secrets to the entry step for `fm env.emit --github` to write into
  the shared slot (#1778). It still privileges GitHub in one way the
  fleet would carry into every example: the pages deploy and the token
  fallback render for GitHub alone.

## Ground-truth contracts (do not violate)

1. **Nothing gates a release on the fleet.** It consumes what a
   release already published; a red example files an issue here.
   (#811.)
2. **An example is born and kept by verbs, from released wheels.**
   `fm new.project` births it, `fm workflow.update` keeps it current
   through its own gate, `fm fleet.assert` asserts the fleet; no hand
   edit, no source install.
3. **Every proof reads outcomes.** Store rows, run conclusions,
   receipts; never a job's log line (ruled 2026-10-15).
4. **One identity per job.** An update pull request is opened by an
   identity whose token starts workflows; the token that creates and
   deletes repositories is held by the job that governs the fleet and
   by no other (ruled 2026-10-11, #1280).
5. **The fleet is configuration, asserted by one idempotent verb.**
   Re-running it is the recovery (W9); the forge is the state, nothing
   is kept locally.
6. **No logic in YAML.** An example's CI files are the workshop's
   render; a decision the lane needs is a verb (#286).
7. **Every forge touch goes through `livery.forge`.** A call outside it
   is a hole in the protocol (the workflows note's coverage rule).
8. **A red example files one issue.** `issue.create` probes by marker
   before it creates (W11); a second red run of the same cause files
   nothing.
9. **Until #810's phase 2 lands, an example publishes nothing.** Its
   packages carry `[release] publish = false`; a python example on
   github.com would otherwise resolve to pypi.org.

## The design

**The fleet.** A declaration of what the fleet is: one example per
template the installed extensions declare (`python-library`,
`python-cli`, `cpp-package` for cmake and conan, `nanobind-module`,
`workshop-extension`), plus the stranger, born with the stock list and
nothing else. Each names its forge, its organisation and its template;
every other fact comes from the template. Mine: the declaration lives
in this repository's `workshop.toml` under a table the e2e plugin
declares, since the fleet serves this repository's proof and publishes
nothing, the way `_e2e` does; phase 15 moves both out of the base
together. Open item 1 holds the alternative.

**The verbs.** `fm fleet.assert` births every declared example that is
missing on its forge, from the released wheels the resolver serves
(`uv tool run` of the released workshop, never this checkout), sets the
example's `FORGE_TOKEN` secret from the fleet identity, and says what
it did and what already stood. `fm fleet.status` prints one line per
example: born or not, the workshop version its lock pins, its last
update pull request and that run's conclusion, its main's verified row.
`fm fleet.scratch` births a throwaway in `livery-forge-e2e`, proves W1
from rows, and deletes it. The verbs are the loop's siblings: same
module home, same readers.

**The update wave.** Each example's contract carries
`[[ci.schedule]] point = "nightly", task = "workflow.update",
args = ["--armed"]`, so a workshop release reaches it within a day with
no dispatch from here. The pull request it opens runs CI because
`FORGE_TOKEN` is the fleet identity's, not the job's. A conflicting
update stays open unarmed with its evidence (W7); `fleet.status` shows
it.

**The identity.** Mine: a machine user of the fleet organisation with a
fine-grained token scoped to it, stored on this repository as
`LIVERY_FLEET_GITHUB_TOKEN` and set on each example as `FORGE_TOKEN`
by `fleet.assert`. A GitHub App is the durable form (short-lived
tokens, per-repository grants, an attributable author); open item 2.

**What a proof reads.** Birth: main's push run after the setup squash
has a verified row naming the setup branch and a check leg scope of
`verified` (the loop's verified-skip proof, through
`livery.workshop._e2e_rows`). Update: the pull request's run is green,
its merge landed, the lock on main pins the released version. Status:
the rows above, read from each example's origin.

**The red path.** `fleet.status` runs on this repository's nightly
after the examples' own nightlies; an example whose main is red, whose
update stayed open past a day, or whose lock lags a release by more
than a day files one issue here, with a marker the next run searches
before it creates.

**Self-release.** Off until #810's phase 2 (`publish = false` in every
example package); on when it lands, with #811's content comparison
across forges in phase 7.

## Phases

Each phase lands gate-green and mergeable alone. Exceptional paths are
tested before happy paths.

### Phase 1: the declaration, the status verb, and the render's asymmetries

Deliverables: the fleet table and its schema fragment; `fm fleet.status`
over the declared examples, printing "not born" for each today; Gitea's
actions pinned by commit like GitHub's, in one pin table keyed by forge
(`livery.workshop._ci_generate.ACTIONS`); the secrets handed to the
entry step's environment as `RUNG_<KEY>` and written into the shared
slot by `fm env.emit --github`, so no job carries a rung step or a
per-secret shell line. The matrix pick by event stays as rendered data:
the contract's two runner lists and which event takes which (decision
record, 2026-10-17). Acceptance, refusals first:

- `test_a_fleet_entry_naming_an_unknown_template_refuses_naming_the_installed_ones`,
  `test_a_fleet_entry_naming_an_unknown_forge_refuses`.
- `fm fleet.status` exits 0 and names each declared example as not born.
- `test_every_action_a_rendered_file_uses_is_pinned_to_a_commit`;
  `fm sync` renders this repository's CI files with no `@v4` and no
  `if [ -n "$RUNG_` line; `git diff --exit-code` after a second sync.
- The loop's develop set passes (`fm ci.e2e --scenario=develop`).

### Phase 2: birth on github.com

Deliverables: the fleet organisation and its identity (a one-time step
in the runbook, recorded there); `fm fleet.assert` birthing each
missing example from released wheels, setting `FORGE_TOKEN`, writing
the update schedule and `publish = false` into the newborn's contracts
through `new.project`'s own options, and walking past what stands.
Acceptance, refusals first:

- `test_fleet_assert_refuses_without_the_fleet_identity_naming_the_variable`,
  `test_fleet_assert_births_nothing_when_every_example_stands`.
- Six repositories exist in the fleet organisation, each main green,
  each with a verified row (`fm fleet.status` prints the six, born).
- `fm fleet.assert` a second time births nothing and says so; a
  repository deleted by hand is born again on the next run.

### Phase 3: the update wave

Deliverables: the proof that a release reaches the fleet: after the
next workshop release, each example's nightly opens an update pull
request the fleet identity's token lets CI run, arms it, and it merges;
`fleet.status` shows the version. Acceptance:

- `test_an_update_pull_request_opened_with_the_job_token_is_named_as_one_ci_cannot_start`
  (a unit test over the status reader: a pull request with no run
  after its open is reported as such, not as in flight).
- One day after a release, every example's lock pins it, by
  `fm fleet.status`.
- A conflicting update stays open unarmed, named, with its evidence in
  the pull request.

### Phase 4: the scratch birth

Deliverables: `fm fleet.scratch` and a weekly `[[ci.schedule]]` entry on
this repository's nightly: born in `livery-forge-e2e`, proved from rows,
deleted whatever happened. Acceptance, refusals first:

- `test_a_scratch_birth_whose_setup_run_leaves_no_verified_row_fails_naming_the_series`.
- `fm fleet.scratch` by hand births, proves and deletes; the
  organisation holds no repository after it.
- The entry runs once a week and its run is green.

### Phase 5: the red path

Deliverables: `fleet.status` on this repository's nightly, filing one
issue per red example with a marker; `issue.create` gains the probe
W11 designed (`--once=<marker>`: search before create, file nothing
when the marker is found). Acceptance, refusals first:

- `test_issue_create_once_files_nothing_when_the_marker_is_found`.
- A forced red example files one issue; a second run files none; a
  green example files none.

### Phase 6: gitlab.com and gitea.com

Deliverables: the same declaration and verbs over the other two hosted
forges, the gitea.com runner booted per cycle as #811 describes, and
#811's drift check across a triple. Acceptance: #811's.

### Phase 7: self-release and the content comparison

After #810's phase 2: `publish = false` goes from the examples, each
releases to its repository's releases, and #811's wheel-content
comparison across forges runs. Acceptance: #811's.

## Temporary, replaced by

| Temporary | Replaced by |
| --- | --- |
| `[release] publish = false` in every example package | #810 phase 2, and this plan's phase 7 |
| the fleet identity as a machine user's token | a GitHub App, when open item 2 rules it |
| the loop's three remaining log reads (the publish job's `prebuilt:` line; the nightly's and a contributed point's task) | conan's per-host artifact (phase 13 of the destination note), and #1748 |
| GitHub proved by this repository's CI alone | phases 2 to 4 |

## Decision record

- 2026-10-17: this note written, against `origin/main` at `849b3d388`.
- Willem, 2026-10-17, by intent: the GitHub lane is the third piece
  after one follow() and the loop on rows; keep going, merge on green
  with good coverage, release where progress needs it.
- Mine, 2026-10-17: the matrix pick by event stays in the rendered
  file. A verb in its place needs an entered job before every check
  leg on every run, and the entry alone measures 11 s to 28 s per job
  (`fm ci.timings`, this repository). The file carries the contract's
  two runner lists and which event takes which; the decision is the
  contract's. Reversed the day a legs job can enter for free.
- Mine, 2026-10-17, for Willem to rule: the fleet is the e2e tooling's,
  declared in this repository's contract (open item 1); github.com
  first, the other two in phase 6; a machine user's token before an
  App (open item 2); the examples publish nothing until #810's phase 2
  (contract 9).

## Open

1. **Where the fleet is declared.** Owner: Willem. (a) A table of this
   repository's `workshop.toml` the e2e plugin declares, recommended:
   the fleet serves this repository's proof and publishes nothing,
   and phase 15 moves the plugin out of the base with the loop. Not
   covered: a second workspace wanting a fleet of its own. (b) A
   workspace extension of its own, `livery-extensions-fleet`: any
   workspace declares one; costs a distribution, a release and a
   docs page for one user.
2. **The fleet identity.** Owner: Willem. (a) A machine user with a
   fine-grained token scoped to the fleet organisation, recommended
   first: one secret, the shape the runbook already uses. Not covered:
   token expiry and rotation. (b) A GitHub App: short-lived tokens,
   per-repository grants, an attributable author; costs the App's
   setup and a minting step in the verbs.
3. **The scratch birth's job and token.** Owner: Willem. The e2e token
   creates and deletes in `livery-forge-e2e`, an organisation of its
   own, not this repository's admin token. Recommended: the nightly's
   own job holds it as the scratch birth's only token; no job holds it
   beside this repository's admin token.
4. **The GitHub lane in the loop's vocabulary.** Owner: me. Whether
   `fm ci.e2e --forge=github` names the fleet verbs, so one command
   drives the three lanes, or the fleet keeps its own family. Decided
   with phase 2.
5. **A status page on the site.** Owner: Willem. Whether
   `fleet.status`'s lines become a page of this repository's site,
   drawn by the docs extension from the same readers.
