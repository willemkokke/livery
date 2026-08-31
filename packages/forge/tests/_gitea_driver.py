"""The ForgeDriver over the local Gitea container.

The driver moves the world through Gitea's own API: pushes are commits
made with the contents API, tags come from the tags API, and CI is the
seeded workflow below, executed by the compose file's act_runner. The
workflow reads its verdict from the commit message (the push event
payload carries it), so the scenario's outcome-at-push contract holds:
``conf:failure`` fails, ``conf:hang`` holds until the driver releases
it by touching a file inside the runner container, anything else
passes.

Every driver request goes through the same opener as the backend under
test, so a recording captures the whole session and a replay needs no
container at all. Blocking (polls, sleeps, the release exec) happens
only in live mode; a replayed poll loop runs exactly as many
iterations as the recording did, because the recorded answers drive
it.
"""

from __future__ import annotations

import base64
import contextlib
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from livery.forge import Forge, ForgeError, GiteaForge, Repository
from livery.forge._http import JsonClient, Opener
from livery.forge.testing import Outcome

ROOT = Path(__file__).resolve().parents[3]

#: The workflow every conformance repository is seeded with. POSIX sh
#: only: the runner is act_runner's alpine image in host mode.
#: The workflow every scratch repository is seeded with. The push
#: trigger carries a branches filter (tag pushes are pushes too, and
#: the settle tags must not spawn runs), and a held job polls the API
#: for its release-<sha> tag, so the same mechanism works on the
#: compose forge and on gitea.com.
WORKFLOW = """\
name: conf
on:
  push:
    branches: ["**"]
  workflow_dispatch:
jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - name: verdict
        shell: sh
        env:
          GITHUB_TOKEN: ${{ github.token }}
        run: |
          if grep -q "conf:failure" "$GITHUB_EVENT_PATH"; then
            echo "verdict: failure"
            exit 1
          fi
          if grep -q "conf:hang" "$GITHUB_EVENT_PATH"; then
            echo "holding until released"
            i=0
            url="$GITHUB_API_URL/repos/$GITHUB_REPOSITORY/tags/release-$GITHUB_SHA"
            until wget -q -O /dev/null \\
                --header "Authorization: token $GITHUB_TOKEN" "$url"; do
              i=$((i+1))
              if [ "$i" -gt 300 ]; then exit 1; fi
              sleep 2
            done
          fi
          echo "verdict: success"
"""

_MARKERS: dict[Outcome, str] = {
    "success": "conf:success",
    "failure": "conf:failure",
    "hang": "conf:hang",
}


