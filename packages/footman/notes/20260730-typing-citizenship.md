# Typing citizenship

**Status: BUILT — all six phases shipped 2026-07-30 (PRs #228, #229,
#230, and the typechecking docs page). Remaining: the two follow-ups at
the end of "Decided".**

footman should be a good citizen for people who strongly type their code:
consumer `tasks.py` files should type-check cleanly against the public API
under the checkers people actually run, and the public interface should be
*verifiably* fully typed — not "we ship `py.typed` and hope". Correctness is
preferred over suppression throughout; where checkers disagree, the plan says
how to arbitrate rather than silencing whichever one lost.

## Where we stand (audited 2026-07-30)

The groundwork is better than the reputation of "task runner, probably
stringly-typed" would suggest:

- `py.typed` ships in the wheel; the `Typing :: Typed` classifier is set.
- `__init__.py` pairs the runtime lazy `__getattr__` with a `TYPE_CHECKING`
  re-export block (`import X as X` form), so all 68 lazy names resolve
  statically — `from footman import task` is never `Any`.
- `tools.pyi` + 33 generated `_stubs/*.pyi` give the tools bridge precise
  per-verb flag types, with an AST parity test and a negative-assertion
  typing file (`tests/typecheck_tools.py`, the
  `reportUnnecessaryTypeIgnoreComment` trick).
- `@task` returns a `TaskFn[P, R]` Protocol (ParamSpec), so decorated tasks
  keep their signatures; there is one `assert_type` test proving it.

But the claim "fully typed" is not currently checked, and does not currently
hold. `basedpyright --verifytypes footman` scores **0.8575**: 638 exported
symbols fully known, 93 ambiguous, 13 unknown. The gate runs basedpyright at
`typeCheckingMode = "standard"` — pyright's default tier, below even
basedpyright's own default. No mypy. No ty. Nothing asserts completeness.

The ambiguity/unknown mass decomposes into a short list of causes:

1. **Unannotated marker singletons leaking private classes.** `exists`,
   `isfile`, `isdir` infer as `_PathRequirement`; `nosplit` as
   `_NoSplitMarker`; `forward` as `_ForwardMarker`; `stdout` as
   `_StdoutMarker`. A consumer can hold one but never name its type.
2. **Unannotated attributes** on the `__slots__` marker classes
   (`suggest.fn`, `between.lo`, `check.fn`, …), `GlobalOption`,
   `Invocation`, `App.brand`, `Runner.__init__`.
3. **Module-level bound-method aliases**: `task = root.task`,
   `group = root.group`, and the seven hook aliases are unannotated
   assignments — verifytypes classifies them as inferred, not declared.
4. **Real `Any` holes**: `.opts()` returns `_Opted`, whose `__getattr__`
   and `__call__` are `-> Any` — calling an opted task erases the signature
   `TaskFn` worked to keep. `parallel()` returns `Any` and its
   context-manager form has no static type at all. `track`, `inherited`,
   `prompt`, `select` return `Any`. The `@requires*` gates return
   `Callable[[Task], Task]` where `Task = Callable[..., Any]` — which is
   *why* docs/composing.md has to teach "`@task` outermost, or the gate
   erases your types". That wart is a symptom of this hole.
5. **Seven non-underscore modules that are de facto private** — `coerce`,
   `config`, `discover`, `executor`, `manifest`, `schedule`, `split` —
   never documented, imported only internally. They aren't in
   `footman.__all__`, but their plain names invite `from footman.split
   import …` and put them in scope for any "is the public API typed"
   tooling. (One deliberate inversion exists and is fine as a pattern:
   `_fetch.py` is private, its `fetch`/`FetchError` surface through
   `footman.__all__`.)
6. **The triple-sync in `__init__.py` is convention-only.** `__all__`, the
   `TYPE_CHECKING` block, and the runtime `__getattr__` tuples are kept
   aligned by hand; nothing tests them against each other. Visible
   symptoms today: `wrap_task`/`wrap_bind` appear in two runtime tuples
   (the second is a dead branch that would `AttributeError`), and
   `"Stdout"` is listed twice.

## The 2026 checker landscape (researched 2026-07-30)

The "ty, basedpyright, mypy" instinct needs one correction:

- **basedpyright** v1.39.9 tracks upstream pyright release-for-release. Its
  *default* mode is `recommended` (stricter than pyright's `standard`,
  which is what we run). It inherits pyright's `--verifytypes <pkg>
  --ignoreexternal` unchanged — the industry-standard public-API
  type-completeness score.
- **mypy** 2.3 (July 2026): `--strict` is the well-understood bundle;
  `stubtest` remains the only tool that checks `.pyi` stubs *against the
  runtime* — the off-the-shelf complement to our AST parity test (names
  today; stubtest adds signatures).
- **ty** (Astral) is still **0.0.x beta** — releases twice a week, stable
  "targeted for 2026" but not shipped, ~53% typing-spec conformance (mostly
  unimplemented features, not wrong answers), and open issues on exactly
  our patterns (module `__getattr__` → `unresolved-attribute`). Nobody
  found gates a library on ty yet; attrs runs it against a curated baseline
  file only.
- **pyrefly** (Meta) shipped **1.0 in May 2026**, ~88% conformance, monthly
  stable cadence, already *gating* in msgspec (`tests/typing` under three
  checkers) and attrs (baseline file). It is the more CI-ready fourth
  checker today, and advertises correct ParamSpec handling where mypy has
  long-standing open bugs.
- Conformance table for calibration (pyrefly's 2026-03 measurement):
  pyright 97.8 · zuban 96.4 · pyrefly 87.8 · mypy 58.3 · ty 53.2.

How well-typed libraries actually combine them (confirmed from their CI):
attrs = mypy + pyright gating, ty/pyrefly on a baseline file; trio = mypy ×
3 platforms + pyright on dedicated type-test dirs + a verifytypes
completeness job; anyio = pyright `--verifytypes` job; msgspec = mypy +
pyright + pyrefly over `tests/typing`, all gating; cattrs = mypy strict
only.

Known cross-checker friction relevant to us, going in eyes-open:

- **ParamSpec'd callable Protocol as a decorator return** (`TaskFn`): mypy
  has open bugs (mypy#16142, #15827) where this infers `[Never, Never]` or
  misbehaves when `__call__` has company; pyright-family handles it. This
  is the most likely place mypy --strict fights the design. Policy below.
- **`.pyi` beside `.py`**: consumers see only the stub; drift is the risk,
  stubtest is the answer.
- **PEP 695 syntax**: stay on `TypeVar`/`ParamSpec` spellings everywhere,
  `.pyi` included, until 3.11 is EOL (Oct 2027) — mypy running *under*
  3.11 cannot parse the new syntax at all (typeshed's own policy).

## The plan

Order matters: fix the surface first, then bolt the gates on, so each gate
lands green rather than landing with a suppression file.

### Phase 1 — draw the public/private line

- Rename the seven de-facto-private modules to underscore names: `coerce` →
  `_coerce`, `config` → `_config`, `discover` → `_discover`, `executor` →
  `_executor`, `manifest` → `_manifest`, `schedule` → `_schedule`, `split`
  → `_split`. Pre-1.0, no deprecation cycle, CHANGELOG notes it. Nothing
  documented refers to any of them; footman's own tests do and get updated.
- `executor.TaskResult` is the one leak: it already surfaces through
  `footman.testing.__all__`, which stays its public spelling. The class can
  live in `_executor` exactly as `fetch` lives in `_fetch`.
- After this, the rule is statable and checkable: **public = `footman`,
  `footman.testing`, `footman.tools`, `footman.params`, `footman.compose`,
  `footman.docstrings`, `footman.markdown`, `footman.app`,
  `footman.invocation`, `footman.registry`, `footman.context`,
  `footman.pytest_plugin`, `footman.env_files`, `footman.tasks.*`;
  everything underscore-prefixed is private.** (docs/api.md documents
  symbols under `footman.registry.*` / `footman.context.*` spellings, so
  those two stay public modules even though `footman.*` re-exports cover
  most consumers.)

### Phase 2 — close the holes (the correctness work)

In rough order of consumer impact:

1. **`.opts()` keeps the signature.** Make the opted proxy generic —
   `TaskFn.opts(**overrides) -> TaskFn[P, R]` (or an `_Opted[P, R]` that
   satisfies it). Calling an opted task then type-checks like calling the
   task. The `assert_type` test grows the opted-call case it currently
   lacks.
2. **Gates stop erasing.** `requires`/`requires_dep`/`requires_tool`/
   `requires_env` return `Callable[[F], F]` with `F` a TypeVar — identity
   in types. The docs/composing.md decorator-order warning about type
   erasure then describes a problem that no longer exists; per the
   timeless-docs rule the paragraph is rewritten, not annotated. (Runtime
   ordering constraints, if any survive, get stated on their own merits.)
3. **`parallel()` gets a real type.** Overloads for the varargs form
   (`Callable[[], T]` homogeneous → `list[T]`; heterogeneous falls back)
   plus a typed handle for the context-manager form (`-> Parallel` with
   `results` typed). Exact shape is a design task within the phase.
4. **`track` → `Iterator[T]`**, `inherited`/`prompt`/`select`/
   `active_status`/`real_stdin` get honest return types (`str` where it is
   a str, unions where it is a union — not `Any`).
5. **Markers get nameable public types.** The instances stay the API
   (`exists`, `nosplit`, `forward`, `stdout`), but each gets an explicit
   annotation whose type has a public (undocumented-is-fine) name, so
   verifytypes stops flagging private leakage and a consumer can spell the
   type if they ever need to. Class-level attribute annotations go onto the
   `__slots__` marker classes, `GlobalOption`, `Invocation`, `App`,
   `Runner`.
6. **The module-level aliases become declared.** `task`, `group`, and the
   seven hook aliases get explicit annotations (a small Protocol carrying
   the overloads, or module-level `def`s that delegate — whichever survives
   all gating checkers; the Protocol is the lighter touch).
7. **Fix the `__init__.py` nits now**: dead `wrap_task`/`wrap_bind` branch,
   duplicated `"Stdout"`.

### Phase 3 — make "fully typed" a checked claim

- Add `basedpyright --verifytypes footman --ignoreexternal` as a gate step
  (a `typecheck`-adjacent task in tasks.py, parallel like the rest),
  asserting the completeness score. After phase 2 the score should be at or
  near 1.0; the assertion is exact, not a ratchet — anyio/trio both hold
  100%.
- Add an AST test binding the `__init__.py` triple together: `__all__` ==
  TYPE_CHECKING re-exports (minus eager `main`/`__version__`) == union of
  the runtime `__getattr__` tuples, no duplicates, no dead entries. That
  turns the convention into an invariant.
- `stubtest footman.tools` (allowlisted as needed for the deliberate
  runtime/stub divergences, e.g. aliased-private imports) joins the suite
  to check stub *signatures* against runtime, complementing the AST
  name-parity test. Ships with the mypy adoption below since stubtest is
  part of mypy.

### Phase 4 — the checker matrix

**All four gate, or they're out** (Willem's ruling, 2026-07-30): no
advisory tier — a checker either runs in the gate with the tree clean
against it, or footman doesn't use it at all. A permanently-yellow CI job
just trains everyone to ignore it. This deliberately departs from the
attrs/trio pattern of advisory baseline-file runs.

- **basedpyright**, raised from `standard` to **`recommended`** (its own
  default) — likely with a handful of per-rule adjustments for `tests/`
  (fixture shadowing etc.), which land as explicit config, not inline
  suppressions.
- **mypy over everything, tiered.** Full `--strict` on `src/` and the
  phase-5 typing files; on `tests/`, the usage-checking half only —
  `check_untyped_defs = true` so every test body is type-checked as
  consumer code, without `disallow_untyped_defs`/`-incomplete-defs`
  demanding `-> None` on every test and annotations on every fixture.
  Rationale (Willem's point, 2026-07-30): most library-seam typing issues
  only show up in *use*, and the test suite is the largest corpus of real
  consumer-shaped code — it also leans on `@task` harder than any
  consumer will, so it surfaces the mypy/`TaskFn` ParamSpec friction
  early. The annotate-the-defs half buys no additional seam coverage,
  only churn; Willem's preference is that it does eventually happen —
  full `--strict` on tests is the destination, the tier a transition
  (see the open question for the measured size). (basedpyright
  needs no such split — it checks unannotated bodies by default, so
  `tests/` is first-class under the primary gate from the start.)
  Where mypy is *wrong* on the ParamSpec-Protocol pattern, the
  suppression is a narrow `# type: ignore[code]` with a comment linking
  the mypy issue — never a design retreat, never a blanket ignore. If the
  pattern turns out unusable under mypy (the `[Never, Never]` bug biting
  every consumer), that is a finding to bring back, not to paper over.

- **ty** and **pyrefly**, same gate, same cleanliness bar. Gating a 0.0.x
  checker is viable because of the lockfile: all four are pinned dev
  deps, so ty moves only when the lock moves — a new release can't
  redden the gate spontaneously; breakage surfaces on a deliberate
  upgrade and gets dealt with then, like any dev-dep bump.
- **Drop, don't demote.** If a checker cannot be made clean without a
  suppression spray or a design retreat — ty's incomplete ParamSpec
  support is the known risk — it leaves the roster entirely, with a note
  here recording why and what would readmit it (for ty: a stable release
  that checks the `TaskFn` pattern).
- **Accepted cost:** four checkers means four suppression dialects
  (`# type: ignore[code]` / `# pyright: ignore[...]` / `# ty: ignore[...]`
  / `# pyrefly: ignore`) can pile onto a line where checkers disagree.
  The correctness-first policy keeps such lines rare; a line needing
  three dialects is a smell worth a second look at the code itself.

All four live in the **`dev` group** (ruling, same date) — they run in
`fm check`'s typecheck step (fanned in parallel like the rest of the
gate) and in CI. The zero-runtime-deps invariant is untouched.

