# Completion

Completion answers from a JSON manifest cached per directory under
`~/.cache/footman/` (or `$XDG_CACHE_HOME/footman/` where that's set, and
`$FOOTMAN_CACHE_DIR` overrides both, moving every footman cache in one go),
so each directory of a [monorepo](monorepos.md) caches its own merged cascade. The hot
path is stdlib-only: it reads one file, parses JSON, and walks the tree, and it
**never imports footman or your tasks**.

## The latency story

Measured cold-process on an M-series Mac. The row that matters is the last
one, because it measures the installed hook's command path end to end (the
benchmark pins a fixture manifest so runs stay comparable):

| variant                                    |   mean |
| ------------------------------------------ | -----: |
| interpreter startup (floor)                | 13 ms  |
| standalone resolver (`python -S`)          | 18 ms  |
| `python -m livery.footman --complete`             | 20 ms  |
| `fm --complete` (the installed hook path)  | 18 ms  |

A structural answer — task names, options, `Literal` choices — costs a few
milliseconds on top of Python starting up at all, which is the floor nothing
in this design can go under (and which moves with your interpreter: these are
CPython 3.14 on an M-series Mac). A
[dynamic completer](#dynamic-completions-are-recomputed-fresh) or the
[first build in a fresh directory](#keeping-the-cache-current) costs more, by
design and bounded. Footman regenerates the manifest for free on any
execution-path run (it is importing your code anyway) and rewrites it only when
the command surface actually changed. Reproduce with
`uv run python scripts/bench_completion.py`.

## How it stays fast

Footman's `main()` checks for `--complete` **before importing the framework or
your tasks**, dispatching straight to the stdlib-only resolver. A bare
`from livery import footman` pays for nothing but the entry module — no pathlib, no
subprocess, no typing, which an invariant test pins. That is why a keystroke
costs what it does rather than what re-importing your project costs. When a
live value is genuinely needed (a dynamic completer, or the first build in a
fresh directory) footman *spawns* a subprocess for it rather than importing on
the hot path, so even then the keystroke stays stdlib-only and can't hang on
your code.

## Dynamic completions are recomputed fresh

A [dynamic completer](typing.md#dynamic-completion) (`suggest(fn)`) queries live
state: git branches, release candidates, deploy targets. When <kbd>Tab</kbd>
lands on one, footman runs that completer **fresh** in a short-lived subprocess:
answering a build-critical question from a stale snapshot is a bug, not a
speed-up, so the manifest holds no snapshot to serve. Expect such a press to
cost what the cold build costs — around 100 ms for a fresh interpreter plus
your completer, against ~20 ms structural. The recompute is bounded
(a couple of seconds) and isolated, so a slow or failing completer degrades to
*no* candidates, never a hung keystroke, and never the old values.

Only the dynamic value pays that cost. Task names, options, and `Literal` choices
still answer instantly from the cache, because those can't change without an edit
to your tasks file.

## Keeping the cache current

The cached manifest is structural, the shape of your CLI, and rebuilds for free
on any real `fm` run. The very first <kbd>Tab</kbd> in a fresh directory, with
nothing cached, builds it once (a beat slower, around 100 ms) and answers
accurately rather than staying blank until that first run. That wait is capped
at a second, and the build is detached: a tasks file heavy enough to miss the
cap leaves the first <kbd>Tab</kbd> blank and the next one instant, rather than
a keystroke that appears to hang. From then on the cache answers instantly; if
it drifts (you added a task) past `max_age`, footman serves the cached answer and
spawns a **detached** rebuild for next time (stale-while-revalidate), so a warm
<kbd>Tab</kbd> never waits on it, and concurrent presses spawn at most one rebuild.

The flip side of never waiting: a task you just wrote can stay invisible to
<kbd>Tab</kbd> for up to `max_age` (10 minutes by default — see below to
tune it). The cache refreshes by age, not by watching your file, and an
aged press still answers from the old cache while the rebuild lands behind
it. Any real `fm` run rebuilds the manifest as part of its work, so when
you want the new task on the menu right now, run anything — `fm --list` is
the cheapest — and the very next <kbd>Tab</kbd> knows it.

Your **user-level** files are the exception, because they are the ones you
edit expressly to change what `fm` offers: the user config and
`~/.config/footman/tasks.py`. Each manifest records what those looked like
when it was built, and a press notices the difference (two `stat` calls,
after its candidates are already on screen — the keystroke waits on
nothing). So a change there rebuilds behind the *next* press rather than
waiting out the clock: press once to see the old menu, again to see the
new one. Creating either file counts as a change, exactly as editing one
does. `completion.max_age = "off"` still means no background rebuilds at
all — it asks for the clock and the trigger alike to stay quiet.

A tasks file that **fails to import** is an answer too, not a silence:
the rebuild leaves behind what the import said, and <kbd>Tab</kbd> shows
it where the shell can — zsh as a message line, fish, nushell and
PowerShell as a note beside the word you typed (accepting it changes
nothing), bash stays quiet. Either way the press answers instantly rather
than paying the cold build's wait into nothing, and the note ages out in
seconds once the file imports again. The full story is one line, so `fm
--list` prints the same error with its traceback when you want the rest.

The rebuild happens in the environment the **run** would use. In a project
whose lockfile pins footman, the child builds with the project's own
interpreter — whichever `fm` answered the keystroke, a globally-installed
one included — so <kbd>Tab</kbd> describes the same tasks the run serves.
A stale project environment (a dependency locked after the last sync) is
mended on the way from uv's cache alone — strictly offline, a keystroke
never downloads — and where that cannot finish, the import-failure note
above ends with the actual fix, `run uv sync`, instead of a bare
`ModuleNotFoundError`.

**Is it safe to press <kbd>Tab</kbd> in a repository you just cloned?**
Treat it like running the code, because the cold build above *is* an
import of the repo's `tasks.py` — the same import a run does, in a
detached subprocess. That is the honest answer for every tool in this
category: a tasks file is a program. What footman guarantees is narrower
and precise: the process answering the keystroke never imports anything
(the children carry `-P`, so a planted `footman.py` in the directory
cannot hijack the import either), and nothing executes twice that a plain
`fm --list` would not already have executed once. Don't trust the repo?
Read `tasks.py` first — it is the same trust decision as `make` or `npm
install`, made visible.

``` mermaid
graph LR
  tab["Tab press"] --> fresh{cache fresh?}
  fresh -->|yes| answer["cached answer, no imports"]
  fresh -->|"no, stale"| serve["serve cached answer now"]
  serve --> rebuild["spawn detached rebuild"]
  rebuild --> nexttime["fresh for the next Tab"]
```

Tune it with `[tool.footman]`:

```toml
[tool.footman]
completion.max_age = "10m"   # default; "30s", "1h", a plain int (seconds)
# completion.max_age = "off" #   or 0, disabling background refresh
```

## Path-style task completion

A nested task's address is one dotted token (`fm docs.serve`), and completion
treats the `.` the way your shell treats `/` in a file path: a group completes
with a trailing dot and the next <kbd>Tab</kbd> lists what's inside it, so a
descent reads like `cd` with dots:

```sh
fm do<TAB>        # docs.  docker.
fm docs.<TAB>     # docs.build  docs.serve
fm docs.s<TAB>    # docs.serve ␣
```

A group with a [default action](orchestration.md#runnable-groups) completes to
its bare name *and* its dotted children: a space runs the default, a `.`
descends. When only one group matches, completion steps straight through it,
the way zsh descends a lone subdirectory.

Two generosities round out the `cd` idiom, and both are **completion-only**:
the runtime resolver stays strict, so an abbreviation that works today can
never change meaning when a new task lands, and scripts cannot rot:

- **Segment-wise abbreviation.** Each typed segment prefix-matches its own
  tree level, the way zsh expands `/u/l/b` to `/usr/local/bin`:

    ```sh
    fm t.sy<TAB>      # tools.sync
    fm d.<TAB>        # ambiguous first segment: db.  deps.  dns.  docker.  docs.
    ```

    Because footman generates the candidates itself, every shell gets the
    expansion, not just zsh.

- **Leaf-name fallback.** When what you typed matches no top-level name at
  all, completion tries *last* segments instead, the rescue for "I know the
  task, not where it lives":

    ```sh
    fm serve<TAB>     # docs.serve
    ```

All five shells are supported to the best of each shell's ability; the
observable differences (description columns, menus, the space after a unique
match) are collected in [Shell differences](completion-differences.md).

## Chained completion

Completion is aware of the whole command line, not just the first word:

```sh
fm workspace.mount --share=<TAB>   # main  scratch  archive
fm format lint --fix <TAB>         # completes within the chain
```

Note the `=`. Every value in footman's grammar is attached, so `--share=` is
where a value goes, and completing an option offers **both** of its
spellings, so you never have to know that in advance:

```text
--share      — which share to mount
--share=     — which share to mount
```

Take the bare one to mean "use its default" (a
[bare mention](plugins.md)); take the `=` one and press <kbd>Tab</kbd>
again to pick a value. A flag has one spelling only: it takes no value, and
`--no-fix` is how you turn one off.

Group names, task names, flags, options, and both static and
[dynamic](typing.md#dynamic-completion) value sets all complete. Where a shell
can show them (zsh, fish, and nushell render a description column, pwsh a
tooltip), **every word footman offers carries its own line**, so holding
<kbd>Tab</kbd> teaches the whole CLI:

```text
build      — compile and bundle
deploy     — ship to an environment
--fix      — apply safe fixes in place
--jobs     — max parallel tasks
--env-file — the .env file to load
```

A task or group name shows its one-line docstring, an option shows its
[`doc("…")`](typing.md) line (or the `Args:` entry it came from), a global
shows what `--help` says about it, and a plugin's global shows the `help=`
it declared. Nothing is written twice for the sake of completion: the words
are the ones already on the page.

## Narrowing a path value

A `Path` parameter hands off to the shell's own file completion, since footman
answers from a cached manifest and never touches the filesystem. `matching()`
is what it hands *along*: the pattern the shell filters by.

```python
from pathlib import Path
from typing import Annotated
from livery.footman import matching, task


@task
def load(env_file: Annotated[Path, matching(".env*")] = Path(".env")): ...
```

`fm load --env-file=<Tab>` then offers `.env`, `.env.local`,
`.env.production`, not every file in the directory. footman's own
`--env-file` and `--profile` declare theirs this way.

Directories are always offered whatever the pattern says, or a match one
level down would be unreachable. The glob matches the file's *name*
(`*.json`, not `**/*.json`), the vocabulary all five shells share.

It is **completion only.** A path typed anyway still binds: narrowing what
<kbd>Tab</kbd> shows is a convenience, and a filter that quietly became
validation would refuse the perfectly good path someone pasted. Use
[`exists`/`isfile`](typing.md) or `check()` when you mean a rule.

Two shells have their own say. fish does not offer dotfiles until you type
the leading `.`, so `.env*` shows once you do. That is fish's behaviour for
every command, not footman's. And **nushell is not filtered**: narrowing
there means returning a list of footman's own, which replaces nushell's
built-in file completion outright and loses directory descent. An
unfiltered walk that reaches every file beats a filtered one that reaches
only this directory's.

## File paths

A value that takes a filesystem path completes files: footman hands off to
your shell's own path completion rather than reading the disk from its cached
manifest. This covers the path-valued globals (`-f`/`--tasks-file`,
`-C`/`--directory`, `--config`) and any task parameter annotated `Path`,
whether an option, a positional, or a variadic:

```sh
fm -f=tasks/<TAB>            # your shell's own file completion
fm build --out=dist/<TAB>    # a Path option
fm deploy dist/<TAB>         # a Path positional (options stay one `-` away)
```

A plain `str` or `int` value has no such handoff: it completes nothing, rather
than bluntly offering files where a name was wanted.

A comma-splitting list completes one item at a time: mid-list, completion
works on the segment after the last comma and keeps what's already typed in
place, so `--paths=src/a.py,<TAB>` completes the second path. The same goes
for a list with a value set (`Literal` choices or
[`suggest()`](typing.md#dynamic-completion)): each comma starts a fresh
item, and values already in the list aren't offered again. A
[`nosplit`](typing.md#comma-splitting-and-nosplit) parameter keeps its
commas literal, in completion as at the prompt.

Accepting an item doesn't end the list. footman tells the shell the value
can continue, and each shell says so in its own accent: zsh writes the
comma for you as a removable suffix — accept `eu` and get `--regions=eu,`,
Tab again for the next item, while a space or ++enter++ takes the comma
back off; bash leaves the cursor right after the item, so the comma (or
the closing space) is your next keystroke instead of a deletion; fish
carries the comma on each candidate it inserts. pwsh and nushell already
land the cursor right after any completion, which is exactly this.

## Your shell

One command, and footman detects which shell invoked it (by walking the
process tree, the way typer's `shellingham` dependency does, minus the
dependency), or takes the name explicitly:

```console
fm --install-completion         # detected: bash, zsh, fish, pwsh, or nushell
fm --install-completion=zsh     # or name it yourself
fm --uninstall-completion       # reverses exactly what install did
```

Each shell has its own page, covering what gets installed where, a
session-only form, and how to style the completion menu, colours included:

| shell | descriptions shown as | installed via | session-only form |
| ----- | --------------------- | ------------- | ----------------- |
| [bash](completion-bash.md) | — (bash has no description column) | script + rc line | `eval "$(fm --setup-completion=bash)"` |
| [zsh](completion-zsh.md) | aligned column (`_describe`) | script + rc line | `eval "$(fm --setup-completion=zsh)"` |
| [fish](completion-fish.md) | aligned column, native | one auto-loaded file | `fm --setup-completion=fish \| source` |
| [PowerShell](completion-pwsh.md) | tooltip (menu completion) | script + `$PROFILE`(s) | `… \| Out-String \| Invoke-Expression` |
| [nushell](completion-nushell.md) | description column, native | script + config line | `fm --setup-completion=nushell` (save to a file, `source` it) |

Every installer and uninstaller is idempotent: running one twice changes
nothing. A custom-branded CLI installs completion for *its* name the same
way (`acme --install-completion=zsh`), and the generated hook calls that
brand's `--complete`.
