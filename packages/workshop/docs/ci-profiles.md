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

## What the file holds

- One track per job, named as the forge names it. Jobs that ran at the same
  time read as the parallel work they were.
- The wait before a job, as its own span, where the run's acceptance time is
  known.
- The job's steps, inside the job.
- Each leg's own timeline, in process groups of its own: its tasks, every
  command inside them, and every test's setup, call and teardown.

A job the forge gave no length, a skip, is an instant carrying its conclusion
rather than a span of no width. A job still running when the file was written
is a slice up to that moment, and says so in its arguments.

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
of a run. The one refspec a sync mirrors is the state store's own namespace,
so no sync brings a trace, no gate reads one, and a checkout pays nothing for
them until someone assembles a run. The janitor keeps the newest runs and
drops the rest.

A push is something noticed, never a verdict: origin refusing one is a printed
line and the job's own result is untouched.

## What it costs

Measured across one run's three check legs:

| Where | Size |
| --- | --- |
| The three files a run's check legs wrote | 9.19 MB |
| The same three on origin, packed | 837 KiB |

Near-identical traces delta against each other, which is why they are kept as
git objects rather than compressed one by one.

## The contract

```toml
[ci]
# Whether CI keeps a trace of what it did at all.
profile = true
# How many runs of traces are kept.
profile-window = 20
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
