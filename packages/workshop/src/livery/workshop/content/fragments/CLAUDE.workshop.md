<!-- Shipped as livery.workshop layer content, delivered to the workspace
     by the sync verb. Edit this copy in the layer and release it;
     an edited delivered copy is a local override, kept and named.
-->
# The workshop's rules

The base layer's fragment: only the rules the workshop itself
enforces. A repository's own facts (identity, intent, layout beyond
the workspace shape) live in its `CLAUDE.project.md`, which always
loads last and wins.

## The gate

`uv run fm check` runs format, lint, the four type checkers, the
type-completeness verdict, and tests in parallel. Run it before every
commit; CI runs the same command. Nothing on the merge path may wait
on anything outside the repository. The gate's verdict is its exit
code, and a `PreToolUse` hook (`fm hooks.pre-bash`) refuses a footman
command piped into head/tail and a push of a branch that conflicts
with `origin/main`.

## Working conventions

- Exceptional cases and fallbacks are tested before happy paths: a
  broken happy path announces itself in daily use, a broken fallback
  hides until the day it is the only path left. A fallback without a
  test forcing it is untested code.

- Development goes through issues: file one (`fm issue.create` or
  `fm issue.start "title"`), work it in its worktree, and let the
  merge close it. Branches follow `<kind>/<number>-<slug>` so the
  close wires itself.
- Issue worktrees (`fm issue.start`) live under the runner's data
  directory (`worktrees/<repo>/`), outside every repository. Ad-hoc
  agent sessions may still use `.claude/worktrees/`.
- Person-wide configuration and tokens live in `.repo.shared.env`
  in the runner's config directory, so every checkout and worktree
  starts warm; `fm env.set KEY --scope=shared` writes it.
- Failure reasons are printed verbatim, never read as booleans.
- Never pipe the output of a command whose verdict you depend on: a
  pipe replaces its exit code with the filter's and truncates the
  failing lines. Redirect to a file and slice the file instead.
- Every workflow verb is idempotent: re-running it is the recovery
  procedure.
- Phases land daily, gate-green, mergeable alone.
- A phase acceptance that ships deferred keeps an open line in the
  plan note until the evidence lands. "Shipped" with a silent
  deferral hides the gap exactly where the next reader trusts the
  record.
- A contract clause that code enforces gets a pinning test before
  its implementation is replaced: name the properties the old code
  enforced, pin them, then replace it. A property living only in the
  contract's prose does not survive a refactor.
- A warning or anomaly found live during other work gets an issue
  the same day, with a body an outsider can read. A patch where it
  was noticed leaves every other arm carrying the same fault.
- The release train owns versions, tags, and receipts. No hand
  stamps, no hand tags; an out-of-band claim (name-squatting on an
  index included) is recorded as debt in the plan the day it is
  made, and the gate refuses a release tag that is not an annotated
  train receipt.
- Notes in `notes/` describe current state; decisions carry dates in
  each note's decision record. A note is updated in the same change
  as the code it describes, or it is wrong.

## Commits and tags

The commit convention, machine-read by the release train: subjects
are `type(scope): subject` with types feat, fix, docs, chore,
refactor, test; a `!` before the colon or a `BREAKING CHANGE:`
footer marks a break. Versions follow footman's practice: after 1.0
a break bumps major, a feature minor, everything else patch; before
1.0 a feature bumps minor (breaks ride along) and everything else is
a patch. `fm release.prepare <path>` derives the bump and the entry
through git-cliff, per the package's rendered `cliff.toml`, from the
commits since the last release tag.


Conventional prefixes (`feat:`, `fix:`, `docs:`, `chore:`,
`refactor:`, `test:`), imperative subject, body only when the subject
cannot carry it. No attribution trailers. Commit and push only when
asked. Tags are release tags, `<path>/v<semver>`, immutable and
pushed alone.

## Layering

Dependencies point only downward; the workspace's layering lint
(`livery.workshop.verify_workspace`) enforces the contract graph. The
importable namespace is PEP 420: **never create a namespace-level
`__init__.py`**.

## Interfaces and typing

This is the final form; there are no typing clean-up passes later.

- Public is what a package's `__init__` re-exports in `__all__`. Every
  other module is underscore-named. A test pins both.
- Four type checkers gate, none advisory: basedpyright with warnings
  as errors, mypy strict on the namespace (linux, darwin, and win32),
  ty, and pyrefly. `fm typecomplete` requires every public API to be
  100% type-complete.
- A suppression is narrow, inline with the code, and carries a reason:
  `# type: ignore[code]`. Pyright-only suppressions use
  `# pyright: ignore[...]` so mypy's unused-ignore check stays honest.

## Docstrings

- Google style only: Args, Returns, Raises, Yields, Attributes. ruff
  enforces the convention.
- No RST anywhere. Not in docstrings, not in comments.
- The voice and word rules of the imported guidance fragments apply to
  docstrings the same as to every other published sentence.
- Refer to other objects by their full public import path, wrapped
  as a reference (`[livery.forge.Forge][]`), so generated API docs
  cross-link them across packages; a bare dotted path stays plain
  text.

## Workspace shape

- `packages/<name>/`: one package, discovered by its `workshop.toml`.
- `packages/<name>/docs/`: plain markdown, the seed of the rendered
  per-package site. Write it as that site, never as scratch.
- `tasks.py`: `plugin("livery.workshop")`, the whole dev loop.
- `workshop.toml`: the workspace contract (layers, forge, runners).
