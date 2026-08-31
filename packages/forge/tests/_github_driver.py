"""The ForgeDriver over github.com scratch repositories.

The driver moves the world through GitHub's own API: pushes are
commits made with the contents API (a new branch gets its ref
created first), tags come from the git refs API, and CI is the seeded
workflow below, executed by GitHub-hosted runners. The job reads its
verdict from the push event payload, so the outcome-at-push contract
holds: ``conf:failure`` fails, ``conf:hang`` holds until the driver
releases it, anything else passes.

The hold's release has no shared filesystem to touch: the held job
polls the repository for a ``release-<sha>`` tag with its own job
token, and the driver's settle creates that tag through the same
recorded opener, so replay stays deterministic.

Scratch repositories are created public: branch protection on private
repositories needs a paid plan, and the conformance content is
throwaway files. Everything is deleted as scenarios re-run; leftovers
carry this module's name prefix and a description saying they are
safe to delete.
"""

from __future__ import annotations

import base64
import contextlib
import os
import secrets
import time
from collections.abc import Callable
from typing import Literal

from livery.forge import Forge, ForgeError, GithubForge, Repository
from livery.forge._http import JsonClient, Opener
from livery.forge.testing import Outcome

#: The workflow every scratch repository is seeded with. The push
#: trigger carries a branches filter: a tag push is a push too, and
#: the settle mechanism creates release-<sha> tags that must not spawn
#: runs of their own.
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
        # GITHUB_TOKEN is an expression, not an ambient variable: a
        # run step's shell only sees it when the step exports it.
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
            ref="git/ref/tags/release-$GITHUB_SHA"
            until curl -fsS -H "Authorization: Bearer $GITHUB_TOKEN" \\
                "$GITHUB_API_URL/repos/$GITHUB_REPOSITORY/$ref" \\
                > /dev/null 2>&1; do
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


