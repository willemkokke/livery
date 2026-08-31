"""The error arms a green conformance run never reaches.

Refusals at construction, floors, unreachable servers, and the
subjects that do not exist: each driven deterministically, most
through crafted cassettes so the fixture layer serves as its own
test double.
"""

from __future__ import annotations

import re
import subprocess
import urllib.error
import urllib.request
from typing import Any

import pytest

from livery.forge import (
    ForgeError,
    GiteaForge,
    GithubForge,
    GitlabForge,
    Unsupported,
)
from livery.forge._http import PAGE_CAP, PAGE_SIZE, JsonClient, _RefuseRedirect
from livery.forge.testing import Cassette, CassetteError, Exchange, ReplayOpener


def _exchange(method: str, url: str, status: int, body: str) -> Exchange:
    return Exchange(
        method=method,
        url=url,
        request_body="",
        status=status,
        reason="",
        content_type="application/json",
        response_body=body,
    )


class _Unreachable:
    """An opener whose server never answers."""

    def open(self, request: urllib.request.Request, /, *, timeout: float = 30.0) -> Any:
        raise urllib.error.URLError("nobody home")


class _Redirecting:
    """An opener that answers every request with a 302 elsewhere."""

    def open(self, request: urllib.request.Request, /, *, timeout: float = 30.0) -> Any:
        import email.message

        headers = email.message.Message()
        headers["Location"] = "https://elsewhere.example"
        raise urllib.error.HTTPError(request.full_url, 302, "Found", headers, None)


def test_an_unreachable_server_raises_with_no_status() -> None:
    client = JsonClient("https://x.invalid/api", headers={}, opener=_Unreachable())
    with pytest.raises(ForgeError) as refusal:
        client.request("/user")
    assert refusal.value.status is None
    assert "unreachable" in str(refusal.value)


def test_a_redirect_is_refused_naming_the_location() -> None:
    client = JsonClient("https://x.invalid/api", headers={}, opener=_Redirecting())
    with pytest.raises(ForgeError) as refusal:
        client.request("/user")
    assert refusal.value.status == 302
    assert "elsewhere.example" in refusal.value.detail


def test_the_refusing_handler_never_follows() -> None:
    request = urllib.request.Request("https://x.invalid")
    assert (
        _RefuseRedirect().redirect_request(request, None, 302, "Found", None, "u")  # type: ignore[arg-type]
        is None
    )


def test_the_page_cap_raises_rather_than_truncating() -> None:
    client = JsonClient("https://x.invalid/api", headers={})
    with pytest.raises(ForgeError, match="truncated"):
        client.paginate(lambda page: [0] * PAGE_SIZE, subject="an endless listing")
    assert PAGE_CAP * PAGE_SIZE  # the cap is a positive bound


def test_a_streaming_request_body_is_refused() -> None:
    replay = ReplayOpener(
        Cassette([_exchange("POST", "https://x.invalid/a", 200, "{}")])
    )
    request = urllib.request.Request(
        "https://x.invalid/a", data=iter([b"chunk"]), method="POST"
    )
    with pytest.raises(CassetteError, match="streaming"):
        replay.open(request)


def test_connect_refusals_name_the_missing_piece(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for var in ("GITEA_URL", "GITEA_TOKEN", "GITLAB_URL", "GITLAB_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ForgeError, match="GITEA_URL"):
        GiteaForge.connect()
    with pytest.raises(ForgeError, match="GITEA_TOKEN"):
        GiteaForge.connect(url="https://gitea.example")
    with pytest.raises(ForgeError, match="GITLAB_URL"):
        GitlabForge.connect()
    with pytest.raises(ForgeError, match="GITLAB_TOKEN"):
        GitlabForge.connect(url="https://gitlab.example")


def test_github_token_resolution_walks_its_ladder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    def minted(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="gh-tok\n")

    monkeypatch.setattr(subprocess, "run", minted)
    assert GithubForge.connect() is not None  # the gh fallback answered

    def refused(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=[], returncode=1, stdout="")

    monkeypatch.setattr(subprocess, "run", refused)
    with pytest.raises(ForgeError, match="gh auth login"):
        GithubForge.connect()

    def absent(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise OSError("no gh on PATH")

    monkeypatch.setattr(subprocess, "run", absent)
    with pytest.raises(ForgeError, match="GITHUB_TOKEN"):
        GithubForge.connect()


def test_an_enterprise_url_gets_its_api_root() -> None:
    forge = GithubForge.connect(url="https://ghe.example", token="t")
    assert forge._client.api_base == "https://ghe.example/api/v3"


def test_gitea_cancel_below_the_floor_names_the_version() -> None:
    cassette = Cassette(
        [
            _exchange(
                "GET", "http://g.invalid/api/v1/version", 200, '{"version": "1.27.4"}'
            )
        ]
    )
    forge = GiteaForge(
        "http://g.invalid/api/v1", token="t", opener=ReplayOpener(cassette)
    )
    with pytest.raises(Unsupported, match=re.escape("1.27.4")):
        forge.repository("o", "r").checks.cancel_run(1)


def test_gitea_refuses_creating_for_a_foreign_user() -> None:
    cassette = Cassette(
        [
            _exchange("GET", "http://g.invalid/api/v1/orgs/somebody", 404, ""),
            _exchange("GET", "http://g.invalid/api/v1/user", 200, '{"login": "me"}'),
        ]
    )
    forge = GiteaForge(
        "http://g.invalid/api/v1", token="t", opener=ReplayOpener(cassette)
    )
    with pytest.raises(ForgeError, match="neither an"):
        forge.create_repo("somebody", "repo")


def test_gitlab_refuses_an_unknown_namespace_and_user() -> None:
    cassette = Cassette(
        [
            _exchange(
                "GET", "http://gl.invalid/api/v4/user", 200, '{"username": "me"}'
            ),
            _exchange(
                "GET", "http://gl.invalid/api/v4/namespaces?search=ghost", 200, "[]"
            ),
            _exchange(
                "GET", "http://gl.invalid/api/v4/users?username=ghost", 200, "[]"
            ),
        ]
    )
    forge = GitlabForge(
        "http://gl.invalid/api/v4", token="t", opener=ReplayOpener(cassette)
    )
    with pytest.raises(ForgeError, match="no namespace"):
        forge.create_repo("ghost", "repo")
    with pytest.raises(ForgeError, match="no user named ghost"):
        forge.repository("g", "r").issue.create("t", assignee="ghost")


def test_the_fake_names_every_missing_subject() -> None:
    from livery.forge.testing import FakeForge

    fake = FakeForge()
    repo = fake.repository("no", "where")
    with pytest.raises(ForgeError, match="no repository"):
        repo.tags()
    with pytest.raises(ForgeError, match="no repository"):
        fake.push("no", "where", "b")
    fake.create_repo("acme", "here")
    here = fake.repository("acme", "here")
    with pytest.raises(ForgeError, match="no run"):
        here.checks.jobs(99)
    with pytest.raises(ForgeError, match="no job"):
        here.checks.job_log(99)
    with pytest.raises(ForgeError, match="no pull request"):
        here.pr.update_title(1, "t")
    with pytest.raises(ForgeError, match="no issue"):
        here.issue.comment(1, "b")
