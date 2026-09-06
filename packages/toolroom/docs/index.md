---
icon: lucide/warehouse
---

# toolroom

Typed surfaces for command-line tools, generated from the tools
themselves.

<!-- example: fragment -->
```python
from livery.toolroom import cmake, git

git.switch("-c", "release/v1.4")
cmake.build("build", parallel=8)
```

A tool room is where precision tools are made, kept with their
measurements, repaired when they drift, and issued for use. That is
the whole design, one verb at a time: a stub generator reads each
tool's own `--help` and writes a typed surface for it; the surfaces
are kept with the version they were read from; a weekly refresh
re-takes them when the tool moves; and the bridge issues any tool as
a Python handle — including tools no stub has ever described.

## Where this came from

Most task runners exist, in the end, to call other command-line
applications. Whatever else a `check` task does, it eventually shells out
to ruff, to pytest, to git. So when [footman](https://willemkokke.github.io/footman/)
was built, a lot of the effort went into making that one act ergonomic:
calling a tool with real keyword arguments, having the flags translate
mechanically instead of being transcribed by hand, and having a wrong
flag be a type error in your editor rather than a subprocess exit code
you read later.

That work turned out not to need a task runner at all. It is useful in a
script, in a notebook, in a test — anywhere you would otherwise write a
list of strings and hope. So it moved out here, unchanged in what it
does: **toolroom is exactly what footman's tools were**, with the same
translation, the same stubs, the same in-process fast path.

The split is a clean one, and deliberately so: neither package depends
on the other, neither imports the other's names, and each is useful with
the other absent. But they were designed together and they are better
together — the recommendation is to use both, and everything below stays
true either way.

## No transcription

toolroom does **not** transcribe a tool's flags into Python
parameters. Transcription drifts: the wrapper pins the flag-set its
author copied, the tool moves on, and one day a call emits a flag the
installed binary rejects. Instead, keyword arguments translate
*mechanically* — `fix=True` becomes `--fix`, `select=["E", "F"]`
repeats the flag, attribute access chains subcommands — so the
installed tool's own CLI stays the single source of truth, at
whatever version it is. The typed stubs *suggest*; they never forbid.
[Using the tools](usage.md) has the full grammar.

## Standalone, or hosted

With nothing else installed, a call spawns the tool with plain
`subprocess` and answers in a `Result` — the exit code as an `int`
subclass, carrying the captured streams. A failure raises `ToolError`
unless the call opted into `nofail`.

When [footman](https://willemkokke.github.io/footman/) is present in
the process, the same call routes through footman's `run()` instead
and inherits everything a task run means: capture, replay-on-failure,
dry-run, `recording()`, `--json` receipts, parallel lanes. Neither
package imports or depends on the other — detection is the seam, and
it speaks stdlib. [With footman](footman.md) tells that story.

## Install

```sh
pip install toolroom
```

toolroom has **zero runtime dependencies** and needs Python 3.11+.
Pre-1.0: the API may move without a deprecation cycle; the
[changelog](changelog.md) carries every step.
