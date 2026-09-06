# Custom CLI

Footman is a library first: `fm` and `footman` are the default-branded
instance of a public `App`. Point your own console script at an `App` carrying
your project's names and version, and every message the user sees (errors,
`--version`, hints) uses *your* branding instead of footman's.

This is how you ship an internal tool under its own name (say `acme`) that is
still footman underneath.

## Build a branded entry point

```python
# acme/cli.py
from footman import App

app = App(
    name="Acme",  # long / display name  → the --version banner
    prog="acme",  # short / command name → "acme: ..." errors and hints
    version="1.4.0",  # YOUR version, not footman's
)


def main() -> None:
    raise SystemExit(app.run())
```

A brand can also rename the tasks file its users write:
`App(..., tasks_file="acmetasks.py")`. Per-project config (the `tasks` key,
in *your* brand's table, see below) still overrides it, and background
completion refreshes honour it, because the filename rides inside the cached
manifest.

Register it as a console script in your package:

```toml
# acme/pyproject.toml
[project.scripts]
acme = "acme.cli:main"
```

Add a `__main__.py` beside it, three lines, so `python -m acme` works too:

<!-- example: fragment -->
```python
# acme/__main__.py
from acme.cli import main

if __name__ == "__main__":
    main()
```

That matters more than it looks. Anything that has to *wrap* your CLI in
another process reaches for the module form — `coverage run -m acme
check`, a profiler, a sandbox — and `python -m footman` runs **stock
footman**, not your brand: it would lose your built-ins, your
`[tool.acme]` table and your `ACME_*` variables, silently, because it is
a different runner wearing the same import. Meter your own module, or the
console script by path (`coverage run $(command -v acme) check`).

Now your tool is fully rebranded:

```console
$ acme --version
Acme 1.4.0

$ acme nonexistent-task
acme: no task named 'nonexistent-task' (know: build, test, deploy)
```

Tasks and plugins can ask which CLI is running: `footman.prog()` is the
command someone types (`acme`), `footman.dist()` the package that ships
it (`acme-cli`, or `None` if you never declared one). Generated output —
a CI workflow, a README line — should use them rather than hardcoding
`fm`, so what you emit names the command your reader actually has. Inside
a task body, `ctx.prog` says the same thing and is the better reach:
declare a `ctx: Context` parameter and read it, which is what footman's
own taskdocs plugin does.

## Where the two names show up

| Setting     | Used for                                                    |
| ----------- | ----------------------------------------------------------- |
| `name`      | the `--version` banner and any display heading (long name)  |
| `prog`      | the error prefix (`acme: …`) and the completion hint (short) |
| `version`   | the `--version` output, your project's version              |

`version` is optional; omit it and footman's own version is used.

## Tasks and completion are unchanged

Your branded CLI discovers tasks exactly like `fm`: the
[`tasks.py` cascade](monorepos.md) from the repo root down to the current
directory. Completion works through your binary too, as
`acme --complete …`, and stays on the same stdlib-only fast path, because
`App.run()` handles `--complete` before importing the framework.

!!! tip "Keep completion fast"

    If your entry-point module imports heavy code at the top (your task
    definitions, third-party libraries), you pay that cost on every
    <kbd>Tab</kbd>. Keep `acme/cli.py` lean, building the `App` and nothing
    else, and let the `tasks.py` cascade carry the tasks.

## The uv handoff follows the brand

A globally installed branded CLI hands off exactly as `fm` does: when the
project's `uv.lock` pins footman and you aren't inside its interpreter environment,
the handoff re-execs the *(branded)* footman you invoked: `acme` hands
off to the project's own `acme`, never to `fm`. The prog you typed is the
prog that runs; only the version moves.

The second rule, a tasks file that declares its own dependencies, needs
one thing more, because it has to know which distribution ships *your*
runner:

```python
app = App(name="Acme", prog="acme", version="1.4.0", dist="acme-cli")
```

With `dist` set, `acme self.install` also knows what to put on the
user's PATH — it runs `uv tool install --upgrade acme-cli --with uv`, the
same install-or-update your users would otherwise type by hand, and the
rest of the `self.*` group (`add`, `remove`, `uninstall`, `path`) manages
that installation in your brand's name. And a
tasks file carrying a
[PEP 723](https://peps.python.org/pep-0723/) header runs in its own
script environment under `acme` too, and must list `acme-cli` among its
dependencies, the way a footman one lists `footman`. Left unset, footman
never guesses a distribution into a script environment: the rule stays out of
your way, and your users' tasks files run exactly where they already did.

## Two folders of your own

Your CLI is a product; footman is a dependency inside it. It keeps things in
exactly two folders, and you place each one:

```python
from pathlib import Path

app = App(
    name="Acme",
    prog="acme",
    cache_dir=Path.home() / ".acme" / "cache",
    data_dir=Path.home() / ".acme" / "data",
)
```

**Cache** is derived data: completion manifests, timing history, the
collector's stamp. It is safe to delete, and footman's collector sweeps it by
age. **Data** is durable and machine-local: credentials, tokens, generated
assets. Nothing ever collects it.

They are not anchored to each other, which matters when your product already
has somewhere for one of them:

```python
import os

acme_home = Path(os.environ.get("ACME_HOME", Path.home() / ".acme"))
app = App(
    name="Acme",
    prog="acme",
    cache_dir=acme_home / ".cache" / "acme-cli",
    data_dir=acme_home / "acme-cli",
)
```

`ACME_CACHE_DIR` and `ACME_DATA_DIR` override them at run time, which is what
lets two installations run side by side under different identities. Left unset
with those variables unset, they fall back to `~/.cache/acme` and
`~/.local/share/acme`.

!!! danger "The two must differ"

    footman refuses to start if the cache and data directories resolve to the
    same place. The collector deletes from the cache by age; pointed at your
    data it would eventually delete credentials.

## Task authors just ask for a folder

None of the above is a task author's problem. They ask for the kind of folder
they want and get one that exists:

```python
import footman
from footman import task


@task
def login(token: str):
    (footman.data_dir() / "credentials.json").write_text(token)


@task
def index():
    (footman.cache_dir() / "index.json").write_text("{}")
```

Both create the directory if it isn't there, so a task never writes a `mkdir`
of its own, and neither knows or cares whether the two share a parent.

## Environment variables follow the brand

`acme` reads `ACME_CACHE_DIR`, `ACME_DATA_DIR`, `ACME_CONFIG_DIR`,
`ACME_CONFIG`, `ACME_CASCADE`, `ACME_NO_GC` and `ACME_NO_UV`, never footman's.
That isolation is the point: someone debugging `fm` with `FOOTMAN_CACHE_DIR`
set must not silently relocate your product's cache. Error messages name your
spelling too, so a user is never taught a variable that does nothing for them.

!!! note "XDG is honoured, and never scrubbed"

    The `XDG_*` variables move the *defaults*: a user who set
    `XDG_CONFIG_HOME` in their profile asked every application to relocate,
    and `acme` follows like the rest. The brand's placements and the
    `ACME_*` variables outrank it, because the specific beats the general.
    And footman never unsets `XDG_*` for the processes it launches: those
    are the user's own tools, and the variable is the user's message to all
    of them. The only environment footman touches in a child is its own
    `ACME_*` namespace.

The prefix is `prog` uppercased, so the command is `acme` and the variables are
`ACME_*`. Set `env_prefix` when that isn't what you want, for either of two
reasons.

**A terse command makes a poor variable.** footman's own command is `fm`, but
its variables are `FOOTMAN_*`: `FOOTMAN_CACHE_DIR` tells a reader who has never
heard of the tool what it belongs to, and is something they can search for.
`FM_CACHE_DIR` is neither. If your command is two or three letters, pin a longer
prefix.

**Or the namespace collides with your product's own.** Keeping it clear is
yours to arrange, because footman never guesses which of your variables is which:

```python
app = App(name="Acme", prog="acme", env_prefix="ACME_RUNNER")
```

## Config files follow the brand too

Your users write `acme.toml`, or a `[tool.acme]` table in `pyproject.toml`,
not footman's. Both come from one field, so they cannot drift apart:

```python
app = App(name="Acme", prog="acme")  # config_name defaults to prog: `acme`
```

Two branded CLIs can then live in one repository, each reading its own
settings instead of fighting over a shared table.

`config_name` is the machine word, the same rule the env prefix follows, and
never the display name, which is free text nobody would type into a TOML
table. Set it when the identity your users should write isn't the command:
footman's own command is `fm`, but its config is `footman.toml` /
`[tool.footman]`, pinned for the same reason its variables are `FOOTMAN_*`.

The *user-level* config file is deliberately not yours to place. It stays at
`~/.config/acme/config.toml`, where a user looks for their own settings.
`ACME_CONFIG_DIR` relocates that corner, the config file and the user tasks
file together, without touching `XDG_CONFIG_HOME`, which would drag every
other application along; `ACME_CONFIG` names a single file, finer still.

## Your users' own tasks

`~/.config/acme/tasks.py`, beside that config file and moving with it under
`ACME_CONFIG_DIR`, holds a user's personal tasks. It is the cascade's
**outermost rung**: personal tasks ride everywhere, inside projects and out,
and anything nearer shadows them. A project task that shares a name wins,
the nearest-wins reading the cascade already has taken one rung further out,
and `--where` points at whichever definition answered.

The rung claims no root. Outside a project `inv.root` is empty and a
`cwd="root"` task runs where the command was typed; `cwd="taskfile"` is the
file's real home, the config directory. Inside a project, `root` is that
project's top, exactly as for every other task. `-f` stays total control:
exactly the named file, no rung.

## Tasks built into the product

Outside a project, a branded CLI has nothing to offer by default — while
the tasks someone needs *before* a project exists (log in, create a
project) are exactly the ones it should offer there. `builtin=` names the
surface your CLI provides with no project at all:

```python
app = App(name="Acme", prog="acme", dist="acme-cli", builtin=["acme.global"])
```

The names are `footman.tasks` entry points: strings, never live objects,
because the background refresh child rebuilds completion manifests in a bare
process where a name travels and an object cannot. The ladder is
**project > user > built-in**, and it holds wherever you stand: the built-in
set is the cascade's outermost rung, the user tasks file sits over it, and a
project's own files sit over both. Everything nearer shadows what is further
out by name, so a project that wants a name a built-in also uses simply
takes it. Nothing is privileged: the set is an ordinary entry point, so a
project can also mount it deliberately — `plugin("acme.global")`, with
`into=`/`only=`/`exclude=` as usual — to place it somewhere of its own
choosing. `--plugins` reports the set as `built in`.

**A built-in task belongs to a project unless it says otherwise.** That is
the default because most of what a CLI ships does: `deploy` without a
checkout is not a shorter `deploy`, it is a task that exits 0 having done
nothing.
A listing shows the noise, but the real damage is the confident fiction.
Declare the exceptions:

```python
@task(expose="always")
def whoami():
    """Who am I logged in as? Answerable from anywhere."""
```

Outside a project, a task that needs one is **not listed, not completed,
and not suggested**, and asking for it by name is refused with the reason
rather than a "no task named", because it does exist:

```console
$ acme deploy
acme: deploy needs a project — no tasks.py found here or in any parent of /tmp
```

That message is aimed at what usually types it: a tool that started in the
wrong directory. It says where footman looked, which is the fact that
resolves the confusion.

`group("ci", expose="project_only")` answers for a whole subtree, and a
child may still answer for itself, the tri-state `hidden` has. The third
answer, `expose="global_only"`, says the opposite — a task that only makes
sense *before* a project exists, like creating one.

The **user rung defaults the other way**: a personal task rides everywhere
unless it says `expose="project_only"`, because that is what a personal
tasks file is for. Each rung's default is its own promise: a package declared
`builtin=` exposes nothing until a task opts in; a file someone wrote for
themselves is theirs everywhere until they opt out.

Outside a project every task runs where the command was typed, exactly as
the user rung does.

Completion follows: outside a project, <kbd>Tab</kbd> answers from one
manifest shared by every project-less directory, keyed by the brand and its
version, so it is cold once per upgrade, not once per directory.
