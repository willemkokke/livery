# The starting environment joins the memo key

**Landed** in the build after v0.46.0 (one commit, `_futures` + one line in
`_executor`), alongside — but independent of — the notes-levels design
(`20260831-misuse-notes-levels.md`).

## The question that opened it

Reviewing environment lineage for the notes design, the maintainer asked:
"isn't a hash of the env part of the memoization key?" It was not — the
cell key was `(work_key(task), frozen arguments, given)` — and the
follow-up was "shouldn't it?".

## Why yes

The run's once-cell exists so that the same *work* runs once. A body can
observe exactly two inputs: its arguments and its environment. Arguments
were in the key; the environment was not, so a caller that wrote
`ctx.env` (or `os.environ`, routed there) before a body call could be
answered by an execution that never saw the write — a wrong cache hit,
silent, decided by claim order. The codebase had already made this
argument to itself twice:

- `given` joined the key because two requests that freeze to identical
  values can still be different requests, and keyed on values alone "the
  second request would be answered by the first — no error".
- `_UNKEYABLE` prefers honest re-execution over a wrong hit.

Over-splitting costs a duplicate run and is visible in the report;
over-sharing is silently wrong. Same bias, third application.

## Why it stays a cache hit in the common case

Every DAG-scheduled task starts from the pinned run snapshot
(`base_env()`), and a body-call child copies its caller's env — which,
untouched, is byte-equal to that snapshot. Equal content, equal digest,
one cell. Digests diverge only when someone actually wrote the
environment, which is precisely when sharing was wrong. Two callers that
made the *same* writes still share: sharing is keyed by content, not by
lineage or claim order.

## The mechanics

- `_env_digest(env)`: sorted items (insertion order must not split
  cells), keys through `_norm` (case-blind exactly where Windows is),
  sha1 over `k=v\0` pairs, `usedforsecurity=False`. A digest rather than
  the frozen pairs only to keep cell keys small.
- Computed at the two key sites, each of which knows the env the callee
  would start from *before it exists*:
  - `_futures.call`: the already-born child's env when lifecycle hooks
    armed an early birth (post `pre_bind`, matching the declared path),
    else the caller's current env — which is exactly what `child()`
    would copy at birth.
  - `_executor._execute_bound` (`work_of`): the callee's `ctx.env`.
- The old comment "the context can never become part of the work's
  identity" was amended rather than deleted: the context *object* stays
  out; its env, the one contextual input a body observes, joins
  deliberately.

## Rejected

- **Keying on what the task actually reads**: unknowable before running.
- **Storing the frozen pairs instead of a digest**: works, but bloats
  every cell key with the whole environment for no gain — equality is
  all a key needs.
- **Treating this as part of the notes/levels build**: orthogonal; it
  ships on its own so either can be reverted or reasoned about alone.

## Consequence recorded for the future

This de-risks the parked musing about scheduled tasks inheriting a
parent's env (the maintainer raised and shelved it the same day): with
env in the key, that change could no longer cause wrong sharing — only
visible, honest duplicate runs. The musing stays shelved.
