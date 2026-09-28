# CI profiles

A run is several machines doing several things at once, and its log is a wall
of text per job. A profile is that same run as one timeline: every job at the
times the forge recorded, the steps inside each one, and under each check leg
the tasks, commands and tests that leg actually ran. Open the file at
[ui.perfetto.dev](https://ui.perfetto.dev); `chrome://tracing` and speedscope
read it too.

Nothing is kept unless the workspace asks for it, and nothing is read unless
someone asks a question.

## Getting one

```console
$ fm ci.profile
  check (ubuntu-latest, 3.14): 15701 event(s) of its own
  profile: .fm/profiles/run-36287488870.json
```

That writes the newest run for the commit you are on. `--run=<id>` names a run
instead, and `--into=<dir>` overrides where the file lands.

A local command can carry the run it caused:

```console
$ fm --profile=submit.json submit
  ...
  profile: run 36292178910 joins this trace, 8 job(s)
  profile: submit.json
```

That file holds the submit's own work, the run its push started, every job of
that run, and every leg's tasks and tests underneath.

## A chain, not one run

A release is more than one run. The branch's pull request run proves the
branch, the merge puts a commit on the base and the base runs its own checks,
and the merge point dispatches the wave that publishes. `--from` writes all of
it as one file:

```console
$ fm ci.profile --from=HEAD
  run 36331515850: check (ubuntu-latest, 3.14): 15701 event(s) of its own, setup 41.0s, teardown 3.0s
  4f2a91c8e1b0: run 36331515850 (pull_request)
  4f2a91c8e1b0 merged as 9ab3c7d15e22 by #817
  9ab3c7d15e22: run 36332008144 (push), run 36332114907 (workflow_dispatch, recorded)
  profile: .fm/profiles/chain-4f2a91c8e1b0.json
```

Three recorded facts make the walk, and nothing else does:

- what the forge lists for a commit, which is every run it filed under it:
  push, pull request and dispatched alike;
- the commit a pull request's merge produced, which every forge publishes;
- the commit a dispatched run ran on, which the run writes down itself.

The third covers one case. A run dispatched on a branch is filed under that
branch's tip, and a merge landing between the dispatch and the run moves that
tip past the commit the dispatch named. The run knows what it checked out, so
it records that, and a walk from the commit finds the run through the commit
rather than through a time. `(recorded)` on a line means the run was found that
way.

Every edge is something a forge or a run wrote down, so a chain followed while
it happens and one walked from the same commit weeks later are the same tree.
`fm workflow.release --armed` makes this walk at the end of its own work, so
the file it leaves holds the release command, its pull request's run, the
base's run and the wave.

## What the file holds

- One track per job, named as the forge names it. Jobs that ran at the same
  time read as the parallel work they were.
- The wait before a job, as its own span, where the run's acceptance time is
  known.
- The job's steps, inside the job.
- Each leg's own timeline, in process groups of its own: its tasks, every
  command inside them, and every test's setup, call and teardown.
- The runner's own work, on a track under the job: the part of the job's span
  the leg's timeline does not cover, as `setup` before the first entry and
  `teardown` after the last.

A job the forge gave no length, a skip, is an instant carrying its conclusion
rather than a span of no width. A job still running when the file was written
is a slice up to that moment, and says so in its arguments.

That last track is why a job adds up. The span comes from the forge, the
entries' timelines come from the leg, and the difference is the checkout, the
caches, the tool store and the post-job save. GitHub and Gitea itemise that
work as steps too, and GitLab reports no steps at all, so there the difference
is all a reader gets. The arithmetic is the same on all three.

Where the two clocks disagree about a job, the line says so and nothing is
drawn: a span of negative length would read as work that happened.

Two timelines from two machines line up because each one records the
wall-clock moment its own zero sits at. Nothing is placed by the order it
arrived in.

## A leg with nothing under it

A job drawn from the forge's times alone, with no timeline beneath it, means
its trace is not in the channel. The verb's own lines say which of these it
was:

- the run is older than the window, so its traces have been dropped;
- the workspace asked for no traces;
- the leg ended before it could push, which its conclusion explains.

What a short window loses is the detail. The shape of the run comes from the
forge and is always there.

## Where the traces live

A leg pushes its trace to refs under `refs/workshop-trace/`, one ref per leg
of a run, and a dispatched run records the commit it ran on beside them, one
ref per commit. The one refspec a sync mirrors is the state store's own namespace,
so no sync brings a trace, no gate reads one, and a checkout pays nothing for
them until someone assembles a run. The janitor keeps the newest runs and
drops the rest.

A push is something noticed, never a verdict: origin refusing one is a printed
line and the job's own result is untouched.

## What it costs

Measured on a channel of 21 runs, 81 legs between them:

| Kept | What the legs wrote | On origin, packed |
| --- | --- | --- |
| 5 runs | 14.0 MiB | 1.28 MiB |
| 10 runs | 28 MiB | 1.29 MiB |
| 21 runs | 74.7 MiB | 6.81 MiB |

Near-identical traces delta against each other, which is why they are kept as
git objects rather than compressed one by one. Doubling from five runs to ten
cost 10 KiB, because those runs ran the same tasks and tests and differ only in
their timings. A span where a trace's own shape changes costs a few hundred KiB
a run instead, so the window's real bound is how long it keeps rather than how
much it holds.

No clone pays any of it: the channel is pushed and never mirrored.

## The contract

```toml
[ci]
# Whether CI keeps a trace of what it did at all.
profile = true
# How many runs of traces are kept.
profile-window = 100
# Where an assembled file lands.
profile-into = ".fm/profiles"
```

A key of the wrong type is named on the leg that read it, and its default
stands: whether a timeline is kept is not worth failing a run over.

`profile = false` means zero cost rather than less: no job runs anything
profiled, nothing is written on a runner, and nothing is pushed. A run from
a period when it was off still assembles at the job level, because that
shape comes from the forge rather than from us. So an installation large
enough to care can leave it off and turn it on while investigating.
