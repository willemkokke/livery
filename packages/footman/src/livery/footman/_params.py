"""Public parameter markers, used inside `Annotated` annotations.

Dynamic completion (`suggest`), one-or-more (`Many`), comma-split opt-out
(`nosplit`), path requirements (`exists`/`isfile`/`isdir`), numeric bounds
(`between`, or a bare `range`), environment fallbacks (`env`), custom
validators (`check`), and per-parameter help (`doc`). Each carries no runtime
weight beyond a small marker object; `footman._coerce.peel` reads them all in
one place.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Final, TypeVar

_T = TypeVar("_T")


class Secret(str):
    """A string that redacts everywhere it is *shown*.

    Answers to `ask(secret=True)` and `prompt(secret=True)` arrive as
    `Secret`, and a parameter annotated `Secret` coerces into one whatever
    its value came from — a flag, an `env()` fallback, a default. It is a
    real `str` for the body (comparisons, formatting — the caller's
    business), but its repr — what tracebacks, logs, and debuggers print —
    is `Secret('***')`, and structured surfaces serialise it as `***`: the
    `--json` envelope, a `Stdout[…]` document, baked manifest defaults.

    Redaction covers the places footman *shows* a value, not the bytes a
    task deliberately writes. `print(token)` and `f"TOKEN={token}"` emit the
    real thing, because every string operation on a `Secret` yields a plain
    `str` — which is what makes a task that must print a secret (an
    `export …` line for `eval`) work without a switch to disarm.

    `reveal()` is that unwrap said out loud, for where it isn't implicit:

    ```python
    @task
    def creds(token: Secret) -> Stdout[dict]:
        return {"token": token.reveal()}   # meant to leave; not redacted
    ```

    Because it is a method, every intentional exposure is greppable — an
    audit list a run-wide "don't redact" flag could never give you.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "Secret('***')"

    def reveal(self) -> str:
        """The real value as a plain `str`, deliberately un-redacted.

        Reach for it where a `Secret` would otherwise survive into a
        structured surface that redacts — a dict, a dataclass field, a
        `Stdout[…]` document — and you mean the value to be there.
        """
        return str.__str__(self)


class suggest:
    """Attach a dynamic completer to a parameter, via `Annotated`:

    ```python
    def build(project: Annotated[str, suggest(list_projects)]): ...
    ```

    `list_projects() -> list[str]` returns the candidate values. footman runs
    it on the execution path — refreshing a cache the completion hot path serves
    — and, when *strict* (the default), validates the supplied value against a
    fresh call. The wrapper is required: a bare callable in `Annotated` is
    refused, so a marker that happens to be callable can never be mistaken
    for a completer.
    """

    __slots__ = ("fn", "strict")

    fn: Callable[[], Any]
    strict: bool

    def __init__(self, fn: Callable[[], Any], *, strict: bool = True) -> None:
        self.fn = fn
        self.strict = strict


def _takes_an_argument(fn: Callable[..., Any]) -> bool:
    """Whether *fn* accepts a positional argument — the sibling view."""
    import inspect

    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return False  # a builtin with no signature: treat as no-argument
    return any(
        p.kind
        in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
        for p in params
    )


