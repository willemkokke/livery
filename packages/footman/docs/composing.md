# Composing the task surface

A tasks file doesn't have to be a flat list you write by hand. Footman treats
a task tree as a *value*: you can hide tasks with plain Python, disable them
with a reason, adopt tasks from other modules, and mount tasks a pip-installed
package advertises. One contract ties it together: everything resolves when
your code imports (so completion keeps answering from its cache), and
conditions re-check *live* when a task actually runs.

## Hidden, omitted, disabled

Three different intents, and the difference is what happens when someone
names the task anyway:

**Hidden — out of the listings, callable as ever.** For the tasks a machine
calls and a human never types: a CI entry point, a step another task drives.

```python
from livery.footman import task


@task(hidden=True)
def ci_publish(): ...
```

It drops out of `--list`, `--tree` and group help, the listings a human
reads to learn what a repo does. Everything else is untouched: `fm
ci-publish` runs it, <kbd>Tab</kbd> completes it, the did-you-mean index
knows it, a `pre=`/`post=` dependency runs it, a runnable group's
empty-body fan-out still includes it, and `--json` reports it *marked*
rather than missing, since a machine is exactly who calls it, so the catalog
keeps it. The generated task docs list it too, badged, because the docs are
where you look up something the listings won't offer.

Hiding and completing are different questions. A listing is prose about the
project; completion is help with a name you are already typing, and a
machine-facing address (long, dotted, typed by hand exactly when something
has gone wrong) is the one most worth being spelled for you.

`hidden` is inherited: unset means "whatever my group said", so one
declaration hides a whole subtree, and a child can still come back.

```python
from livery.footman import group

internal = group("internal", hidden=True)  # the whole subtree, one word


@internal.task
def sweep(): ...  # hidden, like its group


@internal.task(hidden=False)
def status(): ...  # listed again, deliberately
```

Setting it on a `@group.default` says the same thing about the group it
speaks for. A group whose every task ends up hidden prints no heading at
all, rather than an empty one.

`--all` (`-a`) puts the hidden rows back, in `--list`, `--tree` and help
alike: the one flag for "show me everything, including what I'm not meant
to type":

```console
$ fm --list
Tasks:
  internal.status
$ fm --list --all
Tasks:
  ci-publish
  internal.sweep
  internal.status
```

**Omitted — the task does not exist.** A tasks file is executed code, so an
`if` does exactly what it says: no address, nothing to call, nothing to
list. Reach for it when the task is *meaningless* here, not merely
uninteresting to type.

```python
import sys
from pathlib import Path

if sys.platform == "darwin":

    @task
    def notarize(app: Path): ...
```

**Disabled but listed** — pytest-skip semantics, for "this task exists but
can't run *here*":

```python
from livery.footman import task, requires_tool


@task
@requires_tool("docker")
def up(detach: bool = True):
    "Start the dev containers."
```

```console
$ fm --list
Tasks:
  up  Start the dev containers.  (unavailable: requires docker on PATH)
$ fm up
fm: up: Unavailable: requires docker on PATH
```

The name always completes and lists, since the manifest stays stable, and every
availability gate is re-evaluated **live** on every run, so the moment docker appears on
PATH, `fm up` works, whatever the cached manifest thought. `@requires_tool`,
`@requires_dep`, and `@requires_env` are the common availability gates (a tool on
`PATH`, a Python module importable, a variable set) and `@requires(predicate, reason=…)`
is the generic they build on. Stack as many as apply: **every** failure is
reported, each in its own words, so a task needing both a tool and a variable
says both. A predicate that raises reads as unavailable (a broken availability gate must not
swing open).

Keep the availability gates **below `@task`**, as above: `@task` on top, `@requires_*`
stacked beneath it. Either order works, for a type checker too: an availability gate sets an
attribute on the same task object and hands back exactly what it wrapped, so
the task's typed signature and `.opts()` stay in view whichever side of `@task`
it stands on. This order simply reads the way it works: `@task` is the
identity, the availability gates are modifiers under it.

!!! warning "Keep a predicate cheap, because it runs live"

    An availability gate's predicate runs **every time the manifest is built**: on
    every `fm --list`, every help render, and every background cache refresh, not
    only when the task runs. That liveness is the whole point (no stale
    availability), but it means a slow availability gate slows *listing*, not just
    execution. Keep predicates to a `which`, an `in os.environ`, a `find_spec`
    (which is what `@requires_tool`/`_env`/`_dep` already do); never a network
    call or a heavy import. The completion hot path is exempt, since a `<Tab>`
    reads the baked reason from the cache and runs no predicate, but the refresh
    that fills that cache is not.

