# Rendered by the template channel (packages/workshop/src/livery/workshop/templates, project kind);
# the gate keeps it matching its render. Edit the source and
# run `fm template.apply`; an edit here is drift.
"""The dev loop: the workspace's layers, mounted.

Run with ``fm <task>``. ``fm check`` is the whole local gate;
CI runs the same command. The tree comes from the mounted layers; the
template seeds this file once and never rewrites it, so anything a
repository adds below the mount lines is its own.
"""

from footman import plugin

plugin("livery.workshop")

# Every further layer the contract names mounts here, each under its
# own identity, so a task's provenance names its real provider.
# Composition belongs to the workspace's own file, never to a
# layer's import side effects. The late import is load-bearing:
# plugin() above must be the layer's first importer, so its task
# registration lands inside footman's capture.
from livery.workshop import mount_layers  # noqa: E402

mount_layers()
