# Roadmap

This file began as a critical self-audit of footman at **v0.4.0** — every
claim checked against the source, file and line. Almost all of it has
shipped in the releases since. The file does two jobs: the road ahead, and
— for posterity — the original audit preserved item by item, each with the
release that closed it. The full stories live in the
[changelog](https://willemkokke.github.io/footman/changelog/).

**Where footman stands (2026-08-20, v0.43.0, Beta on PyPI).** The typed core
— coercion, chain grammar, manifest, scheduler, cascade — held up; everything
since has been built on it without structural change. The runner now has a
real help story, a testing story, a composition story, completion installed
and functionally tested on five real shells, one `--json` envelope for
machines, docstring-driven parameter docs, markdown export of the task
surface, a progress bar that earns its confidence from duration history, and
one colour palette across the whole CLI. The typed tool surface moved to
[toolroom](https://willemkokke.github.io/toolroom/), which footman neither
imports nor names. Coverage is enforced at ≥ 92%, CI runs 3 OS × Python
3.11–3.14 including free-threaded, and a tag cannot publish unless CI and
the version checks agree.

## The road to 1.0

Two items remain — the rest of this list is struck, kept with the evidence
that closed it:

- ~~**Help strings that carry the whole truth.**~~ **Done.** The pass
  landed: `--jobs` names the floor of 2, `-s` says it reaches `parallel()`
  blocks inside bodies, and `--plugins` says it lists installed plugins
  *pulled or not* — the comparison that makes the flag worth running. The
  strings feed `--help`, completion menus and the generated docs table from
  one source, so each fix lands in three places at once.
- ~~**The tools surface, properly**~~ — **delivered, and no longer
  footman's to ship.** The surface moved to
  [toolroom](https://willemkokke.github.io/toolroom/) unchanged in what it
  does, and every clause of this item landed there: stubs generated from
  each tool's own metadata (click's `opts`/`secondary_opts` read as data),
  a reference page per tool, a `tools.audit` task that fails on drift, and
  the `off` fix — `mkdocs` `clean=off` emits `--dirty`, not the
  `--no-clean` the convention would have guessed. Verified 2026-08-07
  against the generated stub. toolroom releases on its own train; footman
  neither imports nor names it.
- **The stability promise, and the bake cycle after it.** The page landed
  in 0.40.0:
  [Stability](https://willemkokke.github.io/footman/stability/) names the
  four surfaces a project builds on — the public API, the CLI grammar, the
  `--json` envelope, the `[tool.footman]` keys — and says plainly that the
  manifest file and the caches are *not* among them, which the original
  item had wrong. What is left is the bake cycle: a stretch of releases
  with no breaking change to that surface.
- **The 1.0 flip**: pre-1.0 warnings out, promise in — one coordinated
  change, with a TestPyPI dry-run before the real tag. (The
  `Development Status` classifier already moved Alpha → Beta in 0.12.0.)

## New since the audit

Ideas that came out of building the last eight releases, not in the original
plan:

- ~~**A generated `[tool.footman]` reference.**~~ **Landed** (see the
  changelog for the release).
  `_config.KEYS` is the key set as data, `fm docs.config` renders it, and
  configuration.md snippet-includes the result. Found the bug that
  motivated it: `cwd` — a real, validated run-wide policy key — had never
  been written down anywhere, because the only list was prose in a
  docstring.
- ~~**Dedup the branch-vs-PR CI runs.**~~ **Already resolved — do not
  re-add.** `ci.yml` scopes `push:` to `branches: [main]`, so an in-repo
  PR branch fires the `pull_request` event only; a `concurrency` group
  keyed on `github.ref` with `cancel-in-progress` supersedes the rest.
  Verified 2026-08-07 against the workflow.

## After 1.0 — the backlog

Not gating anything, carried forward minus the entries that shipped
(task-returned JSON payloads landed in 0.10.0, the TTY progress UI grew into
0.12.0's history-backed bar, PowerShell/nushell completion landed in 0.8.0,
the typing table's two post-1.0 rows — hidden parameters and fixed-arity
`tuple[X, Y]` in comma form, `--size=800,600` — both landed in 0.34.0, and
`fm new` landed in 0.36.0):

- **Watch mode** — `fm --watch lint`: re-run on file change, debounced.
- **JSONL event streaming** — `--json` is a summary; agents and CI dashboards
  want per-event lines as tasks start and finish.
- **Fingerprint-based skipping** — "inputs unchanged, skip the task"
  (doit/turborepo territory; big, and the DAG is already in place).
- **Per-*task* timeout, and retry** — `@task(timeout=120, retries=2)`.
  Half of this shipped: `run(..., timeout=30)` and a step maker's
  `.opts(timeout=…)` already kill the tree at the deadline, answering exit
  124 with `Result.timed_out` set. What is missing is the *task* level —
  `TaskOpts` has no `timeout` — and retry entirely, which is the harder
  half: a retried task has to decide what its record says (one row that
  succeeded, or every attempt), and the report is not allowed to lie.
- **Clean environment per task** — run a task's subprocesses with a
  reconstructed/allowlisted environment (tox's `passenv`, invoke's
  `replace_env`) so a polluted parent env can't leak into a build — the same
  reproducibility instinct as `clean=True` shells. `env=` stays an overlay by
  default; a replace mode would need to merge the run-wide colour decision into
  the built environment (so the colour contract survives the wipe) and be a
  taught error for an in-process task — code in footman's own process can't be
  handed an isolated environment.
- **Optional rich terminal output** — a lazily-imported renderer, behind a
  stacked `@requires_dep` (the one blessed zero-dep exception), that paints
  `--help` and docstrings as formatted markdown in the terminal. Off by default
  and never a dependency: on only when the package is both installed and asked
  for, so plain text stays the contract and colour stays the only styling
  footman applies unbidden.
- **Handoffs for other package managers** (poetry, pdm) — if there's a
  want. uv shipped first because `uv.lock` makes the fire-rule
  unambiguous, and its native script support carries the PEP 723 rule
  too; each manager needs an equally sharp rule of its own.

Two of the audit's three "never"s are still never, for the same reasons:
counting flags (`-vvv` belongs to the runner, not task params), and short
aliases for task parameters (collision-prone across cascade merges, and they
steal negative-number positionals). Saying never to those is what keeps the
grammar deterministic — the thing that makes separator-free chaining
possible.

The third — **prompts and confirmation** — was reversed, and shipped. The
objection was real: a chained, parallel, CI-first runner is the most hostile
environment interactivity has ever met. The answer was not to drop the
feature but to make that hostility structural, so the awkward cases cannot
arise. `ask()` for a value, `@task(confirm=…)` for a gate, and
`@task(interactive=True)` for a task that owns the terminal — the last of
which forces the run sequential, because a task holding the real terminal
cannot share it. Without a TTY nothing hangs and nothing silently
proceeds: a defaulted parameter falls back, a required one refuses with a
taught message naming the flag, and `--yes` / `--no-input` decide for CI up
front. See [Asking for input](https://willemkokke.github.io/footman/input/).

Recording the reversal rather than quietly deleting the line: a "never"
that a later design answers is worth more as a worked example than as an
embarrassment.

---

## The v0.4.0 audit, for posterity

Everything below is the original audit, condensed to one line per item, with
the release that closed it. Section numbers match the original.

### §1 Bugs — all thirteen, fixed in 0.5.0

| # | Bug | Landed |
| - | --- | ------ |
| 1 | `fm --help build` executed `build` | 0.5.0 — help anywhere before `--` is read-only |
| 2 | Cyclic `pre`/`post` deps → silent exit 0 | 0.5.0 — taught error naming the cycle |
| 3 | `bool` inside collections always `True` | 0.5.0 — real bool token type |
| 4 | `list[bool]` collapsed to a single flag | 0.5.0 |
| 5 | Malformed config TOML silently ignored | 0.5.0 — discovered config warns; `--config` errors |
| 6 | Crashing strict `suggest()` disabled validation | 0.5.0 — fails the run |
| 7 | Ctrl-C → raw traceback | 0.5.0 — cancelled, `interrupted`, exit 130 |
| 8 | Windows `run("...")` mangled by POSIX `shlex` | 0.5.0 — string goes to `CreateProcess` whole |
| 9 | Non-UTF-8 subprocess output crashed | 0.5.0 — `errors="replace"` |
| 10 | Duplicate task name misreported as import crash | 0.5.0 — named user error |
| 11 | `"²".isdigit()` → `int()` traceback | 0.5.0 — taught type error |
| 12 | `--dry-run` recorded no `StepResult` | 0.5.0 — records, honours `quiet` |
| 13 | `py.typed` missing | 0.5.0 — shipped, checked by the release gate |

### §2 Half-baked and dead surface

| Item | Resolution |
| ---- | ---------- |
| `--refresh-manifest` no-op | removed, 0.7.0 |
| `--install-completion` printed "not wired up yet" | wired: bash/zsh/fish 0.7.0, pwsh/nushell + shell detection 0.8.0 |
| README pessimistic about `-v`/`--no-color` | README rewritten as a front door, 0.8.0 |
| Per-task `--help` didn't exist | shipped, 0.5.0 |
| `manifest.is_stale` + `sources` never consulted | removed, 0.7.0 (real freshness arrived as stale-while-revalidate in 0.9.0) |
| `executor.run_chain` had no callers | kept and wired: it became the binding-test harness the suites drive |
| `tools` load-bearing but not exported | public (`__all__`, lazy), 0.7.0 |
| `Group` unrunnable, `Context` unconstructable, `reset()` public | `Runner`/`use_context()` 0.6.0; `reset()` out of the root namespace 0.7.0 |
| `tools.*` seven wrappers vs duty's dozens | the bridge (any executable, no declaration), 0.8.0; typed stubs 0.9.0 |

### §3 Release engineering

| Item | Landed |
| ---- | ------ |
| Any `v*` tag published unverified | 0.5.0 — release runs full CI on the tagged commit |
| Version in two places, checked by nothing | 0.5.0 — tag = pyproject = `__version__` = changelog, enforced |
| Coverage reported, never enforced | 0.5.0 — `fail_under = 92` in CI |
| Docs built strictly only after merge | 0.5.0 — strict build on every PR |
| Missing URLs, dead changelog links, sdist excludes | 0.5.0–0.8.0 housekeeping |
| Alpha classifier + warnings, one coordinated flip | Beta in 0.12.0; the written promise is the road to 1.0 above |

### §4 Test-suite gaps

The hostile-world column filled in: signals, Windows backslash paths, and
non-UTF-8 bytes with the 0.5.0 fixes they guard; the manifest, cascade, and
coercion suites deepened across 0.5.0–0.9.0 (0.9.0's correctness pass drove
coercion, scheduler, and tools through their edges); and 0.8.0 added the
functional column nobody asked for in the audit — every completion hook
driven against its real shell in CI, which is how the bash 3.2 and
PowerShell empty-argument bugs were caught for keeps.

### §5 The testing story

Shipped whole in **0.6.0**: `footman.testing` (`Runner.invoke`,
`recording()`, `use_context()`), three auto-loaded pytest fixtures (`fm`,
`fm_project`, `fm_record`) at zero runtime dependencies, footman's own suite
dogfooding them, and the *Testing your tasks* docs page. The `--json`
envelope (`{"schema": 1, ...}`) landed earlier, in 0.5.0, exactly so
breaking was still free.

### §6 The composition story

Shipped whole in **0.7.0**: `@task(when=…, reason=…)` disable-but-list,
`include(source, into=…, only=…, exclude=…, override=…)`, the
`footman.tasks` entry point with an opt-in `[tool.footman] plugins` key
(0.21.0 replaced that key with the `plugin()` line in a tasks file, where
placement and filtering already lived; a leftover key is a taught refusal),
`registry.capture()` as the public seam, and the *Composing tasks* page.
`@task(requires=…)` followed in 0.9.0 as the import-free dependency gate,
reusing the same availability machinery. The "hiding is an `if` statement"
stance held — no kwarg was ever added.

### §7 Typing parity

| Gap | Landed |
| --- | ------ |
| `bool` in collections | 0.5.0 |
| `exists` / `isfile` / `isdir` | 0.6.0 |
| `between(lo, hi)` / bare `range` | 0.6.0 |
| `env("VAR")` fallback | 0.6.0 — CLI > env > default, same coercion path |
| `check(fn)` validator | 0.6.0 — post-coercion, per element |
| Silent `str` degrade of unknown annotations | 0.6.0 — warns |
| Hidden params, `tuple[X, Y]` | 0.34.0 — `hidden`/`Hidden[T]` keeps a parameter out of the listings and nothing else; a fixed-arity tuple binds from `--size=800,600` and from a JSON array |
| Prompts | **reversed** — shipped as `ask()`, `confirm=`, `interactive=True`, CI-safe by construction |
| Counting flags, short aliases for task params | still never |

### §8 Completion and CLI polish

| Item | Landed |
| ---- | ------ |
| `--install-completion` bash/zsh/fish | 0.7.0 |
| pwsh/nushell installers, shell detection | 0.8.0 |
| Chain-aware completion | 0.7.0 — the resolver walks segments like the splitter |
| Latency headline honesty | 0.10.0 — ~25 ms measured by a committed benchmark, quoted everywhere |
| Wire-or-delete the dead flags | 0.7.0 |
| Public-surface hygiene | 0.6.0–0.7.0 |
| Grow `tools.*` | 0.8.0 bridge, 0.9.0 in-process + stubs |

Beyond the audit's asks: functional tests against all five real shells
(0.8.0), completions that teach and stay fresh (0.9.0), global-flag
completion, `--setup-completion`, `--uninstall-completion`, per-shell docs
pages, and descriptions in every shell that renders them (0.10.0).

### §9 Docs

| Item | Landed |
| ---- | ------ |
| README as a drifting near-superset of the site | 0.8.0 — a front door with pointers |
| `testing.md` | 0.6.0 |
| `composing.md` | 0.7.0 |
| CI page, troubleshooting catalogue | 0.8.0 |
| Benchmark honesty (import cost, completion latency) | 0.7.0 and 0.10.0 — committed scripts behind both |
| Voice pass over the older pages | 0.10.0 docs cycle — restructure, tabs, one voice |
| The cookbook | 0.15.0 — seventeen recipes, agents included |

The audit's other docs worry — the hand-maintained global-options table that
"*will* drift" — proved right on schedule: it drifted three ways, and 0.13.0
generates it from the grammar on every docs build.

### §10 The release train

Went to plan: 0.5.0 (bugs + release engineering + envelope), 0.6.0
(testing + typing), 0.7.0 (composition + completion) shipped as the table
said, in two days rather than four cycles. Reality then added stops the plan didn't
know about: 0.8.0 (the bridge, all five shells, real-shell CI), 0.9.0 (the
correctness pass, in-process tools, stubs), 0.10.0 (the one-envelope `--json`
contract, `doc()`, agents + llms.txt), 0.11.0 (docstring parameter docs, the
stdout/stderr contract, markdown export), 0.12.0 (the progress bar with
duration history, `-j/--jobs`, `FOOTMAN_CACHE_DIR`, one colour palette,
Beta). The train kept its cadence past the audit's horizon; the
[changelog](https://willemkokke.github.io/footman/changelog/) carries every
stop from 0.13.0 on.

### §11 The original backlog

| Item | Status |
| ---- | ------ |
| Task-customizable `--json` payloads | shipped, 0.10.0 — `returned`, symmetric with what footman coerces in |
| A TTY progress UI for the DAG | shipped, 0.12.0 — and it learned to estimate from duration history |
| PowerShell/nushell completion | shipped, 0.8.0 |
| Watch mode, JSONL streaming, fingerprint skipping, timeout/retry | open — carried in the backlog above |
| `fm new` | shipped, 0.36.0 — a starter tasks file, footman's own first built-in, and it refuses to overwrite |
| `fm --plugins` | shipped — lists entry points, pulled or not |

---

*The original audit was generated from a full source read at v0.4.0 (commit
9328109) and is preserved in the git history of this file. This revision
reflects v0.41.0.*
