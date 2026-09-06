# Failure injection in recordings

Status: FEATURE REQUEST — analysis only, nothing designed or built.
An addendum (2026-08-21, at the bottom) narrows the scope and proposes
adopting the table grammar toolroom has since shipped.
The ask (outstanding, not yet filed as an issue): a test using
`recording()` should be able to introduce failure points — designate
that specific recorded calls yield a failing `Result` (chosen code,
chosen stderr) instead of the default success shape — so a task
body's error paths (`nofail`, `keep_going`, fail-fast, Result
adjudication) can be exercised without live tools.

Written 2026-08-05, the day the toolroom split was fully ruled
([20260801-tools-namespace-package.md](20260801-tools-namespace-package.md),
[20260801-tools-separate-repo.md](20260801-tools-separate-repo.md)),
because the interaction analysis between the two threads is worth
keeping: the split barely touches this feature, and the feature
stress-tests two of the split's rulings and validates both.

## Where it lives

`recording()` is a Context mode: each `run()`/`tools.*` call inside
the block appends a `Result` instead of executing. Injection is
therefore an execution-lane feature at the `run()` door — it lands in
footman, nothing of it moves to toolroom, no shared machinery
appears. After the split, every bridge call still passes that door
via host detection, so injection covers `tools.*` calls without
toolroom knowing the feature exists.

## How it interacts with the split

- **Host detection is load-bearing.** Mount-dependent executor wiring
  would let a test without `plugin("toolroom")` silently bypass the
  recording — injected failures would never fire. The docstring's
  "each `run()`/`tools.*` call appends" promise survives the split
  *because* routing is detected, not mounted.
- **The one guardrail the split imposes:** the matching/addressing
  API must speak stdlib — positions, token tuples, string patterns —
  never bridge types (no `isinstance(step, toolroom.Tool)`).
  toolroom vocabulary inside footman would recreate the arrow the
  inversion removed. A recorded step's identity is its token list, so
  this costs nothing.
- **Injected failures exercise the real paths.** The faked `Result`
  is footman's sealed class (the bridge never constructs one), so an
  injected failure flows back through the bridge into
  `nofail`/`keep_going`/fail-fast handling exactly like a live one.
- **Standalone toolroom needs nothing.** The Executor protocol *is* a
  failure point: a fake executor returns whatever failure a
  standalone test likes. Any standalone testing ergonomics are a
  toolroom-side convenience over the seam — later, if ever.

## Consequences for sequencing and surface

- **Build it in footman, before the split.** It hardens the
  `run()`-door contract the split leans on, and toolroom's post-split
  CI (dogfooding a *released* footman) wants exactly this to test
  provision/audit error paths.
- **It is seam-adjacent public surface from day one.** toolroom's CI
  and hse will pin it — design it as public `footman.testing` API
  with a stability note, not an internal hook.
- **The deliberate blind spot stays.** `installed_version()` sits
  outside the task context "so dry-run and recording can't lie to
  it", and the split preserves that property. Injection therefore
  cannot simulate a missing or outdated tool — if the request
  includes that scenario, it is a separate availability-faking lane
  to scope explicitly, not a bolt-on to recording.

## The honesty line

The `step=False` arc taught that report-shaping switches in
production get counterfeited. Recordings are the other side of the
line — sanctioned test doubles whose job is to lie. Failure injection
there is the honest lane; no counterfeit risk.

## Open (the feature itself is undesigned)

- The addressing surface: by index, by token pattern, or both.
- The failure payload: code + stderr (+ stdout?); one-shot vs
  every-match.
- Whether availability faking (the missing-tool scenario) is wanted
  as its own lane, or explicitly out of scope.

## Addendum (2026-08-21): adopt toolroom's table grammar

(The plan note,
[20260801-recording-injection.md](20260801-recording-injection.md), was
rebased the same day and carries the current design. It keeps this
addendum's grammar but widens the scope back to the whole `run()` door
— bridge calls route through it under a live context anyway — and adds
the two answer kinds toolroom's table deliberately refused: exception
values and ordered sequences. Read it, not this, for what to build.)

toolroom has since shipped its standalone-side testing ergonomics —
the "later, if ever" convenience this note anticipated over the seam —
as `toolroom.testing.answers()` (toolroom's
`notes/20260821-testing-seam.md`). That changes this feature's scope
and settles its two open surface questions.

### What narrows

Bridge calls no longer need this feature for canned stdout or injected
failure: `answers()` intercepts at toolroom's `_host` seam — upstream
of `run()`'s door — with real handles doing real rendering, both lanes,
footman present or not. Nested inside a `recording()`, `answers()`
wins and the record sees nothing (pinned by toolroom's conformance
suite against released footman, so the nesting contract is already
load-bearing).

What remains is exactly the calls toolroom cannot see: **plain `run()`
calls** — a command string, a bare list, an in-process callable — with
no toolroom handle behind them. That is still a real population (task
bodies shell out directly all over), so the feature stays worth
landing, but it is failure/answer injection for `run()` calls only.
The "canned stdout is a semantic stretch for a recorder" worry shrinks
with the scope: for the calls left, there is no other door.

### What settles

The two open questions (addressing surface, failure payload) now have
a worked answer with a consumer already trained on it. `answers()`
takes a table:

- **Keys are argv prefixes** — tuples of tokens, or one string split
  on whitespace; the longest matching prefix wins. Matching is against
  the name-led token list, which for a `run()` call is simply its
  split command — stdlib through and through, satisfying this note's
  one guardrail (no bridge types) by construction.
- **Values are the answer**: a `str` is stdout with exit 0, an `int`
  is an exit code, a full `Result` sets code and both streams.
- **Unmatched calls keep recording's default** — silent success — so
  the empty table degenerates to today's `recording()` exactly.
- **A non-zero answer takes the failing lane honestly**: returned
  under `nofail`, raised as `RunFailed` otherwise, so
  `keep_going`/fail-fast/adjudication exercise their real paths with
  footman's sealed `Result`, per the original analysis.

Proposed spelling: `recording(answers={...})` — the parameter named
for the sister feature, so a consumer who learned one table teaches
themselves the other. hse already holds both packages; toolroom's CI
pins `footman.testing` — one grammar, two doors (`answers()` for
bridge calls, `recording(answers=)` for `run()` calls) is the whole
story a downstream test suite has to learn.

Per-match counting (one-shot vs every-match) stayed out of toolroom's
v1 and should stay out here for symmetry until a consumer asks; same
for callable values. The deliberate blind spot is unchanged:
`installed_version()` remains outside the task context — toolroom now
cans it from *its* table via the seam's `probe()` door, which is
toolroom's business, not a recording's.
