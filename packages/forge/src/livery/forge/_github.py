"""The GitHub backend: livery.forge.Forge over REST v3 plus one GraphQL pair.

REST carries everything except auto-merge, which GitHub exposes only
as the GraphQL mutations ``enablePullRequestAutoMerge`` and
``disablePullRequestAutoMerge``.

Construction and the token rule: livery.forge.GithubForge.connect
resolves the server once (github.com unless a GitHub Enterprise URL is
given) and the token as ``GITHUB_TOKEN`` first, then ``gh auth token``,
so a machine with a signed-in gh CLI needs no configuration. The token
belongs to the resolved host and no other.

Capabilities: ``auto_merge``, ``force_cancel``, and
``required_contexts`` are supported. ``ci_secrets`` depends on the
``github-secrets`` extra: the secrets API demands sealed-box
encryption (libsodium), which PyNaCl provides and the standard
library cannot, so ``livery-forge[github-secrets]`` turns the
capability on and a bare install declines it by name. The import is
lazy; nothing outside the secrets path ever loads it.
"""

from __future__ import annotations

import base64
import importlib
import importlib.util
import os
import subprocess
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from livery.forge._errors import ForgeError, Unsupported
from livery.forge._http import JsonClient, Opener
from livery.forge._protocol import Checks, Issues, PullRequests, Releases, Repository
from livery.forge._types import (
    Capability,
    CheckState,
    CombinedStatus,
    Conclusion,
    Issue,
    ItemState,
    Job,
    Label,
    PullRequest,
    Release,
    RepoConfig,
    RepoInfo,
    Run,
    RunStatus,
    StateFilter,
)

_CONCLUSIONS: dict[str, Conclusion] = {
    "success": "success",
    "failure": "failure",
    "cancelled": "cancelled",
    "skipped": "skipped",
    "neutral": "skipped",
}


def _run_state(raw_status: str, raw_conclusion: str) -> tuple[RunStatus, Conclusion]:
    """GitHub's run or job status pair, normalised to the protocol's.

    Terminal words outside the protocol's vocabulary (timed_out,
    action_required, stale) read as failure: the run is over and it is
    not green.
    """
    if raw_status == "completed":
        return ("completed", _CONCLUSIONS.get(raw_conclusion, "failure"))
    if raw_status == "in_progress":
        return ("running", "")
    return ("queued", "")


