# livery.workshop

The devkit: what every repository in the livery ecosystem runs. A
repository's whole `tasks.py` is `plugin("livery.workshop")`; every
verb below arrives through that line.

## The layer model

The workspace contract (`workshop.toml` at the root) names the layers
in precedence order, and that list is the whole of discovery: a
package installed by accident never changes a repository.
Contract keys are kebab-case, at the root and in every package's
contract: a key spelled with underscores refuses on read, naming its
spelling and the file to rename it in.

- `livery.workshop` is the base layer. Importing its plugin registers
  the task surface and then mounts every further layer the contract
  names, in order.
- A further layer is any package advertising a `footman.tasks` entry
  point; `livery.forge` ships its dev containers this way, and a
  workspace gets them exactly when its list says so.
- The instance always wins last. Its own files (`CLAUDE.project.md`,
  anything below the plugin line in `tasks.py`) are seeded once and
  never rewritten.

Each layer may carry a `content/` directory; `fm sync` delivers it:
guidance fragments into `.workshop/`, skills and hooks into
`.claude/` as links (a local override is kept and named), and the
managed `CLAUDE.md` stub whose imports end at the instance's own
`CLAUDE.project.md`.

## The tools a workspace requires

Three sites declare tool requirements, each a name with a floor,
`ruff` or `ruff>=0.16`: a package kind, in its record, for the tools
its checks run, which is how the python kind requires uv, ruff, pytest
and the checkers; a package instance, in its `workshop.toml` under
`[tools] requires`, for what its kind cannot know; and the project, in
the root contract's `[tools] requires`, for what belongs to the
repository. The root contract's `[tools] index` names where the
catalogue is read from, the published index's URL or a directory
holding the index or the records that build one, and `[tools] hosts`
the hosts the repository locks for, the gated three unless it says
otherwise. The repository that authors the records reads them directly.

`fm tools.lock` resolves every site's requirements against the
catalogue and writes `tools.lock` at the root: one version per tool for
the whole repository, the newest that satisfies every floor and
resolves on every locked host, refusing by name otherwise. `fm
tools.add <requirement>` declares a tool at the project site, locks it
and materialises it; `fm tools.upgrade <tool>` moves one entry to the
newest eligible version, and every package with it, since no package
runs a version of its own.

Entering the environment materialises the bundle the sites require:
`fm sync` supplies every locked tool through the machine's store, the
downloaded kinds from the catalogue's deployment for this host through
`[tools] sources` (folders or URLs in the store's layout, consulted
before the origin) and the delegated kinds through their installer, in
one of three modes per tool: `link` puts its entry points in the
checkout's `.workshop/bin`, `path` its own directories on PATH, `none`
neither, for a tool reached only through a typed handle. A binary
links and a system tool takes `none` unless the record or the root
contract's `[tools] modes` says otherwise; every other kind takes
`path`. A system tool is the machine's own: the store installs
nothing for it and holds the copy on PATH to the highest of the
record's floor and the sites' floors, naming the site whose floor it
is under. Each tool leaves a receipt under `.workshop/receipts/`, this
checkout's statement of what it installed: the exact version, the
host, the deployment's digest and what reached PATH, as the release
train's receipt is a release's statement. `fm env.emit` carries the
receipts' paths and variables, and `fm env.check` names each required
tool's receipt and the drift when the lock's deployment has moved
under it, a tool with no receipt that still resolves from PATH being
named and not a problem. `fm` enters the environment for itself as
well: every invocation applies the same PATH entries and variables to
its own process before any task runs, so the locked tools resolve from
any shell, an agent's, a CI step's or a bare terminal's, and an entered
shell adds only completion. The rendered `.vscode/settings.json` makes
the editor's integrated terminal an entered shell from its first
prompt, one profile per platform.

The stubs the four type checkers read are materialised too. `fm
tools.restub` writes them into `typings/` at the root, pyright's
default stub path and a search path the rendered configuration hands
mypy, ty and pyrefly: one stub per tool the lock holds, at the locked
version, as `livery.toolroom.stubs` modules with `livery.toolroom.handles`
beside them declaring the handles for the installed tools package's
index to import, under the namespace package and never inside the tools
package's own directory; a tool the
workspace does not deploy gets no stub. `fm sync`, the lock verbs
and the entry script write them as well, so a checkout and a CI runner
type against the same stubs. The store renders each stub from the
locked version's own surface, from the records or the index, so no
source holds a stub; a receipt under `.workshop/` names the lock and
the source the last write rendered from, and while both stand nothing
is read or written. `fm env.check` counts them and names their
absence.

## The task surface

