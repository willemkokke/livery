# toolroom store

The pinned tool store over strongroom. A record, one directory per
tool, names the tool, every version tracked, and per version and host
the artifact's URL and its sha256 with the deployment resolved through
the record's layers; the store lands the artifact by that digest,
extracts it, collects it as a tree, and names the tree under
`tools/<name>@<version>` in one strongroom store per machine, shared
by every checkout on it.

The store installs, links, emits and fetches:

```python
from pathlib import Path

from livery.strongroom import FolderSource
from livery.toolroom.store import Home, Record, Store

store = Store(
    Home(Path.home() / ".local/share/toolroom"),
    sources=[FolderSource(Path("/mirrors/tools/store"))],
)
records = [Record.load(path) for path in Path("records").iterdir() if path.is_dir()]
ensured = [store.ensure(record, record.versions[-1]) for record in records]
store.link(ensured, Path(".workshop/bin"))
delta = store.delta(ensured, Path(".workshop/bin"))
```

## The record

`records/<tool>/tool.json` is the tool axis: the name, the description,
the kind, the version floor a `system-check` tool must reach, the hosts
the tool has, the tool's layout and its override for one host each.
`records/<tool>/deltas/<nnnn>-<version>.json` is one version's arrival:
its sequence, the version, its date, per host the artifact's URL and
sha256, and the version's override of the layout, for every host or
for one. Deltas run consecutively from `0001`, and a file's name is
its sequence and version.

A deployment resolves through four layers, most specific winning: the
tool's layout, the tool's override for the host, the version's layout,
the version's override for the host. The layout fields are `root`, the
directory inside the archive hoisted to the install root; `exe`, a
binary's executable name; `paths`, the install-relative directories put
on PATH; `env`, with `$package` standing for the install root; `shims`,
a link name to an executable the install carries; and `exclude`, the
archive members left out before import.

Loading a record resolves every host of every version and refuses one
that does not resolve whole: an artifact without a sha256, a layer
naming a host or a version the record does not carry, a layer restating
the value it inherits, a version whose host resolves with no `paths` or
a binary with no `exe`, and a delta out of sequence. The schema of both
documents is `records/record.schema.json`, exported by
`export_schema`.

## The store

`ensure` lands a version's artifact through the sources in order and
through the origin URL unless the store is offline; a mismatch at any
tier is refused naming the tier, and an offline miss names
`<name>@<version>` and the origin. The archive is extracted, its root
hoisted, a binary placed as its `exe`, the shims made, the directory
collected as a tree, `tools/<name>@<version>` moved to it write-once,
and the tree viewed at the home's tool directory. A second `ensure`
is a probe that answers offline. A directory the store did not make
is never removed.

`link` fills a bin directory with one link per executable the
deployments' `paths` name, a launcher where the platform refuses a
link, and removes only links it made; `delta` is the one PATH prepend
and the env with `$package` replaced by the tool directory. `fetch`
lands every version's artifact for the hosts asked into a store at
another root, a mirror by construction, for an offline install
elsewhere.

A version or host the record does not carry refuses by name, naming
what it does carry. The six host keys are `macos-arm`, `macos-x64`,
`linux-x64`, `linux-arm`, `windows-x64` and `windows-arm`. The
delegated kinds, `uv-tool`, `uv-python`, `bun-install` and
`system-check`, and the `fm tools.*` verbs follow in the plan's next
phases.