def _resolve_token() -> str:
    """``GITHUB_TOKEN`` first, then ``gh auth token``, else empty."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        return token
    try:
        result = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, check=False
        )
    except OSError:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


class GithubForge:
    """One GitHub server, spoken to through livery.forge.Forge's verbs.

    Build with livery.forge.GithubForge.connect; the constructor takes
    the resolved values and applies no environment fallbacks.

    Args:
        api_base: The API root: ``https://api.github.com``, or a
            GitHub Enterprise server's ``<url>/api/v3``.
        token: The token sent on every request.
        opener: The network seam; the redirect-refusing default when
            omitted.
    """

    def __init__(
        self, api_base: str, *, token: str, opener: Opener | None = None
    ) -> None:
        """Bind the client to *api_base* with *token*."""
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = JsonClient(api_base, headers=headers, opener=opener)
        self._opener = opener

    @classmethod
    def connect(
        cls,
        *,
        url: str = "",
        token: str | None = None,
        opener: Opener | None = None,
    ) -> GithubForge:
        """The server, resolved once, never inferred from ambient state.

        github.com unless *url* names a GitHub Enterprise server (its
        API is at ``<url>/api/v3``). *token* resolves as
        ``GITHUB_TOKEN`` first, then ``gh auth token``; nothing found
        raises, because an unauthenticated write fails later and
        further from the cause. Pass ``token=""`` to read anonymously
        on purpose.
        """
        web = (url or "https://github.com").rstrip("/")
        if web in ("https://github.com", "http://github.com"):
            api = "https://api.github.com"
        else:
            api = f"{web}/api/v3"
        resolved = _resolve_token() if token is None else token
        if token is None and not resolved:
            raise ForgeError(
                "no GitHub credential: set GITHUB_TOKEN or sign in with"
                ' `gh auth login`, or pass token="" to read anonymously'
                " on purpose"
            )
        return cls(api, token=resolved, opener=opener)

    def whoami(self) -> str:
        """The authenticated user's login name (``GET /user``)."""
        data = self._client.request("/user")
        return str(data.get("login", ""))

    def server_version(self) -> str:
        """The Enterprise version from ``GET /meta``, or ``github.com``.

        github.com carries no version; the constant string satisfies
        the protocol's non-empty answer and names the host class.
        """
        data = self._client.request("/meta")
        return str(data.get("installed_version") or "github.com")

    def supports(self, capability: Capability) -> bool:
        """Honest per-install: ``ci_secrets`` needs the extra installed."""
        if capability == "ci_secrets":
            return importlib.util.find_spec("nacl") is not None
        return capability in ("auto_merge", "force_cancel", "required_contexts")

    def repository(self, owner: str, name: str) -> Repository:
        """The view onto one repository. Cheap, no network."""
        return _GithubRepository(self, self._client, owner, name)

    def create_repo(
        self,
        owner: str,
        name: str,
        *,
        private: bool = True,
        description: str = "",
    ) -> Repository:
        """Create the repository under an org or the token's own user.

        ``auto_init`` gives it the default branch the protocol
        requires.
        """
        body = {
            "name": name,
            "private": private,
            "description": description,
            "auto_init": True,
        }
        if owner == self.whoami():
            self._client.request("/user/repos", method="POST", data=body)
        else:
            self._client.request(
                f"/orgs/{quote(owner)}/repos", method="POST", data=body
            )
        return self.repository(owner, name)

    def get_repo(self, owner: str, name: str) -> RepoInfo | None:
        """The repository's settings, or None when it does not exist."""
        data = self._client.request(
            f"/repos/{quote(owner)}/{quote(name)}", none_on=(404,)
        )
        if data is None:
            return None
        return RepoInfo(
            owner=owner,
            name=name,
            default_branch=str(data.get("default_branch", "")),
            private=bool(data.get("private", False)),
        )

    def delete_repo(self, owner: str, name: str) -> None:
        """Delete the repository; one already gone is success.

        The token needs the ``delete_repo`` scope; a 403 quotes
        GitHub's refusal verbatim.
        """
        self._client.request(
            f"/repos/{quote(owner)}/{quote(name)}", method="DELETE", none_on=(404,)
        )

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """One GraphQL call; errors raise with GitHub's words verbatim.

        GraphQL failures answer 200 with an ``errors`` array, so the
        HTTP layer cannot see them; this is where they become
        livery.forge.ForgeError.
        """
        data = self._client.request(
            "/graphql", method="POST", data={"query": query, "variables": variables}
        )
        errors = data.get("errors") or []
        if errors:
            said = "; ".join(str(e.get("message", "")) for e in errors)
            raise ForgeError(
                f"GraphQL refused: {said}",
                method="POST",
                endpoint="/graphql",
                detail=said,
            )
        result: dict[str, Any] = data.get("data") or {}
        return result


