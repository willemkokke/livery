# Scripted answers for recording() — output/failure injection at the run() door

Status: **LANDED** 2026-08-21 (the same day it was rebased) — built as
designed below, plus the five calls Willem made at build time: reviewers
run on a scripted draft; command strings are matched as a word-boundary
prefix, never tokenised (the same rule on every platform); exception
values are instances only; recorded steps keep the `env` and `cwd` they
would have run with (`Result.env`/`.cwd`, `None` on a live record);
`Runner.invoke(answers=)` rides the same injection seam `stdin=` uses and
implies `--dry-run`. The rest of this note is the design as it stood
when the build started.

Earlier status: PLAN — rebased 2026-08-21 onto footman 0.43.0 and toolroom
0.6.0 (`toolroom.testing.answers()`, toolroom's
`notes/20260821-testing-seam.md`). Later the same day toolroom 0.6.1
(the as-handed `Call` record) and footman 0.44.0 both released, so
every dependency this note names exists; only the build itself
remains. hse reviewed the design from the consumer side (2026-08-21,
after devkit 0.0.46 retired `FakeTool`): three of the opens below are
now settled by that review, and the acceptance population is theirs. The original 2026-08-01 draft posed
ten decision points; the table grammar toolroom shipped settles six of
them, this rebase settles a seventh, and the remaining opens are listed
with sharper edges than they had. Origin unchanged: hse's 0.29.1
conversion report — suites that assert on tool *output*, *failure*, and
*environment*, which `recording()` can do none of. The acceptance test
is still theirs: when this ships (with toolroom 0.6.1's as-handed
`Call` record), their `FakeTool` becomes deletable.

## The two doors

toolroom shipped the hermetic half: `answers()` swaps `_host.run`/
`_host.probe` for a table, upstream of footman, real handles doing real
rendering, footman present or not. Nested inside a `recording()`,
`answers()` wins and the record sees nothing (pinned by toolroom's
`tests/test_testing.py`). That door is the only one a footman-free
consumer (hse-sdk) can hold, and it deliberately stays a minimal
static table.

This feature is the other door: `recording(answers={...})` at footman's
`run()` entry. Its population is **the whole door, not just plain
calls**: under a live footman context, host detection routes every
bridge call through `run()` (toolroom's `tests/test_detection.py` pins
that detection cannot be bypassed), so one `recording(answers=)` block
covers a task body's plain `run("git push")` *and* its `tools.git.push()`
in the same table. What distinguishes this door is not population but
**vocabulary**: it is the hosted rehearsal, so it owns the failure
lanes — and it takes the two answer kinds toolroom's table deliberately
refused:

- **Exception-valued answers.** An `Exception` instance as a table
  value is raised at the door — the missing-tool story
  (`FileNotFoundError`), which hse guards with four production
  `except OSError:` branches and today can only test by patching
  `footman.run`. A raise at the door propagates back through a bridge
  handle exactly as a real spawn failure would.
- **Sequenced answers.** A `list` of answers is consumed in order for
  repeated matches of the same prefix — the `side_effect`
  constituency (hse's `test_version.py`/`test_status.py` patch their
  git seam ~120 times with ordered reply lists; a static table cannot
  express "the same `git describe` answers differently twice").

Division of labour, stated once: `answers()` replaces the world;
`recording(answers=)` scripts the rehearsal. A suite that holds footman
uses whichever door fits the test — and nested, the innermost
(`answers()`, upstream) wins.

## The shape

```python
with recording(
    answers={
        "uv tool list": LISTING,  # str  → stdout, exit 0
        "uv build": 1,  # int  → exit code
        "git push": RunFailedResult,  # Result → code + both streams
        "uv python find": FileNotFoundError(),  # raises at the door
        "git describe": ["v1.2.0", "v1.3.0"],  # ordered: 1st, then 2nd match
    }
) as steps:
    ...
```

## Settled (was: decisions 1, 2, 3, 6, 7, 10)

- **Keying** (1): argv-prefix table, toolroom's grammar verbatim —
  tuple of tokens or one string split on whitespace, longest matching
  prefix wins. Matching is against the name-led token list: for a list
  command its tokens, for a command string its split, for a bridged
  call the normalised shown command (`_show.text(exact=False)` — the
  same spelling `Result.command` records). Stdlib through and through:
  the one guardrail the toolroom split imposed (no bridge types in the
  matching API) holds by construction.
- **Reply shape** (2): `str`/`int`/`Result` base values, plus the two
  additions above. Results stay sealed — a table's `Result` value is
  re-minted at the door with the real `command`/`tokens`/`address`, the
  same way `answers()` re-mints into its own vocabulary. No
  `timed_out` in v1 (nothing in the acceptance population needs it).
- **Failure semantics** (3): a scripted non-zero takes the real lane —
  returned under `nofail`, raised as `RunFailed` otherwise, seats a
  failed step, fail-fast sees it. This was never really open; it is
  the point.
- **Unmatched recorded calls** (6): today's blank success — the empty
  table degenerates to today's `recording()` exactly, and the existing
  suites must not notice this feature shipping.
- **Consumption** (7): scalar values answer every match (a listing
  asked twice is the same listing); list values consume in order.
  What an exhausted list answers is open below.
- **Naming** (10): `answers=` — the parameter named for the sister
  feature, one grammar, two doors. A consumer who learned one table
  taught themselves the other.
