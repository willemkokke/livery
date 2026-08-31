"""Record and replay HTTP exchanges over urllib, with secrets scrubbed.

A cassette is an ordered list of request and response pairs, stored as
JSON. Recording sends real requests through urllib and appends each
exchange; replaying answers the same requests from the file, in order,
with no network at all. The replayed run therefore proves the caller
makes the requests the recording made, byte for byte, and CI needs no
credentials and no server.

Secrets never reach disk: the caller names them at record time and
every occurrence in the URL and both bodies is replaced before the
exchange is stored. Request headers are not stored at all, which is
where tokens actually travel.

The seam is any object with ``open(request, *, timeout)``: a backend
that takes its opener as a constructor argument records with
livery.forge.testing.RecordingOpener, replays with
livery.forge.testing.ReplayOpener, and cannot tell either from the
real thing. The file format is documented in the package's
``docs/fixtures.md``.
"""

from __future__ import annotations

import email.message
import io
import json
import re
import urllib.error
import urllib.request
import urllib.response
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from livery.forge._http import default_opener

#: The value that replaces every scrubbed secret.
REDACTED = "REDACTED"

#: The stored body of an exchange whose request body is nondeterministic
#: (an encrypted payload): recorded as this marker, matched by URL and
#: method alone on replay.
VOLATILE = "VOLATILE"

#: The cassette file format this module reads and writes.
FORMAT = 1


class CassetteError(Exception):
    """Raised when replay and recording disagree.

    The message names the request that arrived and the exchange the
    cassette holds, verbatim, so the drift is readable without
    re-running anything.
    """


class UrlOpener(Protocol):
    """Anything that opens a urllib request: the one seam of this module.

    urllib's own OpenerDirector satisfies it, and so do
    livery.forge.testing.RecordingOpener and
    livery.forge.testing.ReplayOpener, which is what lets a backend
    take its opener as a constructor argument and never know whether
    the network is real.
    """

    def open(self, request: urllib.request.Request, /, *, timeout: float = 30.0) -> Any:
        """Open *request* and return a urllib response object."""
        ...


@dataclass(frozen=True)
class Exchange:
    """One recorded request and its response, secrets already scrubbed.

    Attributes:
        method: The HTTP method.
        url: The full URL, scrubbed.
        request_body: The request body as text, scrubbed. Empty when
            the request had none.
        status: The HTTP status code of the response.
        reason: The status line's reason phrase.
        content_type: The response's Content-Type header value.
        response_body: The response body as text, scrubbed.
        location: The Location header of a refused redirect, scrubbed.
            Empty for every other exchange. Carried so a caller that
            deliberately follows one refused redirect (a signed log
            URL) can do so from the replayed refusal too.
    """

    method: str
    url: str
    request_body: str
    status: int
    reason: str
    content_type: str
    response_body: str
    location: str = ""


class Cassette:
    """An ordered recording of HTTP exchanges, loadable and savable as JSON.

    Attributes:
        exchanges: The exchanges, in the order they happened.
    """

    def __init__(self, exchanges: Iterable[Exchange] = ()) -> None:
        """Hold *exchanges*, empty by default for a fresh recording."""
        self.exchanges: list[Exchange] = list(exchanges)

    @classmethod
    def load(cls, path: Path) -> Cassette:
        """Read the cassette at *path*.

        Raises livery.forge.testing.CassetteError when the file's
        format number is not the one this module writes.
        """
        data = json.loads(path.read_text("utf-8"))
        if data.get("format") != FORMAT:
            raise CassetteError(
                f"{path} has cassette format {data.get('format')!r}, and this"
                f" reader speaks format {FORMAT}: re-record the cassette"
            )
        return cls(Exchange(**raw) for raw in data["exchanges"])

    def save(self, path: Path) -> None:
        """Write the cassette to *path*, creating parent directories."""
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format": FORMAT,
            "exchanges": [asdict(exchange) for exchange in self.exchanges],
        }
        path.write_text(json.dumps(payload, indent=2) + "\n", "utf-8")


