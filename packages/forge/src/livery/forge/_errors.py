"""The exceptions every backend raises the same way."""

from __future__ import annotations


class Unsupported(Exception):
    """Raised when a forge cannot honour a protocol operation.

    The message says why: a server version that predates the
    operation, or a capability the forge declines by name.
    """