A `pre`/`post` dependency on a disabled task is a **hard failure**, not a
silent skip: silently dropping `lint` from `check` on the wrong machine is
how CI learns to lie. When you want the optional-dependency flow, compose the
list instead:

<!-- example: fragment -->
```python
@task(pre=[fmt, lint] + ([docker_up] if shutil.which("docker") else []))
def check(): ...
```

## Two typed verbs over one engine

Composition is two sibling verbs. Where a mount comes from differs; what
happens after it (walk, land, filter, merge) is identical:

- **`plugin("acme.devkit.lint")`** mounts from an installed package's
  **`footman.tasks` entry point**, the console-script of task trees: a
  stable public identity for a Group the package offers, enumerable,
  inert until mounted.
- **`include("mytasks.lint")`** mounts from an **importable module**, the
  same grammar over your own reach: file-splitting, monorepo-local sharing.

The type tag lives in the verb, so no string is ever resolved against both
registries, so there is no precedence and no silent re-pointing when a new
package lands. The model is Python imports: `plugin("acme.devkit.lint")` is
`from acme_devkit import lint` for task trees; mounting a whole container is
the `import *`, safe here because your own definitions silently win and
imported-vs-imported clashes are loud.

## Mounting from your own modules: `include()`

<!-- example: fragment -->
```python
from livery.footman import include

include("shared_tasks")  # everything, at root
include("shared_tasks", only=["lint", "fmt"])  # cherry-pick children
include("mytasks.lint")  # one group out of a module
include("mkdocs_helpers.tasks", into="docs")  # namespaced: fm docs.…
```

The longest importable prefix is imported inside a registry capture (the
provider's decorators can't leak into your tree); the rest of the string
walks the captured tree, so `include("mytasks.lint")` mounts one group out
of a module the way an import mounts one name. Then the engine grafts:

- **A node lands under its own name.** `include("mytasks.lint")` → `fm
  lint`. A whole module is an anonymous container, so mounting it lands its
  *children*, the splat. `into=` (a dotted address, created on demand) is
  your placement; there is no rename.
- **Your names win, silently.** A task or group you define shadows a
  mounted one of the same name, whatever the file order, exactly as nearer
  cascade files shadow farther ones.
- **Mount-vs-mount clashes are loud.** A same-address leaf from two mounts
  raises, citing both providers; pass `override=True` when the later mount
  should win. Group-vs-group is composition, never a clash: two mounts into
  one subtree merge all the way down.
- **Filters take full dotted addresses**, relative to the mounted node:
  `only=["docs.build", "fmt"]` grafts one nested task and one flat one,
  materialising the path (the intermediate groups are the source's own
  copies, help text riding along). Matching is exact; the whole-group
  spelling `only=["docs"]` *is* the glob, and `only=["docs",
  "docs.build"]` is redundant, not an error. A group pruned empty is
  dropped entirely. The default action is just the child named `default`,
  so `only=["lint.default"]` grafts *just* the default action and `exclude=["lint.default"]` grafts
  everything but it, readable without ever opening the provider's source.
- **Typos are loud.** An unknown filter address errors per segment,
  naming what that level actually has.
- **Included tasks run from *your* directory**: a shared lint task lints
  this project, not the provider's install location.
- `--where=lint` still points at the provider's source, so provenance is
  one flag away, and `fm --plugins` lists every installed entry point,
  mounted or not, with where it landed.

Two idioms worth knowing. Renaming a single task needs no machinery at all:
`@task` returns plain functions, so `task(name="fmt")(shared.fmt)` re-exports
one under a new name. And a bare `from shared_tasks import build` at the top
of a tasks file is the one form to avoid: the import executes the provider's
decorators against *your* registry, all-or-nothing, sensitive to import
order. `include()` exists so you never need it.

### A shared library with heavy or optional dependencies

Say you keep release tasks in a `devkit` library, and some need heavy
third-party packages (an API client, a cloud SDK). You want to
`include("devkit.tasks")` at the top of your monorepo's `tasks.py` without
paying those imports on every `fm lint`. You already can; it comes down to
where the heavy `import` lives:

