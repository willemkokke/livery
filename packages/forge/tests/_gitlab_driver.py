"""The ForgeDriver over the local GitLab container.

The driver moves the world through GitLab's own API: pushes are
commits made with the commits API, tags come from the tags API, and CI
is the seeded .gitlab-ci.yml below, executed by the compose file's
shell-executor gitlab-runner. The job reads its verdict from
``CI_COMMIT_MESSAGE``, so the outcome-at-push contract holds:
``conf:failure`` fails, ``conf:hang`` holds until the driver releases
it by touching a file inside the runner container, anything else
passes.

GitLab deletes projects asynchronously and briefly reserves the path,
so the deterministic repository names this driver needs are created
with a bounded retry in live mode.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from typing import Literal
from urllib.parse import quote

from _gitea_driver import ROOT
from livery.forge import Forge, ForgeError, GitlabForge, Repository
from livery.forge._http import JsonClient, Opener
from livery.forge.testing import Outcome

#: The pipeline every conformance project is seeded with.
CI_YAML = """\
gate:
  script:
    - |
      case "$CI_COMMIT_MESSAGE" in
        *"conf:failure"*)
          echo "verdict failure"
          exit 1
          ;;
        *"conf:hang"*)
          echo "holding until released"
          i=0
          until [ -f "/tmp/conf-release-$CI_COMMIT_SHA" ]; do
            i=$((i+1))
            if [ "$i" -gt 600 ]; then exit 1; fi
            sleep 1
          done
          ;;
      esac
      echo "verdict success"
