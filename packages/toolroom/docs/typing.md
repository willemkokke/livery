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

The wheel ships the vocabulary alone: `tools/__init__.pyi` declares
`Tool`, `Argv`, `Result`, the aliases below, and a `__getattr__` that
answers every tool name with a `Tool[Result]`. The per-tool classes are
rendered by `livery-toolroom-store` from the tool records, or from the
published index, into a workspace's `typings/` directory,
pyright's default stub path and a search path the workspace's rendered
configuration hands mypy, ty and pyrefly: `fm tools.restub` writes one
stub per tool the workspace locks, at the locked version, as
`livery.toolroom.stubs` modules, and `livery.toolroom.handles` beside
them declaring each handle, which the wheel's index imports. Both sit
under the namespace package beside the tools package, never inside its
directory. A tool the workspace does not deploy gets no stub, and
its handle is a bare `Tool`, which forbids nothing and completes
nothing. `fm sync` and the lock verbs write them too, and the
workspace's entry script writes them before its type checkers run.

The docs playground renders the same stubs from the index the site
serves beside it, into the package it installs, since an editor's
completer reads a stub only from inside the package.

Each stub's header records the tool version and the platforms it was
read on. The records, their readings and the index that renders them
are `livery-toolroom-bench`, the bench package beside this one; a
workspace that keeps the records current names it as a layer, and a
consumer of the handles never installs it.

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
`ValuedFlag`), and type-checking a toolroom consumer requires nothing
but toolroom — the rendered stubs never import footman.