class _GithubRepository:
    """The livery.forge.Repository view onto one GitHub repository."""

    def __init__(
        self, forge: GithubForge, client: JsonClient, owner: str, name: str
    ) -> None:
        self._owner = owner
        self._name = name
        self._forge = forge
        self._client = client
        self._base = f"/repos/{quote(owner)}/{quote(name)}"
        self.pr: PullRequests = _GithubPullRequests(forge, client, self._base)
        self.checks: Checks = _GithubChecks(forge, client, self._base)
        self.issue: Issues = _GithubIssues(forge, client, self._base)
        self.release: Releases = _GithubReleases(client, self._base)

    @property
    def owner(self) -> str:
        """The owner the view is bound to."""
        return self._owner

    @property
    def name(self) -> str:
        """The repository name the view is bound to."""
        return self._name

    def configure(self, config: RepoConfig) -> None:
        """Assert the stated settings; every step probes before acting."""
        patch: dict[str, Any] = {}
        if config.default_branch is not None:
            patch["default_branch"] = config.default_branch
        if config.squash_only is not None and config.squash_only:
            patch.update(
                allow_squash_merge=True,
                allow_merge_commit=False,
                allow_rebase_merge=False,
            )
        if config.delete_branch_on_merge is not None:
            patch["delete_branch_on_merge"] = config.delete_branch_on_merge
        if config.allow_auto_merge is not None:
            patch["allow_auto_merge"] = config.allow_auto_merge
        if patch:
            self._client.request(self._base, method="PATCH", data=patch)
        if config.required_contexts is not None:
            self._protect_default_branch(config.required_contexts)
        if config.secrets is not None:
            if importlib.util.find_spec("nacl") is None:
                raise Unsupported(
                    "storing CI secrets on GitHub needs sealed-box encryption"
                    " (capability: ci_secrets): install"
                    " livery-forge[github-secrets]"
                )
            self._put_secrets(config.secrets)
        if config.variables is not None:
            for key, value in config.variables.items():
                exists = self._client.request(
                    f"{self._base}/actions/variables/{quote(key)}", none_on=(404,)
                )
                if exists is None:
                    self._client.request(
                        f"{self._base}/actions/variables",
                        method="POST",
                        data={"name": key, "value": value},
                    )
                else:
                    self._client.request(
                        f"{self._base}/actions/variables/{quote(key)}",
                        method="PATCH",
                        data={"name": key, "value": value},
                    )
        if config.labels is not None:
            self._ensure_labels(config.labels)

    def _put_secrets(self, secrets: Mapping[str, str]) -> None:
        """Seal each value to the repository's public key and store it."""
        nacl_public = importlib.import_module("nacl.public")
        key_data = self._client.request(f"{self._base}/actions/secrets/public-key")
        public_key = nacl_public.PublicKey(base64.b64decode(str(key_data["key"])))
        box = nacl_public.SealedBox(public_key)
        for name, value in secrets.items():
            sealed = box.encrypt(value.encode())
            self._client.request(
                f"{self._base}/actions/secrets/{quote(name)}",
                method="PUT",
                data={
                    "encrypted_value": base64.b64encode(sealed).decode(),
                    "key_id": str(key_data["key_id"]),
                },
            )

    def _protect_default_branch(self, contexts: tuple[str, ...]) -> None:
        """Require *contexts* on the default branch's protection."""
        info = self._forge.get_repo(self._owner, self._name)
        if info is None:
            raise ForgeError(f"{self._owner}/{self._name} does not exist")
        self._client.request(
            f"{self._base}/branches/{quote(info.default_branch, safe='')}/protection",
            method="PUT",
            data={
                "required_status_checks": {
                    "strict": False,
                    "contexts": list(contexts),
                },
                "enforce_admins": False,
                "required_pull_request_reviews": None,
                "restrictions": None,
            },
        )

    def _ensure_labels(self, labels: tuple[Label, ...]) -> None:
        """Create or update each label by name; none are ever deleted."""
        existing = {
            str(label["name"])
            for label in self._client.paginate(
                lambda page: (
                    self._client.request(f"{self._base}/labels?page={page}&per_page=50")
                    or []
                ),
                subject=f"{self._base}/labels",
            )
        }
        for label in labels:
            body = {
                "name": label.name,
                "color": label.color.lstrip("#"),
                "description": label.description,
            }
            if label.name in existing:
                self._client.request(
                    f"{self._base}/labels/{quote(label.name)}",
                    method="PATCH",
                    data=body,
                )
            else:
                self._client.request(f"{self._base}/labels", method="POST", data=body)

    def tags(self) -> tuple[str, ...]:
        """Every tag name, complete or raising."""
        tags = self._client.paginate(
            lambda page: (
                self._client.request(
                    f"{self._base}/tags?page={page}&per_page=50", none_on=(404,)
                )
                or []
            ),
            subject=f"{self._base}/tags",
        )
        return tuple(str(tag["name"]) for tag in tags)

    def branch_exists(self, branch: str) -> bool:
        """Whether *branch* exists."""
        return (
            self._client.request(
                f"{self._base}/branches/{quote(branch, safe='')}", none_on=(404,)
            )
            is not None
        )

    def delete_branch(self, branch: str) -> None:
        """Delete *branch*; one already gone is success.

        GitHub answers 422, not 404, for a ref that does not exist.
        """
        self._client.request(
            f"{self._base}/git/refs/heads/{quote(branch, safe='')}",
            method="DELETE",
            none_on=(404, 422),
        )


