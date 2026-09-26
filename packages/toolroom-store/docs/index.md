# toolroom store

The pinned tool store over strongroom. A record, one directory per
tool, names the tool, every version tracked, per version and host the
artifact's URL and its sha256 with the deployment resolved through the
record's layers, and per version the surface its command line
accepts; the store lands the artifact by that digest, extracts it,
collects it as a tree, and names the tree under
`tools/<name>@<version>` in one strongroom store per machine, shared
by every checkout on it.

The store installs, links, emits and fetches:

```python
from pathlib import Path

from livery.strongroom import FolderSource
from livery.toolroom.store import Home, Record, Store, records_in

store = Store(
    Home(Path.home() / ".local/share/toolroom"),
    sources=[FolderSource(Path("/mirrors/tools/store"))],
)
records = [Record.load(path) for path in records_in(Path("records"))]
ensured = [store.ensure(record, record.versions[-1]) for record in records]
store.link(ensured, Path(".workshop/bin"))
delta = store.delta(ensured, Path(".workshop/bin"))
```

## The record

A record is one file of JSON lines, `records/<tool>.jsonl`, named by
the tool. The first line is the tool axis: the name, the description,
the kind, the version floor a `system-check` tool must reach, `prime`,
the oldest version the reading history reaches, the hosts the tool
has, the tool's layout and its override for one host each. A version
line is one version's arrival: the version, its date, per host the
artifact's URL and sha256, the version's override of the layout, for
every host or for one, and `read`, the platforms and the extractor
that read it and the tool's description when it changed. The
statement lines under a version line are what its reading changed:
one option per line with the option's fields whole, one line for a
verb's own fields when they moved, one line for an option or a verb
withdrawn (`gone`), and one line per absence, the platforms that read
the version and did not find the option. Versions run in file order,
a release appends lines, and the review diff is the options that
moved. An option unchanged since the previous reading has no line.

A deployment resolves through four layers, most specific winning: the
tool's layout, the tool's override for the host, the version's layout,
the version's override for the host. The layout fields are `root`, the
directory inside the archive hoisted to the install root; `exe`, a
binary's executable name; `entry_points`, the install-relative paths of
the executables the deployment puts on PATH, annotated here and never
discovered by scanning a directory; `paths`, the install-relative
directories put on PATH; `env`, with `$package` standing for the
install root, where a tool's own switches belong (an update check, a
telemetry opt-out), since entering the environment exports them and
every spawn of the tool inherits them; `shims`, a link name to an
executable the install
carries; and `exclude`, the archive members left out before import,
`fnmatch` patterns over the install-relative path.

## The surface

A delta may carry a `surface`: what the version's command line
accepts, as it was read, and who read it. The reading is one verb at
a time. `verbs` names the verbs this version changed, each whole, with
`help`, `wraps`, `positional`, `lead` and its `options`, and an option
carries `flags`, `negation`, `help`, `type`, `default` and `choices`;
a verb set to `null` is withdrawn, and a verb not named is inherited
from the nearest earlier version that has a surface. The tool's own
options hang off the verb named `""`. `help`, the tool's description,
is set when it changed and inherited otherwise; the record's first
surface sets it. `platforms` names who read the version, from `Linux`,
`macOS` and `Windows`, and `extractor` the generation of the reader;
neither is inherited. `absent` records, per verb and option, the
platforms that read the version and did not find that option, the
option named `""` standing for the verb itself; it names only
platforms among `platforms`. A version whose delta carries no surface
was never read, and `surface_at` answers `None` for it rather than
inheriting a reading. A version with a surface and no artifact is
tracked for its surface alone: it has no host and nothing installs it.

`surface_at` resolves one version whole, every verb in name order with
its options in name order, and `observations` resolves every version
that has a surface, in sequence.

## The catalogue and the lock

A consumer resolves against the catalogue, never against a record. The
catalogue lists every tool with its versions in order, per version the
hosts it has an artifact for with the digest of each host's deployment,
and per version read its surface, from which the store renders that
version's stub. It is read on demand, the pointer first, a tool's tree when the tool is asked for
and a version's hosts when a lock needs them, from the published
index, by URL or from a directory holding one,
through the machine's store, which keeps what it fetched so a second
read is offline; the authoring site reads its records directly and gets
the same catalogue, since a deployment's digest is the digest of its
canonical JSON either way.

