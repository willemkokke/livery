# Typed signatures

Footman reads your function signature and turns it into a CLI, the same idea
typer popularised, applied to a task runner. Types are validated *eagerly*, at
parse time, with taught error messages.

## The core mapping

| Signature                       | CLI shape                                            |
| ------------------------------- | ---------------------------------------------------- |
| `fix: bool = False`             | flag `--fix` / `--no-fix`                            |
| `mode: str = "loose"`           | option `--mode=VALUE`                               |
| `mode: Literal["a", "b"]`       | completable, eagerly-validated choices              |
| `count: int = 100`              | typed option, validated at parse time               |
| `paths: list[Path] = ()`        | repeatable or comma-separated (`--paths=a,b`)       |
| `tags: set[str] = frozenset()`  | the same, handed back as a `set`                    |
| `size: Size` (a `NamedTuple`)   | one option filling every field (`--size=800,600`)   |
| `env: dict[str, int]`           | `--env=KEY=VAL` pairs (repeatable or comma-separated)|
| `template: Path`                | required positional (consumed by exact count)       |
| `*cmd: str`                     | variadic trailing passthrough                       |

**The rule behind the table: the default decides.** A parameter with **no
default** is a **required positional**: a bare word on the line fills it
(`fm greet Ada`). A parameter **with a default** is an **option** you pass by
name (`--mode=loose`), or, for a `bool`, a `--flag`/`--no-flag` switch. That is
the whole distinction: give a parameter a default and it moves from the
command line's *positions* to its *flags*. The container types layer arity on
top: `list[T]`/`Many[T]` take one-or-many (a positional one needs at least
one), and `*args` sweeps up the variadic tail. But the default is still what
sorts each parameter into a position or a flag.

!!! note "One reserved parameter name: `help`"

    A parameter named `help` with a default is the one name the signature
    can't turn into a working option. It would map to `--help`, but `--help`
    (and `-h`) is intercepted **anywhere before `--`** and turns the whole line
    into a help request that never executes anything, so the option could never
    be reached. Footman **refuses the file** rather than shadow the parameter
    quietly, and a refusal at load time takes the whole tasks file with it:
    every task in it, `fm --list` and `fm --help` included, exits 64 with the
    reserved-name message until the parameter is renamed (`show_help`,
    `explain`).

    The reservation is exactly as wide as the problem. A `help` with **no**
    default is a required positional, and `*help` a variadic; neither ever
    spells `--help`, so both stay legal. Every other global name is free to
    reuse: the rest (`--json`, `--version`, `-s`, `-j`, …) must come before the
    first task, so `fm deploy --json` binds `--json` to `deploy`, not to
    footman; only `--help`/`-h` win wherever they land on the line.

## Naming an option without a value

Every option may be named on its own:

```sh
fm build --target        # the default, and asked for on purpose
fm build                 # the default, with no opinion expressed
fm build --target=prod   # a value
```

A bare `--target` binds exactly what absence would have bound, since the same
`env()`-then-default ladder runs, so on its own it changes nothing. What it
adds is that somebody named it, which `given()` reports:

<!-- example: fragment -->

```python
from livery.footman import task, given


@task
def build(*, profile: Path = Path("build-profile.json")):
    """Build, and write a trace when asked for one."""
    if given("profile"):
        trace_to(profile)
```

`fm build` writes no trace. `fm build --profile` writes the declared default.
`fm build --profile=other.json` writes there. Three outcomes from one declared
default. A value alone could never separate the first two, because both hand
the body the same path.

**Supplied means the caller**: an option on the line (bare or attached), a
keyword on a body call, a piped `stdin` payload, an answered `ask()` prompt.
**Not supplied means footman worked it out**: an `env()` fallback, which is
ambient and answers for nobody in particular, or the declared default. A body
call says the same thing a command line does (`build(profile=…)` reads as
given, `build()` does not) and a called task never inherits its caller's
answer.

A parameter with no default has no absence to mean, so naming it bare is still
the taught `=`-attachment error.

## A default computed when the task runs

A Python default is evaluated once, when the module is imported: fine for a
constant, wrong for anything that depends on the machine, the environment or
the clock. `default(fn)` calls `fn()` at bind time instead:

<!-- example: fragment -->

```python
@task
def install(*, shell: Annotated[str, default(detect_shell)] = ""): ...
```

It sits one rung above the declared default (**CLI value > `env()` >
`default(fn)` > the declared default**) and, like `env()`, needs a declared
default to sit on, because a plain Python call of the task with no run around
it has to keep working. `--help` prints what it computes, since the manifest is
built on the execution path: what help shows is what this run would use.

