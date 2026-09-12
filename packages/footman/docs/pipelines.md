# Pipelines

Footman holds up its end of a pipeline: stdin binds to typed
parameters, a task can own stdout with its return value, and the exit code
distinguishes "footman did not understand you" from "the work failed". One
signature, both directions:

```python
from typing import Annotated
from livery.footman import Stdout, stdin, task


@task
def summarise(diff: Annotated[str, stdin] = "") -> Stdout[dict]:
    "Reduce a diff to the numbers."
    added = sum(1 for line in diff.splitlines() if line.startswith("+"))
    return {"added": added}
```

```console
$ git diff | fm summarise | jq .added
12
```

## The pipe in: `stdin`

A parameter marked `stdin` binds from whatever the caller piped. The
annotation decides how the bytes are interpreted:

| annotation | reads |
| --- | --- |
| `Annotated[str, stdin]` / `Stdin[str]` | the stream as UTF-8 text |
| `Annotated[bytes, stdin]` | raw bytes |
| `Annotated[str, stdin("prompt")]` | one top-level key of a JSON document |
| `Annotated[list[int], stdin(lines=True)]` | one line per element, each line coerced like a CLI token |
| a record / `dict` / `list` | the whole JSON document, typed |
| any other type | the stream as one value, coerced to it |

A **record** is a dataclass, a `NamedTuple`, a `TypedDict`, or a class with an
annotated `__init__`, and all four bind from a JSON object the same way.

The last row is the ordinary case: `Stdin[int]` fed
`42` is the **`int` 42**, `Stdin[Colour]` fed `red` is `Colour.RED`. The value
goes through the same coercion, choices, bounds and `check(fn)` validators a
command-line token would, and one trailing newline is ignored: `echo 42 |` is
how you pipe a value, and the newline is the shell's punctuation, not part of
it. A `Stdin[str]` gets the stream verbatim, newline included.

Precedence is **CLI > stdin > env > default > prompt**, so an explicit option
always wins, which buys the Unix `-` convention with no sentinel argument.
The stream is read once, fully, at the boundary, and shared by every
parameter in the run that asks; task bodies never touch stdin, so bound
tasks stay fully parallel and need no `interactive=True`. A terminal on
stdin means "not provided", and nothing ever blocks on a read. `ask()`,
confirm gates, and the interactive forms live on
[Asking for input](input.md).

The dataclass form is the one to reach for when a machine sends you JSON:
an agent-hook payload, a webhook body, another tool's `--json` output:

```python
from dataclasses import dataclass, field


@dataclass
class ToolInput:
    file_path: str = ""


@dataclass
class Event:
    tool_input: ToolInput = field(default_factory=ToolInput)
    stop_hook_active: bool = False


@task(hidden=True)
def on_edit(event: Annotated[Event, stdin]) -> None:
    if event.tool_input.file_path.endswith(".py"):
        ...
```

Unknown keys are ignored (a producer may grow fields without breaking
you), missing keys follow the dataclass's own defaults, nested access is
plain attributes, and every refusal names the exact JSON path. `Event` is
**boundary-only**, since it holds another record and no comma-separated token
can say where the inner one ends, so there is no `--event` flag; the pipe is its
source, and

```console
$ fm on-edit < fixture.json
```

replays the real parse — keep a fixture next to the wiring and every hook
is testable by hand. In tests, `Runner.invoke("on-edit", stdin=payload)`
is the same replay in-process.

A **flat** record — every field a scalar — has both spellings, and the command
line wins when you use it:

```console
$ echo '{"name": "api", "port": 80}' | fm deploy      # Config(name='api', port=80)
$ fm deploy --cfg=web,9000                             # Config(name='web', port=9000)
```

`fm --describe` prints the exact JSON each task expects, field by field, so a
caller never has to read the Python — see
[JSON](json.md#the-shape-a-pipe-expects).

## The pipe out: `Stdout[T]`

A task declares that its return value is the document on stdout — in the
signature, so no call site has to remember a flag. `fm summarise` *is* a
filter, the way `sort` and `jq` are filters. The return type decides the
bytes, mirroring `stdin`: `Stdout[str]` verbatim, `Stdout[bytes]` raw,
anything structured JSON, pretty-printed at a terminal, one compact line
into a pipe. Text and JSON go out as UTF-8 whatever the console's encoding,
the same codec the pipe in reads, so both ends of a footman pipeline agree
without either side guessing. Prints and `run()` lines replay on stderr,
where the summary already lives, so redirecting stdout captures exactly the
document. The full rule set lives on [JSON output](json.md).

## Feeding a child: `run(input=…)`

The two sides above are the task at a pipeline's edge. In the middle of
one, a task also *writes* a child's standard input, because some payloads have no
argv spelling at all (`uv pip install -r -` reads its requirements there):

```python
from livery.footman import run, task


@task
def install(requirement: str) -> None:
    run("uv pip install -r -", input=requirement)
```

The string arrives whole and the pipe closes, so a child that reads to EOF
finishes rather than waits; it is encoded the way the capture is decoded
(`encoding=`). Without `input=` the child inherits the stdin the process
had, and footman never opens an instantly-empty pipe on its behalf. An
in-process tool has no standard input to feed, so `input=` on one is a
taught `TypeError`.

## The exit code is the contract

A caller reads the whole boundary from two observables:

| exit | stdout | meaning |
| --- | --- | --- |
| `0` | a document | the task's result (it declared `Stdout[T]`) |
| `0` | empty | ran, nothing to say |
| `64` | empty | **footman refused**; the command line was wrong |
| other | empty | the task failed; the code is the task's own |

Refusals (an unknown task, a flag that will not coerce, a malformed
chain) exit 64 (`EX_USAGE`), never a code a task could mean on purpose,
so a typo in a wired-up command line can never impersonate a real verdict.
The low codes belong to tasks: `fail("reason", code=2)` means exactly what
it says to whoever reads 2. Interrupt is 130, and a stop signal 143. (A
chatty task that does not
declare `Stdout[T]` still prints to stdout; the table's stdout column is
about the tasks you address, and you wrote the address.)

## Not an in-process pipe

`fm transform validate` does **not** feed transform's document into
validate's stdin. A chain's tasks share the *process's* stdin, the same
payload with its own interpretation each, and the document goes to the
process's stdout. Piping task into task is two invocations:

```console
$ fm transform < in.json | fm validate
```

(That is also why two `Stdout[T]` tasks in one chain refuse at plan time:
one process, one stdout, one document.)