A requirement is a tool's name with a floor, `ruff` or `ruff>=0.16`.
The lock takes for each tool the newest version the catalogue lists
that satisfies every floor and resolves on every locked host: a
downloaded kind resolves on a host when it has that host's artifact, a
delegated kind everywhere its installer does. A requirement that cannot
be met refuses naming the tool, each floor with the site that declared
it, and for a host no eligible version has, the first version that has
it. The lock is `tools.lock` at the repository root, one version per
tool with the deployment digest per locked host; an entry stands while
it still satisfies and resolves, and moves only when asked.

## What a load refuses

Loading a record resolves every host of every version and every
version's surface, and refuses a record that does not resolve whole:
an artifact without a sha256, a layer naming a host or a version the
record does not carry, a layer restating the value it inherits, a
version whose host resolves with no `paths`, a downloaded kind with no
entry point, a binary with no `exe` or whose `exe` is not among its
entry points, a version with neither an artifact nor a surface, a
surface read on no platform, a surface restating the help or a verb it
inherits, a withdrawn verb no earlier version has, an absence naming a
verb or option the version lacks or a platform that did not read it,
and a delta out of sequence. The schema of both documents is
`records/record.schema.json`, exported by `export_schema`.

## The store

`ensure` lands a version's artifact through the sources in order and
through the origin URL unless the store is offline; a mismatch at any
tier is refused naming the tier, and an offline miss names
`<name>@<version>` and the origin. The archive is extracted, its root
hoisted, the excluded members removed, a binary placed as its `exe`,
the shims made, every declared entry point checked for in the tree,
which refuses naming the tool, the version, the host and the path when
one is absent, the directory collected as a tree with the entry points
as its executables and nothing else, `tools/<name>@<version>` moved to
it write-once, and the tree viewed at the home's tool directory. One
archive lands one tree digest on every platform by construction: the
annotation decides the executable bit, not the modes the extractor
happened to produce. A second `ensure` is a probe that answers
offline. A directory the store did not make is never removed.

`link` fills a bin directory with one link per declared entry point,
a launcher where the platform refuses a link, and removes only links
it made; `delta` is the one PATH prepend
and the env with `$package` replaced by the tool directory. `fetch`
lands every version's artifact for the hosts asked into a store at
another root, a mirror by construction, for an offline install
elsewhere.

`supply` is the primitive `ensure` stands on: a tool by name, kind,
version and deployment, which is what a consumer holds after reading
the catalogue. The delegated kinds go through their installer rather
than the objects. A `uv-tool` is installed by uv at the locked version
into a directory of its own under the home, the record's `package`
naming what uv installs when it differs from the tool's name, and its
launchers under `bin` are its entry points. An `npm` tool is installed
the same way through the runtime its record names, node unless it says
bun, whose executable the caller supplied first: npm run on node, or
bun's own installer. Its launchers under `bin` are the entry points,
they start with `#!/usr/bin/env node`, and the runtime's directory
stays on PATH to answer that, bun through the `node` shim its record
declares. A launcher the runtime placed outside the tool's directory
is refused naming where it points. A `system-check` tool is the
machine's own, found on PATH and held to the record's `min_version`
alone, since its locked version is the newest reading the stubs render
for and not a version anyone installs, and the store installs nothing
for it. `uv-python` refuses naming the kind until it is supplied the
same way. A delegated tool has no tree.

A record's `mode` says how a materialised tool reaches PATH: `link`
puts its entry points in the checkout's bin directory, `path` its own
directories on PATH, `none` neither, for a tool reached only through a
typed handle; a binary links and a system tool takes `none` unless the
record says otherwise, and every other kind takes `path`.

A version or host the record does not carry refuses by name, naming
what it does carry. The six host keys are `macos-arm`, `macos-x64`,
`linux-x64`, `linux-arm`, `windows-x64` and `windows-arm`.
