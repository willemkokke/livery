# Development patterns

How to drive several changes at once with the workflow verbs, and
what each pattern costs. Every verb below is described on its own
task page; this page is the order to use them in.

## Submit and move on

An armed submit needs nobody after the push. The forge merges the
pull request on its own green, closes the issue, deletes the branch,
and the next `fm` verb anywhere sweeps the worktree. Waiting for that
is time the next change could use.

```console
fm submit --armed --no-follow
```

The verb gates, pushes, opens or reuses the pull request, arms it, and
returns. Start the next change at once. Later, from any worktree,
`fm ci.status` says where that head's runs stand, and `fm ci.logs`
prints the failed jobs' logs when one is red. Only a red needs a
hand: `fm ci.rerun` for a runner that failed on its own, a fix
commit and `fm submit --armed` again for a real failure.

`fm submit --armed` without `--no-follow` watches until the merge or
the first blocker, and self-heals the two that need no decision
(behind the base, or conflicting with it) by integrating the base and
re-submitting. Use it when the merge is the next thing you need.

## Several changes at once

Each change lives in its own worktree, started from a fetched
`origin/main`, with its own environment:

```console
fm start 123
fm start "the title of a new issue"
fm start docs/a-note
```

Worktrees are independent: a gate in one does not see another's
edits, and each submit is judged alone. `fm start 123 --agent=claude`
hands a worktree to a coding agent with the issue as its briefing.
Re-running `fm start` on started work re-enters it.

## Stacking dependent changes

A change that needs another one's code starts on that branch instead
of main, while the parent is still in review:

```console
fm start 124 --from=feat/123-the-parent
```

The worktree is added at the parent's pushed tip, and the parent is
recorded against the child's branch. `fm submit` from the child
targets the parent, so the child's pull request shows only its own
diff and gets its own CI while the parent's is still running. When
the parent merges, the forge retargets the child's pull request to
main, and the next `fm submit` from the child says it now targets
main. One `fm integrate` then merges main into the child, which takes
the parent's squash in and leaves the child's own diff; the resubmit
pays one more CI run for the new head. A `--base` flag on `fm submit`
always wins over the record.

The parent must be pushed before the child starts on it: an
unpushed parent is refused by name.

## What the forge does with several green pull requests

Each armed pull request merges on its own green, onto whatever main
is at that moment. Main moving under a pull request does not cancel
its arm or its green: the branch protection does not require a
branch to be up to date, so the landing order is the order in which
the runs finish, and a landed pull request re-runs nothing.

Two pull requests that touch different lines both land without a
second run. The combination was proved by neither run; the next push
of anything proves it. Two that touch the same lines cannot both
apply: the forge stops the second, the watch reports it as
conflicting, and `fm integrate` followed by `fm submit --armed`
resolves it at the cost of one local gate on the changed paths and
one CI run.

## Where the clock goes

The local gate runs what the working tree changed since the nearest
tree its record proves, so a test-only edit gates in well under a
minute and a prose-only edit in seconds. CI runs the whole gate on
every host; `fm ci.timings` prints each host's medians per task from
the recorded runs, and the slowest host sets the pull request's time.

A flaky test costs a whole re-run of its host every time it trips.
Fixing the flake pays more than retrying it: file the issue the same
day, with the log line, and fix it on its own branch while the
re-run proves the change at hand.
