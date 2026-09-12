# The shell

!!! question "Already know this?"
    1. When you type `pytest -x | tail` in a terminal, what program
       interprets the `|`?
    2. What happens to `*.py` when no shell is involved?
    3. Why does the same command line need different quoting on Windows?

    All three easy? Skip to [Spawning programs](foundations-spawning.md).

## The concept

The **shell** (bash, zsh, PowerShell…) is just a program: it reads a line
of text, interprets the punctuation (`|` pipes, `>` redirects, `*.py`
globs, `$HOME` expansions) and spawns the programs the line names. None of
that punctuation means anything to the operating system's "run a program"
call, which takes a plain list of argument strings. No shell in the middle,
no pipes, no globs, no expansions: the punctuation arrives at the program
as literal text.

Quoting is the shell's second job, and every shell does it differently.
POSIX shells split words by one set of rules, Windows hands programs a
single raw command line, which is why "worked in my terminal" and "works
from code on every platform" are different achievements.

## Why it matters to a task runner

A task runner spawns programs all day, and silently inserting a shell into
every spawn would mean inheriting its whole interpretation layer (injection
hazards, platform quoting, startup files) for the many calls
that never wanted it. Not inserting one means a command with `|` in it
would *silently* not pipe. Both silent options are wrong; the right design
is to make the choice explicit.

## What footman does about it

`run("cmd …")` uses **no shell**: the string is split into arguments
(platform-correctly) and spawned directly. If the string contains a shell
operator, footman refuses with a taught error rather than passing `|` as a
literal argument. Ask for a shell (`shell=True`, or a named one like
`shell="bash"`), split the pipeline into separate `run()` steps, or pass a
list to use the character literally. A shell you *ask* for is resolved by a
policy (`[tool.footman] shell.default`), can be hardened (`strict=True` for
`set -eo pipefail`, `clean=True` to skip startup files), and receives the
whole string to interpret, punctuation and all.

!!! warning "`shell=True` on Windows means git-bash, and it eats backslashes"

    The default policy is `posix`, which on Windows resolves to git-bash so a
    pipeline written once behaves the same everywhere. A POSIX shell treats
    `\` as an escape character, so a command carrying Windows paths like
    `C:\src\app` arrives with the separators stripped. When the string must
    reach a tool as *Windows* text, ask for the platform's own shell:

    ```python
    from livery.footman import run

    run(r"build.exe --out C:\dist", shell="native")  # cmd, not git-bash
    ```

    Same choice as the `shell.default` config key, made per call. A command
    with no paths in it, the pipelines `shell=True` exists for, is
    unaffected.

The [Running commands guide](tools.md) covers the product surface; the
principle here is the point: **the shell is an interpreter you opt into,
never an accident**.

## The one rule

**No shell unless you ask; ask when you mean pipes.**
