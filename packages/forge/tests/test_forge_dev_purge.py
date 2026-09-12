"""The dev forge's registry purge: refusals first, then the pages walked."""

from __future__ import annotations

import pytest

from livery.forge._registry import purge_packages


def _api(pages: list[list[dict[str, str]]], deletes: list[str], refuse: int = 204):
    calls = {"page": 0}

    def call(method: str, path: str, token: str) -> tuple[int, object]:
        assert token == "t"
        if method == "GET":
            page = pages[calls["page"]] if calls["page"] < len(pages) else []
            calls["page"] += 1
            return 200, page
        deletes.append(path)
        return refuse, None

    return call


def test_a_refused_delete_is_red_naming_the_release() -> None:
    deletes: list[str] = []
    api = _api([[{"name": "loop-echo", "version": "0.1.0"}]], deletes, refuse=500)
    with pytest.raises(
        SystemExit, match=r"refused to delete loop-echo==0\.1\.0: HTTP 500"
    ):
        purge_packages("http://x", "livery", token="t", api=api)


def test_an_owner_without_packages_purges_nothing() -> None:
    deletes: list[str] = []
    assert (
        purge_packages("http://x", "livery", token="t", api=_api([[]], deletes)) == []
    )
    assert (
        purge_packages("http://x", "livery", token="t", api=lambda m, p, t: (404, None))
        == []
    )
    assert deletes == []


def test_named_packages_alone_are_purged() -> None:
    deletes: list[str] = []
    page = [
        {"name": "livery-forge", "version": "0.3.0.dev4"},
        {"name": "livery-workshop", "version": "0.2.0.dev9"},
        {"name": "livery-forge", "version": "0.3.0.dev5"},
    ]
    purged = purge_packages(
        "http://x",
        "livery",
        token="t",
        api=_api([page], deletes),
        names={"livery-forge"},
    )
    assert purged == ["livery-forge==0.3.0.dev4", "livery-forge==0.3.0.dev5"]
    assert all("livery-workshop" not in path for path in deletes)


def test_every_page_is_walked_and_a_gone_release_is_fine() -> None:
    deletes: list[str] = []
    first = [{"name": f"pkg{n}", "version": "0.1.0"} for n in range(50)]
    second = [{"name": "last", "version": "0.2.0"}, {"name": "", "version": "1"}]
    purged = purge_packages(
        "http://x", "livery", token="t", api=_api([first, second], deletes, 404)
    )
    assert len(purged) == 51 and purged[-1] == "last==0.2.0"
    assert deletes[0] == "/packages/livery/pypi/pkg0/0.1.0"
    assert deletes[-1] == "/packages/livery/pypi/last/0.2.0"
