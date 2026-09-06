# The testing seam: `toolroom.testing.answers()`

**Status: landed** — `toolroom.testing` shipped with `_host.probe()` and
the `installed_version` rewire described below.

## The evidence

hse grew a `FakeTool` class (`hse.devkit.testing`, shipped in
hse-devkit's public surface) that hand-imitates a toolroom handle:
`__getattr__` chaining into verb paths, `opts(env=)`, `flags()`, kwargs
rendered to `--kebab-case=value`, every call appended to a `calls` list,
canned stdout keyed by an argv prefix, and failure injection by
substring. Beside it live two older private clones of the same idea
(`_FakeUv`, `_FakeGit`). Its docstring explains why it exists: once
calls went through the bridge, patching `subprocess.run` stopped
intercepting anything, and footman's `recording()` records but cannot
*answer* — no canned stdout, no injected failure — while hse's suites
assert on both (parsing `uv tool list` output; README rollback after a
failed `uv build`; `git reset --keep` after a failed push).

Three copies of a by-hand bridge imitation in one consumer is demand.
It is also drift: `FakeTool` renders every kwarg as `--k=v`, where the
real bridge emits bare boolean flags, the `off` sentinel, single-dash
Go-style options, repeated list flags, and git's `-c color.ui`
injection. A test asserting against the fake's rendering asserts the
fake.

## The decision

toolroom ships the double, and the double fakes **execution, not the
handle**. `toolroom.testing.answers()` is a context manager that swaps
the two doors every call already leaves through — `_host.run` for calls,
`_host.probe` for version reads — for a table lookup:

```python
from toolroom.testing import answers

with answers(
    {
        ("uv", "tool", "list"): "hse-devkit v0.0.18\n- hse\n",
        ("git", "push"): 1,
    }
) as calls:
    release()  # the code under test, calling real handles

assert calls[0].argv[:3] == ["uv", "tool", "list"]
```

The handles under test stay completely real: chaining, flag
translation, `off`, secrets redaction, colour switches, and input
consumption are all the bridge's own, exercised exactly as a live call
would exercise them. Only the seam answers differently — which is the
one part of a call a test has no business exercising.

Vocabulary:

- **Keys** are argv prefixes — tuples of tokens (a string key is split
  on whitespace as a convenience). The longest matching prefix wins.
  Matching is against the *name-led* argv (`git …`, `python …`), never
  the resolved path, so a canned answer reads like the call it answers.
- **Values** are the answer: a `str` is stdout with exit 0, an `int` is
  an exit code, a `Result` sets code and both streams.
- **An unmatched call succeeds silently** — exit 0, empty streams —
  the same benign default `recording()` gives every call, so
  `answers()` with no table is a pure call recorder.
- **A non-zero answer takes the failing lane honestly**: the result
  comes back under `nofail`, and raises otherwise — `ToolError` by
  default, footman's `RunFailed` under `hosted=True` (below).
- The yielded list holds one `Call` per interception: `.argv` (name-led
  raw tokens), `.raw` (what would actually have spawned —
  interpreter path, colour switch and all), `.command` (the shown,
  redacted line), `.env`, `.opts`, `.probe`.

### `hosted=True`

hse-devkit's code runs inside footman tasks in production and handles
footman's `RunFailed`; its tests drive that code from plain pytest,
where no footman context is live. `answers(hosted=True)` simulates the
hosted lane for the block: `_host.hosted` answers True (so the bridge
takes its hosted paths, in-process tools included — the callable is
recorded, never invoked), successes come back as footman's `Result`,
and failures raise footman's `RunFailed`. The default (`hosted=False`)
leaves detection alone and speaks toolroom's own vocabulary
(`Result`/`ToolError`) — what a footman-free consumer (hse-sdk) tests
against.

## `installed_version`: probe, don't route

`installed_version()` bypassed the seam entirely — a raw
`subprocess.run` — and the bypass was deliberate: it is a value read
that must answer truthfully, so `--dry-run` and `recording()` *cannot
be allowed* to lie to it. That ruling stands. What changed is where the
spawn lives, not what it means:

- The spawn moved into the seam as **`_host.probe()`** — same
  semantics to the byte (capture, UTF-8/replace, timeout, caller-built
  environment), still outside any host, still untouched by footman.
  `_host` is now genuinely "the one door" its docstring claims.
- `probe()` takes the name-led spelling alongside the real argv, so
  the testing seam can match `("git", "--version")` however the path
  resolved. Execution ignores it.
- `answers()` intercepts `probe` with the same table — the canned
  version line is parsed by the real `read_version`, so the test
  exercises real parsing. An **unmatched probe refuses** rather than
  answering empty: an empty answer would surface as
  `installed_version`'s own "could not read a version" `ValueError`,
  which reads like a bug in the code under test; the refusal names the
  missing table entry instead. (This is the one asymmetry with
  unmatched calls, and it is on purpose: a call's exit code is often
  incidental to a test, a version read never is — code branches on it.)
- `answers()` snapshots, clears and restores the per-process
  `_version_cache`, so a canned version can neither be pre-empted by a
  real one read earlier in the process nor leak into later tests.

### Rejected: routing the probe through `run(recorded=False)`

footman's `recorded=False` exists for truthful value reads under
orchestration, and it would have made `run` the single door with no new
function. Rejected because it changes hosted behaviour for no gain: the
probe would start traversing footman (receipts, verbose display, the
colour ladder, version-dependent `run()` signatures) when the whole
point of the original bypass was that a version read is *not a step of
anyone's story*. `probe()` keeps the ruling and gives tests their
interception anyway.

### Rejected: a `FakeTool` port

Shipping hse's class (or a polished cousin) would bless the drift
problem rather than fix it: the fake's rendering is a second
implementation of the bridge, permanently chasing it. It also bakes in
footman's value types, which a toolroom-only consumer cannot hold.

### Rejected: leaving it all to footman's `recording()`

footman's own note (20260805, recording failure injection) designs
`recording(…)` growing injected failures at the `run()` door, and that
feature is still worth landing — it serves pure-footman tests with the
tool they already hold. It cannot be the whole answer here: it needs a
live footman, hse-sdk has none, and canned *stdout* (the other half of
what `FakeTool` exists for) is a bigger semantic stretch for a recorder
whose contract is "nothing executes, everything succeeds silently".
`answers()` is the hermetic world; `recording()` is a rehearsal inside
the real one. Nested, the innermost wins: `answers()` intercepts at
`_host`, upstream of footman, so inside a `recording()` block its
answers stand and footman's record sees nothing — which is what
"replace the world" has to mean.

## Invariant amendments (CLAUDE.md updated to match)

- **The wheel** is now the bridge + the testing seam + stubs. Same
  zero-runtime-deps rule: `toolroom/testing.py` imports stdlib and
  `toolroom._host` at module level, nothing else.
- **footman imports in `src/toolroom/`** were "lazily and only behind
  `_host.hosted()`". `testing.py` adds one more door: lazily behind an
  explicit `answers(hosted=True)`, which is the consumer *asserting*
  footman semantics — necessarily a process that has footman. A missing
  footman there is a taught refusal at entry, not an ImportError
  mid-block.

## Open questions

- Should `answers()` grow a `strict=True` (unmatched *call* refuses,
  like unmatched probes already do)? Deferred until a consumer asks;
  the recording-like default keeps the common case quiet.
- ~~Should a canned prefix that matched nothing warn at block exit?~~
  Landed same day as `UnservedAnswers` — see the addendum below.
- Callable table values (`argv -> answer`) for stateful fakes —
  deferred, same reason.
- Whether hse-devkit retires `FakeTool` in favour of this is hse's
  call; the surfaces were designed to line up (`calls` assertions,
  canned stdout by prefix, failure injection, `env` capture).

## Addendum (2026-08-21): unserved answers warn

Suggested by Willem after the call-as-handed build, and landed the same
day. The asymmetry with the deferred `strict=True` is what made it
default-on rather than opt-in: an unmatched *call* is often incidental
— the recorder default exists for exactly that — but an unmatched
*table entry* is a test bug in the making. The author canned an answer,
meaning to steer the code under test, and it was never served: the key
was mis-spelt, path-led where matching is name-led, or split into the
wrong tokens, and the test passes vacuously with the block behaving as
if the entry did not exist.

So a clean block exit warns (`UnservedAnswers`, a `Warning` subclass)
naming every prefix that matched nothing. Two deliberate edges:

- **A warning, not an error.** A fixture that shares one table across
  several tests — each exercising a subset — has legitimate unserved
  entries and can filter the category; a per-test table escalates it
  with a `filterwarnings` mark. The category class is public surface
  precisely so both spellings are one line.
- **Only on a clean exit.** A block that raises already failed loudly;
  a warning stacked behind the exception competes with it and loses.

`strict=True` (refusing unmatched *calls*) stays deferred — the two
guards are independent, and this one carries no API besides the
category.
