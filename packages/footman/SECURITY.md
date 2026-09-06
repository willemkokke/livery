# Security

## Reporting

Report a vulnerability privately through GitHub:
[Security → Report a vulnerability](https://github.com/willemkokke/footman/security/advisories/new).
Please don't open a public issue for one.

You'll get an acknowledgement within a few days. footman is maintained by
one person, so I can't promise an SLA — I can promise you won't be ignored.

## Supported versions

Pre-1.0, only the latest release gets fixes. There are no maintained
backport branches.

## Threat model — what counts

footman's job is to run the code in your `tasks.py`, and to run the programs
those tasks call. So these are working as intended and are **not**
vulnerabilities:

- A task file that runs arbitrary code. Running it is the feature. Reading
  a `tasks.py` you don't trust is the same act as running a `Makefile` or a
  `setup.py` you don't trust.
- Task discovery finding a `tasks.py` in a directory you moved into. The
  [monorepo cascade](https://willemkokke.github.io/footman/monorepos/) walks
  from the repo root down to where you stand — that is documented behaviour,
  and it is why footman won't cascade past a project boundary.
- A task passing your arguments to a program that then does something
  destructive.
- Completion importing your task files to build its cache. The first Tab in
  a fresh directory, and the background rebuild once a cached manifest goes
  stale, spawn a subprocess that imports the same `tasks.py` a run would.
  That is the documented design, not a hole: importing a repo's task files
  is the same risk posture as opening any other code file in that repo, and
  a [dynamic completer](https://willemkokke.github.io/footman/completion/#dynamic-completions-are-recomputed-fresh)
  already runs your function on the execution path. The process that answers
  the keystroke stays stdlib-only and imports nothing; what it spawns runs
  code you already trusted enough to check out.

These **do** count, and I want to hear about them:

- Anything that makes footman run code from outside the task files it is
  meant to load — a completion path that runs something those files never
  declared, a cached manifest that becomes a code path, a config key that
  reaches an exec.
- Files written outside the documented cache, data and config directories,
  or written with permissions wider than they should be.
- A secret that leaks where it shouldn't: into the manifest, the timing
  history, a profile trace, `--json` output, or a progress line. The
  `Secret` marker exists to prevent exactly this — a case where it doesn't
  is a bug worth reporting privately.
- Anything in the completion hot path that can be induced to read or write
  outside the cache directory.
