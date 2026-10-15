"""What each backend's dispatch asks for and what it returns.

Crafted recordings stand in for the servers, so each test pins the one
request a dispatch makes and the answer it reads the run's id from.
"""

from __future__ import annotations

import json

from livery.forge import Forge, GiteaForge, GithubForge, GitlabForge
from livery.forge.testing import Cassette, Exchange, ReplayOpener

GITHUB = "https://api.github.com"
GH = f"{GITHUB}/repos/o/r/actions/workflows/ci.yml/dispatches"
GH_BODY = {"ref": "main", "inputs": {"only": "doctor"}, "return_run_details": True}
GITEA = "http://g.invalid/api/v1"
GT = f"{GITEA}/repos/o/r/actions/workflows/ci.yml/dispatches?return_run_details=true"
GT_BODY = {"ref": "main", "inputs": {"only": "doctor"}}
GITLAB = "http://gl.invalid/api/v4"
GL = f"{GITLAB}/projects/o%2Fr/pipeline"
GL_BODY = {
    "ref": "main",
    "variables": [
        {"key": "FORGE_WORKFLOW", "value": "ci.yml"},
        {"key": "only", "value": "doctor"},
    ],
}


def _post(url: str, sent: object, status: int, answer: object = "") -> ReplayOpener:
    """A server that answers the one POST to *url* carrying *sent*."""
    return ReplayOpener(
        Cassette(
            [
                Exchange(
                    method="POST",
                    url=url,
                    request_body=json.dumps(sent),
                    status=status,
                    reason="",
                    content_type="application/json" if answer else "",
                    response_body=answer
                    if isinstance(answer, str)
                    else json.dumps(answer),
                )
            ]
        )
    )


def _details(run: int) -> dict[str, object]:
    """The run details GitHub and Gitea answer a dispatch with."""
    return {
        "workflow_run_id": run,
        "run_url": f"https://forge.invalid/api/runs/{run}",
        "html_url": f"https://forge.invalid/runs/{run}",
    }


def test_a_server_answering_204_names_no_run() -> None:
    # The fallback first: a GitHub or Gitea server without the run's
    # details answers 204 with no body, and the caller finds the run
    # by listing.
    forges: tuple[Forge, ...] = (
        GithubForge(GITHUB, token="t", opener=_post(GH, GH_BODY, 204)),
        GiteaForge(GITEA, token="t", opener=_post(GT, GT_BODY, 204)),
    )
    for forge in forges:
        checks = forge.repository("o", "r").checks
        assert checks.dispatch("ci.yml", ref="main", inputs={"only": "doctor"}) is None


def test_each_backend_returns_the_run_its_answer_names() -> None:
    forges: tuple[Forge, ...] = (
        GithubForge(GITHUB, token="t", opener=_post(GH, GH_BODY, 200, _details(41))),
        GiteaForge(GITEA, token="t", opener=_post(GT, GT_BODY, 200, _details(42))),
        GitlabForge(
            GITLAB,
            token="t",
            opener=_post(GL, GL_BODY, 201, {"id": 43, "iid": 1, "ref": "main"}),
        ),
    )
    started = [
        forge.repository("o", "r").checks.dispatch(
            "ci.yml", ref="main", inputs={"only": "doctor"}
        )
        for forge in forges
    ]
    assert started == [41, 42, 43]
