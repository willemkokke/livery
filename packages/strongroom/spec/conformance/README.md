# Conformance cases

Behaviour that every implementation must share, as data a harness
runs. A vector under `../vectors/` pins bytes; a scenario here pins
what a store does.

## Shape

Each file holds `scenarios`. A scenario names the namespaces the store
is opened with, each with its mutation class, and a list of steps run
in order against a fresh store. A step is an operation with its
arguments and, where the step observes something, an `expect`.

| Operation | Arguments | Effect |
| --- | --- | --- |
| `put` | `data` (a string, UTF-8), `as` | land the bytes and bind their digest to the name `as` |
| `version` | `tree`, `parents` (names), optional `message`, `as` | build a version over the named tree with the named parents, land it, bind its digest; two versions with the same fields are one object, so a second root needs its own message |
| `set` | `namespace`, `path`, `digest` (a name), `previous` (a name or null), `expect` | move the ref by compare-and-swap |
| `ref` | `namespace`, `path`, `expect` | read the ref |
| `lock` | `namespace`, `path`, `holder` | write the ref's lock file as if held: `live` by a running process now, `dead` by a process that has exited, `expired` by a running process longer ago than the stale bound |
| `unlock` | `namespace`, `path` | remove the lock file |
| `tamper` | `namespace`, `path`, `digest` (a name) | rewrite the ref file out of band, leaving its record |
| `tree` | `entries` (name to bound blob name), `as` | build a tree of blob entries, land it, bind its digest |
| `land` | `data`, `expect` | land the bytes without binding; `expect` is `ok` or `erased` |
| `state` | `digest` (a name), `expect` | the object's state: `present`, `absent` or `erased` |
| `path` | `digest` (a name), `expect` | ask for the object's path; `expect` is `ok` or `erased` |
| `erase` | `digest` (a name), `reason` | erase the object, leaving its tombstone |
| `drop` | `namespace`, `path`, `previous` (a name), `expect` | drop a ref; `expect` is `ok`, `conflict` or `protected` |
| `begin` | `target` (a name, or `MISSING`), `as`, `expect` | begin a publish and bind the pending id to `as`; `expect` is `ok` or `missing` |
| `commit` | `pending`, `namespace`, `path`, `previous`, `expect` | commit the publish; `expect` as for `set`, or `no-such-pending` |
| `retire` | `pending`, `expect` | retire the publish; `expect` is `ok` or `no-such-pending` |
| `pending` | `expect` (bound pending names) | the pending publishes, exactly these |
| `pin`, `unpin` | `name`, `digest` (pin only), `expect` (unpin only) | root a digest under `pins/`, or drop the pin; unpin's `expect` is `ok` or `conflict` |
| `sweep` | `expect_removed` (names), optional `begin_during` (a name) | sweep; with `begin_during`, a publish of that target begins between marking and deleting |

`expect` for `set` is one of `ok`, `conflict`, `write-once-refused`,
`not-fast-forward`, `lock-timeout`, `tampered`, `unknown-namespace`.
`expect` for `ref` is a bound name, `null`, or `tampered`.

## Files

| File | Pins |
| --- | --- |
| [refs.json](refs.json) | compare-and-swap, the three mutation classes, the lock-break rule, out-of-band edits, undeclared namespaces |
| [lifecycle.json](lifecycle.json) | the publish sequence, a refused commit, the sweep's re-scan of pending refs, the three object states, dropping refs, pins |

## The harness

An implementation's test suite interprets these files against its
store. The Python package's harness is in its tests; the lock steps
use the implementation's own lock file and staleness rules, so a
harness in another language writes the lock the way its
implementation does.
