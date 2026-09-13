# toolroom store

The pinned tool store over strongroom. A spec, `specs/<name>.json`,
names a tool, the version a checkout pins, and per host the
artifact's URL and its sha256; the store lands the artifact by that
pin, extracts it, collects it as a tree, and names the tree under
`tools/<name>@<version>` in one strongroom store per machine, shared
by every checkout on it. The spec format is byte-compatible with
hse's, so a spec moves between the two without an edit.

This release carries the spec model and the home's layout:

```python
from pathlib import Path

from livery.toolroom.store import Home, Spec, host_key

spec = Spec.load(Path("specs/bun.json"))
definition = spec.definition_for(host_key("Darwin", "arm64"))
store = Home(Path.home() / ".local/share/toolroom").open_store()
```

A definition with a URL and no sha256 refuses at load. A host the
spec does not carry refuses by name, naming the hosts it does. The
six host keys are `macos-arm`, `macos-x64`, `linux-x64`,
`linux-arm`, `windows-x64` and `windows-arm`.

Installing, emitting the PATH delta, fetching every host's artifact
into a mirror, and the `fm tools.*` verbs follow in the next phases
of the plan in toolroom's notes.
