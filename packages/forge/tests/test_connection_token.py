"""The connection's credential is the protocol's to answer."""

from __future__ import annotations

from livery.forge import GiteaForge, GithubForge, GitlabForge
from livery.forge.testing import FakeForge


def test_an_anonymous_connection_answers_empty() -> None:
    # The fallback first: anonymous means empty, never a placeholder.
    assert FakeForge().token == ""
    assert GiteaForge("http://forge.example/api/v1", token="").token == ""


def test_every_backend_surfaces_the_resolved_credential() -> None:
    assert GiteaForge("http://forge.example/api/v1", token="t1").token == "t1"
    assert GitlabForge("http://forge.example/api/v4", token="t2").token == "t2"
    assert GithubForge("https://api.github.example", token="t3").token == "t3"
    assert FakeForge(token="t4").token == "t4"
