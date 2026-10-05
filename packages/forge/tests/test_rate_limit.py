"""A spent API budget refuses with the time it returns; a response says what is left."""

from __future__ import annotations

import email.message
import email.utils
import io
import time
import urllib.error
import urllib.request
from typing import Any

import pytest

from livery.forge._http import JsonClient
from livery.forge.api import ForgeError, RateBudget, RateLimited


def _headers(values: dict[str, str]) -> email.message.Message:
    headers = email.message.Message()
    for name, value in values.items():
        headers[name] = value
    return headers


class _Refusing:
    """An opener whose server refuses every request with *status* and *headers*."""

    def __init__(self, status: int, headers: dict[str, str]) -> None:
        self.status = status
        self.headers = headers

    def open(self, request: urllib.request.Request, /, *, timeout: float = 30.0) -> Any:
        raise urllib.error.HTTPError(
            request.full_url,
            self.status,
            "Refused",
            _headers(self.headers),
            io.BytesIO(b'{"message": "refused"}'),
        )


class _Answering:
    """An opener whose server answers ``{}`` with *headers*."""

    def __init__(self, headers: dict[str, str] | None) -> None:
        self.headers = headers

    def open(self, request: urllib.request.Request, /, *, timeout: float = 30.0) -> Any:
        headers = self.headers

        class _Response:
            def __init__(self) -> None:
                if headers is not None:
                    self.headers = _headers(headers)

            def read(self) -> bytes:
                return b"{}"

        return _Response()


def _refusal(status: int, headers: dict[str, str]) -> ForgeError:
    client = JsonClient(
        "https://x.invalid/api", headers={}, opener=_Refusing(status, headers)
    )
    with pytest.raises(ForgeError) as refusal:
        client.request("/user")
    return refusal.value


def test_a_403_without_a_spent_budget_is_a_permission_refusal() -> None:
    refusal = _refusal(403, {"X-RateLimit-Remaining": "4999"})
    assert not isinstance(refusal, RateLimited)
    assert refusal.status == 403


def test_a_spent_budget_names_when_it_returns() -> None:
    reset = int(time.time()) + 600
    refusal = _refusal(
        403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset)}
    )
    assert isinstance(refusal, RateLimited)
    assert refusal.reset_at == reset
    clock = time.strftime("%H:%M", time.localtime(reset))
    assert f"it resets at {clock}, in 10 min" in str(refusal)
    assert "GET /user" in str(refusal)


def test_a_429_says_how_long_to_wait_in_seconds_or_as_a_date() -> None:
    before = time.time()
    seconds = _refusal(429, {"Retry-After": "120"})
    assert isinstance(seconds, RateLimited) and seconds.reset_at is not None
    assert before + 119 <= seconds.reset_at <= time.time() + 121
    when = time.time() + 300
    dated = _refusal(429, {"Retry-After": email.utils.formatdate(when, usegmt=True)})
    assert isinstance(dated, RateLimited) and dated.reset_at is not None
    assert abs(dated.reset_at - when) < 2


def test_a_secondary_limit_asks_the_caller_to_come_back() -> None:
    refusal = _refusal(403, {"Retry-After": "60"})
    assert isinstance(refusal, RateLimited)


def test_a_limit_the_forge_does_not_time_says_so() -> None:
    refusal = _refusal(429, {})
    assert isinstance(refusal, RateLimited) and refusal.reset_at is None
    assert "the forge did not say when it resets" in str(refusal)


def test_a_response_reports_what_is_left_of_the_budget() -> None:
    reset = int(time.time()) + 900
    client = JsonClient(
        "https://x.invalid/api",
        headers={},
        opener=_Answering(
            {
                "X-RateLimit-Remaining": "4321",
                "X-RateLimit-Limit": "5000",
                "X-RateLimit-Reset": str(reset),
            }
        ),
    )
    assert client.budget is None
    client.request("/user")
    assert client.budget == RateBudget(remaining=4321, limit=5000, reset_at=reset)
    # A response that reports nothing keeps what the last one said.
    client._opener = _Answering(None)  # pyright: ignore[reportPrivateUsage]
    client.request("/user")
    assert client.budget == RateBudget(remaining=4321, limit=5000, reset_at=reset)


def test_gitlab_names_its_budget_without_the_x_prefix() -> None:
    client = JsonClient(
        "https://x.invalid/api",
        headers={},
        opener=_Answering({"RateLimit-Remaining": "10", "RateLimit-Limit": "600"}),
    )
    client.request("/user")
    assert client.budget == RateBudget(remaining=10, limit=600, reset_at=None)


def test_a_retry_after_that_is_neither_seconds_nor_a_date_falls_to_the_reset() -> None:
    refusal = _refusal(429, {"Retry-After": "soon", "X-RateLimit-Reset": "1234"})
    assert isinstance(refusal, RateLimited) and refusal.reset_at == 1234


def test_every_repository_view_reports_its_clients_budget() -> None:
    from livery.forge.api import GiteaForge, GithubForge, GitlabForge
    from livery.forge.testing import FakeForge

    opener = _Answering(None)
    for forge in (
        GithubForge("https://api.github.com", token="t", opener=opener),
        GiteaForge("http://g.invalid/api/v1", token="t", opener=opener),
        GitlabForge("http://gl.invalid/api/v4", token="t", opener=opener),
    ):
        assert forge.repository("o", "r").rate_budget() is None
    fake = FakeForge()
    fake.create_repo("o", "r", private=True, description="d")
    assert fake.repository("o", "r").rate_budget() is None
    fake.budget = RateBudget(remaining=1, limit=10)
    assert fake.repository("o", "r").rate_budget() == RateBudget(1, 10)