"""

_MARKERS: dict[Outcome, str] = {
    "success": "conf:success",
    "failure": "conf:failure",
    "hang": "conf:hang",
}


class GitlabConformanceDriver:
    """Drive the conformance scenarios against a GitLab server.

    One instance serves one scenario: *namespace* keeps its project
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
        self._gitlab = GitlabForge.connect(url=url, token=token, opener=opener)
        self._client = JsonClient(
            f"{url.rstrip('/')}/api/v4",
            headers={"PRIVATE-TOKEN": token},
            opener=opener,
            timeout=120,
        )
        self._namespace = namespace
        self._live = live
        self._counter = 0
        self._files = 0

    @property
    def forge(self) -> Forge:
        """The forge under test."""
        return self._gitlab

    def unused_repo_name(self) -> tuple[str, str]:
        """A deterministic name whose path is free right now.

        GitLab destroys projects asynchronously, so deleting a
        leftover does not free its path in time for the create that
        follows. The leftover is renamed to a corpse path first,
        which is synchronous and frees the path immediately; the
        corpse name carries the project id, which never recurs, so
        corpses cannot collide with each other and the exchange stays
        replay-deterministic (the id comes from the recorded answer).
        """
        self._counter += 1
        name = f"conf-{self._namespace}-{self._counter}"
        try:
            leftover = self._client.request(
                f"/projects/{_path('livery', name)}", none_on=(404,)
            )
        except ForgeError as exc:
            if exc.status is not None and 300 <= exc.status < 400:
                # A redirect route: the previous corpse still holds a
                # pointer from the old path while its deletion runs.
                # Creation wins over redirect routes, so there is no
                # live leftover to move aside.
                leftover = None
            else:
                raise
        if (
            leftover is not None
            and str(leftover.get("path_with_namespace")) == f"livery/{name}"
        ):
            pid = int(leftover["id"])
            corpse = f"corpse-{pid}"
            self._client.request(
                f"/projects/{pid}",
                method="PUT",
                data={"path": corpse, "name": corpse},
            )
            self._gitlab.delete_repo("livery", corpse)
        return ("livery", name)

    def fresh_repo(self) -> Repository:
        """A new project seeded with the conformance pipeline."""
        owner, name = self.unused_repo_name()
        repo = self._gitlab.create_repo(owner, name)
        self._commit(
            owner,
            name,
            branch="main",
            start_branch="",
            message="conf: seed pipeline",
            path=".gitlab-ci.yml",
            content=CI_YAML,
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
        exists = self._gitlab.repository(repo_owner, repo_name).branch_exists(branch)
        self._files += 1
        return self._commit(
            repo_owner,
            repo_name,
            branch=branch,
            start_branch="" if exists else "main",
            message=f"conf: push {branch} [{_MARKERS[outcome]}]",
            path=f"conf/{branch}/{self._files}.txt",
            content=f"{branch} {self._files}\n",
        )

    def create_tag(self, repo_owner: str, repo_name: str, tag: str) -> None:
        """Create *tag* at the default branch's head (the tags API)."""
        self._client.request(
            f"/projects/{_path(repo_owner, repo_name)}/repository/tags",
            method="POST",
            data={"tag_name": tag, "ref": "main"},
        )

    def settle(self, repo_owner: str, repo_name: str, sha: str) -> None:
        """Release any held job for *sha*, then wait for terminal runs."""
        if self._live:
            subprocess.run(
                [
                    "docker",
                    "compose",
                    "--profile",
                    "gitlab",
                    "--profile",
                    "gitlab-runner",
                    "exec",
                    "-T",
                    "gitlab-runner",
                    "touch",
                    f"/tmp/conf-release-{sha}",
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
            )
        checks = self._gitlab.repository(repo_owner, repo_name).checks
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
        checks = self._gitlab.repository(repo_owner, repo_name).checks
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
        """Read the notes back; merge requests and issues differ here."""
        noun = "merge_requests" if kind == "pr" else "issues"
        notes = self._client.request(
            f"/projects/{_path(repo_owner, repo_name)}/{noun}/{number}/notes"
        )
        if not notes:
            return ()
        ordered = sorted(notes, key=lambda note: int(note["id"]))
        return tuple(str(note.get("body", "")) for note in ordered)

    def await_mergeable(self, repo_owner: str, repo_name: str, number: int) -> None:
        """Poll until merging or arming stops answering 405.

        Two asynchronous facts must both land: the mergeability
        recompute (detailed_merge_status leaves the checking states)
        and the head pipeline's association with the merge request,
        without which merge-when-pipeline-succeeds has nothing to
        wait for.
        """

        def ready() -> bool:
            data = (
                self._client.request(
                    f"/projects/{_path(repo_owner, repo_name)}/merge_requests/{number}"
                )
                or {}
            )
            status = str(data.get("detailed_merge_status", ""))
            checking = ("", "unchecked", "checking", "preparing")
            return status not in checking and bool(data.get("head_pipeline"))

        self._poll(ready, subject=f"mergeability of merge request {number}")

    def await_merged(self, repo_owner: str, repo_name: str, number: int) -> None:
        """Poll until the merge and its aftermath have landed.

        The aftermath is the source branch deletion, which GitLab
        performs about a second after the merge when the merge asked
        for it; whether it did is read from the merged merge request
        itself.
        """
        state: dict[str, object] = {}

        def merged() -> bool:
            data = (
                self._client.request(
                    f"/projects/{_path(repo_owner, repo_name)}/merge_requests/{number}"
                )
                or {}
            )
            state.update(data)
            return data.get("state") == "merged"

        self._poll(merged, subject=f"merge request {number} to merge")
        if not (
            state.get("should_remove_source_branch")
            or state.get("force_remove_source_branch")
        ):
            return
        branch = str(state.get("source_branch") or "")
        repo = self._gitlab.repository(repo_owner, repo_name)
        self._poll(
            lambda: not repo.branch_exists(branch),
            subject=f"source branch {branch} to be deleted",
        )

    def await_issue(
        self, repo_owner: str, repo_name: str, number: int, *, assignee: str = ""
    ) -> None:
        """Poll until listings serve the issue in its current state."""
        issues = self._gitlab.repository(repo_owner, repo_name).issue

        def listed() -> bool:
            for row in issues.list(state="all"):
                if row.number == number:
                    return not assignee or assignee in row.assignees
            return False

        self._poll(listed, subject=f"issue {number} to be listed")

    def required_context(self) -> str:
        """Unreachable on GitLab: required_contexts is declined by name."""
        return "gate"

    def _commit(
        self,
        owner: str,
        name: str,
        *,
        branch: str,
        start_branch: str,
        message: str,
        path: str,
        content: str,
    ) -> str:
        body: dict[str, object] = {
            "branch": branch,
            "commit_message": message,
            "actions": [{"action": "create", "file_path": path, "content": content}],
        }
        if start_branch:
            body["start_branch"] = start_branch
        data = self._client.request(
            f"/projects/{_path(owner, name)}/repository/commits",
            method="POST",
            data=body,
        )
        return str(data["id"])

    def _poll(self, probe: Callable[[], bool], *, subject: str) -> None:
        """Run *probe* until it answers True; sleep only in live mode."""
        deadline = time.monotonic() + 180
        while not probe():
            if self._live:
                if time.monotonic() > deadline:
                    raise ForgeError(f"timed out after 180s waiting for {subject}")
                time.sleep(1)


def _path(owner: str, name: str) -> str:
    return quote(f"{owner}/{name}", safe="")
