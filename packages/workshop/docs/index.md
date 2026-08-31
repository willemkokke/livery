# livery.workshop

The devkit: what every repository in the livery ecosystem runs. A
repository's whole `tasks.py` is `plugin("livery.workshop")`; every
verb below arrives through that line.

## The layer model

The workspace contract (`livery.toml` at the root) names the layers
in precedence order, and that list is the whole of discovery: a
package installed by accident never changes a repository.

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
  packages runs everything.
- `fm submit`: get the branch onto the remote, verified; `--armed`
  lets it land, and the follow classifies the verdict with stable
  exit codes. `fm status`, `fm ci.*`, `fm doctor`, and the
  `workflow.*` exits stand beside it, all on
  [livery-forge](https://pypi.org/project/livery-forge/).
- `fm template.check` keeps rendered files byte-identical to the
  template source the contract names (`[workspace] templates`: a
  local directory, or a fork URL at its own risk); `fm new.package`
  renders a member and wires it in.
- `fm release.prepare` and `fm release.verify` run the path-tag
  train (`packages/<pkg>/v<semver>`); a workshop release also
  publishes the template snapshot, tagged in lockstep.
- `fm update` brings an instance up to date: floors to the latest
  released tags, content, render, then the submit flow. Nothing
  changed means nothing happens.

## Coverage floors

Each package's `livery.toml` may declare `[qa] coverage_floor`, the
high-water mark the gate enforces. The number that is judged is the
CI union: every leg runs measured (each `fm` child included) and the
aggregating job combines all platforms before enforcing, so the
floors are deterministic per change and never depend on one
machine's view. A local `fm test` prints its own lower-biased
preview beside the floor, for information. Raise a floor as the
suite grows; lower it only deliberately, in a reviewed change. The
release legs publish a further, informational union that includes
the live-only code.

The forge lane belongs to `livery.forge`; the workshop orchestrates
local, git, and forge steps and never hands a raw forge verb to a
user.
