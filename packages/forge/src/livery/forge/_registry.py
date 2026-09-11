"""The package-index reader: livery.forge.Registry over the simple API.

One backend for every simple-API index, PyPI and the forges' own
registries alike: the simple API is the one interface they share,
and "which versions of this name are published" needs nothing more.
An index that answers PEP 691 JSON is read as JSON; one that answers
only PEP 503 HTML (Gitea's registry does) is read from its anchors'
filenames. The release train's receipt probe reads through this, so
the answer must be the index's own, never a cache's.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from html.parser import HTMLParser

from livery.forge._errors import ForgeError

_SDIST_SUFFIXES = (".tar.gz", ".zip")


class _Anchors(HTMLParser):
    """The anchor texts of a PEP 503 project page, in page order."""

    def __init__(self) -> None:
        super().__init__()
        self.texts: list[str] = []
        self._inside = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._inside = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._inside = False

    def handle_data(self, data: str) -> None:
        if self._inside and data.strip():
            self.texts.append(data.strip())


def _versions_from_html(page: str, canonical: str) -> tuple[str, ...]:
    """The versions a PEP 503 page's file anchors carry, page order.

    Wheel and sdist filenames both start ``<name>-<version>`` with the
    name's runs of ``-``, ``_`` and ``.`` normalised to ``_``, so the
    version is the segment after the name, up to the wheel's first tag.
    """
    parser = _Anchors()
    parser.feed(page)
    prefix = canonical.replace("-", "_") + "-"
    ordered: dict[str, None] = {}
    for filename in parser.texts:
        stem = filename
        if stem.endswith(".whl"):
            stem = stem[: -len(".whl")]
        else:
            for suffix in _SDIST_SUFFIXES:
                if stem.endswith(suffix):
                    stem = stem[: -len(suffix)]
                    break
            else:
                continue
        if not stem.lower().startswith(prefix):
            continue
        version = stem[len(prefix) :].split("-")[0]
        if version:
            ordered.setdefault(version)
    return tuple(ordered)


class SimpleRegistry:
    """A simple-API index, addressed by its simple root.

    Args:
        base: The index's simple root (``https://pypi.org/simple``,
            or a forge registry's equivalent), with or without a
            trailing slash.
        token: Sent as HTTP basic auth when the index needs it;
            empty reads anonymously. Basic covers the union: a
            forge registry takes its account name and token, and an
            API-token index takes ``__token__`` as the user.
        username: The basic-auth user beside *token*; ``__token__``
            when empty, which is what an API-token index expects.
            Meaningless without a token.
    """

    def __init__(self, base: str, *, token: str = "", username: str = "") -> None:
        self._base = base.rstrip("/")
        self._token = token
        self._username = username

    def versions(self, name: str) -> tuple[str, ...]:
        """The published versions of *name*, oldest first.

        An unpublished name answers the empty tuple (the index's 404
        is that answer, not an error); an unreachable index raises
        livery.forge.ForgeError with the reason, and so does an index
        whose answer is neither PEP 691 JSON nor PEP 503 HTML.
        """
        canonical = name.replace("_", "-").lower()
        if self._token:
            pair = f"{self._username or '__token__'}:{self._token}".encode()
            authorization = {
                "Authorization": f"Basic {base64.b64encode(pair).decode()}"
            }
        else:
            authorization = {}
        request = urllib.request.Request(
            f"{self._base}/{canonical}/",
            headers={
                # HTML is the fallback, declared so an index honouring
                # content negotiation still answers JSON first.
                "Accept": "application/vnd.pypi.simple.v1+json, text/html;q=0.1",
                **authorization,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as answer:
                body = answer.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return ()
            raise ForgeError(
                f"the index refused {canonical}: HTTP {exc.code}",
                status=exc.code,
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ForgeError(
                f"the index at {self._base} is unreachable: {exc}"
            ) from exc
        text = body.decode("utf-8", errors="replace")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            found = _versions_from_html(text, canonical)
            if found or "<a" in text.lower() or "<html" in text.lower():
                return found
            raise ForgeError(
                f"the index at {self._base} answered neither PEP 691"
                f" JSON nor PEP 503 HTML for {canonical}"
            ) from None
        return tuple(str(v) for v in payload.get("versions") or [])


def purge_packages(
    base: str,
    owner: str,
    *,
    token: str,
    kind: str = "pypi",
    api: Callable[[str, str, str], tuple[int, object]] | None = None,
) -> list[str]:
    """Delete every *kind* package version *owner* holds on the Gitea at *base*.

    The workshop's CI loop publishes rehearsal releases into its dev
    forge's registry, and a rebirth of the loop must publish the same
    versions again, which the registry refuses while they exist.
    Returns ``name==version`` for each deleted release. *api* is the
    call seam, ``(method, path, token)`` to ``(status, body)``, with
    *path* below ``/api/v1``; the default speaks to *base*.
    """
    call = api or (lambda method, path, token: _api_json(base, method, path, token))
    purged: list[str] = []
    while True:
        status, body = call("GET", f"/packages/{owner}?type={kind}&limit=50", token)
        if status == 404 or not isinstance(body, list) or not body:
            return purged
        for item in body:
            if not isinstance(item, dict):
                continue
            name, version = str(item.get("name", "")), str(item.get("version", ""))
            if not name or not version:
                continue
            gone, _ = call(
                "DELETE", f"/packages/{owner}/{kind}/{name}/{version}", token
            )
            if gone not in (204, 404):
                raise SystemExit(
                    f"the registry refused to delete {name}=={version}: HTTP {gone}"
                )
            purged.append(f"{name}=={version}")
        if len(body) < 50:
            return purged


def _api_json(base: str, method: str, path: str, token: str) -> tuple[int, object]:
    """One Gitea API call with a JSON answer; the status and the decoded body."""
    import json
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        f"{base.rstrip('/')}/api/v1{path}",
        method=method,
        headers={"Authorization": f"token {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read()
            return int(response.status), (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except urllib.error.URLError:
        return 0, None
