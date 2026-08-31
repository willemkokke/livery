@.claude/guidance/interaction-voice.md
@.claude/guidance/documentation-standards.md

# livery

**Temporary file.** `livery.workshop` will materialise its
replacement; the conventions stated here outlive the file. The
replaced-by table is in `notes/20260830-forge-bootstrap.md`.

The monorepo of the livery ecosystem. Current intent lives in
`notes/`: the forge bootstrap plan, judged against the development
workflows note. Notes describe current state. Decisions carry dates in
each note's decision record. A note is updated in the same change as
the code it describes, or it is wrong.

## The gate

`uv run fm check` runs format, lint, the four type checkers, the
type-completeness verdict, and tests in parallel. Run it before every
commit; CI runs the same command on three OSes. Nothing on the merge
path may wait on anything outside this repository. The gate's verdict
is its exit code, and a `PreToolUse` hook (`fm hooks.pre-bash`)
refuses a footman command piped into head/tail and a push of a branch
that conflicts with `origin/main`.

## Working conventions

- Agent sessions work in worktrees under `.claude/worktrees/`.
- Failure reasons are printed verbatim, never read as booleans.
- Every workflow verb is idempotent: re-running it is the recovery
  procedure.
- A forge quirk without a FakeForge fault mode is a debt: same-day
  fault mode, regression test, and a line in
  `packages/forge/docs/quirks.md`.
- Phases land daily, gate-green, mergeable alone.

## Commits

Conventional prefixes (`feat:`, `fix:`, `docs:`, `chore:`,
`refactor:`, `test:`), imperative subject, body only when the subject
cannot carry it. Author identity: Willem Kokke <mail@willem.net>,
SSH-signed. No attribution trailers. Commit and push only when asked.
Tags are release tags, `packages/<pkg>/v<semver>`, immutable and
pushed alone, with one exception: the annotated `archive/setup` tag
cut at graduation, freezing the setup history that the squash merge
collapses. No other tag class exists.

## Layering

Dependencies point only downward, and `livery.forge` imports only the
standard library at module import time; the one declared optional
extra (PyNaCl, behind `livery-forge[github-secrets]`) loads lazily
inside the one capability path that needs it.
`tests/test_workspace_contracts.py` enforces both and grows into the
real layering lint. The importable
namespace is PEP 420: **never create `livery/__init__.py`**.

## Interfaces and typing

This is the final form; there will be no typing clean-up passes later.

- Public is what a package's `__init__` re-exports in `__all__`. Every
  other module is underscore-named. A test pins both.
- Four type checkers gate, none advisory: basedpyright with warnings
  as errors, mypy strict on `livery.*` (linux, darwin, and win32), ty,
  and pyrefly. `fm typecomplete` requires the public API to be 100%
  type-complete.
- A suppression is narrow, inline with the code, and carries a reason:
  `# type: ignore[code]`. Pyright-only suppressions use
  `# pyright: ignore[...]` so mypy's unused-ignore check stays honest.

## Docstrings

- Google style only: Args, Returns, Raises, Yields, Attributes. ruff
  enforces the convention.
- No RST anywhere. Not in docstrings, not in comments.
- The voice and word choices in `.claude/guidance/` apply to
  docstrings the same as to every other published sentence.
- Refer to other objects by their full public import path,
  `livery.forge.Forge`, so generated API docs can cross-link them
  across packages.

## Layout

- `packages/<name>/`: one package, discovered by its `livery.toml`
- `packages/<name>/docs/`: plain markdown, the seed of the rendered
  per-package site. Write it as that site, never as scratch.
- `tasks.py`: the footman dev loop (`fm check`, `fm forge.dev.up`)
- `livery.toml`: the workspace contract (layers, forge, runners)