def _as_pull_request(data: Mapping[str, Any]) -> PullRequest:
    """GitHub's pull request JSON, normalised."""
    state: ItemState = "open" if data.get("state") == "open" else "closed"
    head = data.get("head") or {}
    base = data.get("base") or {}
    merged = bool(data.get("merged", False)) or bool(data.get("merged_at"))
    return PullRequest(
        number=int(data["number"]),
        title=str(data.get("title", "")),
        body=str(data.get("body") or ""),
        state=state,
        merged=merged,
        head_branch=str(head.get("ref") or ""),
        head_sha=str(head.get("sha") or ""),
        base_branch=str(base.get("ref") or ""),
        url=str(data.get("html_url", "")),
    )


_ENABLE_AUTO_MERGE = """\
mutation($id: ID!, $title: String!, $body: String!) {
  enablePullRequestAutoMerge(input: {
    pullRequestId: $id, mergeMethod: SQUASH,
    commitHeadline: $title, commitBody: $body
  }) { clientMutationId }
}
"""

_DISABLE_AUTO_MERGE = """\
mutation($id: ID!) {
  disablePullRequestAutoMerge(input: {pullRequestId: $id}) {
    clientMutationId
  }
}
"""


class _GithubPullRequests:
    """The pull request operations of one GitHub repository."""

    def __init__(self, forge: GithubForge, client: JsonClient, base: str) -> None:
        self._forge = forge
        self._client = client
        self._base = base

    def open(self, head: str, base: str, title: str, body: str = "") -> PullRequest:
        """Open a pull request (``POST /pulls``)."""
        data = self._client.request(
            f"{self._base}/pulls",
            method="POST",
            data={"title": title, "body": body, "head": head, "base": base},
        )
        return _as_pull_request(data)

    def _scan(self, state: StateFilter) -> list[dict[str, Any]]:
        return self._client.paginate(
            lambda page: (
                self._client.request(
                    f"{self._base}/pulls?state={state}&page={page}&per_page=50"
                )
                or []
            ),
            subject=f"{self._base}/pulls",
        )

    def find_by_head(
        self, branch: str, *, state: StateFilter = "open"
    ) -> PullRequest | None:
        """The pull request whose head branch is *branch*, or None.

        Matched client-side over the listing, as every backend does:
        the answer is then correct whether or not the server's own
        filter would have been.
        """
        for raw in self._scan(state):
            if (raw.get("head") or {}).get("ref") == branch:
                return self.get(int(raw["number"]))
        return None

    def find_by_head_sha(self, sha: str) -> PullRequest | None:
        """The pull request whose head commit is *sha*, or None."""
        for raw in self._scan("all"):
            if (raw.get("head") or {}).get("sha") == sha:
                return self.get(int(raw["number"]))
        return None

    def get(self, number: int) -> PullRequest | None:
        """The pull request *number*, or None.

        The single GET is the one listing entries lack: it carries
        ``merged`` and ``auto_merge``, so the find methods re-read
        through it.
        """
        data = self._client.request(f"{self._base}/pulls/{number}", none_on=(404,))
        return None if data is None else _as_pull_request(data)

    def update_title(self, number: int, title: str) -> None:
        """Retitle pull request *number*."""
        self._client.request(
            f"{self._base}/pulls/{number}", method="PATCH", data={"title": title}
        )

    def close(self, number: int) -> None:
        """Close pull request *number* without merging."""
        self._client.request(
            f"{self._base}/pulls/{number}", method="PATCH", data={"state": "closed"}
        )

    def reopen(self, number: int) -> None:
        """Reopen the closed, unmerged pull request *number*."""
        self._client.request(
            f"{self._base}/pulls/{number}", method="PATCH", data={"state": "open"}
        )

    def merge_now(self, number: int, *, title: str, message: str = "") -> None:
        """Squash-merge now; 405 and 409 refusals pass through verbatim."""
        self._client.request(
            f"{self._base}/pulls/{number}/merge",
            method="PUT",
            data={
                "merge_method": "squash",
                "commit_title": title,
                "commit_message": message,
            },
        )

    def _node_id(self, number: int) -> str:
        data = self._client.request(f"{self._base}/pulls/{number}", none_on=(404,))
        if data is None:
            raise ForgeError(f"no pull request {number} at {self._base}", status=404)
        return str(data["node_id"])

    def arm(self, number: int, *, title: str, message: str = "") -> None:
        """Enable auto-merge through GraphQL, replacing any schedule.

        GitHub cannot update an armed schedule's commit headline, so an
        armed pull request is disarmed first: the protocol promises
        that re-arming replaces the schedule, title included. GitHub
        only arms a pull request that something blocks (branch
        protection with required checks); arming an unblocked one is
        refused with GitHub's own words.
        """
        node = self._node_id(number)
        if self.is_armed(number):
            self._forge.graphql(_DISABLE_AUTO_MERGE, {"id": node})
        self._forge.graphql(
            _ENABLE_AUTO_MERGE, {"id": node, "title": title, "body": message}
        )

    def disarm(self, number: int) -> bool:
        """Disable auto-merge; False when nothing was armed."""
        if not self.is_armed(number):
            return False
        self._forge.graphql(_DISABLE_AUTO_MERGE, {"id": self._node_id(number)})
        return True

    def is_armed(self, number: int) -> bool:
        """Whether auto-merge is enabled: readable state on the REST GET."""
        data = self._client.request(f"{self._base}/pulls/{number}", none_on=(404,))
        if data is None:
            raise ForgeError(f"no pull request {number} at {self._base}", status=404)
        return data.get("state") == "open" and data.get("auto_merge") is not None

    def comment(self, number: int, body: str) -> None:
        """Post *body* on pull request *number* (the issue comments API)."""
        self._client.request(
            f"{self._base}/issues/{number}/comments",
            method="POST",
            data={"body": body},
        )


