---
title: A typed task runner with instant completion
---

# Footman

[![PyPI version](https://img.shields.io/pypi/v/footman?label=PyPI&color=blue)](https://pypi.org/project/footman/)
[![Python versions](https://img.shields.io/pypi/pyversions/footman)](https://pypi.org/project/footman/)
[![License](https://img.shields.io/pypi/l/footman)](https://github.com/willemkokke/footman/blob/main/LICENSE)
[![Docs built with Zensical](https://img.shields.io/badge/docs-Zensical-4051b5)](https://zensical.org)

A task runner with the soul of [duty](https://pawamoy.github.io/duty/) and the
UX of [typer](https://typer.tiangolo.com/): typed function signatures become
real flags and positionals, modules become nested command groups, and shell
completion answers from a cached manifest — **without importing your
code**. Building that cache does import it, once, in a detached subprocess:
the first <kbd>Tab</kbd> in a fresh directory pays for it, and no keystroke
after that. See [completion](completion.md).

Here is the whole project behind the recording below. No CLI framework, no
argument parser, no completion script: three functions, and the annotations
that say what their parameters are.

<!-- hero-demo: the recordings below are made against exactly this file -->
```python
# tasks.py
from typing import Annotated, Literal

from livery.footman import doc, suggest, task
from livery.toolroom.tools import git


def branches() -> list[str]:
    "Every branch in this repo, asked of git rather than written down."
    return git.branch(format="%(refname:short)").stdout.split()


@task
def deploy(
    branch: Annotated[str, suggest(branches), doc("branch to ship")] = "main",
    region: Annotated[Literal["eu", "us", "ap"], doc("region")] = "eu",
):
    "Ship a branch to a region."


@task
def build(release: bool = False, jobs: int = 4):
    """Compile and bundle.

    Args:
        release: optimise and strip symbols
        jobs: parallel compile jobs
    """


@task
def test(watch: bool = False):
    """Run the test suite.

    Args:
        watch: re-run on every file change
    """
```

=== "fish"

    ![Animated: fm TAB lists every task with its summary, deploy --branch TAB completes to the repository's real git branches, and --region TAB offers the values the signature declares](_generated/shots/hero-fish-cast.svg)

=== "zsh"

    ![Animated: fm TAB lists every task with its summary, deploy --branch TAB completes to the repository's real git branches, and --region TAB offers the values the signature declares](_generated/shots/hero-zsh-cast.svg)

=== "bash"

    ![Animated: fm TAB lists every task with its summary, deploy --branch TAB completes to the repository's real git branches, and --region TAB offers the values the signature declares](_generated/shots/hero-bash-cast.svg)

=== "PowerShell"

    ![Animated: fm TAB lists every task with its summary, deploy --branch TAB completes to the repository's real git branches, and --region TAB offers the values the signature declares](_generated/shots/hero-pwsh-cast.svg)

=== "nushell"

    ![Animated: fm TAB lists every task with its summary, deploy --branch TAB completes to the repository's real git branches, and --region TAB offers the values the signature declares](_generated/shots/hero-nushell-cast.svg)

Real sessions, not mock-ups: recorded from live shells on every docs build,
in all five shells footman supports, one frame per keypress. Nothing above is
typed out in full: every token on that line arrives by pressing
<kbd>Tab</kbd>, and the longest thing typed is two characters. `--branch`
completes to **this repository's actual git branches**, because its
candidates come from a function rather than a list someone wrote down; then
`--region` offers what the signature's `Literal` declares. The menus arrive
at keystroke speed because footman answers them from a cached manifest
instead of importing your project. [How that works](completion.md).

Each recording shows its shell at its best, which for three of them means one
line of setup: zsh's `menu select`, bash's `show-all-if-ambiguous`,
PowerShell's `MenuComplete`. Each shell's page
([zsh](completion-zsh.md), [bash](completion-bash.md),
[PowerShell](completion-pwsh.md)) says which line and why. What footman
supplies is the same everywhere: the candidates, their descriptions, and the
values behind them.

Annotations are how you get the most out of it, not the price of entry: a
plain `def deploy(target, port=8000)` is already a working command with a
positional, a typed option, and completion. [Annotate when you want
more](typing.md#what-if-i-dont-like-annotating-types).

```sh
fm lint --fix
fm format lint --fix test          # a chain: three tasks, no separator
fm workspace.mount --share=<TAB>   # main  scratch  archive
```

![fm --tree in a terminal: tasks grouped by command group, bold names, one-line help](_generated/shots/tree.svg)

Ships two console scripts: `footman` and the two-letter `fm`. (That
screenshot is generated from the real CLI, like every terminal image on
this site.)

!!! note "Beta"

    footman is pre-1.0: the surface is settling, but minor versions may still
    include breaking changes, always called out in the
    [changelog](changelog.md), never in a patch release. Pin the minor
    (`livery-footman~=<minor>.0`) if you build on it. What is covered, what is
    internal, and what has to be true before 1.0 are written down in the
    [stability promise](stability.md).

--8<-- "packages/footman/_generated/latest-changes.md"

The full history lives in the [changelog](changelog.md).

## Why

`duty` gets a lot right — the `run()` capture model, the
decorator ergonomics — and footman keeps those ideas. It tries to improve on
the parts you meet every day:

- Completion answers from a cache instead of re-importing your whole project
  on every <kbd>Tab</kbd>. ([The numbers](comparison.md), if you want them.)
- Types and choices validate eagerly, including unions and dynamic value
  sets, with errors that teach.
- Modules become nested command groups, and task signatures carry no `ctx`
  boilerplate.
- Independent tasks run in parallel by default, scheduled from the chain and
  each task's `pre`/`post` dependencies; duty and invoke run these one at a
  time.
- A monorepo task cascade merges a `tasks.py` per directory, from the repo root
  down to where you stand.

The receipts live in the [comparison](comparison.md): a measured
head-to-head against duty, invoke, poe, and typer, every number reproducible
from the repo's `comparison/` directory.

## Install

```sh
uv add --dev footman        # or: pip install footman
```

Requires Python 3.11+ — the first Python with stdlib TOML reading, which
is what makes zero runtime dependencies possible for a tool configured
through `pyproject.toml`. Linux, macOS and
Windows are all first-class: CI runs the full suite on all three (five
Pythons each, free-threaded included) and drives real shell completion on
bash, zsh, fish, PowerShell and nushell.

## A first taste

Write a `tasks.py` in your project root:

```python
from livery.footman import task, group


@task
def lint(fix: bool = False):
    "Run ruff over the project."
    ...


docs = group("docs", help="Documentation")


@docs.task(infinite=True)
def serve(port: int = 8000):
    "Serve the docs locally."
    ...
```

Then:

```sh
fm lint --fix
fm docs.serve --port=8001
fm --list
```

Head to [Getting started](getting-started.md) to go deeper.

## Calling your tools

Tasks mostly run other programs, and `run("ruff check src --fix")` is a
string your editor cannot help you with.
[toolroom](https://willemkokke.github.io/toolroom/) makes those calls
Python:

<!-- example: fragment -->
```python
from livery.footman import task
from livery.toolroom.tools import ruff


@task
def lint(fix: bool = False):
    "Run ruff over the project."
    ruff.check("src", fix=fix)
```

It wraps **any** command-line program, not a fixed list: keyword arguments
become flags, so `toolroom.terraform("plan", var_file="prod.tfvars")` runs
terraform whether or not toolroom has ever heard of terraform.

For a number of common tools it also ships **type hints**, generated from
each tool's own metadata, so your editor knows that `ruff check` takes
`--fix` and what that does. The hints only decide whether your editor can
help. They never decide whether a call works: a tool without them runs
exactly the same, and one whose flags have moved on costs you a stale
suggestion rather than a broken build.

It began as part of footman and was spun out as its own library for two
reasons. It releases on its own schedule. And other people might want type
hinted, documented command-line calls without having to use footman for
anything.

footman does not depend on it and never imports it. Plain
[`run()`](tools.md) is there regardless, and nothing on this page needs
toolroom.
