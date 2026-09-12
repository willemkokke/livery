# Ref namespaces

Refs are the only mutable thing. `refs/<namespace>/<path...>` names a
digest, every update writes a record beside it, and updates are
compare-and-swap on the previous digest. The store owns two
namespaces and interprets no other.

## Mutation classes

A namespace declares a mutation class where it is registered, and the
store enforces the class without learning what the namespace means:

| Class | Rule | A conflict is |
| --- | --- | --- |
| write-once | create if absent; `previous` is `null` and stays `null`; a second write naming a different digest is refused | a rug-pull or a bug, and is loud |
| monotone | fast-forward: the new version's parents must include the current target | a loser that rebases and retries |
| volatile | last writer wins, still under compare-and-swap | not worth a name |

Read the table downward: mutation frequency and conflict cost are
inversely correlated, which is why loud losers are affordable
everywhere.

## Owned by the store

| Namespace | Points at | Class | Purpose |
| --- | --- | --- | --- |
| `pins/<name>` | anything | volatile | explicit roots for the sweep |
| `pending/<id>` | a tree or a version; for a group of moves, the manifest tree of the moves' targets | volatile | the fail-closed publish: written before the bytes land, a root while it exists, retired deliberately |

### The journal of a group

A group's pending record carries its journal in `meta`:

```json
{
  "lease": 3600,
  "applied": 0,
  "moves": [
    {
      "namespace": "tools",
      "path": "bun@1.3",
      "digest": "sha256:...",
      "previous": null,
      "receipt": null,
      "meta": {}
    }
  ]
}
```

`moves` is the list in apply order; `previous` and `receipt` are a
digest string or null; `meta` is the object the move writes into the
ref's record. `applied` counts the moves a commit has already
applied, zero until a commit stopped part-way. A pending record
without `moves` is a single publish's. The manifest tree the pending
ref names has one entry per move, named by the move's index as four
decimal digits (`0000`, `0001`), of kind `tree` when the target
decodes as a tree and `blob` otherwise.

## Conventions the store publishes

So that two consumers who share a cache converge on the same names
without the store knowing either of them. The store enforces the
class and reads nothing else.

| Namespace | Points at | Class | Owner |
| --- | --- | --- | --- |
| `urls/<sha256 of the URL>` | a blob, with validators in the record's `meta` | volatile | a URL-keyed download cache; an origin hint's landing place |
| `tools/<name>@<version>` | a tree, the extracted archive | write-once | a tool store |
| `datasets/<name>/<branch>` | a version | monotone | a versioning consumer |
| `derivations/<call key>` | a tree or a blob | none declared: a key determines its value, so a second write is idempotent | an evaluator's skip tier |
| `queries/<key>` | a blob, with a freshness window in `meta` | volatile | an evaluator's query cache |
| `contracts/<name>@<version>` | a blob | write-once | contract pinning |

## Authority

Every namespace has exactly one authoritative host, the one whose
calls write it. A local store is the authority for its own
namespaces, so offline work continues. A mirror or a share carries
copies of refs with a freshness window, and reading a ref from a
non-authoritative tier is a query. Multi-master ref writes do not
exist.
