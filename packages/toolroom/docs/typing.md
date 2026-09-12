---
icon: lucide/pencil-ruler
---

# Typing & the stubs

toolroom is typed the way the bridge works: the runtime accepts
anything the installed tool accepts, and the stubs make the common
calls autocomplete without ever forbidding the uncommon ones.

## Two rules keep the stubs honest

- **Every verb ends in `**flags: Any`.** A stub can *suggest* flags —
  read from the tool itself — but never forbid one. When a tool grows
  a flag, the bridge already speaks it; the stub merely hasn't heard
  of it yet.
- **Unknown verbs fall through.** Attribute access resolves through
  `Tool.__getattr__`, so nothing the runtime accepts is a type error.

Stub drift therefore degrades a hint, never a run.

## Where the stubs come from

One generated file per curated tool, read from the installed binaries
on Linux, Windows, and macOS — each header records the tool version it
was read from. The generator, the option-event history it maintains,
and the refresh workflow currently live in
[footman's repository](https://github.com/willemkokke/footman) and are
moving here with the machinery.

## The vocabulary of a signature

Every generated flag is spelled in three public aliases, importable for
wrappers that pass flags through:

- **`Flag`** — a boolean flag: `True` emits `--flag`, `off` emits the
  tool's own negation, `False`/`None` omit it entirely.
- **`Value`** — an option that takes a value. Scalars are `str()`-ed —
  a `pathlib.Path` or an `int` passes straight through — and a sequence
  repeats the flag once per item.
- **`ValuedFlag`** — an option usable bare (`gpg_sign=True`) or with a
  value (`gpg_sign="KEY"`).

Positionals are `str | PathLike[str]` for the same reason:
`ruff.check(Path("src"))` is exactly the call the bridge makes.

<!-- example: fragment -->
```python
from livery.toolroom.tools import Flag, Value, ruff


def lint(fix: Flag = None, select: Value = None):
    ruff.check("src", fix=fix, select=select)
```

An option with a closed set of values gets a named alias in its tool's
stub — `OutputFormat`, `TargetVersion` — so a hover shows one name
rather than the whole `Literal` union spelled twice, and the IDE still
offers the members. Each flag's help text sits on one line in the stub,
so hovers and reference pages reflow it to their own width.

## The building surface

Every generated class is generic over what a call returns. A running
handle answers in `Result`; `.argv` re-parameterises the same class
over `Argv`, so a *built* call keeps the same flag checking as a run:

<!-- example: fragment -->
```python
from livery.toolroom.tools import git

sha_cmd = git.rev_parse.argv("HEAD")  # Argv, same completions, same checks
```

## Checked everywhere

The package ships `py.typed`, the hand stub declares the whole public
surface (`Tool`, `Argv`, `Result`, `ToolError`, `off`, `Flag`, `Value`,
`ValuedFlag`, the curated handles), and type-checking a toolroom
consumer requires nothing but toolroom — the stubs never import footman.
