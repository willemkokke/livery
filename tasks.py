"""livery's dev loop: the workshop, mounted as every instance mounts it.

Run with ``uv run fm <task>``. ``fm check`` is the whole local gate;
CI runs the same command. The tree comes from the base layer; this
file is what the project template renders for every instance.
"""

from footman import plugin

plugin("livery.workshop")
