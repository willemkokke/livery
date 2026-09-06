# The display lane

**Status: queued for design.** One slice of it is being built now — the
redaction chokepoint — because it is a security promise and could not wait.
The rest of the lane is unbuilt and undesigned.

Successor to `20260731-execution-model-spec.md` decision 4, which split the
verbosity question, ruled the default, and parked the remainder:

> **4: split as proposed, with the default ruled.** At normal verbosity the
> report shows TASK grain only; verbose adds steps. The full display thread
> (verbosity matrix, provenance markers, emission-time redaction) parks as
> its own post-model thread.

This note is that thread.

## Why it was parked, and why one piece jumped the queue

The parking was right. The three parts share a seam — they all decide what a
committed record *looks like* when it reaches a human — and designing them
one at a time is how you get three mechanisms that disagree. The house rule
for that shape is on the record: model the lane, not the fix.

Redaction jumped anyway, on 2026-08-16, because it is not only a display
question. `SECURITY.md:40` names "a profile trace, `--json` output, or a
progress line" as in-scope vulnerabilities, and says outright: "a case where
it doesn't is a bug worth reporting privately." Three surfaces were leaking a
`Secret` that the caller never unwrapped. PR #453 had already fixed the
returned-document and `--json`-returned halves, which left the promise
*half*-kept — the worst of the three states, because a reader who tests
`Secret` on a returned value concludes it works everywhere.

Ruled (Willem, 2026-08-16): build the one chokepoint the lane needs anyway
and route the emitters through it, rather than either patching the six sites
independently or waiting for the whole lane. The helper is not speculative
work — the lane needs exactly this seam whatever else it does, so this is a
down-payment ordered by which part is a security promise, not a shortcut
around the design.

## What the shape already is, and must stay

`20260731-execution-model-spec.md` ruled the shape while killing a "global
redaction plugin" as an observer write:

> it rewrote `returned` while the same secret sat in captured stdout, in
> receipts, in what dependents and `recording()` already held (the pristine
> value is handed over before any hook fires) … contract-free scrubbing is
> **emission-time display policy over committed records** (decision 4's lane)
> — the sound version of what the observer write only pretended to do.

So the invariant for everything in this lane: **the record keeps the truth;
the emission decides what is shown.** Redacting at the carrier's birth
(`_label()` / `argv_tokens()`) is the tempting shortcut and it is the exact
unsoundness that ruling killed — it would strip the value from `recording()`
and from what dependents hold.

One consequence accepted with the redaction slice: `RunFailed` builds its
message at *construction*, so redaction reaches into a value user code holds
(`except RunFailed as e: print(e)`). Ruled correct — it is still a display —
but it is the one place where "display policy" is not purely at the edge, and
the rest of the lane should not take it as licence to move earlier.

## Still to design

- **The verbosity matrix.** Decision 4 ruled only the two endpoints: task
  grain at normal, steps under `-v`, and a failed task auto-expands its
  failing step and audit line at normal verbosity (confirmed 2026-07-31).
  What `-q`, `-vv`, a non-tty, and `--json` each do to *every* row type is
  unwritten. PR #453 added a rule of this kind in passing — a stack shows
  under `-v` or whenever stderr is not a terminal — which belongs in the
  matrix rather than beside it.
- **Provenance markers.** How a row says where it came from: a plugin's task,
  a lifted step, a shared execution already run this run, a forwarded value.
  Some of this exists ad hoc (`same`, `skip`, `cut`).
- **Redaction beyond the command line.** The slice being built covers
  `Result.command` and the surfaces that render it. Captured stdout is not
  covered and cannot be by this mechanism — a task that prints its own secret
  has done so deliberately, which `params.py` already documents as the escape
  hatch. Whether the timing history and the manifest need their own pass is
  open; `SECURITY.md` names both.

## Open questions

- Does the chokepoint generalise past commands? A row's title, a task
  address, an audit line and a forwarded value are all rendered somewhere; if
  they need the same treatment, the helper wants a wider contract than
  "render a command for display".
- Where does `recording()` sit? It is a testing surface, not a human one, and
  it holds pristine values by design. If a recording is replayed into a
  report, it crosses into this lane.
- Is there a verbosity level at which redaction is *off*? The answer looks
  like no — `reveal()` is the documented, greppable unwrap, and a run-wide
  "show me the secrets" flag is precisely the audit list it was designed to
  make unnecessary. Worth writing down rather than leaving implied.
