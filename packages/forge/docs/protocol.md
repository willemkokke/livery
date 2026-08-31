# The protocol

Being drafted. It freezes only when all three backends pass one
conformance suite. Every verb is used by a development workflow; a
verb no workflow uses is removed.

Verb groups:

- `repo.*`: create, get, configure (idempotent drift repair), tags
- `pr.*`: open, find by head, merge now, arm, disarm, arm state,
  comment
- `checks.*`: combined status, runs, jobs, logs, rerun,
  `cancel_run(run, *, force=False)`, dispatch
- `release.*`: create, get by tag
- `issue.*`: create (title, body, labels, assignee), get (with body),
  list, search (text), assign, assigned-to-me, comment, labels
- `identity.*`: whoami, server version
- `forge.supports(capability)`: the honesty valve
