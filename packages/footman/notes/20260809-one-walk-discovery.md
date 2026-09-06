# One walk for discovery: ceiling, config and task files in a single ascent

*Status: DEFERRED, not built. Raised while landing the branded-tasks-file fix
(#364). Recorded because the reasoning about **why the config chicken-and-egg
is harmless** is the expensive part, and it will otherwise be re-derived every
time someone reads `project_markers()`.*

## The idea

> Can we update `PROJECT_MARKERS` as we go up the tree and find config files,
> and then find the actual task files on the way back down?

One ascent that reads config as it climbs — so a `tasks` key encountered on the
way up updates the filename being looked for — then one descent that collects
task files under the resolved name. Today discovery is three passes:

1. `find_repo_root` **ascends** for a VCS marker, falling back to
   `find_project_root` for a project marker;
2. `load_config` walks `dir_chain(cwd, ceiling)` **down**, merging config;
3. `task_files` walks the same chain **down** again, collecting files.

## It is a performance idea, not a correctness one

The apparent bug it fixes: `project_markers()` uses the **brand's** tasks
filename and deliberately ignores the `tasks` config key that can override it
per project, because finding config needs the ceiling that marker set helps
compute. That reads like a gap.

It is not one, for one reason:

> **To use the `tasks` key you need a config file, and a config file is already
> a project marker.**

So any directory that renames its tasks file is already recognised as a root by
the very file doing the renaming. The chicken-and-egg is self-resolving.

The remaining shape — a root's config renaming the file for a *subdirectory*
that has no config of its own — does not need the subdirectory to be a root at
all. `find_project_root` only decides the **ceiling**, and the config-bearing
ancestor already supplies one; the descent then finds the file under the
config-derived name. Nothing is lost.

What the single walk would actually buy is **one filesystem pass instead of
three**. That is real but modest, and it is not on the TAB hot path:
`_complete` reads the cached manifest and never walks. It lands on `_suggest`
and the execution path.

## The trap, if it is ever built

**A single walk that stops at the first marker of either kind silently shortens
every monorepo cascade.** Today `find_repo_root` scans *all* ancestors for a VCS
marker and only falls back to project markers when none exists anywhere. A
naive merged walk climbing from `packages/api/src` stops at
`packages/api/pyproject.toml` and never reaches the repo root — so the cascade
starts one directory too low and the root's tasks vanish, with no error.

The shape that preserves today's semantics:

- ascend once, remembering the **first project marker as a candidate**;
- keep ascending for a VCS marker regardless;
- the ceiling is the VCS marker if one appears, else the candidate.

Two further constraints the idea does not remove:

- **`cascade_mode()` must still be read before the walk starts.** It decides how
  far the walk may reach (`none` / `repo` / `filesystem`), which is why it is
  user-level-only. Config is therefore read in two phases regardless.
- **Config precedence is nearest-wins**, so an ascent meets the winning value
  first. That happens to be convenient — the first `tasks` key seen going up is
  the final answer — but it inverts `load_config`'s current root-first
  `merged.update` order, which would need rewriting rather than reusing.

## Recommendation

Do not build it to fix the marker set — there is nothing to fix. Build it only
if a discovery-cost measurement justifies collapsing three passes into two, and
if so, land it as a pure refactor with the monorepo case tested first.