- **`recorded=False` interception** (was decision 5; ruled by hse):
  an explicit match intercepts even an off-record call; unmatched
  off-record calls keep executing truthfully (pinned today by
  `tests/test_context.py:2431`). This is a *requirement*, not a lean:
  hse's `_git` seam makes every call `recorded=False, nofail=True`,
  and nearly every double they have left stands in for that seam —
  without interception the feature retires almost nothing. The test
  author declaring the world beats honest execution.
- **Precedence when both tables are active**: toolroom's `answers()`
  wins, because it intercepts upstream at `_host.run` and footman's
  door never sees the call — the same rule its docs already state
  against a plain `recording()`. Written down here so nobody discovers
  it by debugging; the build should pin it with a nested test.
- **Exhausted sequences**: a list that has answered its last entry
  refuses the next match by name (the prefix and the count), in the
  manner of toolroom's unmatched probe. A sequence is a script;
  running past its end is the test being wrong, not the world being
  benign. hse agrees — it matches how they treat every other
  silent-fallback case.

## Implementation facts (so the build is mechanical)

Gathered 2026-08-21 against 0.43.0:

- The slot is one place: `src/footman/context.py:3120`, the
  `ctx.dry_run and recorded` branch, between the tokens computation
  (`:3116`) and the `Result(0, …)` mint (`:3129`).
- **Command strings carry `tokens == ()`** (`to_argv()` refuses
  strings), so matching must split at match time — `shlex.split` on
  POSIX, mirroring what the real spawn path does at `:3236`. Windows
  hands command strings to subprocess unsplit; the matching story
  there needs a decision (lean: match on the POSIX split anyway — the
  table is a test artifact, not a spawn).
- The dry-run branch **returns before the failing lane**: the
  `nofail`/`RunFailed` gate lives at `context.py:3404-3412` and is
  unreachable from the `:3141` early return. An injected non-zero
  needs that logic reachable from the recording path (shared tail, not
  a duplicate).
- `recording(**overrides)` splats everything into `Context`
  (`testing.py:87`) — `answers=` must be carved out as a keyword-only
  parameter before the splat, or `Context.__init__` eats it.
- Plain callables are refused at the door (`context.py:3075`) unless
  they arrive from the bridge with `_show` — so the old decision 9
  ("in-process tools under recording") is moot: the only callables
  that reach the door are bridged, and they match like any other call.

## Still open

- **Reviewers** (was decision 4). Dry-run never runs `pre_record`
  (`context.py:3022` — "nothing ran, nothing captured"). With canned
  output that premise breaks: a scripted draft is reviewable, and
  hse's exit-code adjudication wants testing against scripted exits.
  If reviewers should fire, the review block (`:3306`) must become
  reachable from the injected path. Lean yes, but it widens the
  change.
- **Exception values**: instance only, or type too? Lean instance only
  (`FileNotFoundError("uv")`) — a type invites argument-less
  reconstruction guesses.
- **Run-policy capture.** Several hse sites assert on what a call was
  *handed* — `env=`/`cwd=` kwargs, and the *absence* of `env=`
  (`test_template_cmd.py:988`, `test_check.py:662/739`). Neither door
  records that today (steps are sealed `Result`s; toolroom's `Call`
  keeps `.opts` but only for bridge calls). Out of scope for
  `answers=` itself; if wanted it is its own lane — a richer step
  record, designed against the sealed-Result constraint.
- **`Runner.invoke` channel** (was decision 8). Now known to be a
  materially larger build: `invoke` has no Context seam — the executor
  mints its own (`testing.py:186-218`) — so `replies=` there is
  plumbing, not parameter-passing. Deferred until a branded-CLI suite
  asks. (Meanwhile `answers()` already works around `invoke`: the
  seam swap is process-wide.)

## The acceptance population (hse, confirmed 2026-08-21)

`FakeTool` is gone as of hse-devkit 0.0.46 — toolroom 0.6.1's
as-handed `Call` record was all that migration needed. What waits in
hse for *this* feature, in hse's own words:

- One private reach — `monkeypatch.setattr(toolroom._host, "run",
  raising)` in `test_emission_contract.py` — retires onto an
  exception value (the missing-binary case: an environment fault, not
  an answer, and footman's `run()` owns the `except OSError` path).
- The `_git.query` MagicMocks (`_fake_lsremote` in
  `test_template_cmd.py` and kin) and the `mock_run` sites
  (`test_version.py`, `test_status.py`, ~120 in all) — value reads
  through a seam that calls with `recorded=False, nofail=True` and
  answers from ordered `side_effect` lists. They need the
  `recorded=False` interception and sequenced answers together; that
  pairing is the feature's main consumer.
- `test_release_cmd.py:802/817` — `_run_cliff` retry logic on a plain
  `footman.run` — canned `Result` answers.
- `env=`/`cwd=` kwargs assertions on plain `run()` → the run-policy
  capture open above; until then those stay monkeypatches, honestly.

## Non-goals

- Not a general mocking framework: no call-count assertions, no
  argument capture beyond what `steps` already records.
- Not for live runs: answers exist only inside `recording()`.
  `--dry-run` stays exactly as it is — a rehearsal of the real world,
  never a scripted one. The `step=False` arc taught that
  report-shaping switches in production get counterfeited; recordings
  are the sanctioned doubles whose job is to lie, and this stays on
  that side of the line.
- `installed_version()` stays unfakeable from footman — it sits
  outside the task context on purpose. toolroom cans it from *its*
  table via the seam's `probe()` door, which is toolroom's business,
  not a recording's.
