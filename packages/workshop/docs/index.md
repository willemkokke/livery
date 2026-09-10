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
spelling, and `fm template.apply` rewrites the keys in place.

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

## The task surface

- `fm check`: format, lint, four gating type checkers, public-API
  type-completeness, the tests with per-package coverage floors, and
  the render gate, in parallel. `--affected` narrows the gate to the
  packages the branch's changes can influence (their dependents'
  closure over the `[[depends]]` graph); a change outside the
  packages runs everything, except prose and the site's own files:
  a file under `notes/`, a markdown file anywhere, the root `docs/`
  tree, or the root `zensical.toml` affects no package, so a diff
  confined to them runs no gate and the site build judges them. A
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
  the gate as declared.
- `fm submit`: get the branch onto the remote, verified. Its local
  gate is the one the CI legs run: the whole workspace, or the
  affected gate against the base branch when the contract declares
  `[ci] affected-legs`, and it says which; when a green `fm check` on
  this machine already proved the same tree at a covering scope
  within the week, it skips that gate and names the check, from a
  record in the checkout's git directory that never leaves the
  machine. `--armed`
  lets it land, `--fix` heals mechanical gate findings into the
  branch, and the follow classifies the verdict with stable exit
  codes. `fm submit.merge` lands a green, deliberately-unarmed PR;
  `fm abandon` gives the feature up. `fm status`, `fm ci.*`, and
  `fm doctor` stand beside them, all on
  [livery-forge](https://pypi.org/project/livery-forge/).
- `fm template.check` keeps rendered files byte-identical to the
  template source the contract names (`[workspace] templates`: a
  local directory, or a fork URL at its own risk); `fm new.package`
  renders a member and wires it in.
- `fm release.prepare` and `fm release.verify` run the path-tag
  train (`packages/<pkg>/v<semver>`); a workshop release also
  publishes the template snapshot, tagged in lockstep.
- `fm janitor` sweeps the runner's directories and the state store:
  footman's cache, then what the workshop leaves in the data
  directory (the worktrees of closed issues and merged branches,
  which `fm issue.start` sweeps first, and files and folders no code
  writes any more), then every series the workshop keeps, in the
  scope the run has: the checkout's local series on a machine, the
  remote series too inside CI, where the merge point's gate job runs
  it after every green run. Windows are enforced, aged rows and
  orphaned refs go, anything holding work stays and is named, the
  config directory is reported and never touched, and `--dry-run`
  says what would go. A run with no terminal on stdin, the daily
  collector child and a CI job, keeps to the offline rules.
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
The nightly point runs the whole check with its own tests selected
in, and never skips on the verified record or narrows.

## Coverage floors

Each package's `workshop.toml` may declare `[qa] coverage-floor`: a
percentage the gate enforces, or the mode `"auto-ratchet"`, under
which the floor is the package's mark on the state store. Both modes
pass at the floor minus the package's `coverage-epsilon` (percentage
points, 0.5 when absent). Under auto-ratchet the first gated run
records the mark, a run that clears it by more than epsilon raises
it, and a store that cannot be read falls open with its reason and
writes nothing. Lowering a mark is a person's act:
`fm coverage.accept <package> <value> --reason=<why>` writes a row
naming who and why, and refuses without a reason, at or above the
current mark, or for a package with a committed floor. The number
that is judged is the CI union: every leg runs measured (each `fm`
child included) and the gate job combines all platforms before
enforcing, so the floors are deterministic per change and never
depend on one machine's view. Coverage stays global under the
affected mode: in a check leg's one measured run every test records
under a context named by its node id (the workshop's own pytest
plugin, quiet outside a measured run), the leg splits the run's data
per suite and stores each suite's lines on the store, keyed by the
leg, the package, and the identity of the package's dependency
closure (the tree ids of the package and of every package it depends
on, plus the root's `pyproject.toml` and `uv.lock`), and a leg skips
a suite only when the store holds its lines for that identity;
otherwise the suite runs, and the leg says why. The workspace's own
`tests/` directory is a unit too, keyed by the whole tree, and every
leg that runs a suite runs it. The gate job pulls every skipped suite
from the store before it judges, so the union is the same global
union a full run produces; a suite the store cannot supply is red by
name, never a smaller union. A local `fm test` prints its own
lower-biased preview beside the floor, for information. Raise a
committed floor as the suite grows; lower it only deliberately, in a
reviewed change or an accepted row. The release legs publish a
further, informational union that includes the live-only code.

## The state store

Every row the workshop keeps across runs is a row of the state
store: JSON files on git refs, `refs/workshop/*` on the remote and
`refs/workshop-local/*` in the checkout's git directory. A default
clone or fetch never downloads the remote namespace, no refspec
names the local one, and every row carries the schema the store
stamped and the time it wrote it. The remote series are written by
CI only, `fm coverage.accept` the one exception; a local run reads
them and writes only the local ones, which the checkout's worktrees
share and a fresh clone starts without.

| series | a row is | window | writer |
| --- | --- | --- | --- |
| `metrics` | one run: every job's times, the run's wall, the union's percentages | 300 | the gate job |
| `run/<id>/<leg>` | a check leg's half of its timing row, until the gate job collects it | none | the leg |
| `verified` | a tree a green gate proved, with its scope and the base it composed on | 200 | the gate job |
| `coverage/<leg>/<package>` | a suite's measured lines for one closure identity | 6 | the leg |
| `coverage/marks` | a package's coverage mark and who set it | 400 | the gate job, `fm coverage.accept` |
| `gate-record` (local) | a tree this checkout's `fm check` proved green | 200, 7 days | a green local gate |
| `diagnostics` (local) | the follow classifier's inputs for an unmerged ending | 20 | every unmerged follow |

`fm store.ls` lists them with what each holds; `fm store.show
<series>` prints a series' rows newest first through the same reader
the verdicts use, `--key=<leg>,<package>` for one series of a family
and `--json` for the rows as they are. `fm ci.timings` renders the
`metrics` series: per job and metric the latest, p50, and p90 over
the window, then the movers. `fm janitor` bounds every series in the
scope it runs in, windows, ages, and orphans, the remote ones from
the merge point's gate job after every green run. The deploy renders
the site's coverage pages from the run's legs' data, and when the
legs skipped on the verified record, from the store's union.

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