### Phase 5 — consumer-shaped typing tests

The place multiple checkers earn their keep is not footman's internals —
it's the seam where a consumer's `tasks.py` meets the API. Extend the
`typecheck_tools.py` pattern into a small family of checker-facing files
(non-`test_` names, checked not executed — same mechanism as today):

- positive shapes: `@task` bare and parameterised, `@group.default`,
  markers inside `Annotated`, `run()`/`Result`, `parallel`, forwarding,
  `Runner.invoke`, `include`/`plugin`, `.opts()` — each with `assert_type`
  where a value's type is the contract;
- negative shapes: wrong marker usage, wrong `.opts()` keys, calling a
  task with a wrong argument — via the ignore-comment-must-be-necessary
  trick.

Dialect note: negative assertions are checker-specific (`# pyright:
ignore[...]` vs mypy's `# type: ignore[...]` — and mypy's
`warn_unused_ignores` is the equivalent tripwire). Positive files are
shared across all checkers; negative files are per-checker where the
comment dialects force it. The existing mypy-style bracket codes on
basedpyright-targeted ignores under `src/` get audited when mypy arrives —
today they are decorative; under mypy they become load-bearing.

### Phase 6 — say it out loud

A docs page (or a section on an existing page) stating the typing contract:
what's public, that the public API is verified fully typed and against
which checkers, the PEP 695 spelling policy, and what a consumer can rely
on (`@task` preserves your signature, `.opts()` too, markers are
`Annotated` aliases that vanish at the type level). Plain words.