class GiteaConformanceDriver:
    """Drive the conformance scenarios against a Gitea server.

    One instance serves one scenario: *namespace* keeps its repository
    names deterministic for replay and collision-free across parallel
    scenarios.
    """

    def __init__(
        self,
        namespace: str,
        *,
        url: str,
        token: str,
        opener: Opener | None = None,
        live: bool = True,
    ) -> None:
        self._gitea = GiteaForge.connect(url=url, token=token, opener=opener)
        # Cloud legs point the scratch at the e2e organisation; the
        # default stays the compose seed org.
        self._owner_org = os.environ.get("LIVERY_FORGE_E2E_OWNER") or "livery"
        self._client = JsonClient(
            f"{url.rstrip('/')}/api/v1",
            headers={"Authorization": f"token {token}", "Accept": "application/json"},
            opener=opener,
        )
        self._namespace = namespace
        self._live = live
        self._counter = 0
        self._created: list[tuple[str, str]] = []
        self._files = 0

    @property
    def forge(self) -> Forge:
        """The forge under test."""
        return self._gitea

    def unused_repo_name(self) -> tuple[str, str]:
        """A deterministic name; any leftover from a prior run is deleted."""
        self._counter += 1
        name = f"conf-{self._namespace}-{self._counter}"
        self._gitea.delete_repo(self._owner_org, name)
        return (self._owner_org, name)

    def fresh_repo(self) -> Repository:
        """A new repository seeded with the conformance workflow."""
        owner, name = self.unused_repo_name()
        self._created.append((owner, name))
        repo = None
        for attempt in range(3):
            try:
                repo = self._gitea.create_repo(owner, name)
            except ForgeError as exc:
                # gitea.com answers 500 on bursts of creations; brief
                # retries ride it out, the last answer verbatim.
                if exc.status is not None and exc.status >= 500 and attempt < 2:
                    if self._live:
                        time.sleep(2 + attempt)
                    continue
                raise
            break
        assert repo is not None
        self._commit_file(
            owner,
            name,
            path=".gitea/workflows/conf.yml",
            content=WORKFLOW,
            message="conf: seed workflow",
            branch="main",
            new=False,
        )
        return repo

    def push(
        self,
        repo_owner: str,
        repo_name: str,
        branch: str,
        *,
        outcome: Outcome = "success",
    ) -> str:
        """Commit to *branch* with the outcome marker in the message."""
        exists = self._gitea.repository(repo_owner, repo_name).branch_exists(branch)
        self._files += 1
        return self._commit_file(
            repo_owner,
            repo_name,
            path=f"conf/{branch}/{self._files}.txt",
            content=f"{branch} {self._files}\n",
            message=f"conf: push {branch} [{_MARKERS[outcome]}]",
            branch=branch,
            new=not exists,
        )

    def create_tag(self, repo_owner: str, repo_name: str, tag: str) -> None:
        """Create *tag* at the default branch's head (the tags API)."""
        self._client.request(
            f"/repos/{repo_owner}/{repo_name}/tags",
            method="POST",
            data={"tag_name": tag},
        )

    def settle(self, repo_owner: str, repo_name: str, sha: str) -> None:
        """Release any held run for *sha*, then wait for terminal runs.

        The release is the ``release-<sha>`` tag the held job polls
        for; a tag that already exists is absorbed, so settling twice
        is safe.
        """
        try:
            self._client.request(
                f"/repos/{repo_owner}/{repo_name}/tags",
                method="POST",
                data={"tag_name": f"release-{sha}", "target": sha},
            )
        except ForgeError as exc:
            if exc.status != 409:
                raise
        checks = self._gitea.repository(repo_owner, repo_name).checks
        self._poll(
            lambda: (
                bool(runs := checks.runs(head_sha=sha))
                and all(run.status == "completed" for run in runs)
            ),
            subject=f"runs for {sha} to settle",
        )

    def await_run(
        self, repo_owner: str, repo_name: str, *, head_sha: str = "", event: str = ""
    ) -> int:
        """Poll until exactly one matching run is listed; its id."""
        checks = self._gitea.repository(repo_owner, repo_name).checks
        found: list[int] = []

        def probe() -> bool:
            matching = checks.runs(head_sha=head_sha, event=event)
            if len(matching) > 1:
                raise AssertionError(
                    f"expected one matching run, found {len(matching)}"
                )
            found[:] = [run.id for run in matching]
            return bool(found)

        self._poll(probe, subject="a matching run to appear")
        return found[0]

    def comment_bodies(
        self,
        repo_owner: str,
        repo_name: str,
        number: int,
        *,
        kind: Literal["pr", "issue"],
    ) -> tuple[str, ...]:
        """Read the comments back through Gitea's issue comments API."""
        del kind  # Gitea serves both kinds through the issue endpoint.
        comments = self._client.request(
            f"/repos/{repo_owner}/{repo_name}/issues/{number}/comments"
        )
        if comments is None or comments == {}:
            return ()
        return tuple(str(comment.get("body", "")) for comment in comments)

    def await_mergeable(self, repo_owner: str, repo_name: str, number: int) -> None:
        """Poll until Gitea's mergeability recompute finishes."""
        self._poll(
            lambda: bool(
                (
                    self._client.request(
                        f"/repos/{repo_owner}/{repo_name}/pulls/{number}"
                    )
                    or {}
                ).get("mergeable")
            ),
            subject=f"mergeability of pull request {number}",
        )

    def await_merged(self, repo_owner: str, repo_name: str, number: int) -> None:
        """Poll until the scheduled merge has landed."""
        self._poll(
            lambda: bool(
                (
                    self._client.request(
                        f"/repos/{repo_owner}/{repo_name}/pulls/{number}"
                    )
                    or {}
                ).get("merged")
            ),
            subject=f"pull request {number} to merge",
        )

    def await_issue(
        self, repo_owner: str, repo_name: str, number: int, *, assignee: str = ""
    ) -> None:
        """Poll until listings serve the issue in its current state."""
        issues = self._gitea.repository(repo_owner, repo_name).issue

        def listed() -> bool:
            for row in issues.list(state="all"):
                if row.number == number:
                    return not assignee or assignee in row.assignees
            return False

        self._poll(listed, subject=f"issue {number} to be listed")

    def required_context(self) -> str:
        """Gitea spells an Actions check as workflow / job (event)."""
        return "conf / gate (push)"

    def _commit_file(
        self,
        owner: str,
        name: str,
        *,
        path: str,
        content: str,
        message: str,
        branch: str,
        new: bool,
    ) -> str:
        body = {
            "content": base64.b64encode(content.encode()).decode(),
            "message": message,
        }
        if new:
            body["new_branch"] = branch
        else:
            body["branch"] = branch
        data = self._client.request(
            f"/repos/{owner}/{name}/contents/{path}", method="POST", data=body
        )
        return str(data["commit"]["sha"])

    def _poll(self, probe: Callable[[], bool], *, subject: str) -> None:
        """Run *probe* until it answers True; sleep only in live mode.

        A replayed loop needs no deadline and no sleep: the recorded
        answers drive exactly the iterations the recording made, and a
        drifted loop dies on the cassette's own mismatch error.
        """
        deadline = time.monotonic() + 180
        while not probe():
            if self._live:
                if time.monotonic() > deadline:
                    raise ForgeError(f"timed out after 180s waiting for {subject}")
                time.sleep(1)

    def cleanup(self) -> None:
        """Delete every repository this driver created; errors absorbed.

        Cloud accounts meter repositories, so a live run deletes its
        scratch as each scenario ends instead of leaving it for the
        next run's leftover sweep.
        """
        for owner, name in self._created:
            with contextlib.suppress(ForgeError):
                self._gitea.delete_repo(owner, name)
