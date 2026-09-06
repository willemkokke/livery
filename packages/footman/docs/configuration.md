# Configuration

Settings are discovered the way tasks are: from files along the path to
your current directory, nearer files winning, with sensible behaviour when
no config exists at all. Everything on this page is optional.

## The precedence ladder

From weakest to strongest, each rung overriding the ones below it, key by
key:

1. **Built-in defaults.** No config, full behaviour.
2. **Your user-level file** — `~/.config/footman/config.toml` (honouring
   `XDG_CONFIG_HOME`; point `FOOTMAN_CONFIG` at a different file to move
   it). Personal defaults for every project: a purist's `uv = false`, a
   permanent `progress = false`.
3. **The project cascade** — walking from the repo root down to your
   current directory, each directory may contribute settings; nearer directories
   override farther ones. Within one directory, a standalone `footman.toml`
   overrides `[tool.footman]` in `pyproject.toml`, the customary
   dedicated-file-wins rule.
4. **`--config=PATH`** — total control over config: the named file
   *replaces* the user file and the config cascade entirely (the tasks side
   has the same escape hatch, `-f/--tasks-file`). You said exactly what
   applies.
5. **Environment variables** — `FOOTMAN_NO_UV`, `FOOTMAN_CACHE_DIR`, and
   friends always beat file config. (`NO_COLOR` and `FORCE_COLOR` are
   gentler: they are `--color`'s *default*, and a project's `color` key or an
   explicit `--color=` outranks them, because they speak for the terminal
   in general, not for this invocation.)
6. **Command-line flags** — `-s`, `-j`, `--no-progress`… always win.

The cascade is what makes monorepos comfortable: a package deep in the
tree can carry a two-line `footman.toml` that adjusts behaviour for that
subtree only:

```toml
# services/deep/package/footman.toml — this subtree runs inside the
# already-active parent environment; don't hand off to uv run.
uv = false
```

The repo root's `pyproject.toml` still sets the shared defaults.

## The files

In a `pyproject.toml`, settings live under the tool table:

```toml
[tool.footman]
tasks = "tasks.py"
sequential = false
```

A standalone `footman.toml` is the same keys, top-level:

```toml
tasks = "tasks.py"
sequential = false
```

The user-level `~/.config/footman/config.toml` uses the standalone form.
Unknown keys are kept but ignored, so a newer setting never breaks an
older footman.

## Where footman writes

Nothing footman writes lands inside your project. Three user-level
directories, each honouring its `XDG_*` variable:

| Directory | Default | What lives there |
| --------- | ------- | ---------------- |
| Cache | `~/.cache/footman/` (`XDG_CACHE_HOME`) | Derived data, safe to delete: completion manifests, timing history, `fetch()` downloads. The cache collector sweeps this directory and nothing else. |
| Data | `~/.local/share/footman/` (`XDG_DATA_HOME`) | Durable machine-local state that survives every cache sweep. |
| Config | `~/.config/footman/` (`XDG_CONFIG_HOME`) | Your own writing: the user-level `config.toml` and the user-level tasks file, which travel together. |

The **completion hooks are the one thing outside all three**: they live at
`<data home>/<command>/completion.*` — `~/.local/share/fm/completion.zsh`,
not `~/.local/share/footman/` — because they are keyed by the command you
type rather than the config name. `fm --uninstall-completion` and
`fm self.uninstall` both remove them.

### What footman reserves

A plugin is welcome in these directories — `footman.data_dir()` is there so
you do not have to invent a home of your own — so here is exactly what the
runner claims, and what is therefore yours:

| Directory | footman writes | free for you |
| --------- | -------------- | ------------ |
| `cache_dir()` | `<key>.json` and `<key>.times.json` (completion manifests and timing history), `global-<key>.json`, `gc.stamp`, and the `fetch/` folder | any other name — but the collector sweeps this directory by age, so nothing you would mind losing |
| `data_dir()` | `builtins-<key>.json` (the discovered built-in list, one per Python environment) | any other name |
| `config_dir()` | nothing — footman only ever *reads* `config.toml` and the tasks file here | your own files, though footman will not read them |

The safe convention is **one subfolder named after your distribution** —
`data_dir() / "acme-devkit" / …` — which cannot collide with a future
reserved name, since anything footman adds will be a file or folder named
for what it does.

Two things to know before you settle in. `cache_dir()` is swept by age, so
it is for things you can rebuild and nothing else. And `fm self.uninstall`
removes the cache and data directories **whole**, plugin state included —
the price of living in the runner's home rather than your own.

