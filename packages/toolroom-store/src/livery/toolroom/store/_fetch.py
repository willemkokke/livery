"""Reading over HTTP, and unpacking what was read: bytes, JSON, a file, an archive.

One retrying read over [livery.strongroom.fetch_url][] for the store
and for every reader built on it. A transient failure (a connection
that dropped, a timeout, a 408, a 429, a 5xx) is tried again after a
pause; an answer (a 404, a 403) is final at once, since asking again
does not change it. Reach for [livery.toolroom.store.fetch_bytes][]
for an index, [livery.toolroom.store.fetch_json][] for a forge API
with its token, [livery.toolroom.store.fetch_file][] for an asset
cached by name, and [livery.toolroom.store.unpack][] to extract an
archive whole into a directory.
"""

from __future__ import annotations

import json
import os
import shutil
import tarfile
import time
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from livery.strongroom import Unreachable, fetch_url
from livery.toolroom.store._record import ARCHIVE_SUFFIXES

USER_AGENT = "livery-toolroom"
"""What every read says it is; GitHub and GitLab refuse a request without one."""

TRIES = 3
"""How many times a transient failure is tried before it is reported."""

BACKOFF = 1.0
"""Seconds between tries, multiplied by the try number."""

ARCHIVES = ARCHIVE_SUFFIXES
"""The archive suffixes the store unpacks; the record module's list."""

_TRANSIENT_STATUSES = frozenset({408, 429})


class FetchError(Unreachable):
    """A URL not read after the tries; the message names it and the last cause."""


class UnpackError(Exception):
    """An archive that will not unpack; the message names the archive and why."""


def api_headers(url: str) -> dict[str, str]:
    """The headers a read of *url* sends: a user agent, and GitHub's token for its API.

    The token comes from the environment (`GH_TOKEN`, then
    `GITHUB_TOKEN`) so nothing here depends on a CLI: GitHub allows 60
    unauthenticated API calls an hour per address and 5,000 with a
    token, and a shared runner spends the smaller budget on whoever
    else is on it. Only a URL on GitHub's API host gets the token;
    [livery.strongroom.fetch_url][] keeps it off any other host a
    redirect reaches.
    """
    headers = {"User-Agent": USER_AGENT}
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    return headers


def transient(error: Unreachable) -> bool:
    """Whether *error* is about the connection rather than the object.

    A failed connection, a timeout and a chain that did not end carry no
    status and are worth another try; so are a 408, a 429 and any 5xx.
    Every other status is the source's answer.
    """
    if error.status is None:
        return True
    return error.status in _TRANSIENT_STATUSES or 500 <= error.status < 600


sleep: Callable[[float], None] = time.sleep
"""Waits between tries. A variable, so a test retries without the pause."""


def fetch_bytes(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    tries: int = TRIES,
    connect_timeout: float = 10.0,
    transfer_timeout: float = 600.0,
) -> bytes:
    """The bytes at *url*, read whole; a transient failure is tried *tries* times.

    *headers* default to [livery.toolroom.store.api_headers][] for the
    URL.

    Raises:
        FetchError: when the tries are spent, or at once for an answer
            that says the object is not there.
    """
    sent = dict(headers) if headers is not None else api_headers(url)
    last: Unreachable | None = None
    for attempt in range(max(1, tries)):
        try:
            with fetch_url(
                url,
                connect_timeout=connect_timeout,
                transfer_timeout=transfer_timeout,
                headers=sent,
            ) as response:
                return response.read()
        except Unreachable as error:
            last = error
            if not transient(error) or attempt + 1 == tries:
                break
            sleep(BACKOFF * (attempt + 1))
    raise FetchError(f"{url}: {last}", status=last.status if last else None)


def fetch_json(url: str, *, tries: int = TRIES) -> Any:
    """The JSON at *url*, whatever shape the endpoint answers with.

    Raises:
        FetchError: when the URL cannot be read, or answers with
            something that is not JSON.
    """
    data = fetch_bytes(url, tries=tries)
    try:
        return json.loads(data.decode("utf-8"))
    except ValueError as error:
        raise FetchError(f"{url}: answered, but not with JSON ({error})") from error


def fetch_file(url: str, into: Path, *, tries: int = TRIES) -> Path:
    """Download *url* into *into*, named by its last path segment; the file.

    A file already there with bytes in it is the download, so a second
    ask costs nothing. A download that fails part-way leaves nothing
    behind: a part-written file is never a hit.

    Raises:
        FetchError: when the URL cannot be read after the tries.
    """
    into.mkdir(parents=True, exist_ok=True)
    dest = into / url.rsplit("/", 1)[-1]
    if dest.is_file() and dest.stat().st_size:
        return dest
    last: Unreachable | None = None
    for attempt in range(max(1, tries)):
        try:
            with (
                fetch_url(
                    url,
                    connect_timeout=10.0,
                    transfer_timeout=600.0,
                    headers=api_headers(url),
                ) as response,
                dest.open("wb") as out,
            ):
                shutil.copyfileobj(response, out)
            return dest
        except (Unreachable, OSError) as error:
            dest.unlink(missing_ok=True)
            last = error if isinstance(error, Unreachable) else Unreachable(str(error))
            if not transient(last) or attempt + 1 == tries:
                break
            sleep(BACKOFF * (attempt + 1))
    raise FetchError(f"{url}: {last}", status=last.status if last else None)


def unpack(archive: Path, into: Path, *, name: str = "", format: str = "") -> None:
    """Extract *archive*, a zip or a tar, whole into *into*.

    *name* is the archive's file name when the path does not carry it
    (an object landed by digest); its suffix decides the format unless
    *format* says `zip` or `tar`, for an archive whose name lies. A
    zip member's mode is restored where it is set, and a tar is
    extracted with the data filter, so a member cannot write outside
    *into*.

    Raises:
        UnpackError: for a suffix the store does not unpack, or an
            archive that will not extract.
    """
    label = (name or archive.name).lower()
    into.mkdir(parents=True, exist_ok=True)
    try:
        if format == "zip" or (not format and label.endswith(".zip")):
            with zipfile.ZipFile(archive) as opened:
                opened.extractall(into)
                root = into.resolve()
                for info in opened.infolist():
                    mode = (info.external_attr >> 16) & 0o777
                    if not mode or info.is_dir():
                        continue
                    target = (into / info.filename).resolve()
                    if target.is_relative_to(root) and target.is_file():
                        target.chmod(mode)
            return
        if format != "tar" and not label.endswith(ARCHIVES):
            raise UnpackError(
                f"{name or archive.name} is not an archive the store unpacks"
            )
        with tarfile.open(archive) as opened:
            opened.extractall(into, filter="data")
    except (tarfile.TarError, zipfile.BadZipFile, OSError) as error:
        raise UnpackError(f"{name or archive.name} will not extract: {error}") from None
