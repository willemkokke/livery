# One option model — core globals stop being a dialect

*Status: **LANDED 2026-08-09** — built the same day it was ruled on, as
PRs #371 (declare + derive), #372 (plugin binding parity, a lead-in this
note had dropped), #373 (the one ladder; `_switch` dies), and #374 (plugin
config sections), with #367 landed first as required. "What the build
changed" at the bottom records where reality disagreed with the plan.*

## The itch

The plugin architecture's founding principle was **"no different rules for
plugins and core: one grammar, one completion pipeline, one option model."**
What actually happened is that plugin globals got *task parameters'* rules —
which are better than core's — so core is now the dialect. Two systems declare
options, two resolve them, and each got half the features:

| | core global | plugin `GlobalOption` |
| --- | --- | --- |
| declaration | `GLOBALS` tuple row | typed singleton |
| coercion / taught errors | hand-written per option (`int(str(g["jobs"]))`) | the parameter pipeline |
| `env()` | no — `--color`'s two vars hand-coded in `_resolve_color` | yes, by annotation |
| computed default | table callable (#367) | `default(fn)` |
| config | yes — `_switch` + ad-hoc ladders | **no** (the gap that started this) |
| `.given` | internal presence only | public property |
| choices completion | hardcoded `_GLOBAL_CHOICES` mirror | manifest-derived |
| bare-legal | iff the default column is non-None | always (the owner can ask `.given`) |

The history explains it without justifying it: the table predates the
annotation machinery; `GlobalOption` arrived with the plugin phases and rode
what existed by then. Every two-sources bug this month — `_call_plan` skipping
`default(fn)`, `check(fn)`'s two worlds, help describing a `--jobs` the run
didn't use, bare `--jobs` refused by `_app` after the splitter accepted it —
was this same shape at smaller scale.

## The target

**One class.** Core's own globals become `GlobalOption` instances — internal
module-level singletons — and every derived surface reads *them*:

```python
JOBS = _core_option(
    "jobs",
    Annotated[int, between(1, None), default(_default_jobs)],
    alias="-j",
    hint="N",
    config=True,
    help="max parallel tasks",
)
COLOR = _core_option(
    "color",
    Annotated[Literal["always", "never", "auto"], default(_color_from_env)],
    hint="WHEN",
    config=True,
    help="when to colour",
)
```

- The `GLOBALS` tuple table becomes a **derived view** (name, alias, kind,
  hint, default, help) built from the instances at import — `_parse_globals`,
  help, and the markdown table keep their exact current inputs.
- `_switch` and the ad-hoc `jobs`/`color` ladders die. One ladder, below.
- `--color`'s `NO_COLOR`/`FORCE_COLOR` handling becomes a declared
  `default(_color_from_env)` — `env()` cannot express presence-semantics
  (NO_COLOR set → `never`), a computed default can, and then it shows in
  `--help` as `(computed)` like anything else.
- `--jobs=abc` gets the coercion pipeline's taught error instead of a
  hand-rolled one; `--jobs=0` gets `between`'s.
- Core options gain `.given` (today the profile-plugin question "was it
  mentioned" is only answerable for plugins).

**One ladder** (Willem's rulings, 2026-08-09):

    CLI  >  env()  >  config  >  default(fn)  >  declared default

- `env` outranks `config`: an exported variable aims at this invocation, a
  project setting at every invocation.
- Config-sourced is **not** `given`, exactly as env-sourced is not.
- Core gains no env vars by this: unification makes `env()` *possible* on a
  core option, and no core declaration uses it. There is no `FOOTMAN_JOBS`
  unless one is ever deliberately declared — a decision, not a side effect.

**`config=` lands once, for both.** `config=True` reads the key named like the
option; `config="key"` names it (for a flag renamed around a collision — flag
and key are different namespaces, and only the flag's is shared).

## Where a plugin's config lives

Rulings from the 2026-08-09 thread, recorded:

**The namespace is a reserved child**: `[tool.<brand>.plugins.<section>]`.

- A bare de-dotted section directly under the brand table can collide with a
  scalar key — `[tool.footman.progress]` next to `progress = false` is a
  **TOMLDecodeError** that takes the whole file down and blames a line number.
  Under `plugins.` the names have their own namespace and the collision is
  unrepresentable. Cost: the word `plugins` is reserved as a key name.
- The outer table already follows the brand (`_paths.config_table()`), so
  hse's plugin config lives at `[tool.hse.plugins.…]` with no new mechanism.

**The section name derives from the entry point, de-dotted**
(`acme.devkit` → `acme-devkit`), because TOML's dot in a bare key *is* the
nesting operator and the quoted spelling (`"acme.devkit"`) fails silently when
users omit the quotes.

**Derivation needs provenance `_stamp` doesn't write yet.** Pulls stamp the
entry-point identity on groups and task fns; `GlobalOption` only records its
defining *module*. Extend the stamp to `contributions["globals"]` — the pull
already knows, it just doesn't write it down.

**Overrides exist at both grains**:

- per plugin: `footman.config_section("devkit")` — for a name the derivation
  gets wrong or ugly;
- per option: `config="key"` — the key inside the section.

**Edges, with recommendations:**

- Two entry points de-dotting to one section (`acme.devkit` / `acme-devkit`):
  refuse at discovery naming both — the flag-collision law's habit.
- `include()` has no entry point: `config=True` there is refused unless the
  module declares `config_section(...)` — nothing to derive from, so say so.
- One singleton reached through two pulls is one option (the collision law),
  so its derived section would depend on pull order: refuse `config=True` on a
  multiply-pulled option without an explicit section — loud beats
  order-dependent.

## `KEYS` partially derives

Config keys with a CLI counterpart derive from the options that declare
`config=`; the six keys with none (`tasks`, `cwd`, `gc`, `cascade`, `fetch`,
`shell`) stay a hand-written residue. One renderer emits both. The
both-spellings guard test dissolves for the derived half (nothing to drift);
the `_switch`-names-a-documented-key guard dies with `_switch`.

## The hot path is untouched

`_complete.py` keeps its hardcoded mirrors — stdlib-only is the invariant, so
they cannot derive at import. What changes is what the mirror *tests* compare
against: names, choices (`_GLOBAL_CHOICES`) and file-valued options
(`_GLOBAL_FILES`) all get pinned to the declarations, so a drifting mirror
fails CI. Today only the name set is pinned.

## What stays different, on purpose

- **Short aliases**: core's namespace (ruled in the bare-mentions note — 26
  letters, first-to-claim would poison the ecosystem). The internal factory
  takes `alias=`; the public constructor doesn't have the parameter.
- **Terminal actions** (`--install-completion` trio, `--describe`, `--where`,
  help/version/listing): still consumed by the app, not read by tasks. Their
  *declarations* unify; their behaviour doesn't move.
- **Staging**: the pre-discovery lenient walk and the handful of early
  consumers (`--json` for error shape, colour for error paint, `-C`/`-f`/
  `--config`, the uv handoff) keep reading lexically before discovery, exactly
  as today. Resolution stays two-stage; it stops being two *systems*.
- **The plugin two-pass**: plugin options still can't exist before discovery.

## Registration without the carriage

Constructing a `GlobalOption` *is* registering it (the plugin carriage), and
the collision law refuses names colliding with core's — core instances riding
the carriage would refuse themselves. The internal factory constructs without
registering; the collision law's "collides with footman's own" check reads the
derived table it always read. Watch the import cycle: the instances live where
`_split` can import them without `registry` importing `_split` at module
scope (it doesn't today — the collision law's import is function-local).

## Staging — each phase lands green on its own

1. **Declare + derive.** Core options as instances; `GLOBALS`, `_GLOBAL_*`
   maps, `_VALUE_OPTIONAL` derived; mirror tests extended to choices/files.
   No behaviour change — the diff is declarations in, table out.
2. **One ladder.** `bind_global_options` resolves core options too; `_switch`,
   the jobs/colour ladders, and `_resolve_color`'s env reads die; `config=`
   arrives for core's seven config-backed options. The `g` dict stays as the
   early-stage facade; post-discovery consumers migrate to `.value`/`.given`
   opportunistically, not wholesale.
3. **Plugin config sections.** The `plugins.` namespace, entry-point
   derivation via the extended stamp, both overrides, the three refusals.
   plugins.md rewritten; the `[tool.footman."acme.devkit"]` convention
   retired (documented-only — nothing shipped reads it; CHANGELOG says so).
4. **`KEYS` derivation** and the guard-test cleanup.

Tests follow the session's hard-won rule: **span the seam**. Help text against
run behaviour, declared config key against what resolution reads, derived
section against what `inv.config` indexing finds — never one half alone.

## Open decisions (Willem's)

1. **Prefix-stripping**: `hse.devkit` under the `hse` brand → `plugins.devkit`
   (his proposal), or plain de-dot always → `plugins.hse-devkit` (my
   recommendation: the stripped name makes one plugin's section depend on
   which runner reads it — a monorepo running both spells it twice).
2. **The residual `KEYS` six**: leave hand-written (my recommendation), or
   invent declarations for keys with no CLI surface.
3. **Internal consumption**: how far `_app` migrates from `g` to `.value`/
   `.given` in phase 2 (recommendation: facade stays, migrate where touched).

## Rejected

- **Bare de-dotted sections under the brand table** — the TOMLDecodeError
  collision above; found by testing, not foresight.
- **A shared flat `[tool.footman]` for plugin keys** — drags the flag
  collision into config; the sub-table is what keeps the namespaces apart.
- **`config()` on task parameters** — a task file's author owns the adjacent
  pyproject, so `default(lambda: my_settings()["region"])` already says it;
  the cascade (inherited tasks) is the one case that could reopen this, noted
  and deferred.
- **Env vars for core globals** — nothing asked for them; brand-aware naming
  (`ACME_JOBS`?) has no good answer; the ladder makes them declarable later
  without ceremony.

## What the build changed

Five places where building it disagreed with planning it.

- **A REQUIRED sentinel, not a shared `None`.** The plan said "bare-legal
  iff the default column is non-None" and left the mechanism unstated: a
  plugin's `default=None` IS a value (absence hands the owner `None`), while
  four core rows have no default at all. `_split._REQUIRED` is the
  distinction, and the rule holds with no plugin/core exception.
- **Two latent plugin bugs led the build**, restored from the bare-mentions
  note after this plan dropped them: a `list`/`dict` global bound last-wins
  with commas kept, and a bool had no `--no-x`. Fixed first (PR #372) — the
  parity they establish is what "bind like a task option" then meant.
- **`--uv` stays lexical.** The plan listed the uv handoff among the staged
  early consumers and still counted `uv` in the seven config-backed options;
  building it showed both handoff probes read throwaway configs *before any
  bind exists* and may replace the process. `UV` declares `config=True` for
  the table and the guards; `_uv_wanted` resolves it by hand, the one stated
  early consumer.
- **`KEYS` derivation shrank on contact a second time, and became a guard.**
  The first shrink (bare-mentions build): six keys have no CLI surface. The
  second: a computed default (`cores - 1`) has no machine-independent
  rendering a docs page could bake, and the reference prose is deliberately
  richer than flag help. What landed pins exactly the derivable agreement —
  key exists, bool values/defaults, choice lists — and leaves the prose
  editorial (`test_the_keys_table_agrees_with_the_declarations`).
- **The removed-`plugins`-key tombstone nearly ate the namespace.** The
  reserved `plugins.` child parses as a `plugins` table in the merged
  config, and the tombstone refused on truthiness. It now refuses only
  non-table values, keeping the old key's teaching while the new namespace
  lives beside it — found by the first end-to-end test, not foresight.
