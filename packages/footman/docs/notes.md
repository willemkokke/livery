# Notes & levels

footman watches how a task treats the process it shares with its
siblings. When a task spawns through raw `subprocess`, writes
`os.environ` directly, or reads a working directory that isn't its own,
footman does the safe thing at the site — scopes the write, fills in the
spawn's `cwd` and `env`, answers honestly — and says so, once, on
stderr. Each of those sayings is a **note**, and every note has a
**kind**: a family plus, where one exists, an instance naming what
happened — `environ-write:JAVA_HOME`, `popen-inject:git`,
`lane-wait:serial`.

Notes exist because the safe thing footman did may not be the thing the
author meant. The note teaches the deliberate spelling; the levels below
decide how loudly — up to failing the task, which is what makes a note
enforceable in CI rather than a line an agent scrolls past.

## The four levels

Every kind carries a level, and a project can reclassify any kind:

- **`trace`** — recorded, printed only under `-v`. There is no "off":
  invisible-unless-asked is the mute, and someone who asked to see
  everything still sees it.
- **`info`** — printed once per task and kind, prefixed `info:`.
- **`warning`** — the same, prefixed `warning:`: worth fixing.
- **`error`** — printed as it fires *and* the task **fails at its
  boundary**, listing every banned note with its site.

Whatever printed, every fired note is recorded on its task's row in the
[`--json` envelope](json.md) — `trace` included, since a machine reading
the run wants the record, not the terminal's gating — as
`{kind, level, site, text}` objects under `notes`.

Two properties make the record useful. Notes **dedup per task, kind and
instance**: a task that sets ten variables gets ten notes, one each —
all issues surface in one run, never fix-one-and-see-the-next — while
the same variable written in a loop says it once. And every note carries
its **site**: the first stack frame outside footman, as `file:line`, so
five findings are five places to jump to.

## Why `error` fails at the boundary, not at the site

By the time a note fires, footman has already handled the operation
safely — nothing is prevented by stopping early, and stopping early
would show exactly one issue per run. So an `error`-classified note lets
the body run to completion and fails the task at its boundary with the
full list:

```text
error: task deploy sets JAVA_HOME via os.environ — … [tasks.py:41]
error: task deploy spawns via raw subprocess — … [tasks.py:50]
fm: deploy: 2 banned notes: environ-write:JAVA_HOME at tasks.py:41;
popen-inject:git at tasks.py:50 — fix the site, or classify a
known-harmless instance in [tool.footman.notes]
```

The same bargain `--keep-going` makes: see everything, then fail
honestly.

## Classifying kinds: `[tool.footman.notes]`

The table maps patterns to levels. A key is `[task/]kind` — either side
may be `*` — and the most specific rule wins, in this order: exact
`(task, kind)`, then `(task, family)`, `(task, *)`, `(*, kind)`,
`(*, family)`, the `"*"` blanket, and finally the kind's built-in
default. Tasks are their dotted addresses; `/` appears in neither side,
so the spelling cannot collide.

```toml
[tool.footman.notes]
"*" = "error"                          # ban everything…
"environ-write:JAVA_HOME" = "info"     # …except this audited variable
"docs.build/popen-inject:dot" = "info" # …and this task's graphviz spawn
"migrate/getcwd" = "info"              # …and one task's cwd reads
```

Because the specific entries outrank the blanket, this is also the
adoption path: run at the defaults and let the warnings surface
everything at once; audit each finding by its site; give known-harmless
third-party behaviour a pinned entry as narrow as the evidence supports;
and only then raise the blanket to `error`. The audited entries survive
the flip, and every *new* instance — a different variable, a different
program, a different task — lands outside them and hits the wall.

An unknown kind in the table is refused by name, so a typo cannot become
a wall someone merely believes is up. The instance half of a
parameterised kind is runtime data (footman cannot enumerate your
variables), so only the family is checked: a misspelled instance
silently never matches.

## The kinds

--8<-- "packages/footman/_generated/notes.md"

Kinds with an instance dedup and match per instance; the bare kinds are
addressable per task (`migrate/getcwd`), which is the carve-out shape
for an audited third-party behaviour that has no finer name.

Two relatives live outside this system on purpose: `os.putenv` and
`os.unsetenv` are refused outright in a managed task (they bypass even
`os.environ`), and the pre-run advisories — a group default's positional
naming a child task, a script block ignored inside a pinned project —
teach about circumstances rather than code the author controls.

## In tests

`Runner` results carry the same records: `result.results[0].notes` is
the list of `Note` objects an invocation fired, levels resolved against
whatever config the invocation saw. Asserting a task fires no notes at
all is the strictest form of the ban, and needs no config to say it.
