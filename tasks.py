"""livery's dev loop: the workshop, mounted as every instance mounts it.

Run with ``uv run fm <task>``. ``fm check`` is the whole local gate;
CI runs the same command. The tree comes from the mounted layers; the
template seeds this file once and never rewrites it, so anything a
repository adds below the plugin line is its own.
"""

from footman import plugin

plugin("livery.workshop")
