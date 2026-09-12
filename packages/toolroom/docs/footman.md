---
icon: lucide/concierge-bell
---

# With footman

toolroom and [footman](https://willemkokke.github.io/footman/) are two
zero-dependency packages that make each other better without either
requiring the other. footman never imports, ships, or names toolroom;
toolroom names footman only inside its seam, and only touches it when
footman is already in the process.

## Detection, not configuration

When a `toolroom` call happens where footman is *orchestrating* — a
task body, a `parallel()` worker, a `recording()` block — the bridge
routes the call through footman's `run()`. Nothing opts in and nothing
can forget to: the same `git.push()` that spawns a plain subprocess in
a script earns a receipt, captures through the task's lane, obeys
`--dry-run`, and appears in `recording()` when a footman run is
around it.

```python
from livery.footman import task
from livery.toolroom.tools import ruff, pytest


@task
def check() -> None:
    ruff.check("src")  # a real step: receipt, capture, dry-run, recording
    pytest.opts(in_process=False)()
```

Detection keys on a live footman context, never on mere presence: a
process that only *imported* footman — a pytest run auto-loading
footman's plugin, an app embedding both packages — leaves bare calls
on the standalone lane, deterministically, so which exception a
failure raises never depends on what some other module imported.
Harness infrastructure that must answer truthfully even inside
`recording()` — an availability probe, say — rides
`.opts(recorded=False)`: a value read executes where a story step
would be faked.

## What the host unlocks

- **Receipts and reporting** — every call is a step: a line in the
  run's story, a row in `--json`, output replayed on failure.
- **`recording()`** — tests capture the commands a block would run
  without executing them. When a test needs a tool to *say* something
  or to fail, that is [`livery.toolroom.tools.testing.answers()`](testing.md) —
  toolroom's own double, host or no host.
- **`--dry-run`** — calls are faked, not executed.
- **The in-process lane** — tools that prefer running inside the
  process (pytest via `pytest.main`, mkdocs on macOS where `DYLD_*`
  survives only in-process) do so under footman's stdout and argv
  routers, fully parallel. Standalone this lane does not exist: a
  preference quietly spawns instead, a demand
  (`.opts(in_process=True)`) is a taught refusal.
- **Failure semantics** — a failing call fails the task through
  footman's lane instead of raising `ToolError`.

## The vocabulary twins

toolroom owns its `Argv` and `Result`; footman owns its own pair for
its receipts. Both `Argv`s are ordinary `list[str]` — they compare
equal token-for-token and either flows anywhere the other does. Hosted
calls answer with footman's `Result`, whose surface is a superset of
toolroom's; code written against toolroom's (`ok`, `code`, `stdout`,
`stderr`, `to_argv()`) runs unchanged under both. The seam between the
packages carries only stdlib shapes — `list[str]` in,
int-with-attributes out.

A `str` subclass rides that seam as itself: the bridge never flattens a
value to plain `str` on the execution path, so a marker carried by the
type — footman's `Secret` is one — survives into the argv footman
receives, and footman's own display surfaces can redact it. toolroom's
side of the contract is duck-typed on `reveal()` and nothing else; see
[Secrets show as `***`](usage.md#secrets-show-as) for the caller-facing
half.

## Zero dependencies, both ways

footman's headline — zero runtime dependencies — keeps no asterisk:
it does not depend on toolroom. toolroom's runtime is stdlib-only too;
footman appears in its *development* dependencies, where it runs the
gate and hosts the conformance suite.
