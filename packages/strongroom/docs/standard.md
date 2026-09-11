# The standard

The store is a standard implementable in any language, and this
package is its reference implementation, not its definition. The
line: the standard covers what has to be byte-identical, the
conformance suite covers what has to be behaviour-identical, and
everything else is an implementation's business.

## Where it lives

The normative text is the `spec/` directory beside the package in
the repository, one page per format, each with golden vectors under
`spec/vectors/` and behaviour scenarios under `spec/conformance/`:

| Page | Specifies |
| --- | --- |
| `digest.md` | the digest string grammar and the algorithm registry: `sha256`, required and sole |
| `canonical.md` | RFC 8785 canonical JSON, the one hashed form of every structured format, integers only |
| `tree.md` | the tree: a directory as a manifest, with the portable-name rules and the symlink entry |
| `version.md` | the version: provenance over a tree, with a subject-shaped producer |
| `records.md` | the ref record and the tombstone |
| `layout.md` | the on-disk layout, the root manifest, landing, refs, and the HTTP mapping |
| `namespaces.md` | the two namespaces the store owns, the conventions it publishes, the mutation classes |
| `sources.md` | sources and tiers: the read path, verification, offline, fill |
| `lifecycle.md` | roots, marking by shape, the sweep, the pending publish, erasure |
| `materialiser.md` | the ladder and its rules, symlink entries, the path budget, the removal doctrine |

## Vectors

A vector file has cases and refusals. A case pins the canonical bytes
of an input, typed by hand, and the digest of those bytes. A refusal
pins the rule that rejects an input, by a phrase the implementation's
error must contain. Everything persisted is one of these formats or a
raw blob, and a change to a format after its vector lands is a
migration, priced as one.

## Running the conformance suite

The scenarios are data: the namespaces a store is opened with and the
steps run against it. This package's harness runs them against any
implementation that speaks the store's API:

```python
from pathlib import Path

from livery.strongroom import PythonHooks, Store, load_scenarios, run_scenario

conformance = Path("packages/strongroom/spec/conformance")
for scenario in load_scenarios(conformance):
    run_scenario(
        scenario,
        lambda namespaces: Store.create(Path("/tmp/store"), namespaces=namespaces),
        PythonHooks(),
        Path("/tmp/views"),
    )
```

`run_scenario` raises `ConformanceFailure` naming the scenario, the
step and what was found. An implementation in Python provides a
factory that opens its own store with the scenario's namespaces, and
a `Hooks` object that reaches its three seams: a lock written as if
held by a live, a dead, or an expired holder; a platform that refuses
symlinks; a publish that begins between a sweep's marking and its
deleting. An implementation in another language interprets the same
files with a harness of its own, writing the lock the way its
implementation does.

## Out of the standard

The local index an implementation keeps, the strategy it picks within
the materialiser's ladder, a service's framework, encryption
mechanics, and anything a consumer adds above the formats.
