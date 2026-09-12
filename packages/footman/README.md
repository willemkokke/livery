# footman

A task runner with the soul of [duty](https://pawamoy.github.io/duty/) and
the UX of [typer](https://typer.tiangolo.com/): typed function signatures
become real flags and positionals, modules become nested command groups,
independent tasks run in parallel by default, and shell completion answers
from a cached manifest — **without importing your code**. Building that
cache does import it, once, in a detached subprocess: the first
<kbd>Tab</kbd> in a fresh directory pays for it, and no keystroke after that.

Ships two console scripts, `footman` and the two-letter `fm` — one program,
both names, so a `PATH` that already has an `fm` still gets the long
spelling. Zero runtime dependencies. Python 3.11+ (the first Python with
stdlib TOML reading — that floor is what buys the zero).

> **Beta.** footman is pre-1.0: the surface is settling, but minor versions
> may still include breaking changes — always called out in the
> [changelog](https://github.com/willemkokke/footman/blob/main/CHANGELOG.md),
> never in a patch release. Pin the minor
> (`footman~=0.52.0`) if you build on it. What is covered, what is internal,
> and what has to be true before 1.0 are written down in the
> [stability promise](https://willemkokke.github.io/footman/stability/).

## Why

`duty` gets a lot right — the `run()` capture model, the decorator
ergonomics — and footman keeps those ideas. It tries to improve on the parts
you meet every day: completion served from a cache instead of re-importing your
project on every <kbd>Tab</kbd>, eager type and choice
validation with errors that teach, a DAG scheduler that runs independent
tasks concurrently — four steps that each wait on something finish in the
time of the longest, not the sum — with true fail-fast that kills in-flight work on the
first failure, runnable command groups and parameter forwarding that make
composite commands like `fm lint` and `check` real commands, a monorepo task
cascade that merges a `tasks.py` per directory, and a first-party story for
testing your tasks. The receipts live
in the [comparison](https://willemkokke.github.io/footman/comparison/) —
every number reproducible from
[`comparison/`](https://github.com/willemkokke/footman/tree/main/comparison).

## Taste

```console
uv add --dev footman        # or: pip install footman
```

```python
# tasks.py
from typing import Literal
from livery.footman import task, group, run
from livery.toolroom.tools import pytest, ruff


@task
def lint(fix: bool = False):
    "Run ruff over the project."
    ruff.check("src", fix=fix)


@task
def typecheck():
    "Type-check the project."
    run("basedpyright src")


@task(pre=[lint])
def test(*pytest_args):
    "Run the test suite (extra args after --)."
    pytest(*pytest_args)


@task
def deploy(target: Literal["dev", "staging", "prod"]):
    "Deploy to a target."
    run(f"./deploy.sh {target}")


docs = group("docs", help="Documentation")


@docs.task(infinite=True)
def serve(port: int = 8000):
    "Serve the docs locally."
    run(f"mkdocs serve -a localhost:{port}")
```

```console
$ fm lint --fix
$ fm lint typecheck test                # one chain; independent tasks run in parallel
$ fm docs.serve --port=8001             # options go right after their task
$ fm test -- -k grammar -x              # everything after -- goes to pytest
$ fm deploy produ
fm: deploy: <target> must be one of dev|staging|prod (got 'produ') — did you mean 'prod'?
$ fm --install-completion               # detects your shell; TAB answers from the cache
```

> That `from livery.toolroom.tools import …` is optional.
> [toolroom](https://willemkokke.github.io/toolroom/) makes command-line
> calls Python: it wraps **any** program, not a fixed list, with keyword
> arguments becoming flags — `toolroom.terraform("plan")` runs whether or
> not toolroom has heard of terraform. For common tools it also ships type
> hints generated from each tool's own metadata, so your editor knows the
> flags and what they do; the hints only decide whether your editor can
> help, never whether a call works.
>
> It began as part of footman and was spun out for two reasons: it releases
> on its own schedule, and other people might want type hinted, documented
> command-line calls without using footman. footman does not depend on it
> and never imports it — plain `run("…")` is always there.

### One file, dependencies included

A tasks file can declare what it needs inline
([PEP 723](https://peps.python.org/pep-0723/)), and then it needs no
project at all — drop it in any directory and run it:

```python
#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["livery-footman", "httpx"]
# ///
from livery import footman
from livery.footman import task


@task
def health(url: str = "https://example.com"):
    "Check a deployment is up."
    import httpx

    print(httpx.get(url).status_code)


if __name__ == "__main__":
    footman.main(__file__)
```

`fm health` builds that environment once and runs inside it — name any
other file with `-f=deploy.py` and the same rule applies. `chmod +x`
and `./deploy.py health` works with no runner installed at all. Checked
into a project that pins footman, the header is simply ignored and the
file runs on the project's dependencies — portable and at home.

## Learn more

**[Documentation](https://willemkokke.github.io/footman/)** — start with
[Getting started](https://willemkokke.github.io/footman/getting-started/),
then the good parts:
[typed signatures](https://willemkokke.github.io/footman/typing/) ·
[chaining & parallelism](https://willemkokke.github.io/footman/orchestration/) ·
[monorepos](https://willemkokke.github.io/footman/monorepos/) ·
[composing tasks](https://willemkokke.github.io/footman/composing/) ·
[running tools](https://willemkokke.github.io/footman/tools/) ·
[testing your tasks](https://willemkokke.github.io/footman/testing/) ·
[completion](https://willemkokke.github.io/footman/completion/) ·
[CI & automation](https://willemkokke.github.io/footman/ci/) ·
[comparison with duty / invoke / poe / typer](https://willemkokke.github.io/footman/comparison/)

MIT licensed. The road to 1.0 lives in
[ROADMAP.md](https://github.com/willemkokke/footman/blob/main/ROADMAP.md).