class _GithubChecks:
    """The CI operations of one GitHub repository."""

    def __init__(self, forge: GithubForge, client: JsonClient, base: str) -> None:
        self._forge = forge
        self._client = client
        self._base = base

    def status(self, sha: str) -> CombinedStatus:
        """Commit statuses and check runs folded into the one verdict.

        GitHub keeps two reporting systems: the combined status covers
        commit statuses only, and Actions reports check runs. The
        caller gets one answer over both.
        """
        combined = (
            self._client.request(
                f"{self._base}/commits/{quote(sha, safe='')}/status",
                none_on=(404, 422),
            )
            or {}
        )
        statuses = combined.get("statuses") or []
        runs_data = (
            self._client.request(
                f"{self._base}/commits/{quote(sha, safe='')}/check-runs",
                none_on=(404, 422),
            )
            or {}
        )
        check_runs = runs_data.get("check_runs") or []
        contexts = len(statuses) + len(check_runs)
        if contexts == 0:
            return CombinedStatus(state="none", contexts=0)
        state: CheckState = "success"
        if statuses and str(combined.get("state")) in ("failure", "error"):
            state = "failure"
        elif statuses and str(combined.get("state")) == "pending":
            state = "pending"
        for run in check_runs:
            run_state, conclusion = _run_state(
                str(run.get("status", "")), str(run.get("conclusion") or "")
            )
            if run_state != "completed":
                if state != "failure":
                    state = "pending"
            elif conclusion not in ("success", "skipped"):
                state = "failure"
        return CombinedStatus(state=state, contexts=contexts)

    def runs(self, *, head_sha: str = "", event: str = "") -> tuple[Run, ...]:
        """The repository's Actions runs, newest first."""
        query = "".join(
            f"&{key}={quote(value)}"
            for key, value in (("head_sha", head_sha), ("event", event))
            if value
        )
        raw = self._client.paginate(
            lambda page: (
                self._client.request(
                    f"{self._base}/actions/runs?page={page}&per_page=50{query}"
                ).get("workflow_runs")
                or []
            ),
            subject=f"{self._base}/actions/runs",
        )
        runs = []
        for entry in raw:
            status, conclusion = _run_state(
                str(entry.get("status", "")), str(entry.get("conclusion") or "")
            )
            runs.append(
                Run(
                    id=int(entry["id"]),
                    workflow=str(entry.get("path", "")),
                    head_sha=str(entry.get("head_sha", "")),
                    event=str(entry.get("event", "")),
                    status=status,
                    conclusion=conclusion,
                    url=str(entry.get("html_url", "")),
                )
            )
        runs.sort(key=lambda run: run.id, reverse=True)
        return tuple(runs)

    def jobs(self, run: int) -> tuple[Job, ...]:
        """Every job of one run."""
        raw = self._client.paginate(
            lambda page: (
                self._client.request(
                    f"{self._base}/actions/runs/{run}/jobs?page={page}&per_page=50"
                ).get("jobs")
                or []
            ),
            subject=f"{self._base}/actions/runs/{run}/jobs",
        )
        jobs = []
        for entry in raw:
            status, conclusion = _run_state(
                str(entry.get("status", "")), str(entry.get("conclusion") or "")
            )
            jobs.append(
                Job(
                    id=int(entry["id"]),
                    name=str(entry.get("name", "")),
                    status=status,
                    conclusion=conclusion,
                )
            )
        return tuple(jobs)

    def job_log(self, job: int) -> str:
        """The raw log text of one job.

        GitHub answers the logs endpoint with a redirect to a signed,
        short-lived URL. The refused redirect carries that location in
        the error's detail; it is followed once, bare, with no token
        attached, which is the one redirect this package ever follows.
        """
        try:
            text = self._client.text(f"{self._base}/actions/jobs/{job}/logs")
        except ForgeError as exc:
            if exc.status is None or not (300 <= exc.status < 400) or not exc.detail:
                raise
            plain = JsonClient(exc.detail, headers={}, opener=self._forge._opener)
            return plain.text("") or ""
        return text or ""

    def rerun(self, run: int, *, failed_only: bool = True) -> None:
        """Re-run failed jobs, or the whole run; a live run's refusal passes."""
        endpoint = "rerun-failed-jobs" if failed_only else "rerun"
        self._client.request(
            f"{self._base}/actions/runs/{run}/{endpoint}", method="POST"
        )

    def cancel_run(self, run: int, *, force: bool = False) -> None:
        """Cancel the run; ``force`` discards later runner reports."""
        endpoint = "force-cancel" if force else "cancel"
        self._client.request(
            f"{self._base}/actions/runs/{run}/{endpoint}", method="POST"
        )

    def dispatch(
        self, workflow: str, *, ref: str, inputs: Mapping[str, str] | None = None
    ) -> None:
        """Trigger the workflow file's ``workflow_dispatch`` on *ref*."""
        self._client.request(
            f"{self._base}/actions/workflows/{quote(workflow)}/dispatches",
            method="POST",
            data={"ref": ref, "inputs": dict(inputs or {})},
        )


