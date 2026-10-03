# Rendered by the template channel (packages/workshop/src/livery/workshop/templates, project kind);
# the gate keeps it matching its render. Edit the source and
# run `fm template.apply`; an edit here is drift.
"""The dev loop.

Run with ``fm <task>``. ``fm check`` is the whole local gate;
CI runs the same command. Nothing here mounts anything: the workshop and
every plugin this project depends on directly arrive through the
runner's project rung, and the workshop mounts the extensions the
contract lists. The region below is the repository's own, for tasks of
its own or a plugin it mounts by hand.
"""

# -- workshop: region tasks, yours to edit; the render keeps it --
from livery.footman.api import plugin

# footman's own docs pages (`fm footman.pages`): this repository builds
# them, so it mounts the plugin itself; a project depending on footman
# has no use for the verb.
plugin("livery.footman")
# -- workshop: end tasks --
