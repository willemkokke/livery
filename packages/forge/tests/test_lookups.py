"""Each backend asks its forge the narrowest query a lookup has.

Crafted recordings stand in for the servers. A request a lookup was not
meant to make meets a mismatched or an exhausted recording and raises,
so each test pins the requests the lookup costs.
"""

from __future__ import annotations

import json

from livery.forge import GiteaForge, GithubForge, GitlabForge
from livery.forge._http import PAGE_SIZE
from livery.forge.testing import Cassette, Exchange, ReplayOpener

GITHUB = "https://api.github.com"
GH = f"{GITHUB}/repos/o/r"
GITEA = "http://g.invalid/api/v1"
GT = f"{GITEA}/repos/o/r"
GITLAB = "http://gl.invalid/api/v4"
GL = f"{GITLAB}/projects/o%2Fr"
SHA = "a" * 40


def _get(url: str, body: object, status: int = 200) -> Exchange:
    return Exchange(
        method="GET",
        url=url,
        request_body="",
        status=status,
        reason="",
        content_type="application/json",
        response_body=body if isinstance(body, str) else json.dumps(body),
    )


def _replay(*exchanges: Exchange) -> ReplayOpener:
    return ReplayOpener(Cassette(list(exchanges)))


def _github(opener: ReplayOpener) -> GithubForge:
    return GithubForge(GITHUB, token="t", opener=opener)


def _gitea(opener: ReplayOpener) -> GiteaForge:
    return GiteaForge(GITEA, token="t", opener=opener)


def _gitlab(opener: ReplayOpener) -> GitlabForge:
    return GitlabForge(GITLAB, token="t", opener=opener)


def _pulls(*heads: str, first: int = 1) -> list[dict[str, object]]:
    """A page of a pull request listing, one entry per head branch."""
    return [
        {"number": first + offset, "state": "open", "head": {"ref": head}}
        for offset, head in enumerate(heads)
    ]


def _github_run(run_id: int) -> dict[str, object]:
    return {
        "id": run_id,
        "path": ".github/workflows/release.yml",
        "status": "completed",
        "conclusion": "success",
    }


# --- a pull request by its head branch ------------------------------------


def test_github_finds_by_head_through_a_server_that_ignores_the_filter() -> None:
    # The fallback first: the server answers the head filter with every
    # pull request, a full page of others and the match on the second.
    others = [f"other-{n}" for n in range(PAGE_SIZE)]
    opener = _replay(
        _get(
            f"{GH}/pulls?state=all&head=o:feat/x&page=1&per_page=50",
            _pulls(*others, first=100),
        ),
        _get(
            f"{GH}/pulls?state=all&head=o:feat/x&page=2&per_page=50",
            _pulls("feat/x", "feat/y", first=7),
        ),
        _get(
            f"{GH}/pulls/7", {"number": 7, "state": "open", "head": {"ref": "feat/x"}}
        ),
    )
    found = _github(opener).repository("o", "r").pr.find_by_head("feat/x", state="all")
    assert found is not None and found.number == 7
    opener.verify_exhausted()


def test_github_asks_for_a_head_once() -> None:
    # A branch with no pull request costs one request, and a match costs
    # the listing and its own read, never a second page.
    absent = _replay(
        _get(f"{GH}/pulls?state=all&head=o:feat/none&page=1&per_page=50", [])
    )
    assert (
        _github(absent).repository("o", "r").pr.find_by_head("feat/none", state="all")
        is None
    )
    absent.verify_exhausted()
    present = _replay(
        _get(
            f"{GH}/pulls?state=open&head=o:feat/x&page=1&per_page=50",
            _pulls("feat/x", first=3),
        ),
        _get(
            f"{GH}/pulls/3", {"number": 3, "state": "open", "head": {"ref": "feat/x"}}
        ),
    )
    found = _github(present).repository("o", "r").pr.find_by_head("feat/x")
    assert found is not None and found.number == 3
    present.verify_exhausted()


def test_gitea_finds_by_head_and_stops_at_its_match() -> None:
    # Gitea's list has no head filter, so the walk is on the client; a
    # match on the first full page fetches no second one.
    heads = ["feat/x", *(f"other-{n}" for n in range(PAGE_SIZE - 1))]
    opener = _replay(_get(f"{GT}/pulls?state=open&page=1&limit=50", _pulls(*heads)))
    found = _gitea(opener).repository("o", "r").pr.find_by_head("feat/x")
    assert found is not None and found.number == 1
    opener.verify_exhausted()


# --- a pull request by its head commit ------------------------------------


def test_github_finds_by_commit_among_the_pull_requests_that_carry_it() -> None:
    # The refusal first: a commit GitHub does not know is no pull request.
    unknown = _replay(
        _get(
            f"{GH}/commits/{SHA}/pulls?page=1&per_page=100",
            {"message": "No commit found"},
            422,
        )
    )
    assert _github(unknown).repository("o", "r").pr.find_by_head_sha(SHA) is None
    # A commit can sit in several pull requests; the one it heads is the answer.
    opener = _replay(
        _get(
            f"{GH}/commits/{SHA}/pulls?page=1&per_page=100",
            [
                {"number": 5, "head": {"sha": "b" * 40}},
                {"number": 7, "head": {"sha": SHA}},
            ],
        ),
        _get(
            f"{GH}/pulls/7",
            {"number": 7, "state": "closed", "merged": True, "head": {"sha": SHA}},
        ),
    )
    found = _github(opener).repository("o", "r").pr.find_by_head_sha(SHA)
    assert found is not None and found.number == 7 and found.merged
    opener.verify_exhausted()


