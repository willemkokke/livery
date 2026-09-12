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
| `tree` | `entries` (name to bound blob name), optional `subtrees` (name to bound tree name), optional `links` (name to target), `as` | build a tree, land it, bind its digest |
| `land` | `data`, `expect` | land the bytes without binding; `expect` is `ok` or `erased` |
| `state` | `digest` (a name), `expect` | the object's state: `present`, `absent` or `erased` |
| `path` | `digest` (a name), `expect` | ask for the object's path; `expect` is `ok` or `erased` |
| `erase` | `digest` (a name), `reason` | erase the object, leaving its tombstone |
| `drop` | `namespace`, `path`, `previous` (a name), `expect` | drop a ref; `expect` is `ok`, `conflict` or `protected` |
| `begin` | `target` (a name, or `MISSING`), `as`, `expect` | begin a publish and bind the pending id to `as`; `expect` is `ok` or `missing` |
| `commit` | `pending`, `namespace`, `path`, `previous`, `expect` | commit the publish; `expect` as for `set`, or `no-such-pending` |
| `retire` | `pending`, `expect` | retire the publish; `expect` is `ok` or `no-such-pending` |
| `pending` | `expect` (bound pending names) | the pending publishes, exactly these, groups included |
| `group-begin` | `as`, optional `manifest_as` | begin a group and bind its id to `as`, and its manifest's digest to `manifest_as` |
| `group-add` | `group`, `namespace`, `path`, `digest` (a name), `previous`, `expect`, optional `manifest_as` | add a move; `expect` is `ok`, `missing`, `conflict`, `not-a-group`, `no-such-pending` or `half-applied`; `manifest_as` binds the new manifest's digest |
| `group-commit` | `group`, `expect`, optional `crash_after` | commit the group; `expect` as for `set`, or `not-a-group`, `no-such-pending`, or `crashed` with `crash_after`, the number of applies after which the commit stops as a crash would |
| `group-retire` | `group`, `expect` | retire the group; `expect` is `ok`, `no-such-pending` or `half-applied` |
| `groups` | `expect` (bound group names) | the groups begun and not committed, exactly these |
| `pin`, `unpin` | `name`, `digest` (pin only), `expect` (unpin only) | root a digest under `pins/`, or drop the pin; unpin's `expect` is `ok` or `conflict` |
| `sweep` | `expect_removed` (names), optional `begin_during` (a name) | sweep; with `begin_during`, a publish of that target begins between marking and deleting |
| `view` | `tree` (a name), `at` (a directory name), `as`, optional `expect` | fill the directory from the tree and bind the record to `as`; `expect` is a refusal such as `erased` |
| `entry` | `view`, `path`, `expect_rung` (a list) | the recorded rung of one entry is one of the listed |
| `collect` | `at`, `declared` (paths), `expect` (a name) | collect the declared paths and compare the tree's digest |
| `drop-view` | `view`, `expect_left` (lines) | drop the view; what it left, exactly these lines |
| `stray` | `at`, `path` | write a file inside the view the view did not create |
| `exists` | `at`, `path`, `expect` (a boolean) | whether the path exists, a symlink included |
| `refuse-symlinks` | | from here on the platform refuses to create symlinks |

`expect` for `set` is one of `ok`, `conflict`, `write-once-refused`,
`not-fast-forward`, `lock-timeout`, `tampered`, `unknown-namespace`.
`expect` for `ref` is a bound name, `null`, or `tampered`.

## Files

| File | Pins |
| --- | --- |
| [refs.json](refs.json) | compare-and-swap, the three mutation classes, the lock-break rule, out-of-band edits, undeclared namespaces |
| [lifecycle.json](lifecycle.json) | the publish sequence, a refused commit, the sweep's re-scan of pending refs, the three object states, dropping refs, pins |
| [groups.json](groups.json) | a group of moves: a refused second move moves nothing, replay after a crash, retire refused part-way, the sweep through a group, a single publish is not a group |
| [views.json](views.json) | a view round-trips through collect, a live view is a root, drop leaves what it did not create, an escaping symlink is parked, an erased entry fails the view |

## The harness

An implementation's test suite interprets these files against its
store. The Python package's harness is in its tests; the lock steps
use the implementation's own lock file and staleness rules, so a
harness in another language writes the lock the way its
implementation does.