The spellings are the same on every operating system — the convention uv,
ruff, and git's own XDG support follow — so on Windows everything lives
under `%USERPROFILE%`: `C:\Users\you\.cache\footman\`,
`C:\Users\you\.config\footman\`, and so on. The `XDG_*` variables move the
base directories on any platform, and the
[footman-specific variables](#environment-variables) below move each
corner on its own.

## Tasks outside every project

Some tasks live outside any one project: creating one, cloning a repo,
logging in. A package can ship them as a `footman.tasks` entry point, and
the user-level `builtin` key mounts them as built-in tasks — the cascade's
outermost rung, under your own tasks file and under every project's, so
they are reachable wherever you stand and anything nearer shadows them by
name. What each one offers *where* is the task's own answer
(`expose=`), not an accident of the rung.

```toml
# ~/.config/footman/config.toml
[builtins]
user = ["acme_devkit"]      # entry points you name yourself
```

There are **three sources**, and they mount in this order: the runner's own
set (declared in its code — the product), then whatever `fm self.*`
discovered when you last added a package, then the names you wrote in
`builtins.user`. Duplicates are dropped, so a name the runner already
declares is never mounted twice.

The discovered list is machine-written and lives in footman's **data
directory**, not here — so this file stays yours alone and nothing footman
writes can trample a comment you left. `builtins.user` is the opposite: only
you write it, footman only reads it, and a name in it that is not installed
is refused rather than skipped.

`builtins.discovery_mode` says which of the three contribute:

| mode | the runner's own | discovered | `user` |
| ---- | ---------------- | ---------- | ------ |
| `auto` (default) | yes | yes, kept current by `fm self.*` | yes |
| `manual` | yes | yes, left exactly as it is | yes |
| `internal` | yes | ignored | yes |
| `none` | no | no | yes |

`none` is not an off switch — it means *nothing automatic, only exactly what
I named*. It drops the runner's own commands too, including `fm self.*`, so
if you want them back name them:

```toml
[builtins]
discovery_mode = "none"
user = ["footman.self"]
```

The whole table is **user-level only**: which packages your runner carries
is your machine's business, not any project's, and a project that wants a
set placed somewhere of its own mounts it the ordinary way in its tasks
file.

A package's tasks belong to a project unless one says otherwise:
`@task(expose="always")` says a task works anywhere,
`@task(expose="global_only")` says it only makes sense *before* a project
exists, and an unmarked task refuses by name where it does not belong
rather than going missing. `fm --plugins` shows which source mounted what
— `built in` for the runner's own, `built in (your config)` for the rest.

Editing the table reaches <kbd>Tab</kbd> on the press after next, the way
every user-level edit does (see
[keeping the cache current](completion.md#keeping-the-cache-current)).

## Keys

Every key the runner recognises, rendered from its own list on each docs
build, so this table can neither invent a key nor miss one:

--8<-- "packages/footman/_generated/config.md"

## Environment variables

| Variable            | Effect                                              |
| ------------------- | --------------------------------------------------- |
| `FOOTMAN_CONFIG`    | Path of the user-level config file.                 |
| `FOOTMAN_CONFIG_DIR` | Moves footman's config corner whole (the config file and the user-level tasks file travel together). |
| `FOOTMAN_CACHE_DIR` | Moves every footman cache (completion manifests, timing history). |
| `FOOTMAN_DATA_DIR`  | Moves footman's data directory (durable files that survive a cache sweep). |
| `FOOTMAN_NO_UV`     | Disables both uv handoffs (project and script environment), regardless of any config. |
| `FOOTMAN_NO_GC`     | Disables the cache collector, regardless of any config. |
| `FOOTMAN_CASCADE`   | Overrides the `cascade` key for one invocation (`none` / `repo` / `filesystem`). |
| `FOOTMAN_STACKS_AFTER` | Seconds between stack dumps to stderr, for a run that stops moving ([Troubleshooting](troubleshooting.md#when-a-run-stops-moving)). |
| `NO_COLOR` / `TERM=dumb` | Disable ANSI styling, for footman and for every tool it spawns, which footman tells to stay monochrome too. |
| `FORCE_COLOR`       | Force ANSI styling on, even piped (below `--color` and `[tool.footman] color` in the ladder). |

Values for a *task's* environment are a different lane: `.env` files load
through the first-party `footman.env_files` plugin (`--env-file=.env`) —
see [the built-in on the hooks page](hooks.md#the-built-in-footmanenv_files)
— and a per-parameter `env("VAR")` marker reads a single variable as a
default.

See also [Monorepos & config](monorepos.md) for how the tasks cascade
itself composes, and the [CLI reference](reference.md) for the flags that
sit at the top of the ladder.
