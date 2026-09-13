# toolroom store

The pinned tool store over strongroom. A spec, `specs/<name>.json`,
names a tool, the version a checkout pins, and per host the
artifact's URL and its sha256; the store lands the artifact by that
pin, extracts it, collects it as a tree, and names the tree under
`tools/<name>@<version>` in one strongroom store per machine, shared
by every checkout on it. The spec format is byte-compatible with
hse's, so a spec moves between the two without an edit.

The store installs, links, emits and fetches:

```python
from pathlib import Path

from livery.strongroom import FolderSource
from livery.toolroom.store import Home, Spec, Store

store = Store(
    Home(Path.home() / ".local/share/toolroom"),
    sources=[FolderSource(Path("/mirrors/tools/store"))],
)
ensured = [store.ensure(Spec.load(path)) for path in Path("specs").glob("*.json")]
store.link(ensured, Path(".workshop/bin"))
delta = store.delta(ensured, Path(".workshop/bin"))
```

`ensure` lands the pinned artifact through the sources in order and
through the origin URL unless the store is offline; a mismatch at any
tier is refused naming the tier, and an offline miss names
`<name>@<version>` and the origin. The archive is extracted, its root
hoisted, a binary placed as its `exe`, the shims made, the directory
collected as a tree, `tools/<name>@<version>` moved to it write-once,
and the tree viewed at the home's tool directory. A second `ensure`
is a probe that answers offline. A directory the store did not make
is never removed.

`link` fills a bin directory with one link per executable the specs'
`paths` name, a launcher where the platform refuses a link, and
removes only links it made; `delta` is the one PATH prepend and the
env with `$package` replaced by the tool directory. `fetch` lands
every host's artifact into a store at another root, a mirror by
construction, for an offline install elsewhere.

A definition with a URL and no sha256 refuses at load. A host the
spec does not carry refuses by name, naming the hosts it does. The
six host keys are `macos-arm`, `macos-x64`, `linux-x64`,
`linux-arm`, `windows-x64` and `windows-arm`. The delegated kinds,
`uv-tool`, `uv-python`, `bun-install` and `system-check`, and the
`fm tools.*` verbs follow in the plan's next phases.
