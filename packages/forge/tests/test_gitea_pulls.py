"""Gitea's pull request lookups against the shapes its API answers with."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from livery.forge._gitea import _GiteaPullRequests


class _Client:
    """A JSON client answering one listing, whatever the page."""

    def __init__(self, listing: list[dict[str, Any]]) -> None:
        self.listing = listing
        self.requested: list[str] = []

    def request(self, url: str, **kwargs: Any) -> Any:
        self.requested.append(url)
        return self.listing if "page=1" in url else []

    def paginate(self, fetch: Any, *, subject: str) -> list[Any]:
        return list(fetch(1))

    def pages(self, fetch: Any, *, subject: str) -> Iterator[Any]:
        yield from fetch(1)


def _pull(number: int, ref: str, label: str, *, merged: bool) -> dict[str, Any]:
    return {
        "number": number,
        "title": f"pull {number}",
        "state": "closed" if merged else "open",
        "merged": merged,
        "head": {"ref": ref, "label": label, "sha": f"{number:040x}"},
        "base": {"ref": "main"},
        "html_url": f"http://gitea/o/r/pulls/{number}",
        "user": {"login": "someone"},
    }


def test_a_merged_pull_request_is_found_by_the_branch_it_came_from() -> None:
    """Gitea rewrites a merged pull's head ref once its branch is gone.

    The label keeps the branch name, and the lookup reads it too.
    """
    branch = "workflow/release/loop-cpp+loop-echo+loop-native"
    client = _Client(
        [
            _pull(12, "chore/tests-leg", "chore/tests-leg", merged=True),
            _pull(13, "refs/pull/13/head", branch, merged=True),
        ]
    )
    pulls = _GiteaPullRequests(client, "/repos/o/r")  # type: ignore[arg-type]
    found = pulls.find_by_head(branch, state="all")
    assert found is not None
    assert found.number == 13 and found.merged
    # A branch still open is found by its ref, as before.
    open_client = _Client([_pull(14, "feat/x", "feat/x", merged=False)])
    pulls = _GiteaPullRequests(open_client, "/repos/o/r")  # type: ignore[arg-type]
    found = pulls.find_by_head("feat/x")
    assert found is not None and found.number == 14
    assert pulls.find_by_head("feat/other") is None