class _GithubReleases:
    """The release operations of one GitHub repository."""

    def __init__(self, client: JsonClient, base: str) -> None:
        self._client = client
        self._base = base

    def create(
        self, tag: str, *, name: str, body: str = "", prerelease: bool = False
    ) -> Release:
        """Create the release for *tag*; an existing release is refused."""
        existing = self.get(tag)
        if existing is not None:
            raise ForgeError(
                f"tag {tag} already has a release: probe with release.get"
                " before creating",
                status=409,
            )
        data = self._client.request(
            f"{self._base}/releases",
            method="POST",
            data={
                "tag_name": tag,
                "name": name,
                "body": body,
                "prerelease": prerelease,
            },
        )
        return _as_release(data)

    def get(self, tag: str) -> Release | None:
        """The release for *tag*, or None; the tag is URL-encoded whole."""
        data = self._client.request(
            f"{self._base}/releases/tags/{quote(tag, safe='')}", none_on=(404,)
        )
        return None if data is None else _as_release(data)


def _as_release(data: Mapping[str, Any]) -> Release:
    """GitHub's release JSON, normalised."""
    return Release(
        tag=str(data.get("tag_name", "")),
        name=str(data.get("name") or ""),
        body=str(data.get("body") or ""),
        prerelease=bool(data.get("prerelease", False)),
        url=str(data.get("html_url", "")),
    )


