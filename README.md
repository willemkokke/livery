# livery

The livery ecosystem monorepo. The working record lives in
[notes/](notes/).

| Package | Distribution | What it is |
| --- | --- | --- |
| `packages/forge` | `livery-forge` | One interface to GitHub, Gitea, and GitLab |
| `packages/workshop` | `livery-workshop` | The devkit: the task surface, layers, content, templates, and the forge lane |

The workshop's template snapshots publish to
[workshop-templates](https://github.com/willemkokke/workshop-templates),
tagged in lockstep with `livery-workshop` releases; edits happen here,
behind the render gate.

The gate: `fm check`. CI runs the same command on three OSes.
A fresh machine enters with `source setup.sh`, which installs the
pinned uv, syncs the venv against the lock, and leaves bare `fm`
working.
