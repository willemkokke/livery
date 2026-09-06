# Changelog

All notable changes to footman are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/). While footman is pre-1.0, minor
versions may include breaking changes.

## [0.52.2] - 2026-09-06

### Added

- Footman joins the workspace
- The version rules speak as the ecosystem's own
- The train carries a migrated line's first release

### Fixed

- One release number per distribution line
- The api generator reads the module beside it

## [0.52.1] - 2026-09-06

### Fixed

- **The discovered built-in record is keyed by the environment it
  describes, not the one that wrote it.** v0.52.0 keyed it by the writer's
  `sys.prefix`, which reproduced the very leak the key was added to
  prevent, one hop over: `fm self.*` discovers by asking the **tool**
  environment's own interpreter — that is the only thing that knows its
  entry points — so the answer is never about the process that typed the
  command. Run from a project's venv, `fm self.install` filed the tool
  environment's packages under that venv, which then warned about every
  name it could not import.

  The probe now reports its own `sys.prefix` alongside the names, and
  `write_discovered` requires the prefix rather than defaulting to the
  writer's. If you ran `fm self.install` on 0.52.0 from anywhere other
  than a shell where `fm` resolved to the tool installation, run it once
  more; the misfiled record names an environment that never had those
  packages and is otherwise inert.

## [0.52.0] - 2026-09-06

### Added

- **`--list` and `--tree` split into two sections: project tasks, then
  global ones.** Project first, because your own tasks in the directory
  you are standing in are what you came for; the global rung — the
  built-ins and your personal tasks file — is the backdrop that rides
  everywhere. A section with nothing in it is not printed, and when only
  one side exists the heading stays plain `Tasks:` rather than announcing
  a split that is not there. A group can straddle the two (a personal
  `docs.notes` beside a project's `docs.build`), so it heads a branch in
  each section carrying only that section's children.

  Manifest schema 10 → 11: task rows carry `global` where they come from
  the built-in base or the user tasks file. Caches rebuild on first use.

### Fixed

- **The discovered built-in list is keyed by the Python environment it
  describes.** What it records — which `footman.builtin` entry points are
  importable — is a property of one environment, not of the machine. Keyed
  by brand alone, the record a `self.install` wrote from the *tool*
  environment was then applied by every other runner on the machine: a
  project's dev venv read it, could not import a package that was never
  installed there, and refused to run. It now lives at
  `builtins-<key>.json`, keyed the way completion manifests are keyed by
  cwd — one shared store, each entry named for its subject. So a globally
  installed plugin's tasks answer globally, and a project venv is
  unaffected by them.

  A record written before the key existed names no environment and is
  simply not found, which already means "nothing discovered yet": run
  `fm self.install` once to write the keyed one.

  The record is kept rather than derived on the fly, and deliberately so:
  discovering live on every run would be auto-activation, where any
  installed package advertising `footman.builtin` mounts itself — so
  `uv add` on a library would put its tasks in your CLI, free to collide
  with your project's own names, with nothing recording when it happened.
  The file is the consent boundary, and changes only when you run a
  `self.*` command.

- **A discovered built-in that no longer mounts is skipped, not refused.**
  This list is a record footman writes and can always rebuild, which is
  already why a missing or malformed one means "nothing discovered"
  instead of an error — an entry that stopped resolving is the same thing
  one entry at a time. Refusing also made the remedy unreachable: the
  message says to run `self.add`, and `self.add` mounts the same built-in
  base on its way in, so the way out refused too and the only fix left was
  deleting the record by hand. A name *you* declared in `[builtins] user`
  is a declaration and still refuses.

- **A wrong-case tasks file is now complained about wherever it is, and
  whatever else the run finds.** v0.51.0 shipped this half-built: the
  complaint lived on the "no tasks file found" path, which the built-in
  base returns before ever reaching — so with stock `fm`, a directory
  holding a `Tasks.py` listed `self.*` and said nothing at all. It also
  looked only in the cwd and the cascade top, missing the monorepo case
  where the file that will not load is a package's own, several levels
  from either.

  The complaint now rides the walk itself, at no cost: on a filesystem
  that folds case, the probe hits and the listing that confirms the
  spelling is already in hand at the moment the mismatch is known. So
  every level is covered, no extra directory is ever read, and the
  complaint reaches every exit — including the one where the built-ins
  answer:

  ```
  fm: /w/packages/api/Tasks.py is not tasks.py — the name is case-sensitive,
  so this file is not being loaded. Rename it to tasks.py.
  ```

  It fires even when the walk found a real tasks file elsewhere: you wrote
  a tasks file and footman is not loading it, which is worth saying either
  way.

- **"No tasks file found" now names the case rule.** Where the filesystem
  tells `Tasks.py` and `tasks.py` apart there is no variant for the walk
  to notice, and finding one would mean listing every level of the
  cascade. So the message teaches the rule instead — `looked for tasks.py
  (exactly — the name is case-sensitive)` — which costs nothing and is the
  whole of what a `Tasks.py` owner needs to hear.

- **The test suite no longer reads the developer's own data directory.**
  `builtins.json` lives there, so a maintainer whose *global* `fm` had a
  plugin installed beside it got that plugin's entry point demanded by
  footman's own suite, where it is not installed — five tests red locally,
  green in CI. Tests now run against an isolated `FOOTMAN_DATA_DIR`.

## [0.51.0] - 2026-09-06

### Added

- **`TaskView.address` and `TaskView.path`** — a task's whole command-line
  spelling from a hook. `address` is the dotted form (`forge.dev.up`), `path`
  its segments (`("forge", "dev", "up")`), and `name` still spells the leaf
  alone. `group` only ever named the immediate parent, so a nested task had
  no public full spelling and consumers reconstructed one by walking
  `Tasks._root`.

- **`TaskView.mounted_from`** — which provider a task came from: the
  `footman.tasks` entry-point identity for `plugin()`, the module name for
  `include()`, and `None` for a task your own tasks file writes, which is the
  honest answer rather than a hole (the project owns it). Already public as
  `registry.mounted_from()`; it was missing from the view, so a hook reached
  it through `t.fn`.

- **Manifest task rows carry `mounted_from`**, so `fm --json --list` answers
  ownership without a hook. A provider identity, not a source path: a path
  answers only for a Python function, makes a reader infer ownership by
  matching prefixes, and puts absolute home paths into output people paste
  and commit. Group nodes carry the field too, where it answers something
  stronger — this *whole* group is one provider's, true only when the mount
  landed it on a free name. A mount composing into a group that already
  exists leaves that group shared, so it claims nothing while every task
  under it still answers exactly. Read ownership from the task rows.
  Manifest schema 9 → 10; caches rebuild on first use.

### Changed

- **`Tasks.get`, `tasks[…]` and `in tasks` look tasks up by address, not by
  leaf name.** `inv.tasks["build"]` finds a top-level `build`; a nested one is
  `inv.tasks["docs.build"]`. Addresses are unique by construction — a name is
  a task or a group at each level, never both — so a lookup now either finds
  one task or finds none, where a bare leaf could match several and quietly
  returned whichever the walk reached first. To search by leaf, iterate:
  `[t for t in inv.tasks if t.name == "build"]`, where two answers are visible
  rather than silently narrowed to one.

  A runnable group answers at its bare address: `inv.tasks["lint"]` is
  `inv.tasks["lint.default"]`, since the bare group name is that action's
  other spelling on the command line, and lookup would otherwise be the one
  place that was not true.

### Fixed

- **Discovery no longer scales with how much an ancestor directory
  happens to hold.** Every level of the upward walk *listed* the
  directory to check for `tasks.py`, `pyproject.toml` and the rest —
  one `listdir` beating one `stat` per marker, which holds while a
  directory is small and inverts hard when it is not: the cost is
  O(entries) against O(markers). An ancestor with a few thousand files
  cost about 0.3 s **per level**, so a `TAB` in a directory under a
  crowded parent could take over a second. (Found via macOS's per-user
  `$TMPDIR`, which had reached 8,747 entries and made footman's own
  bare-directory completion test fail.)

  The walk now probes first and lists only to confirm a hit. A miss is
  one `stat` whatever the directory holds; a hit — rare, and the only
  place the spelling matters — pays one listing to prove itself. Measured
  on that directory: `find_repo_root` 0.32 s → 0.1 ms, `task_files`
  0.26 s → under 0.1 ms.

  **The case-exactness guarantee is unchanged**: a `Tasks.py` is still
  not a tasks file, on any platform, because a project accepted that way
  stops working the day it reaches Linux. The probe finds the entry, the
  confirmation rejects the spelling. A new test pins the mechanism rather
  than a clock, so this cannot regress on a machine whose temp directory
  happens to be tidy.

- **A wrong-case tasks file is now named instead of ignored in silence.**
  There is one spelling, `tasks.py`, and a `Tasks.py` was passed over
  without a word — on macOS and Windows because loading a file that opens
  under either name ships a project that dies on the first Linux box, and
  on Linux because it is simply a different file. Either way the report
  was a bare "no tasks file found", which reads like a lie to someone
  looking straight at the file, and named nothing to fix:

  ```
  fm: no tasks file found (looked for tasks.py). /w/Tasks.py is there, but
  it is not tasks.py: the name is case-sensitive, so a project that relies
  on the other spelling stops working the moment it moves to a filesystem
  that tells the two apart. Rename it to tasks.py.
  ```

  The refusal drops its "create one or pass `-f`" tail when it says this,
  because the fix is a rename and there is nothing to create. `--list`
  and `--help` say it too — they are where someone goes to ask why their
  file is not showing up. The check runs only once discovery has already
  come up empty, and looks in two directories (the cwd and the cascade's
  top), so no successful run pays for it.

## [0.50.0] - 2026-09-02

### Added

- **`footman.prog()` and `footman.dist()`** — which CLI is running, for
  code that generates text calling the CLI back. A plugin emitting a CI
  workflow or a README line has to name the command its reader will
  actually type: a branded runner writing `fm check` sends them to a
  command they do not have. `prog()` is that command, `dist()` the
  package that ships it — and `dist()` answers `None` when the brand
  never declared one, because `App(dist=…)` is opt-in and saying
  `footman` there would name the wrong package. (The uv handoffs keep
  their own reading, which falls back deliberately: the lock rule needs
  *some* package to look for.)

  Inside a task body `ctx.prog` remains the better reach — the
  invocation's own answer rather than process state, and what footman's
  own taskdocs plugin uses. These exist for the code that never receives
  a context: a plugin building its tree at mount time, a helper called
  from anywhere.

### Documentation

- **`ctx` is documented as the rare case it now is.** With `prog()` and
  `project_root()` public, every fact a task ordinarily reaches for has a
  free function that finds the running task by itself — `run()`, `cwd()`,
  `given()`, `passthrough()`, `tty()`, `attended()`, `colored()`,
  `cache_dir()`, `data_dir()`. Declaring a `ctx: Context` parameter buys
  exactly one thing they do not offer: the shape of the *invocation*
  (`dry_run`, `verbose`, `quiet`, `jobs`, `sequential`, `invoked_dir`),
  which most tasks should not branch on. The rest of the object is
  footman's own machinery. The tools page says so and demonstrates the
  one real case; the cookbook's `--affected` recipe, which declared `ctx`
  only to read `ctx.root_dir`, now calls `project_root()` and declares
  nothing.
- **`python -m <yourpkg>` for a branded CLI.** The custom-CLI page now
  shows the three-line `__main__.py` and says why it matters: anything
  that has to *wrap* the CLI in another process reaches for the module
  form — `coverage run -m acme check`, a profiler, a sandbox — and
  `python -m footman` runs **stock footman**, silently losing a brand's
  built-ins, `[tool.acme]` table and `ACME_*` variables, because it is a
  different runner wearing the same import.

### Fixed

- **The home page advertises the current release again.** Its "latest
  release" block is lifted from the newest CHANGELOG section, and the
  pattern doing the lifting required an em dash between version and date.
  The changelog moved to the hyphen [Keep a
  Changelog](https://keepachangelog.com/) specifies at 0.42.0, so the
  search skipped every heading since and matched the newest one that
  still had an em dash: the home page has been advertising **0.41.0**,
  nine releases back, since mid-August.

  It failed in the worst available way — finding an *older* match rather
  than none, so there was nothing missing to notice — and the unit test
  passed throughout, because its fixture used the generator's own
  spelling. A synthetic changelog cannot catch the generator disagreeing
  with the real one, so the new guard reads `CHANGELOG.md` itself and
  asserts the lifted version is the newest one. Both separators parse
  now, which keeps the archive readable and cannot silently pick the
  wrong entry again.

## [0.49.1] - 2026-09-02

### Fixed

- **`fm self.uninstall` really removes the completion hooks now.** It
  claimed to, and did not: the hooks live at
  `<data home>/<command>/completion.*` — keyed by the command you type —
  while uninstall cleared `data_dir()`, which is keyed by the config name.
  `~/.local/share/fm/completion.zsh` survived beside
  `~/.local/share/footman/`, and its line in your shell's rc survived with
  it, sourcing a script that was gone: an error on every new shell. It
  now uninstalls each hook properly, script and rc line together, and only
  for shells that actually have one — a script on disk is the evidence an
  rc line was written, so no shell's rc is edited on spec.
  The path is spelled once now (`_shellcomp.hook_path`) rather than three
  times, which retires the comment warning that install and uninstall had
  to be kept in step by hand.
- **A branded CLI's generated help no longer documents footman's config
  table.** `acme docs.config` rendered the notes key as
  `[tool.footman.notes]`, and twelve other strings named footman's
  product name, command or environment prefix in a runner that is not
  footman. This was never cosmetic: a runner reads
  `[tool.<its own stem>]` and nothing else, so telling an `acme` user to
  write `[tool.footman.notes]` documents a table `acme` never opens —
  settings someone writes that silently do nothing, which is the failure
  mode footman refuses everywhere else.
  The config and notes tables now fill `{prog}`, `{config}` and `{env}`
  from the invoking brand, exactly as the globals table has always filled
  `{prog}`; the runtime refusals (`plugins`, `docs_url`, the notes and
  `[builtins]` validators, the banned-notes boundary failure) interpolate
  the brand directly. Stock footman's own output is unchanged, because
  the substitution is per brand rather than a rename — and prose takes
  the *product* word (`footman`, the longer name pinned against the
  two-letter command) while only real command lines take `fm`.
  Entry-point names — `footman.tasks`, `footman.builtin`, `footman.new`,
  `footman.self` — stay literal: they are identifiers every brand's own
  providers advertise under, and substituting them would break real
  mounts. A branding test renders both tables under a brand and fails on
  any bare `footman`/`fm` with those identifiers set aside, so the next
  key someone adds cannot quietly reintroduce this.

### Documentation

- **What footman reserves inside its own directories is written down.**
  `data_dir()` and `config_dir()` are public, so a plugin should keep its
  state in the runner's home rather than inventing one — but until now the
  only way to know which names were already taken was to read the source.
  The configuration page lists them per directory, names the convention
  that cannot collide (one subfolder called after your distribution), and
  states the two things worth knowing before settling in: the cache is
  swept by age, and `self.uninstall` removes the cache and data
  directories whole, plugin state included. It also corrects the data row,
  which pointed at the data *home* rather than the data directory, and
  says plainly that the completion hooks are the one thing living outside
  all three.

## [0.49.0] - 2026-09-02

### Added

- **`fm self.*` — the runner manages its own installation.** Four verbs
  and a question, replacing the `--self-install` flag: `self.install`
  puts the CLI on your PATH or brings it up to date, `self.add <pkg>…`
  installs packages beside it (and, under the default discovery mode,
  records the built-in tasks they advertise, so their commands answer
  straight away), `self.remove <pkg>…` drops them, `self.uninstall`
  takes the whole thing off, and `self.path` says where the runner keeps
  things. Once there was more than one verb, a set of global flags was
  the wrong container: these are commands, with their own options, help,
  completion and `--json`.
  **`add` and `remove` are additive**, which the flag never could be: uv
  rewrites a tool environment from the requirements it is *given*, so an
  upgrade that forgot your extras would silently drop them. Both read
  uv's own receipt first and hand the whole set back, and `remove`
  completes from exactly what is there to drop — never the runner's own
  distribution or the bundled `uv`, and a name that was never added is
  refused rather than passed to uv. `self.uninstall` clears footman's
  leavings (cache, data, completion hooks) but **keeps your config**
  unless you ask for `--purge`.
  `self.path` prints one bare line when named (`DIR=$(fm self.path
  data)`), every location when not, and returns the whole mapping under
  `--json` so a script that wants three of them makes one call.
- **Four more places a task can ask about.** `config_dir()`,
  `config_file()`, `user_tasks_file()` and `project_root()` join
  `cache_dir()` and `data_dir()` in the public API — `fm self.path` is
  the same set from the command line, and a task never has to shell out
  to learn where its own config lives.
- **The `footman.builtin` entry-point group.** A package advertises there
  to say "I am meant to be mounted as a built-in", not merely mountable.
  Discovery reads it at `self.*` time; whether a candidate is actually
  mounted stays the machine owner's call through
  `builtins.discovery_mode`, so a package cannot mount itself simply by
  being installed — which matters because in a project environment every
  dependency shares one metadata space.
- **`prompt(type=…)` — the parameter type language as the prompt's
  validator.** The answer runs through the same coercion pipeline a flag
  does — `int`, `Path`, an enum, `Literal[…]`, a union, or `Annotated[…]`
  carrying `between()` / `check()` / `exists` — and a bad answer is
  taught and re-asked, exactly as `ask()` treats a typed parameter. An
  empty answer takes `default`, unattended runs take it the same way,
  and the return is typed (`type=int` returns `int`). One value per
  prompt: collections and the markers that decide how a *parameter*
  gets filled (`ask()`, `env()`, `suggest()`, …) are refused by name —
  declare a parameter, or use `select()`. `secret=True` stays text-only:
  a typed secret is spelled `ask(secret=True)` on a parameter.
- **Three readers answer "who is on the other end?"** — plain calls, no
  `ctx` parameter, honest anywhere. `attended()` is the input side: stdin
  is a real terminal and the run was not declared unattended
  (`--no-input`, `--dry-run` — `--yes` deliberately does not count, since
  it auto-answers confirms but forbids nothing), so a task can skip an
  optional wizard outright rather than prompt-with-defaults through it.
  `tty()` is the output side with the colour policy left out: a live
  terminal is watching (`NO_COLOR` undresses output, it does not empty
  the chair), the right gate for pagers and live displays. `colored()`
  is the dressing: the same `never`/`always`/`auto` cascade footman's own
  chrome follows, `--color=always` included. A bare `sys.stdout.isatty()`
  inside a task lies (stdout is a capture buffer under parallelism);
  these read the real streams and the run's declared intent. `Context`
  additionally carries the new `terminal` stamp (`tty` minus colour
  policy), which the scheduler sets per task.

### Removed

- **`--self-install`.** It is `fm self.install` now: one spelling, and
  the options the flag was about to grow have a proper home. Being a
  task rather than a global is also why built-ins had to join the
  cascade — a self-management command that vanished the moment you
  stepped into a project would be useless exactly where you reach for it.

### Changed

- **`builtin` becomes the `[builtins]` table, and `builtin = true` is
  gone.** The single key made footman and you write the same list: the
  moment a tool wrote a name into it, neither side could tell its entries
  from yours. There are now three sources, mounted in that order and
  deduplicated — the runner's own set (declared in code, so it can never
  go stale against an upgrade), the list discovery wrote, and
  `builtins.user`, which only you write and footman only reads.
  `builtins.discovery_mode` says which contribute: `auto` (the default),
  `manual`, `internal`, or `none`. `builtins.user` is honoured in every
  one of them, so `none` is not an off switch — it means *nothing
  automatic, only exactly what I named*, and naming `footman.self` brings
  the runner's own commands back.
  **The discovered list lives in the data directory**, not in your config
  file, so "do not hand-edit this" is true by construction: footman
  replaces one small file it owns rather than rewriting a table inside a
  file you also write — which, with no TOML writer in the standard
  library, would have meant losing your comments.
  `builtin = true` shipped in 0.48.0 and goes out again here. It resolved
  its list on **every invocation**, and the enumeration it needed costs
  ~20 ms of a ~75 ms run, forever, for a set that changes when you install
  something. That work now happens once, in `fm self.*`, which writes the
  result down. It also swept in the runner's *own* entry points — on a
  stock install it would have mounted footman's docs, env-file and profile
  providers, two of which exist to contribute global options rather than
  task trees.
  Migration: `builtin = ["x"]` → `[builtins]` with `user = ["x"]`;
  `builtin = true` → let `fm self.add` write the list, or name the
  packages under `user`.
- **Built-in tasks are part of the cascade.** They used to be mounted
  only where discovery found no project, so the moment a tasks file
  turned up they were not there at all — a runner's own commands
  vanished at the doorstep of every project, and reaching one meant
  `plugin("…")` in that project's tasks file. The built-in set is now
  the cascade's **outermost rung**: under the user tasks file, under the
  project's own, reachable wherever you stand, with everything nearer
  shadowing it by name exactly as the cascade already resolves its own
  rungs. A project that wants a set placed somewhere of its own still
  mounts it deliberately, and `-f` still means total control — one file,
  no cascade, no base.
  `expose` is what makes this safe in both directions, and why it had to
  land first: a package's tasks are `project_only` until one opts out, so
  nothing appears outside a project that did not say it belonged there,
  and `global_only` keeps a task that only makes sense *before* a project
  out of every project. The "it's built in, mount it" remedy is gone —
  there is nothing left to teach, because the task is simply there.
  Measured on footman's own repo: `fm --list` 80 ms before, 76 ms after —
  the extra rung is noise, because a built-in provider imports like any
  other tasks file.
- **`fm new` says `global_only`.** Writing a starter tasks file is what
  you do *before* a project exists, and it used to rely on simply not
  being mounted inside one — which stopped being true the moment
  built-ins joined the cascade. It now says so itself, so the answer
  travels with the task rather than with the rung: inside a project it is
  unlisted and refused by name, even where a tasks file mounted it
  deliberately. It also reads its own success — the file it writes is
  what makes the directory a project, so a second `fm new` steps aside
  instead of reaching the overwrite guard.
- **`needs_project` becomes `expose`, and gains the answer it was
  missing.** The boolean could say "this needs a project" and nothing
  else, because it was asked in only one place — the built-in set was
  mounted exclusively where discovery found none, so both of its answers
  included being *inside* a project. `expose` says where a task is
  offered outright: `"always"`, `"project_only"` (the old
  `needs_project=True`), or `"global_only"` — a task that only makes
  sense *before* a project exists, like creating one, which nothing
  could express before. Whichever side a task does not belong on, it is
  unlisted, uncompleted and refused **by name** with the direction it
  was withheld from, never a "no task named".
  `@task(expose=…)` and `group(…, expose=…)` take it, and `@expose(…)`
  is the decorator spelling for authors who keep their gates above the
  signature. It stays tri-state like `hidden` — unset inherits the
  enclosing group — and the **rungs keep their opposite defaults**: a
  package's tasks are `project_only` until one says otherwise, a
  personal tasks file rides everywhere. A value that is not one of the
  three is refused at registration, where a typo would otherwise read as
  silence and put a task somewhere its author meant to keep it out of.
  Migration is mechanical: `needs_project=True` → `expose="project_only"`,
  `needs_project=False` → `expose="always"`. Manifest schema 9.

### Documentation

- **The `ask()` + `suggest()` pairing is now on the input page.** A strict
  completer's values become a numbered up-front menu when the flag is
  missing (`Many[…]` multi-selects; `strict=False` demotes the values to a
  hint beside free text) — built and tested since the completers landed,
  but never documented, so the one interactive shape most tasks want
  (dynamic choices without `interactive=True`) looked missing. The
  dynamic-completion section links across.

## [0.48.0] - 2026-09-01

### Added

- **Tasks that live outside every project, without a branded CLI.** The
  built-in ladder — mount `footman.tasks` entry points as the base of the
  tree wherever no project answers — was reachable only by shipping an
  `App(builtin=…)`. A user-level `builtin` key now opens it to any
  runner: `builtin = ["acme_devkit"]` names the entry points, and
  `builtin = true` mounts every one installed alongside the runner, which
  is honest because putting a package there is already a deliberate act.
  Inside a project nothing changes — the cascade wins outright — and the
  rest of the ladder is the one that already existed: `needs_project`
  still governs what a package exposes out there (unmarked tasks refuse
  by name rather than going missing), and `fm --plugins` distinguishes
  `built in` from `built in (your config)`. User-level only, like
  `cascade`: what a machine offers outside every project is the machine
  owner's business, and a project that wants the same tasks mounts them
  in its own tasks file. A name that will not mount, or a value that is
  neither a list nor `true`, is refused by name. Closes #536.
- **Task names link to their docs.** A `docs_url` template under
  `[tool.footman]` turns every task and group name in `--list`, `--tree`
  and the `--help` pages into a terminal hyperlink (OSC 8) to its own
  documentation, and puts a `docs_url` field on each task's `--json` row
  (additive to schema 1) for readers that cannot click. `{path}` is the
  slash-joined task address, matching the per-task pages `docs.site`
  writes; `{slug}` is the dash-joined one, matching the one-page export's
  heading anchors — an unknown placeholder is refused by name. Links ride
  the colour switch, so piped output stays plain, and each `--help` page
  also prints the URL as visible text, so a terminal without hyperlink
  support still shows something to copy.

### Fixed

- **A user-level edit reaches completion on the next press, not ten
  minutes later.** The completion cache refreshed on an age clock alone,
  so editing the user config or `~/.config/footman/tasks.py` — the two
  files you change expressly to alter what `fm` offers — could leave
  <kbd>Tab</kbd> answering the old menu for a whole `max_age` (10 minutes
  by default), which is exactly the window in which you are pressing it
  to see whether the change worked. Every manifest now records what those
  files looked like when it was built, and a press that sees a difference
  rebuilds behind itself: two `stat` calls, made after the candidates are
  already written, so the keystroke waits on nothing and the hot path
  keeps its one file read. Creating either file counts as a change, not
  only editing one. `completion.max_age = "off"` still means no
  background rebuilds at all.

### Changed

- **`global-unread` speaks at `info`, and teaches the idiom.** The kind
  shipped at `trace` on the strength of its old `-v` gate — its "never
  read *this run*" evidence would nag a task that reads an option only
  inside a branch. The idiomatic fix dissolves that: read a declared
  option unconditionally and branch on the *value* (the read is a
  cheap, side-effect-free lookup), and "never read this run" only ever
  means a genuinely stale declaration. The default is now `info` and
  the note's text carries the idiom; a deliberately lazy reader
  reclassifies `global-unread:<option>` in `[tool.footman.notes]`.

## [0.47.0] - 2026-09-01

### Added

- **Notes have levels, and a project can make them walls.** Every note
  footman says about how a task treats the process — raw `subprocess`
  spawns, `os.environ` writes, foreign `getcwd` reads and their siblings
  — now carries a level: `trace` (recorded, printed under `-v` alone),
  `info`, `warning`, or `error`, with the level as the stderr prefix and
  the offending site (`file:line`, the first frame outside footman)
  appended. A `[tool.footman.notes]` table reclassifies any kind — keys
  are `[task/]kind` patterns, either side `*`, most specific first — so
  a known-harmless third-party instance gets a pinned entry
  (`"environ-write:JAVA_HOME" = "info"`) that survives raising the
  blanket to `"*" = "error"`. An `error`-classified note fails the task
  **at its boundary**, after the body ran to completion, listing every
  banned note with its site — all issues in one run, never
  fix-one-and-see-the-next — which is what makes a note enforceable in
  CI rather than a line an agent scrolls past. An unknown kind in the
  table is refused by name. The kinds, their defaults, and the config
  grammar live on the new **Notes & levels** docs page, whose kind table
  generates from the runtime's own registry.
- **Notes ride the machine surfaces.** Every fired note — `trace`
  included, print-gated or not — lands on its task's row in the `--json`
  envelope as `{kind, level, site, text}` (additive to schema 1) and on
  `TaskResult.notes` for `Runner`-driven tests.

### Changed

- **Notes name their instance, and dedup follows.** A kind's tail names
  what happened, never the task: `environ-write:<VARIABLE>` (one note
  per variable a task sets — every issue surfaces in one run, and a
  whitelist entry cannot hide the next variable),
  `popen-inject:<program>` (the spawned executable's basename),
  `lane-wait:<lane>` (`serial`, `exclusive`, `console`, or a named
  lane — previously the waiting task's name, redundant with the note's
  own task attribution). The plugin hook-return advisory joins the
  channel as `hook-return:<plugin>` — levelled, dedupped and recorded
  like every other kind, where it used to print per occurrence outside
  the system. The unread-`uses=` advisory keeps its quiet: its kind
  defaults to `trace`, so `-v` still owns it — but the record now rides
  the envelope either way, and a project may promote it.
- **The starting environment is part of a task's work identity.** The
  run's once-cell used to key shared work on (task, arguments, what the
  caller supplied) alone, so a caller that wrote its environment before a
  body call could be answered by an execution that never saw the write —
  a wrong cache hit with no error, decided by whoever claimed the cell
  first. The environment a callee would start from now joins the key (as
  a digest: order-blind, case-blind where the OS is). Nothing changes in
  the common case — every scheduled task starts from the pinned snapshot
  and an untouched caller holds an equal copy, so `pre=[build]` plus
  `build()` in the body is still one build — and the cell splits exactly
  when someone wrote the environment on purpose: each request then runs
  under the environment it actually asked for, each with its own row in
  the report, and two callers that made the same writes still share.

### Fixed

- **A stale project environment retries through `uv run` instead of
  failing the import.** With the project's venv activated but out of date
  (a dependency locked after the last sync), a bare `fm` used to die on
  the raw `ModuleNotFoundError` — the handoff's "already home" answer
  trusted a venv that no longer matched its lockfile. A failed import in
  a project whose lockfile pins the runner now hands the invocation to
  `uv run --project`, which syncs and retries; a failure after that is
  real and surfaces unchanged. Where the retry cannot run — no uv, or the
  `uv = false` / `FOOTMAN_NO_UV` opt-outs — the refusal ends with the fix
  instead: run `uv sync`. An embedded `Runner.invoke` never retries: the
  host process's environment is the host's business.
- **Completion children build in the project's environment.** The
  detached refresh child and the dynamic-completer child imported your
  tasks under whichever interpreter answered the TAB — a
  globally-installed `fm` in a project directory built the manifest from
  its *own* environment, where the project's plugins don't exist, and
  completion silently described a world no `uv sync` could ever mend.
  The children now ask the lock rule before the script rule, exactly as
  a run does: in a pinned project they re-exec into the project's
  `.venv`, the refresh child heals a stale venv on the way from uv's
  cache alone (strictly offline — a keystroke never downloads), and a
  broken-import marker in a pinned project names the fix (`run uv sync`)
  instead of leaving a bare `ModuleNotFoundError` as the whole story.

## [0.46.0] - 2026-08-21

### Added

- **`recording(answers=…)` scripts what the commands answer.** A plain
  recording fakes every call as a blank success; a table of answers —
  keyed by command prefix, longest match first — makes a recorded call
  return canned stdout, an exit code, or a full `Result`, raise an
  exception (the missing-binary case), or answer a list of those in order
  across repeated matches, refusing by name once the list runs dry. A
  non-zero answer takes the real failing lane (`RunFailed` unless
  `nofail=True`), a `pre_record` reviewer runs on the scripted draft, and
  a matched call is answered even when it opted out of the record with
  `recorded=False` — a value read is exactly what a test wants to script.
  Unmatched calls keep the blank success, so existing recordings notice
  nothing. `Runner.invoke(answers=…)` takes the same table and implies
  `--dry-run`, so a CLI drives end to end against a scripted world.
  toolroom's `answers()` is the same grammar at the bridge's own seam and
  still wins when both are nested.
- **A recorded step keeps the `env` and `cwd` it would have run with.**
  `Result.env` and `Result.cwd` are set on a recorded step — the task's
  environment or the call's own `env=`, the resolved directory or the
  call's `cwd=`/`rel=` — and `None` on a record that actually ran, so a
  live environment (which may hold secrets) never travels into `--json`.

## [0.45.0] - 2026-08-21

### Added

- **`--self-install` puts the CLI on your PATH, or brings it up to date.**
  One command, `uv tool install --upgrade <dist> --with uv`: it installs
  when nothing is there and moves an existing install to the latest
  release, bundling uv into the tool environment so the handoffs work
  where PATH has none. Always the latest release — a project's pinned
  copy (`uv run fm --self-install`) installs the global `fm` at latest,
  never the version it is itself running. A branded CLI gets the same
  flag from its `dist=`; without one it refuses by name rather than
  guess a package. Nothing may follow the flag — it takes the whole
  invocation — and `--help` still wins over it.

## [0.44.0] - 2026-08-21

### Documentation

- **The testing page names the other door.** `recording()` captures
  commands but answers every one with a blank success; tests that need
  canned output or an injected failure on a bridge call are pointed at
  `toolroom.testing.answers()`, with the nesting rule stated (inside a
  recording, `answers()` wins and the record sees nothing). The tools
  page carries the matching sentence at the seam description.
- **Snippet sources no longer publish as orphan pages.** The generated
  key table, globals table, task-page example and latest-changes blurb
  are included into real pages, yet also shipped as standalone pages in
  the site and its search index. They now regenerate outside `docs/`, so
  only what a page actually includes reaches the reader; the errors
  reference (a nav page) and the terminal shots stay where they were.
- **Where footman writes.** The configuration page names the three
  user-level directories (cache, data, config), what lives in each, the
  Windows spellings, and the variables that move them. The layout itself —
  XDG paths on every platform — is deliberate. Also mends the environment
  table, which a stray paragraph had split in two, and corrects the
  `FOOTMAN_DATA_DIR` description.

### Fixed

- **Markers inside a container element work.** A marker on a collection's
  element type — `list[Annotated[int, between(1, 5)]]` — was silently
  dropped: the annotated element reached the manifest unpeeled, read as
  "not a usable type", and the values passed through as text. The element
  now peels exactly as a dict's value type always did: its bounds, checks,
  path requirements, completer and `nosplit` apply per element, and an
  outer marker on the whole collection wins when both are present.
- **A grouped positional checks each value against its own slot.** A
  NamedTuple/tuple shape taken positionally validated every token against
  the first field's type — `tag 3 hot` against `(copies: int, text: str)`
  was refused claiming "copies expects an integer (got 'hot')", and a bad
  value that happened to fit the first slot escaped the eager refusal to
  fail later, at binding. The stream index now rides into validation
  exactly as it always did on the option path, so a heterogeneous line
  parses and a wrong value is refused naming *its* field.
- **An explicit `--config` no longer overwrites the plain completion
  cache.** A `--config=<file>` run reshapes the task tree, and its
  manifest used to land on the cwd's own completion cache — after which a
  plain TAB offered tasks the plain run refuses. Such a run now keys its
  manifest by `(cwd, config file)`, exactly as `-f` does, and completion
  resolves a typed `--config=` to that same key.
- **The background completion refresh honours the `cascade` setting.**
  The detached rebuild always walked to the repo root, so with
  `cascade = "none"` (or `"filesystem"`) TAB could offer tasks the runner
  then refuses. The child now bounds its walk exactly as the run does.
- **`sys.exit()` at import time is a taught error.** A tasks file that
  exits while being imported used to kill the whole invocation with its
  raw exit code and no words; it now gets the standard broken-file
  refusal, naming the file and the fix.
- **A non-regular `--config` file is refused instead of read.** Pointing
  `--config` at a FIFO or a device used to block forever on the read;
  config is a regular file or it is nothing.
- **Choice-typed options no longer print their value list twice** in
  `--help`: the set already lives in the option's own spelling
  (`--mode={dev|prod}`), so the description now carries only what the
  spelling can't — the default, a doc line, or `(dynamic)`.

### Changed

- **`fm --plugins` says what to do with an unmounted plugin.** The
  listing used to stop at `(not mounted)`; it now ends with the move:
  mount one from your tasks file with `plugin("<name>")`.
- **The errors reference page got readable headings.** Sections are
  titled by each module's own one-line summary instead of its private
  filename.
- Two dead functions no longer ship in the wheel.

## [0.43.0] - 2026-08-20

### Added

- **`@task(timeout=…)` — a deadline for one task.** The fail-fast event,
  scoped: past the deadline no new work starts, the task's in-flight
  subprocess trees are terminated, and generator steps unwind at their
  next checkpoint. A `run()` inside the task inherits whatever is left,
  so a call with no bound of its own is still bounded, and one with a
  longer bound cannot outlive the task. Answers 124, the shell's
  convention, and raises `TimedOut` — a `Failed`, so `except
  footman.Failed:` keeps catching it.

  It means **cancelled at the first checkpoint or subprocess boundary
  past the deadline**, not a hard stop: a body of straight-line Python
  has neither and runs to its own end. That case is reported rather than
  hidden, and the record says which happened: `after_deadline` is
  `stopped` when footman issued a stop before the body returned, or
  `completed` when no stop was issued and the body finished on its own,
  late. "Timed out" never means "did not run". The field is an open set,
  like `state` — tolerate values you don't know.

  A late body still fails. Honouring a result that arrived after the
  deadline would make the verdict depend on a race between the body and
  the kill, and flaky is worse than strict for something used as a gate —
  but the receipt tells the whole truth, naming what the body actually
  decided and that the deadline governs.

- **`@task(retries=N)` — attempt it again, and record every attempt.**
  `retries=2` means up to three runs. Every attempt is a real row with
  its own timing, output and audit; nothing is merged and nothing is
  hidden, because each attempt *is* a record. The earlier ones carry the
  new `retried` state — additive, and the `state` set was always
  documented as open — and the terminal attempt carries the outcome.

  A retriable failure is **not yet a failure**: it does not trigger
  fail-fast, does not block a dependent, and never reaches the exit
  code. Finality moves from "a task failed" to "a task failed with no
  attempts left". A *different* task's terminal failure still wins —
  fail-fast means no new work, and an unstarted attempt is new work.

  Retry is your choice, with no theory about what deserves it: a
  deliberate `fail()` retries exactly like a crash. The one exception is
  not about failure kinds at all — only a `stopped` timeout is retried,
  because a body that merely finished late has nothing transient to
  retry: it outran the deadline once and will again, and another attempt
  repeats work that already happened. `pre=` runs once,
  and so does every gate that guards an attempt rather than performing
  it — availability, `needs_project`, and the confirm prompt, which is
  never re-asked. All attempts count as one unit on the progress bar.
  Your task re-runs from the top, so idempotence is yours to arrange.

  `retries` is visible in the manifest, deliberately: a caller that can
  see a task already retries will not wrap it in a retry of its own. Note
  that a task retry is **outer** to whatever a tool does internally —
  with `[fetch] backend = "curl"` (which already passes `--retry 2`),
  adding `@task(retries=2)` can mean as many as six attempts.

## [0.42.0] - 2026-08-19

### Added

- **A broken tasks file answers <kbd>Tab</kbd> with why.** A tasks file
  that failed to import used to make completion silently answer nothing —
  and pay the full cold-build wait doing it, on every keystroke. The
  rebuild now leaves the import error behind as a marker, the press
  answers instantly (exit 103, one line on stderr), and each shell shows
  it as loudly as it can: zsh a message line, fish/nushell/PowerShell the
  typed word re-offered with the error beside it, bash silent but no
  longer offering filenames in the gap. The marker ages out in seconds
  once the file imports again, and a hook from an older install shows
  exactly the silence it always did.
- **Stack dumps for a run that has stopped moving.** `Ctrl-\` (`SIGQUIT`)
  writes every thread's stack to stderr and lets the run carry on, so
  pressing it twice and watching whether the frames moved is how you tell a
  deadlock from slow progress. Nobody is at a keyboard when CI hangs, and
  Windows has no `SIGQUIT`, so `FOOTMAN_STACKS_AFTER=30` arms the same dump
  on a timer that repeats every 30 seconds. Both write to stderr, so a
  `--json` run keeps its document.

### Changed

- **A warm <kbd>Tab</kbd> imports less than half of what it did.** The
  completion hot path's own overhead drops from ~12.5 ms to ~5.3 ms above
  interpreter startup: pathlib (which drags `glob` and `re` along),
  `subprocess`, and `typing` no longer load on a warm press — the cache
  keys are computed by an `os.path` string core that keys byte-identically
  to the Path spellings the rest of the framework keeps using, and the
  spawn paths import what they need when they run. What remains is the
  JSON parse, which is the job. A new invariant test pins the diet.
- **An enum speaks its token — the member name, projected — on every
  developer-visible surface.** `Level.LOW = 1` used to render as
  `--level={1|2}`: the values are payload, the names carry the meaning,
  and completion offering `1|2` might as well have offered nothing.
  Choices, help, completion, documents out, and work identity now all
  speak the member's **token** (`LOW` → `low`, `LOW_PRIORITY` →
  `low-priority` — the same projection parameter names use). Input stays
  wider than output, identically at every door: the token, any alias
  (Python's duplicate-value bindings, accepted but taught nowhere — a
  compat rename keeps old spellings working), and the value face (`2` on
  the command line; a JSON-typed `2` in a piped document, so foreign
  documents keep binding). A raw member NAME (`LOW`) was never a taught
  spelling and is no longer accepted — its one spelling is the token.
  **Breaking**: documents now carry tokens where they carried values
  (`"level": "high"`, not `2` — `IntEnum` included, moved off
  `json.dumps`' fast path by the same pre-walk that guards `Secret`);
  the manifest schema bumps to 7 with first-class
  `members` (token/value/aliases) beside `choices`; and an enum whose
  members spell ambiguously on any door (`A = 1; B = "1"` both spell
  `"1"` on argv) or project outside the token grammar is refused at
  declaration rather than adjudicated at runtime.
- **The fetch cache is a manifest naming an immutable data file.** Each URL
  keeps one small `<key>.json` that names a content-addressed
  `<key>-<digest>.bin` and carries its validators beside it. A fresh
  download lands under a fresh name and only the manifest is swapped — one
  uncontended replace of a small file no reader holds open — so a path
  `fetch()` returned never changes underneath its holder, two parallel cold
  fetches publish identical bytes under one name, and the Windows
  replace-under-reader failure has nothing left to happen to. The collector
  follows the layout: a live manifest pins its data at any age, an
  unreferenced data file is garbage immediately, and only downloads in
  flight — referenced by nothing yet — keep an age clock. Old-layout cache
  entries are simply re-downloaded on first use and swept by the age rule
  they always had.

### Fixed

- **A mistyped `completion.max_age` refuses on a run.** The one config
  key that parsed quietly to its default now teaches by name like every
  other key — on the execution path only: the refresh child keeps the
  quiet fallback, because a background rebuild must never crash and a
  keystroke is nobody's moment to learn about a config typo.
- **PowerShell no longer offers files as tasks.** When the resolver had
  no candidates, the pwsh hook yielded nothing and PowerShell's default
  completion stepped in — filesystem entries offered at the task-name
  position. The hook now re-offers the typed word itself, a visual no-op
  that keeps the line as it is (a bare <kbd>Tab</kbd> keeps PowerShell's
  default behaviour: an empty word has nothing to re-offer).
- **Built-in globals complete by the both-spellings rule.** Completing a
  value-taking built-in offered only its bare name — the value path
  (`--color=<TAB>`) stayed undiscoverable, while task options and plugin
  globals showed both spellings, and worse, value-required globals like
  `--where` were offered bare, a spelling the grammar refuses. The menu
  now speaks the grammar exactly: `--opt=` beside a bare mention only
  where a default backs it (the bare row naming this machine's resolved
  default), and `--opt=` alone where a value is required.
- **Help fits the terminal.** Seven lines of `fm --help` overflowed an
  80-column terminal and hard-wrapped mid-column, shearing the option
  table apart. The globals table and every parameter listing now wrap
  their details to the terminal's width with a hanging indent — the same
  two-band layout the task listings always drew — with the dimmed
  mechanics moving to their own lines when a row cannot share one.
- **Windows paints only where the console can.** Colour and the live
  status line gated on tty-ness alone, and a legacy Windows console shows
  raw `←[1m` escapes rather than bold. Every ANSI surface now asks the
  console for VT processing first — a no-op under Windows Terminal, an
  upgrade on a conhost that has it off, and a refusal exactly where the
  escapes would print as noise.
- **A tasks file's name is case-exact.** On a case-insensitive filesystem
  a `Tasks.py` was silently accepted (and `--where` then reported a path
  that does not exist on disk) — a project that stops working the day it
  reaches a Linux box. The cascade walk and the project/repo markers now
  match the on-disk spelling exactly, on every platform — and ask each
  directory once instead of once per marker while they're at it.
- **`--help` lists mounted plugin globals.** `--profile` and `--env-file`
  rode the same pre-task position as every global yet appeared on no help
  surface, while the troubleshooting page said `fm --help` lists them all.
  A `plugin globals` section now follows the built-in table, each row with
  its words, its mechanics, and where it came from.
- **Completion follows `-C`.** `fm -C=<dir> <TAB>` offered the invoking
  directory's tasks: the manifest key, the cascade walk, and the spawned
  builders all stood where the shell stood rather than where the run
  would. All of them now stand in the target directory — a `-f` value
  anchors there too — and a mistyped target answers nothing rather than
  someone else's tasks.
- **`-f=~/tasks.py` completion warms.** The hot path keyed the literal
  `~` (`resolve()` does not expand it) while the refresh child keyed the
  expansion, so the manifest the child wrote was never the one TAB read
  and every keystroke paid the full cold bound for silence. The key
  function expands now, so every keyer agrees.
- **Stock `fm`'s completion keys the real version.** The `--complete`
  dispatch configured the built-ins but not the brand version, so global
  mode kept two manifests — one keyed `""` for TAB, one keyed the real
  version for runs. One triple everywhere now.
- **A docstring may open with `Args:`.** A docstring whose first line is
  a section header made `Args:` the task's help text and silently dropped
  every parameter doc — the summary is empty now and the section parses
  whole, for Google, NumPy, Sphinx and `Returns:` openers alike.
- **`Note:` under a parameter is prose, not a parameter.** A Google-style
  continuation line reading `Note: rewrites in place` became a phantom
  parameter — its text vanished from the entry it documented and a raw
  warning fired on every invocation. A line indented deeper than its
  entry now continues it, whatever it says before a colon.
- **A single-dash misspelling refuses instead of acting.** `-version=1`
  printed the version and exited 0, and `-install-completion=zsh` would
  have edited a shell rc — the lenient pre-discovery walk carried the
  unknown token through, and the dash-stripping normalisation quietly made
  it drive the real global before the parse that refuses `-version` ever
  ran. An unknown spelling populates nothing now — and a long name missing
  one dash is taught its spelling (`-jobs` → "did you mean --jobs?")
  instead of the fused-short reading's comic "did you mean -j=obs?".
- **A union carrying `None` admits a JSON null at any width.**
  `int | str | None` refused `null` — the optional walk recognised
  exactly-two-member `T | None`, and a third member cost the union its
  nullability. The remainder keeps union shape and binds as before.
- **A deleted working directory is one taught line.** Running from a
  directory another process removed crashed as a raw `FileNotFoundError`
  traceback out of the first thing that needed a cwd. One line at the
  door now: cd to a real directory and try again.
- **A mistyped `tasks` config key refuses loudly.** `tasks = 123` behaved
  as if unset while every option-backed key refused its wrong TOML type
  by name; the config-only key now teaches the same way.
- **A completer value with a newline is one candidate.** The completion
  protocol is newline-delimited, so a multiline value split into two
  bogus candidates; the value's first line is the whole candidate.
- **`--profile`'s scratch directory cannot leak.** It was consumed only
  by the embed pass, so a run that mentioned the flag and stopped before
  the trace (a refusal, an interrupt) left one temp directory per
  mention, forever. An exit sweep backstops the consume path.
- **The published CLI reference describes computed defaults, never this
  machine's answers.** The docs exporter resolved `--jobs` and `--color` on
  the build runner, so the published reference said `3` and `never` — the
  runner's core count and CI's `NO_COLOR`, machine-specific values dressed
  as the product's. A computed default now renders as its phrase ("the
  machine's cores minus one, never below 2"), kept beside the grammar table
  so a new computed default cannot ship without one; `--help` still
  resolves live, which is right at a terminal — the reader is on the
  machine the number answers for.
- **`docs.cast` takes a `steady=` knob, and the docs build's retry uses
  it.** A recording on a loaded machine drops keystrokes because "output
  went quiet" is often a CPU-starved shell mid-redraw; a key typed into
  that gap diverges the whole interaction. `steady=` widens the
  quiet-detection windows without touching the hard caps, and the hero-cast
  retry re-records at triple patience instead of repeating the same take.
- **A broken annotation is an advisory line, not a Python `UserWarning`.**
  A mis-spelled marker, an unresolvable annotation, or a docstring
  documenting a parameter that does not exist printed as
  `…/site-packages/footman/_manifest.py:416: UserWarning:` with footman's
  own source line quoted underneath — the framework's internals above
  every `--help` and `--list`, pointing away from the user's file the
  message already names. The message was always the whole story; it now
  prints as one clean line on stderr, once per message, like every other
  advisory footman writes.
- **A sibling package leaves with its whole subtree.** The cascade's
  per-file isolation evicted a sibling package's `__init__` but not its
  submodules, so a nested tasks file's `import pkg.sub` re-imported `pkg`
  fresh from its own directory and then took the *stale* `pkg.sub` out of
  `sys.modules` — the other cascade level's copy, wearing the new package.
  Eviction is name-prefixed now: `pkg.*` leaves with `pkg`. Plain sibling
  modules and deeper installed packages behave exactly as before.
- **A project's `tasks` key renames the project's file, and stops there.**
  It used to steer the user-level rung too, so any project with a renamed
  tasks file made the walk look for a personal file the user never wrote —
  and the personal-tasks rung silently vanished. The user rung's name now
  comes from the user's own writing (the user-level config, which may
  legitimately rename it) or the brand default; an explicit `--config`
  stays total control over both.
- **A one-field record reads its field's declared type.** The `T(value)`
  spelling handed the whole raw token to the constructor, and a dataclass
  constructor validates nothing — `n: int` silently received the string
  `'abc'`, and even `'5'` arrived as a string. The token now coerces to the
  field's declared type first, refusing bad values with the field named;
  untyped constructors (`UUID`, `Decimal`, a user type that takes a string)
  expose no readable fields and keep the raw token exactly as before.
- **A branded CLI's script handoff re-enters the brand, not stock `fm`.**
  Handing an invocation to a tasks file's PEP 723 script environment
  re-exec'd `python -m footman` — the stock runner — so a branded child
  re-ran the handoff as the wrong brand and died on the mismatch refusal:
  the loop belt is brand-scoped, and the script block declares the brand's
  dist, not `footman`. The documented `dist=` handoff was broken for every
  brand. A brand now re-enters through its own console script's entry
  point, loaded by name inside the script environment; stock keeps the
  proven `-m footman`.
- **A provider mounted twice contributes its lifecycle once.** `include()`
  of one provider at two addresses registered its `@pre_tasks`/`@post_tasks`
  hooks once per mount — so they ran twice per run, silently, side effects
  and all. The tree mounts twice; the contribution now contributes once,
  deduplicated by identity at the mount engine.
- **A reader hanging up is a calm cut, not a crash.** `fm chatty | head`:
  once the body wrote past the pipe buffer after `head` exited, the next
  print raised EPIPE and footman dressed the reader's "enough" as a crash —
  a raw `BrokenPipeError` traceback, an "Exception ignored while flushing
  sys.stdout" on the way down, and exit 120. Now: one calm reason ("stdout
  closed by the reader — output cut short"), no traceback, and exit **141**
  — 128+SIGPIPE, what any SIGPIPE-default tool reports — so
  `set -o pipefail` still sees the cut while `| head` stays quiet.
- **A failure code the shell cannot carry still fails.** A task returning
  256 printed FAIL and exited **0**: POSIX keeps only the low byte of an
  exit status, so `fm deploy || rollback` never rolled back. A failure code
  outside 1–255 collapses to 1 at the exit boundary; in-range codes pass
  through untouched, and the real number still rides the receipt line and
  the `--json` row.
- **A stream nobody connected means discard, not a traceback.** Starting
  footman with fd 1 closed (a supervisor's redirect, a cron shell) hands
  Python `sys.stdout = None`, and every later touch — the scheduler's
  isatty, the uv handoff's flush, a body's print — was an AttributeError
  traceback. A `None` stdout or stderr is wired to devnull at the entry, so
  the run works, the output goes where a closed stream asked, and failures
  still reach whichever stream is real.
- **A constructor may reject a bad token however it likes.**
  `Decimal("abc")` raises `decimal.InvalidOperation` — an ArithmeticError,
  outside the coercion contract's ValueError/TypeError catch — so the raw
  exception escaped, class name and all, where UUID and every other custom
  type taught the value. A CLI token a constructor refuses is a bad value
  whatever it raises: it reports as one, with the original riding the
  exception chain for `-v`.
- **`docs.page` and `docs.site` teach a bad `--target`.** The resolver's
  message already named what it does know; it just escaped as a raw
  `ValueError:`. Both tasks now deliver it as the deliberate refusal it is —
  flag named, menu intact, no exception class.
- **Import-time chatter is never served as a completion candidate.** The
  dynamic-completer child's stdout is the candidate channel, and computing
  candidates starts by importing the tasks file — so a `print()` at module
  scope, in the tasks file or any module it pulls in, reached the shell as
  a completion the user could insert. The whole computation now runs muted,
  the policy the completer body already had applied to the import that
  precedes it, and only the finished candidates touch the real stream.
- **`fetch()`'s curl child no longer draws the "prefer `run()`" note.** In a
  managed parallel task the Popen injector attributed footman's own spawn to
  the task — advice to prefer `run()` aimed at a body that never spawned
  anything, and quite possibly one using `run()` everywhere else. Notes are
  teach-once per task and kind, so the false one also swallowed the note a
  real raw spawn in the same task would have earned. The spawn is marked as
  footman's own now, with `cwd` and the environment passed explicitly:
  every path on curl's command line was already absolute, and the env is
  snapshotted through the router first, so a managed task's curl keeps
  seeing the same proxy variables the urllib backend reads in-process — the
  injector's work, done by hand, minus the misattribution.
- **A yielding task body is refused, not reported ok.** `@task` on a
  generator function printed `ok (0.0s)` having run nothing: calling a
  generator function only builds the generator, and nothing ever pumped it.
  footman now refuses by name at the definition's `file:line`, the same way
  it refuses an `async def` body — and the refusal is a *reservation*, not a
  position: `yield` on a task is the shape the coming service form gives a
  meaning (run to readiness, hand back a value, tear down after), so the
  check sits exactly where that detection will live. Until then the message
  names what exists today: lift yielding work into a `@step`, or build and
  return the iterator from a non-yielding body — a body that *returns* a
  generator it made is real work and keeps its receipt. `async def`
  generators are refused the same way, on tasks and steps both: they are
  neither coroutines (`iscoroutine` misses them) nor generator functions
  (the step pump never takes them), so they were the coroutine refusal's
  missed sibling — `ok` for zero work through a second door.
- **A `Stdout[T]` document is UTF-8, whatever the console's encoding.** It
  was written through `sys.stdout`'s locale codec, which a run reconfigures
  to replace anything it cannot encode so a tool's stray glyph never crashes
  a run — so on a cp1252 console a document returning `"café naïve — 日本語"`
  reached the pipe as `caf?na?ve ? ???`, exit 0 and nothing on stderr. For
  `Stdout[str]` that is mangled prose; for a dict or a dataclass the damage
  is inside a machine payload, and the bytes are not valid UTF-8 for the
  reader on the other end. Text and JSON documents are encoded and written
  to the byte stream underneath, the same way the completion protocol and
  `stdin` are already pinned. `Stdout[bytes]` was always raw and stays so,
  and a terminal still gets the indented form.
- **A package you only `include()` *through* may be empty.** The shape is
  the ordinary one: constants in `devkit/__init__.py`, tasks in
  `devkit/tasks.py`, and a tasks file that does `from devkit import REGISTRY`
  before `include("devkit.tasks")`. That first import put `devkit` in
  `sys.modules`, and the already-imported path refused before it ever read
  the flag that exists for exactly this — so the whole CLI died, `fm --list`
  included, naming `devkit`, a module the caller never wrote, with advice
  about capturing tasks the package never had. An import that registered
  nothing is *empty*, not spent. The refusal now fires only when the module
  really does hold tasks it could not capture, and an empty module named
  directly gets the error that describes it: nothing here to mount.
- **One task mounted from two cascade levels is now refused.** A task runs
  in the directory of the tasks file that defined it, and that folder is
  recorded on the function — so mounting one provider at two addresses from
  two different directories (`include("shared", into="rootside")` at the
  root, `into="svcside"` in a subfolder) gave one function two answers. The
  last mount won, and the *other* address then ran somewhere its own tasks
  file never named, silently. The two addresses also collapsed into a single
  execution, so asking for both ran one. footman now refuses at load time,
  naming the mount to drop and the task that already holds the address.
  Shadowing is untouched: a nearer file may still mount the same task at the
  same address, which is the cascade working, and two providers that both
  include a common helper are fine because they agree about the folder.
- **A built-in task no longer inherits a folder from an earlier run.** The
  brand's built-ins are mounted from the same function objects every time,
  and the folder stamp stayed on them — so a host that ran once inside a
  project (the test `Runner`, the pytest fixtures, an embedder) left that
  project's directory behind, and the next `fm new` in an empty directory
  refused with "tasks.py already exists here" and wrote nothing.
- **A `footman.py` in the directory you press Tab in no longer runs.** The
  children the completion path detaches — the manifest rebuild, a dynamic
  completer, the cache collector — were spawned with `python -c …` and
  `python -m …`, both of which put the current directory at the head of
  `sys.path`. A file called `footman.py` sitting there answered the child's
  own `import footman` before the installed package did, so one Tab in a
  directory somebody else wrote executed its code, with no task ever run and
  nothing on screen. Those children carry `-P` now, and so do the re-execs
  into a script file's environment. Nothing legitimate loses the entry: a
  tasks file's own directory is put on the path deliberately, for the moment
  it is imported, so importing a helper beside it works as it always has.
- **A config file that is not UTF-8 no longer bricks every command.** One
  stray byte anywhere in any config footman reads — a `footman.toml`, or a
  `pyproject.toml` where the byte sits in `[project] description` and nowhere
  near `[tool.footman]` — escaped as a raw `UnicodeDecodeError` and exit 1,
  so `fm hello` died along with the listings for as long as that file sat
  between the repo root and your cwd. TOML's spec makes UTF-8 mandatory, so
  such a file is malformed by the format's own rule and takes the paths a
  malformed config already took: a discovered one is warned about and
  skipped, a file named with `--config` is exit 64. Both say which byte, and
  that the fix is to re-save the file as UTF-8.
- **A config saved as UTF-8 with a byte-order mark reads normally.** What
  Windows editors write was handed to the parser mark and all, which called
  it an invalid statement on line 1 and threw the whole file away. A leading
  mark is the one encoding hint that is never a guess: a UTF-8 one is
  stripped and the settings behind it apply, and a UTF-16 one is refused by
  name rather than decoded into settings no other TOML tool would read.
- **The listings survive a console that cannot spell every glyph.** A
  terminal whose encoding is narrower than UTF-8 — `PYTHONIOENCODING=ascii`,
  a legacy Windows console on cp1252 — encodes with `errors='strict'`, and
  `--list`, `--tree`, `--plugins` and `--help` all died there with a raw
  `UnicodeEncodeError` traceback and exit 1, the listing cut off mid-row.
  `--tree` and `--plugins` fell over on footman's own strings — the box
  characters that draw the branches, the dash in a plugin header — so pure
  ASCII task names were no protection. footman degrades an unencodable
  glyph to `?` on the way out instead, the same way it already does for a
  tool's output inside a run. The starter file `fm new` writes is plain
  ASCII too: its `hello` docstring carried an em dash, so a brand-new
  project's first `fm --list` was the crash.
- **The cache collector sweeps downloads abandoned mid-flight.** It globbed
  only bodies and sidecars, so a `.part` file left behind by a killed
  process — a multi-gigabyte tarball, possibly — sat in the cache forever.
  It ages on its own short clock: one still being written keeps its own
  mtime fresh, and one that stopped a day ago belongs to a process that is
  not coming back.
- **A `fetch()` lands on its cache path whole, or not at all.** Every backend
  downloaded straight onto the cached file, so two tasks fetching the same
  cold URL truncated each other — one caller was handed a zero-byte file and
  the run reported ok — and a transfer that died halfway left its stump in
  the cache, where the next call mistook it for a good copy and served it.
  With `sha256=` the report was worse than the bug: it blamed the server for
  bytes a sibling task had corrupted after they arrived intact. A download
  now streams into a `.part` file beside the body and is renamed into place
  only once it is complete.
- **A body that arrives short is refused, not cached.** With `urllib`, a
  response that ended before its `Content-Length` was written to the cache
  as a complete file and exited 0 — CPython reads a truncated body without a
  word on purpose. The ETag off that same response then went in the sidecar,
  the healthy origin answered `304` from then on, and the half file was
  served as a hit until somebody deleted the cache by hand.
- **A dead transfer no longer takes the cached copy with it.** A connection
  that dropped mid-body raised the library's own exception straight past
  `fetch()`, so the documented "a cached copy beats a failed refresh"
  fallback never got its say — on `urllib`, `httpx` and `requests` alike.
  Those arrive as `FetchError` now, and the good copy is still there to fall
  back to.
- **The `curl` fetch backend revalidates like every other one.** It threw
  its response headers away, so it stored no ETag, sent no `If-None-Match`,
  and could never receive a `304` — every call re-downloaded the whole file.
  Worse, it reported a download unconditionally, so when another backend's
  sidecar did win it a `304` the receipt still said the bytes had moved. It
  reads the status and the validators off the response now, the way `urllib`,
  `httpx` and `requests` do. Which backend you name picks a socket, not a
  behaviour.
- **A `Secret` passed straight into `run()` no longer prints in the clear.**
  Handing a token to a command as an argument of its own —
  `run(["twine", "upload", "--password", token])` — put it verbatim on the
  step line, the `--verbose` announce, the `--json` step row and its
  address, a `--profile` span, and the `RunFailed` message that lands in a
  CI log. `SECURITY.md` names those surfaces, so the promise was
  half-kept, which is worse than either extreme. Every one of them now
  renders through a single display step that replaces a `Secret` element
  with `***`; the record underneath is untouched, so `recording()`,
  `result.command`, `.raw` and `to_argv()` still hold what actually ran.
  Interpolation is unchanged and still deliberate: a `str` operation on a
  `Secret` yields a plain `str`, so `run(f"login {token}")` and `reveal()`
  emit the real value with no switch to disarm.
- **A supervisor's stop signal stops the run cleanly.** `SIGTERM` — what
  `timeout`, `docker stop`, `kill`, systemd, Kubernetes and a cancelled CI
  job all send — arrived at its default disposition, so footman died where it
  stood: no receipt, no `--json` envelope, and every subprocess tree left
  running, since a spawned child leads its own process group precisely so the
  terminal's signals cannot reach it. A stop now unwinds where Ctrl-C does,
  which is what reaps those trees; stderr says `terminated` and the exit code
  is 143 (`hung up` and 129 for `SIGHUP`, and Windows binds `SIGBREAK` to the
  same meaning as `SIGTERM`). `SIGKILL` and `taskkill /F` stay uncatchable, by
  anyone.
- **An exception leaving a task body is a task failure, whichever side of
  `Exception` it sits on.** The body's failure catch stopped at `Exception`,
  so an `asyncio.CancelledError` — a `BaseException`, and what escapes
  `asyncio.run()` when a task cancels itself — went past the report
  altogether: a raw traceback, no row of its own, no `--json` envelope, and
  a sibling that had already succeeded went unreported too. The task now
  gets an ordinary failed row with its code and the stack an unplanned
  exception carries. `KeyboardInterrupt` and `GeneratorExit` keep their
  run-level meaning: one is the user stopping the process, the other the
  interpreter tearing a frame down, and neither is the task failing.
- **Ctrl-C during an in-body `parallel()` stops the run instead of waiting
  it out.** The fan-out entered its pool with no abort arm, so the interrupt
  unwound in the main thread and then blocked joining workers that were
  still in `communicate()` on children spawned into their own process
  groups, which never saw the signal. It waited out exactly the work you
  asked to cancel — measured at 11 seconds for 12-second children — and a
  user who gave up and killed footman orphaned the whole tree. The fan-out
  now reaps its children the way the scheduler already did one layer up.
- **A runnable group's bare name completes its default's values.** Parked on
  the group, completion never reached the branch that answers values, so
  `fm ci --mode=<TAB>` was silent — no choices, no paths, no `suggest()` —
  while the same action spelled `ci.default` completed in full. A bare group
  still offers its sibling tasks.
- **A generated `Example:` line is a command footman accepts.** The
  synthesised example spelled an option and its value as two words, the one
  spelling footman refuses, so copying the example out of `fm --help` gave
  exit 64. The same text feeds the docs exporter, so it shipped on the site
  too. The test round-trips the generated line through the parser, which
  catches the next one as well.
- **An unwritable cache no longer kills the command.** With a read-only
  `HOME`, every invocation — `fm --help` included — died with a
  `PermissionError` traceback. Writing the manifest is now best-effort at
  the call site: the cache is derived data, and failing to save it is not a
  reason to fail the run.
- **A numeric enum survives the round trip.** `--json` wrote an enum field
  as its number and published a schema saying so, and piping that same
  document back in refused with "expected one of 1|2, got a number". The
  binder now accepts a member by value, as it already did for `Literal`.
- **`between()` and path checks run on a piped document.** A whole-document
  parameter ran only its `check()` validators, so a piped `[1, 9]` passed
  bounds that `--flag=9` refused. Container documents now validate per
  element, like the flag channel.
- **Two `include()` calls from one package both mount.** The first call
  memoised the parent package's empty capture, and the second stopped at
  that entry and looked for its submodule inside an empty tree. A typo in
  the address keeps its taught refusal.
- **A defaultless group as a prerequisite refuses instead of crashing.**
  `pre=[group]` where the group has no `@group.default` gave a traceback and
  exit 1 — but only with the progress line on, which is the default, so it
  looked intermittent. The taught refusal was already written; the summary
  pass now steps aside for a chain that is about to be refused.
- **`run(cwd="unmanaged")` runs where it says.** The token resolved to "no
  directory", which the subprocess injector could not tell from "not asked
  for", so it filled in the task's directory — and then blamed the task for
  spawning raw, recommending `run()` to someone already using it.
- **An emptied cascade takes its completion manifest with it.** Deleting the
  last `tasks.py` left the directory's manifest live, so TAB kept offering
  tasks the runner then refused by name — and every stale press renewed it,
  so the idle sweep never collected it either.
- **`--json` task rows drop a lifted step's receipt.** A failing `step()`
  wrote footman's own receipt line into the row's `output`, duplicating the
  step row beside it. The `run()` path already suppressed this under
  `--json`; the lifted path now does too.
- **The cache collector no longer condemns files it did not write.** One of
  its rules ignored age, so any JSON in the cache directory with a `cwd` key
  naming a missing path was deleted at any age — including files the docs
  invite task authors to keep there. It now checks the file is a manifest.
- **An empty listing says nothing rather than trailing off.** `(know: )`
  with a dangling colon, on a tasks file with no tasks — and on three
  sibling clauses, two of which promised tasks and then named none. The list
  can be empty because everything in it needs a project, too.
- **The `=`-attachment hint is said once.** A task option shadowing a
  value-taking global printed "did you mean `--jobs=4`?" twice in one line.
- **The generated completion hook's header names a command that runs.** Its
  first line quoted `fm --install-completion bash`, which footman refuses —
  inside a file footman writes into your shell config.
- **The shipped `docs.*` options say more than their own type.** `--out=PATH
  a path` told the reader nothing the metavar had not; on two of those tasks
  it was the only option line on the page.

- **An exception escaping a task body says where it came from.** A task
  that raised reported the type and the message and no location at all —
  on a run of any size, finding the line meant guessing. The receipt now
  carries `file:line`, taken from the innermost frame that is *yours*:
  footman's own frames are dropped wherever they sit, including the middle
  of a step's stack, so what is left is the code you wrote. The full
  traceback comes with `-v`, and whenever stderr is not a terminal — a CI
  log keeps everything without anyone having to remember the flag. A
  deliberate stop (`fail()`, `sys.exit("…")`) still renders as its reason
  alone; there is no crash to place. `--json` rows carry a `traceback`
  field under the same rule.
- **`--keep-going` reports every failure, not just the first.** The flag
  exists to collect them all, and reported one. The abort latch is
  process-wide but fail-fast's abort is per-subtree, so a keep-going task
  — deliberately spared the reap — ran on, failed on its own terms, and
  was still stamped "cancelled — fail-fast stopped the run". The label
  replaces the error rather than joining it, so each real failure after
  the first was hidden behind a cancellation that never happened. It also
  skewed the exit code, which is drawn from the first genuine failure.
  Fail-fast is unchanged: a task cut off mid-flight still reports `cut`.
- **A `Secret` inside a returned document redacts.** `Secret` promises to
  redact on every structured surface, and a `Stdout[…]` document printed
  it in the clear. Two holes compounded: the redaction walk stepped over
  dataclasses — the shape a structured return usually arrives in, and one
  that survives `asdict` with the marker intact — and the document surface
  never called the walk at all. `Stdout[dict]` redacted while
  `Stdout[Credentials]` did not. `reveal()` is unaffected: it is still how
  you say a secret is meant to leave.
- **An `async def` task or step is refused, not silently skipped.**
  footman has no event loop, so calling an async body returned a coroutine
  that was never awaited: the body never ran and the task reported
  success. Both now refuse by name, pointing at the definition's
  `file:line`, and the coroutine is closed rather than left to warn at
  some unrelated moment.
- **Ctrl-C reaps the running child.** Interrupting a task blocked in
  `run()` left the subprocess tree alive: the kill was wired to the
  ordinary failure paths, and an interrupt takes neither. The child now
  dies on the way out, on any exception, and is unregistered exactly once.
- **A `KeyboardInterrupt` mid-task no longer wedges the run.** A task
  reached as a scheduler node holds the run's once-cell for that work, and
  leaving by `BaseException` walked past the resolve. Anything sharing
  that work then waited on a cell nobody would fill — not until the task
  ended, but for the rest of the process, and past a second Ctrl-C. The
  sharer is now failed by the same thing that failed the claimant.
- **Two lane deadlocks.** A task waiting for a named lane did not count
  itself as parked, so the arbiter could not see that everyone was
  waiting; and a task holding the serial lane blocked on itself when it
  asked for another lane from inside. Lineage is now exempt, and the wait
  is parked.
- **The "never imports your code" claim covered a build that does.** The
  README, the docs home page and the design page stated it without
  qualification, so a reader checking footman before trusting it found the
  opposite of the promise: the first <kbd>Tab</kbd> in a fresh directory
  builds the manifest by spawning a subprocess that imports your `tasks.py`,
  the same import a run does. What is import-free is the process answering
  the keystroke — a warm <kbd>Tab</kbd> is still one file read, one JSON
  parse and a tree walk. Every surface carrying the claim names that build
  beside it, and `SECURITY.md` lists it among the things that are working as
  intended: it had named "a completion path that executes something" as an
  in-scope vulnerability, which is a description of the cold build.

### Documentation

- **Hovering in the playground answers about a symbol.** Resting the
  pointer in the gap between two arguments recited the callee's entire
  signature — thirty parameters deep for a tool like `ruff.check` —
  because hover fell through to "which call encloses this position?"
  whenever no identifier sat under the pointer. It now says nothing
  there, the way an IDE does; which call the cursor is in is the
  parameter-hints panel's question. The card that does answer reads
  prose first — the docstring, what the call returns, then each
  argument with its documentation — and carries the signature at the
  bottom, collapsed to one line. Labels lead with the name the source
  spells: hovering `@task` said `TaskDecorator(…)` and a tool handle
  said `Check(…)` where the code says `check`, because a synthesized
  signature renders its class. A `Returns:` section is surfaced for the
  first time, and it no longer bleeds into the last argument's
  description.
- **The playground shows parameter hints.** Positionals have
  documentation but completion cannot offer them — the value is yours
  to type — so the editor now does what every IDE does: typing `(` or
  `,` inside a call opens a panel above the cursor, prose first: the
  docstring's summary, what the call returns (the Google-style
  `Returns:` section), and the active parameter's own Args entry — the
  parameter the cursor is on, tracked across commas via the
  interpreter. The signature sits beneath as a one-line footer with
  the active parameter highlighted inline; a parameter without its own
  entry rides on the summary, which is where a variadic positional is
  usually described. The same keystroke also opens the completion
  menu, the way VS Code stacks the two: the menu answers what can be
  inserted, the panel what the slot means. The callee keeps the
  spelling you typed — a tool handle's synthesized signature renders
  its class (`Check`, not `check`) and the panel corrects it from the
  source. Escape closes the menu first and the panel second,
  Ctrl/Cmd-Shift-Space summons the panel, a comma inside a string
  literal never triggers it, and a typed paren never starts the
  runtime download — only the explicit keybinding may.
- **The playground completes in IDE order.** Ctrl-Space inside a call
  used to answer an alphabetical soup starting at `abs`: jedi drops its
  native parameter completions when the callee is a tool handle's
  synthesized `__call__`, and with no typed prefix the raw list won.
  The completer now asks the signature directly and leads with the
  call's own keyword parameters — each carrying its declaration and its
  Args prose, reaching through `__call__` the same way hover help does —
  then ranks names from the editor's own tabs above imports and sinks
  builtins below both. Hovering a decorated task in your own buffer also
  answers with its real `def` header and docstring instead of the
  decorator's inferred `TaskFn` shape.
- **The page's jedi matches the rehearsals'.** Pyodide bundles jedi
  0.19.2 and micropip prefers bundled wheels over PyPI, so the browser
  ran a different jedi than every CPython rehearsal — and 0.19.2
  resolves footman's annotated `task` to a bare name, no signature, no
  docstring (Willem's screenshot, again). The install now floors at
  `jedi>=0.20`, which the bundled wheel cannot satisfy, forcing the
  PyPI line both worlds share. The hover tooltip also stops inflating
  small answers — the minimum width was a leftover cure for a clipping
  problem the body-parented tooltips no longer have.
- **The simulated child honours Popen's bytes contract.** A real Popen
  answers in bytes unless text mode was asked for; the playground's
  stand-in answered `str` unconditionally, and `platform.platform()`
  died on it in the page — `_syscmd_file` calls `.decode()` on what it
  rightly expects to be bytes. macOS rehearsals never took that branch,
  which is why CPython stayed green; a Node-Pyodide parity probe named
  it in real emscripten, and a rehearsal now pins the contract directly.
  The canned table also answers `uname` emptily — the simulated echo was
  leaking into the platform string as its processor field.

## [0.41.0] — 2026-08-16

### Added

- **Completing a list item no longer ends the list.** Accepting an element
  of a comma-splitting value (`--regions=e<TAB>`) used to plant a trailing
  space, so continuing the list meant deleting it and typing the comma.
  The resolver now marks such a reply with exit code `102` — candidates on
  stdout stay exactly as before, so hooks and resolvers of different ages
  degrade to the old behaviour cleanly — and each hook answers in its
  shell's own accent: **zsh** appends the comma as a removable suffix
  (accept `eu`, get `--regions=eu,`; a space or Enter takes it back off),
  **bash** glues the cursor so the comma is the next keystroke, and
  **fish** rides the comma on each inserted candidate. pwsh and nushell
  already glue every completion and are untouched. Applies to choice
  values, `suggest()` completers, and mid-list positionals alike; `nosplit`
  values and path values keep their own protocols. The playground prompt
  reads the same marker. Reinstall or re-`eval` your hook
  (`fm --install-completion`) to pick the new behaviour up.

### Fixed

- **A bare import of a plugin module no longer spends its declarations.**
  A provider module registers by import-time side effect (a `GlobalOption`
  construction, a hook decorator), and a module imports once per process —
  so a plain `import footman.env_files` anywhere before the first proper
  load left every later `plugin()` mount and the unknown-flag scan with an
  empty capture and nothing to reuse: the mount refused with a misleading
  message, and the scan silently stopped teaching `--env-file`. The
  declarations survive in the module's own namespace, so footman now
  rebuilds a contributions-only module's tree from there: a `GlobalOption`
  carries its defining module, and each hook decorator records what it
  registered on the decorated function, wrapper pairs included. Modules
  that define tasks or groups keep the taught refusal — a task tree cannot
  be rebuilt from names alone. This was also the suite's worker-ordering
  flake: four `test_split` scan tests failed together whenever their own
  bare imports ran first on a fresh worker.

### Documentation

- **The playground's editor helps on hover, and its menus behave.**
  Rest the pointer on a name — or inside a call's parens — and a tooltip
  answers from the interpreter: the signature line in code face, the
  docstring's opening beneath it, the toolroom stubs' documentation one
  hover away instead of a completion-menu hunt. The completion menu
  itself learned manners: private and dunder names stay out unless the
  underscore was typed, the list caps at twenty, and the tooltips are
  finally styled through the site's own variables — they matched neither
  palette before, dark mode least of all. **Python itself loads as soon
  as the page does**: the first Run, Tab, or hover no longer pays the
  download, and jedi rides along so the editor helps immediately.
  Hover reaches everything: a long signature wraps to one parameter per
  line in the editor's own colours, a keyword argument answers with its
  own `Args:` entry rather than the whole signature, a parameter answers
  with its declaration and owner, literals and keywords stay silent, a
  Google-style `Args:` section renders as bold names with prose, and
  the tooltips float over the page and scroll — parented to the body,
  because fixed positioning alone still clipped at the pane's edges.
- **The playground's simulated world answers a few reads with data.**
  The homepage's `suggest(branches)` parses `git branch` output — and the
  simulated child answered with its own echo line, which the completer
  chopped into "candidates" (`--branch=[simulated]`, `--branch=git`, …).
  A small canned table now gives well-known reads plausible output —
  `git branch` answers branch names, `git tag` answers tags — so
  completers that parse a child's stdout offer something real. The table
  is deliberately tiny: reads that docs examples parse, nothing that
  pretends the write side happened; everything else keeps the
  `[simulated]` echo, and the page discloses the canned reads beside the
  other sandbox facts.
- **`@task` documents itself on hover.** The `TaskDecorator` protocol —
  the static shape every editor reads for the module-level `task` — said
  only that it was a static shape; it now says what `@task` does, and
  its options overload documents every option Google-style, so
  `serial=`, `confirm=`, `cwd=` and the rest answer keyword hover in
  editors and the playground alike. A drift guard holds the whole
  public surface to that bar: every export, and every public member an
  exported class defines, must carry hover documentation — with an
  `Annotated` alias's inherited typing boilerplate deliberately not
  counting as documentation.
- **The playground's editor completes from the interpreter.** Type `.`
  after a toolroom handle — or press Ctrl-Space anywhere — and the menu
  comes from jedi over the buffer, with footman and toolroom importable:
  a handle completes its **real methods from the typed stubs, docstrings
  riding along** as the info panel. Typing never waits on Python: the
  source fires by itself only right after a dot, and a typed dot never
  starts the runtime download — only an explicit Ctrl-Space may pay that
  cost. jedi installs on first use, like pytest. (The completer runs
  jedi in its in-process environment: the default environment inference
  shells out to a python subprocess for `sys.path`, which the sandbox's
  simulated child would answer with nonsense and the browser cannot
  answer at all.)
- **Prompts work in the playground.** `ask()`, `prompt()`, `confirm()`,
  and `select()` used to fall to defaults or refuse — no terminal, no
  one to ask. The page is the terminal now: the sandbox's stand-in for
  the real stdin answers each read with a **browser dialog**, whose text
  is whatever the framework wrote to the real stderr since the last
  read — a question arrives as the question, a `select()` menu arrives
  with its numbered lines. Cancel reads as end-of-input, so a
  defaultless `ask()` fails with footman's own taught message instead of
  looping; a secret is typed unmasked into the dialog (disclosed — a
  playground, not a vault) and still round-trips as a `Secret`. Gallery
  commands can declare `prompts` — canned answers the rehearsals feed
  through the same seam, so the asked path is CI-tested end to end. A
  new Input example shows both halves: run `release` bare and the page
  asks; supply the values and nobody is asked.
- **Dynamic completion works in the playground.** A `suggest()`
  completer used to hit the recompute sentinel and answer nothing — that
  protocol respawns a `_suggest` child, and the page has no processes.
  The interpreter holding the user's code is the page itself, so Tab now
  runs the completer fresh in place, mirroring the child exactly: same
  walk to the owner, same muting of its chatter, same
  prefix-plus-filtered-values emission. The editor's other tabs are
  written before completing, so a completer that reads a file sees what
  the editor says *now* — the Completion example's `switch` task
  completes branches from a `branches.txt` tab, and editing the tab
  changes the next Tab's answer. File handoffs still answer nothing: the
  page has no filename completion to hand off to.
- **The playground gallery covers every category.** Eleven groups in the
  dropdown — Basics, Typing, Validation, Variadic, Scheduling, Tools,
  Results, Config, Composition, Input, Completion — each example with command
  chips whose exit codes and output are asserted in CI. The sandbox grew
  three abilities to make them honest: a tab named `stdin` is the run's
  **pipe** (its text feeds stdin-bound parameters and is never written to
  disk, so the Results example ships a prepopulated JSON payload),
  `run(shell=True)` resolves to a stand-in shell (the simulated child
  never executes it, so the pipeline example runs), and an example can
  declare **`packages`** — micropip installs fetched on its first run,
  the way pytest always was, so each example carries its own install
  cost and the page stays light.

## [0.40.0] — 2026-08-14

### Changed

- **Completing an option offers both of its spellings.** `--bra<Tab>` now
  answers `--branch` *and* `--branch=`. Every value in this grammar is
  attached, so `--opt=` is the only way to pass one — and completion never
  said so. The value path, including any
  [`suggest()`](https://willemkokke.github.io/footman/typing/#dynamic-completion)
  completer behind it, was reachable only by knowing to type `=` first,
  which is exactly the internal knowledge completion exists to spare you.
  Both rows carry the option's `doc("…")` line: take the bare one to mean
  "use its default", take the `=` one and press <kbd>Tab</kbd> again to pick
  a value. Flags are unchanged and keep their single spelling — a flag takes
  no value at either default (`--fix=true` is a refusal), and `--no-fix` is
  still the off spelling. Verified through all five shells' own completion
  engines, bash included, where `=` is a word-break character.
- **The bare spelling names the value it stands for.** With one `doc("…")`
  shared between the pair, the menu showed what looked like the same row
  twice with nothing to choose by — the first question anyone asked of it
  was why it was listed twice. `--branch` now reads `default: main`, which
  is both the difference between the two and something completion never
  told you before; `--branch=` keeps the description. The default was
  already in the manifest.
- **`docs.cast` records one frame per keypress, played at a fixed pace.**
  It sampled the terminal as it redrew, so what a recording contained
  depended on how the machine happened to be feeling: menus a shell painted
  and cleared inside one chunk vanished, a shell that paints in bursts lost
  keystrokes into a single frame, and a loaded runner produced a different
  recording from the same script. A frame is now taken after each key, once
  the shell has answered *and gone quiet* — the distinction that makes it
  work, since silence straight after a keypress usually means the shell has
  not started. **`max_frames` is replaced by `pace`** (seconds per keypress,
  default 1.2): there is no frame budget to spend when every frame is an
  event the script asked for. Recordings also caption the key that produced
  each frame, because a terminal shows what a keystroke did and never that
  one happened.

### Fixed

- **A named parameter and `*args` can share a signature.** The call the
  executor assembled passed named parameters as keywords and variadic values
  positionally — but Python only reaches `*args` after every slot declared
  before it is filled positionally. Any plain parameter before `*args`
  therefore broke the call the moment variadic (or `--` passthrough) values
  arrived, in one of two ways. Supplied — on the line, or filled by `env()`,
  `ask()`, a forwarded value, or a computed default — it collided:
  `TypeError: got multiple values for argument`. Absent, resting on its
  default, it failed *silently*: the first variadic values shifted left into
  the named slots, so `def test(marker="", *pytest_args)` — getting-started's
  own example — ran `fm test -- -q -x` with `marker='-q'`, wrong data under
  a green exit. Every parameter before `*args` is now emitted positionally
  when variadic values are present, in signature order, a skipped optional's
  slot filled by its default; keyword-only parameters after `*args` keep
  their keywords, and `--` passthrough lands where the docs always said it
  would — in the task's `*args`, never in a named parameter.
- **The docs no longer advertise a spelling the grammar rejects.** Two pages
  showed `fm workspace.mount --share <TAB>` completing to a value list. With
  a space, that offers the task's *other* options and the next chain head —
  the value form is `--share=`. Both now show the `=`.
- **Every recording gets its controls, not just the one on the open tab.** A
  cast inside a closed tab is `display: none`, so its CSS animations do not
  exist yet and there was nothing to attach play/pause/scrub to. They are
  now wired the first time each recording is shown.
- **Recordings fill the column.** An inline `<svg>` carrying only a viewBox
  contributes no intrinsic width, so the theme's `.md-typeset figure`
  (`width: fit-content`) collapsed the figure to its widest child — the
  control bar — and dragged the recording down with it. The rule that was
  supposed to fix this lost on specificity and silently did nothing; it is
  now scoped to outrank the theme's.
- **Recordings no longer repaint each other.** Five casts on one page were
  each correct alone and wrong together: rich's `unique_id` is unique within
  one export but not between two, and an inlined SVG's `<style>` is not
  scoped to that SVG — so every recording defined `.cf0`, `.cf7-r3` and
  `@keyframes cf0`, and the last one in the document won for all of them.
  Cells were painted in another recording's palette, casts animated to
  another's timings, and frames from two recordings showed at once. Every
  class and keyframe is now namespaced by the file it belongs to.
- **An underline no longer leaks across a whole recording.** pyte reads a
  *private* CSI sequence as an ordinary SGR: `ESC[>4;2m` is modifyOtherKeys,
  which fish and nushell enable at startup, and the `>` is ignored so
  parameter 4 sets underline — which every cell drawn afterwards inherited.
  That underlined the prompt in shells that emit no underline at all.
  `ESC[>1m` set bold the same way. Both are dropped before the emulator
  sees them.
- **A terminal reply split across two reads no longer corrupts the screen.**
  DCS replies were stripped per read, so one arriving in halves was stripped
  from neither and its debris reached the emulator as text — and as
  attributes. The stripper now carries its tail between reads, as the query
  answerer beside it already did.
- **A highlighted row is readable.** Reverse video was passed to rich as an
  attribute, and its SVG export painted the background without flipping the
  text, so a selected menu entry came out as a solid block with its own text
  hidden inside it. Contrast was then weighed against a private table of
  ANSI colours rather than rich's export palette — where `black` is
  `#4b4e55` and `white` is `#c5c8c6` — so a selected row measured as
  black-on-white while what rich painted was 2.34:1, a bar that reads as
  blank until you select the text. Reverse is resolved to concrete colours
  before rich sees it, and luminance now comes from the palette doing the
  painting, with WCAG AA as the bar.
- **Hidden frames are hidden.** A frame at `opacity: 0` is still there: it
  hit-tests, so devtools' Inspect landed on the last frame wherever you
  clicked, and its text joined any selection, so copying a line out of a
  recording returned text from frames nobody could see. Frames now step
  `visibility` alongside `opacity`.
- **The player steps by frame.** The last frame could not be reached — the
  stamp naming the boundaries was read off the wrong element, and seeking to
  a boundary landed a fraction before the switch, because the animation's own
  boundaries are percentages rounded to three decimals. Dragging to the end
  showed the *first* frame, since the end of the cycle is also its start.
  The scrubber is indexed by frame rather than by milliseconds and reads
  `n / N`, which is what a recording of keypresses actually measures. The
  player also re-queries its animations instead of caching them: one that
  started late kept its own clock, drifted, and put two frames on screen at
  once.
- **A path-required example runs in the playground.** The page's filesystem
  holds only the editor's files, so a parameter marked `exists`, `isfile`,
  or `isdir` — the cookbook's belt-and-braces deploy, brought over by its
  own "run it there" link — refused every value before the task ran: there
  was nothing on disk for the check to find. The sandbox now simulates path
  requirements the way it simulates children — the check passes, and the
  rest of the validation ladder (types, choices, bounds, `check(fn)`) stays
  real — at both of the check's seats: the splitter's eager CLI-token
  validation and the executor's late one (env fallbacks, variadic values).
  The playground page discloses it beside the other simulations, and a
  rehearsal drives the shipped driver over a path-required task in CPython
  under `_FM_PLAYGROUND_SIM`.

### Documentation

- **The playground is a gallery.** A dropdown of curated examples, grouped
  by category, each with its own files in the editor tabs and a row of
  **command chips** under the prompt — every chip a command line chosen to
  show one thing, its note saying what. Switching examples swaps the
  editor in place, so the loaded Python is reused; edits are remembered
  per example for the visit, and Reset restores the pristine files.
  Entries are linkable (`#example=<id>`), and a "run it there" fragment
  from a docs page appears as its own entry beside the curated ones. The
  registry (`docs/assets/examples.json`) is dual-read: the page fetches
  it, and the docs tests drive **every command line of every entry**
  through the shipped driver — asserting exit codes and output — plus a
  feature-coverage guard that fails when a listed feature has no live
  example. Seeded with Basics, Validation, and Variadic; the categories
  grow from here.
- **The playground edits like an editor and reads like a terminal.** The
  textarea upgrades to CodeMirror — Python syntax highlighting, Tab
  indents, Cmd/Ctrl-Enter runs — and falls back to the plain textarea if
  the editor fails to load. The editor's theme maps CodeMirror's tokens
  to the same `--md-code-hl-*` variables zensical styles Pygments with,
  so the editor and every code block in the docs share one palette by
  construction and follow the light/dark toggle live; the palette
  itself is one variable block (currently VS Code's Light+/Dark+
  colours, for the whole site). The stock autocompletion is off — it
  offered every keyword, builtin, and buffer word; completion that asks
  the interpreter in the page is planned instead. Both panes are set in **Fira
  Code** (vendored, ligatures on), and both stop growing at a screenful
  and scroll inside instead, so a long pytest failure is a scroll, not a
  wall. CodeMirror ships **vendored as one bundle**
  (recipe in `vendor/codemirror/`): loading it as separate CDN modules
  was tried and fails — each `+esm` entry point got its own
  `@codemirror/state` instance, and CodeMirror rejects extensions whose
  `instanceof` checks cross instances — and one bundle is one instance
  set by construction, with no second runtime CDN. The output pane is
  now a **session transcript**: every run appends its prompt line and
  output instead of replacing the pane, rendered in footman's own
  colours (the sandbox forces `--color=always` and the page maps the
  SGR codes it emits), with a Clear button to start over. The editor's
  tab bar is generated from the session's files rather than hardcoded
  to two — groundwork for the example gallery
  (`notes/20260814-playground-gallery.md`).
- **The homepage says what toolroom is.** The README's first code block
  imported from it and no page explained the name, so a reader met
  `from toolroom import ruff` with no idea whether it was required. Both now
  say what it does — it wraps *any* command-line program, with keyword
  arguments becoming flags, and ships generated type hints for common ones
  so an editor knows their flags — and that the hints only decide whether
  your editor can help, never whether a call works. It began as part of
  footman and was spun out because it releases on its own schedule, and
  because type hinted command-line calls are useful without a task runner.
  footman does not depend on it and never imports it.

- **The personal tasks file is documented where the cascade is.**
  `~/.config/footman/tasks.py` has always ridden everywhere, but it appeared
  only on the page about building a *branded* CLI — so "The task cascade"
  described the walk as repo-root-down and stopped, leaving readers with an
  incomplete model. It now names the outer rung, and a new **Personal tasks**
  section covers the file, `project > user`, and marking the ones that need a
  checkout with `needs_project=True`. Config already documented its user rung;
  tasks now do too.
- **`needs_project` is in the `@task(…)` reference**, beside `hidden` and the
  rest, instead of only in the branded-CLI page where it was first written —
  along with `group(…, needs_project=True)` for a whole subtree.
- **CI builds the docs on docs-only pull requests.** The strict site build was
  gated on `code == 'true'` alongside the heavy code jobs, so a change under
  `docs/` skipped it — while the Docs workflow, which only triggers on push to
  `main`, did not run either. Nothing checked the docs on precisely the pull
  requests most able to break them, and a broken page surfaced after merge as
  a failed Pages deploy. It now runs on every pull request.

## [0.39.1] — 2026-08-10

### Fixed

- **A runnable group's bare name honours `needs_project`.** `fm lint` and
  `fm lint.default` are one action with two spellings, and outside a project
  they answered opposite ways — the explicit one refused while the bare one
  ran, printing the very fiction the marker exists to end. Sealing was never
  at fault: the default *task* was marked correctly, but the group node had
  no answer for the listing, completion and dispatch paths to read. The
  group now takes its answer from its default, stamped once where `hidden`
  already resolves the same way. Derived, not inherited — a sibling keeps
  its own answer, so `lint.version` stays reachable when `fm lint` is not.
- **`group(…, needs_project=…)` type-checks at the module level.**
  `GroupFactory.__call__` is documented as "the static shape of
  `Group.group`" and had not gained the parameter, so the module-level
  spelling failed type checking while the method form passed. Runtime was
  unaffected; typed consumers were blocked. A signature census now holds
  the two together.

## [0.39.0] — 2026-08-10

### Added

- **`@task(needs_project=…)`, and a built-in task needs one by default.** A
  branded CLI's `builtin=` set is mounted exactly where discovery found no
  project, and most of what such a CLI ships means nothing there — so it ran
  anyway and *lied*: a `files` task printing nothing as though the project
  had none, a `coverage` task reporting no stamp yet, both exiting 0. Listing
  and completion happen before any body or hook runs, so nothing downstream
  could filter them, and footman already owns the "is there a project"
  predicate.

  Outside a project a task that needs one is not listed, not completed and
  not suggested — and asking for it by name is **refused with the reason**
  rather than 404'd, because the task does exist:
  `deploy needs a project — no tasks.py found here or in any parent of /tmp`.
  The message names where footman looked, because what usually types it is a
  tool that started in the wrong directory.

  `group("ci", needs_project=True)` covers a subtree and a child may still
  say otherwise — the tri-state `hidden` has. The question is only ever asked
  *outside* a project, so this can never hide anything from one. The **user
  rung defaults the other way**: a personal task rides everywhere unless it
  says `needs_project=True`. Each default is that rung's own promise.

- **`matching("*.json")` narrows a path value's <kbd>Tab</kbd>.** A `Path`
  parameter hands off to the shell's own file completion — footman answers
  from a cached manifest and never touches the filesystem — and this is what
  it hands *along*: the pattern the shell filters by. `--env-file` now offers
  `.env`, `.env.local`, `.env.production` instead of every file in the
  directory, and `--profile` offers `*.json`; both declare it themselves.
  Directories always come along, or a match one level down would be
  unreachable, and it is completion only — a path typed anyway still binds.

  bash, zsh, fish and pwsh all narrow. **nushell does not**: filtering there
  means returning a list of footman's own, which replaces its built-in file
  completion outright and loses directory descent. fish shows dotfiles once
  you type the leading `.`, which is fish's behaviour for every command.

- **`step(...).opts(color=…)`** — the same `auto|never|always` `run()` takes,
  applied to the environment the whole step body runs under. One decision at
  the boundary of a body, read by every command it spawns and every
  in-process tool it hosts, instead of the same keyword threaded through each
  call. It composes with `env=` the way `run()` does: `env=` replaces
  wholesale, `color=` then paints that replacement.

### Changed

- **Every word completion offers now says what it does.** Task and group
  names always carried their docstring, and a task's own options their
  `doc("…")` line — but three emitters dropped text footman already had, so a
  <kbd>Tab</kbd> on a flag listed bare names. Now footman's own globals
  (`--jobs — max parallel tasks`), a plugin's globals (the `help=` it
  declared, which the manifest had been carrying all along), and a runnable
  group's default options all describe themselves. zsh and fish right-align
  these into a column and colour them by the user's own settings, so the
  shells were always ready for it.

  The core globals' words ride in the manifest rather than being mirrored
  into the completion hot path a second time — prose is the thing that
  rots, and `CORE_OPTIONS` stays the one place they are written. Manifest
  schema 6; a warm <kbd>Tab</kbd> still measures ~28 ms.

- **An unmounted plugin's flag is now discovered, not listed.** 0.38.1 taught
  `--env-file` and `--profile` from a hardcoded table of two. footman now
  loads the plugins it is willing to speak for and reads what globals they
  declare, so a new option is taught the day it ships and nothing can go
  stale. Two packages qualify: **footman's own** — those two flags belong to
  the framework, are useful to anything built on it, and footman is imported
  by definition, so a branded CLI teaches them too, whether or not it ever
  named a distribution — and **the brand's**, once a branded CLI names one
  with `dist=`, which is the case worth having: a distribution can ship
  several plugins while a tasks file mounts only some of them. The scan runs
  only once a refusal is certain and is memoised, so a successful run never
  pays for it. Everything else keeps the plain "unknown global option":
  reaching a third party's flag would mean importing, on a typo, code the
  project deliberately did not mount.

### Fixed

- **An attached path value completes on PowerShell at all.** The hook handed
  the whole `--env-file=` token to `CompleteFilename`, which looked for a
  file by that literal name and found none — so `--opt=<Tab>` on a path
  completed to silence. The head through the `=` is stripped for the walk and
  put back on each candidate, the same reading the comma-separated branch
  beside it already used.

- **`Runner` puts the brand back after every invocation.** A real entry point
  runs one brand and deliberately never restores the module globals — the
  process *is* that CLI. A test process is the one place that isn't true, and
  `Runner` is documented as saving and restoring around each invocation. It
  did that for the brand's *locations* and not for the brand itself, so
  whichever `Runner(App(...))` ran first in a pytest-xdist worker silently
  decided what every later test in that worker saw. Anyone testing a branded
  CLI alongside another had a test order deciding their results.

## [0.38.1] — 2026-08-10

### Changed

- **The cold-cache <kbd>Tab</kbd> waits a second, not three.** A first-time
  manifest build measures ~100–150 ms (footman's own fat `tasks.py`
  included), so the old bound only ever came into play for a tasks file that
  imports something heavy at module level — and there a three-second freeze
  reads as a broken shell. The build is detached and lands anyway, so the
  shorter cap trades a hang for a blank first <kbd>Tab</kbd> and an instant
  second one.

### Fixed

- **Columns are measured in terminal cells, not in characters.** Every
  aligned surface — `--list`, `--help`, `--plugins`, the step lines, the run
  summary — padded with `len()`, which answers a different question in three
  ways at once: an escape sequence is bytes the terminal eats rather than
  shows, a combining mark rides on the character before it, and an
  East-Asian character or emoji takes two cells. A task named `构建` or a
  `step("🚀 deploy")` bent the column around itself. One helper now answers
  for all of them, with a fast path so plain ASCII costs what it did before.
- **A truncated status line can no longer leave the terminal painted.** The
  live line carries ANSI once it counts a failure, so it measured nine cells
  too wide and a raw slice could cut an escape in half — printing its tail as
  gibberish, or leaving everything after it red. It now cuts on what shows,
  never inside an escape or a wide character, and closes whatever styling was
  still open.
- **A task's option written where globals live now points the right way.**
  `fm --fix lint` answered "unknown global option `--fix` (global options go
  before the first task)", which sends someone *left* when the fix is to move
  the word *right*: the option belongs to a task, and it goes after that
  task's name. footman now says which task owns it — the one on the line,
  when the line names one — and shows the spelling that works:
  `--fix is an option of lint, not a global — it goes after the task name:
  lint --fix`.
- **An unmounted plugin's flag says so instead of "unknown".**
  `fm --env-file=.env build` in a project that never mounted the plugin read
  as a misspelling, sending someone hunting for a typo in a flag they spelled
  correctly. Both first-party flags now name their provider and the line that
  turns them on: `--env-file comes from footman.env_files, which this project
  has not mounted — add plugin("footman.env_files") to tasks.py`.
- **One argument too many reads as arity, not as a bad task name.**
  `fm render page.md out.html spare` answered only "no task named 'spare'".
  A word that names nothing, arriving right after a task whose positionals
  are full, is far more often one argument too many — so the answer carries
  both readings, likeliest first: `… — or one argument too many for render,
  which takes 2 arguments`. A word close to a real task name keeps the
  spelling suggestion instead; two competing "did you mean"s teach nothing.
- **Completion offers a runnable group once.** `ci` and `ci.default` are one
  action wearing two addresses, and the top-level <kbd>Tab</kbd> listed both.
  Listings deduped this in 0.37.0; completion now matches. Descending is a
  different question — at `ci.` the bare row is off the screen, so
  `ci.default` stays offered there.
- **`--json` rows carry only what the task printed.** A failing step wrote
  its human receipt line into the task's capture buffer, so `output` arrived
  as `"FAIL build  echo hi  (0.0s)\n"` — a human's line in a machine's field,
  duplicating the step row directly below it. Under `--json` that chrome is
  left out; a body's own prints still land there.
- **A skipped row knows its own address.** Addresses are assigned when the
  plan is final, not when a task runs, but a row that never ran arrived with
  `address: ""` — which broke the one lookup the envelope promises, since
  `blocked_by` and `after` name addresses and an empty one matches nothing.

## [0.38.0] — 2026-08-10

### Changed

- **A `suggest()` completer runs where its values are wanted, and nowhere
  else.** Every invocation used to run every completer in the tree, because
  the manifest baked their choices on the way past: a git-branch completer on
  one task shelled out on `fm build`, on `fm --list`, on every run of
  everything. Nothing needed that — a <kbd>Tab</kbd> already recomputes fresh,
  ignoring the bake entirely. Now the command line resolves the one parameter
  whose value it is validating, `--help` resolves the ones it prints (and only
  those: `fm --help build` never touches another task's completer), the docs
  exporter still bakes because a page has no reader to resolve later, and a
  line that mentions neither runs nothing. Validation is *more* exact than
  before — it asks the completer now, rather than trusting a snapshot taken
  earlier in the same process — and a broken strict completer surfaces where
  its values were needed instead of refusing every other task's invocation.

- **`--help` says when a choice list is dynamic.** A `suggest()` parameter
  shows its values in the synopsis and the option row, marked `(dynamic)` the
  way a computed default is marked `(computed)` — the list is what the
  completer answered just now, not the law of the task.

### Fixed

- **The two short-option habits are taught, not shrugged at.** `fm -j1` said
  `unknown global option -j1 (global options go before the first task)` —
  which misdiagnosed it twice over: `-j` exists, and it *was* before the task.
  A short option wearing its value now gets the same sentence the spaced form
  already got, `-j takes its value attached — did you mean -j=1?`, so one
  canonical spelling is taught from whichever way a hand reaches for it. And
  `fm -sq` says footman does not combine short options, naming them apart
  (`-s -q`), instead of reading `-sq` as a name nobody wrote. A genuine typo
  still gets the plain unknown-option answer.

- **A hook that chooses to stop speaks for itself.** A `fail("…")` from a
  lifecycle hook was prefixed with the machinery that ran it — `@pre_tasks
  'load': --env-file: … does not exist` — putting plumbing in front of a
  sentence written for a person. The reason now stands alone; a hook that
  *crashed* is still named, because there the machinery is what a reader
  needs.

- **<kbd>Tab</kbd> in a directory with no tasks answers at once.** It used to
  stall the full three-second cold bound and come back empty — every time,
  with nothing ever cached to make the next one different. Which made the
  most common place to press it, a home directory, the slowest. Three faults
  compounded: the completion dispatch never told `_paths` about the brand's
  built-ins, so the hot path could not see that a project-less directory has
  a global tree at all; the fallback to that tree was skipped; and the
  rebuild child, asked to build nothing, wrote nothing. The dispatch now
  carries the built-ins, and one walk on the cache-miss path decides what the
  directory is: a project builds its cascade, a project-less one serves the
  shared global tree, and a directory with genuinely nothing to complete says
  so immediately rather than spawning a build that can only come back empty.
  Measured on a home-like directory: 3,052 ms and no candidates, now 141 ms
  cold and 28 ms warm, with the built-ins offered.
- **`run("tool", "arg")` is refused instead of silently dropping the
  argument.** `run()` takes one command — a string or a list of tokens — but
  the subprocess-style spelling was accepted and the extras only ever reached
  the *label*: `run("echo", "hi")` printed nothing and passed green, and
  `run("sh", "-c", "…")` ran a bare shell that sat on the caller's terminal
  while its receipt read ok. It now raises a `TypeError` naming the spelling
  that works, `run(["sh", "-c", "…"])` — the same refusal a shell operator in
  a shell-free `run()` already gets.

- **A group's default lists once.** `--list` showed a runnable group twice —
  the bare `lint` row, described by its default, and the `lint.default`
  child: one action wearing two lines, and `--tree` and group help repeated
  the shape. Listings show the bare row alone now. The address itself loses
  nothing: `fm lint.default` still runs, still completes, still appears in
  `--describe`, and a typo of it still gets the real spelling back.

- **A mounted global after a task name is taught by name.** `fm build
  --audit` said `unknown option --audit` where a core global in the same
  position already said "goes before the first task name" — one teaching
  for both now, the last place core and plugin options spoke differently.

## [0.37.0] — 2026-08-10

### Added

- **`run(color=)` — a per-call colour override for what the child emits.**
  `"always"` forces the colour variables into this one child's environment,
  `"never"` writes `NO_COLOR` and removes any inherited force variables, and
  the default `"auto"` follows the run. Explicit beats ambient, so
  `color="always"` holds under an exported `NO_COLOR`. This closes the second
  half of per-call colour: a tool's own flags were always reachable per call
  (toolroom builds them into the argv), but a tool that reads the
  *environment* obeyed only the run-wide decision, because footman owns the
  child's environment inside a run — now one keyword reaches both halves, on
  the subprocess and in-process lanes alike.

### Removed

- **`Fanout.also`, the teaching tombstone.** The method existed only to
  raise "parallel().also(...) is gone" — kept from the ban on anonymous
  work while muscle memory caught up. Pre-1.0 with no external users,
  current-state clarity wins: an `AttributeError` says the method does not
  exist, which is the truth. (The `[tool.footman] plugins` config-key
  refusal deliberately stays: config keys fail silent, so that one still
  earns its keep.)

- **`context.color_on()`.** Its only caller was toolroom's hosted lane, and
  toolroom 0.3.0 answers the colour question from the seam instead — the
  environment for the ambient tier, its own `.opts(color=)` for the decided
  one. The seam between the packages is now environment variables and
  per-call options, with no code dependency in either direction; pair with
  toolroom >= 0.3.0.

### Fixed

- **A body callee's row carries only its own sections.** The three places a
  child context was born each hand-listed which fields reset, and they had
  drifted: a callee born from a body call shared its caller's `sections`
  list, so its result row snapshotted profiling the caller recorded before
  the call ever happened. One `Context.child()` birth serves every site now
  — fresh identity and records, a copied environment, everything else
  inherited — so the sites cannot drift apart again.

- **A bare `--env-file` loads the default file, loudly.** It used to refuse
  with `--env-file: . does not exist` — the empty value a bare mention
  carries, read as a path. Presence is the question a bare mention answers:
  the default `.env` loads, and only when it is missing does the mention
  refuse, where plain absence still shrugs. The default is declared on the
  option now, so `--help` renders it instead of hand-written prose.

- **A curl-backend download is never reported "cached".** The receipt derived
  "cached" from "no validators came back", and curl offers none by design —
  so with `backend = "curl"` every fetch, the first one included, read as
  cached in the grid, `--json`, and `recording()`. Downloaded and cached are
  two facts now, answered separately by every backend.

- **The collector actually tends the fetch cache.** `fetch()`'s docs always
  promised it; the sweep only ever visited manifests and timing history. The
  `fetch/` room now ages the same way — idle pairs out after the same window,
  orphan sidecars alone — and a revalidated serve touches the pair's mtimes,
  so a file fetched daily never reads idle just because the server kept
  saying 304.

- **A schema bump rewrites an unchanged tree's manifest.** The rewrite
  guard compared only the tree hash, so upgrading footman left every
  old-schema manifest on disk forever: each <kbd>Tab</kbd> refused it,
  spawned a rebuild that "succeeded" without writing — the fresh manifest
  hashed identically — and paid the full cold bound. Three seconds per
  keystroke, in every previously-visited directory, until the tree happened
  to change. The schema is part of what the file *is*, not part of what it
  describes, and now joins the guard; each directory heals on its first
  rebuild after this fix.

### Changed

- **`--plugins` groups by distribution, and the Summary prints once.** The
  entry-point record cannot carry a description, so every entry a package
  ships used to repeat the same distribution Summary — four identical cells
  for footman's own four. The Summary now heads the package's line; an
  entry describes itself only where footman genuinely knows it — a mounted
  entry from its landed tree, a declared built-in from the tree it
  advertises (the brand vouches for importing its own declarations) — and a
  plain unmounted entry shows its state alone, because importing unmounted
  third-party code could crash a listing. Long mount lists cap at three
  addresses (`+N more`), a family mounted piecemeal speaks with its
  advertised help rather than an arbitrary member's docstring, and a
  single-task plugin's line is the task's own.

- **A plugin with no tasks reports `mounted`.** `footman.profile` lands
  hooks and an option, no tasks — the report read only tree provenance and
  called it `(not mounted)` while its contributions rode every run. The
  mount now stamps every contribution with its entry-point identity, and a
  riding plugin shows the plain word: there is nothing to say "at" about.

## [0.36.0] — 2026-08-09

### Added

- **`App(builtin=…)` — tasks built into the product.** A branded CLI used to
  be empty outside a project, and the tasks someone needs *before* a project
  exists — log in, create a project — were exactly the unreachable ones. The
  brand names `footman.tasks` entry points (strings, never live objects: a
  name rides the refresh child's argv where an object cannot), and they
  become the base of the tree exactly when discovery finds no project task
  files. The ladder is **project > user > built-in**: the user tasks file
  overlays the base, and a project ignores it outright — nothing is
  privileged and nothing is lost, because the set is an ordinary entry point
  a project mounts like any other (`plugin("acme.global")`). Naming a
  built-in inside a project teaches exactly that mount; a brand naming an
  uninstalled entry point is refused naming the brand; `--plugins` reports
  the set as `built in`.

  Completion follows. Outside a project, <kbd>Tab</kbd> answers from one
  manifest shared by every project-less directory — keyed by the brand, its
  version, and the builtin names, never by cwd — so the cache is cold once
  per brand upgrade rather than once per directory, and the first press in
  a fresh directory still answers (the detached child rebuilds the shared
  manifest from the baked names). The manifest schema is now 5; caches from
  earlier versions are rebuilt on the first press after an upgrade, never
  walked.

- **`fm new` writes a starter tasks file** — the first thing to run in an
  empty directory, and footman's own first built-in: the `fm` command
  declares `builtin=("footman.new",)` exactly the way any branded CLI
  would, which is why `fm new` answers where no tasks exist at all. It is
  brand-aware through the configured world — a branded CLI scaffolds its
  own filename and teaches its own command — and it refuses to overwrite a
  file that is already there. Inside a project the ordinary remedy applies:
  mount `footman.new` from the root tasks file to offer it there too.

### Changed

- **BREAKING: the user tasks file is the cascade's outermost rung.** It used
  to answer only where a project had none; personal tasks now ride
  everywhere — into projects too — and anything nearer shadows them, the
  nearest-wins reading the cascade always had, one rung further out. A
  uniform project surface stays personally extendable without touching the
  repo. The rung claims no root: outside a project `inv.root` is `""` and a
  `cwd="root"` task runs where the command was typed — the fallback had
  accidentally made the config directory the root, which no personal task
  ever meant — while `cwd="taskfile"` is the config directory, the file's
  real home. `-f` stays total control, no rung; and a single-file project's
  PEP 723 script environment still engages, because the script rule reads
  project files first.

- **The composing verb is `mount`.** A plugin or module is *mounted* into
  the tree — `plugin()` and `include()` are the mounting calls, and `into=`
  is the mount point — where messages and docs used to say "pull", a word
  git already owns and plugin systems don't use. The function names are
  unchanged; what moves is the prose everywhere it speaks: `--plugins`
  reports `mounted at …` / `(not mounted)`, the collision refusals say
  "mount only one of them", and the composing page teaches the one verb
  that carries both opt-in and placement.

## [0.35.0] — 2026-08-09

### Fixed

- **A branded CLI's tasks file marks a project root.** The project markers
  carried a literal `tasks.py`, so a brand that renames it — `App(tasks_file=…)`
  — had its own file go unrecognised. A project whose *only* marker was
  `acmetasks.py` (no `pyproject.toml`, no checkout, no `acme.toml`) was not
  found as a root, and the cascade started in the wrong directory: standing in
  a subdirectory, the root's tasks were simply absent. Both brand-derived
  filenames now mark a root, which is what `acme.toml` already did.

  The tasks filename used here is the brand's, never the `tasks` config key
  that can override it per project — that key lives in a config file, and
  finding config needs the very ceiling this computes.

- **An in-memory `Runner` drive records no timing history.** The
  estimate/record path treated a synthetic tree like a real invocation and
  wrote `*.times.json` — keyed by an ephemeral test directory — into the real
  user cache from a consumer's own test suite. In-memory runs now pollute no
  cache, times included, the rule `-f` runs already followed.

- **A collection-valued plugin global binds like a task option.** A
  `list`/`dict` annotation parsed fine and described correctly in the
  manifest, then bound wrong: mentions were last-wins and commas rode through
  whole, so `--tag=a --tag=b` bound `"b"` and `--tag=a,b` bound the string
  `"a,b"`. Mentions now accumulate, each value comma-splits unless `nosplit`
  opts out, and a mapping takes `KEY=VALUE` pairs (`dict[K, list[V]]`
  accumulates per key) — every part through the same strict coercion and
  checks an `env()` fallback already ran.

### Added

- **The cascade stops at any version-control boundary, not only git.**
  `REPO_MARKERS` now covers `.git`, `.jj`, `.hg` and `.svn`. footman runs none
  of these tools and reads none of their metadata — it notices whether the
  directory is there — which is why recognising four costs no more than one.
  The gap was real rather than theoretical: Jujutsu's non-colocated mode has no
  `.git` at all, so a jj checkout fell through to the packaging fallback and
  took its cascade ceiling from the nearest `pyproject.toml`, which in a
  monorepo is the wrong directory rather than merely a vaguer one.

- **A branded CLI keeps its own things in its own place.** `App(cache_dir=…)`
  and `App(data_dir=…)` place the two folders a CLI uses. **Cache** is derived
  data — completion manifests, timing history, the collector stamp — swept by
  the collector; **data** is durable and machine-local (credentials, tokens,
  generated assets) and is never collected. The brand places each, so footman
  never guesses at a product's layout, and they are not anchored to each other:
  a product that already has a cache area can put its cache there and its data
  elsewhere. `<PREFIX>_CACHE_DIR` / `<PREFIX>_DATA_DIR` override them at run
  time, which is what lets two installations run side by side under different
  identities. Unset, they fall back to `~/.cache/<name>` and
  `~/.local/share/<name>`.

  footman **refuses to start** if the two resolve to the same directory: the
  collector deletes from the cache by age, and pointed at the data directory it
  would eventually delete credentials.
- **`footman.cache_dir()` and `footman.data_dir()`** — a task asks for the kind
  of folder it wants and gets one that exists, with no idea whether the two
  share a parent or where the CLI put them. Both create the directory, so a task
  never writes a `mkdir` of its own. The data directory is created owner-only
  (`0o700`), like `~/.ssh` — credentials are exactly what it is documented to
  hold.
- **Environment variables follow the brand.** A CLI whose command is `acme`
  reads `ACME_CACHE_DIR`, `ACME_DATA_DIR`, `ACME_CONFIG_DIR`, `ACME_CONFIG`,
  `ACME_CASCADE`,
  `ACME_NO_GC` and `ACME_NO_UV`, and its error messages name those spellings
  rather than teaching a variable that does nothing for its users. The prefix is
  `prog` uppercased, and `env_prefix=` overrides it. A branded CLI reads **only**
  its own prefix, so debugging `fm` with `FOOTMAN_CACHE_DIR` set can no longer
  relocate someone else's product — and by the same token, keeping that
  namespace clear of a product's own variables is the brand's to arrange.
- **Config files follow the brand.** `App(config_name=…)`, defaulting to
  `prog` — the machine word, exactly as the env prefix derives, never the
  display name, which is free text — gives `acme.toml`, `[tool.acme]` and the
  `~/.config/acme/` corner from one field so the three cannot drift. Two
  branded CLIs can share a repository, each reading its own settings. footman
  pins its own (`footman.toml`, not `fm.toml`) for the same reason it pins
  `FOOTMAN` over `FM`. The *user-level* config file is deliberately not
  brand-placed: it stays at `~/.config/acme/config.toml`, where a user looks
  for their own settings.
  `<PREFIX>_CONFIG_DIR` relocates that corner — the config file and the user
  tasks file together — without `XDG_CONFIG_HOME`'s side effect of moving
  every other application's config along with it.
- **footman pins its own prefix** rather than deriving `FM_*` from its command,
  and not for compatibility: `FOOTMAN_CACHE_DIR` says what it belongs to and can
  be searched for, where `FM_CACHE_DIR` is opaque. A terse command is exactly
  when to set `env_prefix` to something longer.
- **A user tasks file.** `~/.config/<name>/tasks.py` holds tasks available
  wherever there is no project — beside the user-level config file, because
  both are the user's own writing rather than anything the brand places. It is
  a fallback, not a rung: a project's cascade wins outright, so there is still
  exactly one way to get tasks into a project tree.

- **A bare mention of an option is legal, and means the caller asked for it.**
  `fm build --target` no longer errors. It binds exactly what absence would
  have bound — the same env/default ladder runs — and adds the one thing a
  value cannot carry: that someone named it. The refusal dated from before
  0.22.0, when `--target prod` was a value spelling and a bare mention was
  genuinely ambiguous; a value has been `=`-attached ever since, so `--target`
  alone cannot be reading its neighbour and the line has exactly one parse.
  A *required* option has no absence to mean, so it still refuses.
- **`given("name")` — did the caller supply this, or did footman fill it in?**
  A value alone cannot tell "the default one, please" from "no opinion": both
  hand the body the same thing. `given()` separates them, which is what makes
  a tri-state like `--profile` (write nothing / write the default file / write
  this file) expressible from one declared default.

  ```python
  @task
  def build(*, profile: Path = Path("build-profile.json")):
      if given("profile"):
          trace_to(profile)
  ```

  Supplied means the caller: an option on the line (bare or attached), a
  keyword on a body call, a piped stdin payload, an answered `ask()` prompt.
  Not supplied means footman inferred it: an `env()` fallback, which is ambient
  and answers for nobody, or the declared default. A command line and a body
  call say the same thing — `fm build --profile` and
  `build(profile=<the default>)` are both "given" — and a called task never
  inherits its caller's answer.
- **`default(fn)` — a default computed when the task runs.** A Python default
  is evaluated once, at import: fine for a constant, wrong for anything that
  depends on the machine, the environment or the clock, which used to need a
  sentinel default and a rebuild inside the body where `--help` could not see
  it. It sits one rung above the declared default (**CLI > env > `default(fn)`
  > declared**) and, like `env()`, needs a declared default to sit on.
- **`--help` shows the default and the environment fallback.** Both were in the
  manifest all along and neither was ever printed, so a reader had to run a
  task to learn what `--name` would be, and a parameter that quietly falls back
  to `$DEPLOY_ENV` said nothing about it. They print in ladder order —
  `from $DEPLOY_ENV; default: world` — with `None` printing as nothing, because
  naming absence tells a reader less than silence does.
- **`GlobalOption.given`**, the twin of `.value`, so a plugin can tell a global
  that was named from one that merely has a default.
- **`--help` says when a default is computed.** `default: 13` reads as an
  arbitrary constant when it is this machine's cores minus one, so a computed
  one is marked — `default: 13 (computed)` — and one that reads its siblings,
  which only an invocation knows, says `default computed` with no value.
- **Reaching rightwards in a sibling view is taught.** A `check(fn)` or
  `default(fn)` that asks for a parameter declared *after* it now gets a
  message naming the constraint and the fix, through `p["x"]` and `p.get("x")`
  alike. The second spelling used to answer `None` in silence and let the run
  succeed, which fed the body a value nobody chose. Reading your own name is
  named separately; an *undeclared* name stays an ordinary `KeyError`, because
  a typo is a different mistake.
- **A `default(fn)` may read its sibling parameters**, the way `check(fn)`
  already could — declare one positional argument and it receives the values
  resolved to its *left*, read-only. A default is often a function of the
  inputs beside it: a window title from the command being screenshotted, a
  report name from the target it describes. `--help` shows no default for one
  of these, because there is no invocation to read.
- **`ask()` works on a parameter that has a default**, which makes it safe to
  put on anything. The default becomes the *offer* — `version [patch]:`, Enter
  accepts — instead of a reason not to ask, and where nobody can be asked (off
  a terminal, `--no-input`, `--json`) it is quietly used. A parameter with no
  default still errors naming the flag, because there is no other answer. So a
  person gets asked and an unattended run gets the default. Naming the option
  bare skips the question: the caller has already said "the declared one".

- **A bool plugin global answers to `--no-x`.** Every task flag has its off
  spelling; a plugin's flag needed a second declaration to be turn-off-able.
  `--no-x` parses, completes beside `--x`, binds last-mention-wins between
  the two spellings, and counts as *given* — off, out loud. A bool claims
  both spellings in the collision law, so a literal `no-x` beside a bool `x`
  is refused at discovery naming both.

- **A plugin's global option reads project config.** `GlobalOption(...,
  config=True)` gives the option a config rung in the one ladder, reading
  the key named like the option from the provider's own section under the
  brand table's reserved `plugins.` child — `[tool.footman.plugins
  .acme-devkit] region = "us"` for the `acme.devkit` entry point, the dot
  becoming a hyphen because TOML's dot is its nesting operator. The section
  derives from the entry point the pull already knew; `footman.
  config_section("...")` names it explicitly (an `include()`d module has no
  entry point to derive from), and `config="key"` renames one option's key.
  Ambiguity refuses at discovery naming the remedy — a singleton reached
  through two different pulls, or two providers deriving one section — and
  a broken value is the same taught refusal core's config keys get, on
  every invocation.

### Changed

- **BREAKING: `GlobalOption(bare=…)` is gone**, replaced by `.given`. It existed
  because three outcomes need two declared values — unless presence carries one
  of them, which it now does. `footman.profile` shows the shape:
  `default=Path("fm-profile.json")` plus `if PROFILE.given`.
- **BREAKING: forwarding carries presence, and may satisfy a required
  parameter.** Both defaultless guards are gone. "A prerequisite must still be
  independently runnable" was never the rule it claimed to be — `ask()` and
  `stdin` already satisfy defaultless parameters — and refusing only pushed
  authors into giving the receiving parameter a default it did not want,
  weakening its contract when the task runs alone. The value channel is
  unchanged: everything still travels, because forwarding only what was asked
  for would drop `env()`-sourced values.
- **BREAKING: passing a value equal to a parameter's default is no longer the
  same work as omitting it.** Presence joins the dedup key, because a body that
  branches on `given()` does different work for each and would otherwise be
  answered silently by the wrong execution. Nothing else about identity moves:
  different values already keyed differently, and positional-versus-keyword
  spelling still names one execution.
- **Every boolean config key now has both CLI spellings**, so a project setting
  is a default rather than a one-way door. Five could only be set in one
  direction: `sequential` and `sort` had no `--no-` counterpart, `progress` had
  no `--progress` (its own documentation said `false` disabled the bar
  *permanently*), `uv` had no flag at all, and `input` was not a config key.
  All five resolve through one rule — **CLI > config > the default** — and two
  guards keep it that way: every boolean project key must have both spellings,
  and every key resolved from config must appear in the reference table.
- **Core's own globals resolve through the one ladder** — CLI > `env()` >
  config > `default(fn)` > declared, the same machine a plugin's options
  use, and the second resolution system (the hand-rolled jobs and colour
  ladders, the boolean switch helper) is gone. On the surface:

  - **Bad values get the pipeline's taught errors.** `--jobs=abc` answers
    `--jobs expects an integer`, `--jobs=0` answers `must be at least 1`,
    `--color=sepia` answers `must be one of always|never|auto` — one wording
    per mistake, shared with every task parameter, and refused eagerly even
    when a listing would have exited first.
  - **A broken config value refuses with the same teaching, on every
    invocation.** `jobs = 0` in config used to be silently ignored; the
    `sort` key's validate-even-when-`--sort`-decides rule now covers every
    config-backed option, spelled `config key 'jobs' must be at least 1`.
  - **`--color`'s default is computed, and says so.** `NO_COLOR` and
    `FORCE_COLOR` live in the declared default — the bottom rung — so
    `--help` shows `default: auto (computed)` and answers for this
    environment. An explicit `--color=` or a project's `color` key outranks
    them, exactly as before: they speak for the terminal in general, not
    for this invocation.

- **A global is bare-legal exactly when it has a default**, declared in the
  grammar table beside its metavar. That is the same rule task options follow
  (absence is legal when there is a default), so both surfaces now answer the
  question the same way instead of one keying on a bracketed metavar. `--jobs`
  and `--color` gain a bare form; `--where`, `-C`, `-f` and `--config` have no
  reading without a value and still refuse.
- **A contextual `check(fn)` sees `*args` from the command line too.** The
  variadic lives outside `kwargs` on that path and inside `bound.arguments` on
  the body-call path, so the same validator saw the variadic from a Python call
  and an empty tuple from a chain.
- **An absent global option runs the same ladder a task parameter does** — env,
  then `default(fn)`, then the declared default. `env()` was accepted on a
  global's annotation and reached the manifest but was never applied, so the
  new help rendering would have advertised a fallback that never happened.
- **A bool flag defaulting to `true` is shown as `--no-x`.** Typing `--x` there
  changes nothing, so the only spelling that acts was buried in a parenthetical
  while the inert one led.
- **The space-form teaching is demoted, not deleted.** It was a diagnostic for a
  spelling outside the grammar; now that a bare mention parses, it rides along
  only on a line that fails anyway — `lint --mode strict` answers
  `expected a task name, got 'strict' … — did you mean --mode=strict?`. It never
  turns a working invocation into a refusal.
- **The documented `[tool.footman."acme.devkit"]` sub-table convention is
  retired** in favour of the reserved `plugins.` child above. It was
  documentation only — nothing shipped ever read it — and the quoted-dotted
  spelling fails silently the day someone omits the quotes, where a bare
  de-dotted name directly under the brand table can collide with a scalar
  key as a whole-file TOML parse error. `plugins.` gives provider sections
  their own namespace, where neither failure is representable.

## [0.34.0] — 2026-08-07

### Fixed

- **The stdin channel honours its annotation.** Three shapes silently
  handed the body a raw string where the type checker had concluded
  otherwise — the same failure class 0.33.0's basic-default inference
  fixed, but with no warning at all:
  - a **`NamedTuple`** parameter (it failed every test in the document
    binder — a `tuple` subclass, so `get_origin` is `None`);
  - a **`TypedDict`** parameter;
  - any **coerced scalar**, so `Stdin[int]` was the text `'42'` and
    `Stdin[Colour]` the text `'red'`. Validation ran on that path but
    coercion did not, so a wrong value was refused while a right one
    arrived as the wrong type.

  All three now bind. `NamedTuple` and `TypedDict` are records like a
  dataclass — one helper decides what a record is, so the binder and its
  gate can no longer disagree — and the scalar path coerces the way the
  `stdin("field")` branch beside it already did.
- **`echo 42 | fm task` works.** A coerced scalar ignores one trailing
  newline, which is the shell's punctuation rather than part of the
  value; piping a value in is the point of the channel, and every
  validated scalar used to fail on it. Text parameters are unchanged: a
  `Stdin[str]` still receives the stream verbatim, newline included.

### Added

- **`tuple[T, ...]` works as a parameter**, on the command line and from
  stdin. It shares every bit of `list[T]`'s grammar — one or many, comma
  or repetition — and differs only in the container the body receives,
  which is the point: coercing it to a list would hand back a type the
  annotation does not name. The return side has always described this
  shape, so the input side rejecting it was an asymmetry between footman's
  own channels.

- **`set[T]` and `frozenset[T]` work as parameters**, on the command line
  and from stdin. Every collection shares one grammar — one or many, comma
  or repetition, `nosplit` and all — and differs only in the container the
  body is handed, which is now the only thing the annotation decides. A
  bare `set`, `frozenset`, `list` or `tuple` means a collection of `str`.
  `set[Spot]` where `Spot` cannot hash is refused by name rather than
  raising a bare `TypeError` from inside binding.

  Sets already serialised (sorted) on the way *out*, so refusing them on
  the way in was an asymmetry in footman's own channels.

- **A fixed-arity shape binds from a JSON array.** `Stdin[tuple[int, int]]`
  fed `[1, 2]` used to hand the body the *string* `'[1, 2]'`; it now binds
  `(1, 2)`. A JSON array is the grouped stream in another dress, so both
  channels group it the same way and report the same errors — including
  agreeing that `[1, 2]` for a `tuple[str, str]` binds `("1", "2")`,
  because `--v=1,2` does. A *named* record still binds from a JSON object,
  which is the spelling its field names earn it.

- **`hidden` on a parameter** (and `Hidden[T]`), meaning what `hidden=True`
  already means on a task: out of the listings a human reads, and out of
  nothing else. It still binds, it still completes, `--describe` marks it
  rather than dropping it, and `--all` reveals it. For a deprecated flag
  kept working but unadvertised, a debug switch, or a flag a wrapper
  script passes. See [Typing](https://willemkokke.github.io/footman/typing/#keeping-a-parameter-out-of-the-listings).

- **`--describe` says what a pipe expects.** A whole-document parameter
  used to carry the *name* of its type and nothing else, so a caller
  learned the JSON was called a `Config` and had to guess the rest. It now
  carries the structure: each field's name, its types (or `choices`), its
  container if it holds one, its own shape if it is a record, and whether
  it is required. A recursive record is emitted by name, since it appears
  in full higher up. See [JSON](https://willemkokke.github.io/footman/json/#the-shape-a-pipe-expects).

  Every record is described the same way, whether it is a dataclass, a
  `NamedTuple`, a `TypedDict`, or a class with an annotated `__init__` —
  where before only a dataclass was described at all. That was the
  manifest holding an opinion about records the binder had already given
  up.

- **A document parameter keeps its command-line spelling.** A record whose
  fields are all scalars can be typed as well as piped —
  `--cfg=name,port`, and the command line wins when both are given. Only a
  shape that holds another record or a collection is pipe-only, because no
  token can say where the inner one ends. Before, every dataclass document
  was pipe-only and every `NamedTuple` was not, which was a difference
  between spellings rather than between shapes.

- **Help prints a shape's own slot names.** `--size=width,height` rather
  than `--size=VALUE ...`, with `repeatable in groups of 2` for a
  container of them — a grouped shape is one value, so the `...` no longer
  claims otherwise.

- **Fixed-arity shapes fill from the command line.** A `NamedTuple`, a
  dataclass, a plain class, or a `tuple[X, Y]` now binds from grouped
  values — `--at=1,2`, `--size=800,600` — where before the whole text
  reached the constructor as one argument and it refused. One rule reads
  all four spellings: whatever the signature declares as its positions is
  the group.

  Values still accumulate from commas and repetition into one stream, and
  the declared arity chunks it, so a container of shapes reads either
  spelling — `--p=1,2 --p=3,4` and `--p=1,2,3,4` are the same two points.
  A remainder is taught rather than rounded: `--p=1,2,3` says it takes
  values in groups of 2 and names them. Prefer a `NamedTuple`, because a
  named shape names the slot that is wrong (`height expects an integer`)
  where a plain tuple can only count it (`value 2 expects an integer`).

  A shape footman groups is one it can **type** — every dataclass,
  `NamedTuple` and annotated `__init__` by construction. An unannotated
  constructor (`uuid.UUID`, `Decimal`) keeps its single-token spelling
  rather than having positions invented for it.

  All of it is refused at *parse* time, like every other typed value: the
  arity and the per-slot types are in the manifest, so nothing has to run
  for `--size=800,tall` to be taught. The binding-time check stays as the
  backstop for the channels the splitter never sees — stdin, `env(...)`,
  and a direct call.

- **A generated `[tool.footman]` reference.** `_config.KEYS` holds the
  recognised keys as data — name, accepted values, default, meaning — and
  `fm docs.config` renders them as the table Configuration includes, so
  the page can neither invent a key nor miss one. It found a real gap on
  the first run: **`cwd`**, a validated run-wide working-directory policy
  (`taskfile`, `root`, `asinvoked`, `unmanaged`, or an absolute path), had
  never been documented anywhere — the only list of keys was prose in a
  module docstring, and prose is what nobody updates.

### Changed

- **Manifest schema 4.** A parameter spec gained `group` (a shape's
  positional arity and per-slot types), gained `hidden`, and `shape`
  became an object describing the document rather than a bare type name.
  Stale completion caches refresh themselves; a consumer reading `shape`
  as a string reads `shape["name"]` instead.

- Three global help strings now carry the whole truth, which reaches
  `--help`, completion menus and the generated reference table at once:
  `--jobs` names the floor of 2, `-s/--sequential` says it reaches
  `parallel()` blocks inside task bodies (not just the chain), and
  `--plugins` says it lists installed plugins *pulled or not* — the
  comparison that makes the flag worth running.

## [0.33.0] — 2026-08-07

### Added

- The docs resolve **toolroom's symbol inventory**, so the pages that
  describe the seam link into it instead of describing it twice.
  toolroom's site resolves footman's the same way — the two sites are
  navigable in both directions, while neither package depends on the
  other.
- `mark` is importable from the package root. It was public all along —
  documented on the API page and served by the lazy loader — but missing
  from `__all__`, so `from footman import mark` type-checked while the
  export table denied it. Found by the generated API page refusing to
  build over the mismatch.

### Fixed

- The generated global-options table writes values `=`-attached
  (`--jobs=N`), matching what `--help` prints. It had rendered them across
  a space (`--jobs N`) — a spelling the splitter refuses with exit 64 —
  while its own docstring promised "the same words `--help` prints … can
  never drift from the runner". Every page that regenerates the table is
  corrected with it.

### Changed

- **Breaking:** a parameter with no annotation but a **basic default** is
  now typed by that default: `port=8000` binds an `int`, `ratio=1.5` a
  `float`, `name="app"` a `str` (a `bool` default was already a flag).
  Previously the default arrived as `8000` and the supplied value as
  `'99'` — one parameter, two types, decided by whether anyone typed the
  flag — while every type checker footman gates on had already read the
  default as `int`. A bad value is now refused eagerly, with the taught
  message an annotated parameter gets, and the `--json` catalog declares
  the type. The rule is to infer exactly where Python's own inference is
  definite: `None` defaults, containers empty or not, `Enum` members, and
  parameters with no default at all are unchanged — the raw string still
  reaches the body. A body doing string operations on an int-defaulted
  parameter is the one thing that changes underfoot.

- **Breaking:** the machine-surface kind of a positional parameter is
  `positional` — in the `--json --list` catalog, in `--describe`, and in the
  taught error (`missing required positional(s)`). Previously the kind was
  spelled `argument`, which put one concept under three words across prose,
  catalog, and error. Manifests rebuild themselves (schema 3); a consumer
  matching on `"argument"` updates one string.

## [0.32.0] — 2026-08-05

### Removed

- **Breaking:** the tools bridge and everything behind it — `footman.tools`,
  the generated stubs, the stub machinery, `tool-history/`, and the weekly
  refresh workflow — moved to
  [toolroom](https://willemkokke.github.io/toolroom/), footman's companion
  package. Respell `from footman import tools` as `import toolroom` (or
  import handles by name: `from toolroom import git`) after
  `pip install toolroom`; calls detect the footman host and route through
  `run()` exactly as the bridge did. The `footman.tools` plugin entry point
  is gone with it, and there is no shim. footman's own gate runs through
  toolroom as a dev dependency; the runtime keeps zero dependencies.

## [0.31.0] — 2026-08-05

### Added

- **Structured returns: the return annotation is the output contract.**
  A task declaring `-> Affected` (a dataclass, `TypedDict`, `NamedTuple`,
  container, `Literal`/`Enum`, scalar bridge type, or `T | None` of those)
  gets an output schema derived from the annotation — no decorator, no
  schema language — baked into the manifest beside the param specs. An
  annotation outside the set declares nothing and changes nothing:
  "describable" is a subset of "returnable", never a new gate. Bare `int`
  stays the exit-code channel; `Stdout[T]` describes `T`.
- **`returned_schema` and `returned_mismatch` in the `--json` envelope.**
  A declaring task's entry carries its schema (footman's compact native
  form) beside `returned` — data and how to read it, one call — and every
  reported value is walked against the declared shape: a break (renamed
  key, wrong type, undeclared extra) warns on stderr in every mode and
  rides the entry as a `returned_mismatch` note naming the first broken
  path. Loud but local, like `returned_error`: the value still serialises
  and the exit code never moves. Additive to schema 1.
- **`fm --describe[=ADDR]` — the contract without a run.** One JSON
  document with every task's params and its return shape rendered as JSON
  Schema (2020-12), plus the docstring's `Returns:` prose. A task address
  answers with one entry; a group address answers for its whole subtree
  (the prefix-names-a-subtree rule, so no wildcard syntax is needed), and
  a runnable group's default alone is its real `group.default` address.
  Sorted by address, hidden tasks included and marked, dynamic completer
  choices dropped — built for checking into a consuming repo and diffing
  in CI, so a producer-side rename becomes a visible diff at integration
  time. The rendered schema is contract, not presentation.
- **`Returns:` docstring sections join the surface.** Google, NumPy, and
  Sphinx `Returns:` prose is parsed (`footman.docstrings` grows a
  `returns` field), baked as `returned_doc`, shown by `--help` on a
  `returns:` line beside the declared shape, and rendered by the markdown
  exporter with a field table on task pages.
- **The `footman.profile` plugin — a run as a profiler trace.** Pull
  `plugin("footman.profile")` and `fm --profile check` writes the run as
  Chrome Trace Event JSON (`--profile=FILE` names it; bare `--profile`
  writes `fm-profile.json`), readable at ui.perfetto.dev, chrome://tracing
  and speedscope. One track per worker, a slice per task with `queue_ms` in
  its args, lane waits at the head of the slot, every `run()` step nested
  inside its task, task-authored sections and streams, an instant per mark,
  a flow arrow per dependency edge — and the writer times its own
  serialisation as the last slice. Anything that genuinely overlaps on one
  track (a `parallel()` block's children, a stream's windows) renders as
  async spans rather than mis-stacked slices.
- **Profiling from inside a task: `section()`, `stream()`, `mark()`.**
  `with footman.section("resolve"):` times a block on the task's own
  timeline (nested blocks nest); `footman.mark("…")` drops a labelled
  instant; `footman.stream("ci")` opens a named parallel timeline where
  overlap is legal, with bracketing (`with ci.section("poll"):`) and
  retroactive (`ci.section("build", start=t0, end=t1)`, datetimes or epoch
  seconds) forms — the latter places a window learned after the fact, a CI
  check's real run, by wall clock, even before the run began. Records are
  `Section`s on the task's result row, in `--json` as `sections` with
  task-relative `at_ms`, whether or not a profile is written.
- **`after` in the `--json` report.** A row with prerequisites lists the
  addresses it waited for — the plan's edges, what the profile draws its
  arrows from. Steps carry `at_ms`, their placement inside the task's span.
  Additive to schema 1.
- **`GlobalOption(bare=…)` — value-optional plugin globals.** `bare=` names
  what a bare mention means (`--profile` vs `--profile=out.json`), the
  grammar footman's own `--install-completion` speaks; the value runs the
  ordinary coercion pipeline, and a word following the bare form teaches
  the `=`-attachment. Meaningless on a flag, refused at registration.
- **Steps know when they started.** A `run()` step's `Result` carries
  `started` on the run's clock, so a step places inside its task's span —
  what the profile's nesting and the envelope's `at_ms` read.
- **Children join the profile.** A profiled run exports `FM_PROFILE_DIR`
  to every task's environment; any child may drop Chrome-trace fragments
  there (`ts` in epoch microseconds, its own `pid`) and the writer embeds
  each as its own process group, shifted onto the run clock. A malformed
  drop is named on stderr and skipped, never fatal.
- **pytest joins out of the box.** footman's pytest plugin, seeing
  `FM_PROFILE_DIR`, records every test's setup/call/teardown and drops the
  fragment on session finish — under xdist, once per test from the
  controller, workers as named tracks. A profiled `fm --profile check`
  shows the suite as thousands of individual test slices beside the
  runner's own tracks; an unprofiled pytest pays one environment read.

### Fixed

- **`tools.list` never claims a present tool is absent.** A tool that
  passes the installed filter but whose `--version` read fails (a stalled
  spawn, a timeout, output with no version token) now renders the
  diagnosis — `unreadable (timed out after 30s)` — instead of the false
  `not installed`. Presence and readability are different facts, and the
  version probe already knew which one failed.

## [0.30.0] — 2026-08-05

### Added

- **`lane_waits` in the `--json` report.** A task row whose lane claim
  actually waited records which lanes serialised it and for how long
  (`[{"lane": "cspell-cache", "waited_ms": 812.4}]`; the claim's own label —
  a named lane, `serial`, `exclusive`, `console`). "Why was this run slow"
  is now answerable from the report; a claim granted on arrival records
  nothing. Additive to schema 1.
- **`.argv` on every tool and verb — build the command line instead of
  running it.** Insert `.argv` right before the parentheses:
  `mkdocs.gh_deploy.argv(force=True)` hands back an `Argv` — an ordinary
  `list[str]` of the raw tokens — with the same flags, completion and type
  checking the running call has. Tokens pass on as plain Python
  (`run(cmd)`, `uv.run("--", *cmd)`); at a shell boundary the caller names
  the shell — `cmd.posix()` (`shlex` quoting) or `cmd.windows()`
  (`list2cmdline`) — so remote commands nest one quote-layer per hop
  instead of by hand-written `'"'"'` runs. The chain position follows
  `.opts()`: valid anywhere before the call, documented right before the
  parentheses. Built lines are colour-free and lead with the tool's name,
  not a resolved local path.
- **`Result.to_argv()` — what a call ran, as re-quotable tokens.** The
  executed argv of any spawned `run()` or bridge call, as the same `Argv`
  value (`r.to_argv().posix()` for a receipt headed elsewhere); in-process
  calls and command *strings* never had separable tokens, and the error
  says so. Named apart from `.argv` so `git.push().argv` — a build that
  would have executed — stays an `AttributeError`.
- **A bare container in a positional is a taught refusal.** A `list`,
  `tuple`, `set`, `frozenset` or `dict` passed positionally — to a bridge
  call or inside a `run()` list — used to stringify into the single token
  `"['a', 'b']"` and fail late at the tool. It now refuses at the call,
  naming the meant spelling (`*value`, `**value`); a bare `Argv` refuses
  with its own lesson, `*cmd` for tokens or `cmd.posix()` for one quoted
  line, since one positional slot cannot say which was meant.
- **Generated stubs type the build path.** Every verb is now a class
  parameterised by what its call returns, so `.argv` re-spells the same
  signature over `Argv` — flag typos and bad values are errors in the
  build path too, and `.opts()` after a verb now type-checks (previously
  a `MethodType` dead end, as the docs' own
  `uv.pip.install.opts(input=…)` example was).
- **`fm tools.restub` — re-render every stub from checked-in history.** No
  tools read, no network: for when the *renderer* moves while the readings
  stand, so a template change reviews apart from version drift.
- **`.at(path)` on every tool — the identity channel.** Rebinds a handle
  to an executable while keeping everything else: verbs, bound flags,
  policy, the typed surface, the shown name. `tools.python.at(venv)` is
  that venv's interpreter carrying python's whole stub. Which executable
  runs is never policy, so it does not ride `.opts()`; and because the
  in-process lane runs the current interpreter, an `.at()` handle always
  spawns — `in_process=True` on one is a taught refusal.
- **`--all` (`-a`) — the listings, hidden rows included.** One flag across
  `--list`, `--tree`, global help and group help: show everything, not just
  what you are meant to type.

### Changed

- **One broken annotation degrades one parameter, not the task.**
  `eval_str` is all-or-nothing, so a single unresolvable name used to cost
  every parameter its types, choices and completion — and the pass-through
  warning repeated once per parameter, every invocation. The fallback now
  evaluates each annotation on its own: siblings keep their grammar, and
  the warning is one line naming the task, the parameter and the
  underlying error. Assertions matching the old `is not a usable type`
  text for *string* annotations should match `did not resolve` instead;
  the old wording still covers non-string oddities.
- **coverage 7.15.3** rewords 1 description. It also restates its own description.
- **docker 29.7.0** adds `--compress`, `--cpu-period`, `--cpu-quota`, `--cpu-shares`, `--cpuset-cpus`, `--cpuset-mems`, `--force-rm`, `--isolation`, `--memory`, `--memory-swap`, `--rm` and `--security-opt`. It also rewords 12 descriptions.
- **gh 2.97.0** adds `--allow-escape-sequences`.
- **prek 0.4.12** adds `--force` and `--require-group`. It also drops `--overwrite` and rewords 4 descriptions.
- **uv 0.12.1** adds `--prerelease-package`.
- **`hidden` is a listings word, not a completion one.** A hidden task now
  completes and answers the did-you-mean index; what it stays out of is
  `--list`, `--tree` and group help. A listing is prose about what a repo
  does, and clutter there is the thing `hidden` was invented to remove;
  completion is help with a name you are already typing, and a
  machine-facing address — long, dotted, typed by hand exactly when
  something has gone wrong — is the one most worth being spelled for you.
  Nothing else moves: `--json` still reports hidden nodes marked, and the
  generated task docs still badge them.
- **The playground's opening sample lints through the tools bridge.**
  `lint` calls `ruff.check("src", fix=fix)` rather than assembling the
  command line by hand, so the first thing a visitor reads shows a typed
  wrapper turning a task parameter into a flag — and `fix=False` omitting
  one — beside the `run()` that `deploy` still needs for a project script.

### Fixed

- **A reading that lost bytes is refused, not recorded.** Captured output is
  decoded as UTF-8 because dev tools emit it whatever the OS code page says
  — but djLint prints its banner's `·` as one cp1252 byte on Windows, and
  `errors="replace"` turned that into U+FFFD. The store recorded it, the
  delta said the help text had changed, and djlint 1.43.2 was credited with
  an event it never had. Now a mangled read is taken again with the locale
  codec, which is what a tool that ignored UTF-8 was speaking; and if a
  surface still carries a replacement character, `surface_of` refuses it
  outright. That costs one release on one platform, reported as a hole and
  filled by the next run — against a store where nothing downstream can tell
  a mangled byte from a real edit.
- **The report is ordered by when work was created, not by when it ran.**
  `--json`'s items list and `inv.results` promised "the order the work was
  created" and delivered it only approximately: rows sorted by start time,
  with the request stamp breaking ties inside a 10ms bucket. Two independent
  tasks in a parallel run start in whatever order the pool hands them
  workers, so the report reshuffled between runs of the same command —
  intermittently, because the bucket hid it for a pair that shared one and
  left a pair straddling a boundary to chance. It surfaced as a flaky test
  on a free-threaded build. The stamp now decides outright, and the clock
  breaks a tie only for a row minted outside the request pipeline.
- **The playground runs its own sample again.** `run()` hands `input=` to
  `communicate()` on every call, whether or not a task feeds the child —
  and the browser sandbox's simulated child, written before there was a
  stdin to feed, took only `timeout=`. Every `run()` in the page died with
  a `TypeError` before the first `[simulated]` line, so the opening sample
  failed at `lint` and nothing downstream of it ran. The simulated child
  takes the keyword now, and a rehearsal drives the shipped driver over the
  shipped sample in CPython under `_FM_PLAYGROUND_SIM` — the sandbox tracks
  a real call surface, so the gate is where the next drift shows up, not
  the page.
- **A word groff broke across lines is one word again.** A manual read for
  a stub arrived carrying U+2010 — groff's own marker for a hyphen *it*
  inserted, never the ASCII one a literal hyphen renders as — so ssh's stub
  documented "authenticated en- cryption". Rejoining is exact rather than a
  guess, because the character says who put it there; anywhere else it
  becomes a plain `-`. The same character had failed CI two ways at once:
  ruff reads it as ambiguous, and its UTF-8 tail byte is undefined in
  cp1252, which is what Windows decodes with when a reader forgets to say
  `encoding=`.

### Removed

- **The `system` provisioning tier, and the Homebrew keg lookup it fed.**
  It named the tools read straight off the machine because fetching them
  per release was not yet possible — git and docker, latterly. docker
  fetches its own static builds now and git is read from kernel.org's
  manuals, so the tier stood empty: a rule with nothing to apply to. With
  it goes `_resolve`'s macOS preference for a Homebrew **keg** over `PATH`,
  which existed only to read host tools; resolution is now `shutil.which`
  on every platform, so a provision prefix and a venv win as they always
  did. Nothing a stub is generated from comes off the host any more.

## [0.29.1] — 2026-08-01

### Added

- **`.opts(input=…)` and `.opts(env=…)` on every tool.** The two `run()`
  parameters the bridge's policy channel didn't yet carry. `env=` is the
  child's environment exactly as `run(env=…)` means it, replaying like any
  policy. `input=` feeds the child's standard input and is **consumed**:
  stdin is consumable, so the payload is delivered exactly once however
  the handle is chained or shared — the cell rides by reference through
  every derived tool — and a second call is a taught refusal rather than
  a silently-unfed child hanging on a stdin that never comes. A dry-run
  or `recording()` rehearsal consumes exactly as the run it predicts
  would; `.opts(input=…)` re-arms with a fresh payload. An in-process
  tool has no standard input to feed, so the existing `run()` refusal
  teaches there too.

## [0.29.0] — 2026-08-01

### Added

- **`run(input=…)` feeds a child's standard input.** The write side of
  the process boundary: the string arrives whole and the pipe closes, so
  a child that reads to EOF (`uv pip install -r -`) finishes rather than
  waits; it is encoded the way the capture is decoded (`encoding=`).
  Without `input=` the child inherits the process's stdin untouched. The
  read side was always the `stdin`-marked parameter — between them a task
  sits in the middle of a pipeline without a shell on either side. An
  in-process tool has no standard input to feed, so `input=` on one is a
  taught `TypeError`.

- **`tools.ssh`, `tools.ssh_keygen`, and `tools.ssh_keyscan`.** OpenSSH
  joins the curated tools, stubbed from its own manual pages — ssh has
  no `--help`, so
  the pages are the surface. The whole surface is single-letter flags,
  which the bridge already spells (`p=2222` → `-p 2222`; a tuple
  repeats its flag: `o=("BatchMode=yes", …)` → `-o … -o …`). ssh is a
  wrapper: everything after `destination` belongs to the remote
  command, so the call's flags precede the positionals, and the remote
  command itself rides as a positional — the transport is the tool,
  the command is its payload. `ssh -V` (the whole version surface,
  answered on stderr) backs `installed_version()`; ssh-keygen has no
  version output of its own and ssh-keyscan none either, so the ssh
  release answers for all three. Their option histories are backfilled
  across the portable release line.

- **The man tier reads any published manual, not just git's.** Where a
  `kind="man"` tool's pages live is now the driver's `Manual`
  descriptor — index listing, archive name, page members — with git's
  kernel.org fetch as the first instance and OpenSSH's portable
  release tarballs as the second. Manuals with all-short options
  (mdoc) extract fully: short-only flags key through the ordinary
  `shorts` policy, a two-token head reads its bare metavar, and the
  Go-style single-dash fallback no longer runs against manuals — it
  fabricated options out of ssh-keygen's `-Y find-principals`-style
  verb words.

### Changed

- **Breaking: `--dry-run` is a rehearsal, not a parse echo.** The run
  proceeds: task bodies execute with their bound arguments, and
  everything footman owns and records — `run()` calls, tools, deferred
  steps — is faked into plan-line receipts instead of executing. The
  lifecycle fires (a hook is part of "what would happen"), `confirm=`
  gates are assumed yes with a note, and the prompt layer is unattended
  (defaults answer; a prompt with no default fails loudly). `--json
  --dry-run` answers in the ordinary items envelope; the separate
  `plan` envelope is gone. A typo'd chain still refuses with exit 64
  before anything runs. Off-the-record calls (`recorded=False`) still
  execute — they are how a task learns, and faking the learning would
  corrupt the recorded story downstream.

- **Breaking: `lanes=` refuses anything that is not a `Lane` handle.**
  A string in `@task(lanes=…)`, `.opts(lanes=…)`, or a step maker's
  `.opts(lanes=…)` is a taught `TypeError` at the declaration site —
  handles, never strings, so a typo is an undefined name instead of a
  crash in the arbiter mid-run.

### Fixed

- **A missing executable is a taught error.** `run()` — and so every
  `tools.*` call — raises `CommandNotFound` when the program does not
  exist: the message names the executable, says it is not installed
  here or is misspelled, and points at `@requires_tool(…, reason=…)`
  so the task lists as unavailable instead of failing mid-run. It
  subclasses `FileNotFoundError`, so existing handlers keep catching
  it, and `nofail=True` does not silence it — no command ran, so
  there is no exit code to accept. A `cwd=` naming a directory that
  does not exist keeps the honest OS error rather than blaming the
  tool.

- **The report's order is deterministic under parallel bursts.** Task
  rows seat chronologically by start, as always — but starts landing
  inside the same instant are worker-thread noise, so the tie now
  breaks on request order: plan order for scheduled segments (minted
  topologically, so a prerequisite always outranks its dependent), the
  written line for a `parallel()` block's calls, the call moment for
  body calls. A rerun can no longer shuffle the summary.

- **A share copies what a body-claimed execution reported.** The
  claimed body call now hands its sealed row to the shared cell before
  the future resolves, so a later request's `shared` row carries the
  reviewed title, reported value, and audit — previously it copied
  nothing when the first execution came from a body call rather than
  the scheduler, and it could seat before the execution it joined.

- **Dry-run receipts carry addresses.** A faked `run()` record now
  gets the same tree-derived address its real twin would, so a
  rehearsal's `--json` items are prefix-filterable like a run's.

## [0.28.1] — 2026-08-01

### Changed

- **A `with parallel()` block refuses to run over a dead item.** A step
  item built inside the block but never handed to it — not queued with
  `p(item)`, not called in place — is a forgotten hand-off: building
  runs nothing, so the work would silently not happen. The block now
  raises before running anything, naming the dead items.

- **`TaskResult.pristine` is now `body_returned`.** The field holds
  what the body handed over — the value dependents and body callers
  receive, snapshotted at the body's exit — and the new name says so,
  pairing visibly with `returned`, its reported twin that a reviewer's
  `set_returned` rewrites. An adjective read like a flag; a noun reads
  like what it holds.

### Fixed

- **`wrap_task`/`wrap_bind` spans survive nested executions.** A body
  call with a different binding runs its execution inline on the
  caller's thread; the wrapper's span state is now a stack, so the
  inner span closes with the inner record and the outer span still
  closes with its own — previously the outer span was silently lost.

## [0.28.0] — 2026-08-01

### Changed

- **Breaking: `--json` speaks one flat list — `items`.** The envelope's
  `results` array (rows with nested `steps`) is now `items`: tasks and
  steps as one creation-order list, every record carrying its `address`,
  the tree recoverable from prefixes without making a reader recurse. A
  row has `"task"`, a step has `"command"` — the kind test — and looking
  a task up by name returns a list by contract, since one label can name
  distinct work (distinct by address). Nobody consumes `--json` yet, so
  the schema field stays `1`: footman launches with the shape rather
  than migrating to it.

- **`parallel()` returns sealed records.** Each entry of the returned
  list — and of the block form's own list — is a `Result`: named,
  addressed, timed, and *being* its exit code, so every code-reader
  (`== [0, 0]`, truthiness, sums) keeps working unchanged. The report
  stops speaking a poorer language than the records it carries.

- **Task grain at normal verbosity.** A green step's receipt shows under
  `--verbose` (and for uncaptured, live steps, whose output needs its
  label); a FAILED step always shows, with its output replayed and — when
  anyone touched the verdict — its audit on one line (`body 1 → review
  reformatted_is_fine 0 → observe budget 3`). Green is collapsible;
  failure is never hidden.

- **Breaking: diverging forwards make two nodes, not a refusal.** One
  identity rule, everywhere: a piece of work is its declaration, its
  option overrides, and its resolved arguments. Two dispatchers
  forwarding *different* values to one shared prerequisite used to be a
  `ChainError` ("run the forwarding tasks separately"); now each gets the
  node it meant — the plan splits them exactly as the execution layer
  always keyed them — while same-value dispatchers still share one
  execution. What you meant is what runs, silently and correctly, on
  every path.

- **Breaking: nothing anonymous runs — `parallel()` and `run()` take only
  tasks and steps.** footman only schedules, records, and safely cancels
  work it owns, and a bare callable is a stranger: a lambda, a
  `functools.partial`, or a plain function handed to `parallel()` is now a
  taught error naming the one-word fix (`step(fn)(…)` — the lift buys a
  receipt), `run(callable)` is retired the same way (in-process work is a
  step; the tools bridge keeps its in-process lane), and `p.also` is gone
  — a lifted item joins a block through `p(item)`. The partial-of-a-task
  footgun that silently defeated interception dies with it. footman's own
  gate migrated the same day it broke: `check` now fans out through the
  block form, and the four checker functions are named steps with real
  receipts instead of borrowed `__name__`s.

- **Breaking: a committed record is sealed — everywhere.** `Result`
  refuses attribute writes after construction (amend verdicts in the
  review window; veto with `fail()`), so a step observer holds the same
  read-only truth a row observer does. The seal is static too: the
  record's fields are read-only properties, so a write is a type error
  before it is a runtime one. And a generator step that yields
  a *value* is a taught error: yields are checkpoints, the draft is what
  a yield evaluates to, `return` carries the value out.

- **Breaking: `post_task` observes a sealed record — observers see, never
  judge.** The observer's `result` view is now fully read-only:
  `set_returned` moved into the review window (`ResultView.set_returned`,
  on the draft a `pre_record` reviewer holds), where the rewrite is
  attributed in the record's audit. An observer that finds a problem
  vetoes instead: `footman.fail(reason, code)` from a `post_task` hook
  fails the task with the hook's own code, `failed_at` reads `"observe"`,
  and the audit keeps the code the work had earned. And a `shared` row now
  reports what the execution's sealed record reported — a shared answer is
  the record reused — so a review's rewrite covers shares automatically
  (previously the posts re-fired per share to the same effect).

- **Breaking: `run(step=False)` is spelled `run(recorded=False)`.** The
  off-the-record call — executed under full management, no receipt — now
  carries the record family's own word, on `run()` and through the tools
  bridge's `.opts(recorded=False)` alike. A rename, not a behaviour
  change; there is no compatibility shim, and the old keyword is a plain
  `TypeError`. First step of the work-item build
  ([the design](https://willemkokke.github.io/footman/design/)).

- **Breaking: `fail(code=0)` is refused.** `fail()` means failure, and
  exit code 0 is success — the old verbatim honouring could produce a row
  that read as ok while carrying an error. The refusal teaches the honest
  spelling: `return 0` (or a plain `return`) stops a task early with
  success.

### Added

- **Lanes: one resource, one holder.** `footman.lane("database")` makes a
  named resource to serialise on — a *binding*, shared by importing it
  (a typo is an undefined name; re-declaring a taken name is an error
  naming both sites). Claim it with `@task(lanes=(db,))` or a step
  maker's `.opts(lanes=…)`: one holder at a time, contending only with
  claimants of the same lane, granted atomically at the work's boundary
  (mid-body escalation cannot be spelled), everything unrelated running
  untouched. footman ships exactly two, built with the same call:
  `cwd_lane` (sole occupancy of the real working directory, applied with
  a real chdir for the hold) and `console_lane` (the one terminal —
  `interactive=True`'s claim, now nameable). `serial=` remains the
  all-lanes claim, and the two regimes wait on each other symmetrically;
  lane waits print the same named notes.

- **Every record carries its address.** Raw commands name by tool word
  plus verb (`release/git-push` — flags never re-identify a step, and
  `git fetch`/`git push` stay distinct across runs); owned work names by
  its function, task, or title; leaves are parse-safe (the grammar's `/`
  and `#` cannot be faked from a command token — `./ship` names `ship`,
  while the record's `command` shows the original verbatim). A tree-derived name — the path of
  requests that led to it, with an ordinal once a label repeats among
  siblings (`check/typecheck`, `build/git`, `build/git#2`) — assigned in
  request order as written, so it is deterministic across runs and hosts.
  Rows and steps both carry it, `--json` includes it additively, and an
  address prefix names a subtree. Diverged forwards read as `shared` and
  `shared#2`: distinct work, distinctly named.

- **`step()` — steps you make yourself, in three positions.** `@step` on
  a local function makes a *maker*: calling it **builds** a bound,
  deferrable piece of work (the `range(10)` precedent — nothing runs
  until `parallel()` or a direct call executes it); `with step("title"):`
  records a block of your own code where it stands, no execution boundary
  created; `step(fn, title=…)` wraps a function you didn't write. Items
  earn full records — receipt, captured output, audit, `--json` — and the
  maker carries the step's policy: `.opts()` for per-use execution
  policy, `.pre_record`/`.post_step` for its reviewer and observer. A
  generator step's bare `yield`s are checkpoints — the only places
  footman cancels it, for exactly three reasons (fail-fast, Ctrl-C, its
  own `timeout=`), each arriving as "the loop never resumes" so cleanup
  always runs. A step's return value is data, never an exit code;
  dry-run fakes deferred makers like any subprocess.

- **Every task's handle carries its own lifecycle.** `@build.pre_task`
  runs setup that belongs to build; `@build.pre_record` attaches its
  reviewer; `@build.post_task` watches its sealed record;
  `@build.pre_bind` and the `@build.wrap_task`/`@build.wrap_bind` sugar
  complete the mirror of the per-task moments. Code lives local to the
  task it governs — a rule about one task never needs a central file that
  lists everybody — and the plugin lane keeps only hooks with no task
  knowledge in them. Attachment is permanent (the task changes for every
  requester), the hooks stay plain callables, plugins remain the outer
  ring (a task's own hooks nest closest to the body), and the handle
  lane fires with or without any plugin registered.

- **`@pre_record(fn)` — a task's own reviewer, stacked on the `def`.** The
  same review window `run()` and the tools bridge gained, at task grain:
  the reviewer sees the row's draft after the body concluded — before it
  is sealed, observed, or reported — and may set `title` and `code`; the
  row's verdict follows the review, the audit names every reviewer, and a
  raising reviewer fails the task with its own error. Reviewers run from
  the inside out — the hook written closest to your function sees the
  draft first, and each one above it sees what the previous reviewers
  left. Rows gained `title`, `audit`, and the derived `failed_at`/
  `work_code` readings; `--json` rows carry `title`, `audit`, and
  `failed_at` when a review happened, additively.

- **Every step's record carries its audit** — the verdict's provenance.
  `Result.audit` lists every lifecycle moment that acted on the verdict, in
  execution order: the body entry with what the work itself produced, a
  review entry per reviewer (its code, or `None` when it only set the
  title). Two derived readings answer the common questions without
  scanning: `failed_at` names the moment a failure came from (`None` on
  success — a red tool reviewed green *is* green), and `work_code` keeps
  the code the work had earned when a later moment failed it (a green run
  failed in review shows its 0, visible rather than inferred). `--json`
  steps carry `failed_at`, additively; `work_code` is a reading on the
  Python `Result`. `AuditEntry` is public.

- **`run(..., pre_record=…)` — the review window.** Some tools speak exit
  codes that need interpreting: `djlint --reformat` exits 1 when it changed
  files, which is success for a formatting gate. A reviewer receives the
  record's draft (`ResultView`, now public) after the work ran and before
  anything is sealed: it reads what was captured and may set `title` and
  `code` — the receipt, the record, and the raise-on-nonzero decision all
  read what the review leaves behind, so the call site writes no `nofail=`.
  The tools bridge takes it through `.opts(pre_record=…)`. A raising
  reviewer fails the call with its own error; review sees what was captured
  (an uncaptured call reviews the code alone); an off-the-record call has
  no record to review — a note, not an error. The first slice of the
  record surface from
  [the design](https://willemkokke.github.io/footman/design/).

## [0.27.1] — 2026-07-31

### Changed

- **The seven undocumented framework modules are private now.** `coerce`,
  `config`, `discover`, `executor`, `manifest`, `schedule` and `split` were
  never documented and only ever imported by footman itself, but their plain
  names read as an invitation. They are `_coerce`, `_config`, `_discover`,
  `_executor`, `_manifest`, `_schedule` and `_split`; nothing documented
  moves. `TaskResult` keeps its public spelling, `footman.testing.TaskResult`.

### Added

- **`run(..., cwd="unmanaged")` works per call.** The task-level token,
  accepted on a single call: that call gets no directory opinion — a
  subprocess inherits the live process cwd, an in-process callable runs
  wherever the process is — while the task keeps `ctx.cwd` for everything
  else. The tools bridge takes it through `.opts(cwd="unmanaged")`, and
  combining it with `rel=` is the same taught error as on a task.

- **The public API verifies 100% type-complete, and the gate holds it
  there.** `basedpyright --verifytypes footman --ignoreexternal` scores a
  full 1.0 (from 0.86), `fm check` grew a `typecomplete` step whose exit
  code enforces it, and CI runs the same check — a new public symbol
  without a fully known type is a red gate, not a consumer's surprise.
  What moved to get there, all visible to a consumer's checker:

  - `.opts()` keeps the task's signature: `build.opts(atomic=True)("web")`
    type-checks exactly like `build("web")`, chaining included.
  - The `@requires` gates are identity in types — they no longer erase a
    decorated function to `Callable[..., Any]` on either side of `@task`.
  - `parallel(*calls)` returns `list[int]`; the bare block form returns the
    `Fanout` it always did, now typed (`list[int]` underneath). `track()`
    is `Iterable[T] -> Iterator[T]`; `select()` is typed over its
    documented strings-or-pairs contract; `inherited()`, `prompt()`,
    `active_status()` and `real_stdin()` state their real types.
  - The marker singletons (`exists`, `nosplit`, `forward`, `stdout`, …)
    have public, nameable types, and every marker class declares its
    attributes.
  - The module-level `task`, `group` and hook registrars carry declared
    types (`TaskDecorator`, `GroupFactory`, `HookRegistrar` Protocols); an
    AST test pins `TaskDecorator` to `Group.task` so they cannot drift.
    Registering a lifecycle hook returns the hook's own type.
  - **The option names inside `.opts(...)` complete now.** `.opts()` and
    `TaskView.set_opts` declare their eleven policy options as a typed
    `TaskOpts` set (`keep_going`, `atomic`, `cwd`, …), so an editor offers
    the names with their types and a misspelt or wrongly-typed option is a
    static error at the call site — while a dynamic caller still gets the
    taught runtime error. A parity test holds `TaskOpts` to the runtime
    validator's key set.

- **Four type checkers gate now: basedpyright, mypy, ty, and pyrefly.**
  `fm check`'s typecheck step fans across all four in parallel (CI runs the
  same four), and the tree is clean under each — none are advisory. mypy
  runs strict on footman itself and checks every test body as consumer
  code (`check_untyped_defs`; the full test-annotation pass is a planned
  follow-up); ty and pyrefly cover the library. Every platform's typeshed
  is checked from any machine: ty and pyrefly check the platform union
  (`python-platform = "all"`), and mypy — which has no such mode — runs
  once per platform (linux, darwin, win32), so POSIX-only branches like
  the fork guard and the pty recorder carry real platform narrowing
  instead of breaking the check on Windows. pyrefly runs its `default`
  preset — its strict tier demands `@override`, which is Python 3.12+
  typing and out of reach for a zero-dependency 3.11 library. The one
  place a checker is overruled is the deliberate monkeypatching in the
  process-globals router, where suppressions state what no checker can
  bless; everywhere else the code was made clean, not quieted — among the
  real fixes: a dataclass field annotation that shadowed the `ask` marker
  class with its own field, `parallel()`'s queue and status-line contracts,
  and honest narrowing in the binder and scheduler.

- **The typed contract has consumer-shaped tests now.** Two checked-never-
  executed files pin the seam where a `tasks.py` meets the API:
  `tests/typecheck_api.py` holds the positive shapes — signatures kept
  through `@task`, `.opts()` and the gates, markers vanishing at the type
  level, `run()`/`parallel()`/`select()`/`Runner` contracts — under all
  four checkers, and `typecheck_api_negative.py` holds the misuses that
  must stay static errors, with ignores both mypy and basedpyright police
  for staleness. Writing them improved `select()` again: dedicated
  plain-strings overloads, so every checker agrees a string menu answers
  with exactly `str`.

- **The typing contract is documented.** A Guide page, "Type-checking
  your tasks", states what the types promise — signatures kept through
  `@task` and `.opts()`, markers that vanish at the type level, identity
  gates, the typed context surfaces — plus the private-is-private line
  and how the promises are enforced rather than aspired to.

### Fixed

- **`.opts()` takes `None` for the options that mean "unset".** The tools
  bridge forwards `.opts()` policy straight to `run()`, which reads `cwd`,
  `rel`, `title` and `timeout` as *no opinion* when they are `None` — but the
  typed stub declared them non-optional, so a caller computing one
  (`timeout=cfg.timeout`, `cwd=None if inline else build_dir`) got a type
  error against code that ran fine. The stub now carries `run()`'s own types,
  and a test holds the two signatures together.

  A task's `.opts(cwd=None, rel=None)` agrees: `None` clears a declared
  policy for that one use instead of reaching the path validators, which used
  to raise a bare `Path(None)` `TypeError`.

## [0.27.0] — 2026-07-30

### Added

- **A task never inherits `PYTHONHOME` or `PYTHONEXECUTABLE`.** On Windows
  `uv run` exports `PYTHONHOME` pointing at the environment it launched —
  footman's own — and every child inherits it, so a console script belonging
  to any *other* Python loads that stdlib instead of its own and dies during
  start-up (`Could not import runpy._run_module_as_main`). One tool walk read
  107 tools as holes that way: every one whose launcher was a console script,
  while native binaries read clean. Both variables are now dropped from the
  environment a task *inherits*, so nothing footman spawns carries the
  caller's interpreter into a tool that has its own.

  Dropped from the inherited copy rather than at spawn, so a task that sets
  `PYTHONHOME` deliberately — through `os.environ`, `ctx.env` or
  `run(env=…)` — still hands over exactly what it asked for. `PYTHONPATH` is
  untouched: people export it on purpose and it is harmless when merely
  present.

- **A captured child gets no console window on Windows.** Windows Terminal
  hands each spawn a visible window, and a tool that interrogates the
  terminal at start-up hangs against it — so whether a read hung depended on
  which window the run was launched from. Every captured `run()` now spawns
  with `CREATE_NO_WINDOW`: a child writing to pipes has no business owning a
  console. Not `DETACHED_PROCESS`, which leaves console-hosted runtimes with
  none at all (pwsh dies at start-up, git-bash goes mute). Streaming
  (`capture=False`) and `interactive=True` runs are exempt — both are
  reaching for the real terminal on purpose.

- **A task owns its environment, and `del os.environ[…]` works.** `ctx.env`
  is a whole environment copied from the run's, not an overlay over a
  snapshot — so removing a variable is ordinary Python: it goes from this
  task's environment and from the children it spawns after, while a sibling's
  copy is untouched. It used to be a taught error for any key the task had
  not set itself, which left variables read by *presence* (`NO_COLOR`, `CI`)
  impossible to hide from an in-process tool, since there is no child
  environment to construct in that lane.

- **`timeout=` — a bound the caller declares.** `run(cmd, timeout=30)` and
  `tool.opts(timeout=30)` kill a call that overstays, and kill the whole
  process tree — the escalation fail-fast already uses — so a hung tool's own
  workers die with it. Expiry raises `RunTimeout`, a subclass of `RunFailed`
  so existing handlers keep working while a probe can tell a hang from an
  ordinary failure; under `nofail=True` it returns the `Result` instead. The
  code is 124 (the shell convention), `result.timed_out` says so, and
  whatever the command printed first is still on `stdout`/`stderr`.

  It ignores `atomic=True` — that guards against a *sibling's* failure, where
  a timeout is this call's own bound. A bound needs a process, so an
  in-process tool demotes to its subprocess twin (as a foreign `cwd` already
  forces) and `run(timeout=…)` on a plain callable is a taught error.

- **`step=False` — a call that is not part of the task's story.** A tool call
  is a *step* by default: a receipt line, a row in `--json`, an entry in
  `recording()`, its output in the task's block. Some calls are how a task
  *knows* something rather than something it did — `git rev-parse HEAD` in a
  release task — and those now say so: `git.opts(step=False).rev_parse("HEAD")`
  runs, hands back its `Result`, and reports nothing. Available on `run()` and
  in every tool's `.opts()`.

  It is unreported, not unmanaged: the call keeps the task's directory,
  environment and lane, is terminated with the rest under fail-fast, and still
  fails the task on a non-zero exit unless `nofail=True`. It also **executes
  under `recording()`**, where a step is faked — a value read is not the story
  being recorded, and faking it would corrupt the story that is.

- **`fm tools.provision --only` takes a set.** `--only=uv,bun` is what a
  gather actually drives: it installs each release itself, and the
  provisioned binaries only run the tiers — `uv` for the python and PyPI
  ones, `bun` for npm. The weekly refresh fetches those two and nothing
  else.

- **`fm tools.provision --strict` turns a failed tier into a failed run.**
  Without it the table names what did not arrive and the run still
  succeeds, which is right for a person deciding what to do next and wrong
  for a job that will read the prefix and believe it: a refresh where bun
  hit a rate limit reported `ok`, and the half-provisioned prefix went
  into the gather unremarked. The weekly refresh provisions strictly.

- **`fm tools.owed` answers what a gather would read, without installing
  anything.** A gather provisioned all 28 tools and then discovered there
  was nothing to observe, which is most weeks — a current store stays
  current until something ships. Listing is network and nothing else, so
  the weekly refresh asks first and provisions only when the answer is not
  zero: 9 seconds against ~30 downloads per platform. `uv` is still
  provisioned first, because it carries CPython's download index inside
  the binary and a stale one would report a stale newest python. An index
  that would not answer counts as work rather than quiet.

- **git is read from its manuals, and every release of them is kept.**
  git's `-h` omits about half its flags, so it has always been read from
  its manual — and a manual is not a binary. kernel.org publishes the
  pages per release, so nothing is installed and nothing is run: 343
  releases back to 2013 read in six minutes for a third of a megabyte,
  where a binary tier would have had to build or fetch each one. The
  stub now says when a flag arrived — `Added in 2.41.0` — because the
  history is finally deep enough to know.

  The pages are also the same bytes everywhere, so two machines reading
  this tier cannot disagree and the cross-platform tagging simply has
  nothing to say about git. A machine with no `man` to render them (any
  Windows box) skips the tool and says so, which is not a hole: a hole
  means a release could not be had, where this means the reader is
  missing and another machine already recorded the same bytes.

  With git fetched, **no tool footman ships a stub for is read from the
  host any more** — every one comes from something it fetched itself.
- **A tool's plugins are fetched and paired, not borrowed from the
  machine.** `docker compose` and `docker build` are not docker: compose
  and buildx are separate projects on their own release lines, found under
  the user's home rather than on `PATH`. So a walk read whatever *this*
  machine had installed and filed it under the docker release being
  observed — compose's surface of today recorded as docker 20.10's, and
  two machines with different plugins reading as a genuine per-platform
  divergence. Each plugin is now fetched with the release, paired by date
  — the one a user of that docker would have had — and read from a
  throwaway home, so the machine's own plugins never answer. An era before
  a plugin existed pairs with nothing and its verbs read absent, which is
  what they were; a plugin that is known but cannot be fetched stops the
  observation rather than recording an absence nobody saw.
- **docker's own release index is a tier.** Docker publishes a static build
  of every release, per platform and architecture, in a plain directory —
  so it is fetched like any other tool rather than read from whatever the
  machine happens to have installed. Its option history could hold exactly
  one version before this; it can now be walked back as far as the index
  goes.
- **Completion continues a comma-separated list.** Mid-list in a
  comma-splitting value, <kbd>Tab</kbd> completes the segment after the last
  comma with the typed items kept in place: `--paths=src/a.py,<TAB>`
  completes the second path (a new resolver exit code, 101, tells the bash,
  zsh, fish, and pwsh hooks to strip through the comma before the file walk;
  nushell's completer protocol can't rewrite part of a token, so it keeps
  completing the first item only). A list with a value set — `Literal`
  choices or `suggest()` — completes each item after a comma too, minus the
  values already in the list, and `suggest()` still recomputes fresh with
  just the tail as the partial. `nosplit` parameters keep their commas
  literal, in completion as at the prompt. Reinstalled hooks pick this up;
  an existing hook keeps today's behaviour everywhere else.

### Changed

- **`run(env=…)` replaces the child's environment rather than overlaying it**,
  exactly as `subprocess` means it: what you pass is what the child gets.
  An overlay could only add or override, never remove — which made the
  standard copy-modify-pass idiom silently wrong, since a key deleted from
  your copy returned from the layer beneath. Both idioms now work as written:
  `run(cmd, env={**os.environ, "CI": "1"})` to add, and `dict(os.environ)`
  minus a key to take one away. Inside a task `os.environ` *is* the task's
  environment, so the copy is exact.

  **Migration:** a `run(env={…})` passing a partial set of variables must
  become `{**os.environ, **your_dict}`, or the child gets those variables and
  nothing else — no `PATH`. `Context(env={…})` changes the same way.

- **The weekly refresh opens its pull request without arming auto-merge.**
  The gate replays every chain and regenerates every stub, which proves
  the store is *consistent* — not that the readings in it are true. A
  wrong reading passes it unchanged, and the first dispatch of the
  workflow produced one.

- **build 1.5.1** adds `--env-dir`, `--report` and `--sdist-extract-dir`. It also rewords 4 descriptions and restates its own description.
- **A group's `default` is listed first.** It *is* the group — `fm db` runs
  it, and the group's own row is described by it — so a listing that showed
  it wherever it happened to be written put the headline act in the middle,
  or at the bottom. Where it sits in the file is the author's business; where
  it sits in a listing is footman's. `--list`, `--tree` and group help all
  lead with it, `--sort` included.

### Removed

- **`run(silent=)`**, replaced by `step=`. It suppressed the display but still
  recorded the call, so a "silent" run appeared in `--json` and `recording()`
  anyway — two switches that could disagree about one call. Undocumented, and
  its only users were two tests.

### Fixed

- **Reading a tool never makes it check for a newer one.** gh runs its
  update check from any command unless told not to — a network call, and a
  banner alongside the answer. Only the version read said so, and only the
  maintainer's one: a walk asked `gh --help` and `gh <verb> --help` once
  per release, and `installed_version()` did the same from a user's task.
  Every read shares the setting now.

- **A week with nothing to read is a success.** The pre-pass skips
  provisioning, the walk and the upload when a platform owes nothing —
  which is the point of it — and the assembler then failed for want of an
  observation document from each platform. The first genuinely quiet run
  failed on its own quietness. A leg that *died* still stops the workflow,
  because a failed matrix job skips the assembler outright; what changed
  is that reporting nothing is now read as the answer it is.

- **A transient failure reading an index is retried too.** `_download`
  already retried a dropped connection; the listing path did not, so a
  `504 Gateway Timeout` on a release index ended a whole platform's
  gather — the same failure the download retry exists to prevent, one
  layer up. Every index read now follows the same rule, and `Unreachable`
  still ends the run once the tries are spent: an index that will not
  answer must never read as "nothing new".

- **A home is scrubbed however Windows spells it.** A gather set `HOME`
  to a path under `%TEMP%` and docker echoed it back with the 8.3 short
  name — `C:\Users\WILLEM~1\…` — where the string handed to the scrub had
  the long one. Compared as text those are two different paths, so
  neither was replaced and a shipped stub carried a machine's directory
  again. Each segment now matches itself or a short name for itself, and
  case is not a difference, because on Windows it is not one.

- **A tool's summary is found past a wrapped usage.** The usage stands
  between the `usage:` line and the description, and what it wrapped onto
  decided the answer: a continuation opening `[--sdist…` reads as prose and
  became the summary, one opening `--config-json…` reads as an option and
  ended the search. Two platforms wrapping differently disagreed about
  `build`'s description for that reason, and neither had found it — the
  tool says `A simple, correct Python build frontend.` two lines below.

- **A dropped download is retried.** `Remote end closed connection without
  response` says nothing about the asset — the release was there, the
  download was not finished — and it cost a refresh leg its whole
  platform, for a tool the gather never opens. Three tries with a short
  backoff, and only for failures about the connection: a 404 is an answer,
  and asking again will not change it.

- **The weekly refresh runs bash on every platform.** A `run:` block on a
  Windows runner is PowerShell by default, and the pre-pass — the gather
  job's first multi-line step — is shell script. It failed on Windows
  alone with `Missing '(' after 'if'`, and took provisioning and the
  gather down with it while the other two legs passed.

- **A wrapped usage line is no longer read as an option row.** A usage
  that wraps continues on an indented line of bracketed flags — the shape
  of an option row — and it lives in the preamble, where there is no
  `Usage:` heading for the section filter to skip. Every flag on the
  continuation was swept into whichever option came first: `build` was
  recorded with one option carrying six flags and no help, and the five it
  had swallowed missing entirely. The first weekly refresh read it that way
  and opened a pull request to record it.

- **The weekly refresh authenticates while provisioning, not only while
  observing.** Provisioning reads release indexes too, and unauthenticated
  that is 60 GitHub API calls an hour *per IP*, shared with every other
  runner in the region. The first dispatch spent it before the macOS and
  Windows legs reached bun: bun failed, and cspell and markdownlint were
  skipped for want of it. The token was on the observe step alone.
- **A walk reads the plugin home it made, not the one on `PATH`.** The
  home was derived — resolve the binary, look beside it — and the
  derivation found the wrong one: `shutil.which` reads `os.environ` while
  the walk's `PATH` overlay goes to `ctx.env`, so the lookup never saw the
  release's own directory and settled on the provisioned prefix, which
  keeps a home of its own holding the *latest* plugins. Ten docker
  releases were read with one compose between them, and the five that
  recorded it recorded the same surface five times. A caller that knows
  where it put things hands the home over now, so each release is read
  with the plugins that shipped alongside it.
- **In-process pytest runs in a parallel task again.** pytest sets
  `PYTEST_VERSION` and deletes it on the way out, and its session teardown
  re-chdirs to where it started — two moves the process-global guards
  refused wholesale, so `pytest(...)` through the tools bridge failed any
  parallel run. Both are harmless: deleting a key the task itself set
  scoped now round-trips out of the overlay (deleting a base-environment
  key stays a taught error), and a chdir to the directory the process is
  already in is a no-op, not a violation.
- **A machine with no bun says so instead of reporting holes.** bun is how
  the node tier installs, so without it every release of every node tool
  fails to install — and each failure was recorded as a hole, which claims
  those releases could not be had. A macOS gather reported 23 of them
  across cspell and markdownlint; the same walk with bun in a prefix read
  all 23 with none missing. The tools are named and skipped now, the way
  git is where there is no `man` to render its pages.
- **A tool given a home of its own is anonymised against *that* home.**
  Inside a run, the overlay that hands a tool a throwaway home writes to
  the children's environment, so the tool echoed that home while the
  process reading it still reported the machine's own — and the scrub,
  which asked `Path.home()`, matched nothing. docker's config default came
  back from the first Windows gather as a path ending
  `…\\Temp\\footman-gather-2_wvx66g\\docker-29.6.2\\home\\.docker`: a
  random per-run directory, which would have differed on every run and
  disagreed across platforms forever. The home is handed to the scrub now
  rather than discovered, and a home nested inside another is replaced
  whole.
- **A backfill is recorded but never announced.** A walk that reaches
  backwards changes the tool's surface at every step it takes, and every
  one of those steps is a change the tool made years ago — filling git's
  history announced that 2.44.0 "adds `--no-checkout`" as though it had
  happened that week. Only a release newer than anything seen before is
  news now, so the older ones are still read, folded and stubbed, and the
  changelog stays a record of releases nobody had seen.
- **A tool's defaults no longer carry the home directory of the machine
  that read them.** docker reports its config path expanded, so
  `/Users/<name>/.docker` was recorded as docker's documented default —
  in the option history, and in the `docker.pyi` that ships. Readings are
  taken with `~` in place of this machine's home, which is both what the
  tool means and the one spelling every platform agrees on: without it
  each leg of the cross-platform matrix would overwrite the last and every
  weekly run would report a change nobody made.
- **A re-reading can now correct a verb's own description.** Merging an
  observation wrote its options and nothing else, so a verb's summary and
  its positional shape were frozen at whatever the first reading said —
  git's root verb kept a fragment of a usage line where its manual says
  "the stupid content tracker", and no better extractor could replace it.
  Those fields now settle exactly as options do.
- **A verb that answers with the tool's own help is no longer recorded as
  that verb.** Asked for a subcommand it does not have, docker prints its
  root help and exits 0 — so the reading looked like a successful one, and
  `compose up` was recorded carrying docker's global options and docker's
  own summary.
- **A release asset that is a JSON is never mistaken for a binary.**
  Provenance, SBOM and sigstore files sit beside each build under names
  that match the platform; only the shortest-name tiebreak was keeping
  them out.
- **An archive whose top directory is named after the tool now extracts.**
  docker ships `docker/docker`; matching a member by name alone found the
  directory first, which failed the tar path outright and wrote a
  zero-byte binary from a zip.
- **A second playground `fm test` sees your edits.** In-process pytest left
  the editor's files in `sys.modules`, so rerunning collected the first
  run's modules until the page was reloaded; the driver now evicts them
  after every run (and skips bytecode caching, whose mtime granularity
  could resurrect a stale rewritten test inside one clock tick).

### Documentation

- **The tool version helpers are documented.** `installed_version()`,
  `read_version()` and `version_tuple()` were public and typed all along, and
  no page said so — a downstream project asked for the API in its upgrade
  report and wrote its own scraper meanwhile. The tools page now covers the
  question they answer (is the CLI new enough), why `installed_version()`
  differs from a stub header, and why a build tail ends a comparison instead
  of becoming extra digits.
- **`shell=True` on Windows is git-bash, and that eats backslashes.** The
  posix policy resolves to git-bash so one pipeline behaves the same
  everywhere, but a POSIX shell reads `\` as an escape — so a command
  carrying Windows paths lost its separators, with the fix (`shell="native"`)
  only findable in the config table. The page that teaches `run(shell=…)`
  now says it where a port will read it.
- **`Arg[T]` says which rule it is bending.** It is the one parameter that
  keeps its position despite having a default; its reference entry now names
  the default-decides rule it departs from and points at the typing guide.
## [0.26.0] — 2026-07-28

### Added

- **`with parallel()` — a fan-out written as ordinary calls.** Past two or
  three, threading arguments through thunks reads worse than the calls
  themselves, and thunks never gave the values back. Inside the block a task
  call is queued rather than run, so it has no value there (using one is
  taught, never a silent `None`); everything runs when the block ends, under
  the rules a call has anywhere else — its own row, sharing, hooks, `-s`/`-j`
  — and `p.results` hands back what each returned, in the order written. `p`
  is still the list of exit codes `parallel()` has always returned.
  `p.also(fn, *args)` brings a straggler that is not a task — a lambda, a
  plain function — into the same fan-out.
- **A task can be defined while a run is in flight.** `@task` inside a body
  is ordinary Python, and what it makes is a real task — own row, sharing,
  hooks, a place in a `parallel()` block. It lives for the run that made it
  and is swept when the run ends, so a task no listing can show never
  outlives its run; `task(fn)(args)` is the general way to run a plain
  callable as a task. A duplicate name in a tasks file stays a taught error;
  one made mid-run is numbered (`rmtree`, `rmtree-2`), so a `lambda` in a
  loop and a helper used twice are each their own work.
- **The docs have a Playground.** footman runs in the browser: the editor
  is a `tasks.py`, the prompt is `fm`, and the run goes through the same
  in-process `Runner` the testing page teaches. Python arrives via Pyodide
  on first run — nothing installs, and nothing you type leaves the page. A
  browser has no processes and one thread, so `run()` children are
  simulated — labelled `[simulated]`, exit 0 — runs are sequential, and
  failure demos are plain Python (`fail("…", code=3)`); the page says all
  of this plainly. **`fm test` runs the real pytest, in-process** through
  the tools bridge, against a second editor tab whose tests fail on
  purpose — read the diff, fix the code, run it green. The prompt
  completes on Tab — tasks, flags, choice values — through the same
  manifest walk a shell hook consults, rebuilt from whatever the editor
  says. Every runnable example in the docs carries a small "run it there"
  link that opens it in the editor together with what it builds on.
- **Every docs example is executed by the test suite.** A page reads as a
  session: its python blocks run in order in one shared namespace, so the
  first block carries the imports and later blocks build on what came
  before. A block that is deliberately an illustration says so with a
  marker in the source; everything else must import, register, and resolve
  every name — task bodies included — or the gate fails.
- **A tasks file can carry its own dependencies.** A file with a
  [PEP 723](https://peps.python.org/pep-0723/) header (`# /// script`)
  declares what it needs inline; uv builds that environment and the run
  continues inside it, so one portable file works in any folder with no
  project at all. `-f=deploy.py` applies the same rule to any named file.
  uv reads the block natively, so `requires-python` and `[tool.uv]` tables
  (sources, indexes) work as written. The file must list the runner it
  imports — the one refusal, because that environment provably could not
  run it. A project whose lockfile pins footman still owns its runs: the
  header is ignored there, mentioned only under `-v`, so a portable file
  is equally at home checked into a repo. Everything else stays soft: no
  uv, several cascading files, or an unreadable block just run as before,
  and an import that then fails says where the environment went.
- **`footman.main(__file__)` makes a tasks file its own command.** Paired
  with a `#!/usr/bin/env -S uv run --script` shebang, `./deploy.py build`
  runs the file's own tasks from any directory — same options, same
  `--help` — with no runner installed. An explicit `-f` still wins.
- **`pip install footman[uv]`** bundles uv beside the `fm` script, so a
  globally-installed runner carries the tool both handoffs need. Nothing
  imports it; it is found on disk, this runner's environment before PATH.
- **`App(dist=...)`** tells a branded CLI which distribution ships it —
  what a lockfile pins, and what a script header must declare. Unset,
  both handoffs stay out of a branded runner's way.
- **The option history knows which platforms it has looked at.** An
  observation comes from one platform and says nothing about the others, so
  each release records who read it and — beside the surface, never inside it
  — the options they looked for and did not find. Exceptions only: nothing
  recorded means nothing contradicts the option, so a stub says "Not
  available on Windows." exactly where somebody checked. A later sighting on
  that platform clears the claim; a platform that did not run says nothing
  either way. `since` and `until` are withheld where the observations cannot
  support them — a platform's own floor is not a since.
- **`fm tools.gather` and `fm tools.assemble` — observe here, fold there.**
  A Linux box cannot say what a tool's `--help` prints on Windows, so
  gathering writes a portable, self-describing document of what one machine
  saw, and assembling folds any number of them into the store in one
  process. `fm tools.refresh` is both at once, unchanged for a single
  machine. Gathering a tool a platform has never read starts at that tool's
  *base*, so new coverage begins at the version people actually run.
- **`fm tools.prepare-release`** rolls a release the way the runbook does:
  the two version files that must agree, the `--version` example the drift
  test guards, and `[Unreleased]` into a dated section with its compare
  links. Refuses when there is nothing to release.
- **The weekly refresh observes on ubuntu, macOS and Windows** and assembles
  on one runner. Its PR opens on any tree change and auto-merges once the
  gate is green; auto-release is built and gated on `vars.AUTO_RELEASE`,
  default off.
- **The tool walks gather in parallel, on footman's own runtime.** Each
  (tool, release) observation is a task of its own, fanned out with
  `parallel()` in bounded waves — the task boundary gives every observation
  its own environment, listings fetch concurrently, and the chains assemble
  from whatever order the pool finishes in. A release that will not install
  is a reported *hole* rather than the end of a tool's walk: `refresh` plans
  every unobserved release down to the floor, so it fills its own holes on a
  later run. Nine mkdocs releases primed in 27 seconds.
- **`fm --json tools.refresh` answers the release question in one field.**
  The `refresh` row's `returned` carries `release` — were any events
  appended — beside `read`, `events`, `holes`, `unreachable` and
  `wrote_changelog`, which is everything the weekly job reads.
- **The weekly refresh workflow.** Mondays 06:00 UTC (and on demand):
  provision the latest tools, refresh the histories, and open a PR only when
  something changed a command-line surface — carrying the CHANGELOG entry
  the events wrote. An index that would not answer fails the run loudly.
  The PR is pushed with a fine-grained PAT (`REFRESH_TOKEN`) so CI runs on
  it; without the secret the job refuses with directions.
- **`_toolhistory.insert` — a release can arrive at any position.** `extend`
  appends below the floor and `promote` replaces the head; this is the third
  case the format was designed for, a release belonging *between* two the
  chain already holds. Exactly one entry is recomputed — the inserted
  release's successor — however long the chain. What it buys is that
  gathering need not be ordered: installing a release and reading its
  `--help` depends on no other release, so a walk can fetch in any order,
  in parallel or across runs, and assemble as results arrive. A release that
  will not install stops being fatal to a tool's whole walk; the gap is
  filled later, and until then costs precision rather than correctness.
- **Writing plugins — the provider's guide.** One page for the whole
  authoring story: the worked provider, the `footman.tasks` entry point
  (lifecycle-only modules included), pull semantics, the
  `[tool.footman."<entry-point-name>"]` configuration convention, the two
  optional-dependency patterns, the determinism rule, and testing through
  the real pull path.
- **A `GlobalOption` completes dynamically.** A `suggest()`-marked global
  recomputes its choices fresh at <kbd>Tab</kbd>, exactly as a task
  parameter's completer does — the completion subprocess now addresses an
  option by name alongside a parameter by path.
- **Task help shows the declared globals.** A task with `@task(uses=[OPT])`
  ends its `--help` with `reads --env-file (from footman.env_files)` — the
  dependency named where the option will be typed.
- **The wiring advisories.** An option nothing is wired to read — its owner
  contributes no lifecycle hook and no task declares it — draws a warning at
  discovery; a task that declared a global in `uses=` but finished without
  reading it is an advisory under `--verbose`.

### Changed

- **`--tree` aligns its descriptions.** One description column whatever
  depth a name sits at, wrapped with a hanging indent, and no `—`
  separator — `--list`, `--tree` and group help now draw their two-band
  layout through one shared renderer, so a rule about one is true of all
  three (group help gains description wrapping in the bargain).
- **Body calls are units on the live status line.** An inline `build()` —
  fresh, or satisfied by sharing — counts and shows as running exactly
  like a scheduler node or a `parallel()` child. A task handed to
  `parallel()` is counted once, by the body-call machinery, never twice.
- **A GitHub token is used when one is offered.** `GH_TOKEN` or
  `GITHUB_TOKEN` in the environment raises the API budget from 60 calls an
  hour to 5,000 — which matters less for footman's own volume than for a
  shared CI runner, where the unauthenticated allowance is spent by whoever
  else is on that IP. Sent to `api.github.com` only, never to an asset
  download: urllib carries headers across redirects, and the CDN behind a
  release asset has no business seeing a credential. Absent, everything still
  works on the smaller budget.
- **Every curated tool's history reaches ten releases**, so the stubs can say
  when an option arrived rather than only what exists today — 102 new `Added
  in` claims across the set. eclint reaches four, because four is all that
  has been published; prek and python already reached further and keep it.
- **build 1.5.1** adds `--env-dir`, `--report` and `--sdist-extract-dir`. It also rewords 3 descriptions.
- **djlint 1.43.0** adds `--stdin-filename`.
- **markdownlint 0.23.2** rewords 1 description. It also restates its own description.
- **ty 0.0.64** adds `--exclude-scripts`.

### Fixed

- **A reading older than the extractor is read again.** `EXTRACTOR` was
  recorded against every observation from the start and nothing ever read it,
  so an extractor that learned to see more had no way to say so. Three twine
  releases sat in the store with no options at all — recorded when the tool
  died before argparse ran under today's dependencies — and the only thing
  that noticed was another platform reading them correctly and appearing to
  *disagree*, which turns a bug into a divergence report. A gather now offers
  any release whose reading predates the current generation, so the store
  heals itself when extraction improves rather than needing the record edited
  by hand. Reading each release in its own era is generation 2, and every
  observation taken before it is owed a second look.
- **A body call's output is no longer dropped in an uncaptured run.** A task
  reached by `build()` runs with its own buffer so its output stays one
  block, but that block was only ever handed to a *capturing* parent — under
  `--json`, a document run, a `parallel()` child. In an ordinary terminal run
  the parent streams, so there was nothing to hand it to and the callee's
  output went nowhere. It now goes to the terminal, the same handoff
  `parallel()` makes for its own children.
- **A gather that observed almost nothing no longer reports success.** The
  summary line was the same one a complete run printed, and the exit status
  was 0 with 330 of 363 releases unread — a document that looks foldable, and
  folding it records a platform where the tools do not exist. The counts are
  now stated together, so a truncated read shows what was missed beside what
  was found, and a run whose holes outnumber its observations exits 75. Not
  any hole: one release whose asset has gone is ordinary, and failing on it
  would teach a reader to ignore the exit code.
- **A full disk stops the walk instead of being recorded as absence.** A hole
  says *this release* could not be had; a disk with no room says nothing
  about any release, and every observation after it fails identically. An
  install failure with under 512 MB free now ends the run with the same
  "look again" code an unreachable index uses.
- **A hand-written stub is named as one.** The six shells carry the default
  provision kind, so a skipped list called them `uv tier` — a tier nobody
  fetches them from.
- **`footman.docstrings` and `footman.markdown` resolve for a type-checker.**
  Both are lazily served by `__getattr__` and both were named in `__all__`,
  but neither was declared to a type-checker — so the package advertised two
  names no editor could complete, no definition could be jumped to, and a
  consumer running a strict check saw footman complain about itself. Declared
  in the `TYPE_CHECKING` block, which never executes: a bare `import footman`
  still imports nothing.
- **The npm tier no longer needs node on the machine.** It installs through
  bun, but what bun installs is a launcher beginning `#!/usr/bin/env node` —
  and bun only stands in for node when bun itself runs a script, while the
  extractor spawns the launcher as a subprocess where the shebang is resolved
  by the operating system. A machine without node read every npm-tier release
  as `No such file or directory`: twelve cspell releases and eleven
  markdownlint ones, on a Linux box, and on every CI runner it would have been
  the same two tools lost on every leg, indefinitely. The walk now writes a
  `node` that forwards to `bun --bun`, inside the scratch directory so it goes
  when the walk does, and only where bun is present and node is not.
- **A tool's man probe can no longer open a browser.** `git help <verb>`
  honours `help.format`, which Windows defaults to html — so extracting the
  git driver opened the HTML docs in a browser tab per verb, twenty-one at
  a time, from what should be a captured read. The probe pins
  `help.format=man`: a POSIX box reads the same text it always did, and a
  box with no man viewer fails quietly into the existing empty-text
  fallback.
- **The detached refresh and collector children stay invisible on Windows
  11.** With Windows Terminal as the default terminal, a `DETACHED_PROCESS`
  child is handed a *visible* terminal window — one popped over the shell
  for every stale-manifest <kbd>Tab</kbd> and every due collection, and a
  console grandchild allocated another window of its own. They spawn with
  `CREATE_NO_WINDOW` now: a hidden console instead of none, which their
  children inherit.
- **A platform folding into an older release no longer contradicts what is
  stored.** Every release below the newest is kept as a *step*, not a
  surface, and the merge read that step as though it were one — so every
  option the arriving platform saw looked like one nobody had ever found.
  The first real Linux fold tagged 25,802 options as missing on macOS,
  across a store macOS itself had written. The merge now compares against
  the replayed surface, and restitches the two entries that describe a
  release whose surface genuinely moved.
- **A reading has to describe a tool to be recorded as one.** A launcher
  that cannot find its interpreter still prints prose and exits: a machine
  without `node` read every npm-tier release as one bare verb, no options,
  help text saying `No such file or directory`. Stored, that claims the tool
  accepts nothing — 855 options "missing on Linux" for a tool that never
  ran. Such a reading is a hole now, which is what a later run fills.
- **A stub header names every platform that read it**, rather than the first
  alphabetically — a release observed on two platforms credited one and
  quietly disowned the other's evidence.
- **One piece of work is one unit on the live line, whatever the spelling.**
  `parallel(lambda: build("web"))` counted twice — once as the thunk, once as
  the request inside it — where `parallel(build)` and a `functools.partial`
  counted once. `parallel()` now counts every child it is handed and hands
  that unit down; the first task request inside claims it rather than
  counting a second one for the same work. The claim is one-shot and never
  reaches the callee, so a thunk running two tasks counts two, and a plain
  thunk keeps the unit nobody claimed.
- **A task called from `@pre_tasks` or `@post_tasks` is refused, and says
  why.** Both moments sit outside the run, so such a call quietly became a
  plain function call — no result row, no sharing, no availability gate, and
  a task declaring `ctx` took the call's first argument into that slot.
  `pre_tasks` also runs in the child that rebuilds the completion manifest,
  where the call executed the task on a <kbd>Tab</kbd> press. The refusal
  names the moment and both ways out: edit the tree through `inv.tasks`, or
  move the call to a per-task moment, which runs inside the run and gives it
  a real task boundary. Calling a task from outside footman entirely — a
  REPL, an import of the tasks module — is untouched.
- **Re-reading a release no longer overwrites what other platforms saw.**
  The same-version path replaced the stored surface outright — erasing every
  recorded absence while the entry went on claiming those platforms had
  looked — and never recomputed the delta beneath it, so everything below
  replayed against a surface that no longer existed. It merges now, and
  restitches the two entries that reference a surface it changed.
- **Provisioning survives Windows.** The python tier linked its interpreter
  with a symlink, which Windows grants only with developer mode or
  elevation; it falls back to a launcher, since a copied `python.exe` finds
  no standard library.
- **Click extraction no longer mistakes this environment for the binary.**
  The entry point imports from the running process while the binary comes
  from `PATH`, and nothing tied the two together — so priming a click tool
  that also lives in footman's own venv recorded *our* surface under the
  *release's* label: nine empty deltas in a row for mkdocs and zensical, the
  exact two drivers importable here. `_from_click` now requires the entry
  point's distribution version to match the binary's, and falls to the help
  path — which asks the binary itself — on any disagreement. Both chains are
  re-primed: mkdocs turns out to have changed surface in 1.6.0, 1.5.3 and
  1.4.3, zensical in 0.0.50 and 0.0.48.
- **A release asset is fetched by the tag the listing recorded, not one
  derived from the version.** bun tags `bun-v1.3.13` for a binary answering
  `1.3.13`, so deriving the tag made its entire history unreachable — listing
  worked, and only installing failed, which is why nothing looked wrong.
## [0.25.0] — 2026-07-27

### Added

- **`footman.env_files` — the .env built-in.** `plugin("footman.env_files")`
  loads `.env` from the invocation's directory at the run's single-threaded
  moment — before availability gates, so `@requires_env` sees it — with
  env-wins semantics: the real environment always beats the file.
  `--env-file=PATH` names another file (a `GlobalOption`, so it completes as
  a file and exists only when the plugin is pulled); a missing named file
  refuses, a missing default is nothing to do. Parsing is python-dotenv's —
  an optional dependency, imported lazily and taught by name when absent,
  never a footman requirement — with interpolation off.
- **`GlobalOption` — a plugin's own global option.** Constructing one is
  registering it: a module-level singleton in the provider, stamped with the
  defining module, riding the contributions carriage — so `--env-file=…`
  exists exactly when its owner is pulled, and an unpulled owner's option is
  an unknown option, taught. Long-form only, `=`-attached; a `bool`
  annotation is a flag, anything else takes a value coerced and validated
  through the same pipeline as a task parameter, which is also what makes
  completion work by construction — choices, `Path` file handoff — from a
  new `globals` section in the manifest (schema 2; stale completion caches
  refresh themselves). Read `OPT.value` anywhere in-run (frozen after
  parse; outside a run the read is taught). `@task(uses=[OPT])` declares
  the dependency into the manifest and task metadata; an undeclared in-task
  read still works, with a note naming the fix. Collisions are loud: a core
  name names footman, two plugins on one name names both owners.

### Fixed

- **A version scrape never touches the network, and an empty read names its
  cause.** The tool-driver version read now runs with gh's update check
  disabled (`GH_NO_UPDATE_NOTIFIER=1` — every other tool ignores the
  variable), and distinguishes its three failure shapes — spawn failed,
  spawn timed out, output carried no version token — so the CI check that
  guards version-keyed history teaches which one happened instead of
  reporting an em-dash.

- **A prerequisite's `confirm=` is asked.** The documented rule — a task
  that asks for confirmation gets it however it is reached — held for a
  command-line segment (asked up front) and a body call (asked at the
  call), but a task pulled in via `pre=`/`post=` ran unasked. Every gate in
  the plan is now asked up front, in dependency order; one reference is one
  question, however many segments and prerequisites reach it (a repeated
  gated segment also stops asking twice). A denial becomes the task's
  result before anything runs, so it never launches and its dependents skip
  with the denial as their cause.

## [0.24.0] — 2026-07-27

### Added

- **`@post_tasks` — the run report's moment.** The closing bookend to
  `@pre_tasks`: once per invocation, on the main thread, after every task
  concluded and before the summary or the `--json` envelope prints. The
  invocation carries the whole story — `inv.results` (every row,
  chronological, as result views), `inv.skipped` (the subset that never
  ran), `inv.total_ms` — so a run-level reporter finally sees what a
  `post_task`-only reporter cannot: the nodes that never started. Under
  `--json` a hook's stdout is rerouted to stderr (the envelope owns
  stdout); hooks run in cascade order, and a raising hook is named and
  fails the invocation.
- **Skipped nodes are reported, not silently absent.** A node the run never
  started — its prerequisite failed, or the run stopped reaching for new
  work — gets a row: `state: "skipped"`, `blocked_by` naming what prevented
  it, seated directly after that cause in the chronological report. The
  summary prints it (`skip build (blocked by lint)`), the `--json` envelope
  carries it (with `blocked_by`), and the exit code never takes it as the
  headline — the cause already owns that. `state` is an open set; consumers
  should tolerate values they don't know. `blocked_by` means prevention and
  nothing else: a `shared` row carries none — nothing blocked it, it was
  answered — and instead has its own `started`, the instant the request
  concluded, so it seats in the report where it actually happened; a
  request that waited on an execution that *failed* is blamed on it.
- **`queued_ms` — launch latency, on the row and in the envelope.** A node
  with prerequisites records when it became eligible (its last
  prerequisite's finish); `started - eligible` is how long it sat ready,
  waiting for a worker — reported as `queued_ms`, never folded into
  `duration_ms`, because the task wasn't running. Roots have no latency and
  no field. A `skipped` row still records no time at all, only its cause:
  a node that never launched never waited anywhere a clock runs.
- **Tasks wear their names on the thread, and the report says where they
  ran.** While a task executes, its worker thread is named after it —
  `fm:build`, badged `[serial]`/`[exclusive]` under a lane hold — so a
  sampling profiler's timeline (py-spy, viztracer, an OTel exporter reading
  thread names) shows tasks and lane occupancy instead of
  `ThreadPoolExecutor-0_1`; the name is restored afterwards, and a body call
  nests naturally. The pools themselves are named `fm-worker` and
  `fm-parallel`. Each executed row records `thread` (the worker's stable
  name) and `thread_id` (the OS id, `threading.get_native_id`) — the
  correlation keys a profiler dump uses — and both ride into the `--json`
  envelope. A row that executed nothing (a `shared` row, a refusal) records
  neither. Thread names are Python-level: samplers that read the
  interpreter see them; a native profiler attributing by OS thread name
  will not.
- **`@wrap_task` and `@wrap_bind` — the pair as one generator.** When the
  pre and the post are two halves of one thought, a wrapper says it in one
  place: locals instead of `task.state`, `try/finally` doing the pairing.
  `wrap_task` takes exactly one yield — pre half, `result = yield`, post
  half — enters at the `pre_task` moment and is resumed with the
  `ResultView`, so every pair rule is its rule too: per request (a `shared`
  row resumes it), reverse unwinding, a raising half failing the task,
  named. `wrap_bind` takes exactly two yields, enters at the bind boundary
  (`bound = yield` receives the bound arguments), and closes even when
  binding fails — the failure arrives raised at the first yield, where a
  `try/finally` observes it. The one asymmetry, stated plainly: `wrap_task`
  never sees a task that failed to bind (its anchor moment never fires);
  observing the bind boundary is what `wrap_bind` is for. Both desugar at
  registration into the same engine as the explicit hooks — one engine, two
  spellings — and yield-count violations are taught, naming the wrapper.
- **`@pre_bind` — the moment before parameters exist.** It fires before the
  task's parameters are bound, so what it writes into `task.env` is what
  `env()` fallbacks resolve, what coercion sees, and what `check(fn)`
  validators read — the one moment a plugin can influence what the body will
  be handed. A body call binds like a segment, so its binding sees the same
  injected environment. `task.args` is not readable here (nothing is bound
  yet — read values in `pre_task`); the same handle carries through the whole
  ladder, so state set at `pre_bind` is there at `post_task`. Binding happens
  per request while execution happens per work, so `pre_bind` may fire for a
  request whose row ends up `shared` — the whole ladder does, because the
  pair is per request and only the body is shared; a bind failure still
  fires the posts — the attempt concluded — with the refusal as the result.
- **The task's managed window opens before binding.** Hook code and the user
  code binding runs — `check(fn)` validators, custom constructors — now
  answer to the same rules a body does: an `os.environ` write is captured
  into the task's own overlay instead of leaking to parallel siblings, and a
  prompt outside an interactive task is refused. footman's own prompts —
  `ask()` questions and menus, `confirm=` — read the real terminal and are
  never caught by those guards, wherever they fire.
- **`@pre_task` / `@post_task` — the per-task lifecycle pair.** Where
  `@pre_tasks` runs once over the plan, this pair runs around every
  *execution* — a chain segment, a prerequisite, a fan-out member, a body
  call all count the same. `pre_task(inv, task)` fires after binding and
  reads the bound arguments (`task.args`, defaults included, read-only);
  `post_task(inv, task, result)` fires after the body, whatever the outcome.
  The `task` handle also carries `task.state` (scratch private to the plugin
  and the execution, delivered from pre to post), `task.env` (the task's own
  environment overlay — the one sanctioned lane for per-task env), and
  `task.source_hash` (the body digest, a tripwire, `None` when unreadable).
  `result` reads everything and writes one thing: `set_returned(value)`,
  which rewrites the *reported* value — the summary and `--json` — never
  what a dependent or a body caller received. Pres run in plugin order and
  posts unwind in reverse; the pair is **per request** — only the body is
  shared, so a request satisfied by an execution the run already performed
  still gets the whole ladder, closed with its `shared` row
  (`result.state`) — and the post is the task-finished event: once a
  request's ladder opened, it fires when the request concludes,
  irrespective of which pres a plugin registered or how they fared. A
  raising pre fails the task like a failed prerequisite, a raising post
  fails an otherwise-green task, and both failures name the plugin. Nothing
  fires under `--dry-run`. A `pre_task`'s return value is **reserved** for a
  future "supply the result, skip the body" power — today it is noted and
  ignored.
- **`registry.task_source_hash()` — a digest of a task's own body.** Normalised
  through the AST rather than taken over the text, so reformatting and comments
  do not move it while a real edit does; decorator lines count, so a changed
  `pre=` shows up. Deliberately a **tripwire, not an identity**: it covers the
  function's own source and nothing it calls, which makes it right for "warn me
  if the body moved and nobody said so" and wrong as a cache key. Exposed to
  hooks as `task.source_hash`.

- **A prime no longer leaves its downloads on the machine.** uv writes to two
  places of its own accord — a wheel cache, and the store holding the
  interpreters this machine actually runs — and priming CPython's releases put
  90 interpreters in that store and left them there, around 7 GB. Both now
  point inside the prime's scratch directory, so one cleanup removes every
  byte the walk caused and the python you develop against is never a candidate
  for deletion. Each release is also discarded as soon as its surface has been
  read, which is the difference between peak disk being one release and being
  all of them: a full prime of ruff would otherwise stand up 416 environments
  at once.
- **A refresh writes its own release note.** The events it found become a
  `### Changed` bullet per tool under `[Unreleased]`, naming the options added
  and dropped by their command-line spellings and counting the descriptions
  that merely moved — a release can reword half a dozen without changing what
  the tool accepts, and listing those turns a note into a diff dump. Per tool
  rather than per release, because a reader cares that prek gained `--glob`,
  not which patch carried it. Written into the file rather than printed: the
  job already edits `tool-history/` and the stubs and lands through a PR
  either way, so the note rides in the same diff.
- **`fm tools.refresh` — read every release published since the history was
  last updated, and say whether that warrants a release.** `prime` walks
  backwards to deepen a history; this walks forwards to catch one up,
  installing **every** release in between rather than jumping to the newest, so
  a flag that arrived in 0.16.1 is attributed to 0.16.1 and not to whatever
  was latest the day the job ran. A release that changed nothing still records
  an empty delta. `fm --json tools.refresh` returns what was read, which of
  those carried events, what could not be read, and whether a release is
  warranted — and the events are the CHANGELOG line.
- **CPython's releases can be primed into the option history.** The index is
  the provisioned uv's own — uv carries it inside the binary, so `fm
  tools.prime` gained a `--prefix` and drives the tiers from there: a stale uv
  reports a stale newest python and the walk starts too low without saying so.
- **The python stub tracks the newest CPython** instead of a pinned 3.13.

### Changed

- **A body call binds like a segment.** A parameter the caller leaves out now
  consults the same sources an absent option does — stdin, then its `env()`
  variable, then the default, with a defaultless `ask()` prompting as the last
  resort — so `build()` under `DEPLOY_ENV=prod` builds what `fm build` builds
  instead of silently taking the Python default. footman sees the call before
  Python fills in defaults, so omitting a parameter and passing the default's
  value explicitly are different requests: an explicit value wins over env,
  exactly as a command-line value does. Resolution happens before the work is
  keyed, so a segment, a prerequisite and a call that resolve to the same
  values are one piece of work. The ladder is consulted through a per-task
  plan built on the first call — a task never called from Python pays
  nothing. Outside a run, a call is still the plain function call it looks
  like.
- **Explicit call values run the annotation's validators.** Choices, bounds,
  path requirements and `check(fn)` now refuse at a body call exactly what
  they refuse on the command line — `scale(20)` against `between(1, 10)` is a
  taught error naming the call — because the annotation is the contract
  wherever the value comes from. Values are validated, never coerced: a
  Python caller passes real values under the signature's types, and the type
  checker polices those.

### Removed

- **BREAKING: `@finalize` is gone, replaced by `@pre_tasks`.** Removed
  outright rather than left as a refusing alias: the lifecycle has one name
  per moment, and a retired second name for the same moment is exactly the
  duplication the rest of this design keeps removing. The migration is
  mechanical — take `inv` instead of `tasks`, and read `inv.tasks` where the
  tree view used to arrive. It runs at the same moment, in the same cascade
  order, and can now also set the environment every task will see. Internally
  the readers for a task's declared prerequisites are `registry.pre_deps` /
  `post_deps`, leaving `pre_tasks` / `post_tasks` to name the lifecycle
  moments.

### Fixed

- **A prime could append a release newer than the one it was walking back
  from.** It asked whether a release predated the chain's floor by date, but a
  base carries the date it was *observed*, not the date it was published — so
  on a first prime the floor is dated today and every release ever published
  passes. Invisible while every base happened to be the newest release, and
  wrong the moment one is not, which is any stub synced from an outdated
  binary. The walk now positions the floor in the source's own ordering, and
  a floor that ordering cannot place stops the walk and says so.
- **An index that cannot be read is no longer an empty one.** Every network
  error, timeout and malformed body was swallowed into `{}`, so a throttled
  registry and a tool that had genuinely not released were the same answer.
  That is the one distinction a release job cannot afford: "is there anything
  new" answered "no" by a rate limit would end the job with "nothing to
  release", and a renamed package would answer it that way forever while the
  job kept reporting success.
- **A release chain is ordered by version, not by publication date.** Three
  curated tools keep more than one series alive at once — cmake 3.31.x beside
  4.x, pytest's 4.6 LTS beside 5.x, CPython's five — so the most recently
  published release is not the newest one. Ordered by date, a walk back from
  CPython 3.14.6 stepped to 3.13.14 and read every 3.14 option as dropped and
  then re-added, making every interval derived from it wrong. Pre-releases are
  also excluded from chains: an alpha is not something to say a flag arrived
  in, and two tools only *looked* like they shipped series in parallel because
  one sorted as though it were final.
- **A version the comparator cannot separate no longer moves the base.** Two
  builds of one base — eclint's `0.6.0-wk.3` against its `-wk.5` — compare
  equal, and "not older" was read as "newer", so a stale checkout could
  promote the older build and push the newer one down the chain. That is the
  rewrite the guard exists to refuse; it now declines and names the tool.
- **A vendored build tail no longer keeps a tool out of its own index.** PyPI
  ships `ninja` 1.13.0 and the binary in it answers
  `1.13.0.git.kitware.jobserver-pipe-1`, which matched no release, so ninja
  could not be primed at all.
- **The CPython listing no longer depends on what the machine has installed.**
  `uv python list` replaces a version's download entry with a local path once
  it is installed, dropping the URL its publication date is read from — so
  each primed release vanished from the index the next prime reads. It now
  asks for downloads only.

- **A `ctx`-declaring task now keys its body calls on the caller's
  arguments.** The work key bound a call's arguments against the declared
  signature, where the first positional value landed in the injected `ctx`
  slot and was then discarded — so `render("web")` and `render("api")` were
  the same key, and the second call wrongly shared the first's result. Calls
  now bind against the signature a Python caller actually sees, with the
  context parameter stripped.

## [0.23.0] — 2026-07-27

### Added

- **The stubs are rendered from a record, not from a reading.** Each curated
  tool gets a file under `tool-history/` holding what it accepted, release by
  release: the newest observed release stored whole, and — once the fetchers
  land — every older one as a delta describing how to step back to it.
  `fm tools.sync` now records its reading there first and renders the stub
  from *that*, so what ships is a view of the record rather than a second
  record that can disagree with it. All 26 stubs regenerate byte-identical
  through the round-trip, which is the proof the store loses nothing.

  Deltas point **backwards** because that is the shape of the work: priming
  older releases is pure append, the current version costs no replay (it is
  the base), midfill rewrites exactly one entry, and "did this release change
  anything" is "is its delta non-empty" — the question a release job asks,
  answered without comparing surfaces. An empty delta means *observed and
  unchanged*, which is not the same as a release nobody looked at; those are
  simply absent.

  The store is tracked but **not shipped**: it lives outside `src/`, so no
  install pays for history nobody reads. Generation is a maintainer task run
  from a checkout, and users read the stubs, which already carry everything
  the log is for.

- **`fm tools.prime` reads a tool's past releases into its history.** Walks
  backwards from the release the history already holds, installing one
  version at a time into a throwaway environment and appending a delta for
  each — so nothing already written is touched, and a release the chain
  already has is skipped. A prime stopped by a rate limit is resumed by
  running it again. `--count` is how far back to reach *this* run, and the
  floor a tool actually reached is recorded as `observed_from`: an option
  present in the oldest release read is "at or before" that version, never
  "since" it.

  Releases are ordered by publication date, with the version breaking a
  same-day tie — prek shipped 0.4.7 and 0.4.8 on one day, and a tie resolved
  by index order let the walk skip one and a later run append it *below* its
  own successor. Only the PyPI tier can be listed today; every other tool is
  named and skipped rather than left looking like a tool with no history.

### Changed

- **BREAKING: calling a task from a task body is part of the run.** It used to
  be a plain function call: it ran the task again even if the run had just run
  it, on the caller's thread, with no context of its own and no result anywhere
  — the one execution path footman could not see. A call now gets a real task
  boundary (its own context and working directory, its `@requires` and
  `confirm` gates, its own entry in the report), and the run performs a task's
  work **once per task and arguments**, whoever asks. So a prerequisite you
  also call hands back what it already produced, which is how a task finally
  reads a value `pre=` cannot pass:

  ```python
  @task(pre=[build])
  def publish():
      artifact = build()  # the build that already ran, not a second one
  ```

  Whether a task was reached by declaration or by a call makes no difference to
  how often it runs. Calling a task that is running on another thread waits for
  that run; a call that could never return (a task calling itself, or two
  calling each other) is refused by name instead of hanging, where
  self-recursion used to be a stack overflow. Two calls are refused outright: a
  `serial=`/`exclusive=` task, whose lane is taken at the task boundary and
  never mid-body, and an `infinite` task. A call outside a run is still the
  plain call it looks like, so importing a tasks file and calling a function
  keeps working. An `int` return remains a segment's exit code, while a call
  gets the number as a value.

- **`@task(shared=False)` — work that is never shared.** Some work exists to
  happen again: a notification, a timestamp, a scratch clean. Every request for
  such a task runs, whether that request is a call, a chain segment, or a
  `pre=` edge — one rule, so the spelling never changes the answer. Sharing is
  a property of the request: `.opts(shared=…)` first, then the task's
  declaration, then whatever asked for it, then shared. It propagates down the
  dependency subtree, because an unshared request asks unshared for what it
  needs, so `shared=True` is the pin for a step that genuinely is reusable. An
  unshared run never rewrites what the run already remembers — the first result
  stands.

- **BREAKING: a run performs a task's work once per request, however the task
  was asked for.** Two tasks declaring `pre=[build]` shared one build already;
  now a chain segment, a body call and a prerequisite all count the same, so
  `fm check check` runs `check` once and reports the second mention as
  `shared`. Repeating a task in a chain used to run it once per mention. What
  still runs twice: different arguments (`fm build web build api`), a different
  policy (`pre=[build.opts(atomic=True)]` is a different invocation), and
  anything declared `shared=False`.

  An unshared execution is nobody else's answer: it neither reuses a result nor
  becomes one. Otherwise whether a shared request reused would depend on which
  of two nodes the scheduler happened to start first, and how much work a run
  performs would stop being predictable.

- **A request the run had already satisfied is reported as `shared`.** A body
  call that finds its work already done returns the memoised value — and used
  to leave no trace, so the second request simply vanished from the report. It
  now appears, marked `shared`: dimmed in the summary with "already run this
  run" where a duration would be, and carrying `state: "shared"` in the
  `--json` envelope. It never began, so it has no start of its own and the
  ordering rule places it directly after the execution that satisfied it; it
  is `ok`, since the work succeeded just earlier, so the run's exit code is
  untouched.

  `state` is the reported spelling of what happened — `ok` / `failed` /
  `cancelled` / `shared` — resolved in one place. `ok` and `code` stay the
  exit-code channel, so a future outcome is another value of `state` rather
  than another boolean beside it. Sharing within a run and caching across runs
  are two axes, and each keeps one word.

- **The run's report reads in the order the run happened.** A dependency
  listing has no place for a task reached by a call; a chronological one does.
  Sequential runs are unchanged, since dependency order already is
  chronological, and a prerequisite still precedes its dependent; only
  independent tasks in a parallel run move, to wherever they actually ran.
  Anything that never began sits directly after whatever prevented it, so the
  report reads as cause then consequence. `TaskResult` carries `started` and
  `blocked_by` for this. A task reached by reference rather than typed is now
  reported by its *address* (`import_` shows as `import`) — the spelling you
  could actually type.

### Removed

- **BREAKING: a bare callable in `Annotated` is no longer a completer.** It
  used to mean `suggest(fn)`, which read as a convenience and behaved as a
  guess: the branch swallowed *anything* callable that was not a class, so a
  marker of the wrong shape — a plain function, or an instance with
  `__call__` — silently became a shell-completion function. The marker
  vanished, a mystery completer appeared, and nothing said a word either
  way. `suggest(fn)` is now the only spelling, the way one spelling per
  concept goes everywhere else in footman.

  The bare form is **refused**, not ignored, because it used to work:
  `Annotated[str, my_fn]` teaches `suggest(my_fn)`. Unrecognised metadata
  that is *not* callable is still passed over in silence, which is what lets
  a parameter carry a marker footman has never heard of.

### Fixed

- **A called task passes the same gates a declared one does.** A body call used
  to slip past `@requires` availability (an unavailable task *ran* when called,
  though the same task as a prerequisite refuses) and never asked a
  `@task(confirm=)` gate. Both now hold however the task was reached; the
  confirm is asked at the call, since a call cannot be known before the run the
  way a chain segment can.

## [0.22.0] — 2026-07-27

### Changed

- **BREAKING: a value is always `=`-attached.** Every option value — long
  options, short aliases, globals, task options — attaches with `=`:
  `fm build --target=prod`, `fm -j=4 check`, `fm --color=never lint`. A
  bare `--x`/`-x` is a flag, a bare word is a task or a positional, so a
  chain reads token by token with no arity table. The space form refuses
  with the fix spelled out — `fm build --target prod` answers "did you
  mean `--target=prod`?", never "unknown task 'prod'" — and a bare
  value-bearing option teaches the shape (`--jobs expects a value,
  attached: --jobs=N`). Values that start with a dash now just work
  (`--jobs=-1`); lists repeat or comma-join as before (`--tag=a --tag=b`,
  `--tag=a,b`); dict pairs read better anchored on their first `=`
  (`--env=DEBUG=1`). The completion-installer trio keeps its optional
  value (`--install-completion` detects the shell; `=zsh` names one), and
  `--help` renders every option in the attached spelling. Both the
  splitter and the completion hot path drop their value-consumption
  states — the whole option grammar is lexical now. The `tools.*` bridge
  is untouched: it renders each child tool's argv in that tool's own
  dialect, and everything after `--` stays opaque passthrough.

### Added

- **`Secret.reveal()` — deliberate exposure, said out loud.** A task whose
  *job* is to print a credential (an `export …` line for `eval "$(fm
  env-export)"`) needs the value to leave, and a `Secret` inside a
  structured document (`Stdout[dict]`, the `--json` envelope) redacts to
  `***`. `reveal()` returns the plain `str`, so the intent reads at the
  point of use and every deliberate exposure in a codebase is one `grep`
  away — an audit surface a run-wide "don't redact" flag could never give.
  Formatting was never redacted and still isn't: string operations on a
  `Secret` yield a plain `str`, which is what makes `f"export
  TOKEN={token}"` work with no switch to disarm protection elsewhere.

### Fixed

- **A subcommand group is a nested class, and the tool reference shows it.**
  `docker compose up` was `DockerCompose` sitting *beside* `Docker` — a name
  invented by flattening — and a reference page named only the root class, so
  `docker compose up`, `uv pip install`, `gh pr create` and every flag they
  take were described nowhere at all: **48 callables and 641 options**, a fifth
  of the whole stubbed surface. Groups now generate as `Docker.Compose`,
  `Uv.Pip`, `Gh.Pr`, which the docs renderer walks on its own — one directive
  documents a tool whatever its shape, and two tools can no longer collide over
  an invented name. `flags()` returns `Self` rather than its class by name,
  since a nested class cannot refer to itself from inside its own body and
  `Self` is what the chain meant anyway.

  Two things fell out of the rename. footman's own `Tool` and `Result` are
  imported privately in generated stubs (`Tool as _Tool`), because `uv tool`
  would otherwise write `class Tool(Tool)` — a class deriving from itself; a
  verb can never produce a leading underscore, so the collision is gone rather
  than dodged. And docstring wrapping now knows its nesting depth, so a group's
  help text stays inside the line limit instead of pushing four characters past
  it.

  The index table had the same blind spot from the other side: it flattened
  nested verbs to bare names, so `compose.up` read as `up` and uv's two
  `install` verbs collapsed into one row. Verbs are listed dotted now, as they
  are called, and footman's own `flags()` accessor stops posing as a verb of
  the tool.

- **One version parser, and `installed_version()` answers about the binary it
  runs.** The bridge and the stub extractor each had their own regex, and the
  bridge's read `PATH` while the extractor resolves differently (a Homebrew
  keg for host-read tools), so on macOS `git` reported 2.55.0 to one and
  2.50.1 to the other. The parsing is now shared — only the *choice of
  binary* differs, deliberately — and the bridge asks the executable it will
  actually invoke, so `tools.python.installed_version()` reports the running
  interpreter rather than whatever `python` happens to be on `PATH`. A stub's
  recorded version was never the same question, and the docstring now says so.

- **One comparator too: a build tail can't read as newer than its own base.**
  `installed_version()` scraped every digit it found, so `eclint 0.6.0-wk.5`
  compared as `(0, 6, 0, 5)` — *after* the 0.6.0 it is a fork build of, which
  is backwards under every version grammar — and ninja's
  `1.13.0.git.kitware.jobserver-pipe-1` picked up a stray `1`. Both now
  compare as their base, `(0, 6, 0)` and `(1, 13, 0)`, which is the honest
  answer to the only question the tuple is asked: is the CLI new enough. The
  snapshot guard's own copy of that logic is gone; `tools.version_tuple` is
  the one comparator, beside `tools.read_version`, and the exact string
  survives for anything that records *which* build was read.

- **`cmd.installed_version()` works.** Windows `cmd` has no `--version`; it
  spells it `cmd /c ver`. `Tool(…, version_argv=…)` makes that declarable
  rather than a special case nobody could reach, and it rides a chain like
  every other construction fact.

- **`prompt(secret=True)` returns a `Secret`.** It hid the typing and then
  handed back a bare `str`, so a mid-task secret was fully printable in the
  next traceback or `--json` payload — while the identical-looking
  `ask(secret=True)` redacted. Hiding a value while it is typed and then
  printing it at the first error is a strange kind of secret. An unattended
  default is wrapped too: where the value came from doesn't change what it
  is.

### Documentation

- The input guide gains a **Secrets** section, covering the two halves
  (`secret=True` collects, `Secret` displays), what redaction deliberately
  does *not* cover (the bytes a task writes on purpose), and the honest
  flip side — a secret f-stringed into a log message loses its redaction
  the same way, because nothing can tell the two apart.

## [0.21.0] — 2026-07-26

- Tool stubs retaken at release, per the audit: **uv, prek, djlint** had
  released newer versions than the snapshots recorded. A snapshot only
  ever moves forward.

### Added

- **`stdin` — a parameter bound from the pipe.** footman is a pipe target:
  `git diff | fm review` and `fm review < changes.patch` both bind the
  stream to a typed parameter, with no flag to remember. The annotation
  decides the interpretation — `Annotated[str, stdin]` (or `Stdin[str]`)
  reads the stream as UTF-8 text, `bytes` reads it raw, `stdin("field")`
  reads one top-level key of a JSON document, and `stdin(lines=True)` binds
  a list one line per element, each line coerced exactly like a CLI token
  (`list[int]`, `list[Path]`). Precedence is CLI > stdin > env > default >
  prompt, so an explicit option always wins and one signature serves both
  spellings. The stream is read once, fully, at the boundary and shared by
  every parameter in the run that asks — task bodies still never touch
  stdin, so bound tasks stay fully parallel with no `interactive=True`. A
  terminal means "not provided": a defaulted parameter falls back, a
  required one refuses with a taught message, and nothing ever blocks on a
  read. `--help` says what a parameter reads; `Runner.invoke` grew a
  `stdin=` argument so tests pipe without touching the real stream.

- **`Stdout[T]` — the return annotation that owns stdout.** A task declares
  that its return value is the document on stdout, in the signature:
  `def status() -> Stdout[dict]` makes `fm status | jq .` work with no flag
  at any call site — a filter the way `sort` and `jq` are filters. The
  return type decides the bytes, mirroring `stdin`: `Stdout[str]` verbatim
  plus a trailing newline, `Stdout[bytes]` raw, anything structured JSON —
  pretty-printed at a terminal, one compact line into a pipe, dataclasses
  and `Secret` redaction handled by the same encoder `--json` uses. The
  rules: `--json` wins (the document rides in `results[].returned`); only
  the addressed task emits (a declaring `pre=`/`post=` dependency or
  fan-out member is suppressed, not refused); two declaring tasks in one
  chain is a plan-time refusal; `None` means empty stdout, exit 0;
  `Stdout[int]` makes the number the document (a bare `-> int` stays the
  exit-code channel); a failed task emits nothing; everything that is not
  the document replays on stderr, so `fm status > out.json` captures
  exactly the document. `Stdout[T]` + `interactive=True` is a taught
  declaration-time error, and a body call is unaffected.

- **The document binder: JSON on stdin into typed shapes.** A parameter
  annotated with a dataclass, `dict`, or `list` and marked `stdin` binds
  the whole JSON document: nested dataclasses recurse (no dotted field
  paths — `event.tool_input.file_path` is just attribute access), `list[T]`
  and `T | None` recurse too, and scalar leaves run the same coercion a
  CLI token gets, so `Path`, `Literal`, enums and `datetime` behave as they
  do on the command line. Unknown keys are ignored, never refused — a
  producer may grow fields without breaking a consumer. Missing keys follow
  the dataclass: a field with a default is optional, a defaultless absent
  one is a taught refusal. Every refusal names the exact JSON path
  (`event.tool_input.file_path: expected text, got a number`). A
  dataclass parameter is boundary-only — not a flag, not a positional; the
  pipe is its only source, and `fm task < fixture.json` replays the real
  parse. A bare-marker `list` parameter reads a JSON array; a `dict`
  parameter reads a JSON object.

- **`[tool.footman] sort = true` — alphabetical listings.** One boolean
  orders every human-facing walk of the tree: `--list`, `--tree`, help,
  the `--json` catalog, and the generated docs pages. A `--sort` global
  does the same for a single invocation. The default stays definition
  order — a tasks file is an authored page, and its order is the
  author's (the same order the composition rules preserve). Tasks still
  list before groups at every level, and the setting is presentation
  only: what runs, and in what order, never follows it.

- **`@task(hidden=True)` — listed nowhere, callable as ever.** For the tasks
  a machine calls and a human never types: a CI entry point, a step another
  task drives. The task drops out of `--list`, `--tree`, group help, the
  did-you-mean index and completion, and nothing else changes — `fm <name>`
  runs it, a `pre=`/`post=` dependency runs it, a runnable group's
  empty-body fan-out still includes it. It is presentation, not policy.
  `--json` reports it **marked** rather than missing, because a machine is
  exactly who calls it, and the generated task docs badge it, because the
  docs are where you look up what the listings won't offer.

  `hidden` is inherited: unset means "whatever my group said", so
  `group("internal", hidden=True)` (or a `hidden=True` on the group's
  `@group.default`) hides a whole subtree in one word, and a child can say
  `hidden=False` to come back. A group with nothing listed under it prints
  no heading at all. `TaskView.hidden` reads the declaration — `None` when
  it inherits — so a `@finalize` hook can tell an override from silence.

  This is a third thing, next to the two that already existed, and the docs
  now separate them: **hidden** is listed nowhere but callable; a plain
  `if` around the decorator **omits** the task entirely (no address at all);
  `@requires…` **lists it with a reason** it can't run here.

- **A stub snapshot only ever moves forward.** Two readings are worth less
  than the file already checked in, and both are now named and left alone
  rather than treated as drift: a tool **missing from a `--prefix`** (a
  partial provision would otherwise fall through to the host's copy, quietly
  turning a failed fetch into "the tools moved"), and one whose version is
  **older than the stub records** (a machine behind the one that took the
  snapshot has nothing to add, and reading it would rewrite the stub
  backwards, dropping flags that exist upstream). Neither counts as behind —
  they are unanswered, and `sync` leaves those files untouched. Only the
  `system` tier (git, docker) is meant to come from the host. On a laptop
  that trails the snapshots, `tools.audit` went from reporting eight tools
  as drifted to two.

- **`--prefix` on `tools.sync` and `tools.audit`.** Both can now read the
  binaries from a `fm tools.provision` directory instead of the machine
  they run on — the question a scheduled check actually wants to ask
  ("have the tools released anything since our snapshot?") rather than the
  one the local PATH answers ("is this laptop's ruff the one we read?").
  `color` already took `--prefix`; the three now share one helper that
  scopes the overlay to the task through `ctx.env`, with a bare-call
  fallback for callers outside a run.

### Changed

- **BREAKING: refusals exit 64 (`EX_USAGE`), not 2.** Exit 2 used to mean
  four different things — an unknown task or flag, a value that would not
  coerce, a task saying `fail(code=2)`, and a `run()` subprocess exiting 2 —
  so a caller could not tell a broken invocation from a real verdict, and a
  harness reading 2 as "blocking error" acted on refusals as if they were
  results. Now every refusal — parse, binding, tasks-file, config,
  availability, `--where`, the completion installers — exits 64, on the
  process and inside the `--json` envelope (`error.code`, `results[].code`)
  alike. Interrupt stays 130. The low codes belong to tasks again: exit 2
  from a task now means exactly what the task said. Anyone keying on 2 for
  footman's own errors must key on 64; hook recipes that flattened every
  failure to one code now pass 64 through untouched (`docs/agents.md` shows
  the shape).

- **`--tree` draws a tree.** It used to print every task at its full dotted
  address under an indented group header, which made it the `--list` output
  with worse alignment. It now draws branches (`├─`, `└─`, `│`) and names
  each leaf once, so the shape is the point; `--list` remains the flat view
  where every row is a copy-paste-runnable address. Both now read one
  traversal (`_describe.walk`), so a rule about what is listed cannot be
  true of one and false of the other — which is what let `hidden` reach
  both by construction.

- **footman's own tasks overlay the tree — no container at all.** The
  first-party plugins are pulled at the end of footman's own `tasks.py`,
  so each node merges with what the file already defined: the docs tasks
  join the local `docs` group leaf by leaf (`docs.serve` beside
  `docs.page`), and `tools.…` lands at top level — one surface, no
  `footman.` prefix (entry-point names unchanged; your
  `plugin("footman.tools", into=…)` still mounts wherever *you* say).
  Merging is order-independent for any plugin, not just ours: a pull
  composes with a group the file already defined, and a `group()` defined
  *after* a pull now adopts the pulled group instead of replacing it —
  either way you get the union, with local tasks winning name clashes;
  order only decides listing order. The
  documenter's self-exclusion now keys on per-task provenance instead of
  a hardcoded mount name, so it excludes exactly the pulled tasks
  wherever an author mounts them — the author's own tasks on a shared
  group stay documented. The tools index also stops saying "unknown" for
  the shells' in-process capability: a hand-written stub means there is
  no entry point to call, so the answer is "no". `--list` wraps long descriptions with a hanging
  indent to the description column, so a narrow terminal no longer
  shears the two-column layout apart. And the custom-CLI page says what
  was always true: the uv handoff re-execs the *(branded)* footman you
  invoked — `acme` hands off to the project's own `acme`, never `fm`.

- **BREAKING: a stub that is behind is news, not a failure.** A stub records
  what one tool accepted at the version it was read from, and footman
  promises no particular speed at retaking that snapshot — so
  `fm tools.audit` finding a newer release upstream never meant anything was
  wrong, while its exit code and its wording ("differ from the installed
  tool … run `fm tools.sync` to update") both said otherwise. It now reports
  and exits zero: *"3 tool(s) have released a newer version than the stub
  snapshot … nothing is broken — the bridge speaks flags the stub hasn't
  heard of."* `--strict` restores a non-zero exit for automation that needs
  something to trip on, and the task returns its findings
  (`{"checked", "behind", "skipped", "resnapshotted"}`), so
  `fm --json tools.audit` hands a scheduled job the list to act on.

  One finding keeps failing unconditionally: a disagreement in footman's
  negation or wrapper tables. Those are read by the *runtime*, so a mismatch
  means a task emits the wrong command today — a defect, not a release
  someone else shipped.

- **BREAKING: `fm tools.list --missing` is now `--show missing`.** Listing
  all tools, only the installed ones, or only the missing ones is one
  question with three answers, so it is one parameter:
  `show: Literal["all", "installed", "missing"]`, defaulting to `all`. The
  boolean it replaces could only say two of the three, and adding a second
  boolean would have made `--missing --installed` a representable request
  that silently lists nothing.

### Fixed

- **A background manifest rebuild no longer loses a race with the TAB that
  spawned it (Windows).** Windows refuses to replace a file another process
  holds open, and a reader holding it open is the design: completion polls
  the cached manifest every 30 ms while the detached refresh rewrites it.
  The `os.replace` failed, the child swallowed the error (a background
  refresh must never crash or print), and the rebuild silently never landed
  — leaving the stale answer in place until some later write found a quiet
  moment. `write_manifest` now retries for about a second, and cleans up its
  temp file if it does give up rather than littering the cache directory.

- **pytest is reported as in-process by default, because it is.** `tools.py`
  builds it in-process (through `pytest.main`), but its driver never said so,
  so the two places that *label* the mode read the detected capability
  instead: `fm tools.list` showed pytest as `capable` and its stub header as
  `In-process: available` rather than `default`. A parity test now pins every
  driver's `name`/`in_process` to how `tools.py` builds that tool, so the
  label can't drift from the runtime again.

### Documentation

- footman's own agent hooks are footman tasks now: `.claude/settings.json`
  runs `fm hooks.post-edit` and `fm hooks.stop` (a hidden group in
  `tasks.py`) instead of shell one-liners. The payload arrives as a typed
  dataclass from stdin — the `jq` host dependency is gone — the loop guard
  is a field read, and the exit contract is exact: 0 quiet, 2 a blocking
  verdict with receipts on stderr, 64 passing through so a broken hook
  line reaches the human, never the model.

- Corrected the tools-bridge page and its code comments where they still said
  a zero-argument `main()` serialises — the argv router ended that. pytest's
  dedicated `pytest.main` path is kept for the reason that still stands: its
  console entry is the private `_console_main`, which on a broken pipe points
  the process's real stdout at `/dev/null` — harmless in a subprocess,
  blinding in footman's, where it would take every task running beside it
  with it.

## [0.20.0] — 2026-07-25

### Added

- **`Arg[T]` — the optional trailing positional.** `def files(pattern:
  Arg[str] = "*")` makes `fm files src` fill the positional and a bare
  `fm files` run on the default. The grammar stays deterministic and
  greedy: a following bare word *is* the value — never re-interpreted as
  the next task, no name-peeking — and capped at one token. To run
  argument-less ahead of another task, say so with the explicit boundary:
  `fm files + build` — and completion offers the `+` right at the optional
  slot, so the boundary documents itself. An `Arg` needs a default, takes
  at most one token, and must trail every required positional — each rule
  a taught error.

- **Live `suggest` completers drive the prompt.** A defaultless `ask()`
  parameter carrying a *strict* completer now asks with a numbered menu of
  the completer's fresh values — and `Many[...]` makes it a multi-select
  (numbers, `all`, `none`) — instead of free text. A best-effort completer
  (`strict=False`) shows its values as a hint and leaves the answer free.
  Bad picks re-ask; answers ride the same coercion pipeline as CLI values.
- **Secrets redact.** Answers to `ask(secret=True)` arrive as
  **`footman.Secret`** — a real `str` for the body, but its repr (logs,
  tracebacks, debuggers) is `Secret('***')`, the `--json` envelope
  serialises it as `***` (containers are walked), and a secret parameter
  never publishes values into the completion manifest (no baked choices,
  its completer never runs there).
- **Prompt hardening.** Stdin closing mid-prompt (Ctrl-D, piped input
  running out) is a taught error instead of an infinite re-ask; everything
  a prompt echoes back — including your own mistyped input — is scrubbed
  of control characters, closing the terminal-injection class `select()`
  already guarded against.

- **A generated "Errors & notes" reference page.** Everything footman can
  say when it refuses, warns, or teaches — every message-bearing error and
  every teach-once note — extracted from the source itself on each docs
  build (runtime holes shown as ⟨placeholders⟩), grouped by module, listed
  under Reference. Generated, not transcribed, so it can never drift from
  what the runner actually says.

- **The argv router — the last hidden serialisation point is gone.** A
  legacy zero-argument `main()` that reads `sys.argv` used to take a
  process-wide lock while footman patched the global around it; inside a
  run, `sys.argv` is now a per-call view served by a router (the same move
  the environment router makes for `os.environ`), so those entries
  parallelise exactly like their argument-accepting siblings. Nothing in
  the in-process path serialises any more — the parallel regime's claim
  ("the only non-parallel execution is declared") now holds with no
  asterisk. Outside a run the classic patch remains as the bare-call
  fallback; a C extension reading the list storage directly, or code
  *reassigning* `sys.argv` wholesale, degrades to the old behaviour.

- **Dotted cherry-picking in `only=`/`exclude=`.** The last surface of
  one-spelling-everywhere: filters take full dotted addresses relative to
  the pulled node — `plugin("acme.shared", only=["docs.build", "fmt"])`
  grafts one nested task and one flat one, materialising the path with
  the source's own group copies. Matching is exact (the whole-group
  spelling is the glob); unions are redundant, not errors; a group pruned
  empty is dropped, never grafted as a shell; validation is per-segment
  ("no task or group at 'docs.buidl' (docs has: build, serve)"). And
  because the default is the child named `default`, default-ness survives
  only if the default survives: `only=["lint.python"]` grafts a
  default-less `lint`, `only=["lint.default"]` grafts just the default,
  `exclude=["lint.default"]` everything but it.
- **`fm --plugins`** lists the installed `footman.tasks` entry points,
  marked pulled-or-not and where each landed — "installed but nobody
  pulled it" becomes visible. Descriptions are two-tier: a pulled plugin
  shows its landed tree's own help; an unpulled one shows its
  distribution's Summary (entry-point records can't carry a description),
  read from metadata with zero imports. A container module's docstring
  becomes its root help.
- **Group defaults take positionals.** The `@group.default` no-positional
  rule existed only because `fm lint foo` used to be ambiguous; dotted
  addressing dissolved the ambiguity (the subtask is `fm lint.foo`), so
  the rule goes with it: the default's signature is now the group's whole
  CLI surface — `fm lint src/` hands `src/` to the default like any task
  argument. The grammar stays deterministic (a positional wins) but never
  silent: a value that exactly names a child gets a one-line stderr note
  with the dotted spelling, an edit-distance-1 near miss (`fm lint
  markdwon`, which used to error and would now filter on nothing) names
  the nearest subtask, and a path-shaped value (`fm lint ./markdown`) is
  the documented quiet spelling.
- **Runnable groups are listed and described.** `--list`, group help, and
  the did-you-mean index now carry the bare-group spelling as its own
  row, described by the default's docstring — or,
  when the default has none, by generated text that says what it does:
  "run every task in this group" for an empty body, "run this group's
  default action" for a custom one.
- **Completion grows the other half of the `cd` idiom — two generosities,
  both completion-only** (the runtime resolver stays strict, so scripts
  cannot rot). *Segment-wise abbreviation*: each typed segment
  prefix-matches its own tree level, `fm f.t.sy⇥` → `footman.tools.sync`,
  the way zsh expands `/u/l/b` — and because footman generates the
  candidates itself, every shell gets it. An ambiguous segment expands up
  to itself and lists that level's matches. *Leaf-name fallback*: a token
  matching no top-level name completes against last segments instead
  (`fm serve⇥` → `docs.serve`) — the rescue for "I know the task, not
  where it lives", gated on zero top-level matches so it can never pollute
  a first tab or a valid descent.
- **The transition TAB is crash-proof.** The completion hot path now
  checks the manifest's `schema` before walking it: a cache baked by a
  different footman routes into the existing cold build (which also
  refuses to serve the stale file mid-rebuild), so the first TAB after an
  upgrade serves correct candidates instead of a traceback. A drift test
  pins the hot path's literal to `manifest.SCHEMA_VERSION`.
- **A "Shell differences" docs page.** The path-style completion model is
  documented shell-neutrally; the observable per-shell differences
  (description columns, menus, the space after a unique match) now have
  one advanced page, and the five-shell functional tests drive a dotted
  descent through real bash/zsh/fish/pwsh/nushell.

- **Questions front-load: asked one at a time, run in parallel, as early as
  correct.**
  Every promptable `ask()` parameter across the whole run answers *up
  front*, right after the `confirm=` gates and before anything executes —
  you answer once and walk away, and think-time can never land inside a
  task's recorded duration. Only a question carrying a live `suggest`
  completer waits (its menu may need a prerequisite's output) and resolves
  at its task's launch, as before. A required question with no way to ask
  (`--no-input`, no terminal) now refuses the whole run before anything
  starts, naming the flag — instead of failing one task mid-run. Accepted
  answers flow through the same coercion pipeline as command-line values.

- **The cascade walk is configurable.** A user-level **`cascade`** key —
  `none` (the current directory's own files only), `repo` (the `.git`
  ceiling, the default and today's behaviour), or `filesystem` (past
  repository boundaries, up to the filesystem root) — decides how far
  discovery ranges, and task files and config follow the same walk. The
  key is user-level-only (what sits above a repo is the machine owner's
  layout, not any project's business; a project file setting it is
  stripped with a `-v` advisory), and a new **`FOOTMAN_CASCADE`**
  environment variable overrides it per invocation
  (`FOOTMAN_CASCADE=none fm test` in CI). An unknown value is a taught
  error naming the three modes, never a silent default.
- **The working directory is a policy, not an accident.** A task's cwd is
  resolved once, before the body runs, by a ladder — `.opts(cwd=)` per use,
  `@task(cwd=, rel=)` per definition, `[tool.footman] cwd` as the run-wide
  default — from four tokens or an absolute path: `taskfile` (the directory
  of the file defining the task — the default, today's implicit behaviour,
  now named), `root` (the highest cascade file's directory), `asinvoked` (a
  pinned snapshot of the launch directory), and `unmanaged` (footman stays
  out: children spawn from the live process cwd). `rel=` appends a relative
  suffix to the resolved base — a nearer `rel` replaces a farther one — and
  `ctx.cwd` is always concrete inside a run. Per call, `run()` gains
  `rel=` beside `cwd=`, so `run("npm run build", rel="web")` roots one
  command in a subdirectory of the task's cwd. Relative `cwd=`, absolute
  `rel=`, and `rel=` under `unmanaged` are taught errors. `.opts(cwd=)`
  works on direct body-calls too, and two uses of one task at different
  cwds are two DAG nodes, never silently merged.

- **`footman.fail(reason, code=1)` — a blessed way to fail a task.** A function
  (not a `raise`) that stops the current task with a reason: the reason renders
  verbatim on the failure line and in the `--json` `error` field, and
  `fail("…", code=3)` sets the exit code too. It is a function on purpose — a
  task lives in your repo under your linter, and a call trips no flake8-errmsg
  (`EM101`) or tryceratops (`TRY003`), where `raise SomeError("literal")` would;
  the same reason `sys.exit()` and `pytest.fail()` are functions. The exception
  it raises, **`footman.Failed`**, is exported for `except footman.Failed:`. The
  stdlib idioms (`return N`, `sys.exit(...)`, raising) still work unchanged.
- **Colour survives footman's no-PTY boundary.** footman spawns tools over
  pipes, not a pseudo-terminal, so a tool sees a non-terminal and turns colour
  off — even when footman itself is on a terminal. footman now forces it back:
  every subprocess gets `FORCE_COLOR`/`CLICOLOR_FORCE` when the run is colourful
  and `NO_COLOR` when it is not, and the captured bytes replay onto footman's
  own terminal (colour is position-independent, so it survives the round-trip;
  live cursor control is the thing a PTY-less run genuinely can't carry, and it
  isn't attempted). One run-wide decision drives all of it, resolved from a
  new **`--color=always|never|auto`** global (`--no-color` is the `never`
  alias), **`[tool.footman] color`**, then `NO_COLOR`/`FORCE_COLOR` in the
  environment. `always` colours even into a pipe (for `less -R`); a captured
  `--json` run stays byte-clean. The few tools that ignore the environment and
  take a flag instead — git's `-c color.ui=always` — are forced through their
  own switch, injected into the executed command only, so `recording()` and
  `--dry-run` still show the tool's own call while `--verbose` shows what ran.
  Which tools obey the environment and which need a switch is *probed*, not
  guessed: **`fm footman tools color`** runs each tool with colour forced on and
  off and reads the bytes, categorising every direction `env`/`flag`/`none`, and
  regenerates the `_colordata.py` the forcing table loads. Forcing colour *off*
  is the absence of `FORCE_COLOR`, not `FORCE_COLOR=0` — some tools (ruff) read
  the mere presence of `FORCE_COLOR` as "on", so `--no-color` and piped runs
  clear it rather than setting `"0"`.

- **`run(shell=…)` runs a command string through an explicit shell.** `run()`
  stays shell-free by default — a string is split, no shell, so `|`/`>`/`$VAR`
  are literal — but `run("a | b", shell=True)` runs the whole string through a
  resolved interpreter, so pipes, redirects, globs, and variables work.
  `shell=True` follows the project's shell policy (`[shell] default`: `posix`
  by default — bash, then plain sh, git bash on Windows and Homebrew bash on
  macOS; `native`; `pwsh`; or a concrete shell); a string names a concrete
  shell (`bash`/`zsh`/`sh`/`fish`/`nu`/`pwsh`/`cmd`) or a strategy. A missing or
  wrong-platform shell is a taught error, and a shell-free `run("a | b")` now
  teaches instead of passing the operator as a literal argument. Two flags
  harden a run: **`strict=True`** fails on the first error and a masked pipe
  stage (`set -eo pipefail` for bash/zsh; `set -e` with a note on plain sh;
  `$ErrorActionPreference='Stop'` for pwsh; a taught error where there is no
  errexit), and **`clean=True`** runs the interpreter without the user's
  startup files, so a task's shell behaves the same on every machine. On
  Windows, the shown/`--verbose` command line now quotes the way cmd and
  PowerShell can read (stdlib `subprocess.list2cmdline`). The curated shell
  tools gain a sixth, **`tools.cmd`** (`cmd /c …`), so all of
  `bash`/`zsh`/`fish`/`pwsh`/`nu`/`cmd` read consistently.

- **`run()` returns a `Result`.** `run()` — and every `tools.*` call — now
  returns a `Result` instead of a bare exit code. A `Result` *is* the exit code
  (it subclasses `int`, so `code = run(...)`, `if run(...)`, and `== 0` are all
  unchanged), and it also carries the captured output split by stream, so
  `run("git rev-parse HEAD").stdout.strip()` reads the hash without stderr
  glued on. `.stdout` and `.stderr` are separated for both subprocess and
  in-process tools, and `.ok`, `.command`, `.raw`, and `.duration` round it
  out. The typed tool stubs return `Result` too, so `.stdout` is checked at the
  call site.
- **Single-dash long flags for Go-style tools.** `Tool(single_dash=True)`
  spells every long flag with one dash — `tools.eclint(fix=True)` →
  `eclint -fix` — for tools whose Go `flag` package rejects the `--fix` form.
- **`djlint`** joins the curated tools — the HTML/Django/Jinja template
  linter-formatter, with a typed `tools.djlint(...)` stub.
- **`@group.default` takes `@task`'s policy options.** A group default can now
  be parameterised — `@lint.default(pre=[bootstrap], keep_going=True,
  confirm="…", atomic=True)` and the rest — the same orchestration surface a
  task has, minus `name` (the group already names it). `interactive=True` on an
  **empty-body** default is a load-time error: an empty body fans the group's
  tasks out in parallel, so there is no single body to own the terminal.

### Changed

- **BREAKING: composition is two typed verbs — `plugin()` pulls entry
  points, `include()` pulls modules — and the `plugins=` config key is
  gone.** A pull line in your tasks file replaces the config mount:
  `plugin("footman.docs", into="footman")`. The longest installed
  `footman.tasks` entry-point name (for `plugin()`) or importable module
  prefix (for `include()`) is the *identity*; the rest of the string walks
  the provider's tree, so `plugin("acme.devkit.lint")` reads like
  `from acme_devkit import lint`. A pulled node lands under its **own
  name** — identity never becomes an address; `into=` (a dotted address,
  created on demand) is the consumer's placement, and a whole container
  splats its children (the devkit one-liner). Filters are relative to the
  pulled node; a subpath can land a single task
  (`plugin("acme.linters.default", into="lint")` adopts a provider's
  default, which then fans out the group it *landed in* — default-ness is
  parent-relative). Collisions are provenance-based: your own definitions
  silently win, whatever the file order; two pulls clashing at one leaf
  raise loudly, citing both identities, unless `override=True`;
  group-vs-group composes recursively all the way down. A leftover
  `plugins=` key is a taught refusal pointing at the pull line, never a
  silent ignore.
- **BREAKING: the group default is the child task named `default`.**
  `@group.default` now registers its function as the child `default` — a
  fixed, well-known name — so the default finally has an address
  (`@lint.default` ↔ `fm lint.default`), appears in listings and
  completion, and default-ness is *derived* from the tree
  (`Group.default_task` became a read-only property), so a fork or graft
  can never desync it. The name is the mechanism: any task named
  `default` — via the decorator, `@task(name="default")`, or a pull — is
  its group's default through the same validations (an empty body fans
  out, and excludes itself from its own fan-out set; `interactive=True`
  on an empty body still refuses). Two consequences to know: a group
  that declared both `@group.default` and a task literally named
  `default` now collides loudly at load, and a *group* named `default`
  is refused (a group-typed default is incoherent — bare `fm lint`
  resolving to another bare group).

- **BREAKING: a nested task's address is one dotted token, everywhere.**
  `fm docs serve` is now `fm docs.serve`; flat tasks and chains are
  unchanged (`fm build lint test`). One spelling serves every surface —
  running, `--help`, `--where`, completion — and `--help`/`--where` drop
  the space walk for the same strict resolver (`docs..serve`, `.docs`, and
  a trailing `docs.` are taught errors, never silently normalised). The
  space form is permanently *taught*, not parsed: `fm docs serve` answers
  "nested tasks use dots: 'docs.serve'", the lookahead teaching the longest
  resolvable path (`fm footman tools sync` → `footman.tools.sync`), and a
  child of a just-run runnable group teaches the same way (`fm lint python`
  → `lint.python`). Unknown addresses get did-you-mean suggestions over the
  flat dotted index; listings, `--tree`, help, and the markdown exporter
  print full dotted addresses so everything shown is copy-paste-runnable.
- **BREAKING: task completion is path completion — the `.` is footman's
  `/`.** Candidates sit one segment beyond the typed prefix, `ls -F`
  style: a namespace group always carries its trailing dot (`docs.`), a
  runnable group offers itself *plus* its dotted children (space runs the
  default, `.` descends), a task completes terminally. A unique namespace
  match completes straight through to its children, so no shell ever
  strands the cursor with a space after `docs.`.
- **BREAKING: task and group names may not contain `.` or whitespace** —
  `.` is the address separator, so `group("v2.0")` or
  `@task(name="docs.build")` would alias into fake nesting; both refuse at
  load time with a taught `RegistrationError`.

- **`os.environ` is virtualised for the run.** Reads inside a task see the
  run-start snapshot plus the task's own overlay — exactly what the
  subprocess branch of the same call injects as `env=`, so in-process and
  subprocess tool calls finally read the same world. Writes from a task
  body scope to the task's overlay: visible to its own reads and every
  child it spawns, invisible to siblings — with a one-time, task-attributed
  stderr note naming the deliberate spelling (`env=` / `ctx.env`). Deleting
  a variable has no additive spelling and is a taught error. The env
  overlay for in-process calls now rides this router (thread-confined, no
  lock — concurrent overlaid calls no longer serialise); outside a run,
  `os.environ` behaves exactly as stock Python, and bare `run(callable,
  env=…)` calls keep the classic guarded global patch as their fallback.
- **`serial=` and `exclusive=` — declared serialisation, the only kind
  left.** `@task(serial=True)` (and `.opts(serial=True)` per use) declares
  "this task owns the process globals": the scheduler runs at most one
  serial task at a time, *overlapping the full parallel pool*, and inside
  it footman restores the old conveniences safely — a real chdir to the
  task's resolved cwd, the env overlay applied to the real `os.environ`,
  both snapshotted and restored, with the routers and guards standing
  down. `@task(exclusive=True)` is the honest full drain for benchmarks
  and migrations: it runs with nothing else in flight, exempting only
  ancestors parked waiting on their own children. Lane waits are never
  silent (a note names the holder after two seconds), new starts yield to
  a waiting exclusive, and a fan-out child of a lane holder inherits the
  lane — a lineage extends a hold, it never contends with it. Body-calls
  keep inheriting the caller's regime; the markers are scheduling
  declarations, read at task boundaries — which is what keeps the whole
  design deadlock-free. **`footman.chdir()`** completes the serial story:
  real directory changes as a context manager (default target the task's
  own cwd, marker-grammar arguments, `ctx.cwd` kept in sync, everything
  restored) — legal in serial/exclusive tasks and a taught error in
  parallel ones.
- **Raw `subprocess` is quietly correct in parallel.** `subprocess.Popen`
  (and everything that funnels through it — `subprocess.run`, `os.popen`,
  third-party code) gets the task's context filled in when a spawn passes
  neither `cwd=` nor `env=`: the child starts in the task's directory with
  the snapshot-plus-overlay environment, exactly as `run()` would spawn it —
  with a one-time note suggesting the deliberate spellings. Explicit
  arguments always win, `env={}` stays a deliberately clean environment, and
  the `unmanaged` policy is the one off-switch.
- **An interactive task no longer stops the world.** `interactive=True`
  used to force the entire run sequential; it now claims the arbiter's
  *console* lane instead — one terminal owner at a time, granted
  atomically with any serial/exclusive lane so partial holds can't chain —
  while the parallel pool keeps running around it, captured. A finished
  sibling's buffered output queues until the wizard frees the terminal,
  so nothing splats over a prompt — and the status line *suspends* for
  exactly the ownership window instead of yielding for the whole run: it
  clears when a wizard takes the terminal and repaints, truthfully, the
  moment it frees. A wizard costs you the terminal — not the run's
  parallelism, and no longer its progress line. Listings and `--json`
  also carry a `lane` key (`serial`/`exclusive`), so the scheduling
  declarations show where `interactive`/`infinite`/`confirm` already do.
- **stdin is guarded like the global it is.** A bare `input()` (or any
  `sys.stdin` read) in a plain parallel task is now a taught error naming
  the exits — declare the value with `ask()`, or mark the task
  `interactive=True` to own the terminal — instead of a silent hang or a
  stolen read. Interactive and serial tasks, the framework's own boundary
  prompts, and anything outside a run pass through untouched.
- **In-process tool calls demote instead of breaking.** A `tools.*` call
  that would run in-process but needs a cwd other than the live process
  directory runs as its subprocess twin instead — same command, same
  semantics, right directory, still fully parallel; the in-process
  startup saving is the only loss (a `-v` note says so). Equal target and
  live cwd — the common single-package case — stays in-process untouched,
  and a serial task's cwd is really applied, so it stays in-process too.
  `Tool.opts()` gains **`cwd=` and `rel=`** beside `nofail`/`capture` —
  the bridge's per-call override, threading straight into `run()`, so
  `tools.npm.opts(rel="web").run("build")` roots one call in a
  subdirectory (and a bound `web_npm = tools.npm.opts(rel="web")` roots
  every call through it).
- **The process globals are guarded in parallel tasks.** `os.chdir` /
  `os.fchdir` raise a taught error (the cwd belongs to no one in a parallel
  run); `os.putenv`/`os.unsetenv` raise one too (they bypass env scoping
  even in plain Python); `os.getcwd` warns once per task toward
  `footman.cwd()`; `os.fork` warns that forking a threaded process is
  unsafe; and `multiprocessing`'s `BaseProcess.start` (which also covers
  `ProcessPoolExecutor`) notes that in-process workers inherit the real
  environment, not the task's overlay — and that self-parallelising tools
  lose little by taking the serial lane. Everything passes through
  untouched outside a run.
- **Breaking: footman never chdirs in a parallel task.** In-process calls
  used to get a real `os.chdir` (guarded by a process-wide lock) whenever
  the task's directory differed from the process cwd — which silently
  serialised the run for every such call, whether the callable cared or
  not. The chdir is gone: an in-process call whose target directory equals
  the live process cwd (the common single-package case) runs untouched and
  fully parallel; a *foreign* target is now a taught error naming the exits
  — run it as a subprocess (which gets `cwd=` for free), build paths from
  the new **`footman.cwd()`** (the task's resolved directory as a concrete
  path), or declare `@task(cwd="unmanaged")` if the call genuinely doesn't
  care. The env overlay for in-process calls is unchanged. Subprocesses were
  always spawned with an explicit `cwd=` and keep working exactly as before.
- **A tool's `.opts()` is now footman run-control; a tool's own globals move to
  `.flags()`.** A `tools.*` call is pure flags and positionals again: run-control
  no longer rides reserved call kwargs. `.opts(nofail=…, in_process=…,
  capture=…, title=…)` is a closed vocabulary that rides *beside* the call and
  never becomes a tool flag — `git.opts(nofail=True).push()` — mirroring a task's
  policy-vs-work `.opts()`. A tool's own global options (bound before a verb)
  move from `.opts(host=…)` to **`.flags(host=…)`** — `docker.flags(host="x").ps()`.
  Two consequences: `capture` and `title` reach the bridge for the first time
  (`pytest.opts(capture=False)` streams a run live), and a tool that really has
  a `--capture` (pytest) can now be spelled in the call, `pytest(capture="no")`,
  since footman's `capture` lives on `.opts()`. Migration: `tools.x(…,
  nofail=True)` → `tools.x.opts(nofail=True)(…)`; `tools.x.opts(host=…)` →
  `tools.x.flags(host=…)`.
- **The test-harness result is renamed `InvokeResult`.** `Runner.invoke()` now
  returns `footman.testing.InvokeResult` (was `Result`), freeing the prominent
  `footman.Result` to name the run-step `Result` above. Test code that calls
  `Runner().invoke(...)` and reads `.ok`/`.stdout`/`.exit_code` is unaffected —
  only the type's own name changed.
- **A trailing underscore is stripped from a CLI flag.** A task or parameter
  named with Python's keyword-escape underscore (`sync_`, `import_`) now maps
  to `--sync` / `--import`, not `--sync-` — matching the `tools.*` bridge,
  which already stripped it.
- **`fm --json` step entries carry `stdout` and `stderr`** separately, in place
  of the previous merged `output` field.
- **`help` is now a reserved task parameter name.** A flag or option parameter
  named `help` maps to `--help`, which footman intercepts anywhere on the line
  to render help and never run a task — so the option could never bind. Instead
  of silently shadowing the parameter, footman now rejects it at manifest-build
  time with a taught error that names the fix (rename to e.g. `show_help`). The
  check is precise: a required positional `<help>` or a variadic `*help` never
  produces `--help`, so both stay legal. This is the only reserved name — every
  other global must precede the first task, so a task parameter may reuse it.

### Fixed

- **`include()` carries a group's default and finalizers.** Grafting a module
  with `include()` kept its tasks but silently dropped the group's
  `@group.default` — breaking the bare-group command and hiding its options —
  and its `@finalize` hooks; both now come across, so a `tasks.py` composed from
  per-module `include()`s behaves the same as one that defines them directly.
- **`parallel()` collects a `SystemExit`.** A thunk that calls `sys.exit()` or
  `raise SystemExit(...)` — a common "fail this task" idiom — is now collected
  like any other failure instead of escaping and crashing the whole pool.
- **A task's `sys.exit("reason")` shows its reason.** A task body that raises
  `SystemExit` with a string (Python's "print this message, exit 1" idiom) used
  to fail with a bare `exited with code 1`, the message swallowed; the debugging
  tax was re-running the task under Python to read the traceback. The reason now
  surfaces in the failure line and the `--json` `error` field, rendered verbatim
  (no `SystemExit:` prefix). An int/`None` code (`sys.exit(2)` — the
  fail-with-a-code idiom) is unchanged. (A reason raised inside a `parallel()`
  thunk is still normalised to a code-only failure — the fan-out deliberately
  converts a `SystemExit` to a catchable `RunFailed`.)
- **`run("a | b")` teaches instead of mis-running.** A string command with a
  bare shell operator (`|`, `>`, `&&`, …) now raises a taught error: `run()`
  uses no shell, so the operator would otherwise ride along as a literal
  argument and the pipeline would silently not happen. Reach for a shell
  explicitly (`tools.bash("-c", …)`) or split into steps.
- **`fm footman tools provision`** skips the hand-written shell drivers instead
  of printing a spurious `uv tool install` failure for each.
- **`fm --help "group task"` accepts a quoted or dotted path.** A path handed as
  one shell token (`"docs serve"`) or dotted (`docs.serve`) is split into its
  components and resolved, instead of failing with a self-referential "did you
  mean 'docs serve'?". A genuinely unknown quoted path now names its first bad
  component and suggests a real neighbour.
- **A missing `include()` module names the call, not the file.** `include("x.y")`
  for a module that can't be imported raised "failed to import `<tasks.py>`",
  blaming the tasks file; it now raises a taught error naming the `include()`
  call and the reason (`include('x.y'): failed to import (ModuleNotFoundError:
  …)`), the same shape `plugin()` already gives.
- **`Runner.invoke` never hands off to uv.** The uv re-exec replaces the
  process via `execvp` when the interpreter running footman sits outside a
  project venv whose `uv.lock` pins footman. `Runner.invoke` could reach it,
  so an embedded invocation could exec the *host* process — under pytest-xdist
  that is the worker whose stdio carries the test protocol, and every
  Runner-based test died as `worker 'gwN' crashed` with no traceback. Embedded
  invocations now always run in-process; real CLI entry still hands off.
- **`fm footman docs cast` retries a cast that produced no output.** A cold
  pwsh (.NET startup) on a loaded CI runner can exceed the 1.5s pty settle
  window, and zero frames looked identical to a dead session. An empty cast
  now retries once with a 5s settle; the happy path pays nothing, and a
  genuinely broken shell still raises the same error.

### Documentation

- Added an abbreviations glossary with site-wide hover tooltips for footman's
  coined vocabulary (manifest, cascade, chain, taught error, …), split the
  execution-model pages (new **Asking for input** and **Progress & timing**
  guide pages), moved `@finalize`/`TaskView` onto the composing page, added
  mermaid diagrams for the dependency graph and completion refresh, and added
  docs-drift test guards so undocumented public symbols and stale version pins
  fail the gate.
- Documented the plain-prose output convention (task docstrings/`doc()` render
  as plain text in `--help` and export cleanly to markdown; footman paints no
  rich markup in the terminal — colour is the one styling it applies), and
  recorded an optional post-1.0 rich-terminal renderer in the roadmap.

## [0.19.0] — 2026-07-23

### Added

- **Per-subtree keep-going scoping.** The failure policy is now resolved *per
  node*, not run-wide: a keep-going gate keeps its own prerequisites going with
  it, while an independent task in the same run keeps its own policy. So
  `fm check deploy` — `check` keep-going, `deploy` fail-fast — surfaces every
  `check` failure *and* still bails `deploy` on the first, where before one
  task's `keep_going=True` forced the whole run to keep going. A command-line
  `-k`/`--fail-fast` still overrides every scope at once, and a task's own (or
  `.opts()`-set) policy wins over one inherited from a gate above it. True
  fail-fast reaps only the *fail-fast* subprocess trees still in flight on a
  failure, so a keep-going task's long-running child keeps going while a doomed
  fail-fast branch dies at once.
- **Per-use option overrides — `.opts()`.** A task or runnable group carries an
  `.opts(...)` that overrides its orchestration options *for one use* without
  touching the registered task: `pre=[fmt.opts(atomic=True)]`,
  `pre=[lint.opts(keep_going=True)]`, or a body call
  `deploy.opts(atomic=True)("prod")`. It takes the policy options — `keep_going`,
  `atomic`, `interactive`, `progress`, `confirm`, `infinite` — and rejects a
  task parameter with a taught error, because work goes in the call and policy
  rides beside it, the same split `tools.*` draw with their `.opts()`.
  Keep-going resolution now spans the whole dependency graph, so a declared or
  opted `keep_going` on a `pre`/`post` prerequisite counts, not only a task
  named in the chain. As a side benefit, `@task` now **forwards the wrapped
  function's signature** in the type system (parameters and return type), where
  a decorated task used to be typed `Callable[..., Any]` — so a body call with a
  wrong or missing argument is now a type error, not silently `Any`.
- **`TaskView` round-out for finalizers.** A `@finalize` hook's `TaskView` now
  reads a task's owning `group` (or `None` at top level), its policy flags
  (`keep_going`, `atomic`, `infinite`, `interactive`, `timed`, `confirm`) and
  its **cascade provenance** —
  `defining_dir` (the folder it was defined in), `shadowed` (the task it
  overrides one cascade level up), `shadow_chain` (it and everything it
  shadows), and `source_file` — so a finalizer can make decisions by *where* a
  task came from and *what* it overrode (e.g. gate every task defined under an
  `infra/` folder). New `set_opts(**overrides)` sets a task's policy for every
  use of it — the permanent, tree-wide counterpart to `.opts()`, taking the same
  options and rejecting a task parameter the same way. A command-line
  `-k`/`--fail-fast` still wins over a set `keep_going`.

### Changed

- **Homebrew resolution for host-read tools on macOS (stub generation only).**
  The tools footman reads straight off the host — git, docker, uv, never
  provisioned into an isolated prefix — resolve their Homebrew **keg**
  (`opt/<name>/bin/<name>`, which survives `brew unlink`) before falling back to
  `PATH`, so on macOS the stub describes the newest build (Homebrew git over
  Apple's older `/usr/bin/git`). Every **provisioned** tool (ruff, mkdocs,
  pytest, gh, …) still resolves on plain `PATH`, so a `provision --sync` prefix
  and a venv win and no stale `/opt/homebrew/bin` console-script shim can shadow
  them. Only `<tool> --help`/`--version` parsing is affected; running a
  `tools.*` task resolves on `PATH` as before.

### Fixed

- **`fm footman tools provision --sync` no longer strips plugin flags.** A
  provisioned prefix is a bare install, so reading pytest's stub from it dropped
  every `--cov*` flag (they come from pytest-cov). A driver can now name extra
  wheels to install alongside a tool — `Provision(plugins=("pytest-cov",))` —
  which `provision` adds with `uv --with`, so the prefix holds a plugin-complete
  pytest and the sync reads its full flag surface.
- **A `v`-prefixed `0.x` version is read whole.** The version a stub records is
  scraped from `<tool> --version`, and a tool that printed `v0.23.1` was recorded
  as `0.23.1`'s tail, `23.1`, because the match required a word boundary the `v`
  removed. It now reads `0.23.1` (markdownlint-cli2, and any tool that glues `v`
  to a `0.` version).

## [0.18.0] — 2026-07-22

### Added

- **Tri-state failure policy and true fail-fast.** Keep-going is now three-state:
  an explicit command-line choice wins, otherwise a task can declare its own
  (`@task(keep_going=True/False)`), otherwise the built-in fail-fast — so
  "unspecified" means *the code decides*, not a silent default. The new
  `--fail-fast` global forces fail-fast when a task declares keep-going, the
  mirror of `--keep-going`. And fail-fast now actually *is* fast: on the first
  failure it stops launching new work **and terminates the subprocess trees
  still running** — each child *and its own children*, so a tool's workers
  (pytest-xdist, `make -j`, a script's background jobs) die with it instead of
  orphaning — escalating SIGTERM to SIGKILL for anything that ignores the first
  signal. A task cut off this way reports as *cancelled*, kept distinct from a
  genuine failure; the run's exit code follows the real failure. `Ctrl-C` reaps
  in-flight trees the same way. `@task(atomic=True)` opts a task's subprocesses
  out of the kill — they run to completion, so a mid-write can't be truncated —
  and an `interactive` task's child stays attached to the terminal it owns.
  In-process runs are never killed (there's no child to signal).
- **Parameter forwarding.** A parameter marked `Annotated[T, forward]` (or the
  shorthand `Forward[T]`, like `Many[T]`) passes its value to every task this
  one dispatches — its `pre`/`post` prerequisites and a runnable group's
  surfaces — that declares a parameter of the same name; the rest run on their
  own defaults. So `@task(pre=[format, lint]) def check(fix: Forward[bool])`
  reaches `--fix` into the tasks that support it and lets the ones that don't
  just run, and the value chains through a callee that re-declares the marker.
  Precedence is CLI > forwarded > default, and a forwarded value overrides a
  default without rescuing a required parameter (a prerequisite stays runnable
  on its own). Two dispatchers sending different values to a shared
  prerequisite is a taught error, not a silent last-wins. `NoSplit[T]`,
  `Exists`, `IsFile`, and `IsDir` join `Many`/`Forward` as terse aliases for
  the bare markers.
- **Runnable groups.** A group gains a default action with `@group.default` —
  a typed function whose signature is the group's own options — so `fm lint`
  runs it while `fm lint markdown` still runs one surface. An empty-body default
  fans out the group's own tasks (`fm lint --fix` fixes what's fixable and lints
  the rest); a custom body is the escape hatch. A positional parameter on a
  default is a load-time error, because a bare word after a group names a child.
  The group tab-completes and self-documents like a first-class command, and is
  **callable from a task body** — `check` can call `lint(fix=fix)` the way it
  calls any task, running the default's action (or fanning out) in order.
- **Discovery hooks (`@finalize`).** A function decorated `@footman.finalize`
  runs once on the fully-merged task tree, after the whole `tasks.py` cascade is
  assembled but before dispatch — footman's `pytest_collection_modifyitems`. Use
  it to edit the tree in bulk: add a `pre` to every task whose name matches a
  pattern, switch a set of tasks off by policy, and so on. Because it runs at
  discovery the edits are part of the plan — an added `pre` runs and shows in
  `--dry-run`, a disabled task drops from listings — not a runtime surprise. The
  hook is handed a `Tasks` view of the tree; iterate it or index it by
  command-line name for a `TaskView` that reads (`pre`, `post`, `disabled`) and
  edits (`add_pre`, `add_post`, `disable`) each task through a defined interface,
  never footman's private attributes. Hooks run in cascade order — root's first,
  the folder nearest your cwd last, each seeing the previous edits.
- **Availability by `@requires` decorators.** Task availability moved from
  `@task(when=/requires=/reason=)` to stacked decorators: `@requires_dep` (a
  Python module importable), `@requires_tool` (a command on `PATH`),
  `@requires_env` (an environment variable set), and the generic `@requires`
  (a live predicate) they build on. Each carries its own `reason=`, so a task
  gated on both a missing package and a missing variable can say *both* — and
  `availability()` now collects **every** failing gate instead of stopping at
  the first, each in its own words. Gates are still re-checked live on every
  run, and a failing one lists the task with its reason rather than hiding it.
  **Breaking (pre-1.0):** `@task`'s `when=`, `requires=`, and `reason=` are
  removed; stack the decorators above `@task` instead.

## [0.17.0] — 2026-07-22

### Added

- **`check()` validators can read the other inputs.** A `check` callable that
  declares a second parameter receives the parameters to its left at their
  effective values (a provided value, else the default), read-only — so a version
  can be validated against the current release of the package named in an earlier
  argument, or an end-date against a start-date, without hardcoding a bound that
  drifts out of sync with the signature.
- **Interactive input, typed and CI-safe.** A parameter marked
  `Annotated[T, ask()]` prompts for its value when the CLI and env don't
  supply it, coercing the answer through the same pipeline as a flag — a
  `Literal` is a typed choice, a bad value re-asks — with precedence
  CLI > env > default > prompt. Off a terminal, under `--no-input`, or in
  `--json` it errors naming the flag rather than hanging. `prompt()`,
  `confirm()`, and `select()` are public primitives for asking mid-run, but
  **guarded**: called inside an ordinary task they raise a taught error,
  because the prompt would be swallowed by the capture buffer or race a
  parallel sibling — a task that genuinely owns the terminal declares
  `@task(interactive=True)` (it runs sequentially, uncaptured, with sole
  stdio). New globals: `--yes` (auto-answer confirms) and `--no-input`
  (never prompt).
- **Dynamic completions are recomputed fresh at <kbd>Tab</kbd>, not served
  stale.** A `suggest(fn)` completer queries live state (git branches, release
  candidates, deploy targets), so footman now runs it fresh in a bounded,
  isolated subprocess when you complete its value — rather than serving the
  snapshot baked into the manifest, which is exactly wrong for a build-critical
  answer. A slow or failing completer degrades to no candidates, never the old
  values; task names, options, and `Literal` choices still answer instantly from
  the cache.
- **The first <kbd>Tab</kbd> in a fresh directory builds the manifest instead of
  answering empty.** A cold completion cache used to stay blank until your first
  real `fm` run; now the first <kbd>Tab</kbd> builds it once (bounded, and out of
  the import-free hot path) and answers accurately. A slow `tasks.py` degrades to
  empty with the build finishing in the background, so the next <kbd>Tab</kbd> is
  warm — never a hung keystroke.
- **<kbd>Tab</kbd> completes file paths for path-valued arguments.** The
  path-valued globals (`-f`/`--tasks-file`, `-C`/`--directory`, `--config`) and
  any task option annotated `Path` now hand off to your shell's own file
  completion — `_files` in zsh, readline's filename completion in bash, and the
  fish/pwsh/nushell equivalents. A plain `str`/`int` value still completes
  nothing, so files are offered only where a path is actually wanted.
- **`fm -f <file> <TAB>` completes that file's tasks.** A one-file run reads
  its own tasks, so its completion now does too — cached under a key pairing the
  file with the cwd, separate from (and never overwriting) the plain-cwd cache.
  `-f` and `--config` are documented as orthogonal: each disables only its own
  cascade.
- **`fm footman tools provision`** — fetch the latest of every curated tool
  into one throwaway prefix, without polluting the machine. Almost every tool
  ships an installable PyPI wheel (the Rust and C++ ones included), so
  `uv tool install` into an isolated `UV_TOOL_DIR`/`UV_TOOL_BIN_DIR` covers
  most of them; bun comes from its own GitHub release (first, since the node
  tier runs through it), the node CLIs via `bun add`, and the Go CLIs (gh,
  eclint) from a release asset matched off the release's own asset list.
  `--sync` then rewrites the stubs against the prefix; `--clean` deletes it,
  and deleting the prefix is the whole undo.
- **Ten more curated tools, with generated stubs:** `gh`, `eclint`, `mypy`,
  `ty`, `twine`, `git-changelog`, `git-cliff`, `build`, `cmake`, `ninja`.
- **A sixth help dialect — Go's stdlib `flag`** — single-dash long options
  (`-color`) under `Usage of <prog>:` with descriptions on the next line, so
  a Go tool like `eclint` reads as fully as a clap or cobra one.

### Changed

- **python, pytest, and the shells are first-class tools.** `tools.python`
  and `tools.pytest` are `Tool` instances with generated stubs, not bespoke
  functions — `Tool` gained `path=` (so `tools.python` targets
  `sys.executable`) and `entry=` (so `tools.pytest` runs the arg-accepting
  `pytest.main` and stays parallel). The shells footman completes for —
  `tools.bash`/`zsh`/`fish`/`pwsh`/`nu` — run a command string through a real
  shell (`bash -c "…"`). `sh` is removed: it never used a shell, so `run("…")`
  is the honest spelling. A per-tool short-option policy (`none`/`only`/`all`,
  default `only`) controls whether a stub keys on a short flag, so python's
  `-m`/`-c` are complete without cluttering other tools.
- **footman's first-party tasks are now two plugins, `footman.docs` and
  `footman.tools`**, each opt-in on its own — a project can mount the
  end-user-facing doc generator without the maintainer-facing stub toolkit.
  A plugin's name is its command path, so a dotted name nests one group per
  segment (`["footman.docs"]` → `fm footman docs …`), and plugins that share
  a prefix meet under one namespace group without either owning it.

### Fixed

- **The tools reference sidebar is generated from the drivers, not
  hand-maintained** — `fm footman tools pages` regenerates the docs nav
  (alphabetically, between markers) and a test fails when a tool is added
  without it, so the stale "13 tools" sidebar can't recur.
- **The `--help` parser no longer swallows a flag's trailing punctuation** —
  clap's repeatable `--verbose...` and a manual's `--merge.` ending a sentence
  had become the keywords `verbose___` / `merge_`; a dot is now read only
  inside a name.
- **Bare lowercase value placeholders are read** (gh's `--assignee login`,
  docker's `--memory bytes`) from `--help`, while a man page's prose reference
  (`the --patch option.`) is left as the switch it is.
- **Bulleted option lists are read** — markdownlint-cli2 prints its options as
  `- --fix  …`, and the leading bullet no longer hides the flag.
- **A backslash in a tool's help** (mypy's `--exclude '\.py$'`) is escaped in
  the generated docstring instead of becoming an invalid escape sequence.

## [0.16.0] — 2026-07-21

### Added

- **The command line footman shows is now separate from the one it runs.**
  `run()` renders a normalised, syntax-highlighted invocation — options in
  their readable separated form, values shell-quoted, coloured by role the
  way `--help` colours a usage line — while execution takes whatever
  spelling the tool needs. `StepResult` carries both: `.command` (what
  `recording()` asserts and the terminal shows) and `.raw` (the exact
  executed bytes, what `--verbose` prints). One translation feeds both, so
  they can never disagree about what a call means.

- **The `tools.*` stubs are generated from the installed tools.** The
  bridge never went stale, because it transcribes nothing — but its stub
  could, because a stub describes a tool at a version. Now it is read
  from the tool: one file per tool under `footman/_stubs/`, carrying each
  flag's own help text as a docstring, the values it accepts as a
  `Literal`, and the one fact a bridge can never infer — how that tool
  spells "off" (`clean=off` → `mkdocs build --dirty`). Five help dialects
  are understood: click and argparse structurally, plus clap, cobra,
  commander and git's own `--[no-]flag` notation read from `--help`.
- **`fm footman tools …`** — `list` (what is curated and installed),
  `spec` (what a tool says about itself right now), `sync` (rewrite the
  stubs) and `audit` (fail when a stub and its tool disagree). Tools that
  are not installed are skipped *and named*, so a check can't quietly
  cover three of thirteen.
- **git's stubs are read from its manual, not its terse `-h`.** `git
  commit -h` lists 19 flags; the manual lists 37, and git is exactly the
  tool where autocomplete earns its keep. footman now reads `git help
  <verb>` for each git verb — twice the options, each with its own help,
  and a clean per-form `SYNOPSIS` that gives `git clone` its required
  `repository` while multi-form verbs (`git branch` lists *and* creates)
  stay permissive. The manual is read only when regenerating stubs, so it
  never becomes a runtime dependency; the extraction folds the manual's
  typographic punctuation to ASCII and keeps one sentence per flag.
- **git's globals reach `.opts()`, and every multi-command tool's
  `.opts()` keeps the chain typed.** `tools.git.opts(git_dir="…",
  work_tree="…").commit(…)` now completes git's global options — read from
  the `git help git` manual — and places them before the verb, where git
  requires them (`git -C x commit` runs in x; `git commit -C x` reuses a
  commit). Every tool with subcommands declares a self-returning `opts()`,
  so the chain after it stays typed even for a tool footman found no
  globals for.
- **`tools.<tool>.opts(...)` binds a tool's global options before the
  subcommand** — `tools.docker.opts(host="tcp://x").compose.up(detach=True)`
  runs `docker --host=tcp://x compose up --detach`. Some options belong to
  the tool, not the verb, and must precede it (cobra tools like docker
  reject a global after the subcommand); `opts` places them correctly and
  keeps chaining, typed per tool and returning the tool so the rest of the
  chain stays checked. A generic untyped `opts()` is available on any tool.
- **The stubs know each verb's positional shape.** Read from the tool's
  own usage line (or click's declared arguments): `mkdocs build` takes only
  options, so a stray positional is now a type error; `docker run` requires
  an image positionally, so `docker.run(image="x")` is caught. The parser
  is deliberately conservative — anything ambiguous stays permissive, so it
  never forbids a call the tool would accept, and git's idiosyncratic
  multi-form `-h` grammar is trusted for nothing.
- **A reference page per tool**, in a new **Tools** section of the docs.
  mkdocstrings renders each one straight from that tool's stub, so every
  flag arrives with the tool's own help text, its accepted values as a
  `Literal`, and the `off` spelling where one applies. The index table
  states the version each stub was read from and whether the tool can run
  in footman's process — built from the checked-in stubs, so the docs
  build needs nothing on PATH.
- **A type-level test for the stubs** (`tests/typecheck_tools.py`): a file
  of tool calls that is never executed and never collected, only
  type-checked. Its negative cases are the real assertions — since
  `**flags: Any` swallows an unknown keyword, a call that is *required to
  fail* is what proves a flag is declared and typed.

### Changed

- **Valued long options are executed attached** (`select="E"` →
  `--select=E`). This is invisible in what footman shows you — the shown
  line stays separated and readable — but it fixes two silent failures:
  an optional-value option whose value was read as a positional
  (`--abbrev 4` → `--abbrev=4`), and a dash-leading value read as another
  option (`--format -%h` → `--format=-%h`). The rule covers every tool,
  including undeclared ones. `recording()` assertions on `.command` are
  unaffected; assert on `.raw` for the exact spelling.

### Fixed

- **A wrapper verb's flags no longer leak into the wrapped command.**
  `tools.uv.run("pytest", "-q", frozen=True)` emitted
  `uv run pytest -q --frozen` — and uv never saw `--frozen`, because
  everything after `run`'s arguments belongs to pytest. The bridge now
  knows which verbs wrap a command (`uv run`, `uv tool run`, `coverage
  run`, `docker run`/`exec`, `docker compose run`/`exec`) and places their
  flags first: `uv run --frozen pytest -q`. The wrapper set is read from
  each verb's usage line and checked by `fm footman tools audit`.
- **Optional-value options are no longer mistyped as switches.** A tool
  that glues its placeholder to the flag — git's `--gpg-sign[=<key-id>]`,
  `--untracked-files[=<mode>]`, ruff's `--add-noqa[=<REASON>]` — was read
  as taking no value, so the stub rejected `gpg_sign="KEY"`, which is
  valid. These now type as `_ValuedFlag`: usable bare (`gpg_sign=True`,
  sign with the default key) *or* with a value, both spelling a valid
  command.
- **`off` now speaks each tool's own dialect.** It assumed the negation
  of a default-on flag is `--no-<name>`, which is wrong often enough to
  break real commands: `mkdocs build --no-clean` is rejected outright —
  the flag is `--dirty` — and five of mkdocs' eight negatable options
  disagree with the convention. The spelling is per-flag data only the
  tool knows, so footman asks: the new `footman._toolspec` reads click's
  `secondary_opts` (with defaults, types, and help text for the stubs
  and reference pages to come), and the exceptions ride in a table `off`
  consults. `clean=off` emits `--dirty`; `strict=off` still emits
  `--no-strict`; other tools are untouched. A test diffs the table
  against the installed tools, so a tool that changes its spelling fails
  a check instead of quietly producing a command it refuses.

## [0.15.0] — 2026-07-20

### Added

- **Counted progress: `progress(done, total)` and `track(iterable)`.**
  Work that knows how far along it is — 23 of 150 migrations, bytes of
  a download — is better evidence than any duration history, so a
  reported count now drives the live bar directly and outranks the
  estimator. That makes the bar honest on a task's *first* run, where
  the estimator is still gathering samples. A reporting task
  contributes a fractional unit to the run (three done and a fourth
  halfway is 3.5/4), so a chain of reporters fills smoothly and a mixed
  chain is smooth where it can be. `track()` is the ergonomic form —
  total from `len()`, `total=` for generators, report cleared if you
  break out early. Both are no-ops outside a run.
- **`fetch(url)` — download into footman's cache.** Cached by URL under
  `footman_cache_dir()` (so `FOOTMAN_CACHE_DIR` relocates it and the
  daily collector tends it), revalidated with ETag / `If-Modified-Since`
  rather than re-downloaded, optionally verified with `sha256=`, and
  copied anywhere with `into=`. A fetch is a **step**: `--dry-run`
  prints it without touching the network, `recording()` asserts on it,
  `--json` carries it, and it lands in the step lines beside `run()`.
  Byte counts feed the new progress bar. A cached copy survives a
  failed refresh, so a warm cache still builds offline.
  **Backends**: stdlib `urllib` by default — zero dependencies,
  deterministic, and the only one that can report bytes as they arrive
  — with `curl` (in Windows' System32 since build 17063, and on every
  POSIX box), `httpx`, and `requests` available when named, plus an
  explicit `auto`. Choose per call or set `[fetch] backend` anywhere on
  the config ladder: a machine behind a corporate proxy names curl once
  in `~/.config/footman/config.toml` and every project follows.
  Deliberately never automatic — a download that silently changed
  engine when an unrelated dependency appeared would change its TLS
  trust store and proxy semantics with it; a urllib failure instead
  raises a taught error naming that exact config line.

- **`inherited()` — extend an overridden task instead of replacing it.**
  A nearer `tasks.py` overriding a task by name usually means *and
  also*, not *instead of*. Inside the overriding task, `inherited()`
  hands you the task you shadow as the plain function it is:
  `inherited()(fix=fix)`. Forwarding is deliberately manual — the two
  signatures are independent, so automatic forwarding could only drop
  arguments silently or fail at run time, where spelling the call out
  shows the mismatch as you type it — and it chains through a cascade
  of any depth. Two
  discovery surfaces come with it: `fm --where <task>` now lists the
  whole shadow chain (winner first, each shadowed definition after),
  and `fm --help <task>` shows the inherited task's usage line, so the
  forwarding call can be read straight off it (additive `shadows` key
  in the manifest, present only when something is shadowed). Calling it
  where nothing is shadowed is a taught error naming `--where`.

- **`@task(infinite=True)` — tasks that run until you stop them.** A dev
  server or follow-mode tail isn't late, it's intentional: `infinite`
  implies `progress=False`, the status line yields to a one-time dim
  hint (`serve runs until you stop it — Ctrl-C`), and listings and
  `--help` carry a `(runs until Ctrl-C)` note (additive `infinite` key
  in the manifest). Distinguishing "don't time this" from "this never
  ends" came out of reading the cookbook's dev-server recipe.
- **Brands can rename the tasks file.** `App(..., tasks_file="acme.py")`
  sets the default filename a branded CLI looks for; per-project config
  (`tasks`) still overrides it, and the filename is baked into the
  cached manifest (additive) so the background completion refresh — a
  child that cannot know the brand — reads it back and rebuilds with
  the right file.

### Fixed

- **Completion output is LF on every platform.** Windows text-mode
  stdout translated the resolver's newlines to CRLF, so a shell reading
  lines literally — git-bash's `read` — kept the carriage return and
  completed `--fix\r`, planting a stray CR at the cursor. The resolver
  now writes bytes straight to the underlying buffer, which skips the
  translation and pins UTF-8 besides. Found by driving the real
  git-bash on a Windows runner, not by reading the code.
- **git-bash on Windows is detected and installed correctly.** A bare
  `fm --install-completion` inside git-bash used to answer "pwsh",
  because PowerShell's `PSModulePath` is machine-level environment and
  is set there too — so the user got a hook their shell would never
  read. Detection now checks the `MSYSTEM` variable git-bash exports
  first, and the `source` line written into `~/.bashrc` uses the MSYS
  spelling (`/c/Users/…`); a backslashed Windows path in a bash rc is a
  string of escapes that silently sources nothing. Install and uninstall
  build that line through the same helper, so uninstall can't strand it.
  The Windows CI job now drives the real git-bash to prove detection.
- **Recordings no longer depend on what's installed beside footman.**
  fish's autosuggestion drew on the build machine's PATH, so the same
  script recorded `factor` on macOS and `f77` — the Fortran compiler —
  on the Linux runner, which read as stray characters at the prompt.
  Autosuggestions are off in the recording's scratch config now: a cast
  should show footman's completion, not the host's toolchain.
- **Casts render dim text as dim.** pyte spells the bright ANSI
  colours `brightblack`; rich spells them `bright_black` and silently
  ignores a style it cannot parse — so anything dim was drawn in the
  normal foreground. That is the whole story behind the stray "77" in
  the fish recording: it was fish's own autosuggestion (`f77`, a real
  Fortran command on the Linux build machine; `factor` on macOS) drawn
  in white instead of grey, so it read as characters typed into the
  prompt rather than a suggestion.
- **Casts no longer type the terminal's own answers into the prompt.**
  The recorder answers cursor-position queries because PSReadLine and
  reedline paint nothing without one — but fish asks *mid-session* and
  then inserts the reply at the cursor, so `fm che` recorded as
  `fm ch77e`. Cursor replies are now sent only to the shells that need
  them (pwsh, nushell); bash and zsh never cared, and fish is visibly
  happier without. Verified by re-recording all five.
- **Casts no longer flash the shell's terminal queries.** pyte doesn't
  consume DCS sequences, so fish's XTGETTCAP capability probe rendered
  its hex payload as screen text for one frame before the prompt
  painted. Those sequences are terminal protocol, not screen content,
  and are now stripped before the emulator sees them; recordings also
  skip any blank frames before the first paint, so they open on the
  prompt.
- **The 0.12.0 changelog entry had a second `Changed` section** holding
  the merged-coverage note, which shipped in 0.13.0 — it now sits under
  the release that carried it.

### Documentation

- **The `serve` examples use `@task(infinite=True)`** on the home page,
  the README, and getting-started, matching what the runner now offers.
- **The cookbook.** Seventeen recipes across the whole surface — the
  parallel gate, passthrough, stacking validators, git-branch TAB
  completion via `suggest()`, build matrices, monorepo overrides,
  tasks that return data for `jq`, the coding-agent loop, testing
  recipes, and a branded CLI — closing the last open docs item from
  the original v0.4.0 audit.

## [0.14.0] — 2026-07-20

### Added

- **Install once, run anywhere: the uv handoff.** A globally-installed
  `fm` (`uv tool install footman`) now hands the invocation to `uv run`
  when the project's `uv.lock` pins footman and the running interpreter
  isn't already inside the project's environment — so plain `fm check`
  works from any uv project, at the project's pinned footman version,
  with the project's tools on PATH, no `uv run` prefix. The rule is one
  sentence: the lockfile declaring footman is what makes it fire. POSIX
  replaces the process (`execvp`); Windows spawns and waits, because
  `exec` there lies about exit codes. `--version`, completion management,
  and the TAB hot path never hand off; `uv = false` in `[tool.footman]`
  or `FOOTMAN_NO_UV=1` opts out for purists, and `-v` says when a handoff
  happened. uv only for now — poetry/pdm handoffs will be considered
  if there's a want for them.
- **A user-level config file completes the precedence ladder.**
  `~/.config/footman/config.toml` (honouring `XDG_CONFIG_HOME`; move it
  with `FOOTMAN_CONFIG`) now seeds every merge: personal defaults — a
  purist's `uv = false`, a permanent `progress = false` — that every
  project layer cascades over. The ladder, weakest to strongest:
  defaults, the user file, the root-to-cwd cascade (standalone
  `footman.toml` beating `pyproject.toml` within a folder, as is
  customary), `--config`, environment, flags. The docs gain a dedicated
  [Configuration](https://willemkokke.github.io/footman/configuration/)
  page for all of it.
- **The cache cleans up after itself.** At most once a day, a run
  spawns a detached collector that removes cache pairs whose project
  directory no longer exists (manifests now bake in the `cwd` they
  describe — additive) and pairs idle for 90 days. A fresh cache only
  plants tomorrow's stamp, so short-lived caches — a test suite's tmp
  dirs — never spawn anything; the invoking directory's own files are
  never touched; and every deletion is safe by construction, because
  the cache is derived state that rebuilds on the next run. It runs
  after the uv handoff, so a pinned project's own footman collects.
  `gc = false` disables it — from the user-level config file only,
  since per-project switches for a shared cache would lie (a `-v` run
  notes and ignores them); `FOOTMAN_NO_GC=1` is the blunt override.

### Changed

- **`--config` now replaces all discovered configuration** — the global
  file and the cascade both — instead of overlaying the cascade. With a
  user-level file in the ladder, "the named file is exactly what
  applies" is the only rule that stays one sentence; an explicit
  `--config` is total control by intent.

## [0.13.0] — 2026-07-19

### Added

- **Keyword-only parameters are options — required options without a
  default.** Python's `*` already says "must be named": a parameter after
  `*` (or `*args`) now maps to `--name`, and without a default it is a
  *required* option — the shape defaultless dicts and flags always had.
  Previously a defaultless keyword-only parameter was silently treated as
  a positional, which its own signature then refused at call time.
- **`fm footman docs shots` — terminal screenshots that cannot lie.** Runs
  a command on a real pseudo-terminal (colours, receipts, taught errors,
  exactly as a terminal renders them), collapses live rewrites to their
  final frame, and saves a macOS-style framed SVG via rich. Everything
  after `--` is the command line to capture; `--width`, `--title`, and
  `--cmd` shape the frame (the default executable is the invoking CLI, so
  branded CLIs screenshot themselves). rich is *not* a dependency: the
  task is gated with footman's own `@task(requires="rich")` and lists as
  unavailable without it — the availability machinery, dogfooded. The
  docs site now embeds these, regenerated on every build.
- **Both engines dress step lines identically.** A chain's buffered
  blocks (`fm lint format`) rendered plain `ok` lines while the same
  work inside a task-body `parallel()` (`fm check`) rendered the full
  terminal treatment — ✓ marks, bold names, dim commands, cyan times.
  Captured children now style for the terminal they replay onto, exactly
  like `parallel()` children always did; in-place rewrites and the
  announce line stay live-only, so no control bytes ever land in a
  capture buffer (or the `--json` envelope). One look, both engines,
  finishing the 0.12.0 unification.
- **Captured blocks no longer start with the `→ running` line.** The
  arrow announces what is running *now*, which is only worth a line while
  output is live — a TTY rewrites it in place, a streamed CI log may wait
  minutes under it, and both keep it. A buffered block (chains of two or
  more, `parallel()` in a task body) flushes when the task is already
  done, where "starting X" directly above "finished X" said nothing —
  those blocks now open straight with the completion line. Surfaced by
  the first `docs shots` screenshot, which faithfully photographed the
  redundancy.
- **`fm footman docs cast` — animated terminal recordings, no JavaScript.**
  Boots a real interactive shell — zsh, bash, fish, pwsh, or nushell —
  from a scratch config with footman's completion hook loaded via
  `--setup-completion`, types a keystroke script (`"fm che"`, `<TAB>`,
  `<ENTER>`, `<WAIT>`…), and replays the capture through a terminal
  emulator into one self-contained SVG animated by CSS keyframes with
  the session's own timing — an `<img>` plays it. **Every completion
  page now opens with its shell's own recording**: zsh's `_describe`
  menu (and a real `fm check` run to its receipts), fish's pager,
  PSReadLine's MenuComplete grid with tooltips, nushell's completion
  menu, bash's candidate list — re-recorded from live shells on every
  docs build. The session answers terminal interrogations (capability,
  cursor-position, and colour queries) like a plain xterm, because
  modern shells refuse to paint a prompt into silence, and it makes the
  pty its child's controlling terminal, because fish, nushell, and
  PSReadLine refuse interactive mode without one. Needs rich + pyte
  (the `shots` group), gated with `@task(requires=…)` like its sibling;
  the scratch HOME hands the invoker's completion cache through
  `FOOTMAN_CACHE_DIR`, so TAB answers exactly as it would at your
  prompt.
- **`fm footman docs globals` — the runner's global options as a markdown
  table.** Rendered straight from the CLI grammar: the same rows, in the
  same order, with the same words `--help` prints, with `{prog}` speaking
  a branded CLI's own name. `footman.markdown.globals_table(prog=…)` is
  the function behind it. This site's CLI reference now regenerates its
  table on every docs build, so it can never drift from the runner again
  (it had, three ways, which is how this feature earned its place).

### Changed

- **The published coverage report is the merged matrix picture.** The
  docs site's embedded report used to be re-measured on one
  ubuntu-only run, understating the number CI actually gates on. The
  merge job now renders the combined HTML — every OS, every Python,
  the real-shell jobs, and the docs build itself, which runs the whole
  taskdocs pipeline (five shell casts included) under coverage and
  merges in like any other job — and both docs builds embed that
  artifact instead of measuring their own slice.

## [0.12.0] — 2026-07-19

### Added

- **A progress bar that earns its confidence.** On a TTY, every run keeps
  one live status line on stderr: green runs teach footman how long each
  exact invocation shape takes (last 50 wall totals per chain + values +
  passthrough + serial/parallel, per directory), and once five recent runs
  agree closely enough, the line becomes a real bar — filling against the
  history's 90th percentile, clamped at 98% so it never claims done early,
  labelled with elapsed vs. typical time. Sparse or erratic history renders
  an honest bouncing pulse with elapsed time instead. Both parallel engines
  feed the same line, so a chain and a `parallel()` inside a task body
  finally present identically, running names appearing the moment each unit
  starts. Without a TTY, a confident estimate prints once as `eta ~5.8s` on
  stderr — CI still records, still learns. Off switches at every level:
  `--no-progress` for a run, `progress = false` in `[tool.footman]` for
  good, and `@task(progress=False)` for tasks whose duration has no rhyme
  (runs containing one never record and only pulse). Failed runs are never
  recorded; a missing, corrupt, or read-only history never fails a run.
- **`FOOTMAN_CACHE_DIR`** relocates every footman cache — completion
  manifests and timing history alike — in one variable; the XDG rules stay
  unchanged beneath it, and the completion hot path honours it with no
  re-install.
- **`-j/--jobs N` and `jobs = N` in `[tool.footman]` cap the parallel
  width** — in both engines: the scheduler's pool and `parallel()` inside
  task bodies. Unset, the default is now cores - 1 (never below 2) instead
  of effectively unbounded — the machine stays responsive while fan-outs
  stay real. The width is part of the timing key, so `-j2` runs build
  their own duration history.
- **Receipts are task-shaped: `✓ check  (5.2s)`.** The end-of-run summary
  speaks the same grid as the step lines — mark, name, time — with the
  name in bold cyan (same family as the steps, one rank up) and durations
  humanised. A single task's receipt *is* the total, so the separate
  `took` line only appears for chains of two or more, dimmed, where the
  wall total genuinely adds information. `--timings` keeps millisecond
  precision on the receipts. The `--json` envelope carries the total as
  an additive top-level `total_ms`.
- **One palette across the whole CLI.** `--help`, `--list`, `--tree`, the
  `--dry-run` plan, and error messages now speak the same visual language
  as the step lines and receipts: names and headers bold, groups bold
  cyan, mechanics and optional syntax dim, required placeholders cyan,
  the `fm:` error prefix red. Usage lines and synthesised examples are
  painted from one token grammar (prog/group/task/required/optional), so
  every command line footman prints is lit the same way. Colour is gated
  per stream on its own TTY — piped output, `--json`, `--where`, and
  `NO_COLOR`/`--no-color`/`TERM=dumb` runs stay byte-identical to before.

### Changed

- **Development Status: Alpha → Beta.** The PyPI classifier now says what
  the last few releases have shown: the surface is settling, the test bed
  is broad, and coverage is enforced. Pre-1.0 minors may still include
  breaking changes, as the header above says.

### Fixed

- **`-s/--sequential` now reaches inside task bodies.** It serialised the
  scheduler's tasks but `parallel()` inside a body still fanned out — so
  `fm -s check` ran just as parallel as ever. The user's sequential request
  now rides the task context (`ctx.sequential`) and `parallel()` honours
  it: `-s` means no concurrency anywhere. Serial runs already kept their
  own timing history (the flag is part of the chain key), so their
  estimates stay honest too.
- **A single-task invocation now streams live, with colour.** The default
  scheduler treated even one task as a parallel plan, so `fm check` — the
  most common shape there is — buffered everything into one uncoloured
  block flushed at the end, and `run()`'s TTY mode (green ✓ / red ✗, the
  in-place step rewrite) never fired. One node has nothing to parallelise:
  it now takes the sequential-live path, so steps appear as they happen and
  the TTY treatment applies. Chains of two or more keep the buffered
  non-interleaving contract unchanged.

## [0.11.0] — 2026-07-19

### Added

- **Parameter docs come straight from your docstrings.** Google
  (`Args:`), NumPy (`Parameters` + underline), and Sphinx (`:param x:`)
  styles are auto-detected per docstring; entries fill each parameter's
  help in `fm --help <task>`, in completion menus that show descriptions,
  and in the `--json --list` catalog — everywhere a `doc("…")` marker
  reaches, and the marker still wins for the same parameter. The body
  between the summary and the section becomes the task's **long help**,
  rendered by `--help` and carried as an additive `long` key. A docstring
  entry that names no real parameter warns, the same loudness a broken
  annotation gets.
- **`footman.docstrings` — the parser behind it, public and standalone.**
  Stdlib-only with no footman imports (lift the file into any project):
  `parse(text)` returns a frozen `Docstring` with `summary`, `long`, and
  `params`, tolerant of tabs, CRLF, uneven indentation, and unusual
  section orders.
- **The docs site follows your system's colour scheme by default**, with a
  three-state auto → light → dark toggle.
- **`fm footman docs page` / `site` — your tasks, documented.** A
  first-party plugin (mount with `[tool.footman] plugins = ["footman"]` —
  the two-line demo of the plugin system) renders a project's task tree as
  markdown: one page (scoped to the tree, a group, or a task, headings
  nestable for snippet includes, pipeable to pandoc) or a linked site
  (one file per task, an `index.md` per group) for zensical/mkdocs navs.
  Two flavors: portable CommonMark, or `material` with anchors and example
  admonitions. Content is phrased by the same code as `--help` — names,
  params, docstring help, defaults, synthesized examples — so pages can't
  drift from the CLI. Usage lines carry the CLI you invoked — a branded
  `acme` documents itself with no flag (`ctx.prog`, new on the task
  context, carries the invoking brand); `--prog` overrides. The renderer
  is public (`footman.markdown`), the manifest gains an additive `default`
  key, and footman's own docs dogfood both modes: the Task reference
  section and the embedded sample on the "Your tasks, documented" page are
  regenerated on every docs build.

### Changed

- **Step lines are columns now: mark · task name · command · time.** Every
  `run()` line carries the task it belongs to, padded so siblings align;
  on a colour terminal the name is bold, the command dimmed, and the
  `(time)` cyan, aligned to the widest command — the width rides the
  timing history, so a warm run aligns from its very first line (a cold
  one learns as it streams). Anonymous
  `parallel()` thunks show `…` — pass a named function or a
  `functools.partial` (its callee's name is used) for a real label.
  Durations everywhere now humanise past seconds: `4.1s`, `42s`, `1m10s`,
  `4h35m` — step lines included, which used to print raw seconds forever.
- **The run summary and live progress line moved to stderr.** One rule now
  governs the streams: *stdout is the answer, stderr is the commentary*.
  Task output — and footman's own answers (listings, help, `--json`
  envelopes) — stays on stdout; the `ok`/`FAIL` summary, `--timings`, and
  the live status line join warnings and errors on stderr. So
  `fm task > file` captures exactly what the task produced, and piping
  stdout keeps the live line visible on the terminal. Behavioral: anything
  that parsed the summary from stdout should read stderr (or use `--json`);
  wrappers that treat stderr bytes as failure can pass `-q`.

## [0.10.0] — 2026-07-19

### Added

- **`doc("…")` — per-parameter help, in the established `Annotated` marker
  idiom.** One line of the author's words per parameter, and it pays three
  times: it leads the option's line in `fm --help <task>`, it becomes the
  option's completion description in shells that render one (zsh, fish,
  nushell, PowerShell tooltips — options used to complete bare), and it
  rides in the `--json --list` catalog as an additive `doc` key. Inert at
  run time, like every marker.
- **An AI agents page and a generated `llms.txt`.** docs/agents.md ships a
  paste-ready CLAUDE.md/AGENTS.md snippet (the discovery loop, grammar,
  envelope, exit codes) plus edit-time and stop-gate hook recipes for
  Claude Code and Cursor. The docs build now generates `llms.txt` and
  `llms-full.txt` from the nav — an agent-readable index and full text of
  the site — and the Pages workflow builds through `fm docs build --check`,
  the same task devs run.

- **Tasks can return JSON.** A task's return value now lands in its `--json`
  entry under `returned`: return a dict (or list, string, bool, …) and a
  machine consumer gets it verbatim; return `None` and the key is absent. An
  `int` return keeps its existing meaning — the exit code, never data. The
  types footman coerces *in* (`Path`, `Enum`, `datetime`, `UUID`, `Decimal`,
  dataclasses, sets) serialise symmetrically on the way *out*; any other type
  is dropped loudly — a `returned_error` note in the entry, a warning on
  stderr, and the run's exit code untouched. The envelope stays `schema: 1`
  (additive only). `Runner.invoke(...).results[n].returned` already exposed
  the same value for tests.
- **`--json` now means: stdout is exactly one JSON document, whatever
  happened.** New envelopes cover every surface that used to fall back to
  text: a refusal (typo'd task, bad flag, broken tasks file, `--config`
  error, Ctrl-C) emits `{"schema": 1, "error": {"code", "message"}, "results":
  []}` alongside the stderr message; `--list`/`--tree`/bare `fm` emit the full
  task tree with parameter specs (`{"schema": 1, "tree": …}`) — the machine
  catalog agents were missing; `--dry-run` emits the parsed plan
  (`{"schema": 1, "globals": …, "plan": …}`); `--version` emits
  `{"schema": 1, "name": …, "version": …}`. The one exception is `--help`,
  which stays human — its machine twin is `fm --json --list`.

- **`--uninstall-completion [shell]` reverses the installer exactly**: the
  script file goes, the rc/profile line goes (UTF-16 profiles stay UTF-16,
  one BOM), and both directions are idempotent. When the shell itself has
  vanished from PATH, the script is still removed and the leftover rc line
  is printed for hand-removal.
- **A completion page per shell.** bash, zsh, fish, PowerShell, and nushell
  each get their own docs page: what installs where, the session-only form,
  what the completion menu shows, and — new — how to customise its colours
  and appearance with copy-paste snippets (`zstyle list-colors`,
  `fish_pager_color_*`, PSReadLine `-Colors`, nushell's `completion_menu`
  style block), each verified against the real shell.
- **`--setup-completion <shell>` prints the completion hook to stdout**, for
  enabling completion in the current shell only — no rc file touched:
  `eval "$(fm --setup-completion zsh)"` (bash/zsh), `fm --setup-completion fish
  | source`, or `| Out-String | Invoke-Expression` for PowerShell. A bare
  `--setup-completion` detects the shell, with the note on stderr so stdout
  stays clean for `eval`.
- **`fm`'s own global options now complete.** Typing a flag before the first
  task — `fm --<TAB>`, `fm --inst<TAB>`, `fm -<TAB>` — offers the globals
  (`--help`, `--list`, `--install-completion`, `-C`, …); a bare `fm <TAB>`
  still lists tasks only. Resolver-side, so no re-install is needed.
- **Python 3.14 is tested in CI**, including the free-threaded (no-GIL) build —
  footman runs tasks in real parallel threads, and the suite passes with the
  GIL disabled.

### Changed

- **nushell completions now carry descriptions.** The external-completer hook
  returns `{value, description}` records, so task and group names show their
  one-line docstring in nushell's menu instead of being stripped to bare names.
  Re-run `fm --install-completion nushell` to pick it up.
- **zsh completions now use the native `_describe` builtin.** The rich-
  description hook right-aligns descriptions into a column and honours your
  completion styling (`list-colors`, `descriptions` `format`) — the same look
  `_git` and `_npm` produce — instead of the hand-formatted `name -- desc`.
  Re-run `fm --install-completion zsh` to pick it up.

### Fixed

- **The completion-latency headline is now the number users actually get.**
  The docs quoted ~19/20/23 ms in different places; the honest figure for the
  installed hook path (`fm --complete` via the console script) is **~25 ms**,
  now measured directly by `scripts/bench_completion.py` and quoted
  consistently everywhere. The ~15× multiplier vs re-importing runners is
  unchanged.
- **`fm --help <typo>` now refuses with a suggestion** (exit 2, `unknown task
  or group 'nope' — did you mean …?`) instead of silently printing the global
  help with exit 0. With a real target on the line (`fm --help deploy prod`),
  extra words are still tolerated as argument values.
- **A misplaced global option is taught by position, not treated as unknown.**
  `fm check --json` now says ``--json is a global option — it goes before the
  first task name`` instead of `unknown option`; same for short aliases
  (`fm lint -k` names `--keep-going`). A task parameter that shares a
  global's name still wins by position, as before.
- **Bare `fm` now ends with the same `--help <task>` pointer the help screen
  shows** — the no-argument path is exactly where a newcomer lands.

## [0.9.0] — 2026-07-18

### Changed

- **In-process tools import only when they actually execute.** Resolving a
  tool's `[console_scripts]` entry point is now pure metadata; the `.load()`
  that imports the tool's module is deferred into the callable footman runs.
  So a `--dry-run`, a `recording()` test, or a branch you never take costs
  zero tool imports — the property that made duty's lazy design nice, now
  without the build-vs-run split (a call is still always a call). One
  behaviour change: a console-scripts entry that exists but fails to import
  now surfaces as a task failure with the real error, instead of silently
  falling back to a subprocess.
- **Exit codes now follow the documented contract.** A binding refusal — a bad
  coercion, an out-of-bounds value, an unknown option — exits **2**, not a flat
  `1`; a `run()` command that fails propagates the command's own exit code; and
  a failing `parallel()` thunk propagates too. `fm` mirrors what it ran.
- **`--no-color` / `NO_COLOR` / `TERM=dumb` drop the live progress line
  entirely**, matching piped output, instead of rewriting it without escapes.
- **In-process tools honour cwd and env.** They run from the folder that defined
  the task and see its environment overlay — the run-from-defining-folder
  contract the subprocess path already obeyed — and `run(..., capture=False)`
  streams output live instead of buffering it.

### Added

- **`@task(requires=...)` — gate a task on optional dependencies,
  import-free.** Names Python modules a task needs, checked with
  `importlib.util.find_spec` (which locates without importing), so a shared
  library can carry release tasks with heavy third-party deps: keep the
  `import` in the body (paid only when the task runs), and a missing package
  lists the task as `(unavailable: <reason>)` and refuses to run cleanly,
  instead of a raw `ModuleNotFoundError`. Reuses the `when=` availability
  machinery — shown in `--list`/`--help`, re-checked live, a `pre`/`post`
  on it fails hard. New docs: *A shared library with heavy or optional
  dependencies* in Composing tasks.
- **`off` — disable a flag a tool turns on by default.** `False`/`None`
  mean *omit* (so a task parameter's default flows through), which left no
  way to spell a negation. `strict=off` → `--no-strict` fills the gap and
  completes the boolean story (`True` → `--flag`, `off` → `--no-flag`);
  it's the same as naming the negation directly (`no_strict=True`) but
  reads as intent and lets a variable drive it
  (`directory_urls=pretty or off`). Typed in the stubs, so it autocompletes
  and a garbage value is still a type error.
- Filled a real gap in the `ruff.check` stub — `exit_zero`,
  `exit_non_zero_on_fix`, `quiet`, `silent`, `verbose`, `isolated`,
  `cache_dir` now autocomplete, so you're guided to the right flag instead
  of guessing a name like `exit=` that `**flags: Any` silently accepts and
  a `False` value quietly omits. Docs now spell out that escape hatch: an
  unknown flag either errors at the tool (truthy) or is dropped (`False`/
  `None`), and a literal `"--flag"` positional always sidesteps it.
- **Tool autocompletion via stubs — zero runtime cost.** `tools.pyi` gives
  IDEs and type checkers typed verbs and common flags for the curated
  tools (`tools.ruff.check(` completes `fix=`, `select=`, …; `fix="yes"`
  is a type error), while the runtime bridge stays a few mechanical lines
  the stub never touches. Every stubbed verb ends in `**flags: Any` and
  unknown verbs fall through to `Tool`, so the stub can suggest but never
  forbid — drift degrades a hint, not a run. `None` is typed as the omit
  sentinel everywhere, matching the translation rules.

- **The tools bridge runs Python tools in-process.** `Tool(...,
  in_process=True)` (or `in_process=True` per call) resolves the tool's own
  `[console_scripts]` entry point and calls it with `sys.argv` patched —
  the no-transcription contract, minus the interpreter spawn. `mkdocs`,
  `zensical`, and `coverage` default to it. Beyond speed this is a
  correctness fix on macOS, where SIP strips `DYLD_*` from child processes:
  a tool needing Homebrew's native libraries (mkdocs + cairo) only works
  in-process. Preferences fall back to a subprocess when no entry point
  exists; per-call demands error with a taught message. And parallelism
  survives: capture routes through the per-task stdout router
  (thread-confined — also fixing a pre-existing race where the global
  redirect could cross-contaminate concurrent in-process captures), and
  argument-accepting entries (click commands, `main(argv=None)` — nearly
  all of them) are called directly. Only a legacy zero-arg `main()` gets
  the `sys.argv`-patching fallback, and only those serialise.
- **Completions that teach.** In zsh and fish, task and group descriptions
  render next to the candidates; `--help` ends with a synthesised `Example:`
  invocation built straight from the signature; and a "did you mean?" hint fires
  at every not-found site (unknown task, option, choice, or `--where` target).
  Bare `fm` now lists the tasks instead of erroring.
- **Completions that stay fresh.** A stale-while-revalidate background refresh
  rebuilds a directory's cached manifest once it ages past `[tool.footman]
  completion.max_age` (default 10 min; `off`/`0` disables) — the <kbd>Tab</kbd>
  returns the cached answer instantly and never blocks on the rebuild.
- **`--opt=value` completes in every shell**, and value-bearing globals (`-C`,
  `--config`, `--tasks-file`, …) no longer send the completion walk descending
  as if their value were a task.
- **`capture`, `Runner`, `Result`, and `recording`** import straight from
  `footman` (previously only from `footman.testing`).

### Fixed

- **PowerShell completion after a space.** Windows PowerShell 5.1 and pwsh
  7.0–7.2 silently drop an empty-string argument to a native command, so
  pressing <kbd>Tab</kbd> after a space re-completed the previous word instead
  of the fresh position. The hook now flags the empty position with
  `--empty-partial` and the resolver supplies the `""` itself. **Re-run
  `fm --install-completion pwsh`** to pick up the new hook.
- **`--help` never touches the filesystem.** `fm --install-completion fish
  --help` used to write rc files before printing anything; and `fm --help` with
  no tasks file now shows the global help (so a stuck newcomer sees `-f`/`-C`),
  not a bare one-liner.
- **`-C/--directory` restores the working directory** afterwards, so an
  in-process caller (a test runner) is no longer left in the changed folder.
- **`-f/--tasks-file` no longer poisons** the directory's cached completion — a
  one-off `-f` run leaves <kbd>Tab</kbd> describing the real cascade.
- **Plugins and the cascade are sturdier.** A plugin that fails to import is
  taught at exit 2 instead of dumping a traceback on every invocation;
  `availability()` never crashes on a `requires=` whose parent package raises; a
  cascade file that registers tasks and then raises no longer leaves ghost tasks
  behind; each `tasks.py` gets its own copy of a sibling `import helpers`; and
  provider trees are isolated per project so one project's tasks can't leak into
  another.
- **Completion install is more robust.** bash `COMPREPLY` is glob-safe
  (`printf %q`), rc-file edits sniff BOM/encoding so a UTF-16 Windows PowerShell
  profile no longer crashes the install, and installs target the rc files shells
  actually read (`$ZDOTDIR` for zsh; the login profile alongside `.bashrc` for
  macOS bash).
- **Loud errors where footman used to stay silent** — a missing or typo'd
  `--config` file, a `**kwargs` task, `=value` on a flag-shaped global, and a
  `--` handed to an option as its value.
- A broad correctness pass across type coercion (strict env and variadic values,
  unions that carry both choices and types, dict value-type markers), the
  scheduler (each explicit chain segment runs; `parallel()` steps surface in
  `--json`), and the tools surface (`tools.run`/`tools.sys` resolve to Tools;
  `installed_version()` decodes UTF-8).

## [0.8.0] — 2026-07-17

### Added

- **PowerShell completion installer.** `fm --install-completion pwsh` (alias:
  `powershell`) writes a `Register-ArgumentCompleter` hook and dot-sources it
  from the profile PowerShell itself reports (`$PROFILE`), for PowerShell 7+
  and Windows PowerShell alike. Idempotent, branded, and covered by a
  functional test that drives PowerShell's own completion engine on every CI
  platform.
- **nushell completion installer.** `fm --install-completion nushell` (alias:
  `nu`) writes an external-completer hook sourced from the config nushell
  itself reports (`$nu.config-path`). The hook *wraps* any existing external
  completer (carapace, …) — it answers for `fm` and passes every other
  command through. Verified against a real nushell. Every shell footman
  promised is now installed with one command.
- **`tools.*` became a bridge, not a transcription.** Every executable on
  PATH is a tool with no declaration (`tools.terraform("plan")`), attribute
  access chains subcommands (`tools.docker.compose.up(detach=True)`), and
  keyword arguments translate mechanically (`fix=True` → `--fix`, lists
  repeat, single letters go short, trailing `_` escapes keywords). This is
  a deliberate answer to the drift in hand-transcribed wrappers — duty's
  `ruff.check(show_source=True)` emits a flag modern ruff rejects; a bridge
  has nothing to go stale. `tool.installed_version()` (cached, resolved
  outside the task context) covers the rare version-dependent branch.
  Curated spellings for ruff, uv, git, docker, bun, mkdocs, zensical,
  coverage, cspell, prek, markdownlint (-cli2), basedpyright; pytest keeps
  its in-process path. A tools *plugin* mechanism was considered and
  rejected: tools are plain objects, so publishing them is publishing
  Python — an import already beats an entry point.
- **A live progress line for parallel runs.** On a TTY, the scheduler keeps
  one status line (`/ 2/5 (1 failed)  running: lint, test`) between the
  finished tasks' output blocks. Event-driven (no timer thread), always
  cleared before a block lands so output stays non-interleaved, red only
  when something failed, plain under `NO_COLOR`/`--no-color`, and absent
  entirely under `--quiet`, `--json`, or a pipe. The last item on the
  README's original roadmap besides `tools.*` growth.
- **Bare `--install-completion` detects your shell.** No argument needed:
  footman walks the parent-process tree (the way typer's `shellingham`
  dependency does — without the dependency, and correctly skipping over
  `uv run`), with the `PSModulePath` tell on Windows and `$SHELL` as the
  last resort. Undetectable → a taught error naming the five options.
  Verified through a real shell with `$SHELL` deliberately lying.

### Documentation

- **The README is a front door now** — what footman is, why it exists, one
  taste, and pointers into the site — instead of a 460-line hand-maintained
  copy of the documentation that drifted on every change.
- Two new pages: **CI & automation** (the `--json` envelope contract, exit
  codes, keep-going/sequential in CI, agents) and **Troubleshooting** — a
  catalogue of every taught error, generated against real output, with the
  standing invitation that a raw traceback is a footman bug.

### Changed

- **Every completion hook is now functionally tested against its real
  shell.** New tests drive bash (`COMP_WORDS`/`COMPREPLY`), zsh (the hook's
  exact expansion idiom), and fish (its own `complete -C` engine) alongside
  the existing pwsh and nushell tests — and a dedicated `shells` CI job
  installs zsh, fish, and a pinned nushell so none of them can skip
  silently. The bash 3.2 slice bug taught us: a hook that hasn't met its
  shell isn't tested.

### Fixed

- The pwsh installer now writes its hook into **every** PowerShell profile
  present — PowerShell 7 and Windows PowerShell keep *different* `$PROFILE`
  files, so on a machine with both, completion previously landed in only
  one of them (and not necessarily the one the user asked for). The hook
  runs on both shells unchanged (`Register-ArgumentCompleter` exists since
  PS 5.0), so whichever PowerShell opens, TAB works.
- Completion no longer re-offers an option the segment already has —
  `fm lint --fix <TAB>` suggests what can still bind, not `--fix` again.
  Repeatable (`list`/`dict`) options rightly stay on offer, and a fresh
  segment starts with a clean slate.

## [0.7.0] — 2026-07-17

### Removed

- `--refresh-manifest` — it was parsed and never read; the manifest already
  rebuilds on every execution-path run, so the flag had no job to do.
- `manifest.is_stale` and the manifest's `sources` block — scaffolding for a
  staleness check no live path ever consulted.
- `reset()` is no longer re-exported from the package root (it remains in
  `footman.registry` for test suites); it was a test-suite helper living on
  the public namespace.

### Changed

- `footman.tools` is now a real public export (`__all__`, lazy) — it was
  load-bearing in the docs and footman's own tasks file while officially not
  existing.
- The `import footman` vs `import typer` cost claim is now backed by a
  committed script (`scripts/bench_import.py`), and the comparison page's
  repro commands include the required `--group comparison`.

### Added

- **Shell completion installers.** `fm --install-completion bash|zsh|fish`
  writes the hook and (bash/zsh) one guarded `source` line into your rc
  file; fish needs no rc edit at all. Idempotent, branded (`acme
  --install-completion zsh` installs for `acme`), and the generated hook
  stays on the cached stdlib-only fast path. The bash hook survives macOS's
  bash 3.2 (whose quoted array slices collapse to a single word — found the
  hard way, tested for keeps).
- **Chain-aware completion.** The resolver now walks segments the way the
  splitter does — exact positional arity, then a trailing `Many`/variadic
  consumer, then the next word starts a new segment — so
  `fm format lint --fi<TAB>` completes *lint's* options, a satisfied task
  offers the next task names, `+` resets, and after `--` nothing is offered
  (it's the passthrough's). Latency is unchanged: same one-file-read walk.
- **Composable task surfaces.** Three mechanisms, one contract (resolve at
  import time, re-check availability live): `@task(when=…, reason=…)`
  disables-but-lists a task that can't run here (pytest-skip semantics —
  shown in `--list`/`--help`, refuses to run with the reason, a `pre`/`post`
  dependency on it is a hard failure); `include(source, into=…, only=…,
  exclude=…, override=…)` grafts another module's tasks into your tree
  (loud on collisions and typos, provider imported under a registry capture
  so nothing leaks, adopted tasks run from *your* directory); and packages
  advertise a `Group` under the `footman.tasks` entry point that projects
  opt into via `[tool.footman] plugins = ["name"]` — never auto-loaded,
  user names shadow plugin groups, missing plugins are crisp errors naming
  what *is* installed. New docs page: *Composing tasks*.
- `registry.capture()` — the public seam for importing task-defining modules
  without touching the live registry.

## [0.6.0] — 2026-07-17

### Added

- **A first-party testing story.** `footman.testing` ships `Runner.invoke`
  (drive a full command line in-process: exit code, stdout/stderr, structured
  `TaskResult`s, isolated completion cache), `recording()` (capture the
  commands a block *would* run, silently, without executing), and re-exports
  the new public `use_context()`. Three pytest fixtures — `fm`,
  `fm_project`, `fm_record` — auto-load via a `pytest11` entry point; pytest
  is still not a dependency (only pytest itself imports the module).
  footman's own suite dogfoods them. New docs page: *Testing your tasks*.
- **Validation markers**, all in the `Annotated` idiom: `exists` / `isfile` /
  `isdir` path requirements and `between(lo, hi)` numeric bounds (a bare
  `range` works for ints), both validated eagerly with taught errors;
  `env("VAR")` fallbacks (CLI > env > default, the env value flowing through
  the same coercion/bounds/checks as a CLI token); and `check(fn)` custom
  validators, run post-coercion, per element for collections. `env()` on a
  parameter without a default (or on a dict) is a taught build-time error.
- **Opaque annotations warn.** A parameter whose annotation resolves to
  nothing footman can coerce (an unresolved name, a value) now emits a
  `UserWarning` instead of silently treating every value as text.

### Documentation

- Fixed the dynamic-completion examples: the documented `suggest[str, fn]`
  syntax never existed — the real form is `Annotated[str, suggest(fn)]`.

## [0.5.0] — 2026-07-17

### Added

- **A real help story.** `fm --help` documents the runner itself (usage
  grammar plus the full global-options table, generated from the same table
  the parser reads). `fm --help <group>` shows a group's tasks, and
  `fm --help <task>` renders per-task usage, docstring, and typed
  positional/option tables from the manifest. `-h`/`--help` anywhere before
  `--` turns the whole line into a read-only help request — `fm deploy --help`
  can never execute `deploy`.
- **`bool` is now a real token type.** `dict[str, bool]` values and
  `list[bool]` elements parse `true/false/1/0/yes/no/on/off` (eagerly
  validated with a taught error) instead of collapsing to a flag or silently
  reading every value as `True`.
- **Dependency-cycle detection.** A cyclic `pre`/`post` graph is a taught
  error naming the cycle; previously it ran nothing and exited 0.
- **`py.typed` marker** — downstream type checkers now see footman's inline
  types (the `Typing :: Typed` classifier was already claiming they could).
- **Ctrl-C is handled**: pending tasks are cancelled, the run reports
  `interrupted`, and the exit code is 130 — no more raw traceback.

### Changed

- **Comma-splitting is now the default for collections.** A `list` / `dict`
  parameter splits a single token on commas (`--tag a,b,c` → `["a", "b", "c"]`)
  out of the box, in addition to the repeatable form (`--tag a --tag b`). The
  old opt-*in* `csv` marker is replaced by an opt-*out* `nosplit` marker, for
  the parameters whose values may themselves contain a comma.
- **`--json` output is now enveloped**: `{"schema": 1, "results": [...]}`
  instead of a bare list, so post-1.0 additions never break consumers. This is
  the blessed machine surface; future changes will be additive.
- **Errors name their culprit.** A failing tasks-file import names the file; a
  duplicate task name is reported as the user error it is (not "failed to
  import"); a malformed discovered config TOML warns and is skipped; a
  malformed `--config` file is a hard error; a *strict* `suggest()` completer
  that raises now fails the run (it used to silently disable the validation it
  promised).
- Dry-run now records `StepResult`s (and honours `quiet`), so tests can assert
  which commands *would* run without executing anything.

- Releases are gated: `release.yml` now runs the full CI suite on the tagged
  commit and refuses to publish unless the tag, `pyproject.toml`,
  `__version__`, and the changelog all agree on the version (and the wheel
  ships `py.typed`).
- Coverage is enforced (`fail_under = 92`), and the strict docs build runs on
  every PR instead of only after merge.

### Fixed

- `fm --help <task>` used to **execute the task**.
- `run("...")` string commands are no longer `shlex`-split on Windows —
  backslash paths survive; the string goes to `CreateProcess` whole.
- Non-UTF-8 subprocess output no longer crashes `run()` (decoded with
  `errors="replace"`).
- Digit-lookalike tokens (`"²"`) are taught type errors instead of an
  `int()` traceback.
- An exception escaping a worker thread in a parallel run (including a
  `KeyboardInterrupt` raised inside a task) now propagates instead of being
  silently dropped and reading as success.

### Documentation

- Docstrings converted from reStructuredText to Markdown (renders natively via
  mkdocstrings).

## [0.4.0] — 2026-07-16

### Added

- **Custom-branded CLIs.** A public `App(name, prog, version)` carries your
  project's names and version and threads them through every user-facing string
  (the `--version` banner, the `prog:` error prefix, the completion hint) — so
  you can ship an internal tool under its own name while it stays footman
  underneath. footman's own `fm`/`footman` are now just the default-branded
  `App()`.
- **API reference** on the docs site, generated from docstrings via
  [mkdocstrings](https://mkdocstrings.github.io/).
- **Coverage report** embedded directly in the docs via an inline `<iframe>`,
  regenerated on every deploy.

## [0.3.0] — 2026-07-16

### Added

- **Monorepo task cascade.** Every `tasks.py` from the repo root (the nearest
  `.git`) down to the current directory is merged into one command set: new
  names append, collisions are overridden nearest-wins, and groups merge. Each
  task runs from the folder that defined it.
- **Config discovery.** `[tool.footman]` in `pyproject.toml` and a standalone
  `footman.toml`, walked up to the repo root (nearest wins), plus a
  `--config PATH` override.
- **Per-directory completion cache**, so each folder of a monorepo caches its
  own merged cascade.
- **Documentation site** (Zensical) published to GitHub Pages.

## [0.2.0] — 2026-07-16

### Added

- **Richer type system:** union parameters (validated and coerced by
  specificity), `Many[T]` one-or-many values, opt-in `csv` comma-splitting,
  `dict[K, V]` (including `dict[str, list[...]]`), and custom types via their
  typed constructors.
- **Execution layer:** `run()` (subprocess or in-process callable, capture with
  replay-on-failure, dry-run, `--json` steps), the typed `tools.*` wrappers, and
  opt-in `Context` injection.
- **Parallel-by-default DAG scheduler:** independent tasks run concurrently;
  `pre`/`post` dependencies, the `parallel()` helper, `-s/--sequential`, and
  grouped non-interleaved output.

## [0.1.0] — 2026-07-16

### Added

- Initial release: typed function signatures become CLIs (flags, options,
  positionals, choices), modules become nested command groups, a separator-free
  chain grammar, and instant shell completion answered from a cached JSON
  manifest without importing your code.

## 0.0.2 — 2026-07-16

- Placeholder release claiming the `footman` name on PyPI (MIT license, project
  URLs). Not tagged in git.

## 0.0.1 — 2026-07-16

- Placeholder release claiming the `footman` name on PyPI. Not tagged in git.

[Unreleased]: https://github.com/willemkokke/footman/compare/v0.52.1...HEAD
[0.52.1]: https://github.com/willemkokke/footman/compare/v0.52.0...v0.52.1
[0.52.0]: https://github.com/willemkokke/footman/compare/v0.51.0...v0.52.0
[0.51.0]: https://github.com/willemkokke/footman/compare/v0.50.0...v0.51.0
[0.50.0]: https://github.com/willemkokke/footman/compare/v0.49.1...v0.50.0
[0.49.1]: https://github.com/willemkokke/footman/compare/v0.49.0...v0.49.1
[0.49.0]: https://github.com/willemkokke/footman/compare/v0.48.0...v0.49.0
[0.48.0]: https://github.com/willemkokke/footman/compare/v0.47.0...v0.48.0
[0.47.0]: https://github.com/willemkokke/footman/compare/v0.46.0...v0.47.0
[0.46.0]: https://github.com/willemkokke/footman/compare/v0.45.0...v0.46.0
[0.45.0]: https://github.com/willemkokke/footman/compare/v0.44.0...v0.45.0
[0.44.0]: https://github.com/willemkokke/footman/compare/v0.43.0...v0.44.0
[0.43.0]: https://github.com/willemkokke/footman/compare/v0.42.0...v0.43.0
[0.42.0]: https://github.com/willemkokke/footman/compare/v0.41.0...v0.42.0
[0.41.0]: https://github.com/willemkokke/footman/compare/v0.40.0...v0.41.0
[0.40.0]: https://github.com/willemkokke/footman/compare/v0.39.1...v0.40.0
[0.39.1]: https://github.com/willemkokke/footman/compare/v0.39.0...v0.39.1
[0.39.0]: https://github.com/willemkokke/footman/compare/v0.38.1...v0.39.0
[0.38.1]: https://github.com/willemkokke/footman/compare/v0.38.0...v0.38.1
[0.38.0]: https://github.com/willemkokke/footman/compare/v0.37.0...v0.38.0
[0.37.0]: https://github.com/willemkokke/footman/compare/v0.36.0...v0.37.0
[0.36.0]: https://github.com/willemkokke/footman/compare/v0.35.0...v0.36.0
[0.35.0]: https://github.com/willemkokke/footman/compare/v0.34.0...v0.35.0
[0.34.0]: https://github.com/willemkokke/footman/compare/v0.33.0...v0.34.0
[0.33.0]: https://github.com/willemkokke/footman/compare/v0.32.0...v0.33.0
[0.32.0]: https://github.com/willemkokke/footman/compare/v0.31.0...v0.32.0
[0.31.0]: https://github.com/willemkokke/footman/compare/v0.30.0...v0.31.0
[0.30.0]: https://github.com/willemkokke/footman/compare/v0.29.1...v0.30.0
[0.29.1]: https://github.com/willemkokke/footman/compare/v0.29.0...v0.29.1
[0.29.0]: https://github.com/willemkokke/footman/compare/v0.28.1...v0.29.0
[0.28.1]: https://github.com/willemkokke/footman/compare/v0.28.0...v0.28.1
[0.28.0]: https://github.com/willemkokke/footman/compare/v0.27.1...v0.28.0
[0.27.1]: https://github.com/willemkokke/footman/compare/v0.27.0...v0.27.1
[0.27.0]: https://github.com/willemkokke/footman/compare/v0.26.0...v0.27.0
[0.26.0]: https://github.com/willemkokke/footman/compare/v0.25.0...v0.26.0
[0.25.0]: https://github.com/willemkokke/footman/compare/v0.24.0...v0.25.0
[0.24.0]: https://github.com/willemkokke/footman/compare/v0.23.0...v0.24.0
[0.23.0]: https://github.com/willemkokke/footman/compare/v0.22.0...v0.23.0
[0.22.0]: https://github.com/willemkokke/footman/compare/v0.21.0...v0.22.0
[0.21.0]: https://github.com/willemkokke/footman/compare/v0.20.0...v0.21.0
[0.20.0]: https://github.com/willemkokke/footman/compare/v0.19.0...v0.20.0
[0.19.0]: https://github.com/willemkokke/footman/compare/v0.18.0...v0.19.0
[0.18.0]: https://github.com/willemkokke/footman/compare/v0.17.0...v0.18.0
[0.17.0]: https://github.com/willemkokke/footman/compare/v0.16.0...v0.17.0
[0.16.0]: https://github.com/willemkokke/footman/compare/v0.15.0...v0.16.0
[0.15.0]: https://github.com/willemkokke/footman/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/willemkokke/footman/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/willemkokke/footman/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/willemkokke/footman/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/willemkokke/footman/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/willemkokke/footman/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/willemkokke/footman/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/willemkokke/footman/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/willemkokke/footman/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/willemkokke/footman/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/willemkokke/footman/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/willemkokke/footman/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/willemkokke/footman/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/willemkokke/footman/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/willemkokke/footman/releases/tag/v0.1.0
