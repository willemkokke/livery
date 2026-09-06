# Notes grow levels: trace / info / warning / error

**Landed** in the build after v0.46.0, alongside — but independent of —
the env-in-memo-key change (`20260831-env-in-memo-key.md`).

## Where this came from

The maintainer wanted footman's misuse notes (raw `subprocess` use,
`os.environ` writes, and their siblings) to be **bannable in CI so
agents can't ignore them**: an advisory line on stderr costs an agent
nothing to skip past; a failing exit code does not. The design grew over
one long review (2026-08-31 → 09-01) in which every piece was ruled
explicitly; this note keeps the reasoning and the rejected shapes.

## What existed before

One choke point, `_globals._note(kind, text)`: task-attributed,
teach-once per `(task, kind)`, written to the real stderr as `note: …`,
and nothing else — no levels, no config, no machine surface. Ten kinds,
plus one stray (`_executor._reserved_note`) that printed `note:` without
the choke point, per occurrence, with no dedup.

## The ruled design

**Levels, not ad-hoc severities.** Standard `trace / info / warning /
error`, the level as the stderr prefix. There is no `off`: trace prints
only under `-v`, which *is* the mute — visible exactly to someone who
asked to see everything. (First proposal was `note/error/off`; the
maintainer ruled "change our classifiers to standard
trace/info/warning/error", which made `off` redundant.)

**Kind tails carry instance identity, never the task.** The asymmetry
that forced this rule: early drafts had `lane-wait:<task>` (the waiter)
next to `global-read:<option>` (the thing). The maintainer rejected the
asymmetry outright; the resolution is that a tail names *what happened*
— `environ-write:<VAR>`, `popen-inject:<program>`,
`global-read/unread:<option>`, `lane-wait:<lane>` — while the task,
which every note already has (it is half the dedup key), became a
uniform config axis instead. `getcwd`, `fork`, `mp-start`, the two
recording notes and the unified hook-return note are genuinely
instance-less and stay bare.

**Config: `[tool.footman.notes]`, keys `[task/]kind`.** Both sides
globbable, most-specific-first:
`(task, kind)` → `(task, family)` → `(task, *)` → `(*, kind)` →
`(*, family)` → `"*"` → the kind's built-in default. Tasks are dotted
addresses and `/` appears in neither, so the spelling cannot collide.
An unknown family is refused by name; a parameterised tail is runtime
data and cannot be validated (a typo silently never matches — inherent).

**Dedup is uniform and deliberately fine.** `(task, full kind including
instance)`, for every level — error included ("nothing after matters").
The maintainer's explicit want: **see all issues at once**, never fix
one and have the next pop up. A task that sets ten variables notes ten
times, once each; the same variable in a loop notes once.

**Error collects at the boundary instead of raising at the site.**
Raise-at-site was the first ruling and was reversed by the
see-everything principle: a raise shows one issue per run. Every
interception is advisory-after-the-fact — footman has already done the
safe thing by the time `_note` fires — so nothing is *prevented* by
raising. Error level therefore: the immediate visible line per instance
(like warning), then the task fails at its boundary listing every
banned note with its site. Same bargain `keep-going` embodies: see
everything, then fail honestly.

**Every note captures its site.** The first stack frame outside footman
itself, as `file:line` — five issues means five pinpointed sites in one
run, which beats a traceback that shows one.

**Notes land in the `--json` envelope.** Once collection exists for the
error boundary, every fired note is already a structured record
`(kind, level, site, text)`; not attaching them to the task's item
would mean a second, poorer channel next to an existing one (the
maintainer: "if we collect them, we might as well store them in the
envelope"). All levels ride the envelope, trace included — the machine
channel ignores print gating. `Runner` results expose the same list.

**Default levels.**

| level | kinds |
| --- | --- |
| info | `environ-write:*`, `getcwd`, `global-unread:*`, `recorded-title`, `pre-record-recorded`, `lane-wait:*` |
| warning | `popen-inject:*`, `fork`, `mp-start`, `global-read:*`, `hook-return:*` |
| error | nothing — projects promote |

`lane-wait` was proposed as trace and ruled up to info: it is the
"why is my run stuck" heartbeat, and caution won. `fork` was considered
for a default of error and left at warning.

`global-unread` took a detour the release should have waited for. The
build shipped it at **trace**, deviating from the info in this table,
because its old call site was deliberately `-v`-gated ("never nags an
ordinary run"): its evidence is "never read *this run*", and a task
that reads an option only inside a branch would nag on every run that
skips the branch — a false positive by construction. The deviation was
flagged in the PR and v0.47.0 released with the flag unanswered; the
maintainer's answer landed immediately after and reframed the problem:
the conditional read has a one-line idiomatic fix — **read a declared
option unconditionally and branch on the value** (the read is a cheap,
side-effect-free lookup) — and with that as the taught idiom, "never
read this run" collapses into "never read at all" and the evidence
becomes trustworthy. Ruled to **info** in the release after v0.47.0,
with the idiom in the note's own text. Residuals accepted:
caller-declares-callee-reads still notes (the hoisted read that passes
the value down is the fix there too), and a deliberately lazy reader
reclassifies `global-unread:<option>` per project. Process lesson,
recorded for its own sake: an unanswered deviation flag is enough to
hold a release for.

**The whitelist workflow** the design serves (the maintainer's stated
adoption path): run at defaults → audit each finding using its site →
pin narrow entries for known-harmless third-party behaviour
(`"environ-write:JAVA_HOME" = "info"`, `"docs.build/getcwd" = "info"`)
→ once clean, `"*" = "error"`. Precedence makes the flip safe: the
pinned entries outrank the blanket, and every *new* instance lands
outside the whitelist and hits the wall.

## Rejected

- **Raise-at-site for error** — one issue per run; reversed (above).
- **A third match axis on origin** (the offending frame's package, for
  "harmless when sphinx does it") — deferred until a real audit forces
  it: a library is nearly always called from one or two tasks, so the
  task axis covers it (`docs/environ-write:JAVA_HOME`), and the frame
  is captured from day one, so nothing is lost by waiting.
- **CI-only enforcement** — a ban that holds only in CI teaches agents
  the local run is lax; the policy lives in `pyproject.toml` and CI
  inherits it.
- **A hand-written kinds table in the docs** — the kinds are a
  registry; the docs table generates from it in the existing snippet
  lane (`_generated/`, `--8<--`-included, drift-guarded), so a new kind
  cannot ship undocumented.

## Adjacent facts established during the review

- Notes print-gating vs `-v` reaches the emitter through the task's own
  context (`ctx.verbose`).
- `putenv`/`unsetenv` are hard errors, not notes — they bypass even
  `os.environ` — and stay outside this system.
- The splitter's advisory notes (`seg.notes`), the sh-has-no-pipefail
  line and the plugin-resolution advisory are teaching about
  circumstances, not code the author controls; they stay outside the
  levels system.
