"""The HTTP plumbing every REST backend shares.

One JSON client over urllib: authenticated requests, refusal of
redirects, error translation into livery.forge.ForgeError with the
server's words attached, and pagination that is complete or raises.
The network seam is the opener, anything satisfying
livery.forge.testing.UrlOpener, so a backend records and replays its
traffic by construction argument alone.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from http.client import HTTPMessage
from typing import IO, Any, Protocol

from livery.forge._errors import ForgeError

#: Items asked for per page. Every forge here clamps at or above 50, so
#: a batch shorter than this means the last page has been reached.
PAGE_SIZE = 50

#: Pages any one listing walks before the completeness rule raises: the
#: listing is not complete, so "not found" would be a guess.
PAGE_CAP = 40


class Opener(Protocol):
    """The network seam: urllib's opener shape, replayable in tests."""

    def open(self, request: urllib.request.Request, /, *, timeout: float = 30.0) -> Any:
        """Open *request* and return a urllib response object."""
        ...


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    """Report a redirect instead of replaying the request elsewhere.

    urllib's default handler copies every non-Content header onto the
    redirected request, the Authorization token included, with no
    same-host check, and 301/302 turn a POST into a GET. A
    misconfigured or hostile server could collect the token, and a
    write could silently become a read. Returning None makes urllib
    raise the 3xx as an HTTPError, which the client renders as a
    livery.forge.ForgeError naming the location.
    """

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        """Refuse every redirect: None means "do not follow this"."""
        return None


def default_opener() -> Opener:
    """The opener a backend uses when none is injected.

    Keeps every default handler (proxies included) except the redirect
    one, which livery.forge._http._RefuseRedirect replaces.
    """
    return urllib.request.build_opener(_RefuseRedirect())


class JsonClient:
    """An authenticated JSON API client for one server.

    Args:
        api_base: The API root, no trailing slash.
        headers: The authentication headers, sent on every request. An
            empty mapping reads anonymously.
        opener: The network seam; the redirect-refusing default when
            omitted.
    """

    def __init__(
        self,
        api_base: str,
        *,
        headers: dict[str, str],
        opener: Opener | None = None,
    ) -> None:
        """Bind the client to *api_base* with *headers*."""
        self.api_base = api_base.rstrip("/")
        self._headers = headers
        self._opener = opener if opener is not None else default_opener()

    def request(
        self,
        endpoint: str,
        *,
        method: str = "GET",
        data: dict[str, Any] | None = None,
        none_on: tuple[int, ...] = (),
        timeout: float = 30,
    ) -> Any:
        """Make one request; the parsed JSON answer, or None per *none_on*.

        Returns the parsed JSON body ({} for an empty one). Statuses in
        *none_on* return None instead of raising; every other HTTP
        error raises livery.forge.ForgeError with the status as a
        number and the body verbatim in ``detail``. An unreachable
        server raises the same type with no status. A redirect is
        refused rather than followed and raises with the 3xx status.

        *data* is sent as a JSON body whenever it is not None, so an
        empty mapping means "an empty object", not "no body".
        """
        raw = self._raw(
            endpoint, method=method, data=data, none_on=none_on, timeout=timeout
        )
        if raw is None:
            return None
        # strict=False: user-authored fields (PR bodies) have been
        # observed carrying literal control characters, which the
        # default strict decoder rejects mid-poll.
        return json.loads(raw, strict=False) if raw else {}

    def text(
        self,
        endpoint: str,
        *,
        none_on: tuple[int, ...] = (),
        timeout: float = 30,
    ) -> str | None:
        """GET *endpoint* as plain text (a job log), or None per *none_on*."""
        raw = self._raw(
            endpoint, method="GET", data=None, none_on=none_on, timeout=timeout
        )
        return None if raw is None else raw.decode("utf-8", errors="replace")

    def _raw(
        self,
        endpoint: str,
        *,
        method: str,
        data: dict[str, Any] | None,
        none_on: tuple[int, ...],
        timeout: float,
    ) -> bytes | None:
        url = f"{self.api_base}{endpoint}"
        payload = json.dumps(data).encode() if data is not None else None
        headers = dict(self._headers)
        if payload is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url, data=payload, method=method, headers=headers
        )
        try:
            response = self._opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code in none_on:
                return None
            detail = exc.read().decode(errors="replace")
            if 300 <= exc.code < 400:
                location = exc.headers.get("Location", "") if exc.headers else ""
                raise ForgeError(
                    f"the server redirected {method} {endpoint}"
                    + (f" to {location}" if location else "")
                    + ", and following it would resend the token elsewhere."
                    + " Point the configured URL at the server that answers"
                    + " directly.",
                    status=exc.code,
                    method=method,
                    endpoint=endpoint,
                    detail=location,
                ) from exc
            raise ForgeError(
                f"HTTP {exc.code} on {method} {endpoint}: {detail}",
                status=exc.code,
                method=method,
                endpoint=endpoint,
                detail=detail,
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            # No status: the server never answered, which is a different
            # decision for the caller than any code it could have sent.
            raise ForgeError(
                f"server unreachable on {method} {endpoint}: {exc}",
                method=method,
                endpoint=endpoint,
            ) from exc
        body: bytes = response.read()
        return body

    def paginate(
        self,
        fetch: Callable[[int], list[Any]],
        *,
        subject: str,
    ) -> list[Any]:
        """Collect every page from *fetch*, or raise on an unfinished walk.

        *fetch* takes a 1-based page number and returns that page's
        items; a batch shorter than livery.forge._http.PAGE_SIZE ends
        the walk. Hitting livery.forge._http.PAGE_CAP with a full last
        page raises livery.forge.ForgeError naming *subject*: the
        listing is not complete, and a prefix is never the answer.
        """
        items: list[Any] = []
        page = 1
        while page <= PAGE_CAP:
            batch = fetch(page)
            items.extend(batch)
            if len(batch) < PAGE_SIZE:
                return items
            page += 1
        raise ForgeError(
            f"scanned {PAGE_CAP * PAGE_SIZE} entries of {subject} and the"
            " server has more: reporting a truncated listing would be a"
            " guess. Narrow the query."
        )
