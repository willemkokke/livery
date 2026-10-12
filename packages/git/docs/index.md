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

## What is not here

Porcelain that waits on a person, an editor or a signer, is the caller's
to arrange: the functions pass git's own words back and never answer a
prompt.