Declare one positional argument and it receives the **sibling parameters**
resolved so far, the same courtesy `check(fn)` gets, because a default is
often a function of the inputs beside it:

<!-- example: fragment -->

```python
@task
def cast(
    *keys: str,
    shell: str = "zsh",
    title: Annotated[str, default(lambda p: f"{p['shell']} · completion")] = "",
): ...
```

Read-only, and only what is to its *left* in the signature. The view holds
*effective* values, what each parameter will actually be from whichever rung
of the ladder supplied it, and only a left parameter has one yet. That is also
why a cycle cannot be written down: the signature fixes a total order and
binding walks it, so nothing can depend on something unresolved.

Reaching rightwards is a taught error rather than a silent surprise, through
`p["later"]` and `p.get("later")` alike:

```text
'title' may only read parameters declared before it, and 'cmd' comes after
— so it has no value yet. Move 'cmd' above 'title' in the signature.
```

`--help` shows no default for a sibling-reading one, since there is no
invocation to read, but it does say there is one, as `default computed`. A computed default
that *can* be resolved is marked too, because a bare number reads as an
arbitrary constant when it is really this machine's:

```text
  -j, --jobs=N       max parallel tasks; default: 13 (computed)
      --color=WHEN   when to colour: always|never|auto; default: auto (computed)
```

## What if I don't like annotating types?

Then don't. Every rule above still holds, because the one that sorts a
parameter into a position or a flag reads the *default*, not the
annotation. This is a working CLI:

```python
from livery.footman import task


@task
def ship(target, port=8000, ratio=1.5, name="web", verbose=False):
    "Ship it."
    print(target, port + 1, ratio, name, verbose)
```

`fm ship prod --port=9000 --verbose` gives you a required positional, three
options, and a `--verbose`/`--no-verbose` flag pair, completed, listed, and
documented like any other task. The four basic types read from their
defaults: `port` arrives as an `int`, `ratio` as a `float`, `name` as a
`str`, and a `bool` default becomes a flag. Passing `--port=abc` is refused
before anything runs, with the same taught message an annotated `int` gets.

The limit is what a bare default can't say. These stay strings:

```python
@task
def stamp(
    out=None, paths=(), tags=["docs"]
): ...  # out, paths and tags all arrive as `str`
```

A `None` default names no type, and a container's default says nothing about
what belongs *in* it, so footman infers nothing and hands the value over as
typed on the command line. That is deliberate: the rule is to infer exactly
where Python's own inference is definite, which is also exactly where a type
checker reading your file agrees. Anything less certain would be a guess, and
a wrong guess is worse than a string.

So annotate when you want what a default cannot express:

| you want | annotate |
| --- | --- |
| a fixed set of choices, completed and validated | `Literal["dev", "prod"]` |
| a bound | `Annotated[int, between(1, 32)]` |
| a path that must exist | `Exists` / `IsFile` |
| a list, and what goes in it | `list[Path]`, `Many[int]` |
| several fields filled from one option | a `NamedTuple` |
| a value from the environment | `Annotated[str, env("DEPLOY_ENV")]` |
| a prompt when it is missing | `Annotated[str, ask(prompt="Target?")]` |

Everything else on this page is that list expanded. None of it is required to
get a good CLI out of a plain function.

## Unions and one-or-many values

A parameter can accept a union of types; footman validates the value against the
union and coerces it by specificity: the most specific member that accepts the
value wins (`int` → `float` → `Path` → `str`, with `str` as the universal
fallback):

```python
from livery.footman import task


@task
def scale(factor: int | float): ...
```

`Many[T]` is exactly `list[T]`: a parameter that accepts one or more values and
is **always a list**, even for a single value (it reads more intentfully than a
bare `list[T]` at a positional). Required when positional, so at least one value
must be given:

```python
from livery.footman import Many


@task
def build(targets: Many[str]):
    ...  # fm build web     -> ["web"]
    # fm build web api -> ["web", "api"]
```

`set[T]`, `frozenset[T]` and `tuple[T, ...]` accept values exactly the same
way. They differ only in the container your function receives, which is the
point of naming one:

```python
@task
def label(tags: set[str] = frozenset()): ...  # fm label --tags=a,b,a -> {"a", "b"}
```

A bare `list`, `set`, `frozenset` or `tuple` means a collection of `str`.

## Comma-splitting and `nosplit`