class default:
    """Compute a parameter's default when the task runs, via `Annotated`:

    ```python
    def install(shell: Annotated[str, default(detect_shell)] = ""): ...
    ```

    A Python default is evaluated once, at import — fine for a constant, wrong
    for anything that depends on the machine, the environment or the clock.
    `default(fn)` calls `fn()` at bind time instead, so `--help` and the run
    agree and both are current.

    It sits in the ladder just above the Python default — **CLI value > env >
    `default(fn)` > the declared default** — and, like `env()`, it needs a
    declared default to sit on: a plain Python call of the task, outside any
    run, has to keep working.

    Declare one positional argument and it receives the **sibling parameters**
    resolved so far — the same courtesy `check(fn)` gets, and for the same
    reason: a default is often a function of the inputs beside it.

    ```python
    def cast(*, shell: str = "zsh",
             title: Annotated[str, default(lambda p: f"{p['shell']} · x")] = ""): ...
    ```

    Read-only, and only what is to its *left* in the signature. The view holds
    *effective* values — what each parameter will actually be — and only a left
    one has that yet, which is also why a cycle cannot be written down: the
    signature fixes a total order and binding walks it. Reaching rightwards is
    a taught error, through `p["x"]` and `p.get("x")` alike. `--help` cannot
    show such a default — there is no invocation to read — so it shows none
    rather than one it would have to invent.

    The value is used as it comes back, not coerced: `fn()` returns a real
    object, and coercion exists because the command line only has strings. It
    still runs the annotation's validators, so a `default(fn)` that would be
    refused as a typed value is refused here too rather than smuggled in.
    """

    __slots__ = ("fn", "reads_siblings")

    fn: Callable[..., Any]
    reads_siblings: bool
    """Whether *fn* takes the sibling view. Decided once, here, because both the
    binder and the manifest need the answer and the manifest cannot import the
    binder — asking the marker beats asking the same question twice in two
    modules. Inspected rather than probed by call, so a real arity error raised
    inside *fn* is never mistaken for the no-argument form."""

    def __init__(self, fn: Callable[..., Any]) -> None:
        self.fn = fn
        self.reads_siblings = _takes_an_argument(fn)


# `Many[T]` is exactly `list[T]`: a parameter that is *always* a list — one or
# more values, variadic when positional. It reads more intentfully than a bare
# `list[T]` at a call site, but carries no runtime marker of its own.
Many = list


class NoSplitMarker:
    """Marker for `nosplit`."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "nosplit"


nosplit: Final[NoSplitMarker] = NoSplitMarker()
"""Opt a list/dict parameter OUT of comma-splitting, via `Annotated`:

```python
def build(names: Annotated[list[str], nosplit] = ()): ...
```

By default a collection parameter splits a single token on commas
(`--tag a,b,c` -> `["a", "b", "c"]`) *in addition to* the repeatable form
(`--tag a --tag b`). Mark it `nosplit` when a value may itself contain a comma:
then only the repeated flag adds items and `--name "a,b"` stays the literal
`"a,b"`. `NoSplit[list[str]]` is the terser spelling of the same thing."""


NoSplit = Annotated[_T, nosplit]
"""Shorthand for `Annotated[T, nosplit]`: `NoSplit[list[str]]` opts a collection
out of comma-splitting (see `nosplit`)."""


class HiddenMarker:
    """Marker for `hidden`."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "hidden"


hidden: Final[HiddenMarker] = HiddenMarker()
"""Keep a parameter out of the listings a human reads, via `Annotated`:

```python
def deploy(target: str, legacy: Annotated[str, hidden] = ""): ...
```

It means on a parameter what `hidden=True` means on a task. `--help` does not
list it and the synthesised example does not use it, but it still binds, still
completes, and `--all` reveals it. The manifest marks it rather than dropping
it, so an agent reading `--describe` still learns it exists — hiding and
completing are different questions, and a long machine-facing flag is the one
you most want spelled for you.

For a deprecated flag kept working but unadvertised, a debug switch, or a flag
a wrapper script passes. `Hidden[str]` is the terser spelling."""


Hidden = Annotated[_T, hidden]
"""Shorthand for `Annotated[T, hidden]`: `Hidden[str]` keeps a parameter out of
the listings while it still binds and completes (see `hidden`)."""


class ForwardMarker:
    """Marker for `forward`."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "forward"


forward: Final[ForwardMarker] = ForwardMarker()
"""Forward this parameter to the tasks this task dispatches, via `Annotated`:

```python
@task(pre=[build, lint])
def check(fix: Annotated[bool, forward] = False): ...
```

A `forward`-marked parameter's value is passed to every task this one
dispatches — its `pre`/`post` prerequisites, and a runnable group's fan-out —
that declares a parameter of the same name; tasks that don't declare it run on
their own defaults. The forwarded value overrides the callee's default, chains
through callees that re-declare the marker, and may satisfy a defaultless
parameter — the same courtesy `ask()` and `stdin` extend, so a receiver never
needs a fake default just to be reachable. Presence travels beside the value,
so `given()` reads the same sentence in the dispatcher and in what it
dispatched."""


class ArgMarker:
    """Marker for `Arg`."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "optional-arg"


