"""One interface to GitHub, Gitea, and GitLab.

The protocol is not here yet: this release reserves the distribution
name and proves the release train. Runtime dependencies are the
standard library and nothing else; a workspace test enforces it.
"""

from __future__ import annotations

from livery.forge._errors import Unsupported

__version__ = "0.0.1"

__all__ = ["Unsupported", "__version__"]
