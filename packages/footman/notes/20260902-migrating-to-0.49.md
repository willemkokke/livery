# Migrating to footman 0.49

For hse and livery. Everything here is mechanical unless marked
**judgement**; budget roughly one sitting per repo.

Five breaking changes, all in one release because they are one change:
built-in tasks became part of the cascade, and everything else follows
from that.

---

## 1. `needs_project=` becomes `expose=`

A rename with a third answer. Search for `needs_project` and rewrite:

| before | after |
| --- | --- |
| `@task(needs_project=True)` | `@task(expose="project_only")` |
| `@task(needs_project=False)` | `@task(expose="always")` |
| `group("ci", needs_project=True)` | `group("ci", expose="project_only")` |

The third value is the one the boolean could not express:
`expose="global_only"` — offered **only outside** a project. Use it for
anything that sets a project up (scaffolding, cloning, first-time login).

A decorator spelling exists if you keep your gates above the signature:

```python
@task
@expose("global_only")
def bootstrap(): ...
```

Unset still inherits the enclosing group. A value that is not one of the
three is refused **at registration**, so a typo cannot quietly put a task
somewhere you meant to keep it out of.

**Rung defaults are unchanged**: a package's tasks are `project_only`
until one opts out; a personal `~/.config/<brand>/tasks.py` rides
everywhere.

---

## 2. Built-in tasks are part of the cascade

Previously a brand's `builtin=` set was mounted **only** where discovery
found no project. Inside a project it was not there at all, and reaching
one meant mounting it by hand:

```python
# tasks.py — no longer needed just to reach a built-in
plugin("acme.global")
```

The set is now the cascade's outermost rung: under your user tasks file,
under the project's own files, reachable wherever you stand. Anything
nearer shadows it by name, so a project that wants `deploy` for itself
simply takes it.

**What to check:**

- **Delete `plugin("…")` mounts that existed only to reach built-ins.**
  They still work — an explicit mount places the set where you choose —
  but they are no longer required, and a mount *plus* the base means the
  tasks appear twice (once where you put them, once at the root).
- **Look for name collisions.** A project task and a built-in sharing a
  name used to be impossible inside a project; now the project's wins.
  Usually what you want — but check you are not shadowing the one you
  meant to call.
- **Judgement: audit your package's `expose`.** Tasks that never said
  anything default to `project_only`, which is right for most of a
  devkit. Anything meaningful *before* a project exists now needs
  `expose="always"` or `"global_only"` to be reachable out there, and
  anything meaningless *inside* one should say `"global_only"` so it
  stops appearing in every project.

`-f/--tasks-file` is unchanged: one file, no cascade, no base.

---

## 3. `fm new` is `global_only`

It writes a starter tasks file, which is what you do before a project
exists. Inside a project it is now unlisted and refused by name — **even
where a tasks file mounted `footman.new` deliberately**. If you relied on
that mount, replace it with your own scaffolding task.

**Known papercut, in a monorepo:** `cd packages/new-thing && fm new` is
inside a project (the repo root has a tasks file), so it is refused. Write
the starter file yourself, or give the repo a scaffolding task. If this
bites, say so — the clean fix is `new` taking a target directory rather
than relying on cwd.

---

## 4. `builtin = …` becomes the `[builtins]` table

The single key had footman and you writing the same list. There are now
three sources, mounted in that order and deduplicated:

1. the runner's own set — declared in its code, so it cannot go stale
   against an upgrade;
2. what `fm self.*` discovered — machine-written, in the **data
   directory**, never in your config file;
3. `builtins.user` — only you write it, footman only reads it.

```toml
# ~/.config/footman/config.toml   (or ~/.config/<brand>/config.toml)
[builtins]
user = ["acme_devkit"]
# discovery_mode = "auto"   # auto | manual | internal | none
```

| before | after |
| --- | --- |
| `builtin = ["acme_devkit"]` | `[builtins]` with `user = ["acme_devkit"]` |
| `builtin = true` | **removed** — let `fm self.add` write the discovered list, or name the packages under `user` |

`builtin = true` went because it re-resolved on **every invocation**
(~20 ms of a ~75 ms run, forever) and swept in the runner's *own* entry
points — on a stock install it would have mounted footman's docs, env-file
and profile providers, two of which exist to contribute global options
rather than task trees.

`discovery_mode` selects which sources contribute. `builtins.user` is
honoured in **every** mode, so `none` is not an off switch — it means
*nothing automatic, only exactly what I named*:

```toml
[builtins]
discovery_mode = "none"
user = ["footman.self"]   # keeps fm self.* reachable
```

A name in `builtins.user` that is not installed is **refused**, not
skipped: under `none` that list is the only thing between you and an empty
runner, so silence there would look like footman losing your tasks.

---

## 5. `--self-install` becomes `fm self.*`

| before | after |
| --- | --- |
| `fm --self-install` | `fm self.install` |
| — | `fm self.add <pkg>…` — install packages beside the runner |
| — | `fm self.remove <pkg>…` — drop them again |
| — | `fm self.uninstall [--purge]` |
| — | `fm self.path [place…]` |

`add` and `remove` are **additive**, which the flag never could be: uv
rewrites a tool environment from the requirements it is given, so a plain
upgrade that forgot your extras would drop them. Both read uv's own
receipt first and hand the whole set back.

`self.uninstall` clears footman's cache, data directory and completion
hooks but **keeps your config** unless you pass `--purge`.

`self.path` is for scripts: named, it prints one bare line
(`DIR=$(fm self.path data)`); bare, every location; under `--json`, the
whole mapping in one call.

### If you ship a devkit

Advertise a `footman.builtin` entry point to say "these are meant to be
mounted as built-in tasks". `fm self.add <your-package>` then records them
automatically under the default `auto` mode, and a user's
`discovery_mode` still decides whether they actually mount — a package
cannot mount itself merely by being installed.

---

## 6. Nothing to do, but worth knowing

- **Manifest schema 9.** Old completion manifests are refused and rebuilt
  on the next run or press. If you pin one in CI, drop it.
- **New public accessors** — `config_dir()`, `config_file()`,
  `user_tasks_file()`, `project_root()` join `cache_dir()` and
  `data_dir()`, so a task never has to shell out to learn where its own
  config lives.

## What is *not* breaking

- Every other decorator, marker and global option.
- The `--json` envelope, still schema 1.
- The `[tool.footman]` keys you already set.

## Checklist

```sh
grep -rn "needs_project" .        # → expose=, per the table in §1
grep -rn "self-install" .         # → fm self.install
grep -rn "^builtin *=" ~/.config/footman/config.toml   # → [builtins] user = [...]
grep -rn 'plugin("' .             # → drop mounts that only reached built-ins

fm --list                         # inside a project: anything unexpected?
cd /tmp && fm --list              # outside one: is the global set right?
```