_arg: Final[ArgMarker] = ArgMarker()

Arg = Annotated[_T, _arg]
"""An *optional trailing positional*, via `Annotated`-alias:

```python
@task
def files(pattern: Arg[str] = "*"): ...
```

`fm files src` fills the positional; a bare `fm files` runs on the default.
The grammar is deterministic and greedy: when a bare word follows, it *is*
the value — never re-interpreted as the next task, no name-peeking — and
capped at one token. To run the task argument-less ahead of another, say so
with the explicit boundary: `fm files + build`. An `Arg` needs a default
(absence must mean something), takes at most one token (use a list
parameter for many), and must trail every required positional.

`Arg` is the one exception to the rule that sorts every other parameter:
*the default decides* — no default makes a required positional, a default
makes an option you pass by name. `Arg` says "keep me a positional even
though I have a default", which is why it needs the greedy-but-capped
grammar above. The rules it is bending are laid out in the
[typing guide](https://footman.willem.net/typing/)."""


Forward = Annotated[_T, forward]
"""Shorthand for `Annotated[T, forward]`, like `Many[T]` is for a list:

```python
@task(pre=[build, lint])
def check(fix: Forward[bool] = False): ...
```

`Forward[bool]` expands to `Annotated[bool, forward]` — the same marker, less
noise on a signature full of forwarded parameters. See `forward`."""


class PathRequirement:
    """Marker for `exists` / `isfile` / `isdir`."""

    __slots__ = ("_name", "kind")

    kind: str
    _name: str

    def __init__(self, kind: str, name: str) -> None:
        self.kind = kind
        self._name = name

    def __repr__(self) -> str:
        return self._name


exists: Final[PathRequirement] = PathRequirement("exists", "exists")
"""Require a `Path` parameter to name something that exists on disk:

```python
def rm(target: Annotated[Path, exists]): ...
```

Validated eagerly (at parse time) with a taught error. See also `isfile`
and `isdir`."""

isfile: Final[PathRequirement] = PathRequirement("file", "isfile")
"""Require a `Path` parameter to name an existing *file* (see `exists`)."""

isdir: Final[PathRequirement] = PathRequirement("dir", "isdir")
"""Require a `Path` parameter to name an existing *directory* (see `exists`)."""


class matching:
    """Narrow a `Path` parameter's <kbd>Tab</kbd> completion to a glob:

    ```python
    def load(env_file: Annotated[Path, matching(".env*")] = Path(".env")): ...
    ```

    footman cannot complete paths itself — it answers from a cached manifest
    and never touches the filesystem — so a path value is handed to the
    shell's own file completion. This is what it hands *along*: the pattern
    the shell filters by, so `--env-file` offers `.env` and `.env.local`
    rather than every file in the directory.

    Directories are always offered whatever the pattern says, or a match one
    level down would be unreachable. The glob matches the file's *name*
    (`*.json`, not `**/*.json`) — the vocabulary all five shells share.

    **Completion only.** It narrows what <kbd>Tab</kbd> shows; it does not
    reject a value typed anyway — that is `check()`'s job, or
    `exists`/`isfile`'s. A completion filter that quietly became validation
    would refuse the perfectly good path someone pasted.
    """

    __slots__ = ("pattern",)

    pattern: str

    def __init__(self, pattern: str) -> None:
        if not pattern:
            raise ValueError("matching() needs a glob, e.g. matching('*.json')")
        self.pattern = pattern

    def __repr__(self) -> str:
        return f"matching({self.pattern!r})"


Exists = Annotated[Path, exists]
"""Shorthand for `Annotated[Path, exists]` — `target: Exists` requires the path
to exist. Type-fixed to `Path`; use `Annotated` directly for a `list[Path]`."""

