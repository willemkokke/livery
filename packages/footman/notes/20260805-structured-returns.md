# Structured returns — the return annotation becomes the output contract

Status: **BUILT, 2026-08-05** — the full-fat v1 landed the same day the
design closed: `returned_spec` (annotation → native spec, baked in the
manifest), `returned_schema`/`returned_mismatch` in the envelope, the
producer-side check in every mode, `--describe[=TASK]` rendering JSON
Schema, `Returns:` docstring prose (`returned_doc`), the `--help`
returns line, and the markdown exporter's field table. The CHANGELOG's
`[Unreleased]` entries carry what shipped; this note keeps the
reasoning.
Design closed 2026-08-05 — **all seven calls ruled the same
day the note opened**, full-fat throughout; the last (`pre=`
aggregation: per-entry) landed 2026-08-05. No opens.
Supersedes the 2026-07-22 working draft (which predated `Stdout[T]`, the
`items[]` envelope, and the per-parameter annotation fallback);
prioritised ahead of the tools-dist split (2026-08-05, Willem: "more
utility than a separate tools library, though I still want that too").

## The ask, and who is asking

footman turns a typed *input* signature into a structured surface —
flags, completion, the manifest. The mirror move: turn the **return
annotation** into a structured *output* surface, so a consumer can rely
on the shape without running the task and can learn it without reading
the body.

The first named consumer is hse (affected-graph feedback, item 3,
2026-08-05): their CI scope steps consume `items[].returned`
programmatically, a key rename today is a silent break, and they pin key
names with a hand-written test. Their ask, verbatim in spirit: a task
*declares* its return shape; consumers rely on the keys; `--help`/docs
show what a task returns.

## What already works (re-verified 2026-08-05)

- A task's return value lands in the `--json` envelope at
  `items[].returned` — every node in the run, `pre=` prerequisites
  included, one flat address-carrying list (the 07-22 draft's
  `results[]` spelling is stale; the envelope reshaped since).
- Serialisation is `_describe.json_default` (context: "a task may return
  what it accepts"): `Path`→str, `Enum`→value, `datetime/date/time`→ISO,
  `UUID`→str, `Decimal`→str, dataclasses→`asdict`, `set`/`frozenset`→
  sorted list. Anything else refuses loudly-but-locally
  (`returned_error` + stderr warning, exit code untouched).
- Bare `int` is the exit-code channel, never data; `bool` is data.
- `Stdout[T]` (0.21) makes the return value the stdout *document*, and
  under an explicit `--json` the document rides `items[].returned` — one
  value, two doors, same serialisation.
- In tests the same value is `Runner.invoke(...).results[n].returned`.
- hse runs all of this in production today (`hse graph.affected` returns
  its dataclass dict; 0.30.0 integrated).

So the data path needs nothing. The work is **discovery** — and, new
since 07-22, **drift protection**.

## The model

> schema = static (annotation → manifest); data = dynamic (`--json` run).

```python
@dataclass
class Affected:
    tasks: list[str]
    reason: str
    since: str


@task
def affected() -> Affected: ...
```

The annotation *is* the declaration — no decorator, no schema language,
the same philosophy that makes `fix: bool = False` the whole input
contract. From it:

1. **`returned_schema` in the manifest**, baked beside the param specs in
   `_task_node`. Static, so completion/`--dry-run` semantics are
   untouched; the hot path parses the same one file (this repo's baked
   manifest is ~8 KB today — a schema block per returning task adds
   single-digit KB; confirm at build time, but the TAB budget does not
   move for that).
2. **`returned_schema` per entry in the `--json` envelope** — data and
   how to read it, one call, additive under `schema: 1`.
3. **A static describe surface** — the shape without a run (spelling
   open, below).
4. **Help/docs**: `--help` gains a returns line for declaring tasks;
   the task-docs renderer shows the fields.

### The describable set

Exactly `json_default`'s set, recursively: dataclasses (nested),
`list[T]`/`tuple[T, ...]`/`dict[str, T]`, the scalar bridge types
(`Path`, `Enum` — with its values as an enum schema, `datetime`, `UUID`,
`Decimal`), `str`/`int`/`float`/`bool`/`None`, `Literal[...]` as an
enum, `T | None` as nullable (mirroring param-union handling). A return
annotation *outside* the set is not an error — the task still runs, the
value still serialises or refuses at runtime exactly as today — it just
gets no schema. "Describable" ⊆ "returnable", never a new gate on
registration.

A **broken** return annotation now degrades gracefully for free: the
per-parameter fallback (0.30.0) resolves annotations individually and
warns once naming the task and the error — a schema is simply absent,
siblings unaffected.

### Drift protection — the new half

hse's actual pain is the silent rename, and the 07-22 draft's answer
("the return type is the contract, checked by the type-checker") does
not reach across a repo boundary — hse's CI is not type-checking
footman-side task bodies.

Two mechanisms, not exclusive:

- **Consumer-side snapshot.** The describe surface gives a stable JSON
  shape; a consumer checks it into their repo and diffs it in CI
  (`fm --json <describe spelling> > expected.json`). A producer rename
  becomes a visible diff at integration time, replacing hse's
  hand-written key test with one they never write again. Zero runtime
  cost, zero new semantics — this falls out of the describe surface
  existing at all.
- **Producer-side check.** footman validates the returned value against
  the declared schema at the boundary and, on mismatch, attaches a
  loud-but-local note (`returned_mismatch`, same family as
  `returned_error`) — the rename goes red in the *producer's* own gate,
  before any consumer integrates. The 07-22 draft ruled runtime
  validation out; hse's cross-repo pain is the new fact that reopened
  it. Cost: a recursive walk of the returned value per declaring task,
  paid only by tasks that declare — measured at ~34 µs for a 158-node
  report. Never an exit-code change — a payload problem stays a note,
  per the existing rule.

**Decided (Willem, 2026-08-05): both, in v1** — the snapshot falls out
of the describe surface, and the producer-side check ships with it. The
07-22 no-runtime-validation ruling is formally overturned.

## Where it plugs in

- `_manifest._task_node` — introspect the return annotation into a
  `returned` spec beside the param specs (bumps `tree_hash` once; caches
  rebuild, expected).
- `_describe` — type→schema generation, reusing the param-spec
  introspection; `json_default` already fixes the target set. Reuse
  `_coerce.emitted` (the `Stdout[T]` detector) so `Stdout[Report]`
  describes `Report` — the document and the returned value are one
  declaration.
- `_app` — envelope: `returned_schema` per entry read from the manifest;
  the describe spelling; the `--help` returns line.
- `_complete` — untouched: reads the same baked file, ignores the new
  key.
- Zero runtime deps throughout: stdlib `dataclasses`/`typing`
  introspection, never pydantic.

## Decided (Willem, 2026-08-05)

- **Performance (was call 1's worry): measured, not a bottleneck, and
  cached by construction.** Generation runs at *manifest build* — the
  artifact that already rebuilds only when the tree hash moves, which is
  the "most schemas rarely change" instinct, mechanized. Micro-bench on
  a nested two-level report dataclass: **~25 µs to generate a schema**,
  **~400 bytes compact** per declaring task (fifty declaring tasks ≈
  +20 KB on an ~8 KB manifest — sub-millisecond extra TAB parse), and
  **~34 µs** for the producer-side validation walk of a 158-node value,
  per run, only for declaring tasks. Binding follow-up: re-measure on
  the real tree when the generator exists; if a tree ever carries
  hundreds of declaring tasks, schemas can move to a sibling baked file
  the completion hot path never reads — an escape hatch, not the plan.
- **Dialect (1): decided — native baked, JSON Schema rendered at the
  describe door** (Willem, 2026-08-05: "seems to have no downsides").
  The two small costs, named so they stay paid: the renderer is a second
  representation and gets **golden-pair tests**, under the discipline
  that the native shape never expresses anything JSON Schema cannot say;
  and the **rendered output is contract, not presentation** — hse-style
  snapshots pin the describe output, so renderer changes are deliberate
  and changelogged, envelope-grade, never cosmetic. Sub-decision that
  falls out: the envelope's per-entry `returned_schema` carries the
  *native* form (already baked, compact, one vocabulary with the param
  specs); the describe door is the interop surface and the thing a
  snapshot pins. The manifest itself stays an internal shape, as ever.
- **Describe scope (2): as leaned.** Whole-tree contract dump allowed —
  bare `--describe` hands an agent the entire input+output API; it is
  also the file hse's snapshot pins.
- **Drift protection (3): full fat.** Snapshot *and* producer-side
  `returned_mismatch` in v1 (see above).
- **Describable set (4): full fat.** Dataclasses (nested), `TypedDict`,
  `NamedTuple`, the scalar bridge types, containers, `Literal`,
  `T | None` — the whole `json_default` mirror in v1. Bare
  `dict[str, Any]` describes as `object` with no field claims.
- **`returned_doc` (5): full fat.** The docstring `Returns:` prose ships
  beside the schema.
- **Adapters (7): deferral confirmed.** First-party adapters and the
  `report()`-verb idea live in the tools-dist thread
  (notes/20260801-tools-namespace-package.md); v1's story is tasks
  returning their own types.

## Decided last — `pre=` aggregation: per-entry (Willem, 2026-08-05)

**The question, as re-explained for the ruling.** Take `check` with
`pre=[format, lint, test]`, where `test` returns a `PytestReport`.
Today — and under everything decided above — the envelope carries **one
entry per node**: `items[]` has a `test` row with its `returned` (and,
now, its `returned_schema`), a `lint` row, and a `check` row whose own
`returned` is whatever `check`'s body returns (usually nothing — `check`
is typically an empty gate). A consumer who wants the test report keys
`items[]` by task/address and reads it off the `test` row directly.

The question is whether `check` should additionally *compose* its
prerequisites' reports into its own row — something like
`check.returned = {"test": <PytestReport>, "lint": <RuffReport>}` — so a
consumer reads one row instead of three. That would need one of:

- an affordance for a gate's body to receive its prerequisites' returns
  (a "collect my pre-results" parameter — new machinery, new signature
  vocabulary), or
- automatic aggregation for empty-bodied gates (footman inventing a
  return value the body never wrote — receipts would claim data the
  task didn't produce).

**Ruled: per-entry.** No aggregation affordance, no automatic
composition — a gate's row carries only what its body returned, the
consumer keys `items[]` by task/address, and the cost is one `jq` hop.
The two rejected mechanisms above stay rejected for the reasons given:
a "collect my pre-results" parameter is new signature vocabulary
nothing else needs, and automatic aggregation forges a return the body
never wrote. This closed the note's last open.

## Not in scope

- Runtime *coercion* of returns (they are already Python values).
- A new result channel — `items[].returned` is the channel.
- Graph effects — a schema is static node metadata.
- pydantic or any schema framework.
- Cross-run schema versioning/registry — the consumer snapshot *is* the
  version pin, pre-1.0.
