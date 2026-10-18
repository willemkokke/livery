"""The GitHub backend's arm against the limits GitHub sets on a squash message.

GitHub's ``enablePullRequestAutoMerge`` refuses a commit headline over
320 UTF-8 bytes and a commit body over 65535, with messages that name
a quarter of each ("maximum is 80 characters"). The backend cuts a
long headline at a word boundary and refuses a long body before any
call. Each case is driven through a crafted cassette, so the GraphQL
request the backend sends is pinned byte for byte.
"""

from __future__ import annotations

import json

import pytest

from livery.forge import ForgeError, GithubForge
from livery.forge._github import _ENABLE_AUTO_MERGE, _squash_headline
from livery.forge.testing import Cassette, CassetteError, Exchange, ReplayOpener

_PULL = "https://api.github.com/repos/acme/ws/pulls/5"
_GRAPHQL = "https://api.github.com/graphql"
_NODE = "PR_kwDOprobe"


def _exchange(method: str, url: str, body: str, *, request_body: str = "") -> Exchange:
    return Exchange(
        method=method,
        url=url,
        request_body=request_body,
        status=200,
        reason="OK",
        content_type="application/json",
        response_body=body,
    )


def _arm_exchanges(headline: str, body: str = "") -> list[Exchange]:
    """The node read, the armed read, and the enable mutation with *headline*."""
    pull = json.dumps({"node_id": _NODE, "state": "open", "auto_merge": None})
    variables = {"id": _NODE, "title": headline, "body": body}
    return [
        _exchange("GET", _PULL, pull),
        _exchange("GET", _PULL, pull),
        _exchange(
            "POST",
            _GRAPHQL,
            '{"data": {"enablePullRequestAutoMerge": {"clientMutationId": null}}}',
            request_body=json.dumps(
                {"query": _ENABLE_AUTO_MERGE, "variables": variables}
            ),
        ),
    ]


def _arm(title: str, message: str, exchanges: list[Exchange]) -> ReplayOpener:
    opener = ReplayOpener(Cassette(exchanges=exchanges))
    forge = GithubForge("https://api.github.com", token="t", opener=opener)
    forge.repository("acme", "ws").pr.arm(5, title=title, message=message)
    return opener


def test_a_body_over_the_limit_is_refused_before_any_call() -> None:
    body = "b" * 65536
    with pytest.raises(ForgeError) as refused:
        # An empty cassette raises CassetteError on the first request,
        # so reaching ForgeError proves nothing was sent.
        _arm("feat: probe", body, [])
    said = str(refused.value)
    assert "65535 bytes" in said
    assert "65536" in said


def test_a_body_at_the_limit_is_sent_whole() -> None:
    body = "b" * 65535
    _arm("feat: probe", body, _arm_exchanges("feat: probe", body)).verify_exhausted()


def test_a_long_title_is_cut_at_a_word_boundary_to_the_limit() -> None:
    title = "feat(forge): " + " ".join(f"word{i}" for i in range(100))
    assert len(title.encode()) > 320
    headline = _squash_headline(title)
    assert len(headline.encode()) <= 320
    assert title.startswith(headline)
    assert title[len(headline)] == " ", "the cut falls between two words"
    assert len(headline.encode()) > 320 - len(" word99")
    _arm(title, "", _arm_exchanges(headline)).verify_exhausted()


def test_a_cut_right_before_a_space_keeps_the_last_word() -> None:
    title = "a" * 320 + " tail"
    assert _squash_headline(title) == "a" * 320


def test_a_title_with_no_space_is_cut_at_a_character_boundary() -> None:
    # 200 two-byte characters: 320 bytes keep 160 of them, never half
    # of one.
    assert _squash_headline("é" * 200) == "é" * 160
    assert _squash_headline("\U0001f600" * 81) == "\U0001f600" * 80


def test_leading_space_alone_is_no_word_boundary() -> None:
    title = " " + "a" * 400
    assert _squash_headline(title) == " " + "a" * 319


def test_a_title_within_the_limit_is_the_headline_unchanged() -> None:
    at_limit = "feat: " + "x" * 314
    assert len(at_limit.encode()) == 320
    assert _squash_headline(at_limit) is at_limit
    multibyte = "é" * 160
    assert _squash_headline(multibyte) is multibyte
    _arm(at_limit, "", _arm_exchanges(at_limit)).verify_exhausted()


def test_a_short_title_arms_with_the_same_headline() -> None:
    _arm("feat: arm", "body", _arm_exchanges("feat: arm", "body")).verify_exhausted()


def test_a_drifted_headline_fails_the_replay() -> None:
    with pytest.raises(CassetteError):
        _arm("feat: arm", "", _arm_exchanges("feat: something else"))
