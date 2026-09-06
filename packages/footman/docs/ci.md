# CI & automation

A task runner spends most of its life in CI, so footman keeps the automation
surface small and stable: exit codes that mean something, one JSON envelope
for machines, and flags that make chains behave under supervision.

## The one-liner

The command you run locally is the command CI runs, which is the point of a
task runner:

```yaml
# .github/workflows/ci.yml
- run: uv run fm check
```

`check` composed with `pre=[format, lint, typecheck, test]` runs its
prerequisites **in parallel** on the runner too, and the first failure sets
the job's exit code. Output never interleaves: each task's output lands as
one block, so CI logs stay readable.

Two flags earn their keep in CI:

- `-k / --keep-going` runs every independent branch even after a failure,
  so one red step doesn't hide three others. The exit code is still the
  first failure's.
- `-s / --sequential` does one thing at a time, for constrained runners or when
  you're bisecting an ordering suspicion. The request reaches inside task
  bodies too: `parallel()` calls run one at a time under it, so `-s` means
  no concurrency anywhere. (A project can make this the default with
  `sequential = true` in `[tool.footman]`.)

One rule governs the streams: **stdout is the answer, stderr is the
commentary.** Task output, and footman's own answers (listings, help,
`--json` envelopes), land on stdout; the per-task `ok`/`FAIL` summary, the
live progress line, warnings, and errors are stderr. So `fm build > out.log`
captures exactly what the tasks produced, and a wrapper that treats stderr
bytes as failure (cron's mail rule, say) should pass `-q` to silence the
summary.

Without a TTY there is no progress bar; timing still records, and a
confident estimate prints one `eta` line to stderr at run start; the
details live on [Progress & timing](progress.md#in-ci).

## `--json`: the machine surface

```console
$ fm --json check
{
  "schema": 1,
  "total_ms": 812.4,
  "items": [
    {"task": "lint", "ok": true, "code": 0, "duration_ms": 812.4,
     "output": "...", "error": null},
    {"command": "ruff check .", "code": 0, "duration_ms": 790.1}
  ]
}
```

Everything a task (or anything it spawned) wrote is captured into the
payload, so **stdout stays pure JSON**: pipe it straight into `jq` or hand
it to an agent. Tasks can return their own structured data (`returned`),
refusals emit an error envelope instead of bare stderr text, and
`--list`/`--dry-run`/`--version` all have machine forms. The full contract,
every envelope field by field with recipes, lives on one page:
**[JSON output](json.md)**.

The CI shape-check:

```sh
fm --json check | jq -e '.error == null and ([.items[] | select(.task)] | all(.ok))'
```

## Exit codes

`0` all green · `1` a task raised · `N` a task (or its `run()` command)
exited N · `64` footman refused (a taught message says why) · `130`
interrupted · `143` a supervisor asked it to stop (a cancelled job, a
`timeout`, `docker stop`). The full table is part of the machine contract:
[JSON output § exit codes](json.md#exit-codes).

Exit 64 before anything runs is a *feature* in CI: a typo'd workflow fails in
milliseconds with a taught message, not after twenty minutes of setup.

## Agents

Everything above is what coding agents want too: one command, structured
results, captured output, real exit codes. A paste-ready instructions
snippet and edit/stop hook recipes live on [AI agents](agents.md). Three
commands to know:

- `fm --json --list` (or bare `fm --json`) prints the whole task tree as an
  envelope: every task and group with its parameters, types, choices, and
  defaults. One call, full catalog.
- `fm --json --dry-run <chain>` rehearses the chain and answers in the
  ordinary items envelope. A typo still refuses with exit 64 before
  anything runs, and what would run arrives as faked receipts. Bodies
  execute (that is what makes the answer real), so rehearse chains whose
  side effects live behind `run()` and steps.
- `fm --help <task>` renders a task's full typed surface from the manifest,
  read-only, wherever `--help` appears on the line.

## Conditional tasks in CI

`@requires_env` re-checks live on every run, so gating a task on the
environment works naturally:

```python
from footman import requires_env, task


@task
@requires_env("CI")
def publish_coverage(): ...
```

Locally it's listed as `(unavailable: set CI)` and refuses to run; on the
runner it just works. Remember the contract: a `pre`/`post` dependency on an
unavailable task is a **hard failure**, because CI must never silently skip a step
you declared.
