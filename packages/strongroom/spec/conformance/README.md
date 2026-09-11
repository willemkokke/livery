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
| `version` | `tree`, `parents` (names), `as` | build a version over the named tree with the named parents, land it, bind its digest |
| `set` | `namespace`, `path`, `digest` (a name), `previous` (a name or null), `expect` | move the ref by compare-and-swap |
| `ref` | `namespace`, `path`, `expect` | read the ref |
| `lock` | `namespace`, `path`, `holder` | write the ref's lock file as if held: `live` by a running process now, `dead` by a process that has exited, `expired` by a running process longer ago than the stale bound |
| `unlock` | `namespace`, `path` | remove the lock file |
| `tamper` | `namespace`, `path`, `digest` (a name) | rewrite the ref file out of band, leaving its record |

`expect` for `set` is one of `ok`, `conflict`, `write-once-refused`,
`not-fast-forward`, `lock-timeout`, `tampered`, `unknown-namespace`.
`expect` for `ref` is a bound name, `null`, or `tampered`.

## Files

| File | Pins |
| --- | --- |
| [refs.json](refs.json) | compare-and-swap, the three mutation classes, the lock-break rule, out-of-band edits, undeclared namespaces |

## The harness

An implementation's test suite interprets these files against its
store. The Python package's harness is in its tests; the lock steps
use the implementation's own lock file and staleness rules, so a
harness in another language writes the lock the way its
implementation does.