#: Resolved at module scope: inside the issues classes the method
#: named `list` shadows the builtin in annotation scope.
_Rows = list[dict[str, Any]]


def _as_issue(data: Mapping[str, Any]) -> Issue:
    """GitHub's issue JSON, normalised."""
    state: ItemState = "open" if data.get("state") == "open" else "closed"
    return Issue(
        number=int(data["number"]),
        title=str(data.get("title", "")),
        body=str(data.get("body") or ""),
        state=state,
        labels=tuple(
            str(label.get("name", "")) for label in (data.get("labels") or [])
        ),
        assignees=tuple(
            str(person.get("login", "")) for person in (data.get("assignees") or [])
        ),
        url=str(data.get("html_url", "")),
    )


class _GithubIssues:
    """The issue operations of one GitHub repository.

    GitHub's issue endpoints serve pull requests too; listings filter
    them out and livery.forge.Issues.get answers None for a number
    that names a pull request, so the two spaces never mix.
    """

    def __init__(self, forge: GithubForge, client: JsonClient, base: str) -> None:
        self._forge = forge
        self._client = client
        self._base = base

    def _listing(self, query: str, *, state: StateFilter) -> _Rows:
        raw = self._client.paginate(
            lambda page: (
                self._client.request(
                    f"{self._base}/issues?state={state}{query}&page={page}&per_page=50"
                )
                or []
            ),
            subject=f"{self._base}/issues",
        )
        return [entry for entry in raw if "pull_request" not in entry]

    def create(
        self,
        title: str,
        *,
        body: str = "",
        labels: tuple[str, ...] = (),
        assignee: str = "",
    ) -> Issue:
        """Open an issue; GitHub takes label names directly."""
        data = self._client.request(
            f"{self._base}/issues",
            method="POST",
            data={
                "title": title,
                "body": body,
                "labels": list(labels),
                "assignees": [assignee] if assignee else [],
            },
        )
        return _as_issue(data)

    def get(self, number: int) -> Issue | None:
        """The issue *number*, or None; a pull request's number answers None."""
        data = self._client.request(f"{self._base}/issues/{number}", none_on=(404,))
        if data is None or "pull_request" in data:
            return None
        return _as_issue(data)

    def list(self, *, state: StateFilter = "open") -> tuple[Issue, ...]:
        """The repository's issues in *state*, oldest first."""
        issues = [_as_issue(raw) for raw in self._listing("", state=state)]
        issues.sort(key=lambda issue: issue.number)
        return tuple(issues)

    def search(
        self,
        text: str,
        *,
        state: StateFilter = "open",
        labels: tuple[str, ...] = (),
    ) -> tuple[Issue, ...]:
        """The issues whose title or body contains *text*.

        Matched client-side over the complete listing: GitHub's search
        API indexes asynchronously, and a probe that can miss what was
        just created is not a probe.
        """
        query = f"&labels={quote(','.join(labels))}" if labels else ""
        issues = [
            _as_issue(raw)
            for raw in self._listing(query, state=state)
            if text in str(raw.get("title", "")) or text in str(raw.get("body") or "")
        ]
        issues.sort(key=lambda issue: issue.number)
        return tuple(issues)

    def assign(self, number: int, assignee: str) -> None:
        """Make *assignee* the single assignee, replacing the list."""
        if self.get(number) is None:
            raise ForgeError(f"no issue {number} at {self._base}", status=404)
        self._client.request(
            f"{self._base}/issues/{number}",
            method="PATCH",
            data={"assignees": [assignee]},
        )

    def assigned_to_me(self) -> tuple[Issue, ...]:
        """The open issues assigned to the token's user."""
        me = self._forge.whoami()
        return tuple(
            _as_issue(raw)
            for raw in self._listing(f"&assignee={quote(me)}", state="open")
        )

    def comment(self, number: int, body: str) -> None:
        """Post *body* on issue *number*."""
        if self.get(number) is None:
            raise ForgeError(f"no issue {number} at {self._base}", status=404)
        self._client.request(
            f"{self._base}/issues/{number}/comments",
            method="POST",
            data={"body": body},
        )
