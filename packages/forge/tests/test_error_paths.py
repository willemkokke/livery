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


class _Dropping:
    """An opener whose server closes the connection without a response."""

    def open(self, request: urllib.request.Request, /, *, timeout: float = 30.0) -> Any:
        import http.client

        raise http.client.RemoteDisconnected(
            "Remote end closed connection without response"
        )


class _DroppingMidRead:
    """An opener whose response drops while its body is read."""

    def open(self, request: urllib.request.Request, /, *, timeout: float = 30.0) -> Any:
        class _Response:
            def read(self) -> bytes:
                raise ConnectionResetError(54, "Connection reset by peer")

        return _Response()


def test_a_dropped_connection_raises_with_no_status() -> None:
    # The transport's own exceptions (http.client's, the socket's) are
    # not urllib's URLError; each is still "the server did not answer".
    for opener in (_Dropping(), _DroppingMidRead()):
        client = JsonClient("https://x.invalid/api", headers={}, opener=opener)
        with pytest.raises(ForgeError) as refusal:
            client.request("/user")
        assert refusal.value.status is None
        assert "unreachable" in str(refusal.value)
        assert "GET /user" in str(refusal.value)


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


def test_gitlab_token_resolution_walks_its_ladder_and_gitea_has_no_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The three refusals share one shape: no <forge> credential, then the ways in."""
    from livery.forge import GiteaForge, GitlabForge

    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    asked: list[list[str]] = []

    def minted(
        args: Any, *rest: Any, **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        asked.append(list(args))
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="glab-tok\n")

    monkeypatch.setattr(subprocess, "run", minted)
    forge = GitlabForge.connect(url="https://gitlab.example.com:8443/")
    assert forge.token == "glab-tok"
    assert asked == [
        ["glab", "config", "get", "token", "--host", "gitlab.example.com:8443"]
    ]

    def refused(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=[], returncode=1, stdout="")

    monkeypatch.setattr(subprocess, "run", refused)
    with pytest.raises(
        ForgeError,
        match=r"^no GitLab credential: set GITLAB_TOKEN or sign in with `glab auth"
        r" login`$",
    ):
        GitlabForge.connect(url="https://gitlab.com")

    def absent(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise OSError("no glab on PATH")

    monkeypatch.setattr(subprocess, "run", absent)
    with pytest.raises(ForgeError, match="GITLAB_TOKEN"):
        GitlabForge.connect(url="https://gitlab.com")
    monkeypatch.setenv("GITLAB_TOKEN", "from-env")
    assert GitlabForge.connect(url="https://gitlab.com").token == "from-env"
    # A GitHub refusal reads the same way.
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(
        ForgeError,
        match=r"^no GitHub credential: set GITHUB_TOKEN or sign in with `gh auth"
        r" login`$",
    ):
        GithubForge.connect()
    # Gitea: no CLI hands out a token, so the variable is the whole ladder.
    monkeypatch.delenv("GITEA_TOKEN", raising=False)
    with pytest.raises(ForgeError, match=r"^no Gitea credential: set GITEA_TOKEN$"):
        GiteaForge.connect(url="https://gitea.example.com")


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
    with pytest.raises(ForgeError, match="no pull request"):
        here.pr.update_body(1, "b")
    with pytest.raises(ForgeError, match="no issue"):
        here.issue.comment(1, "b")


# --- a created project's default branch, and the published merge hold ---------

_GL = "http://gl.invalid/api/v4"


def _gl_exchange(method: str, url: str, status: int, body: str) -> Exchange:
    from livery.forge.testing._cassette import VOLATILE

    exchange = _exchange(method, url, status, body)
    if method in ("POST", "PUT"):
        return Exchange(**{**exchange.__dict__, "request_body": VOLATILE})
    return exchange


def test_gitlab_create_under_the_users_namespace_waits_for_the_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The fallback first: a personal namespace follows the instance
    # default, which the token cannot read (403), so the create waits
    # for the rule; it arrives on the second look and goes.
    import time

    import livery.forge._gitlab as gitlab

    naps: list[float] = []
    monkeypatch.setattr(time, "sleep", naps.append)
    rules = f"{_GL}/projects/me%2Frepo/protected_branches"
    cassette = Cassette(
        [
            _gl_exchange("GET", f"{_GL}/user", 200, '{"username": "me"}'),
            _gl_exchange("POST", f"{_GL}/projects", 201, '{"default_branch": "main"}'),
            _gl_exchange(
                "GET", f"{_GL}/application/settings", 403, '{"message": "403"}'
            ),
            _gl_exchange("GET", rules, 200, "[]"),
            _gl_exchange("GET", rules, 200, '[{"name": "main"}]'),
            _gl_exchange("DELETE", f"{rules}/main", 204, ""),
        ]
    )
    opener = ReplayOpener(cassette)
    forge = GitlabForge(_GL, token="t", opener=opener)
    forge.create_repo("me", "repo")
    opener.verify_exhausted()
    assert naps == [gitlab._NEW_PROTECTION_POLL]


def test_gitlab_create_in_a_group_reads_its_default_before_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A group whose default is no protection (0) never gets a rule, so
    # nothing is polled; a group the token cannot read is taken as
    # protected; and a budget that runs out leaves the branch as it is.
    import time

    import livery.forge._gitlab as gitlab

    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    rules = f"{_GL}/projects/acme%2Frepo/protected_branches"

    def head(default: tuple[int, str]) -> list[Exchange]:
        return [
            _gl_exchange("GET", f"{_GL}/user", 200, '{"username": "me"}'),
            _gl_exchange(
                "GET",
                f"{_GL}/namespaces?search=acme",
                200,
                '[{"full_path": "acme", "id": 3}]',
            ),
            _gl_exchange("POST", f"{_GL}/projects", 201, '{"default_branch": "main"}'),
            _gl_exchange("GET", f"{_GL}/groups/acme", *default),
        ]

    open_default = ReplayOpener(
        Cassette(head((200, '{"default_branch_protection": 0}')))
    )
    GitlabForge(_GL, token="t", opener=open_default).create_repo("acme", "repo")
    open_default.verify_exhausted()

    unreadable = ReplayOpener(
        Cassette(
            [
                *head((404, '{"message": "404"}')),
                _gl_exchange("GET", rules, 200, '[{"name": "main"}]'),
                _gl_exchange("DELETE", f"{rules}/main", 204, ""),
            ]
        )
    )
    GitlabForge(_GL, token="t", opener=unreadable).create_repo("acme", "repo")
    unreadable.verify_exhausted()

    monkeypatch.setattr(gitlab, "_NEW_PROTECTION_WAIT", 0.0)
    budget = ReplayOpener(
        Cassette(
            [
                *head((200, '{"default_branch_protection": 2}')),
                _gl_exchange("GET", rules, 200, "[]"),
            ]
        )
    )
    GitlabForge(_GL, token="t", opener=budget).create_repo("acme", "repo")
    budget.verify_exhausted()


def test_merge_hold_names_a_missing_pull_request_and_reads_the_published_state() -> (
    None
):
    # The refusal first on every backend: a pull request that does not
    # exist raises with 404; then the published field, verbatim.
    gitlab_mr = f"{_GL}/projects/acme%2Fws/merge_requests/5"
    gitlab = GitlabForge(
        _GL,
        token="t",
        opener=ReplayOpener(
            Cassette(
                [
                    _exchange("GET", gitlab_mr, 404, '{"message": "404"}'),
                    _exchange(
                        "GET", gitlab_mr, 200, '{"detailed_merge_status": "checking"}'
                    ),
                ]
            )
        ),
    )
    with pytest.raises(ForgeError, match="no merge request 5") as caught:
        gitlab.repository("acme", "ws").pr.merge_hold(5)
    assert caught.value.status == 404
    assert gitlab.repository("acme", "ws").pr.merge_hold(5) == "checking"

    github_pr = "https://api.github.com/repos/acme/ws/pulls/5"
    github = GithubForge(
        "https://api.github.com",
        token="t",
        opener=ReplayOpener(
            Cassette(
                [
                    _exchange("GET", github_pr, 404, '{"message": "Not Found"}'),
                    _exchange("GET", github_pr, 200, '{"mergeable_state": "blocked"}'),
                ]
            )
        ),
    )
    with pytest.raises(ForgeError, match="no pull request 5"):
        github.repository("acme", "ws").pr.merge_hold(5)
    assert github.repository("acme", "ws").pr.merge_hold(5) == "blocked"

    gitea = GiteaForge(
        "http://g.invalid/api/v1",
        token="t",
        opener=ReplayOpener(
            Cassette(
                [
                    _exchange(
                        "GET", "http://g.invalid/api/v1/repos/acme/ws/pulls/5", 404, ""
                    )
                ]
            )
        ),
    )
    with pytest.raises(ForgeError, match="no pull request 5"):
        gitea.repository("acme", "ws").pr.merge_hold(5)


def test_a_locked_merge_request_is_still_open() -> None:
    # GitLab locks a merge request for the beat its merge runs; a
    # follower reading that beat as closed would stop before the
    # merge lands. Locked reads open and not merged; merged and
    # closed read as they are.
    mr = f"{_GL}/projects/acme%2Fws/merge_requests/5"
    body = '{"iid": 5, "title": "t", "state": "%s", "sha": "a", "source_branch": "f"}'
    forge = GitlabForge(
        _GL,
        token="t",
        opener=ReplayOpener(
            Cassette(
                [
                    _exchange("GET", mr, 200, body % "locked"),
                    _exchange("GET", mr, 200, body % "merged"),
                    _exchange("GET", mr, 200, body % "closed"),
                ]
            )
        ),
    )
    pulls = forge.repository("acme", "ws").pr
    locked = pulls.get(5)
    assert locked is not None and locked.state == "open" and not locked.merged
    merged = pulls.get(5)
    assert merged is not None and merged.state == "closed" and merged.merged
    closed = pulls.get(5)
    assert closed is not None and closed.state == "closed" and not closed.merged
