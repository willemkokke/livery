# livery-git

Git access for the livery ecosystem. Every call is a function of the
repository's root: `git.position(root)` says where a checkout stands,
`git.fetch(root)` and `git.push_force(root, branch)` reach the network,
`git.commit_all(root, message)` commits. A reader of refs, objects,
history or config opens the repository's own files in-process, through
dulwich, and starts no git; a mover, and a reader of the index, the
working tree or the remote, runs the git the tool store pins, never one
found on the path. Every function returns strings or raises `GitError`,
with git's own words where git ran.

```python
from pathlib import Path

from livery import git

root = Path(".")
branch, head = git.position(root)
if git.conflicts_with_base(root, "main"):
    raise SystemExit("resolve the conflict first")
```

Nothing is held between calls. A function answers what git holds at the
moment it is called, so a flow that needs one fact twice reads it twice,
and a function that moves the repository says so in its name: `fetch`,
`push`, `commit_all`, `switch`, `integrate`.

`git.run(root, *args)` and `git.call(root, *args)` are the door for a
subcommand no function names: `run` raises on a non-zero exit, `call`
returns the result whatever the code, for a caller that reads the exit
code as an answer.

## The plugin

`plugin("livery.git")` in a project's `tasks.py` mounts the package as a
footman plugin; a project that depends on `livery-git` directly gets it
through footman's project rung, and every workshop project runs with it.
Its one hook writes into each task's own environment, which the task's
subprocesses see:

- `GIT_SSH_COMMAND`, so every network call shares one ssh connection per
  remote host: a master opens on the first call and lingers ten minutes
  after the last, with its socket under the runner's data directory. A
  `GIT_SSH_COMMAND` or `core.sshCommand` of your own wins, and a file named
  `unsupported` in the socket directory turns it off with the words it
  holds; delete the file to try again. `fm doctor` says which it is.
- The tools' identity, `<prog>` at `<prog>@<prog>.invalid`, as author and
  committer, when git's config names no `user.email`; a person's own
  identity stands.
- `GIT_EDITOR=true`, unless one is set already: no verb reads a message
  from an editor.

A task marks itself for the cases it knows better: `@git.identity(name,
email)` commits as that identity whatever the config says, and
`@git.unsigned` turns commit and tag signing off for the task's git. The
plugin never turns signing off on its own.

Any process of the same user may reuse the open connection for its
window, without the agent's consent: the same trust boundary as
`SSH_AUTH_SOCK`.

## The state store

JSON rows on git refs, in a namespace a default clone or fetch never
downloads: `refs/workshop/*` on the remote and `refs/workshop-local/*`
in the checkout's git directory. A `StateStore` is a checkout and the
policy its builder gave it: whether this process is a CI run, the
identity every commit carries, and the variable a published listing
travels in. A `Series` declares one ref's rows and reads and writes
through the store; a `Keyed` family declares one series per key.

```python
from livery import git

store = git.StateStore(
    root, in_ci=False, identity=("fm", "fm@fm.invalid"), snapshot_variable="MY_SNAPSHOT"
)
rows = git.Series("metrics", window=300)
print(rows.rows(store).rows[:3])
```

A write is a compare-and-swap on the sha the read returned. Only the
wire is a git process: a remote read is one fetch, a remote write one
fetch and one push, a local read or write none, whatever the series
holds, since every tree is read and every commit written in the
checkout's own object store. A read that names the files it wants,
`rows(store, only=...)` or `read(store, ref, only=...)`, reads those
alone and names the rest, so a ref of large rows costs a reader of a few
of them those few; `row` and `file` read the one file they name. A
`remote_snapshot` block makes every read inside one listing and one
fetch. `sweep` is the janitor. Every failure is a printed reason, never
a boolean.

## What is not here

Porcelain that waits on a person, a signer, is the caller's to arrange:
the functions pass git's own words back and never answer a prompt.