def test_gitlab_finds_by_commit_among_the_merge_requests_that_carry_it() -> None:
    opener = _replay(
        _get(
            f"{GL}/repository/commits/{SHA}/merge_requests?page=1&per_page=100",
            [
                {"iid": 2, "state": "merged", "sha": "b" * 40},
                {"iid": 3, "state": "merged", "sha": SHA, "source_branch": "feat/x"},
            ],
        )
    )
    found = _gitlab(opener).repository("o", "r").pr.find_by_head_sha(SHA)
    assert found is not None and found.number == 3 and found.merged
    opener.verify_exhausted()


# --- runs -------------------------------------------------------------------


def test_github_reads_a_workflows_newest_runs_in_one_request() -> None:
    # The refusal first: a workflow the repository does not have has no runs.
    missing = _replay(
        _get(
            f"{GH}/actions/workflows/gone.yml/runs?page=1&per_page=50",
            {"message": "Not Found"},
            404,
        )
    )
    assert _github(missing).repository("o", "r").checks.runs(workflow="gone.yml") == ()
    opener = _replay(
        _get(
            f"{GH}/actions/workflows/release.yml/runs?page=1&per_page=3&event=workflow_dispatch",
            {"workflow_runs": [_github_run(n) for n in (9, 8, 7)]},
        )
    )
    runs = (
        _github(opener)
        .repository("o", "r")
        .checks.runs(event="workflow_dispatch", workflow="release.yml", limit=3)
    )
    assert [run.id for run in runs] == [9, 8, 7]
    opener.verify_exhausted()


def test_gitea_reads_a_workflows_runs_until_the_limit() -> None:
    page = [
        {
            "id": 100 - n,
            "path": "ci.yml@refs/heads/main",
            "status": "completed",
            "conclusion": "success",
        }
        for n in range(PAGE_SIZE)
    ]
    opener = _replay(
        _get(
            f"{GT}/actions/workflows/ci.yml/runs?page=1&limit=50&event=push",
            {"workflow_runs": page},
        )
    )
    runs = (
        _gitea(opener)
        .repository("o", "r")
        .checks.runs(event="push", workflow="ci.yml", limit=2)
    )
    assert [run.id for run in runs] == [100, 99]
    opener.verify_exhausted()


def test_gitlab_keeps_unnamed_pipelines_for_a_workflow_and_stops_at_the_limit() -> None:
    # Only a dispatched pipeline carries a workflow's name: a named one of
    # another workflow goes, an unnamed one stays, and a full page that
    # already holds the limit fetches no second.
    page = [
        {"id": 200, "name": "nightly.yml", "source": "web", "status": "success"},
        {"id": 199, "name": "release.yml", "source": "web", "status": "success"},
        {"id": 198, "name": None, "source": "web", "status": "success"},
        *(
            {"id": 197 - n, "name": None, "source": "push", "status": "success"}
            for n in range(PAGE_SIZE - 3)
        ),
    ]
    opener = _replay(_get(f"{GL}/pipelines?page=1&per_page=50", page))
    runs = (
        _gitlab(opener)
        .repository("o", "r")
        .checks.runs(event="workflow_dispatch", workflow="release.yml", limit=2)
    )
    assert [run.id for run in runs] == [199, 198]
    opener.verify_exhausted()


# --- tags -------------------------------------------------------------------


def test_github_reads_a_prefixs_tags_in_one_request() -> None:
    # The refusal first: an empty repository has no refs, and says 409.
    empty = _replay(
        _get(
            f"{GH}/git/matching-refs/tags/",
            {"message": "Git Repository is empty."},
            409,
        )
    )
    assert _github(empty).repository("o", "r").tags() == ()
    opener = _replay(
        _get(
            f"{GH}/git/matching-refs/tags/packages/forge/v",
            [
                {"ref": "refs/tags/packages/forge/v0.1.0"},
                {"ref": "refs/tags/packages/forge/v0.2.0"},
            ],
        )
    )
    tags = _github(opener).repository("o", "r").tags(prefix="packages/forge/v")
    assert tags == ("packages/forge/v0.1.0", "packages/forge/v0.2.0")
    opener.verify_exhausted()


def test_gitea_reads_a_prefixs_tags_in_one_request() -> None:
    # The refusal first: no tag under the prefix answers 404.
    none = _replay(
        _get(f"{GT}/git/refs/tags/packages/none/v", {"message": "Not Found"}, 404)
    )
    assert _gitea(none).repository("o", "r").tags(prefix="packages/none/v") == ()
    opener = _replay(
        _get(
            f"{GT}/git/refs/tags/packages/forge/v",
            [{"ref": "refs/tags/packages/forge/v0.1.0"}],
        )
    )
    assert _gitea(opener).repository("o", "r").tags(prefix="packages/forge/v") == (
        "packages/forge/v0.1.0",
    )
    opener.verify_exhausted()


def test_gitlab_checks_the_prefix_of_what_its_search_returns() -> None:
    # The server's search matches more than the prefix: the names are
    # checked again here.
    opener = _replay(
        _get(
            f"{GL}/repository/tags?page=1&per_page=100&search=%5Epackages%2Fforge%2Fv",
            [{"name": "packages/forge/v0.1.0"}, {"name": "packages/forgery/v2.0.0"}],
        )
    )
    tags = _gitlab(opener).repository("o", "r").tags(prefix="packages/forge/v")
    assert tags == ("packages/forge/v0.1.0",)
    opener.verify_exhausted()
