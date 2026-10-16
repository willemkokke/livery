# livery-extensions-docs

The documentation site of a workshop workspace. A workspace turns it on
by listing the extension:

```toml
[workspace]
extensions = ["docs"]
```

It owns the site's assembly, the `docs` verbs, the `docs.config`,
`docs.members` and `docs.theme` queries, the `docs` and `deploy`
phases, the staged extension css, the generators a package declares and
the seam the site publishes through, and it arrives through its own
task entry point. An extension that adds to the site, such as
`mkdocstrings`, requires docs, so a workspace that lists that extension
takes docs in through it, and the canonical list leaves docs out.

## The contract tables

A package's `[docs]` table is the extension's whole, `api` among its
keys, so it refuses, naming docs, while the workspace does not list
the extension. The base reads no `[docs]` table: the extension places
each package in the site's Packages tree at the shallowest import path
the package's code is referenced by, and the python extension copies a
package's `docs/` into the wheel it builds. The base imports no
extension, which `layering.base-imports-no-extension` enforces.

The root contract's `[docs]` table is the extension's, `site-url`
among its keys. A `pre_tasks` hook of the extension links each task
name in `--help`, `--list` and `--json` rows to the task's page on the
site, through the redirect page `tasks/<name>/` under `site-url`,
where `<name>` is the task's address with its dots as dashes. A
workspace whose pages live elsewhere sets a URL template in
`[workspace] docs-url` instead, which the base's own hook hands to
footman: `{path}` is the task's address joined by slashes, `{slug}`
joined by dashes. That key comes before the site's, and footman's own
configured `docs-url` before both.

## The queries

The extension answers `python.docs-group` with zensical, the site's
builder, so the `docs` dependency group installs it. It declares three
more queries:

- **The site's config, `docs.config`.** An extension that adds tables
  to it answers with the function that writes them for the packages a
  config covers, every package for the site and one for a package's
  preview. The docs extension joins each answer's tables after its own,
  in composition order. A table two answers write refuses naming both,
  and the joined config must parse.
- **The site's private-members policy, `docs.members`.** `public` keeps
  the API reference's default filter and `all` documents every member.
  The nearest answer wins, and any other value refuses naming both.
- **The theme block, `docs.theme`.** A theme extension answers with a
  table of the block's values (`language`, `font.text`, `font.code`,
  `features`, `palette`) in part. Each key comes over the base's block
  from the nearest answer setting it, the one no other answer setting
  it requires. Two answers setting one key while neither requires the
  other refuse, naming both, and so does an unknown key.

## The phases

The extension declares two phases of its own, both at the workspace
level, outside the gate and with no verb. Their steps run side by side,
and a failing one is named and stops the verb.

Every docs verb runs the `docs` phase before it assembles the site's
config. Its `generators` step runs the verbs each package names under
`[docs] generators`, each once, at the root. Its `task-reference` step
writes the task reference of every package that advertises
`footman.tasks` entry points; it is declared off until the root
contract turns it on:

```toml
[phases.docs.task-reference]
enabled = true
```

Before the phase runs, the verb removes every generated tree, each
package's `docs/_generated/` and the root's, which the phase's steps
and the extension's own passes write again. So a step that no longer
runs, its extension unlisted or the step turned off, leaves no page
and no nav block behind.

A step that writes elsewhere names those paths under its `outputs`
key, each relative to the root or `package/<path>` for the path in
each package. A generator names its own in its `[docs] generators`
entry, `{ verb = "...", outputs = ["docs/tools/"] }`. The task
reference declares the root's `docs/tasks/`, where each task's
redirect page lives, and coverage's step each package's `htmlcov/`.
The phase's run removes each such path that no running step or listed
generator declares any more, the extension behind it uninstalled
included, and keeps the rest as their writers left them. An output
that leaves the root, or names `docs/` itself, refuses when the
extension mounts.

`fm docs.publish` runs the `deploy` phase on the built site just before
it publishes it, then puts each declared coverage report's tree into
the site. A listed extension that requires docs adds its steps to
either phase at its top level, and any other in its `[for.docs]`
table, while docs is listed.

## The extensions' css

An extension's site css ships in its wheel under
`content/docs/assets/`. The build stages it under `docs/_extensions/`
and lists it in `extra_css` in extension order, before the packages'
declared sheets and the workspace's own `docs/assets/site.css`, which
loads last. A step an extension withdraws takes its lines with it.

## The site's CI

The site's CI comes with the extension: its `extension.toml` declares
the gate point's `docs` job, which the verdict waits for, as
`[ci.jobs.gate.docs]` with the task it runs. A workspace that does not
list the extension renders no such job. The job names the function
that lists the system packages it installs and declares the files the
site reads, so the CI render and the shell's skip rule work without
reading the `[docs]` table. The job reads the workspace's `docs/`,
`zensical.toml` and `README.md` and each package's pages, nav, assets,
examples and generated pages, so a pull request that changes only
`notes/` skips the site build.

`[ci.jobs.merge.docs]` keeps the built site as the run's artifact on a
push, and `[ci.jobs.merge.gate]` collects it into the gate job, adds
the publish to the gate job's entries after its verdict, and names the
function that answers the publish seam. So the site is built once per
run, its deploy phase draws this run's coverage trees into it from the
union the gate judged, and it is published from the gate job: through
the container and ssh seams directly, and through the forge's pages by
uploading it for the forge's own pages deploy, a job with no checkout
and no entry.

## The development pages

The extension renders the site's development section from the prose
fragments the mounted extensions ship for a human reader, one page per
section under `development/`.
