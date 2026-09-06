# The stability promise

footman is pre-1.0. This page exists so that "pre-1.0" means something
specific instead of "anything may happen."

## The promised surface

Four things are what a project actually builds on, so these four are what
the promise covers:

1. **The public API** — every name exported from `footman` itself: the
   decorators, [`group()`](reference.md), [`run()`](reference.md), the
   parameter markers, [`Context`](reference.md), the testing
   [`Runner`](testing.md). If you can `from footman import` it without a
   leading underscore, it is on the surface.
2. **The CLI grammar** — how a command line is read: chains without
   separators, [dotted addressing](orchestration.md), where `--` hands off,
   how a global option binds, and what each
   [exit code](troubleshooting.md) means.
3. **The [`--json` envelope](json.md)** — the machine-readable output for
   every surface: results, errors, the task catalog, dry-run plans.
4. **The [`[tool.footman]` configuration keys](configuration.md).**

## What is not promised

Anything with a leading underscore is internal and moves without notice —
that is most of the package by line count, and deliberately so.

The manifest file's on-disk layout, the timing history, and the cache
directories are private between one footman and itself. Read them at your
own risk; they are an implementation detail of making <kbd>Tab</kbd> fast,
not a format.

Error *messages* are written to teach, and get reworded whenever a better
wording is found. Match on exit codes, never on prose.

## The rule today, before 1.0

- A breaking change to the promised surface may land in a **minor**
  release. Never in a patch.
- Every one is called out in the [changelog](changelog.md), with what broke
  and what to do about it.
- `--json` and the configuration keys grow by addition: fields get added,
  existing fields do not change meaning under you.

So: **pin the minor.**

```toml
footman~=0.52.0
```

## The rule at 1.0

The same surface, one stricter rule: breaking changes only in a **major**
release. `--json` and the configuration keys stay additive-only across the
whole 1.x line.

## How footman gets to 1.0

Two conditions, and only one of them is in my hands:

1. A bake cycle with no breaking changes to the promised surface.
2. Use by people who did not design it.

The first I can schedule. The second is why this page defines the promise
rather than making it. footman is already the core orchestrator where I
work, migrated off duty, so the surface is under daily load on real builds
— that is what makes writing this down a description rather than an aspiration. But
everyone running it so far arrived through me, and a design's worst
assumptions are the ones its author cannot see. A stability guarantee is a
claim about strangers' code; it should wait until some strangers have
written any.

I would like that to be soon. The promised surface has not broken in
several releases. But the version number moves when the second condition is
met, not when I would like it to be.

If you are building on footman and something here is not covered that you
need covered, [say so](https://github.com/willemkokke/footman/issues) —
that is exactly the feedback the second condition is asking for.