```python
# devkit/tasks.py
from livery.footman import task, requires_dep


@task
@requires_dep("stripe", reason="pip install devkit[release]")
def publish(version: str):
    "Cut and publish a release."
    import stripe  # imported only when publish actually runs

    ...
```

`include()` imports `devkit.tasks` to read task *signatures* for the
manifest, listing, and completion, and it never runs a body. So a body-level
`import stripe` costs nothing until `fm publish` executes; `fm lint`,
`fm --list`, and every `<TAB>` stay clean. (Keep your CLI parameter types
cheap (`version: str`, `dry_run: bool`) for the same reason; an exotic
annotation is the one thing signature introspection might try to resolve.)

`@requires_dep` closes the last gap: the *optional* dependency. It names modules
the task needs, checked with `importlib.util.find_spec`, which locates them
**without importing**, so a missing package makes the task list as
`(unavailable: pip install devkit[release])` and refuse to run with that
message, instead of crashing with a raw `ModuleNotFoundError`. Installed or
not, the check never imports the package; your body still does, only when it
runs. (`find_spec` is import-free for a top-level distribution; a deeply
dotted name like `google.cloud.storage` imports its parent packages, so name
the top-level dist where you can.)

## Mounting from installed packages: `plugin()`

A package publishes a `Group` under the `footman.tasks` entry point:

```toml
# the plugin package's pyproject.toml
[project.entry-points."footman.tasks"]
"acme.mkdocs" = "acme_mkdocs:tasks"
```

```python
# acme_mkdocs/__init__.py
from livery.footman import Group, requires_tool

tasks = Group("mkdocs", help="MkDocs site tasks")


@tasks.task
def build(strict: bool = True): ...


@tasks.task
@requires_tool("mike")
def deploy(version: str): ...
```

And a project **opts in** with a mount line in its tasks file:

<!-- example: fragment -->
```python
from livery.footman import plugin

plugin("acme.mkdocs")  # fm mkdocs.build, fm mkdocs.deploy
plugin("acme.mkdocs", only=["build"])  # just one child
plugin("acme.mkdocs.build", into="site")  # one task, placed by you
plugin("acme.devkit")  # a container of groups: the splat
```

The longest installed entry-point name is the **identity**, consumed at
resolve time and retained as provenance, and the rest of the string walks the
advertised tree, dot by dot. The mounted node lands under
its **own name**: the identity (`acme.mkdocs`) never becomes an address,
and placement is always yours (`into=`). A provider advertising a whole
container of groups splats them: one line adopts a devkit, and a devkit
update that adds a group just appears on the next mount.

Design choices you can rely on:

- **Never auto-loaded.** `pip install something` growing your command
  surface unasked is a supply-chain surprise; the task surface stays
  reproducible from the files in your repo. The `importlib.metadata` scan
  runs only when a mount line asks for it, only on the execution path, so the
  completion hot path never changes, and footman stays zero-dependency.
- **A missing plugin is a crisp error** naming the entry points that *are*
  installed, because a typo or a missing install should read as one. `fm
  --plugins` lists them all, marked mounted-or-not and where they landed,
  so "installed but nobody mounted it" is visible.
- **Your names win.** A task or group you define shadows a mounted one of
  the same name silently; two mounts clashing at one leaf is loud, and the
  message cites both identities.
- **Publisher convention:** advertise either one named group (an ecosystem
  plugin) or a container of groups (a devkit, which splats). Loose tasks in
  a published container are a smell: the splat drops them straight into
  every consumer's top level. Entry-point names stay vendor-prefixed
  (`acme.devkit`) for identity hygiene in the shared registry, not address
  design.

Footman's own tooling follows the same rule: built-ins are ordinary,
opt-in plugins. This repo's tasks.py mounts its first-party
plugin: `plugin("footman.docs")` is [your tasks,
documented](taskdocs.md) (`fm docs.page` / `site`). A branded CLI writes
`into="acme.tools"` instead, since branding is a one-line authoring choice,
not framework machinery. A naming symmetry to
know: the `footman.tasks` entry-point *group* is served by the
`footman.tasks` *package*: different namespaces, one product.

## Around and beyond: hooks

Editing the merged tree once per invocation, running code around every
task, one-task hooks on the task's own handle, and a plugin's global
options all live on [Hooks & plugin options](hooks.md), together with
the caching contract every composing verb above shares.
