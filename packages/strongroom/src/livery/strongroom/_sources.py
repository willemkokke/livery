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
from collections.abc import Callable, Generator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urljoin, urlsplit

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
    """A source did not answer for this object; the fetch skips it and reports.

    Attributes:
        status: The HTTP status the source answered with, when the
            refusal is an answer (a 404, a 503) rather than a connection
            that failed or a chain that did not end; None otherwise. A
            caller deciding whether to try again reads it: a connection
            failure and a 5xx are worth another try, a 404 is not.
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


REDIRECTS = 5
"""How many redirects a fetch follows before it refuses the chain."""

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


@contextlib.contextmanager
def fetch_url(
    url: str,
    *,
    connect_timeout: float,
    transfer_timeout: float,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
) -> Generator[http.client.HTTPResponse]:
    """Open *url* for reading, or raise Unreachable naming why.

    The one seam every network read goes through, so tests fake it and
    a fetch never needs a server. The connect timeout bounds
    establishing the connection; the transfer timeout then bounds
    each wait for bytes, headers included. `method` is `GET`, or
    `HEAD` to learn whether a source holds an object without reading it.
    *headers* are sent with the request to *url*'s host and port, and
    to a redirect on that same host and port; a redirect anywhere else
    gets none of them, so a credential meant for an API never reaches
    the storage host the API redirects a download to.

    A redirect (301, 302, 303, 307, 308) is followed to its `Location`,
    resolved against the URL that answered it, with the same method
    and both timeouts per hop, up to `REDIRECTS` hops: a forge serves
    a release asset from a storage host it redirects to, and the bytes
    are the same bytes. A chain longer than that, or a redirect naming
    no location, is refused naming the chain.

    Yields:
        The response, status 200, positioned at its first byte.

    Raises:
        Unreachable: on a connection error, a timeout, a redirect chain
            that does not end, or any final status other than 200. A
            404 is the source saying it does not have the object, which
            for the fetch is the same thing.
    """
    chain = [url]
    first = urlsplit(url)
    first_host = (first.hostname, first.port)
    while True:
        parts = urlsplit(chain[-1])
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
            same = (parts.hostname, parts.port) == first_host
            sent = dict(headers or {}) if same else {}
            connection.request(method, target, headers=sent)
            response = connection.getresponse()
            if response.status in _REDIRECT_STATUSES:
                location = response.getheader("Location")
                if not location:
                    raise Unreachable(
                        f"{chain[-1]}: HTTP {response.status} names no location"
                        f" (after {' -> '.join(chain)})"
                    )
                if len(chain) > REDIRECTS:
                    raise Unreachable(
                        f"{url}: more than {REDIRECTS} redirects"
                        f" ({' -> '.join([*chain, location])})"
                    )
                chain.append(urljoin(chain[-1], location))
                _check_url(chain[-1])
                continue
            if response.status != 200:
                raise Unreachable(
                    f"{chain[-1]}: HTTP {response.status}", status=response.status
                )
            yield response
            return
        except (OSError, http.client.HTTPException) as error:
            raise Unreachable(f"{chain[-1]}: {error}") from None
        except ValueError as error:
            raise Unreachable(f"{chain[-1]}: {error}") from None
        finally:
            connection.close()