Values accumulate from commas and from repetition into **one stream**, so
`--tag=a,b,c` and `--tag=a --tag=b --tag=c` are the same three values. Only
`,` is a separator (no alternatives), and it is shell-portable, including
PowerShell:

```python
@task
def release(tags: list[str] = []): ...  # fm release --tags=a,b,c  -> ["a", "b", "c"]
```

When a value may itself contain a comma, mark the parameter `nosplit`: then only
the repeated flag adds items, and a comma stays literal.

```python
from livery.footman import NoSplit


@task
def notify(lines: NoSplit[list[str]] = []): ...


# fm notify --lines="Smith, John" --lines="Doe, Jane"  -> two names, commas kept
```

`NoSplit[list[str]]` is shorthand for `Annotated[list[str], nosplit]`: the
same marker, less to type. Every bare marker has a subscript form like this;
[Terse aliases](#terse-aliases-and-forwarding) below has the full set and the
rule for which markers can have one.

One stream is the whole rule. A collection takes all of it; a shape with a
declared arity takes it in groups of that size, which is the next section.

## Dictionaries

`dict[K, V]` maps `KEY=VALUE` pairs, and it composes with the rest of the type
system: `dict[str, int | str]`, and even `dict[str, list[...]]`:

```python
@task
def env(vars: dict[str, int | str]): ...  # fm env --vars=port=8080 --vars=name=web
```

## Fixed-arity values

A shape that declares how many fields it has takes that many values from one
option. Prefer a `NamedTuple`: it names its fields, and the names do real
work:

```python
from typing import NamedTuple
from livery.footman import task


class Size(NamedTuple):
    width: int
    height: int


@task
def render(size: Size = Size(1920, 1080)): ...  # fm render --size=800,600
```

`--size=800,600` fills `width` and `height`. So does `--size=800 --size=600`,
because there is only ever one stream of values and the declared arity groups
it. That is also what makes a *container* of shapes work:

```python
class Spot(NamedTuple):
    x: float
    y: float


@task
def route(points: list[Spot] = ()): ...


# fm route --points=1,2 --points=3,4   -> [Spot(1.0, 2.0), Spot(3.0, 4.0)]
# fm route --points=1,2,3,4            -> the same two points
```

Nothing is guessed. `--points=1,2,3` cannot be a whole number of points, so it
is refused rather than rounded:

```console
$ fm route --points=1,2,3
fm: route: --points takes values in groups of 2 (x,y) — got 3, which leaves 1 over
```

**A plain `tuple[int, int]` behaves identically**: same grouping, same
chunking, same refusals. Only the messages are poorer, because a plain tuple
has no field names to report with:

```console
$ fm render --size=800,tall
fm: render: --size: height expects an integer (got 'tall')   # a NamedTuple
fm: render: --size: value 2 expects an integer (got 'tall')  # tuple[int, int]
```

That is the whole argument for preferring the named form.

A dataclass, or any class with an annotated `__init__`, works the same way,
with the constructor's parameters as the fields:

```python
from dataclasses import dataclass


@dataclass
class Window:
    title: str
    width: int = 800


@task
def open_(window: Window = Window("footman")): ...  # fm open --window=Docs,1024
```

### Arity ranges

When a shape has optional fields, the count settles it: `Window` above takes
one value or two, and `--window=Docs` leaves `width` at its default. `--help`
says so: `1 to 2 values`.

Inside a *container* that flexibility goes away: every field must be given.
One group can let the count settle it, but two cannot:
`--windows=Docs,1024,Notes` could be one window and a second one, or two
windows of one field each, and guessing is what this design refuses to do. So
a container groups by the full arity and says so:

```console
$ fm many --windows=Docs,1024,Notes
fm: many: --windows takes values in groups of 2 (title,width) — got 3, which leaves 1 over
```

Give every field a value, or bind the list from [stdin](pipelines.md), where
JSON's own brackets say where each one ends.

### What is refused, and where to go instead

A shape only takes a command-line spelling when **one token can fill each of
its fields**. Two things put a shape out of reach, both with somewhere to go:

- **A field that is itself a shape or a collection.** `Line(start: Point, end:
  Point)` has no comma spelling, since nothing in `--line=1,2,3,4` says which pair
  is the start. Pipe it as JSON instead: a nested document binds in full.
- **An untyped or `*args` constructor.** Footman groups a shape it can *type*;
  a constructor that describes none of its parameters is not one, and keeps
  the single-token `T(value)` form below.

Both are pipe-able today, and `fm --describe` prints the exact JSON a task
expects; see [JSON](json.md#the-shape-a-pipe-expects).

## The same annotation, whichever channel

A parameter's type means the same thing wherever the value comes from. The
command line and a JSON document on stdin are two spellings of one contract:
`Size` is a `Size` whether it arrived as `--size=800,600` or as
`{"width": 800, "height": 600}`, and a `list[Spot]` is a list of `Spot`
either way.

Where they differ is only in what a spelling can *express*. A command line has
commas; JSON has brackets, so a shape holding another shape has no
command-line form and is pipe-only, and a shape whose fields are all scalars
has both. Nothing is silently downgraded in either direction: a shape footman
cannot honour on a channel says so.

[Pipelines](pipelines.md) covers the boundary itself.

## Custom types

Any type footman can construct from a **single token** works, and it is called
with that token. `datetime` uses `fromisoformat`; everything else is
constructed as `T(value)`:

```python
from uuid import UUID
from decimal import Decimal
from datetime import datetime


@task
def record(id: UUID, amount: Decimal, when: datetime): ...
```

This is the one-field case of the rule above: a type with a single constructor
parameter takes the whole token, and a type with two or more annotated ones is
filled from a group instead.

## Validation markers

Eager, taught validation is the whole pitch, so constraints ride in
`Annotated`, the same idiom as `suggest` and `nosplit`:

```python
from pathlib import Path
from typing import Annotated
from livery.footman import task, between, check, doc, env, isfile


def semver(value: str) -> None: ...  # your validator: raise ValueError to refuse


@task
def deploy(
    config: Annotated[Path, isfile],  # must exist, be a file
    jobs: Annotated[int, between(1, 32)] = 4,  # inclusive bounds
    target: Annotated[
        str, env("DEPLOY_ENV")
    ] = "staging",  # CLI > $DEPLOY_ENV > default
    version: Annotated[str, check(semver)] = "0.0.0",  # your own validator
    force: Annotated[bool, doc("skip the health check")] = False,  # help text
): ...
```

```console
$ fm deploy missing.toml
fm: deploy: <config> must be an existing file (got 'missing.toml')
$ fm deploy app.toml --jobs=99
fm: deploy: --jobs must be between 1 and 32 (got '99')
$ DEPLOY_ENV=prod fm deploy app.toml      # target == "prod"
```

- **Paths.** `exists`, `isfile`, `isdir` require the value to name something
  real on disk; validated at parse time like a bad choice would be.
- **Bounds.** `between(lo, hi)` is inclusive; either end may be `None`. A
  bare `range(0, 8)` also works for ints, with Python's half-open semantics
  (`0` through `7`; the end is excluded, exactly as in a `for` loop).
- **Env fallbacks.** `env("VAR")` fills an *absent* option from the
  environment; the value flows through the same coercion, bounds, and checks
  a command-line token would (just at binding time, since the parser never sees
  the environment). Only valid on a parameter with a default, because a
  fallback needs somewhere to fall.
- **Custom validators.** `check(fn)` runs after coercion, per element for
  collections; raise `ValueError` with a message written for the user.
- **Help text.** `doc("…")` puts one line of your own words on a
  parameter. It leads the option's line in `fm --help <task>`, becomes the
  option's description in shells that render one (zsh, fish, nushell,
  PowerShell tooltips), and rides along in the `fm --json --list` catalog.
  The task's own help stays the docstring's first line; `doc` is for the
  parameters.

## Terse aliases, and forwarding

A **bare** marker, one that takes no arguments, has a `Name[T]` shorthand, the
way `Many[T]` reads better than `list[T]`:

- `NoSplit[list[str]]` ≡ `Annotated[list[str], nosplit]`.
- `Exists`, `IsFile`, `IsDir` ≡ `Annotated[Path, exists/isfile/isdir]`: bare,
  no subscript, since the type is always `Path`: `def rm(target: Exists)`.
- `Forward[T]` ≡ `Annotated[T, forward]`.
- `Hidden[T]` ≡ `Annotated[T, hidden]`.

Arg-taking markers (`suggest`, `between`, `env`, `check`, `doc`, `ask`) keep the
full `Annotated[...]` form, because their value can't ride in a type subscript.

The `forward` marker is an orchestration tool that threads a value onward to
the tasks this one dispatches, and is covered in
[Chaining & parallelism](orchestration.md#forward-a-value-to-what-a-task-dispatches).
Markers compose by listing them: `Annotated[bool, ask(prompt="Fix?"), forward]` both
prompts for the value and forwards it: one prompt at the top, the answer
flowing down.

## Keeping a parameter out of the listings

`hidden` takes a parameter out of what a human reads, and out of nothing
else:

```python
from typing import Annotated
from livery.footman import task
from livery.footman.params import hidden


@task
def publish(target: str, legacy: Annotated[str, hidden] = ""):
    """Ship it."""
```

`fm --help publish` shows `<target>` and stops there: no `--legacy`, and the
synthesized example does not use it. Everything else about it is unchanged:
it still binds, it still completes when you type `--l<TAB>`, and
`fm --describe` carries it marked `"hidden": true` rather than dropping it.
`fm --all --help publish` lists it.

That is the same rule `hidden=True` follows on a task, one level down. Hiding
and completing are different questions. A long machine-facing flag is the one
you most want spelled for you, and a machine reading the contract is exactly
who is meant to still find it.

For a deprecated flag you keep working but stop advertising, a debug switch, or
a flag a wrapper script passes.

## Or just write a docstring

Footman reads the parameter docs you already write, in Google, NumPy, and
Sphinx styles, auto-detected per docstring. Everything a `doc("…")` marker
feeds (help lines, completion descriptions, the catalog) fills from the
docstring instead, the body between the summary and the section renders in
`fm --help <task>` as the task's long help, and an explicit `doc("…")`
always wins over a docstring entry for the same parameter:

=== "Google"

    <!-- example: revision -->
    ```python
    @task
    def deploy(target: str, fix: bool = False):
        """Ship a build.

        Checks out, builds, and uploads — see the release runbook.

        Args:
            target: where to deploy
            fix: apply fixes first
        """
    ```

=== "NumPy"

    <!-- example: revision -->
    ```python
    @task
    def deploy(target: str, fix: bool = False):
        """Ship a build.

        Parameters
        ----------
        target : str
            where to deploy
        fix : bool
            apply fixes first
        """
    ```

=== "Sphinx"

    <!-- example: revision -->
    ```python
    @task
    def deploy(target: str, fix: bool = False):
        """Ship a build.

        :param target: where to deploy
        :param fix: apply fixes first
        """
    ```

A docstring entry that names no real parameter earns a `UserWarning`, the
same loudness a broken annotation gets. The parser itself is public and
standalone (`footman.docstrings.parse`) if you want structured docstrings
for your own tooling.

One asymmetry to know about: path and bounds violations on the command
line are caught *eagerly* (before anything runs); the same violations in an
env-supplied value are caught at binding time, because that's when the
environment is read.

## Dynamic completion

`suggest` attaches a completer: a function that returns live values (git
branches, deploy targets, the shares below). Footman runs it **fresh** each time
you complete that value, in a short-lived subprocess: a value you <kbd>Tab</kbd>
to answer a build-critical question must be current, so nothing keeps a copy to
serve you instead. The recompute is bounded and isolated, so a slow or failing
completer degrades to no candidates, never the old values, never a hung
keystroke. This holds for *every* completer, whether or not the task owns the
terminal (`interactive=True`); a real run validates the value you pass against
the same live call. Pair a completer with [`ask()`](input.md) and the same
values become a numbered menu when the flag is missing, asked up front with
the run's other questions.

A completer runs only where its values are wanted: the parameter whose value
is being completed or validated, and the options `--help` is printing. A line
that never mentions the parameter never calls it, so a completer that shells
out costs nothing on an unrelated task. `--help` shows the values it found and
marks them `(dynamic)`, the way a computed default is marked `(computed)`:
what you are reading is this moment's answer, not a fixed set.

```python
from typing import Annotated
from livery.footman import task, suggest


def shares() -> list[str]:
    return ["main", "scratch", "archive"]


@task
def mount(share: Annotated[str, suggest(shares)]): ...
```

Keep a completer's imports **inside its body**, the way footman keeps optional
dependencies out of a task's import path. Loading your tasks file stays cheap;
the completer's cost (a subprocess, a network round-trip) is paid only when it
runs, not every time the file is imported:

```python
def branches() -> list[str]:
    import subprocess  # here, not at module top

    out = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        capture_output=True,
        text=True,
    )
    return out.stdout.split()
```

The first example, recorded in PowerShell: the demo project's tasks.py is
extracted from this page at build time, so the code above and the session below
cannot disagree. <kbd>Tab</kbd> offers what `shares()` returned; <kbd>Tab</kbd>
again walks the menu.

![Animated: fm mount TAB offers main, scratch, archive from the suggest completer; TAB again moves the selection](_generated/shots/pwsh-suggest-cast.svg)
