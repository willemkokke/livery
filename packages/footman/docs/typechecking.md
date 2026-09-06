# Type-checking your tasks

Footman is written for people who type their code. The package ships
`py.typed`, every public symbol has a fully known type — pyright's
`--verifytypes` scores the surface 100%, and footman's own gate fails if
that ever slips — and the whole tree is kept clean under four checkers:
basedpyright, mypy (strict), ty, and pyrefly. Whichever checker your
project runs, a `tasks.py` full of footman gets real answers, not `Any`.

## What the types promise

**`@task` keeps your signature.** A decorated task is not erased to a bare
callable: calling it from another task body checks your parameters and
return type, and your editor completes them.

```python
from footman import task


@task
def build(target: str, release: bool = False) -> int: ...


build("web", release=True)  # checked: parameters, names, return type
```

<!-- example: fragment -->

```python
build(7)  # a type error, before anything runs
```

**`.opts()` keeps it too — and its own options are typed.** An opted
reference calls exactly like the bare task, chains included, and the
policy options themselves (`keep_going`, `atomic`, `cwd`, …) are a closed
typed set: the names complete inside the parens, and a misspelt one is a
static error at the call site.

```python
build.opts(atomic=True)("web")  # still (target: str, release: bool) -> int
```

<!-- example: fragment -->

```python
build.opts(atomci=True)  # a type error: not a policy option
```

**Markers vanish at the type level.** `suggest`, `nosplit`, `between`,
`env`, `check`, `doc`, `ask` and the path requirements ride inside
`Annotated`, and `Many[T]`, `Arg[T]`, `Forward[T]`, `Stdin[T]`,
`Stdout[T]` are `Annotated` aliases — inside the body every parameter is
its plain type. A `Stdin[str]` parameter is a `str`; a task returning
`Stdout[dict]` returns a `dict` to a body caller.

**Availability gates are identity.** `@requires`, `@requires_dep`,
`@requires_tool` and `@requires_env` hand back exactly what they wrap, so
they never cost you the signature — whichever side of `@task` they stand on.

**The rest of the surface states its types.** `run()` returns a `Result`
(`.code`, `.ok`, `.stdout`, `.stderr`); `parallel(*calls)` returns
`list[Result]` — a record per call, and since `Result` subclasses `int`,
reading one as its exit code still checks — and a `with parallel() as p:`
block *is* that list, so `p[0]` is a `Result` too (`p.results`, the values
the queued calls returned, is heterogeneous by nature and typed to say so);
`select()` answers a string menu with `str` and `(label, value)` pairs with
the value's type; `prompt()` returns `str` — or `T` when `type=` names a
plain class, with `Literal`/`Annotated` forms falling back to `Any` —
and `confirm()` returns `bool`; the testing
`Runner` returns a typed `InvokeResult`. Registering a lifecycle hook
returns the hook unchanged, type included.

One deliberate edge: calling a runnable *group* (`lint(fix=True)`) is
untracked statically, because the group's default is attached after the
group exists — no type system can retrofit that. The default's own handle
keeps its signature, so call it directly when you want parameter hints.

## Private is private

The public surface is what the [API reference](_generated/api.md) documents: the
`footman` namespace and the documented modules (`footman.testing`,
`footman.params`, …). Everything underscore-prefixed is
implementation — it moves without notice, and the types deliberately
don't invite you in. If something private turns out to be the only way to
do something real, that's an issue worth opening, not an import worth
writing.

## Checked, not promised

These are not aspirations; they are enforced. Footman's gate type-checks
its whole tree — tests included, as consumer code — under all four
checkers on every platform's typeshed, verifies the public API 100%
type-complete on every run, and holds a pair of consumer-shaped typing
suites: one asserting the shapes above stay legal, one asserting the
misuses stay errors. A signature that loosens fails the build before it
reaches a release.