class GithubConformanceDriver:
    """Drive the conformance scenarios against github.com.

    One instance serves one scenario: *namespace* keeps its repository
    names deterministic for replay and collision-free across parallel
    scenarios. GitHub-hosted runners queue for tens of seconds, so the
    poll deadline is long; replay skips every wait.
    """

    def __init__(
        self,
        namespace: str,
        *,
        token: str,
        opener: Opener | None = None,
        live: bool = True,
    ) -> None:
        self._github = GithubForge.connect(token=token, opener=opener)
        self._client = JsonClient(
            "https://api.github.com",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            opener=opener,
        )
        if live and opener is None:
            # A plain live run (the release legs) gets unique names:
            # replay determinism only matters when recording, and a
            # cloud forge keeps redirects from earlier corpse renames
            # that a recreated deterministic path can collide with.
            namespace = f"{namespace}-{secrets.token_hex(3)}"
        self._namespace = namespace
        self._live = live
        self._recording = opener is not None
        self._me = self._github.whoami()
        # Cloud legs point the scratch at the e2e organisation; the
        # default stays the signed-in user for local recording.
        self._owner = os.environ.get("LIVERY_FORGE_E2E_OWNER") or self._me
        self._counter = 0
        self._created: list[tuple[str, str]] = []
        self._files = 0

    @property
    def forge(self) -> Forge:
        """The forge under test."""
        return self._github

    def unused_repo_name(self) -> tuple[str, str]:
        """A deterministic name; any leftover from a prior run is deleted."""
        self._counter += 1
        name = f"livery-forge-conf-{self._namespace}-{self._counter}"
        self._github.delete_repo(self._owner, name)
        return (self._owner, name)

    def fresh_repo(self) -> Repository:
        """A new public repository seeded with the conformance workflow."""
        owner, name = self.unused_repo_name()
        self._created.append((owner, name))
        repo = self._github.create_repo(
            owner,
            name,
            private=False,
            description="livery.forge conformance scratch; safe to delete",
        )
        self._put_file(
            owner,
            name,
            path=".github/workflows/conf.yml",
            content=WORKFLOW,
            message="conf: seed workflow",
            branch="main",
        )
        if self._live and not self._recording:
            # github.com indexes a new workflow file asynchronously;
            # dispatching before that answers 404. Recording keeps the
            # old exchange shape (the lag never bit a recording run),
            # so only the plain live legs wait.
            base = f"/repos/{owner}/{name}"
            for _ in range(30):
                listing = self._client.request(f"{base}/actions/workflows")
                names = [
                    str(flow.get("path", "")) for flow in listing.get("workflows", [])
                ]
                if any(path.endswith("conf.yml") for path in names):
                    break
                time.sleep(2)
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
        base = f"/repos/{repo_owner}/{repo_name}"
        exists = self._github.repository(repo_owner, repo_name).branch_exists(branch)
        if not exists:
            head = self._client.request(f"{base}/git/ref/heads/main")
            self._client.request(
                f"{base}/git/refs",
                method="POST",
                data={
                    "ref": f"refs/heads/{branch}",
                    "sha": str(head["object"]["sha"]),
                },
            )
        checks = self._github.repository(repo_owner, repo_name).checks
        for attempt in range(3):
            self._files += 1
            sha = self._put_file(
                repo_owner,
                repo_name,
                path=f"conf/{branch}/{self._files}.txt",
                content=f"{branch} {self._files}\n",
                message=f"conf: push {branch} [{_MARKERS[outcome]}]",
                branch=branch,
            )
            # GitHub can silently drop workflow-run creation for a
            # push under load, so a push is not done until its run is
            # listed. The wait is attempt-counted, not clock-based, so
            # a replay walks exactly the recorded polls.
            for _ in range(45):
                if checks.runs(head_sha=sha):
                    return sha
                if self._live:
                    time.sleep(2)
            del attempt
        raise ForgeError(
            f"three pushes to {branch} produced no workflow run:"
            " GitHub is dropping run creation for this repository"
        )

    def create_tag(self, repo_owner: str, repo_name: str, tag: str) -> None:
        """Create *tag* at the default branch's head (the git refs API)."""
        base = f"/repos/{repo_owner}/{repo_name}"
        head = self._client.request(f"{base}/git/ref/heads/main")
        self._client.request(
            f"{base}/git/refs",
            method="POST",
            data={"ref": f"refs/tags/{tag}", "sha": str(head["object"]["sha"])},
        )

    def settle(self, repo_owner: str, repo_name: str, sha: str) -> None:
        """Release any held run for *sha*, then wait for terminal runs.

        The release is the ``release-<sha>`` tag the held job polls
        for; creating it twice is absorbed, so settling twice is safe.
        """
        base = f"/repos/{repo_owner}/{repo_name}"
        try:
            self._client.request(
                f"{base}/git/refs",
                method="POST",
                data={"ref": f"refs/tags/release-{sha}", "sha": sha},
            )
        except ForgeError as exc:
            if exc.status != 422 or "already exists" not in exc.detail.lower():
                raise
        checks = self._github.repository(repo_owner, repo_name).checks
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
        checks = self._github.repository(repo_owner, repo_name).checks
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
        """Read the comments back through the issue comments API."""
        del kind  # GitHub serves both kinds through the issue endpoint.
        comments = self._client.request(
            f"/repos/{repo_owner}/{repo_name}/issues/{number}/comments"
        )
        if not comments:
            return ()
        return tuple(str(comment.get("body", "")) for comment in comments)

    def await_mergeable(self, repo_owner: str, repo_name: str, number: int) -> None:
        """Poll until GitHub's mergeability recompute finishes."""
        self._poll(
            lambda: (
                (
                    self._client.request(
                        f"/repos/{repo_owner}/{repo_name}/pulls/{number}"
                    )
                    or {}
                ).get("mergeable")
                is not None
            ),
            subject=f"mergeability of pull request {number}",
        )

    def await_merged(self, repo_owner: str, repo_name: str, number: int) -> None:
        """Poll until the merge and its aftermath have landed.

        The aftermath is the head branch deletion, which github.com
        performs asynchronously after the merge when the repository
        asks for it; whether it does is read from the repository
        itself.
        """
        state: dict[str, object] = {}

        def merged() -> bool:
            data = (
                self._client.request(f"/repos/{repo_owner}/{repo_name}/pulls/{number}")
                or {}
            )
            state.update(data)
            return bool(data.get("merged"))

        self._poll(merged, subject=f"pull request {number} to merge")
        settings = self._client.request(f"/repos/{repo_owner}/{repo_name}") or {}
        if not settings.get("delete_branch_on_merge"):
            return
        head = state.get("head")
        branch = str(head.get("ref", "")) if isinstance(head, dict) else ""
        repo = self._github.repository(repo_owner, repo_name)
        self._poll(
            lambda: not repo.branch_exists(branch),
            subject=f"head branch {branch} to be deleted",
        )

    def await_issue(
        self, repo_owner: str, repo_name: str, number: int, *, assignee: str = ""
    ) -> None:
        """Poll until listings serve the issue in its current state.

        github.com's issue listings run about nine seconds behind
        writes.
        """
        issues = self._github.repository(repo_owner, repo_name).issue

        def listed() -> bool:
            for row in issues.list(state="all"):
                if row.number == number:
                    break
            else:
                return False
            if not assignee or assignee not in row.assignees:
                return not assignee
            # assigned_to_me is search-backed and indexes later than
            # the listing; the scenario asserts it, so the wait does.
            return number in [mine.number for mine in issues.assigned_to_me()]

        self._poll(listed, subject=f"issue {number} to be listed")

    def required_context(self) -> str:
        """GitHub spells an Actions check by its job name."""
        return "gate"

    def _put_file(
        self,
        owner: str,
        name: str,
        *,
        path: str,
        content: str,
        message: str,
        branch: str,
    ) -> str:
        data = self._client.request(
            f"/repos/{owner}/{name}/contents/{path}",
            method="PUT",
            data={
                "message": message,
                "content": base64.b64encode(content.encode()).decode(),
                "branch": branch,
            },
        )
        return str(data["commit"]["sha"])

    def _poll(self, probe: Callable[[], bool], *, subject: str) -> None:
        """Run *probe* until it answers True; sleep only in live mode.

        GitHub-hosted runners queue for tens of seconds, so the live
        deadline is long; a replayed loop needs neither deadline nor
        sleep, and a drifted one dies on the cassette's own mismatch.
        """
        deadline = time.monotonic() + 600
        while not probe():
            if self._live:
                if time.monotonic() > deadline:
                    raise ForgeError(f"timed out after 600s waiting for {subject}")
                time.sleep(2)

    def cleanup(self) -> None:
        """Delete every repository this driver created; errors absorbed.

        Cloud accounts meter repositories, so a live run deletes its
        scratch as each scenario ends instead of leaving it for the
        next run's leftover sweep.
        """
        for owner, name in self._created:
            with contextlib.suppress(ForgeError):
                self._github.delete_repo(owner, name)
