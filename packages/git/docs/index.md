# livery-git

Git access for the livery ecosystem. Every call is a function of the
repository's root: `git.position(root)` says where a checkout stands,
`git.fetch(root)` and `git.push_force(root, branch)` reach the network,
`git.commit_all(root, message)` commits. The functions run the git the
tool store pins, never one found on the path, and return strings or
raise `GitError` with git's own words.

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

## What is not here

Porcelain that waits on a person, a signer, is the caller's to arrange:
the functions pass git's own words back and never answer a prompt.
