"""livery.forge.SimpleRegistry against JSON and HTML simple indexes."""

from __future__ import annotations

import base64
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


# The credential paths, refusals and header shape before the happy
# path: an authenticated index (a forge's own registry) is the case
# that forces them.


def test_a_refused_credential_names_the_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refused(request: urllib.request.Request, timeout: float = 0) -> Any:
        raise urllib.error.HTTPError(
            request.full_url, 401, "unauthorized", Message(), None
        )

    monkeypatch.setattr(urllib.request, "urlopen", refused)
    registry = SimpleRegistry("http://forge.example/simple", token="wrong")
    with pytest.raises(ForgeError, match="HTTP 401"):
        registry.versions("livery-forge")


def test_an_anonymous_read_sends_no_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = _serve(monkeypatch, json.dumps({"versions": []}).encode())
    SimpleRegistry("http://forge.example/simple").versions("livery-forge")
    assert requests[0].get_header("Authorization") is None


def test_the_token_rides_as_basic_auth_with_the_token_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = _serve(monkeypatch, json.dumps({"versions": []}).encode())
    SimpleRegistry("http://forge.example/simple", token="t").versions("livery-forge")
    credential = base64.b64encode(b"__token__:t").decode()
    assert requests[0].get_header("Authorization") == f"Basic {credential}"


def test_a_named_user_rides_in_the_basic_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = _serve(monkeypatch, json.dumps({"versions": []}).encode())
    SimpleRegistry(
        "http://forge.example/api/packages/o/pypi/simple",
        token="t",
        username="livery-admin",
    ).versions("livery-forge")
    credential = base64.b64encode(b"livery-admin:t").decode()
    assert requests[0].get_header("Authorization") == f"Basic {credential}"


def test_the_gitlab_purge_lists_by_project_and_deletes_by_id() -> None:
    from livery.forge._registry import purge_gitlab_packages

    calls: list[tuple[str, str]] = []

    def api(method: str, url: str, token: str) -> tuple[int, object]:
        calls.append((method, url))
        if method == "GET":
            return 200, [
                {"id": 1, "name": "acme-echo", "version": "0.1.0"},
                {"id": 2, "name": "acme_native", "version": "0.1.0"},
            ]
        return 204, None

    # Names narrow the purge, compared as the index compares them.
    deleted = purge_gitlab_packages(
        "http://gitlab", "livery/ci-e2e-loop", token="t", names=["acme-native"], api=api
    )
    assert deleted == ["acme_native==0.1.0"]
    assert calls[0][0] == "GET"
    assert "/projects/livery%2Fci-e2e-loop/packages?package_type=pypi" in calls[0][1]
    assert calls[1] == (
        "DELETE",
        "http://gitlab/api/v4/projects/livery%2Fci-e2e-loop/packages/2",
    )
    # Every package without names; a listing the forge refuses deletes nothing.
    calls.clear()
    assert purge_gitlab_packages(
        "http://gitlab", "livery/ci-e2e-loop", token="t", api=api
    ) == [
        "acme-echo==0.1.0",
        "acme_native==0.1.0",
    ]
    assert (
        purge_gitlab_packages(
            "http://gitlab", "p", token="t", api=lambda m, u, t: (403, "no")
        )
        == []
    )