def _scrub(text: str, secrets: tuple[str, ...]) -> str:
    """*text* with every occurrence of every secret replaced."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    return text


def _scrub_fields(body: str, fields: tuple[str, ...]) -> str:
    """*body* with every named JSON field's string value replaced.

    The caller's secret list covers credentials it holds; this covers
    the ones the server mints inside its answers (a project's
    runners_token), which no caller can know in advance.
    """
    for field in fields:
        body = re.sub(
            rf'("{re.escape(field)}"\s*:\s*")[^"]*(")',
            rf"\g<1>{REDACTED}\g<2>",
            body,
        )
    return body


def _request_body(request: urllib.request.Request) -> str:
    """The request's body as text, empty when it has none."""
    data = request.data
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    raise CassetteError(
        "only bytes request bodies can be recorded: streaming bodies have"
        " no stable representation to match on replay"
    )


def _response_for(exchange: Exchange, url: str) -> urllib.response.addinfourl:
    """A urllib-shaped response answering with *exchange*'s body."""
    headers = email.message.Message()
    headers["Content-Type"] = exchange.content_type
    body = exchange.response_body.encode("utf-8")
    response = urllib.response.addinfourl(
        io.BytesIO(body), headers, url, exchange.status
    )
    return response


class RecordingOpener:
    """An opener that performs real requests and records each exchange.

    Wrap the opener a backend would use, run the operations once
    against the real server, then save the cassette. Every value in
    *secrets* is scrubbed from URLs and bodies before an exchange is
    stored, and request headers are never stored.

    HTTP errors are recorded like any other exchange and re-raised, so
    the recording captures refusals as faithfully as successes.

    Args:
        cassette: The cassette exchanges are appended to.
        secrets: The values that must never reach disk. Pass every
            token the recorded client holds.
        scrub_fields: JSON field names whose string values are
            redacted in stored response bodies: the secrets the
            server mints inside its answers, which no secret list can
            know in advance.
        inner: The opener that actually talks to the network. The
            redirect-refusing default when omitted, never urllib's
            stock opener: following a redirect would forward the
            Authorization header to the new location, and a recording
            session holds real credentials.
    """

    def __init__(
        self,
        cassette: Cassette,
        *,
        secrets: tuple[str, ...] = (),
        volatile_bodies: tuple[str, ...] = (),
        scrub_fields: tuple[str, ...] = (),
        inner: UrlOpener | None = None,
    ) -> None:
        """Record through *inner* into *cassette*, scrubbing *secrets*.

        A request whose URL contains any *volatile_bodies* substring
        has a nondeterministic body (an encrypted payload); it is
        stored as the VOLATILE marker and matched by method and URL
        alone on replay.
        """
        self.cassette = cassette
        self._secrets = secrets
        self._volatile = volatile_bodies
        self._scrub_fields = scrub_fields
        self._inner: UrlOpener = inner if inner is not None else default_opener()

    def open(self, request: urllib.request.Request, /, *, timeout: float = 30.0) -> Any:
        """Send *request*, record the scrubbed exchange, answer as urllib does."""
        method = request.get_method()
        url = request.full_url
        body = _request_body(request)
        try:
            response = self._inner.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read()
            exchange = self._record(
                method,
                url,
                body,
                status=exc.code,
                reason=exc.reason,
                content_type=exc.headers.get("Content-Type", ""),
                response_body=detail.decode("utf-8", errors="replace"),
                location=exc.headers.get("Location", ""),
            )
            raise _replay_error(exchange, url) from None
        raw = response.read()
        exchange = self._record(
            method,
            url,
            body,
            status=response.status,
            reason=getattr(response, "reason", "") or "",
            content_type=str(response.headers.get("Content-Type", "")),
            response_body=raw.decode("utf-8", errors="replace"),
        )
        return _response_for(exchange, url)

    def _record(
        self,
        method: str,
        url: str,
        request_body: str,
        *,
        status: int,
        reason: str,
        content_type: str,
        response_body: str,
        location: str = "",
    ) -> Exchange:
        volatile = any(marker in url for marker in self._volatile)
        exchange = Exchange(
            method=method,
            url=_scrub(url, self._secrets),
            request_body=VOLATILE if volatile else _scrub(request_body, self._secrets),
            status=status,
            reason=reason,
            content_type=content_type,
            response_body=_scrub_fields(
                _scrub(response_body, self._secrets), self._scrub_fields
            ),
            location=_scrub(location, self._secrets),
        )
        self.cassette.exchanges.append(exchange)
        return exchange


