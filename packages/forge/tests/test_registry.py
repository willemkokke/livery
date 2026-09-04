"""livery.forge.SimpleRegistry against JSON and HTML simple indexes."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from email.message import Message
from typing import Any

import pytest

from livery.forge import ForgeError, SimpleRegistry


def _anchor(filename: str) -> str:
    return f'<a href="/x/{filename}#sha256=aa">{filename}</a><br/>'


_HTML_PAGE = "\n".join(
    [
        "<!DOCTYPE html>",
        "<html>",
        "  <head><title>Links for livery-forge</title></head>",
        "  <body><h1>Links for livery-forge</h1>",
        _anchor("livery_forge-0.1.0-py3-none-any.whl"),
        _anchor("livery_forge-0.1.0.tar.gz"),
        _anchor("livery_forge-0.2.0-py3-none-any.whl"),
        _anchor("unrelated-9.9.9.tar.gz"),
        "  </body>",
        "</html>",
    ]
)


class _Answer(io.BytesIO):
    def __init__(self, body: bytes) -> None:
        super().__init__(body)
        self.headers = Message()

    def __enter__(self) -> _Answer:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _serve(
    monkeypatch: pytest.MonkeyPatch, body: bytes
) -> list[urllib.request.Request]:
    requests: list[urllib.request.Request] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float = 0) -> _Answer:
        requests.append(request)
        return _Answer(body)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return requests


# The fallback first: an HTML-only index (Gitea's registry) answers
# PEP 503 anchors, and the probe must read the versions from them.


def test_an_html_only_index_answers_through_its_anchors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _serve(monkeypatch, _HTML_PAGE.encode())
    registry = SimpleRegistry("http://forge.example/api/packages/o/pypi/simple")
    assert registry.versions("livery-forge") == ("0.1.0", "0.2.0")


def test_an_html_page_without_this_name_answers_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _serve(monkeypatch, _HTML_PAGE.encode())
    registry = SimpleRegistry("http://forge.example/simple")
    assert registry.versions("other-name") == ()


def test_an_answer_in_neither_format_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _serve(monkeypatch, b"stream timeout")
    registry = SimpleRegistry("http://forge.example/simple")
    with pytest.raises(ForgeError, match="neither PEP 691 JSON nor PEP 503 HTML"):
        registry.versions("livery-forge")


def test_a_missing_name_is_the_empty_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def gone(request: urllib.request.Request, timeout: float = 0) -> Any:
        raise urllib.error.HTTPError(
            request.full_url, 404, "not found", Message(), None
        )

    monkeypatch.setattr(urllib.request, "urlopen", gone)
    registry = SimpleRegistry("http://forge.example/simple")
    assert registry.versions("never-published") == ()


def test_a_json_index_answers_its_versions_and_gets_asked_for_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = json.dumps({"versions": ["0.1.0", "0.2.0"]}).encode()
    requests = _serve(monkeypatch, body)
    registry = SimpleRegistry("https://pypi.org/simple", token="t")
    assert registry.versions("Livery_Forge") == ("0.1.0", "0.2.0")
    accept = requests[0].get_header("Accept", "")
    assert "application/vnd.pypi.simple.v1+json" in accept
    assert requests[0].full_url.endswith("/livery-forge/")
