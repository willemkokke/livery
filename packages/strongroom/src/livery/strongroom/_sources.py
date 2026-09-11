"""Sources and tiers: where a store looks for an object it does not have.

A store instance is its local objects directory plus an ordered list
of sources. A source is a folder in the same layout, an HTTP base URL
serving the layout, or an origin hint: one URL that yields one named
object. Every source is verified and none is trusted: a hit is hashed
on arrival, an unreachable source is skipped after a short connect
timeout and reported, and `offline` means never an origin.

Reach for [livery.strongroom.FolderSource][],
[livery.strongroom.HttpSource][] and [livery.strongroom.OriginHint][]
to declare sources at [livery.strongroom.Store.open][], and
[livery.strongroom.Store.fetch][] to consult them.
"""

from __future__ import annotations

import contextlib
import http.client
import socket
from collections.abc import Callable, Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlsplit

from livery.strongroom._digest import Digest

FillPolicy = Literal["copy", "reference"]
"""What a folder hit does: land a local copy, or answer into the folder."""

Progress = Callable[[str], None]
"""Where a fetch reports a skipped or refused source; never prints on its own."""

_CONNECTIONS: dict[str, type[http.client.HTTPConnection]] = {
    "http": http.client.HTTPConnection,
    "https": http.client.HTTPSConnection,
}


def _check_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in _CONNECTIONS or not parts.hostname:
        raise ValueError(f"url {url!r} is not http or https with a host")


@dataclass(frozen=True)
class FolderSource:
    """A folder in the store layout: a mirror, or a read-only share.

    Attributes:
        path: the folder's root, holding a manifest of the same layout
            and algorithm as the store that consults it.
        fill: `copy` lands a hit into the local store; `reference`
            answers with a path into the folder and lands nothing, for
            a share where a local copy would be a second copy of
            everything.
    """

    path: Path
    fill: FillPolicy = "copy"

    def __post_init__(self) -> None:
        if self.fill not in ("copy", "reference"):
            raise ValueError(f"fill {self.fill!r} is not copy or reference")

    def describe(self) -> str:
        """The source, for a report."""
        return f"folder {self.path} ({self.fill})"


@dataclass(frozen=True)
class HttpSource:
    """An HTTP base URL serving the store layout: nginx over a folder.

    Attributes:
        base_url: the URL the layout hangs from; a trailing slash is
            ignored.
        connect_timeout: seconds to wait for the server to answer at
            all; an unanswered source is skipped and reported.
        transfer_timeout: seconds a stalled transfer may wait between
            bytes.
    """

    base_url: str
    connect_timeout: float = 3.0
    transfer_timeout: float = 60.0

    def __post_init__(self) -> None:
        _check_url(self.base_url)

    def describe(self) -> str:
        """The source, for a report."""
        return f"http {self.base_url}"

    def url(self, relative: str) -> str:
        """The URL of *relative*, a path in the layout."""
        return f"{self.base_url.rstrip('/')}/{relative}"


@dataclass(frozen=True)
class OriginHint:
    """One URL that yields one named object: the ingress form.

    A tool spec's `{url, sha256}` per host is an origin hint, and so is
    a vendor download. Consulted only for its own digest, and never
    when the store is offline.

    Attributes:
        digest: the object the URL yields.
        url: where to get it.
        connect_timeout: seconds to wait for an answer.
        transfer_timeout: seconds a stalled transfer may wait.
    """

    digest: Digest
    url: str
    connect_timeout: float = 3.0
    transfer_timeout: float = 60.0

    def __post_init__(self) -> None:
        _check_url(self.url)

    def describe(self) -> str:
        """The source, for a report."""
        return f"origin {self.url}"


Source = FolderSource | HttpSource | OriginHint
"""Anything a store may consult for an object."""


class Unreachable(Exception):
    """A source did not answer for this object; the fetch skips it and reports."""


@contextlib.contextmanager
def fetch_url(
    url: str, *, connect_timeout: float, transfer_timeout: float
) -> Generator[http.client.HTTPResponse]:
    """Open *url* for reading, or raise Unreachable naming why.

    The one seam every network read goes through, so tests fake it and
    a fetch never needs a server. The connect timeout bounds
    establishing the connection; the transfer timeout then bounds
    each wait for bytes, headers included.

    Yields:
        The response, status 200, positioned at its first byte.

    Raises:
        Unreachable: on a connection error, a timeout, or any status
            other than 200. A 404 is the source saying it does not
            have the object, which for the fetch is the same thing.
    """
    parts = urlsplit(url)
    connection = _CONNECTIONS[parts.scheme](
        cast(str, parts.hostname), parts.port, timeout=connect_timeout
    )
    try:
        target = parts.path or "/"
        if parts.query:
            target = f"{target}?{parts.query}"
        # Connecting is bounded by the connect timeout; once the socket
        # exists, every later wait is bounded by the transfer timeout.
        connection.connect()
        cast(socket.socket, connection.sock).settimeout(transfer_timeout)
        connection.request("GET", target)
        response = connection.getresponse()
        if response.status != 200:
            raise Unreachable(f"{url}: HTTP {response.status}")
        yield response
    except (OSError, http.client.HTTPException) as error:
        raise Unreachable(f"{url}: {error}") from None
    finally:
        connection.close()