class ReplayOpener:
    """An opener that answers from a cassette, in order, with no network.

    Each request must match the next recorded exchange on method, URL,
    and body, after the same scrubbing the recording applied. A
    mismatch or an exhausted cassette raises
    livery.forge.testing.CassetteError naming both sides, because a
    drifted request is the finding, not a nuisance.

    Args:
        cassette: The recording to answer from.
        secrets: The values the replaying client holds where the
            recording held real ones. Scrubbed from incoming requests
            before matching, so a dummy token replays against a
            recording made with a real one.
    """

    def __init__(self, cassette: Cassette, *, secrets: tuple[str, ...] = ()) -> None:
        """Replay *cassette*, scrubbing *secrets* from incoming requests."""
        self._exchanges = list(cassette.exchanges)
        self._secrets = secrets
        self._cursor = 0

    @property
    def remaining(self) -> int:
        """How many recorded exchanges have not been replayed yet."""
        return len(self._exchanges) - self._cursor

    def verify_exhausted(self) -> None:
        """Raise unless every recorded exchange was replayed.

        A leftover exchange means the caller stopped making a request
        the recording made, which is drift in the quiet direction.
        """
        if self.remaining:
            nxt = self._exchanges[self._cursor]
            raise CassetteError(
                f"{self.remaining} recorded exchanges were never replayed;"
                f" the next one is {nxt.method} {nxt.url}"
            )

    def open(self, request: urllib.request.Request, /, *, timeout: float = 30.0) -> Any:
        """Answer *request* from the next recorded exchange."""
        method = request.get_method()
        url = _scrub(request.full_url, self._secrets)
        body = _scrub(_request_body(request), self._secrets)
        if self._cursor >= len(self._exchanges):
            raise CassetteError(
                f"the cassette is exhausted and {method} {url} arrived:"
                " the caller makes a request the recording never made"
            )
        expected = self._exchanges[self._cursor]
        body_matches = (
            expected.request_body == VOLATILE or body == expected.request_body
        )
        if (method, url) != (expected.method, expected.url) or not body_matches:
            raise CassetteError(
                "replay mismatch:\n"
                f"  recorded: {expected.method} {expected.url}"
                f" body={expected.request_body!r}\n"
                f"  arrived:  {method} {url} body={body!r}\n"
                "Re-record the cassette if the new request is intended."
            )
        self._cursor += 1
        if expected.status >= 300:
            # Refused redirects (3xx) and refusals (4xx, 5xx) alike
            # replay as the errors the recording raised.
            raise _replay_error(expected, request.full_url)
        return _response_for(expected, request.full_url)


def _replay_error(exchange: Exchange, url: str) -> urllib.error.HTTPError:
    """The HTTPError a recorded refusal raises, on record and on replay."""
    headers = email.message.Message()
    headers["Content-Type"] = exchange.content_type
    if exchange.location:
        headers["Location"] = exchange.location
    return urllib.error.HTTPError(
        url,
        exchange.status,
        exchange.reason,
        headers,
        io.BytesIO(exchange.response_body.encode("utf-8")),
    )
