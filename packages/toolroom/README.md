# toolroom

Typed surfaces for command-line tools, generated from the tools
themselves.

```python
from toolroom import git, cmake, ruff

git.switch("-c", "release/v1.4")
cmake.build("build", parallel=8)
ruff.check("src", fix=True, select=["E", "F"])
```

A tool room is where precision tools are made, kept with their
measurements, repaired when they drift, and issued for use. That is the
whole design, one verb at a time: a stub generator reads each tool's own
`--help` and writes a typed surface for it; the surfaces are kept with
the version they were read from; a weekly refresh re-takes them when the
tool moves; and the bridge issues any tool as a Python handle —
including tools no stub has ever described.
`toolroom.terraform("plan")` runs whether or not toolroom has heard of
terraform.

> **Beta.** toolroom is pre-1.0: minor versions may include breaking
> changes — always called out in the
> [changelog](https://github.com/willemkokke/toolroom/blob/main/CHANGELOG.md),
> never in a patch release. Pin the minor (`toolroom~=0.6.0`) if you
> build on it.

## No transcription

toolroom does **not** transcribe a tool's flags into Python parameters.
Transcription drifts: the wrapper pins the flag-set its author copied,
the tool moves on, and one day a call emits a flag the installed binary
rejects. Instead, keyword arguments translate *mechanically*, so the
installed tool's own CLI stays the single source of truth, at whatever
version it is:

- `fix=True` → `--fix` (`False`/`None` → omitted entirely)
- `strict=off` → `--no-strict` (`off` is the `toolroom.off` sentinel)
- `output_format="github"` → `--output-format github`
- `select=["E", "F"]` → `--select E --select F`
- `x=1` (single letter) → `-x 1`
- a trailing underscore escapes Python keywords: `import_="x"` → `--import x`

Attribute access chains subcommands
(`toolroom.docker.compose.up(detach=True)`), and positional strings pass
through verbatim. The typed stubs *suggest* — your editor knows the
flags and what each one does — but they never forbid: the hints decide
whether your editor can help, never whether a call works.
[Using the tools](https://willemkokke.github.io/toolroom/usage/) has the
full grammar.

## Standalone, or hosted

With nothing else installed, a call spawns the tool with plain
`subprocess` and answers in a `Result` — the exit code as an `int`
subclass, carrying the captured streams. A failure raises `ToolError`
unless the call opted into `nofail`.

When [footman](https://willemkokke.github.io/footman/) is present in the
process, the same call routes through footman's `run()` instead and
inherits everything a task run means: capture, replay-on-failure,
dry-run, `recording()`, `--json` receipts, parallel lanes. Neither
package imports or depends on the other — they were designed together
and are better together, but each is useful with the other absent.
[With footman](https://willemkokke.github.io/toolroom/footman/) tells
that story.

## Built, not run

`.argv` turns a handle's call into a command line instead of an
execution — an ordinary `list[str]`, with `.posix()` and `.windows()`
for the moment it crosses into a shell:

```python
cmd = git.push.argv(force=True)  # ["git", "push", "--force"]
cmd.posix()  # "git push --force", quoted for a POSIX shell
```

## Testing

Code that calls tools is tested with `toolroom.testing.answers()`: the
handles stay real — chaining, flag translation, redaction are all
exercised — and only execution is replaced, answered from a table of
canned output and exit codes:

```python
from toolroom.testing import answers

with answers({("git", "push"): 1}) as calls:
    release()  # exercises the failure path

assert calls[0].argv == ["git", "push"]
```

[Testing](https://willemkokke.github.io/toolroom/testing/) has the whole
story.

## Install

```sh
uv add toolroom        # or: pip install toolroom
```

toolroom has **zero runtime dependencies** and needs Python 3.11+. The
docs live at
[willemkokke.github.io/toolroom](https://willemkokke.github.io/toolroom/).
MIT licensed.
