# The execution model — the build (order of battle)

**Status: BUILT — shipped in 0.28.0 (2026-08-01), the target release,
as one coherent wave.** The spec it implements is
[20260731-execution-model-spec.md](20260731-execution-model-spec.md); the public
contract is docs/design.md.

Reconciled against the code 2026-08-19, stage by stage: `recorded=` is
on `run()` and `step=` is gone (A); `@pre_record` attaches a reviewer
whose `ResultView` carries `title`/`code`/`ok`/`set_returned` (B);
`pre_task`/`post_task`/`pre_bind` are on the handles (C); `step()` is
public (D); `parallel()` returns Results and `--json` emits the flat
`items` list under schema 1 (E, F); `lane()` ships with `cwd_lane` and
`console_lane` dogfooding it (G). The design page's three examples were
run as real code: the reviewer sets a verdict, `run(…, recorded=False)`
stays off the record, and `parallel()` refuses a bare callable with the
taught message while accepting `step(fn, title=…)`.

**Correction to this note's own ratchet.** It claimed every
`<!-- example: fragment -->` on docs/design.md "marks surface this build
has not shipped yet", making un-marking the definition of done. That
conflated two meanings. The marker belongs to the docs-examples harness
and means "this block is an illustration, do not execute it as part of
the page's session" — a stub excerpt, a snippet that needs a git repo, a
block whose whole point is that it raises. There are 46 of them across
17 pages; design.md's 3 are ordinary. They are HTML comments, invisible
on the published page, and removing them would make the harness try to
execute a snippet containing `shutil.rmtree`. Stage H's "the design
page's fragments all unmarked" was therefore never achievable, and its
absence is not outstanding work.

Stages in dependency order; each lands through its own PR(s) with the
gate green, breaking changes in the CHANGELOG as they land:

- **A — the honest verbs** (no model machinery; lands on today's
  runtime). `fail(code=0)` becomes a taught error everywhere (the
  executor's verbatim honouring was drift; the pass-branch spelling
  was rejected in the task-failure design). `step=` → `recorded=` on
  `run()` and the tools bridge (off the record — the record-family
  spelling; ruled with decision 6, no shim).
- **B — the record surface.** The step draft (`ResultView` for steps),
  `pre_record` (`.opts()` on tools and steps; stacked `@pre_record`),
  review rules (sees what was captured; sets title/code, `ok` derives;
  a raising reviewer fails the item; inside-out order, `.opts` last);
  the audit backbone on records (`(moment, actor, code)` entries) with
  `failed_at` as a derived reading. hse's djlint gate becomes the
  one-liner.
- **C — hooks on handles, observation goes static.** The task trio
  (`pre_task`/`pre_record`/`post_task` + `pre_bind`, wrap sugar) and
  the step pair (`pre_record`/`post_step`) on their handles; global
  `post_task` narrowed to read-only (`set_returned` moves into the
  review window — breaking); observers hold the sealed `Result`;
  veto-never-forge with the never-code-0 guard; lifecycle-tagged
  failures everywhere.
- **D — the substrate.** One node kind underneath; `step()` in its
  three positions (build-not-run makers, the with-block, the wrap);
  the generator pump (checkpoints; the three cancellation reasons);
  the ban (`parallel()`/`run()` refuse bare callables, `p.also` and
  `run(callable)` retire — breaking); the fan-out as an anonymous
  parent.
- **E — identity and addresses.** Resolved arguments join the plan
  key (divergent-forwarding `ChainError` retires — divergence makes
  two nodes; breaking); tree-derived deterministic addresses on every
  record.
- **F — the report.** The flat items list under schema 1 (replaces
  rows-with-steps — breaking, no consumers); `parallel()` returns
  Results; display defaults (task grain at normal verbosity, `-v`
  adds steps, a failed task always expands its failing step and audit
  line); `recording()` reads the new shape.
- **G — lanes.** `lane()` (handles, collision refusal with
  provenance), `cwd_lane` + `console_lane` dogfooding it, one holder
  per lane, `serial=` as sugar for both, boundary-atomic claims wired
  through the pump.
- **H — the docs sweep and the release.** Timeless rewrite of every
  page the model touches (orchestration, tools, tools-bridge, json,
  testing, pipelines); the design page's fragments all unmarked; the
  loom's restated stubs traded for real imports or surfaced as
  contradictions; 0.28.0 cut; hse migrates; the context-free audit
  runs against docs/design.md.

Stage order is dependency, not ceremony: B needs A's spelling; C needs
B's review window; D needs C's hook surface to attach to; E and F need
D's one node kind; G needs D's pump; H needs everything. Within a
stage, smallest honest PRs win.
