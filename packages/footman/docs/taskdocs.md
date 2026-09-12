# Your tasks, documented

Footman ships a first-party plugin that renders a project's task tree as
markdown: the same names, params, docstring help, defaults, and examples
that `fm --help` shows, as pages you can publish. Everything on this page is
dogfooded: the [task reference](_generated/tasks/index.md) in this site's nav is
generated output, and the sample further down is embedded live.

## Write plain prose

Footman surfaces a docstring, and a [`doc()`](typing.md#validation-markers)
string, **as plain text**: verbatim in `fm --help`, so it reads in any terminal
and survives a pipe (`fm --help build | less`) byte for byte, and as plain
paragraphs in the exported markdown. It renders no rich markup in the terminal:
no bold, no headings, no reflowed tables. That is deliberate: footman is
[zero-dependency](comparison.md), and plain text is the one format every
terminal, pager, and CI log agrees on.

So write your help as prose. A sentence or two that reads straight is worth more
than markup footman won't paint, and it exports cleanly either way. The one
styling footman *does* apply is [colour](configuration.md), to what footman
itself prints (names, receipts, the progress line) and, through the tools it
spawns, to their output, never injected into your text. Your task's own stdout is yours; footman
routes it untouched.

(A future opt-in could render markdown in the terminal too, see the
[roadmap](roadmap.md), but it stays off by default and never becomes a
dependency.)

## Mount it

The plugin mounts like any other, in one line in your tasks file:

```python
from livery.footman import plugin

plugin("footman.docs")
```

That's also the one-line demo of the [plugin system](composing.md): the
entry point is the identity, `into=` is your placement, and after the mount
`fm --list` shows `docs.page` and `docs.site`. (Cherry-pick
with `only=`, or pass `into="footman"` to nest the group deeper, as
`footman.docs.page`.)

## One page: `fm docs.page`

```sh
fm docs.page > TASKS.md          # the whole tree, one document
fm docs.page --target=docs       # just one group…
fm docs.page --target=docs.build # …or one task
fm docs.page --out=TASKS.md      # write the file directly
```

The page goes to stdout (stdout is the answer; footman's summary is stderr
commentary), so it pipes:

```sh
fm docs.page | pandoc -o tasks.pdf     # or .html, .docx, …
```

`--heading=2` (up to 6) makes the headings start deeper, so the output nests
under a host page's own title, which is exactly how the sample below is
embedded, via a [`pymdownx.snippets`](https://facelessuser.github.io/pymdown-extensions/extensions/snippets/)
include of a file the docs build regenerates:

```markdown
--8<-- "packages/footman/_generated/tasks-page.md"
```

`--flavor=plain` (the default) is pure CommonMark and pipe tables, safe for
pandoc and any renderer. `--flavor=material` opts into what a
zensical/mkdocs-material site already understands: heading anchors for
stable deep links and an `!!! example` admonition for the synthesized
invocation.

## A linked site: `fm docs.site`

```console
$ fm docs.site docs/tasks
wrote 20 pages under docs/tasks
```

One file per task, an `index.md` per group with relative links, directories
mirroring your group tree: drop it into your docs source and put the index
in your nav. This site's **Task reference** section is exactly that, wired
into [`zensical.toml`](https://github.com/willemkokke/footman/blob/main/zensical.toml)'s
nav. `site` defaults to `--flavor=material` because a docs site is where it
lands; pass `--flavor=plain` for anything else.

## The runner itself: `fm docs.globals`

Your tasks aren't the only thing worth documenting; the runner's global
options deserve a page too. `globals` renders them as a markdown table
straight from the CLI grammar: the same rows, in the same order, with the
same words `--help` prints. This site's [CLI reference](reference.md) table
is exactly that, regenerated on every docs build, so it *cannot* drift,
because it was never written by hand.

```console
$ fm docs.globals --out=_generated/globals.md
wrote _generated/globals.md
```

## Terminal screenshots: `fm docs.shots`

Prose about colours drifts the moment the palette changes; a screenshot
generated from the CLI cannot. `shots` runs a command on a real
pseudo-terminal (colours, receipts, taught errors, exactly as a terminal
shows them), collapses the live rewrites to their final frame, and renders
the capture as an SVG in a macOS-style window:

```console
$ fm docs.shots --out=docs/_generated/shots/run.svg -- format lint
wrote docs/_generated/shots/run.svg
```

Everything after `--` is the command line to capture; `--width` sets the
terminal columns, `--title` the window title, and `--cmd` swaps the
executable (default: the CLI that invoked it, so a branded CLI screenshots
itself; `App` is a library, and the screenshotter simply runs *your* CLI).
The command really executes, so don't screenshot tasks whose side
effects you don't want. Every terminal image on this site is one of these,
regenerated by the docs build.

This task needs [rich](https://github.com/Textualize/rich) and a POSIX
pseudo-terminal. Neither is a footman dependency: the task is gated with
footman's own `@requires_dep("rich")`, so without rich it lists as
`shots … (unavailable: requires rich)` and refuses to run with that
message; add rich to your docs dependency group and it comes alive.
Footman documenting itself with its own availability machinery is exactly
the use `@requires_dep` was built for.

## Animated sessions: `fm docs.cast`

A static frame can't show <kbd>Tab</kbd> completion. `cast` boots a real
interactive shell (zsh, bash, fish, pwsh, or nushell) from a scratch config
with footman's hook loaded, types a keystroke script, and replays the
captured bytes through a terminal emulator into an **animated SVG**: CSS
keyframes with the session's own timing, no JavaScript, plays anywhere an
image does. The session even answers its shell's terminal interrogations
(capability, cursor, and colour queries) the way a plain xterm would, since
modern shells refuse to paint a prompt into silence:

```console
$ fm docs.cast --out=docs/_generated/shots/zsh-cast.svg \
      --shell=zsh -- "fm " "<TAB>" "<WAIT>" "che" "<TAB>" "<ENTER>" "<WAIT:2500>"
wrote docs/_generated/shots/zsh-cast.svg (55 frames)
```

Everything after `--` is the script: plain text is typed at a human-ish
cadence; `<TAB>`, `<ENTER>`, `<SPACE>`, `<BACKSPACE>`, `<CTRL-C>`,
`<WAIT>`, and `<WAIT:ms>` are keys. The shell really runs what you type:
the recording on the [zsh completion page](completion-zsh.md) ends with a
real `fm check`. Needs rich and [pyte](https://github.com/selectel/pyte)
(the terminal emulator), gated the same way: without them the task lists
as unavailable and says which package to add.

## Keep it fresh

Generated pages drift unless a build regenerates them. The tasks are plain
functions, so footman's own docs task calls them directly. Copy the shape:

```python
from pathlib import Path
from livery.footman import group

docs = group("docs", help="Documentation")


@docs.task(name="build")
def docs_build(check: bool = False):
    "Build the docs site; regenerates the task reference first."
    from livery.footman.tasks.docs import globals_, page, site
    from livery.toolroom.tools import zensical

    site(Path("docs/tasks"))
    page(target="docs", heading=3, out=Path("_generated/tasks-page.md"))
    globals_(out=Path("_generated/globals.md"))
    zensical.build(clean=True, strict=check)
```

Add the generated paths to `.gitignore`: they're build output, not source.
Under [`--json`](json.md), both tasks `return` the list of files they wrote,
so `returned` carries it for CI to verify.

Two flags to know. Usage lines and examples carry **the CLI you invoked**, so
a [branded CLI](custom-cli.md) documents itself as `acme` with no flag at
all, and `--prog` overrides the name when you need to. `--all` includes the
mounted `footman` group itself (excluded by default, since the documenter
doesn't document itself unless asked).

## Link the terminal back to the pages

Once the pages are published, close the loop: a `docs_url` template under
`[tool.footman]` turns every task and group name in `--list`, `--tree` and
the `--help` pages into a terminal hyperlink to its own docs, and puts a
`docs_url` field on each task's [`--json`](json.md) row for readers that
cannot click.

```toml
[tool.footman]
docs_url = "https://docs.example.dev/tasks/{path}/"   # docs.site: page per task
# docs_url = "https://docs.example.dev/reference/#{slug}"  # docs.page: anchors
```

`{path}` is the task's slash-joined address (`docs.build` → `docs/build`),
matching the per-task pages `docs.site` writes; `{slug}` is the dash-joined
one (`docs-build`), matching the heading anchors the one-page `docs.page`
export carries. An unknown placeholder is refused by name. The links ride
the colour switch — piped output stays plain, and a terminal without
hyperlink support simply ignores them — and each `--help` page also prints
the URL as visible text, so there is always something to copy.

## The live sample

Everything below this line is `fm docs.page --target=docs --all
--heading=3 --flavor=material`, regenerated on every docs build:

--8<-- "packages/footman/_generated/tasks-page.md"
