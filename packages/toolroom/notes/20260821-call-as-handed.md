# Record the call as handed: `Call.args` / `Call.kwargs`

Status: **landed** (2026-08-21, same day) — built as planned, no
deviations. The typing check at the bottom ran and passed: a probe file
(`from toolroom import testing` used as a context manager yielding
`list[Call]`) type-checked clean under basedpyright, so the submodule
resolves ahead of the stub's catch-all `__getattr__` and no
`testing.pyi` declaration was needed. Written from footman's side (the
FakeTool deletability audit, 2026-08-21); Willem handed it to a
toolroom session. Everything below names files as they stood at
`2ffa1a2`.

## The evidence

The testing seam's own note ([20260821-testing-seam.md](20260821-testing-seam.md))
left FakeTool's retirement as "hse's call; the surfaces were designed to
line up." The audit ran, and they line up except in exactly one place.

hse's `_sync_uv` helper wraps `FakeTool` with this docstring:

> FakeTool renders every call keyword — a False flag included — so the
> recorded argv names each option sync passed, and an assertion must
> match by prefix (the bridge itself omits False from the real argv).

Two tests lean on that deliberate over-rendering
(`test_a_plain_sync_may_still_rebuild`,
`test_the_reconcile_may_fetch_a_pinned_interpreter`, both in
`packages/hse-devkit/tests/hse/devkit/test_orchestrate.py`): they assert
`not any(token.startswith("--python") for token in argv)` — meaning "the
flag was never *handed*, not even as False". Both guard a live Windows
incident (uv fetching a fresh CPython over a venv built on another
patch release, leaving it half-removed). Under `answers()` the bridge
renders honestly, a False keyword vanishes from the argv, and both
tests pass vacuously — the one regression the migration cannot absorb.
`Call` records `argv`/`raw`/`command`, all post-render; the thing the
assertion is about no longer exists by the time the seam sees the call.

A second consumer wants the same record: footman's own
`tests/test_tasks.py` stubs six checker handles with a hand-rolled
`Recorder` proxy precisely because its assertions are on the as-handed
call — `((verb, args), kwargs)` — not the rendered line. One feature
retires both fakes.

## The decision

Carry the call as handed through the seam, and record it on `Call` as
two new attributes:

- **`Call.args`** — the positional arguments, verbatim (a `Path` stays
  a `Path`; no `str()` conversion — rendering is what `argv` is for).
- **`Call.kwargs`** — the call keywords as the caller wrote them,
  **False values included** (the bridge omits them from argv; the
  record must not).

The assertion the gap needs becomes direct, and strictly stronger than
the over-render trick it replaces:

```python
assert "python" not in calls[0].kwargs  # never handed at all
assert calls[0].kwargs["frozen"] is False  # handed, as False
```

(The over-render trick also went vacuous the *other* way: a flag handed
as False rendered as `--frozen=False` and could satisfy a positive
prefix match no real call would produce.)

### Mechanics

- `src/toolroom/__init__.py`: both `_host.run()` call sites — the spawn
  lane (`:967`) and the in-process lane (`:1059`) — pass one more
  keyword, `handed=(args, dict(kwargs))`. The dict is copied at the
  call boundary so a later mutation cannot rewrite the record.
- `src/toolroom/_host.py`: `run()` (`:344`) accepts `handed` and
  ignores it. Precedent already in the file: `probe()` carries the
  name-led spelling "so the testing seam can match … Execution ignores
  it." Same shape, same justification.
- `src/toolroom/testing.py`: `fake_run` records it onto the two new
  `Call` slots (keep `__slots__` alphabetical: `args, argv, command,
  env, kwargs, opts, probe, raw`). Probe `Call`s keep them empty —
  `()` and `{}` — a version read has no call keywords by construction.
- Scope note for the docstring: `args`/`kwargs` are the *call*.
  `.flags()` keywords are part of the handle (rendered into the verb
  path before any call exists) and stay visible only in `argv`/`raw`;
  run policy stays in `.opts`.

## Tests (`tests/test_testing.py`)

- A False call keyword shows in `.kwargs` and is absent from `.argv`.
- Positionals recorded verbatim (hand a `Path`, get the `Path` back).
- The hse-shaped conformance case, pinning the deletability claim: a
  call handing neither `python=` nor `no_python_downloads=` yields
  `"python" not in call.kwargs`; the same call handing
  `no_python_downloads=False` yields it present — with the argv
  identical in both cases.
- The in-process lane records `handed` too (extend the hosted
  simulation test — the callable is recorded, never invoked, and the
  call record still carries the kwargs).
- Probe `Call`s answer `()`/`{}`.

## Docs / CHANGELOG / release

- `docs/testing.md`: add the two attributes where `Call` is described,
  with the one-line rule of thumb — assert on `kwargs` for *what the
  code decided to pass*, on `argv` for *what would have run*.
- `docs/api.md` renders `Call` from its docstring — rewrite the
  docstring, the page follows.
- CHANGELOG `[Unreleased]` → `### Added`. Ships as **0.6.1** when
  Willem tags; nothing here bumps the version files (the release train
  does that).

## Non-goals — deliberately not this build

- **Exception-valued answers** (a seam call raising
  `FileNotFoundError`) and **sequenced answers** (repeated matches of
  one argv answering differently, the `side_effect` constituency).
  Both live footman-side by design: footman's `run()` door owns the
  failure lanes (`RunFailed`, `nofail`, the `except OSError` guards),
  and `answers()` stays a minimal static table. The design lives in
  footman's rebased `notes/20260801-recording-injection.md`
  (`recording(answers=)`); hse reaches those through footman, not
  through this seam.

## A check, not a change

`src/toolroom/__init__.pyi` ends in `def __getattr__(name: str) ->
Tool[Result]: ...`, and there is no `testing.pyi`. Verify a
basedpyright consumer resolves `from toolroom import testing` to the
real (inline-typed, `py.typed`) submodule rather than the stub's
catch-all `Tool`. If it mistypes, declare the submodule in the stub;
if it resolves, record that nothing was needed.
