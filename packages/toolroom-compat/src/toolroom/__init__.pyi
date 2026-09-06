# The shim's typed surface is the real package's, whole.
from livery.toolroom import *  # noqa: F403 - the surface is the real package's
from livery.toolroom import Tool as Tool

def __getattr__(name: str) -> Tool: ...