IsFile = Annotated[Path, isfile]
"""Shorthand for `Annotated[Path, isfile]`: require an existing *file*."""

IsDir = Annotated[Path, isdir]
"""Shorthand for `Annotated[Path, isdir]`: require an existing *directory*."""


class between:
    """Inclusive numeric bounds for an `int`/`float` parameter:

    ```python
    def test(jobs: Annotated[int, between(1, 32)] = 4): ...
    ```

    Validated eagerly with a taught error (`--jobs must be between 1 and 32`).
    Either bound may be `None` for open-ended ranges. A bare `range` in
    `Annotated` also works for ints, with Python's half-open semantics
    (`range(0, 8)` accepts 0 through 7).
    """

    __slots__ = ("hi", "lo")

    lo: float | None
    hi: float | None

    def __init__(self, lo: float | None, hi: float | None) -> None:
        self.lo = lo
        self.hi = hi


class env:
    """Fall back to an environment variable when the option isn't given:

    ```python
    def deploy(target: Annotated[str, env("DEPLOY_ENV")] = "staging"): ...
    ```

    Precedence is CLI > `$DEPLOY_ENV` > default. The env value flows through
    the same coercion and validation as a command-line token. Only valid on a
    parameter with a default — an env fallback *makes* it optional, so it
    needs somewhere to fall. A body call that omits the parameter reads the
    same ladder; a value passed explicitly wins over env, however the task
    was asked for.
    """

    __slots__ = ("var",)

    var: str

    def __init__(self, var: str) -> None:
        self.var = var


class stdin:
    """Bind this parameter from the process's standard input, via `Annotated`:

    ```python
    def review(diff: Annotated[str, stdin] = ""): ...
    # git diff | fm review     AND     fm review < changes.patch
    ```

    The annotation decides how the bytes are interpreted: `str` reads the
    stream as UTF-8 text (universal newlines), `bytes` reads it raw. Two
    call forms refine that:

    - `stdin("prompt")` binds one top-level field of a JSON document on
      stdin — the value flows through the same coercion and validation as
      a CLI token.
    - `stdin(lines=True)` binds a list parameter from lines of text — each
      line is coerced like one CLI token, so `list[int]` and `list[Path]`
      work as they would as repeated flags.

    Precedence is CLI > stdin > env > default > prompt, so an explicit
    option always wins and one signature serves both `fm process file.txt`
    and `cat file.txt | fm process`. The stream is read once, fully, at the
    boundary — never inside a task body, so the parallel-stdin guard is
    never in play — and the same payload serves every parameter that asks.
    A terminal on stdin means "not provided": the parameter falls back to
    its default, and a required parameter refuses with a taught message
    rather than blocking on a read.
    """

    __slots__ = ("field", "lines")

    field: str | None
    lines: bool

    def __init__(self, field: str | None = None, *, lines: bool = False) -> None:
        if field is not None and lines:
            raise ValueError(
                "stdin(field) and stdin(lines=True) are exclusive — a field "
                "binds one JSON value, lines bind a list of tokens"
            )
        self.field = field
        self.lines = lines


Stdin = Annotated[_T, stdin]
"""Shorthand for `Annotated[T, stdin]`, in the `Arg`/`Forward` mould:

```python
def review(diff: Stdin[str] = ""): ...
```

The bare-marker form only — the call forms (`stdin("field")`,
`stdin(lines=True)`) are spelled inside `Annotated`, like `env("VAR")`."""


class StdoutMarker:
    """Marker for `stdout`."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "stdout"


stdout: Final[StdoutMarker] = StdoutMarker()
"""Declare that a task's return value is the document on stdout, via the
*return* annotation:

```python
@task
def status() -> Stdout[dict]: ...
# fm status | jq .branch
```

