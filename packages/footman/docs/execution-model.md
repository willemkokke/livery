# The execution model

Everything on this page follows from three decisions the rest of the docs
already lean on. **Nothing anonymous runs**: footman schedules, records, and
safely cancels only work it owns. **One identity rule everywhere**: the same
task with the same arguments, starting from the same environment, is the same
work, however it was asked for. And
**a declaration is a commitment**: sharing, gates, and the guarantee of a
record exist exactly where a declaration does. Hold those three and every
rule below is a consequence, not a convention.

[Chaining & parallelism](orchestration.md) covers the day-to-day: chains,
`pre`/`post`, `parallel()`. This page is the fine print: what "runs once"
means precisely, what a body call is, how sharing is decided, and where
steps come from.

## One execution per request, however it was asked for

A run performs a task's work **once per task and arguments**, and every way of
asking counts the same: a prerequisite, a chain segment, a body call. So
`fm check check` runs `check` once and reports the second mention as `shared`,
exactly as two `check()` calls in a body would, and exactly as two tasks that
both declare `pre=[check]` do. Nothing about how you reached a task changes how
often it runs.

Different arguments are different work and run, so `fm build web build api` builds
twice. A different policy is a different invocation too, so
`pre=[build.opts(atomic=True)]` does not reuse a plain `build`. And a task (or
one reference to it) that declares [`shared=False`](#work-that-is-never-shared-sharedfalse)
runs for every request, which is how you say "this must happen again".

## A call is part of the run

Calling a task is not a shortcut around footman: the callee gets a real task
boundary, with its own context and working directory, its `@requires` and `confirm`
gates, and its own entry in the report, and the run performs its work **once per
task, arguments, and starting environment**, whoever asks for it. So a
prerequisite you also call hands back what it already produced, which is how a
task reads a value `pre=` cannot pass:

```python
from footman import run, task


@task
def build() -> str:
    ...
    return "dist/app.tar"


@task(pre=[build])
def publish():
    artifact = build()  # the build that already ran, not a second one
    run(f"./upload {artifact}")
```

Whether a task was reached by declaration or by a call makes no difference to
how often it runs, so you never have to hold that distinction in your head. The
same rules follow from it: different arguments are different work and run; a
caller that changed its environment before calling asks for different work too,
so the callee runs fresh instead of being answered by an execution that never
saw the change; calling a task that is running on another thread waits for that
run rather than starting a second; and a call that could never return (a task
calling itself,
or two tasks calling each other) is refused by name instead of hanging.

Two calls footman refuses outright, because a call has nowhere to put them: a
`serial=`/`exclusive=` task (its lane is taken at the task boundary, never
mid-body, which is what keeps the scheduler deadlock-free) and an `infinite`
task (a call that never returns). Declare those with `pre=` instead.

## A call binds like a segment

A parameter the caller leaves out consults the same sources an absent option
does: stdin, then its `env()` variable, then the default, with a defaultless
`ask()` prompting as the last resort, so a task behaves the same however it is
asked for:

<!-- example: fragment -->
```python
from typing import Annotated
from footman import env, task


@task
def build(target: Annotated[str, env("DEPLOY_ENV")] = "dev") -> str: ...


@task
def release():
    build()  # $DEPLOY_ENV if set, "dev" otherwise: exactly like `fm build`
    build("dev")  # explicit, so env is never consulted
```

Footman sees the call before Python fills in defaults, so leaving a parameter
out is not the same request as passing the default's value yourself: an
explicit value wins over env, exactly as a value on the command line does. And
because resolution happens before the work is keyed, a segment, a prerequisite
and a call that resolve to the same values are one piece of work.

An explicit value runs the annotation's validators (choices, bounds, path
requirements, `check(fn)`) because the annotation is the contract wherever
the value comes from. It is never *coerced*, though: a Python caller passes
real values under the signature's types, and the type checker polices those;
coercion exists because the command line only has strings. Outside a run, a
task is the plain function it looks like: a unit test that calls it gets
Python's own semantics, nothing more.

## Work that is never shared: `shared=False`

Some work exists to happen again: a notification, a timestamp, a scratch
clean. `@task(shared=False)` says exactly that: every request for the task
runs, whether the request is a call, a chain segment, or a `pre=` edge. One
rule, so the spelling you used never changes the answer.

