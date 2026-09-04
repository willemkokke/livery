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

import json
import urllib.error
import urllib.request
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
        livery.forge.ForgeError with the reason, and so does an index
        whose answer is neither PEP 691 JSON nor PEP 503 HTML.
        """
        canonical = name.replace("_", "-").lower()
        request = urllib.request.Request(
            f"{self._base}/{canonical}/",
            headers={
                # HTML is the fallback, declared so an index honouring
                # content negotiation still answers JSON first.
                "Accept": "application/vnd.pypi.simple.v1+json, text/html;q=0.1",
                **({"Authorization": f"Bearer {self._token}"} if self._token else {}),
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