The task is then a Unix filter by declaration — no flag at any call site.
The return type decides the bytes, mirroring `stdin`: `Stdout[str]` emits
the string verbatim (plus a trailing newline), `Stdout[bytes]` writes raw
bytes, anything structured is JSON — pretty-printed on a terminal, one
compact line into a pipe, dataclasses and `Secret` redaction handled by
the same encoder `--json` uses — so a `Secret` inside a structured document
serialises as `***`, and a task that *means* to emit one says
`token.reveal()`. A `Stdout[str]` built by formatting is unaffected: string
operations on a `Secret` yield a plain `str`, which is how the
print-a-credential filter (`eval "$(fm env-export)"`) works without a
switch to disarm redaction everywhere else.

The rules that keep it honest: an explicit `--json` wins (the document
rides inside `results[].returned`); only the addressed task emits (a
declaring task reached as a `pre=`/`post=` dependency or a fan-out member
is suppressed, not refused); two declaring tasks in one chain is a
plan-time refusal; `None` means empty stdout, exit 0; a failed task emits
nothing; everything that is not the document — prints, `run()` output —
replays on stderr. A bare `-> int` stays the exit-code channel; declaring
`Stdout[int]` makes the int the document instead. A body call is
unaffected: `status()` from another task just returns the value."""


Stdout = Annotated[_T, stdout]
"""Shorthand for `Annotated[T, stdout]` on a *return* annotation:

```python
def wordcount(text: Stdin[str] = "") -> Stdout[int]: ...
```

`Stdout[dict | None]` is the house spelling for a filter that sometimes
has nothing to say (marker outermost, like `NoSplit[list[X] | None]`);
`Stdout[dict] | None` means the same thing. See `stdout`."""


class check:
    """A custom validator, run after coercion; raise `ValueError` to reject:

    ```python
    def tag(version: Annotated[str, check(semver)]): ...
    ```

    The callable receives the coerced value (each element, for collections).
    Its `ValueError` message is shown to the user, so write it for them.

    Declare a second parameter to also receive the **siblings** — the parameters
    to this one's left at their *effective* values (a provided value, else the
    parameter's own default), coerced and read-only (empty for the first
    parameter) — so a check can validate against another input:

    ```python
    def newer(v, params):
        current = current_version(params["name"])   # the package named earlier
        if Version(v) <= current:
            raise ValueError(f"must be newer than {current}")

    def release(name: str, version: Annotated[str, check(newer)]): ...
    ```
    """

    __slots__ = ("fn",)

    fn: Callable[..., Any]

    def __init__(self, fn: Callable[..., Any]) -> None:
        self.fn = fn


class doc:
    """Help text for one parameter, via `Annotated`:

    ```python
    def lint(fix: Annotated[bool, doc("apply fixes in place")] = False): ...
    ```

    One line, written for the person at the prompt. It shows in
    `fm --help <task>`, as the option's description in shells that render
    one (zsh, fish, nushell, PowerShell tooltips), and in the
    `fm --json --list` catalog.
    """

    __slots__ = ("text",)

    text: str

    def __init__(self, text: str) -> None:
        self.text = text


class ask:
    """Prompt for a parameter's value when it isn't supplied, via `Annotated`:

    ```python
    def release(version: Annotated[str, ask()]): ...
    ```

    A parameter marked `ask()` is prompted for when the command line does not
    give it and no `env` fills it — the answer runs through the same coercion
    and validation as a CLI token, re-asking on a bad value. Precedence is
    **CLI > env > prompt (offering the default) > the default**.

    A declared default becomes the *offer*: the prompt shows it, Enter accepts
    it, and where nobody can be asked — off a terminal, under `--no-input`, in
    `--json` — it is simply used. So `ask()` is safe on any parameter: a person
    gets asked, an unattended run gets the default. Without a default there is
    no other answer, so those cases error naming the flag rather than hanging.

    Naming the option bare (`--version`) skips the question: the caller has
    already said "the declared one", and asking again would be footman not
    listening.

    `secret=True` hides the input (getpass) and never shows the default, though
    Enter still accepts it; `prompt="…"` overrides the question text.
    """

    __slots__ = ("prompt", "secret")

    prompt: str | None
    secret: bool

    def __init__(self, *, secret: bool = False, prompt: str | None = None) -> None:
        self.secret = secret
        self.prompt = prompt
