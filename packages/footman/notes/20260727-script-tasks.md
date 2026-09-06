# Script tasks: a tasks file that carries its own dependencies

**Landed** 2026-07-27. The docs (`getting-started`, `cookbook`,
`configuration`, `custom-cli`) and the CHANGELOG say what it is; this note
says how it got there and what it nearly was instead.

## The idea

Willem's, in one line: `-f=script_tasks.py` should notice the file is a
PEP 723 script and re-exec itself inside that environment — "then if you
have fm installed globally, you can just run them", and it should apply to
a plain `tasks.py` too, "so you can use it in any folder, not just inside
a project".

Two things fell out immediately. It completes the plugin funnel — a script
file can name plugin packages in its block, so plugins work with zero
project scaffolding. And the machinery was mostly built: `_uv_handoff`
already owned a re-exec with a recursion belt, opt-outs, and the POSIX /
Windows split.

## What made the completion story easy

The first sketch treated completion as the hard part: the refresh child
and the dynamic-completer child both import user code, so both need the
script environment. Willem's observation dissolved it — every surface that
needs the environment is *already* a spawned argv→stdout filter, so the
handoff composes as a command prefix rather than a design. The TAB hot
path itself never needed anything: it reads baked JSON.

The rule the children follow is **never touch the network**: `child_python`
syncs `--offline`, so an environment already built (or one whose wheels are
all in uv's cache) is entered, and anything else means "not yet" — the
child then rebuilds in place, exactly as it always did.

That fallback turned out to matter more than expected. A tasks file's
*module-level* imports are usually just the runner — third-party imports
live inside task bodies — so the in-place rebuild very often produces a
correct manifest anyway. Completion is only empty when the file needs its
own dependencies to import at all *and* uv's cache is cold. The first
real run fixes both.

## Native uv, not reconstruction

The draft rebuilt the environment itself: `uv run --with dep --with dep2
--python <spec> …`, injecting `--with footman==<version>` when the block
didn't name it. A design review killed it, and the spike confirmed the
alternative:

- `uv sync --script <file>` materialises the environment, honouring
  `dependencies`, `requires-python` **and `[tool.uv]` tables** natively;
- `uv python find --script <file>` names its interpreter;
- exec `<python> -m footman <argv>`.

Reconstruction's holes, all avoided by not doing it: an injected
`footman==<version>` pin is unresolvable for a dev checkout or an unreleased
version; and `[tool.uv.sources]` (a dep pinned to a git ref or a private
index) would have been silently dropped, re-resolving the *name* against
PyPI — misresolution, not an error. Verified end to end: a script whose
block points footman at a local path ran against that checkout.

Cost of the native path: two extra subprocesses (warm: ~45 ms, silent) and
a uv floor. Spiked on uv 0.11.1; `--offline` behaves as needed, and
`uv python find` on an unmaterialised environment answers with a *base*
interpreter at exit 0 — which is why every caller syncs first and treats
find as a lookup, never a probe.

## Willem's rulings during the build

1. **A pinned project ignores the block entirely** — "not even a warning;
   it can be noted during -v but it is not an issue". This reversed the
   drafted precedence, where an explicit `-f=<script>` beat the lock rule.
   His reason: portable files should be storable in repositories "without
   friction". So the lock rule is decided first and wins outright; the
   script rule only applies where no project has spoken.
2. Consequently the refusals softened to hints. The only hard refusal left
   is a block that declares dependencies but not the runner — an
   environment that provably cannot import what the file imports.

## Rejected / deferred

- **`--with` reconstruction** (above) — kept only as the fallback if the
  uv floor ever proves unreachable.
- **A `Brand.dist` guess for branded CLIs.** footman cannot know which
  distribution ships someone else's runner, and a wrong guess execs a user
  into an environment without it. `dist` is opt-in; unset, the rule stays
  out of the way.
- **Multi-file cascades.** A script environment can only be *the*
  environment; two files have no single answer, so the rule declines. Not
  a refusal — the file runs as it always did.

## Gotchas worth keeping

- The stock `fm` console script goes through `App()`, not `DEFAULT_BRAND`
  — so `main()` passes `dist="footman"` explicitly. Defaulting it on
  `Brand` alone silently disabled the whole feature, and every unit test
  still passed (they construct `Brand` directly).
- `uv python find` returning the *current* interpreter is the loop guard
  that matters most; the env belt (`FOOTMAN_UV_REEXEC`) is the second.
- `VIRTUAL_ENV` from a surrounding shell makes uv warn on every script
  command. It is dropped before the sync and before the exec: a script
  environment is deliberately not the active one.
- `resolve_task_files` hardcoded `Path.cwd()`; the handoff must resolve
  against the `-C` probe *before* the chdir. It gained a `cwd=` parameter
  rather than growing a second, drifting walk.