Sharing is a property of the *request*, resolved in this order: the reference's
own `.opts(shared=…)`, then the task's declaration, then whatever asked for it,
then shared. `.opts(shared=False)` asks for one unshared run without changing
the task, on a call or on a declared edge alike:

<!-- example: fragment -->
```python
@task
def stamp(): ...


@task
def deploy():
    stamp()  # shared: the run's one stamp
    stamp.opts(shared=False)()  # this one runs, whatever came before
```

An unshared run gets its own value but never rewrites what the run already
remembers: the first result stands, so later shared requests stay stable. A
request answered by an earlier execution is reported as `shared`, so the run
never looks like it did less than you asked.

!!! warning "Unsharing propagates down the subtree"

    An unshared request asks unshared for everything it needs, since otherwise
    the promise would be a half-truth, so one `shared=False` unshares that task's
    **whole dependency subtree**. A `compile` shared by two unshared builds
    runs twice, and a deep tree multiplies. Pin anything that genuinely is
    reusable with `shared=True`, which beats an inherited answer.

## Steps you make yourself

`run()` makes a step out of a command. `step()` makes one out of *your own
code*, in one name and three positions:

<!-- example: fragment -->
```python
import shutil

from footman import step


@step  # 1. a function that IS a step
def clean():
    shutil.rmtree("build", ignore_errors=True)


with step("prepare") as s:  # 2. record a block, where it stands
    write_fixtures()
    s.title = "prepared 3 fixtures"

archive = step(make_archive, title="archive")  # 3. wrap someone else's
```

One note, learned from Python itself: **calling a lifted function
builds its step, it doesn't run it**: `clean()` hands you a bound piece
of work ready to schedule, the same way `range(10)` hands you a range
without counting anything. Hand it to `parallel(clean(), archive("dist"))`
(a zero-argument maker is welcome bare: `parallel(clean, …)`), or call the
built item to run it right here. Either way it earns a full record, a
receipt with a real duration, captured output and a place in `--json`, and
the maker carries the step's policy: `.opts(timeout=…, capture=…,
recorded=…, title=…, env=…, color=…, pre_record=…)` per use,
`@clean.pre_record` for its reviewer and `@clean.post_step` to watch its
sealed record, permanently.

`color=` is the same `auto|never|always` [`run()`](tools.md) takes, applied
to the environment the whole step body runs under, so every command it
spawns and every in-process tool it calls reads one decision, without
threading a keyword through each of them.

A step can also be a generator, which buys two things with one keyword:

<!-- example: fragment -->
```python
@step
def convert(images: list[Path]):
    view = yield  # the step's own record, mid-work
    for done, image in enumerate(images, start=1):
        view.title = f"converting {done}/{len(images)}"
        to_webp(image)
        yield  # a checkpoint, once per image
```

Every bare `yield` is a **checkpoint**: the only kind of place footman
will ever cancel the step, and only for three reasons: the run is failing
fast, Ctrl-C, or the step ran past its own `timeout=`. All three arrive
the same way: the loop simply never resumes, any `try`/`finally` around
the yield runs, and the worst case costs one trip around the loop. The
value a step returns is **data, never an exit code**. A step fails by
raising (or by what its reviewer rules), and a failing item raises
`RunFailed` exactly as a failing command does. The `with step():` block is
the one form that creates no execution boundary: its statements run
exactly as they would bare, dry-run included, and only the record is
new. Deferred makers, by contrast, are footman's to execute, so `--dry-run`
fakes them like any subprocess.

## Making a task while the run is going

`@task` is ordinary Python, so it also works *inside* a body: the decorator
runs when the body does, and what it makes is a real task: its own row,
sharing, hooks, and a place in a `parallel()` block like any other call. It
lives for the run that made it and is swept when the run ends, because the
manifest was written before any of it happened and a task no listing can
show has no business outliving its run.

That makes `task(fn)` the general way to run a plain callable *as a task*,
in a block or out of one:

<!-- example: fragment -->
```python
from footman import parallel, task

with parallel() as p:
    build("web")
    task(tidy_up)("stale")  # a plain function, run as a real task
```

Names are the one wrinkle. A duplicate written in a tasks file is a mistake
and stays a taught error; a duplicate made mid-run is not, so it is
numbered `rmtree` then `rmtree-2`, which is what lets a `lambda` in a
loop, or the same helper used twice, each be their own piece of work.
