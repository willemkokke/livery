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

`uv run fm check` runs format, lint, typecheck, and tests in parallel.
Run it before every commit; CI runs the same command on three OSes.
Nothing on the merge path may wait on anything outside this
repository.

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

## Layering

Dependencies point only downward, and `livery.forge` imports only the
standard library at runtime. `tests/test_workspace_contracts.py`
enforces both and grows into the real layering lint. The importable
namespace is PEP 420: **never create `livery/__init__.py`**.

## Layout

- `packages/<name>/`: one package, discovered by its `livery.toml`
- `packages/<name>/docs/`: plain markdown, the seed of the rendered
  per-package site. Write it as that site, never as scratch.
- `tasks.py`: the footman dev loop (`fm check`, `fm forge.dev.up`)
- `livery.toml`: the workspace contract (layers, forge, runners)
