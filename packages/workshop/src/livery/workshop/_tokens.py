"""The forge token surface: two names, host-qualified variants.

``FORGE_TOKEN`` and ``FORGE_ADMIN_TOKEN`` are the only token names
the workshop speaks. The shared rung may store host-qualified
variants (``FORGE_TOKEN__<HOST>``), resolved through the contract's
forge URL, so one machine serves several forges and two instances
of one kind never collide. Everything per-forge (ambient job
tokens, secret APIs, dialects, their own fallback variables) lives
in the backends.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse

#: The host a kind means when the contract carries no URL. Gitea has
#: no public default: its contract always names the server.
_DEFAULT_HOSTS = {"github": "github.com", "gitlab": "gitlab.com"}


def host_qualifier(kind: str, url: str) -> str:
    """The key suffix the contract's forge resolves through.

    The host (and port, when one is spelled) uppercased, every other
    character folded to ``_``: ``https://forge.example.com:3000`` is
    ``FORGE_EXAMPLE_COM_3000``. Empty when neither the URL nor the
    kind names a host, and the bare names are the whole ladder.
    """
    host = urlparse(url).netloc if url else ""
    if not host:
        host = _DEFAULT_HOSTS.get(kind, "")
    return re.sub(r"[^A-Z0-9]", "_", host.upper())


def _lookup(name: str, qualifier: str) -> tuple[str, str]:
    """The first non-empty rung of one name's ladder, and its variable."""
    if qualifier:
        qualified = f"{name}__{qualifier}"
        value = os.environ.get(qualified, "")
        if value:
            return value, qualified
    value = os.environ.get(name, "")
    if value:
        return value, name
    return "", ""


def forge_token(kind: str, url: str) -> tuple[str, str]:
    """The everyday token and the variable that supplied it.

    Host-qualified first, then bare ``FORGE_TOKEN``; both empty means
    the backend's own documented resolution decides (GitHub asks
    ``gh auth token``; a backend may read its own dialect's
    variables).
    """
    return _lookup("FORGE_TOKEN", host_qualifier(kind, url))


def admin_token(kind: str, url: str) -> tuple[str, str]:
    """The admin ladder: ``FORGE_ADMIN_TOKEN`` first, then the everyday token.

    The fallback keeps a solo developer whose one token already
    administers working with nothing extra. The variable name rides
    along so a refusal can teach the missing grant; it is empty when
    nothing resolved.
    """
    token, var = _lookup("FORGE_ADMIN_TOKEN", host_qualifier(kind, url))
    if token:
        return token, var
    return forge_token(kind, url)