## Phase 4 outcomes (built 2026-07-30, same day)

- **All four checkers gate, and ty survived the roster rule.** The drop
  risk turned out misplaced: ty handled the `TaskFn` ParamSpec pattern
  without complaint. Its findings were substantive — it caught
  `Peeled.ask` annotating itself with its own field name (shadowing the
  `ask` class), and its dict-splat strictness forced honest intermediate
  types. Its one wall: module/class attributes are effectively Final —
  it refuses even a signature-identical monkeypatch (`def fork() -> int`
  onto `os.fork`), so `_globals.py`'s 13 patch lines carry suppressions
  (bare `# type: ignore` where mypy also objects, `# ty: ignore[…]` where
  only ty does). Concentrated, uniform, one file whose nature is the
  patch — the documented exception, not a spray.
- **pyrefly gates at its `default` preset.** `strict` demands `@override`
  (`typing.override` is 3.12+; unreachable for a zero-dep 3.11 library)
  plus annotate-every-empty-container churn. Revisit at 3.11 EOL.
- **Scope:** basedpyright + mypy check everything; ty + pyrefly check
  `src/` — the consumer seam in tests is already double-checked, and the
  Rust checkers' fake-object modelling isn't ready for the suite's
  doubles.
- **All platforms, from any host** (Willem's call, same day): basedpyright
  defaults to `pythonPlatform: All`; ty and pyrefly check the platform
  union (`python-platform = "all"`); mypy has no all-mode, so the gate
  runs it three times (linux by config, darwin and win32 by flag), each
  with its own cache dir — mypy's SQLite cache does not tolerate
  concurrent writers. The win32 view forced two honest improvements: the
  process-globals router now patches via `setattr` (a dynamic write said
  dynamically — zero suppressions left in the file, B010 excused for it),
  and POSIX-only code carries `sys.platform` narrowing (`if`-statements,
  not asserts — mypy's platform reasoning only follows ifs, and never
  across a nested-function boundary).
- **mypy tier landed as agreed** (strict on `footman.*`, usage-checking on
  tests), with one structural inversion: mypy's per-module patterns can't
  address the tests' bare top-level module names, so the *global* config
  is the tests tier and a `footman.*` override tightens the library.
- **Portability idioms that emerged** (use these, not suppressions):
  `task_name(fn: Any)` for `__name__` on `Callable` (ty is per-spec right
  that Callable has none); getattr-then-call instead of hasattr-narrowing
  (not portable); annotated intermediate dicts instead of heterogeneous
  inline `**{…}` splats (ty distributes the value union); `cast` where
  isinstance narrows to `dict[Unknown, Unknown]` and invariance blocks
  the reassignment.

## Phase 5 outcome (built 2026-07-30, same day)

Two files, split by suppression dialect: `tests/typecheck_api.py` is the
positive consumer surface — no ignores, so ty and pyrefly include it by
name (their one tests/ file); `typecheck_api_negative.py` is the must-fail
surface, mypy+basedpyright only, every line's ignore policed for staleness
from both sides. The exercise caught real API texture both times it ran a
new checker over consumer shapes: select() grew plain-strings overloads
(ty solved the pair-overload's TypeVar to Unknown on a string menu), and
the group-default handle's signature-keeping is pinned (the answer to
"where do default parameters autocomplete" is: on the handle, call it
directly).

## Phase 6 outcome (built 2026-07-30, same day)

docs/typechecking.md, a Guide page after "Testing your tasks": what the
types promise (with runnable examples — the deliberate type-error lines
are `example: fragment` blocks so the page-as-session harness skips
them), private-is-private said consumer-facing, and the enforcement story.
All six phases of this plan are now built; what remains are the two
follow-ups (test-alias respelling, full test annotation).

## Rejected along the way

- **An advisory tier.** The first draft proposed ty + pyrefly as a
  non-gating CI job with promotion criteria (the attrs/trio pattern).
  Rejected by Willem (2026-07-30): no point in advisory checkers —
  either footman uses one and is clean against it, or it doesn't use it.
  The failure mode a checker meets instead is being dropped from the
  roster (see phase 4), not demoted to decoration.
- **Chasing basedpyright `all`.** `recommended` is the strictest tier the
  maintainers themselves default to; `all` is a baseline-file lifestyle.
  Revisit after everything else is green.
- **A single `typing/` stub-only distribution or separate stubs package.**
  Inline types + targeted `.pyi` (tools) is working; PEP 561 inline is the
  preferred form for a first-party typed library.
- **PEP 695 syntax anywhere** before 3.11 EOL — mypy under 3.11 can't
  parse it.
- **Renaming `_fetch.py` public.** Private module + re-exported public
  names is a legitimate pattern (it's also what phase 1 creates for
  `TaskResult`); the line is drawn at module *names*, membership in
  `footman.__all__` defines symbol publicity.

## Decided (Willem, 2026-07-30)

- **Roster: all four gate — basedpyright, mypy, ty, pyrefly — no
  advisory tier.** A checker that can't be clean is dropped, not
  demoted. All four live in the `dev` group.
- **basedpyright: `recommended` everywhere** — the whole include set,
  tests included; per-rule relaxations land as explicit config if the
  fixture idioms need them, never as inline suppressions. Willem also
  granted latitude to split the tier (stricter on `src/`, lighter on
  `tests/`) if that works better in practice — implementer's choice;
  `recommended` everywhere is the starting position.
- **verifytypes runs inside `fm check`.**
- **The private line is wanted, firmly**: "if it's private it's private —
  we do a fair amount of magic and discouraging people from hooking
  inside is not a bad idea." Settles the phase-1 direction and the
  `--ignoreexternal`/private-exempt semantics of verifytypes. The
  specific seven-module rename list (`coerce`, `config`, `discover`,
  `executor`, `manifest`, `schedule`, `split`) stands as proposed unless
  one gets flagged.

- **mypy on tests: full `--strict` is the destination, as a follow-up.**
  Agreed 2026-07-30: the four-checker matrix lands first with the
  `check_untyped_defs` tier on `tests/` (identical seam coverage of test
  bodies), then a dedicated commit series does the full annotation pass
  against a quiet tree. The measured size of that pass (AST count over
  `tests/`, 2026-07-30): 3250 function defs across 48 files; 3067
  missing a return annotation (overwhelmingly mechanical `-> None`,
  largely scriptable via ruff's ANN fixes); 1526 defs carry 2298
  unannotated params (mostly fixture/helper types — `tmp_path: Path`,
  `fm: Runner` — shallow individually, a multi-session pass in
  aggregate); 47 of 48 files touched, so it rewrites nearly every test
  file and must not share a branch with anything else.

No open questions remain.
