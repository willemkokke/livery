# AI agents

An agent can drive a project with three commands: `fm --json --list` to
learn the surface, `fm --json --dry-run <chain>` to validate a line, and
`fm --json <chain>` to run it: refusals teach, stdout stays one JSON
document, and results carry the task's own data. This page packages that:
a paste-ready instructions snippet, and agent hooks that keep an agent's
work formatted, linted, and gated.

An agent-readable index of this whole site lives at
[`llms.txt`](https://willemkokke.github.io/footman/llms.txt) (and the full
text at
[`llms-full.txt`](https://willemkokke.github.io/footman/llms-full.txt)).

## The snippet

Put this in `CLAUDE.md` for Claude Code; the identical text works as
`AGENTS.md` for Codex, Cursor, Copilot, Zed, and most other agents (Gemini
CLI reads it as `GEMINI.md`). Two blanks to fill for your project: the
runner prefix and the gate task.

```markdown
## Tasks (footman)

Tasks are typed Python functions in `tasks.py`, run with `uv run fm`.
The gate is `uv run fm check`. Run it before calling any change done;
it must exit 0.

- Discover: `fm --list` (tasks + descriptions), or `fm --json --list`
  for the full tree with parameter types, choices, and defaults.
- Contracts: `fm --describe` gives the whole input+output API as one JSON
  document, return shapes rendered as JSON Schema; `fm --describe=<task>`
  for one task. Nothing runs.
- Inspect: `fm --help <task>` gives typed usage, options, and an example.
  `--help` anywhere on the line never executes anything.
- Validate a command line: `fm --json --dry-run <chain>`. A typo refuses with exit 64; a valid chain rehearses (bodies run, footman's recorded work is faked) and answers in the items envelope.
- Run for machines: `fm --json <chain>`. stdout is exactly one JSON
  envelope: {"schema": 1, "total_ms", "items": [...]}, one flat list
  where a task row carries {task, ok, code, duration_ms, output, error,
  returned} and each recorded command is its own row keyed "command".
  A task's return value lands in `returned`, its declared shape beside
  it as `returned_schema`; refusals put a taught message in a top-level
  `error`.
- Jump to a task's source: `fm --where=<task>` prints file:line.

Grammar: globals (`--json`, `-k`, …) go **before** the first task; a
task's options come right after that task; several tasks on one line
form a chain, and independent tasks run in parallel (output never
interleaves). Everything after `--` passes through to the task's
`*args`.

Exit codes: 0 all ok · 1 a task raised · N a task exited N · 64 footman
refused the line (the stderr message states the fix) · 130 interrupted ·
143 a signal stopped the run.

To add or change tasks, edit `tasks.py`: the signature is the CLI.
Never edit the completion cache under `~/.cache/footman/`; it's derived.
```

## Hooks: Claude Code

A hook is a pipeline whose producer and consumer happen to be an agent
harness: JSON arrives on stdin, the verdict leaves as an exit code plus
stderr. That is exactly the boundary [Pipelines](pipelines.md) describes,
so a hook is a small footman task: the payload binds to a dataclass, the
loop guard is a field read, and no `jq` is involved. The mechanics in one
sentence: a hook's **stderr plus exit code 2** is fed back to Claude as
something to fix; anything else is display-only; and footman's own refusal
(a typo'd flag, an unknown task) exits 64, which the harness shows to the
*human*, so a wiring problem never impersonates a verdict about the project.

The tasks, in `tasks.py`, where `hidden=True` keeps machine-called adapters out
of `--list` and `--tree`, while <kbd>Tab</kbd> still spells them for you
when you run one by hand:

```python
from dataclasses import dataclass, field
from typing import Annotated
from footman import RunFailed, fail, group, stdin, task

hooks = group("hooks", hidden=True, help="Agent lifecycle hooks")


@task
def format(): ...  # stand-ins for your own gate tasks


@task
def lint(): ...


@task
def check(): ...


@dataclass
class ToolInput:
    file_path: str = ""


@dataclass
class HookEvent:
    tool_input: ToolInput = field(default_factory=ToolInput)
    stop_hook_active: bool = False


@hooks.task
def post_edit(event: Annotated[HookEvent, stdin]) -> None:
    """Format and lint a Python file the agent just edited."""
    if not event.tool_input.file_path.endswith(".py"):
        return
    try:
        format()  # your own format/lint tasks, body-called
        lint()
    except RunFailed:
        fail("format/lint failed; fix it before continuing", code=2)


@hooks.task
def stop(event: Annotated[HookEvent, stdin]) -> None:
    """Refuse to let a session end on a red gate."""
    if event.stop_hook_active:
        return  # this stop already is the retry; never ping-pong
    try:
        check()
    except RunFailed:
        fail("the gate is red; fix it before stopping", code=2)
```

The wiring, in `.claude/settings.json`, where the redirection is what puts the
receipts on the channel Claude reads:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          { "type": "command", "command": "uv run fm hooks.post-edit 1>&2" }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          { "type": "command", "command": "uv run fm hooks.stop 1>&2" }
        ]
      }
    ]
  }
}
```

Unknown payload fields are ignored by construction, so the hook survives
the harness growing its schema. To debug one, keep a fixture and replay it:
`uv run fm hooks.stop < fixture.json` exercises the real parse, and
`Runner.invoke("hooks.stop", stdin=payload)` is the same replay in a test.
(This repository wires its own hooks exactly this way.)

## Hooks: Cursor

`.cursor/hooks.json` (project hooks run from the project root):

```json
{
  "version": 1,
  "hooks": {
    "afterFileEdit": [{ "command": ".cursor/hooks/fm-format.sh" }],
    "stop":          [{ "command": ".cursor/hooks/fm-gate.sh" }]
  }
}
```

`fm-format.sh` is just `uv run fm format lint`. Cursor's `afterFileEdit`
is observational, so this keeps the tree formatted but can't push lint
output back into the loop. The feedback channel is the `stop` hook, which
may return a `followup_message` that auto-submits as the next prompt
(Cursor caps the loop at 5 by default), and this is where `--json` earns
its keep:

```sh
#!/bin/sh
# .cursor/hooks/fm-gate.sh: block "done" on a red gate, with receipts.
out=$(uv run fm --json check) && exit 0
printf '%s' "$out" | jq '{followup_message:
  ("fm check failed; fix these, then finish:\n" +
   ([.items[] | select(.task) | select(.ok | not)
     | "\(.task): exit \(.code)\n\(.output)"] | join("\n")))}'
```

!!! warning "Cursor's hooks are beta"

    The event names and the `followup_message` shape above match
    [Cursor's hooks reference](https://cursor.com/docs/hooks) at the time
    of writing, but the feature is marked beta and may move. If a hook
    stops firing after a Cursor update, check that page first; the
    footman side (`fm --json check` and its envelope) is the stable half.

## Everyone else

The snippet is the portable layer, since `AGENTS.md` reaches most agents. For
agents with no hook system (Copilot's coding agent runs in Actions, for
instance), the enforcement layer is the one you already have:
`uv run fm check` in [CI](ci.md) plus branch protection, which catches
every agent and every human identically.