- `fm check`: format, lint, four gating type checkers, public-API
  type-completeness, the tests with per-package coverage floors, and
  the render gate, in parallel. On a machine the gate is the reflex:
  it runs what the working tree changed since the nearest tree this
  checkout's own green gates proved (the one with the fewest changed
  paths, a green check of the dirty tree included), the packages that
  delta can influence (their dependents' closure over the `[[depends]]` graph),
  and records the working tree as proved, so the tenth commit of a
  branch pays for what the tenth commit touched and a tree the record
  already proves runs nothing; `--full` runs everything. A delta
  confined to a package's test files runs those files alone, after
  the kind's gate build when its tests run on a build (a C++
  package's ctest); a changed conftest or helper widens to the suite,
  and a source change runs the suite and its dependents'. A machine's
  test run sets the runner's variables, so a test that reads them is
  judged here as on the legs. The chain of
  records rests on a full gate here or on the nearest tree in HEAD's
  history that CI's record holds: a fresh branch off main starts
  proved, and one cut before main's own run is green pays for that
  merge's changes in its first step. A change outside the packages
  runs everything and roots a new chain, with two exceptions. Prose and the site's
  own files, a file under `notes/`, a markdown file anywhere, the root
  `docs/` tree, or the root `zensical.toml`, affect no package, so a
  diff confined to them runs no gate and the site build judges them.
  The workspace's own `tests/` directory is a unit of its own: a
  change under it formats, lints, type-checks, and runs those tests
  alone, and the record supplies every package's suite. A
  workspace that declares
  `[ci] affected-legs = true` has its CI check legs run that scoped
  gate against the pull request's base branch; the gate job runs
  the render gate and the provenance check on every run, and after
  a green verdict stamps the tree it proved on the `workshop/verified`
  record, so a later run of the same tree, such as main's run after
  a squash of a branch on its tip, skips the gate in seconds. A
  narrowed run stamps too when the tree it narrowed against is on
  the record in full: every package it skipped is byte-identical to
  that base tree's, so their verdicts carry over, and the row names
  the base. Without such a base a narrowed run stamps nothing. The
  release train reads the same record before it waits on main's run.
- The check legs run on every runner the contract names, with the
  newest Python of a derived matrix; the nightly point runs the whole
  matrix, floor included, so the floor's legs cost runner minutes at
  night and no pull request time, and a floor-only failure reaches a
  person the next morning. A declared `[ci] python-versions` runs at
  the gate as declared. On a Windows leg of a GitHub-shaped workspace
  the entry points `TEMP` and `TMP` under the runner's own temp: the
  hosted runners keep the workspace, the uv cache, and that temp on
  the fast working drive and the system temp on the slow system
  drive, so the tests' files and the environments they build share
  the drive with the cache and uv links instead of copying.
  `[ci] windows-temp = "system"` leaves the system temp, for a runner
  without a separate working drive; `"runner"` asks for the move on
  any forge.
- On a GitHub-shaped workspace every job restores two caches before
  it enters, both under the runner's temp, the working drive, where
  the checkout and the venv are and where the store's links into the
  checkout stay links: the tool store under the data directory the
  entry places there, keyed by `tools.lock` with the OS and
  architecture, and uv's cache, keyed by `uv.lock` per leg. Each falls back to the
  nearest archive under its prefix, so a moved lock restores what is
  unchanged and saves a fresh archive at the end; a restored store is
  a tier the store verifies on access; the entry exports both
  placements before its sync and materialise, and `fm ci.run` prunes
  uv's cache before the save. The other lanes cache nothing until they have a
  cache action.
  Tests are namespaced by their path (pytest's importlib mode, set by
  the project template), so two packages may share a test file's
  name; a helper module in a package's `tests/` carries the package's
  name, and the session refuses to start otherwise.
- `fm start`: open the work. An issue number assigns and branches
  `<kind>/<number>-<slug>`, a quoted title files the issue first, and
  `<kind>/<slug>` starts a branch that belongs to no issue, whose
  submit closes nothing. Every form branches from a fetched
  `origin/main` into a worktree under the runner's home, provisioned
  and entered when a person is at the terminal: where the shell hook
  is installed the worktree is entered in that shell, with its
  environment loaded and nothing to leave, and without the hook a
  child shell opens there instead (`--open=code` opens the editor,
  `--open=none` prints the path); `--no-worktree` reuses this
  checkout.
- `fm commit <type> "<subject>"`: a conventional commit on a proved
  tree. It stages the change (`--only` narrows), derives the scope
  from the packages the change touches, runs the affected gate first
  in its fix mode (`--no-check` skips), validates the subject the way
  `fm submit` validates a title, and refuses on `main` and on a
  reserved branch. `git commit` keeps working.
- `fm integrate`: bring `origin/main` into the branch by merge. A
  merge rewrites nothing, so every other copy of the branch stays
  valid, and the squash erases it at landing; a conflict stops with
  git's own words. When the merge moved `uv.lock`, the root
  manifest, or a member's, the verb matches the environment to it and
  names what moved, so the next gate runs on the merged environment.
  Every command makes that check before it runs: the venv follows the
  lock and the manifests, and one that drifted is synced, then the
  command runs again on the installed code.
- `fm submit`: get the branch onto the remote, verified. Its local
  gate is the reflex; when the chain of this machine's green gates
  proves HEAD's tree, back to a full gate here or a tree in HEAD's
  history that CI's record holds, it skips the gate and names the chain, from a record in
  the checkout's git directory that never leaves the machine. A row
  proves its tree for a week: the tree id covers the pins, not the
  machine's tools. `--armed`
  lets it land, `--fix` heals mechanical gate findings into the
  branch, and the follow classifies the verdict with stable exit
  codes; a follow that sees the merge from a linked worktree removes
  the tree and its branch, naming the directory to go to, and keeps
  a tree holding something the merge did not take. `--no-close`
  submits without the close footer, for preparatory work the merge
  must leave open; a pull request already green when `--armed` asks
  is merged directly, since a forge that arms only a blocked pull
  request refuses. `fm submit.merge` lands a green,
  deliberately-unarmed PR; `fm abandon [branch]` gives a feature up,
  this one or a named one, its worktree with it. `fm status`,
  `fm ci.*`, and `fm doctor` stand beside them, all on
  [livery-forge](https://pypi.org/project/livery-forge/).
- `fm template.check` keeps rendered files byte-identical to the
  template source the contract names (`[workspace] templates`: a
  local directory, or a fork URL at its own risk), and refuses a
  committed `tasks` nav block that lags the package's advertised
  task tree, naming `fm docs.task-reference` as the remedy;
  `fm new.package` renders a member and wires it in.
- `fm release.prepare` and `fm release.verify` run the path-tag
  train (`packages/<pkg>/v<semver>`); a workshop release also
  publishes the template snapshot, tagged in lockstep.
- `fm janitor` sweeps the runner's directories and the state store:
  footman's cache, then what the workshop leaves in the data
  directory (the worktrees of closed issues and merged branches,
  which `fm start` sweeps first, the checkout's local branches
  whose pull request merged, and files and folders no code writes any
  more), then every series the workshop keeps, in the
  scope the run has: the checkout's local series on a machine, the
  remote series too inside CI, where the merge point's gate job runs
  it after every green run. Windows are enforced, aged rows and
  orphaned refs go, anything holding work stays and is named, the
  config directory is reported and never touched, and `--dry-run`
  says what would go. The same work runs wherever it is called from:
  what is safe to remove is decided by the rule that proves it, never
  by who is watching.
- `fm update` brings an instance up to date: floors to the latest
  released tags, content, render, then the submit flow. Nothing
  changed means nothing happens.

## Where a test runs

A test declares the CI points it runs at. Without a marker it runs
at the default points, the gate (a pull request's legs) and the
merge (main's run). `@pytest.mark.only_at("nightly")` runs it at the
named points and nowhere else; `@pytest.mark.also_at("release")`
adds points to the default ones. The job runner names the point to
every child it spawns, and the workshop's pytest plugin deselects
the rest; a local `fm test` selects for the gate, and
`fm test -- --workshop-point nightly` selects for a point on demand.
A `[[ci.schedule]]` entry in `workshop.toml` attaches a task to a
point's job; `every = "1w"` or `"2w"` runs it on Mondays, or on the
Monday of an even ISO week, and any other run of the point skips it
naming the day it runs next. On Windows, a test whose child exits with
`STATUS_CONTROL_C_EXIT`, the status a console control event leaves,
gets a section in its failure report: the command, the process and
its worker, the run, every control event the process itself saw, and
every process attached to the console, so one sighting carries what
the next reader needs.
The nightly point runs the whole check with its own tests selected
in, and never skips on the verified record or narrows.
`fm ci.dispatch --point=nightly` starts the nightly now, on `main`
unless `--ref` names another, and follows the run to its verdict;
`fm ci.status --point=nightly` and `fm ci.logs --point=nightly` read
the newest nightly run by workflow rather than by commit, so a
failure only the nightly meets reaches a person through `fm`.
A package contributes a point of its own with `[[ci.point]]` in its
`workshop.toml`: `name` (a workflow file name and a job name at once),
`task`, and optionally `args`, `every`, `runners` (the root contract's
when absent) and `pythons` (the newest gate Python when absent). The
point runs on the clock and by hand, one job on those runners and
Pythons calling the task through `fm ci.run`, with the job token and
nothing more: a permission, a secret or an environment in the table
refuses, as does a builtin name, a name two packages claim, a cadence
that is not one, or a task no layer mounts. Removing the package
removes its workflow: `fm template.check` reports the file as retired
and `fm template.apply` deletes it.
On GitLab the clock is a pipeline schedule, a project setting rather
than a line in the pipeline document: `fm workflow.configure` creates
one per point that runs on the clock, named `workshop: <point>`, and
deletes the ones it named for points no longer declared. GitHub and
Gitea keep the clock in the workflow file and reconcile nothing.
`fm ci.dispatch --point=gate` starts the gate the same way. A
dispatched gate is the full gate: the check verb reads the event and
narrows on a pull request alone, and it sets the verified record
aside as the nightly does, so a dispatch proves the tree it checks
out whatever the record already says. The shell spells one call on
every event; nothing passes `--full`.

## Coverage floors

Each package's `workshop.toml` may declare `[qa] coverage-floor`: a
percentage of statements and branches the gate enforces, or the mode
`"auto-ratchet"`, under
which the floor is the package's mark on the state store. Both modes
pass at the floor minus the package's `coverage-epsilon` (percentage
points, 0.5 when absent). Under auto-ratchet the first gated run
records the mark, a run that clears it by more than epsilon raises
it, and a store that cannot be read falls open with its reason and
writes nothing. Lowering a mark is a person's act:
`fm coverage.accept <package> <value> --reason=<why>` writes a row
naming who and why, and refuses without a reason, at or above the
current mark, or for a package with a committed floor. The number
that is judged is the CI union: on every leg the tests run measured,
each process a test starts included and the gate's own driver never,
so a line counts only when a test reached it, and the gate job
combines all platforms before enforcing, so the floors are
deterministic per change and never depend on one machine's view. Coverage stays global
under the
affected mode: in a check leg's one measured run every test records
under a context named by its node id (the workshop's own pytest
plugin, quiet outside a measured run), the leg splits the run's data
per suite and puts each suite's lines on its per-run ref of the
state store, with the identity of the suite's dependency closure
(the tree ids of the package and of every package it depends on,
plus the root's `pyproject.toml` and `uv.lock`). Main has a
coverage record, and so does every branch with a pull request run:
per check leg, one row per suite, the arcs the last run judged and
the closure each was measured at. A leg skips a suite only when its
branch's record or main's holds it at the suite's current closure;
otherwise the suite runs, and the leg says why. The workspace's own
`tests/` directory is a unit too, keyed by every package's tree, and
every leg that runs a suite runs it. The gate job unions the legs'
lines with every skipped suite carried from the records before it
judges, so the union is the same global union a full run produces,
and writes that union back onto the branch's record; a suite no
record can supply is red by name, never a smaller union. A branch's
record outlives the branch by a day, because main's run for the
squash that merged it carries the suites its legs skipped from that
record, minutes after the merge deleted the branch. At the
merge main's run finds its tree on the verified record, which names
the branch, skips the gate, and copies the branch's record into
main's without measuring; a squash of a stale branch has another
tree and pays the full gate. A suite neither record holds is
measured on the spot. The janitor drops a branch's record once the
branch is gone from origin. A local `fm test` prints its own
lower-biased preview beside the floor, for information. Raise a
committed floor as the suite grows; lower it only deliberately, in a
reviewed change or an accepted row. The release legs publish a
further, informational union that includes the live-only code.

## Test speed

The speed marks are off until the contract declares
`[ci] speed-marks = true`: a hosted runner's test time varies by tens
of seconds between two runs of one tree, so a mark taken from such
runs says nothing about the suite. Declare the key on runners that
keep a steady clock. While the key is absent the gate job drops any
marks left from before, so a later opt-in starts from the recorded
timings alone. On, the gate job judges each check leg's summed
test time per package against a mark on the state store. The mark is the median of the
leg's last five green runs for the package, recorded once five are
seen; it moves down when that median beats it by more than five per
cent, so a suite cannot regress slowly. A run over the mark by more
than fifteen per cent and twenty seconds warns in the gate job's
output, naming the leg's slowest tests and the ones that grew most
against their own medians; the second run over in a row on the
ubuntu leg is red, and the other legs warn only. A new heavy test is
a cost someone chose: `fm speed.accept <package> <seconds>
--reason=<why>` raises the mark, on the ubuntu leg unless `--leg`
names another. The marks are judged on the CI legs alone: a
machine's clock is not a leg they were taken on, so `fm test` on a
machine prints each package's summed time and reads no mark, and
`fm speed.judge` is unavailable outside CI. `fm ci.timings` shows
the marks beside the timings.

## The state store

Every row the workshop keeps across runs is a row of the state
store: JSON files on git refs, `refs/workshop/*` on the remote and
`refs/workshop-local/*` in the checkout's git directory. A default
clone or fetch never downloads the remote namespace, no refspec
names the local one, and every row carries the schema the store
stamped and the time it wrote it. A machine's gate never reaches
origin: `fm sync` and `fm start` mirror the remote namespace into
`refs/workshop-origin/*` and stamp the fetch, and `fm check` reads
the store from that mirror alone, so a hung SSH agent cannot hold
the gate. The mirror is what the last sync saw, by design; a
checkout the store was never fetched into reads it as unreachable,
roots its chain on a full gate, and names the sync. The remote series are written by
CI only, `fm coverage.accept` and `fm speed.accept` the exceptions;
a local run reads them and writes only the local ones, which the
checkout's worktrees share and a fresh clone starts without. A read
or a write of a series is a fixed handful of git processes whatever
the series holds: one `cat-file --batch` reads every row, one
`hash-object --stdin-paths` writes every blob, so a series of three
hundred rows costs no more processes than one of three (git 2.38 or
newer). A verb that reads several series takes one snapshot of the
remote namespace: one listing of the store's refs and the branches
together, then one fetch of the refs the checkout lacks, and every
read inside answers from the local object store, so `fm store.ls`
and `fm ci.timings` cost two round trips. The job runner takes one
snapshot for the whole job and publishes it to the entries it runs,
so the gate job lists once for its union, collect, judge, stamp, and
janitor. A write keeps its compare-and-swap on the listed sha, its
push's own report is its verdict, and it records the sha it pushed
for the reads and the entries after it. A check leg writes its
per-run ref once, its timing row beside its measured suites.

| series | a row is | window | writer |
| --- | --- | --- | --- |
| `metrics` | one run: every job's times, the run's wall, the union's percentages | 300 | the gate job |
| `run/<id>/<leg>` | a check leg's half of its timing row, and the suites it measured with the scope it ran, until the gate job collects it | none | the leg |
| `verified` | a tree a green gate proved, with its scope and the base it composed on | 200 | the gate job |
| `coverage/<base>/<leg>` | a record on one leg, main's or a branch's: one row per suite, the arcs its last run judged at the closure each was measured at, replaced in place; a branch's goes with the branch | none | main's gate job at a merge; a branch's own pull request runs |
| `coverage/marks` | a package's coverage mark and who set it | 400 | the gate job, `fm coverage.accept` |
| `speed/marks` | a package's test time mark on one check leg and who set it | 400 | the gate job, `fm speed.accept` |
| `gate-record` (local) | a tree this checkout's `fm check` proved green | 200, 7 days | a green local gate |
| `fetched` (local) | when origin's namespace was last mirrored into `refs/workshop-origin/*`, and how many refs | 1 | `fm sync`, `fm start` |
| `diagnostics` (local) | the follow classifier's inputs for an unmerged ending | 20 | every unmerged follow |

`fm store.ls` lists them with what each holds; `fm store.show
<series>` prints a series' rows newest first through the same reader
the verdicts use, `--key=<leg>,<package>` for one series of a family
and `--json` for the rows as they are. `fm ci.timings` renders the
`metrics` series: per job and metric the latest, p50, and p90 over
the window, then the movers. `fm janitor` bounds every series in the
scope it runs in, windows, ages, and orphans, the remote ones from
the merge point's gate job after every green run. The deploy renders
the site's coverage pages from main's coverage record, the union
main's gate judged whatever its legs ran.

## Where a test lives

Every pytest under a workshop venv runs apart from the machine's live
runner state. The workshop's isolation plugin points footman's data,
cache, and config directories at a fresh temporary home for the
session, before the first test and for every child process a test
starts, so a test neither reads the developer's config, tokens,
worktrees, and caches nor writes into them; a variable the outer
environment already set is kept. The one deliberate exception is a
live test reading the dev containers' credentials through
`livery.forge.testing.shared_env_path`, which skips without the file.
The plugin also guards the process-global task registry: a test that
leaves tasks there fails at its teardown naming them, and the next
test never inherits them.

The forge lane belongs to `livery.forge`; the workshop orchestrates
local, git, and forge steps and never hands a raw forge verb to a
user.
