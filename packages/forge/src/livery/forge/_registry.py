"""The package-index reader: livery.forge.Registry over the simple API.

One backend for every PEP 691 index, PyPI and the forges' own
registries alike: the simple API is the one interface they share,
and "which versions of this name are published" needs nothing more.
The release train's receipt probe reads through this, so the answer
must be the index's own, never a cache's.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from livery.forge._errors import ForgeError


class SimpleRegistry:
    """A PEP 691 simple-API index, addressed by its simple root.

    Args:
        base: The index's simple root (``https://pypi.org/simple``,
            or a forge registry's equivalent), with or without a
            trailing slash.
        token: Sent as basic auth when the index needs it; empty
            reads anonymously.
    """

    def __init__(self, base: str, *, token: str = "") -> None:
        self._base = base.rstrip("/")
        self._token = token

    def versions(self, name: str) -> tuple[str, ...]:
        """The published versions of *name*, oldest first.

        An unpublished name answers the empty tuple (the index's 404
        is that answer, not an error); an unreachable index raises
        livery.forge.ForgeError with the reason.
        """
        canonical = name.replace("_", "-").lower()
        request = urllib.request.Request(
            f"{self._base}/{canonical}/",
            headers={
                "Accept": "application/vnd.pypi.simple.v1+json",
                **({"Authorization": f"Bearer {self._token}"} if self._token else {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as answer:
                payload = json.load(answer)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return ()
            raise ForgeError(
                f"the index refused {canonical}: HTTP {exc.code}",
                status=exc.code,
            ) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ForgeError(
                f"the index at {self._base} is unreachable: {exc}"
            ) from exc
        return tuple(str(v) for v in payload.get("versions") or [])
