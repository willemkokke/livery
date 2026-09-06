# Docs restructure — the split, the ruled vocabulary, the composability story

**Status: LANDED, 2026-08-06 — all eight steps merged the same day.** The
note itself was step 1 (#323); then the correctness pass (#324), the
`positional` rename with manifest schema 3 (#325), the generated API page —
whose first refusal exported the forgotten `mark` (#326), the Basics/In
depth restructure with execution-model.md and hooks.md (#327), the
nomenclature sweeps and glossary rewrite (#328), the voice pass (#329),
design.md's front-door growth (#330), and the seven drift guards (#331).
Still open beyond this repo: the toolroom queue (colour table page,
objects.inv exchange) and a release wrapping the src rename, on Willem's
call. Reviews ran 2026-08-05; the 24 rulings below were made one by one on
2026-08-05/06. Related notes: [20260725-foundations-plan.md](20260725-foundations-plan.md)
(the Foundations section this plan leaves untouched),
[20260805-structured-returns.md](20260805-structured-returns.md) and
[20260731-execution-model-spec.md](20260731-execution-model-spec.md) (the era whose
vocabulary this plan finishes rolling out).

## The brief

Willem, 2026-08-05, condensed: the docs voice "still isn't as natural as I'd
like"; beginners should get a basics path usable "without knowing any of the
advanced stuff"; advanced topics must stack in a logical order; the docs
should show that "careful design and not compromising early on design
decisions allows for a very composable design where the complexity stacks";
gather the design principles stated across development and the designs that
fell out of them; run the usual correctness/missing-features pass; and rule
on nomenclature **before any modification**.

Four parallel review passes ran over all 45 authored pages, all 28 notes,
and the source. Their findings are recorded below, then the archaeology,
then the sequence. Nothing was edited before the rulings closed.

## The ruling ledger

Each ruling is Willem's, made in sequence. The voice pass and sweeps cite
this ledger as law.

1. **thunk — retired.** The glossary defined it as "a lambda or
   functools.partial that binds a task's arguments" — the exact idiom 0.28
   turned into a taught refusal (`context.py` `parallel()` accepts tasks,
   step items, step makers). Current vocabulary: *task / step item / step
   maker / owned call*. The word survives only in source internals
   (`_run_thunks`).
2. **wrapper verb — deleted from the glossary.** Zero uses in docs/README;
   it fired only on CHANGELOG text. The glossary lists living vocabulary
   only.
3. **Tooltip inflections — added.** The abbr extension matches exact
   spellings; glossary keys gain inflected variants (*chains*, *chained*,
   *serialises*, …) plus a drift test asserting inflected entries stay
   textually identical to their singular. "We can always undo it."
   **Amendment, 2026-08-07 — and undone for one stem.** *serialise /
   serialises / serialised* are removed; the bare adjective *serial*
   stays. An inflection is only worth a tooltip if the spelling means
   footman's thing wherever it appears, and this verb has an older, more
   common English sense the docs themselves use more often: of twelve
   prose fires, nine were the JSON sense or a whole-run one, two were
   right in sentences that already said "lane", and two landed on
   sentences about a *sequential run* — which the definition explicitly
   contrasts itself with. `serial task` / `serial lane` / `serial=True`
   have no such twin, so the adjective keeps its entry. **The rule this
   yields: an inflection earns a tooltip only where the word has one
   meaning in English, not merely one meaning in footman.** No guard
   follows — the failure is a wrong *sense*, and no test reads sense.
4. **Glossary membership.** Add six: *context, refusal, envelope, receipt,
   lane, shared*. First-use inline instead for seven: *step, record,
   verdict, reviewer, sealed, audit, observer* — step/record because ~91
   occurrences each would underline the site (and "record" the verb would
   catch tooltips), the verdict family because it clusters on few pages.
   **arbiter — retired from docs prose entirely**: the scheduler/arbiter
   split (DAG ordering in `_schedule.py` vs the claims predicate in
   `_globals.py`) is real in source but never load-bearing for a reader;
   one actor, one word — *the scheduler*. The glossary's `serial` entry is
   reworded to drop it.
5. **surface (group-member sense) — renamed.** "A runnable group's
   surfaces" becomes plain words: *the group's tasks / its members*. The
   generic senses ("machine surface", "product surface") stay.
6. **lane — reserved for resource claims.** The tool-call execution sense
   becomes *path* (in-process path / subprocess path); the hook attachment
   sense becomes *channel* (testing.md already said channel).
7. **ladder — precedence-shaped only** (cwd resolution, configuration,
   regimes). The hook lifecycle is a sequence, not a precedence: *the hook
   order* / *the request's lifecycle*.
8. **gate — bare form means the check command.** Every other sense is
   always qualified: *availability gate*, *confirm gate*, *stop-gate*.
9. **hook — qualified at first use per page** (*completion hook*,
   *lifecycle hook*, *agent hook*), bare thereafter within the page.
10. **address — qualified per page**: *task address* (dotted CLI spelling)
    vs *request address* (report path, `check/git#2`). The JSON keys
    (`task`, `address`) stay as they are — coherent already.
11. **environment — bare means the process environment.** The interpreter
    sense is always qualified (*interpreter environment*, *the project's
    environment*).
12. **default — bare means the parameter sense.** The group sense is
    qualified (*default action*, *group default*), first-use-per-page.
13. **Six collapses, all accepted:** folder → *directory*;
    duration history → *timing history*; collector names → *the cache
    collector*; the Tab keypress → rendered keycap in prose, `<TAB>` only
    inside console fences; "first-class" → rewritten (4 uses); and
    **positional everywhere including the machine**: the JSON catalog kind
    `"argument"` renames to `"positional"` and the taught error text
    aligns — a small src PR of its own.
14. **Register ratified** — flag = boolean, option = value-bearing, switch
    only for `--x/--no-x` pairs; parameter = declared, argument = supplied;
    colour in prose / color in machine spellings; *rehearsal* welded to
    `--dry-run`; the metaphor register stands (honest, loud/silent, taught,
    stranger/owned, world in Foundations, *spelling* as the house idiom for
    an API form, blessed/sugar/graft/splat sparing). *chrome* retired;
    "turtles" cut (rewrite keeps the regress explicit). **Amendment:
    sentence-initial "Footman" takes a capital**; lowercase everywhere
    mid-sentence; "Footman" as site title.
15. **The three-register rule is law.** Notes-tier vocabulary (work item,
    grain, loom, finalize, provision, prereq, command tree, PTY) has zero
    docs occurrences today and any future occurrence is a defect. Tiers:
    notes-tier (free in notes/), docs-tier (this ledger), source-tier
    (internals — free in code, banned in prose).
16. **Nav shape: one Guide tab, two sidebar sections** — **Basics** and
    **In depth** — with cookbook as the bridge between them. No tenth tab.
17. **Composability story: design.md grows + built-on openers.** design.md
    absorbs the missing older principles and stays the single capstone
    essay; every In-depth page opens by naming the decision it rides (the
    working-dir.md pattern); the seven missed-chance pages each get their
    connecting sentence. No separate essay page.
18. **Splits and moves bundle — accepted in full.** Details under
    "Restructure" below.
19. **color-support.md — the table follows its data to toolroom.** The page
    is deleted here; taskdocs.md's link repoints or drops; toolroom's docs
    gain the regenerated table (queued in that repo).
20. **CHANGELOG 0.28.0 amended.** The entry claimed `--json` steps carry
    `work_code`; that key never shipped (it is a `Result` property only).
    The entry is corrected to what shipped.
21. **api.md is generated.** Generator in the docs build; input is the
    typechecked `TYPE_CHECKING` export table; an ordered grouping
    declaration carries sections and intro prose; the build fails naming
    the name on divergence in either direction; deliberate omissions only
    via an explicit exemption list. Riders: public-path directive spike
    (`::: footman.run`) first, backlinks toggled on, toolroom inventory
    cross-linking queued with 19.
22. **Docstrings stay plain.** No cross-reference syntax in source
    docstrings; raw readability (help(), hover) wins; revisitable if the
    generated page feels under-linked once backlinks land.
23. **Drift-guard bundle — accepted**: execute the docs' Python envelope
    examples; nav-orphan check; link+anchor guard; config-key parity vs
    `_config.py`; exit-code prose guard tied to `EX_USAGE`; glossary
    inflection-consistency; RST guard on our own docstrings. Deliberately
    skipped: a general prose-truth guard, and gating the benchmark numbers.
24. **Sequencing — the eight steps at the bottom, in order.**

## Standing principle recorded this session

> "History is always secondary, clear overview of the current state is what
> we're aiming for. We have everything in place for a good backwards
> compatibility story, but we won't use it until we have external users."

Applied here to: the JSON kind rename (13), the CHANGELOG amendment (20),
and api anchor/inventory path changes (21). Until external users exist, the
rename/fix beats the compatibility shim, and neither the json.md stability
promise nor changelog immutability blocks a correction.

## Findings A — voice

The register is largely already the target (the index/getting-started
voice: direct, concrete, second person, short declaratives with
personality). Exactly **one** banned-jargon hit in 45 pages: "seamlessly"
(composing.md). No leverage/utilize/delve/synergy/robust/etc. anywhere.
The real patterns:

1. **Compression overshoot** — mechanism + rationale + edge case folded
   into one em-dash chain until a sentence needs two reads. Concentrated:
   composing.md (the hook half), json.md (the items-envelope section is a
   ~40-line paragraph doing a field table's job), orchestration.md from
   `.opts()` onward, foundations-regimes ("one atomic predicate" on a
   from-zero page), progress.md. Fix direction, by example: "An opted
   reference with a *different* policy is a distinct prerequisite from a
   bare one — …" → "Same policy: one node. Different policy: two."
2. **Abstract openers** — agents.md, pipelines.md, composing.md open with
   apposition lists about footman instead of a command doing something.
3. **Verbatim duplication** — six page-pairs share copied paragraphs
   (progress↔cookbook, ci↔progress, input↔pipelines — each calling the
   *other* "the full story" — orchestration↔design, typing↔completion,
   plus typing's help-note re-explained in reference.md). The restructure
   gives each fact one owner.
4. **Minimizers** — eight "simply/just" uses to trim; "easily" already at
   zero. "first-class" ×4 rewritten per ruling 13.

Most needing rewrite: composing.md, json.md, orchestration.md (back half).
The calibration pages to spread: troubleshooting.md, testing.md, the
Foundations series, working-dir.md (whose opener derives the whole page
from one premise — the model for built-on openers).

## Findings B — audience and restructure

Two ordering bugs drove the design: **run() is taught on the 6th Guide page
but used from the 2nd onward**, and **profiling.md (advanced) sat
mid-basics**. Worst leak: getting-started's 23-line uv-handoff block on
page two (lockfile semantics, PEP 723, `uv = false`, `FOOTMAN_NO_UV`)
before the reader has written a task. Other leaks: getting-started's
dry-run section using steps/receipts vocabulary unmet; typing.md's forward
paragraph leaning on the dispatch graph; tools.md's contextvars escape
hatch on the beginner run() page; playground's bare "manifest walk";
progress.md's request enumeration ahead of the vocabulary.

**The ruled shape** (rulings 16, 18):

- Guide tab, section **Basics**: getting-started (uv block demoted to two
  sentences + cookbook link; dry-run trimmed of record vocabulary) →
  playground → typing (minus forward; keeps the alias table) → **tools**
  (moved up; contextvars paragraph moves to working-dir) → orchestration
  front half (chaining, parallel-by-default, failing, pre/post, forward,
  parallel() basics, runnable groups) → input → progress → monorepos →
  testing. A reader stopping here has never met lanes, hooks, records,
  `--describe`, or plugin authoring.
- **Cookbook** sits at the section boundary as the bridge; stays one mixed
  ramp by design.
- Section **In depth**, each page opening with the decision it rides:
  working-dir (after Foundations, its declared prerequisite) → **The
  execution model** (new page: orchestration's back half — one execution
  per request, `shared=False`, steps, calls bind like segments, mid-run
  tasks; deletes the design.md duplication) → **Composing tasks** (the
  intermediate half: hidden/omitted/disabled, include(), plugin()
  consumption) → **Hooks & plugin options** (new page: the lifecycle hook
  family, wrap_*, handle hooks, GlobalOption, env_files, the caching
  contract) → plugins → taskdocs → custom-cli → design.md as capstone.
- **Machine use** reorders to pipelines → json → ci → agents; profiling
  moves here after json. stdin gets one owner (pipelines; input keeps a
  five-line pointer); ci.md's duplicated paragraphs become links.
- Foundations and the per-shell completion pages: untouched — both already
  the right shape.

**Composability story** (ruling 17): design.md today tells the 0.28-era
story (identity, honesty, lanes, the report) and almost none of the older
load-bearing principles — zero deps, the import-free hot path, the manifest
doctrine, one-spelling, the lexical grammar, stdout/stderr, caller-
blindness, right-not-configurable, taught-errors-as-institution. design.md
absorbs those; the seven pages presenting for-free features as arbitrary
get their connecting sentence: progress (honest counting *because* nothing
runs anonymously), profiling (a consumer of the record model, not its
owner), json (the flat list *because* addresses are deterministic),
playground (real pytest in a browser *because* the in-process path is a
first-class twin), testing (isolated caches *because* manifests key per
directory), taskdocs (the branded CLI screenshots itself *because* App is a
library), orchestration (echo comparison.md's "the 4× gap is architecture").

## Findings C — nomenclature inventory (decision inputs)

Full sweep: ~150 terms across coined/CLI/systems/Python/feature categories;
the rulings above resolve everything contested. Kept here for the sweeps:

- Glossary before this plan: manifest, cascade, chain, taught error,
  fan-out, thunk (retired), passthrough, stale-while-revalidate,
  in-process, sequential, serial, wrapper verb (deleted). After: those ten
  plus context, refusal, envelope, receipt, lane, shared — with inflected
  keys throughout, and `serial` reworded to drop "arbiter".
- First-use family (defined where first met, no tooltip): step, record,
  verdict, reviewer, sealed, audit, observer.
- Confirmed-consistent, ratified: colour/color split (zero en-US leaks),
  parameter/argument discipline, rehearsal↔dry-run welding, the metaphor
  register (honesty ~30 uses is the master metaphor; loud/silent ~46;
  spelling ~30 as the house idiom for API form; world confined to
  Foundations).
- Notably absent (and now banned by rule 15): work item, finalize,
  provision, prereq ("prerequisite" always), env var ("environment
  variable" always), command tree ("task tree" always), task file ("tasks
  file" always), PTY ("pseudo-terminal"), butler/valet vocabulary (the
  domestic conceit lives in the name and the bell logo only).

## Findings D — correctness (all source-verified)

Confirmed errors, for the correctness PR:

1. **Refusal exit code "2"** survives at ci.md:76-82, troubleshooting.md:112
   (contradicting its own line 20), cookbook ×2 including a test example
   asserting `exit_code == 2`. Source: `EX_USAGE = 64`, `_app.py:92-97`.
2. **Pre-0.28 envelope stragglers**: agents.md:38's paste-ready snippet
   describes a nested `steps` key that does not exist (steps are sibling
   items); testing.md:151's golden test reads `t["steps"]` → KeyError.
   Both blocks only *define* functions, which is how the page-as-session
   harness missed them.
3. **run() callable regressions**: tools.md:18/30 ("a callable also
   works" — refused since 0.28), tools.md:31 (`.opts(nofail=True)` on
   run() — wrong channel; the spelling is `run(cmd, nofail=True)`),
   reference.md:91/94 (callable + "thunks"), testing.md:45 (the recording
   caveat describes the refused `run(fn)`), orchestration.md:156 ("a plain
   callable"; "`tools.*` entry point" is pre-split naming).
4. **The thunk cluster** (ruling 1): glossary entry + fan-out entry +
   orchestration.md:309/387 + cookbook teaching `parallel(partial(...))`
   three times and a lambda in `post=` — all refused at runtime.
5. **Pre-0.27 env model**: testing.md:56's `Context(env={"CI": "1"})` hands
   the task a one-variable environment (env is complete since 0.27, "not a
   diff"); honest spelling `{**os.environ, "CI": "1"}`. tools.md:37 still
   says "overlay".
6. **Pre-split leftovers**: migrating.md:28 concedes a tools library
   footman no longer ships; index.md:45 keeps "the tools wrappers" (README
   was fixed at the split, index missed); "tools bridge" naming in
   playground.md (whose own JS imports toolroom), foundations-spawning.md:41,
   working-dir.md prose.
7. **orchestration.md:176** lists `.opts()` as six options; `TaskOpts` has
   twelve (`registry.py:499-518`) and the same page uses
   `.opts(shared=False)` at line 641.
8. **CHANGELOG 0.28.0** `work_code` claim — amended per ruling 20.

Missing coverage: the `cascade` config key (`none|repo|filesystem`,
user-level-only) + `FOOTMAN_CASCADE` documented nowhere (configuration.md
promises "every key"); `Result.to_argv()` no handwritten mention;
`GlobalOption(bare=)` absent from plugins.md; `Result.timed_out`/exit-124
thin; api.md missing ~20 of the 0.28+ exports (superseded by ruling 21 —
generation makes the gap structural rather than patched).

Structural: color-support.md handled by ruling 19. The docs/tasks generator
never cleans, so local builds carry ghost pre-split tool pages (CI deploys
from a fresh checkout; the published site rebuilds clean — worth one check
that the post-split deploy ran: the published llms-full.txt should have no
`from footman import tools`). All 163 internal links resolve today.
README/index agree on pins and numbers.

RST audit (2026-08-05): zero .rst files of ours, zero directives/roles,
zero Sphinx fields in our own docstrings. The `:param` strings that exist
are the feature (docstrings.py parser + its fixtures + typing.md's
documented third style). One RST-ism: `_gc.py:8/13` double-backtick
literals — cleanup-pass item. `docstring_style = "google"` renders any
future violation visibly broken; the RST drift guard (ruling 23) makes it
structural.

## Findings E — api.md generation design (ruling 21)

State: api.md hand-lists 69 directives vs 84 `__all__` exports; the drift
test only word-searches the docs blob. The `TYPE_CHECKING` import block in
`__init__.py` is the typechecked name→defining-module map — a wrong path
there fails basedpyright, which is what "we know the public API because we
typecheck against it" cashes out to.

Design: a generator in the docs build (the `_generated/globals.md`
pattern). Input: the export table. The one human artifact: an ordered
grouping declaration (section → names, plus intro sentences). The build
fails naming the name on either-direction divergence; deliberate omissions
only via an explicit exemption list, empty by default. Module-shaped
exports (`docstrings`, `markdown`) render as member directives.

Riders, in order: (1) the **public-path spike** — confirm `::: footman.run`
renders and registers the public spelling in anchors and objects.inv
(griffe reads the TYPE_CHECKING aliases statically); fall back to
defining-path directives if not. (2) **Backlinks on** for the generated
page. (3) One mechanical check that mkdocstrings processes directives in a
build-time-generated page before choosing generated-into-place vs
included-body layout.

Environment facts (verified 2026-08-05): pinned zensical 0.0.50; latest
0.0.53 (2026-08-04) — deltas are autorefs/objects.inv caching across
rebuilds and search work, no semantic changes; upgrade is independent
housekeeping. Resolved handler stack is current (mkdocstrings 1.0.6,
mkdocstrings-python 2.0.5, mkdocs-autorefs 1.4.4): scoped/relative
crossrefs, backlinks, public filter, `__all__` ordering all present. The
built site already emits objects.inv and carries 308 autorefs links; the
native one-directive form (`::: footman` + public filter) was considered
and declined — alphabetical-by-kind loses the learning-order sections.
Ruling 22: source docstrings adopt no crossref syntax.

Toolroom queue (rulings 19 + 21): its docs gain the colour table
(regenerated from `_colordata.py`), and the two sites exchange objects.inv
inventories for two-way symbol links.

## The archaeology — principles

Mined from all 28 notes (2026-07-20 → 2026-08-05), CHANGELOG, design.md.
Format: statement — source note(s). "design.md: no/partly/yes" marks
whether the public essay tells it today; the "no/partly" rows are the
design.md growth list (ruling 17).

1. **Zero runtime dependencies** — no "just a small one" clause
   (20260726-process-boundary, 20260801-tools-namespace-package).
   design.md: no.
2. **The completion hot path never imports framework or user code** — every
   feature checked against the ~20 ms budget (20260726-plugin-architecture,
   20260731-execution-model-spec). design.md: no.
3. **The manifest is written from declarations, never executions** —
   runtime creation can name work in the report, never on the CLI
   (20260726-plugin-architecture, 20260731-execution-model-spec I9). design.md: no.
4. **One spelling per concept, everywhere** — dotted addressing set the
   precedent; contracts differ by name, never by a boolean on a shared name
   (20260725-dotted-addressing, 20260726-plugin-architecture D19/D22,
   20260727-incremental-caching). design.md: no.
5. **The typed signature is the whole contract** — both directions since
   0.31; no decorator, no schema language (20260805-structured-returns,
   20260731-execution-model-record). design.md: partly.
6. **Errors teach** — name the culprit, state the expectation, spell the
   fix; a refusal must never misdiagnose (20260725-dotted-addressing,
   20260726-plugin-architecture; CHANGELOG 0.5.0). design.md: partly.
7. **Parallel by default; the only non-parallel execution is declared**
   (20260725-process-globals pinned claim, 20260725-foundations-plan).
   design.md: partly.
8. **Resource claims are boundary-atomic, never mid-body** — deadlock-free
   by construction (20260725-process-globals v1 obituary,
   20260731-execution-model-spec I8). design.md: yes.
9. **footman must not know who is calling it** — enforced by a grep test;
   survived into toolroom unnamed (20260726-process-boundary,
   20260801-tools-namespace-package). design.md: no.
10. **No different rules for plugins and core** — core dogfoods its own
    extension points (20260726-plugin-architecture, 20260731-execution-model-spec).
    design.md: partly.
11. **Records are never fiction** — the forged receipt is unspellable, not
    discouraged (20260731-execution-model-spec I3, 20260731-execution-model-record).
    design.md: yes.
12. **Verdicts are decided in the open; observers may veto, never forge**
    (20260731-execution-model-spec). design.md: yes.
13. **A Result is its exit code** — the record subclasses int; later
    promotions stay non-breaking (20260731-execution-model-spec I2). design.md: yes.
14. **Model the lane, not the fix** — point-fixes accrue a bill;
    `step=False` → forged receipts is the canonical tale
    (20260731-execution-model-record). design.md: partly.
15. **Right, not configurable** — when a design change removes the need,
    the knob is never built (20260726-process-boundary, 20260801-ssh-man-stubs,
    20260729-env-handoff "`isolated=` is never built"). design.md: no.
16. **Pre-1.0 breaks freely but loudly; docs timeless; CHANGELOG owns the
    narrative** (20260726-process-boundary, 20260731-execution-model-record).
    design.md: partly. Extended this session by the history-secondary
    ruling above.
17. **The CLI grammar is deterministic and lexical — refusals over
    guessing** — no arity tables, values `=`-attached
    (20260726-plugin-architecture, 20260725-dotted-addressing). design.md: no.
18. **Measure before choosing; verify, don't remember**
    (20260720-tools-bridge-gaps "nothing here is from memory",
    20260726-tool-option-history, 20260729-process-globals-refinement).
    design.md: yes.
19. **Limitations are named, never hidden; a store never asserts what it
    didn't see** (20260726-tool-option-history, 20260723-color-support-plan,
    20260726-process-boundary). design.md: partly.
20. **Declaration is the commitment boundary** — declare, don't observe
    (20260731-execution-model-spec I13, 20260731-execution-model-record). design.md: yes.
21. **Handles, not strings** — a resource is a binding; a typo is a
    NameError (20260731-execution-model-spec). design.md: yes.
22. **Recorded by default; noise is a display problem, never a recording
    problem** — green is collapsible, failure is never hidden
    (20260731-execution-model-record). design.md: yes.
23. **No bare callables at footman's boundaries — limitations are a
    legitimate purchase** (20260731-execution-model-record, 20260731-execution-model-spec
    I12). design.md: yes.
24. **One identity rule everywhere; no declared-vs-dynamic difference**
    (20260726-plugin-architecture, 20260731-execution-model-spec I6). design.md: yes.
25. **An error must never read as an answer; cache keys fail toward the
    safe side** (20260726-tool-option-history, 20260727-incremental-caching).
    design.md: no.
26. **stdout is the answer, stderr is the commentary** (20260721-interactive-
    input, 20260726-process-boundary; CHANGELOG 0.11.0). design.md: no.
27. **A vocabulary register with tiers; plain words in public** — a
    notes-word in docs or an error is a bug (20260731-execution-model-record,
    20260727-incremental-caching "two axes, one word each"). design.md:
    partly. Ratified as law this session (ruling 15).
28. **Explicit beats implicit at every boundary; the caller names the
    shell** — auto-detect fails in the dangerous direction
    (20260803-tool-command-lines). design.md: yes.
29. **User-side coupling is the user's business; the line is drawn inside
    `src/footman/`** (20260726-process-boundary, 20260727-incremental-
    caching "the plugin owns the vocabulary, footman never learns the
    word"). design.md: no.
30. **Stubs suggest, never forbid; the tool is the only judge**
    (20260720-tools-bridge-gaps, 20260726-tool-option-history). design.md: no.
31. **Degradation loses speed, never width or correctness; exactness over
    approximation** (20260725-process-globals). design.md: no.
32. **Enforcement over convention** — invariants get structural tests or
    type-level seals; "declarations nothing checks become lies"
    (20260726-process-boundary, 20260731-execution-model-record,
    20260730-typing-citizenship). design.md: partly.
33. **Never hang, never silently proceed** (20260721-interactive-input,
    20260725-process-globals). design.md: no.
34. **The blessed idiom must be lint-clean in the user's repo** — why
    fail() is a function (20260724-task-failure). design.md: no.
35. **Dogfooding is the acceptance test** — "a real project runner could be
    built purely as plugins" (20260726-plugin-architecture,
    20260805-profile-plugin). design.md: no.
36. **Retire the category, not the instance** — one rule that closes a bug
    class, including for unknown tools (20260720-tools-bridge-gaps).
    design.md: partly.
37. **The process boundary is a total contract** — refusal ≠ failure ≠
    success; document vs empty stdout (20260726-process-boundary).
    design.md: no (lives in json/agents docs).
38. **Naming carries semantics** — mistakes get an `Error` suffix, chosen
    outcomes don't (20260724-task-failure). design.md: no.

## The archaeology — emergent compositions

The for-free features, each with the decision that carried it. The
strongest are the built-on openers and design.md exhibits (ruling 17).

1. Typed select menus fell out of `ask()` + the coercion pipeline —
   "static option sets are *free* the moment ask() exists"
   (20260721-interactive-input).
2. Group defaults gained positionals because dotted addressing deleted the
   ambiguity — "the rule dissolves; several splitter tie-breaks go with it"
   (20260725-dotted-addressing).
3. White-label branding became a one-liner because built-ins are ordinary
   plugins (20260725-dotted-addressing).
4. `=`-only killed the two-pass parser unbuilt; plugin globals needed no
   grammar of their own (20260726-plugin-architecture).
5. `GlobalOption` completes and validates like a parameter "by
   construction" — same manifest machinery (20260726-plugin-architecture D18).
6. `Stdout[dataclass]` worked before it was designed — the `--json`
   serialiser already existed; redaction came free through `json_default`
   (20260726-process-boundary).
7. `Stdout[T] | None` needed zero design — `coerce.peel` already strips
   any nesting on the parameter side (20260726-process-boundary).
8. stdin × ask() composed with no new rule — a pipe is not a TTY and ask()
   already refuses off-terminal (20260726-process-boundary).
9. Script-task completion was a command prefix, not a design — TAB reads
   baked JSON and never enters an environment (20260727-script-tasks).
10. The weekly refresh engine leaned entirely on the runtime — env
    isolation, fan-out, fail(), work-keys, the envelope, all reused
    (20260726-tool-option-history).
11. pytest-xdist workers correct for free — Popen injection is data, not
    state, so execnet's spawns are covered unknowingly
    (20260725-process-globals).
12. The release gate is a property of the file format — "is this delta
    non-empty", and the events are the changelog entry
    (20260726-tool-option-history).
13. `fm tools.restub` fell out of stub-as-rendering — re-rendering became a
    query, "no tools read, no network" (20260726-tool-option-history;
    CHANGELOG 0.30.0).
14. `parallel()` returning Results was non-breaking because a Result is an
    int — I2 did the compatibility work (20260731-execution-model-spec).
15. The flat report derives the tree — and a jsonl streaming form — for
    free; addresses encode parentage (20260731-execution-model-spec).
16. The bare-callable ban enforces itself in the type system — both maker
    protocols demand `.opts`, which a lambda lacks (20260731-execution-model-spec).
17. hse's djlint gate collapsed to one line — the review window + Result-
    as-int made "fail by this tool's definition" free
    (20260731-execution-model-spec).
18. Structured returns needed no data path — the value already rode
    `items[].returned`; drift-snapshots fell out of `--describe` existing
    (20260805-structured-returns).
19. Failure injection covers the bridge without toolroom knowing the
    feature exists — one run() door + host detection
    (20260805-recording-failure-injection).
20. The Argv twin needed no shared protocol — a plain `list[str]` flows
    into run() as the list it is (20260801-tools-namespace-package).
21. Third-party parameter markers already work — peel ignores unknown
    metadata; plugin markers need nothing from core
    (20260727-incremental-caching).
22. The profile plugin shipped with zero private hooks — post_tasks +
    GlobalOption + the timing already in `--json` carried a Chrome-trace
    exporter (20260805-profile-plugin).
23. `wrap_task` is sugar by construction — running a generator to its yield
    *is* the pre phase (20260726-plugin-architecture).
24. The colour "off" direction cost nothing — both directions come from the
    same scraped choices list (20260723-color-support-plan).
25. Per-argument caching and mid-sweep resume pre-exist — the args-key
    means per-date entries with no extra work (20260727-incremental-caching,
    20260731-execution-model-spec walk 6).
26. Dotted cherry-picking rode the composition merge — every rule reused
    one the plan already had (20260725-dotted-addressing).

## The archaeology — refusals that paid off

1. **The chdir lock** — killed by deadlock audit; obituary kept "so nobody
   resurrects it"; became boundary-atomic claims, then lanes, then provably
   safe generator cancellation (20260725-process-globals,
   20260731-execution-model-record, 20260731-execution-model-spec).
2. **A caller-side `--emit` flag** — "the task knows, and the caller should
   not have to"; the declaration moved into the return annotation and
   structured returns reused it wholesale (20260726-process-boundary,
   20260805-structured-returns).
3. **A configurable usage exit code** — "right, not configurable"; the
   boundary became a total contract, low codes handed back to tasks
   (20260726-process-boundary).
4. **A space-form fallback for dotted addresses** — "a space fallback would
   forfeit the win"; became the cited precedent for `=`-only and the
   `shared=` rename (20260725-dotted-addressing,
   20260726-plugin-architecture).
5. **Warn-but-accept for space-form values** — accepting means parsing,
   which resurrects the ambiguity machinery; the two-pass subsystem was
   never built and `--jobs=-1` parses trivially (20260726-plugin-architecture).
6. **Default-shared memoisation** — Willem argued off his own default
   ("you have convinced me for now"); the honest default later collapsed
   cleanly into the single identity key (20260726-plugin-architecture,
   20260731-execution-model-spec).
7. **`fail(code=0)` as a pass-branch** — "one verb fighting its own name";
   the ruling later closed the observer greenwash hole
   (20260724-task-failure, 20260731-execution-model-spec).
8. **A receipt primitive** — "a forged receipt with the mask off"; hse's
   real need got the honest `pre_record` instead (20260731-execution-model-record,
   20260731-execution-model-spec).
9. **Flipping recording off by default** — "guts dry-run, inverts the
   failure mode to silence"; noise was solved as display policy
   (20260731-execution-model-record).
10. **The bare-callable-in-Annotated convenience** — removed on one-
    spelling grounds though it worked; opened the plugin-marker lane
    safely (20260727-incremental-caching).
11. **An advisory type-checker tier** — "all four gate, or they're out";
    the gate became a design instrument (the typed loom forced findings
    1–10 of the work-item spec) (20260730-typing-citizenship,
    20260731-execution-model-spec).
12. **`argv(fmt=…)` + `.do()` terminators** — format belongs at the
    boundary, on the value; a forgotten `.do()` is a silent no-op; the
    toolroom twin later worked *because* the value stayed a plain token
    list (20260803-tool-command-lines, 20260801-tools-namespace-package).
13. **Reconstructing script environments with `--with`** — would have
    silently dropped `[tool.uv.sources]`; native `uv sync --script` meant
    dev checkouts just worked (20260727-script-tasks).
14. **pydantic (any third-party binder)** — "ruled out by the zero-dep
    invariant before taste enters into it"; the binder stayed ~150 lines
    and the toolroom seam stayed stdlib (20260726-process-boundary,
    20260801-tools-namespace-package).
15. **Mount-dependent executor wiring for toolroom** — a mount-keyed lane
    would be invisible to recording() and --dry-run; host detection was
    proven right twice within days (20260801-tools-namespace-package,
    20260805-recording-failure-injection).
16. **Stripping ANSI from stubborn tools** — footman can't tell a bug from
    a deliberate override; the off direction rode each tool's own switch
    (20260723-color-support-plan).
17. **File-derived DAG edges / observed-read cache keys** — "keep `pre=`
    the only edge"; dry-run, --where and completion stay readable from
    declarations (20260727-incremental-caching, 20260731-execution-model-record).
18. **A `ctx.returned()` accessor** — "calling the task *is* the
    accessor"; closed the body-call hole and fixed two latent parity bugs
    (20260726-plugin-architecture).
19. **The host-read `system` tier** — never defended, then deleted;
    "nothing footman reads comes off the host"
    (20260726-tool-option-history).
20. **An in-process task-to-task pipe** — recorded tempting-but-rejected;
    intra-run data flow stayed on the forwarding/structured-results road
    (20260726-process-boundary, 20260805-structured-returns).

## Execution sequence (ruling 24)

1. **This note.** Its own PR.
2. **Correctness PR** — findings D items 1–8, the CHANGELOG amendment, the
   quick coverage wins (cascade key + FOOTMAN_CASCADE, to_argv(),
   GlobalOption(bare=), timed_out/124), color-support.md deleted, its link
   repointed.
3. **src PR** — JSON kind `"argument"` → `"positional"`, taught-error text
   aligned, CHANGELOG entry.
4. **api generation PR** — public-path spike, generator + grouping
   declaration + exemption list, backlinks.
5. **Nav restructure PR** — Basics/In depth, both splits, all moves, the
   built-on openers.
6. **Nomenclature sweep PR** — glossary rewrite + the mechanical sweeps
   from rulings 5–14 (including Footman capitalisation).
7. **Voice pass** — decompression rewrites (composing, json, orchestration
   first), design.md growth (the "no/partly" principles above), register
   trims, the `_gc.py` backticks. Likely several PRs.
8. **Drift-guard PR** — the seven guards, last, gating settled content.

Toolroom repo, separately: colour table page, objects.inv exchange both
ways. Independent housekeeping, any time: zensical 0.0.50 → 0.0.53.
A release wrapping the src rename waits for Willem's call.

## Deliberately not done

- No general prose-truth guard; no benchmark-number gating (weather).
- The native `::: footman` one-directive api form — declined for losing
  learning-order sections.
- Docstring crossref syntax — declined for raw readability; revisitable.
- The comparison/measured numbers were not re-run this session.
