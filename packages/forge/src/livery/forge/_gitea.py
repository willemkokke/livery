"""The Gitea backend: livery.forge.Forge over Gitea's REST v1 API.

Server floor: the 1.28 line, the first with workflow-run cancellation.
Every other operation works on earlier servers; livery.forge.Checks.cancel_run
probes the version once and raises livery.forge.Unsupported naming it
when the server predates the endpoint.

Construction and the token rule: livery.forge.GiteaForge.connect
resolves the server once, an explicit ``url`` beating the configured
``GITEA_URL``, and reads ``GITEA_TOKEN`` unless a token is passed. The
token belongs to the configured host and no other:
livery.forge.gitea_is_configured_host is the test to apply before
constructing a client for a checkout's remote, and a foreign host is
read anonymously (``token=""``) instead of being sent a token it never
issued.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from livery.forge._errors import ForgeError, Unsupported
from livery.forge._http import JsonClient, Opener
from livery.forge._protocol import Checks, Issues, PullRequests, Releases, Repository
from livery.forge._types import (
    Capability,
    CheckState,
    Codeowners,
    CodeownersEntry,
    CombinedStatus,
    Conclusion,
    Issue,
    ItemState,
    Job,
    Label,
    Protection,
    PullRequest,
    Release,
    RepoConfig,
    RepoInfo,
    Review,
    ReviewState,
    Run,
    RunStatus,
    ScheduleEvent,
    ScheduleEventKind,
    StateFilter,
)

#: The first Gitea line with run cancellation, this backend's floor for
#: livery.forge.Checks.cancel_run.
CANCEL_FLOOR = (1, 28)


def gitea_configured_host() -> str:
    """The host ``GITEA_TOKEN`` belongs to, from ``GITEA_URL``, lowercased.

    Empty when no server is configured. Never a checkout's remote: the
    point is to have something to compare a remote against.
    """
    url = os.environ.get("GITEA_URL", "")
    return url.split("://", 1)[-1].strip("/").lower()


def gitea_is_configured_host(host: str) -> bool:
    """Whether *host* is the server this environment holds a token for.

    Apply this before constructing a client for a host taken from a
    checkout's remote; a foreign host is read with ``token=""`` rather
    than sent a token it never issued.
    """
    return bool(host) and host.lower() == gitea_configured_host()


def _version_pair(version: str) -> tuple[int, int]:
    """The leading major.minor of a Gitea version string, (0, 0) unparsed."""
    match = re.match(r"(\d+)\.(\d+)", version)
    if match is None:
        return (0, 0)
    return (int(match.group(1)), int(match.group(2)))


def _check_state(state: str, contexts: int) -> CheckState:
    """Gitea's combined-status state, normalised."""
    if contexts == 0:
        return "none"
    if state == "success":
        return "success"
    if state in ("failure", "error"):
        return "failure"
    return "pending"


_TERMINAL = {"success", "failure", "cancelled", "skipped", "stopped"}

_CONCLUSIONS: dict[str, Conclusion] = {
    "success": "success",
    "failure": "failure",
    "cancelled": "cancelled",
    "skipped": "skipped",
}


def _run_state(raw_status: str, raw_conclusion: str) -> tuple[RunStatus, Conclusion]:
    """Gitea's run or job status pair, normalised to the protocol's.

    Gitea reports terminal runs through ``status`` and sometimes also
    ``conclusion``; either way the verdict maps into the protocol's
    conclusions, an unknown terminal word reading as failure.
    """
    verdict = raw_conclusion or (raw_status if raw_status in _TERMINAL else "")
    if verdict:
        return ("completed", _CONCLUSIONS.get(verdict, "failure"))
    if raw_status == "running":
        return ("running", "")
    return ("queued", "")


class GiteaForge:
    """One Gitea server, spoken to through livery.forge.Forge's verbs.

    Build with livery.forge.GiteaForge.connect; the constructor takes
    the resolved values and applies no environment fallbacks.

    Args:
        api_base: The API root, ``<server>/api/v1``.
        token: The token sent on every request; empty reads anonymously.
        opener: The network seam; the redirect-refusing default when
            omitted.
    """

    def __init__(
        self, api_base: str, *, token: str, opener: Opener | None = None
    ) -> None:
        """Bind the client to *api_base* with *token*."""
        headers = {"Authorization": f"token {token}"} if token else {}
        headers["Accept"] = "application/json"
        self._client = JsonClient(api_base, headers=headers, opener=opener)
        self._version: str | None = None
        # Gitea serves the API under /api/v1 on the web host.
        self._web_root = api_base.rstrip("/").removesuffix("/api/v1")

    @classmethod
    def connect(
        cls,
        *,
        url: str = "",
        token: str | None = None,
        opener: Opener | None = None,
    ) -> GiteaForge:
        """The server, resolved once, never inferred from ambient state.

        An explicit *url* wins; ``GITEA_URL`` is the configured
        default. *token* defaults to ``GITEA_TOKEN``; a missing token
        raises rather than silently reading anonymously, because an
        unauthenticated write fails later and further from the cause.
        Pass ``token=""`` to read a foreign or public server
        anonymously on purpose.
        """
        web = url or os.environ.get("GITEA_URL", "")
        if not web:
            raise ForgeError(
                "no Gitea server to connect to: pass url= or set GITEA_URL"
            )
        resolved = os.environ.get("GITEA_TOKEN", "") if token is None else token
        if token is None and not resolved:
            raise ForgeError(
                'GITEA_TOKEN is not set: set it, or pass token="" to read'
                " anonymously on purpose"
            )
        return cls(f"{web.rstrip('/')}/api/v1", token=resolved, opener=opener)

    def whoami(self) -> str:
        """The authenticated user's login name (``GET /user``)."""
        data = self._client.request("/user")
        return str(data.get("login", ""))

    def server_version(self) -> str:
        """The server's version string (``GET /version``), cached."""
        if self._version is None:
            data = self._client.request("/version")
            self._version = str(data.get("version", ""))
        return self._version

    def supports(self, capability: Capability) -> bool:
        """Gitea offers every named capability."""
        return capability in (
            "auto_merge",
            "force_cancel",
            "required_contexts",
            "min_approvals",
            "ci_secrets",
            "schedule_events",
        )

    def repository(self, owner: str, name: str) -> Repository:
        """The view onto one repository. Cheap, no network."""
        return _GiteaRepository(self, self._client, owner, name)

    def members(self, owner: str) -> tuple[str, ...]:
        """The org's member logins; a user namespace is its one login."""
        try:
            rows = self._client.paginate(
                lambda page: self._client.request(
                    f"/orgs/{quote(owner)}/members?limit=50&page={page}"
                ),
                subject=f"/orgs/{owner}/members",
            )
        except ForgeError as error:
            if error.status == 404:
                return (owner,)
            raise
        return tuple(sorted(str(row.get("login", "")) for row in rows))

    def teams(self, owner: str) -> tuple[str, ...]:
        """The org's team names; a user namespace has none."""
        try:
            rows = self._client.paginate(
                lambda page: self._client.request(
                    f"/orgs/{quote(owner)}/teams?limit=50&page={page}"
                ),
                subject=f"/orgs/{owner}/teams",
            )
        except ForgeError as error:
            if error.status == 404:
                return ()
            raise
        return tuple(sorted(str(row.get("name", "")) for row in rows))

    def codeowners(self, entries: tuple[CodeownersEntry, ...]) -> Codeowners:
        """Gitea's dialect: ``.gitea/CODEOWNERS``, ``@owner`` lines.

        Same shape as GitHub's; a per-path approval count is
        approximated repository-wide through protection.
        """
        lines = []
        notes = []
        for entry in entries:
            owners = " ".join(f"@{name}" for name in entry.owners)
            lines.append(f"{entry.path} {owners}")
            if entry.min_approvals > 1:
                notes.append(
                    f"{entry.path}: {entry.min_approvals} approvals wanted;"
                    " Gitea expresses one repository-wide count through"
                    " protection, not per path"
                )
        return Codeowners(
            path=".gitea/CODEOWNERS",
            content="\n".join(lines) + "\n" if lines else "",
            notes=tuple(notes),
        )

    def user_url(self, login: str) -> str:
        """The address of *login*'s profile; nothing on the wire."""
        return f"{self._web_root}/{login}"

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
        requires. Creating under a user other than the token's own is
        not a thing Gitea offers and raises.
        """
        body = {
            "name": name,
            "private": private,
            "description": description,
            "auto_init": True,
        }
        if self._client.request(f"/orgs/{quote(owner)}", none_on=(404,)) is not None:
            self._client.request(
                f"/orgs/{quote(owner)}/repos", method="POST", data=body
            )
        elif owner == self.whoami():
            self._client.request("/user/repos", method="POST", data=body)
        else:
            raise ForgeError(
                f"cannot create {owner}/{name}: {owner} is neither an"
                " organisation nor the token's own user"
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
        """Delete the repository; one already gone is success."""
        self._client.request(
            f"/repos/{quote(owner)}/{quote(name)}", method="DELETE", none_on=(404,)
        )

    def _require_cancel_floor(self) -> None:
        """Raise unless the server has run cancellation (the 1.28 line)."""
        version = self.server_version()
        if _version_pair(version) < CANCEL_FLOOR:
            raise Unsupported(
                f"this Gitea is {version}, and run cancellation arrives in"
                f" {CANCEL_FLOOR[0]}.{CANCEL_FLOOR[1]}: upgrade the server"
            )


class _GiteaRepository:
    """The livery.forge.Repository view onto one Gitea repository."""

    def __init__(
        self, forge: GiteaForge, client: JsonClient, owner: str, name: str
    ) -> None:
        self._owner = owner
        self._name = name
        self._forge = forge
        self._client = client
        self._base = f"/repos/{quote(owner)}/{quote(name)}"
        self.pr: PullRequests = _GiteaPullRequests(client, self._base)
        self.checks: Checks = _GiteaChecks(forge, client, self._base)
        self.issue: Issues = _GiteaIssues(forge, client, self._base)
        self.release: Releases = _GiteaReleases(client, self._base)

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
                allow_merge_commits=False,
                allow_rebase=False,
                allow_rebase_explicit=False,
                default_merge_style="squash",
            )
        if config.delete_branch_on_merge is not None:
            patch["default_delete_branch_after_merge"] = config.delete_branch_on_merge
        if patch:
            self._client.request(self._base, method="PATCH", data=patch)
        # allow_auto_merge needs no switch: scheduling a merge is always
        # available on Gitea.
        if (
            config.required_contexts is not None
            or config.min_approvals is not None
            or config.require_codeowner_review is not None
        ):
            self._protect_default_branch(config)
        if config.secrets is not None:
            for key, value in config.secrets.items():
                self._client.request(
                    f"{self._base}/actions/secrets/{quote(key)}",
                    method="PUT",
                    data={"data": value},
                )
        if config.variables is not None:
            for key, value in config.variables.items():
                exists = self._client.request(
                    f"{self._base}/actions/variables/{quote(key)}", none_on=(404,)
                )
                method = "PUT" if exists is not None else "POST"
                self._client.request(
                    f"{self._base}/actions/variables/{quote(key)}",
                    method=method,
                    data={"value": value},
                )
        if config.labels is not None:
            self._ensure_labels(config.labels)

    def _protect_default_branch(self, config: RepoConfig) -> None:
        """Apply the protection half of *config* as one rule.

        Admins are always bound (``apply_to_admins``): a protection
        that exempts admins is a bypass nobody reviewed.
        """
        info = self._forge.get_repo(self._owner, self._name)
        if info is None:
            raise ForgeError(f"{self._owner}/{self._name} does not exist")
        branch = info.default_branch
        payload: dict[str, object] = {
            "branch_name": branch,
            "rule_name": branch,
            "apply_to_admins": True,
        }
        if config.required_contexts is not None:
            payload["enable_status_check"] = True
            payload["status_check_contexts"] = list(config.required_contexts)
        if config.min_approvals is not None:
            payload["required_approvals"] = config.min_approvals
        if config.require_codeowner_review is not None:
            # Gitea has no codeowner-approval switch; the nearest
            # honest lever is blocking on official review requests,
            # which codeowners files feed.
            payload["block_on_official_review_requests"] = (
                config.require_codeowner_review
            )
        existing = self._client.request(
            f"{self._base}/branch_protections/{quote(branch)}", none_on=(404,)
        )
        if existing is None:
            self._client.request(
                f"{self._base}/branch_protections", method="POST", data=payload
            )
        else:
            self._client.request(
                f"{self._base}/branch_protections/{quote(branch)}",
                method="PATCH",
                data=payload,
            )

    def _ensure_labels(self, labels: tuple[Label, ...]) -> None:
        """Create or update each label by name; none are ever deleted."""
        existing = _label_ids(self._client, self._base)
        for label in labels:
            body = {
                "name": label.name,
                "color": f"#{label.color.lstrip('#')}",
                "description": label.description,
            }
            if label.name in existing:
                self._client.request(
                    f"{self._base}/labels/{existing[label.name]}",
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
                    f"{self._base}/tags?page={page}&limit=50", none_on=(404,)
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

    def protection(self, branch: str) -> Protection | None:
        """The protection on *branch*, or None when none is configured."""
        data = self._client.request(
            f"{self._base}/branch_protections/{quote(branch, safe='')}",
            none_on=(404,),
        )
        if data is None:
            return None
        return Protection(
            required_approvals=int(data.get("required_approvals") or 0),
            # Gitea has no codeowner-approval toggle to read.
            require_codeowner_review=None,
            block_on_outdated=bool(data.get("block_on_outdated_branch")),
            block_on_rejected=bool(data.get("block_on_rejected_reviews")),
            required_contexts=tuple(
                str(c) for c in (data.get("status_check_contexts") or [])
            ),
        )

    def web_url(self) -> str:
        """The repository's home page; string building, nothing on the wire."""
        return f"{self._forge._web_root}/{self._owner}/{self._name}"

    def pr_url(self, number: int) -> str:
        """The address of pull request *number*."""
        return f"{self.web_url()}/pulls/{number}"

    def issue_url(self, number: int) -> str:
        """The address of issue *number*."""
        return f"{self.web_url()}/issues/{number}"

    def commit_url(self, sha: str) -> str:
        """The address of commit *sha*."""
        return f"{self.web_url()}/commit/{sha}"

    def compare_url(self, base: str, head: str) -> str:
        """The address comparing *base* to *head*."""
        return f"{self.web_url()}/compare/{base}...{head}"

    def tag_url(self, tag: str) -> str:
        """The address of *tag*'s release view."""
        return f"{self.web_url()}/releases/tag/{tag}"

    def delete_branch(self, branch: str) -> None:
        """Delete *branch*; one already gone is success."""
        self._client.request(
            f"{self._base}/branches/{quote(branch, safe='')}",
            method="DELETE",
            none_on=(404,),
        )


def _label_ids(client: JsonClient, base: str) -> dict[str, int]:
    """The repository's labels, name to id, complete or raising."""
    labels = client.paginate(
        lambda page: client.request(f"{base}/labels?page={page}&limit=50") or [],
        subject=f"{base}/labels",
    )
    return {str(label["name"]): int(label["id"]) for label in labels}


def _as_pull_request(data: Mapping[str, Any]) -> PullRequest:
    """Gitea's pull request JSON, normalised."""
    state: ItemState = "open" if data.get("state") == "open" else "closed"
    head = data.get("head") or {}
    base = data.get("base") or {}
    return PullRequest(
        number=int(data["number"]),
        title=str(data.get("title", "")),
        body=str(data.get("body") or ""),
        state=state,
        merged=bool(data.get("merged", False)),
        head_branch=str(head.get("ref") or ""),
        head_sha=str(head.get("sha") or ""),
        base_branch=str(base.get("ref") or ""),
        url=str(data.get("html_url", "")),
        author=str((data.get("user") or {}).get("login") or ""),
    )


class _GiteaPullRequests:
    """The pull request operations of one Gitea repository."""

    def __init__(self, client: JsonClient, base: str) -> None:
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
        """Every pull request in *state*, complete or raising.

        The listing is scanned client-side because Gitea's server-side
        ``head=`` filter has been observed returning every open pull
        request when the branch name carries a ``/``; matching here
        makes the answer correct whether or not the server honours the
        filter.
        """
        return self._client.paginate(
            lambda page: (
                self._client.request(
                    f"{self._base}/pulls?state={state}&page={page}&limit=50"
                )
                or []
            ),
            subject=f"{self._base}/pulls",
        )

    def find_by_head(
        self, branch: str, *, state: StateFilter = "open"
    ) -> PullRequest | None:
        """The pull request whose head branch is *branch*, or None."""
        for raw in self._scan(state):
            if (raw.get("head") or {}).get("ref") == branch:
                return _as_pull_request(raw)
        return None

    def find_by_head_sha(self, sha: str) -> PullRequest | None:
        """The pull request whose head commit is *sha*, or None."""
        for raw in self._scan("all"):
            if (raw.get("head") or {}).get("sha") == sha:
                return _as_pull_request(raw)
        return None

    def get(self, number: int) -> PullRequest | None:
        """The pull request *number*, or None."""
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
        """Squash-merge now; merging an already merged pull request is success.

        Gitea refuses the second merge with a 405, so the refusal is
        absorbed only after verifying the pull request really merged;
        every other 405 or 409 passes through verbatim.
        """
        try:
            self._client.request(
                f"{self._base}/pulls/{number}/merge",
                method="POST",
                data={
                    "Do": "squash",
                    "merge_title_field": title,
                    "merge_message_field": message,
                },
            )
        except ForgeError as exc:
            already = self.get(number)
            if exc.status == 405 and already is not None and already.merged:
                return
            raise

    def arm(self, number: int, *, title: str, message: str = "") -> None:
        """Schedule a squash merge for when the checks succeed."""
        self._client.request(
            f"{self._base}/pulls/{number}/merge",
            method="POST",
            data={
                "Do": "squash",
                "merge_when_checks_succeed": True,
                "merge_title_field": title,
                "merge_message_field": message,
            },
        )

    def disarm(self, number: int) -> bool:
        """Cancel a scheduled merge; 404 means nothing was scheduled."""
        result = self._client.request(
            f"{self._base}/pulls/{number}/merge", method="DELETE", none_on=(404,)
        )
        return result is not None

    def is_armed(self, number: int) -> bool:
        """Whether a merge is scheduled, read from the issue timeline.

        Gitea's pull request JSON carries no auto-merge field and the
        only endpoint touching the schedule cancels it, so the one
        read-only source is the timeline: arming records a
        ``pull_scheduled_merge`` event, cancelling records
        ``pull_cancel_scheduled_merge``, and a ``close`` or
        ``merge_pull`` event clears the armed state, because Gitea
        drops the schedule when the pull request leaves the open state
        whether or not it emits a cancel event.
        """
        events = self._client.paginate(
            lambda page: (
                self._client.request(
                    f"{self._base}/issues/{number}/timeline?page={page}&limit=50"
                )
                or []
            ),
            subject=f"{self._base}/issues/{number}/timeline",
        )
        armed = False
        for event in events:
            kind = event.get("type")
            if kind == "pull_scheduled_merge":
                armed = True
            elif kind in ("pull_cancel_scheduled_merge", "close", "merge_pull"):
                armed = False
        return armed

    def reviews(self, number: int) -> tuple[Review, ...]:
        """The submitted reviews on pull request *number*."""
        rows = self._client.request(f"{self._base}/pulls/{number}/reviews")
        out: list[Review] = []
        for row in rows or []:
            author = str((row.get("user") or {}).get("login") or "")
            state = str(row.get("state") or "")
            verdicts: dict[str, ReviewState] = {
                "APPROVED": "approved",
                "REQUEST_CHANGES": "changes_requested",
                "COMMENT": "commented",
            }
            verdict = verdicts.get(state)
            # PENDING is an unsubmitted draft and carries no verdict.
            if author and verdict is not None:
                out.append(Review(author=author, state=verdict))
        return tuple(out)

    def schedule_events(self, number: int) -> tuple[ScheduleEvent, ...]:
        """The merge-scheduling history, from the issue timeline."""
        events = self._client.paginate(
            lambda page: (
                self._client.request(
                    f"{self._base}/issues/{number}/timeline?page={page}&limit=50"
                )
                or []
            ),
            subject=f"{self._base}/issues/{number}/timeline",
        )
        kinds: dict[str, ScheduleEventKind] = {
            "pull_scheduled_merge": "scheduled",
            "pull_cancel_scheduled_merge": "unscheduled",
            "merge_pull": "merged",
            "close": "closed",
            "reopen": "reopened",
            "pull_push": "pushed",
        }
        out: list[ScheduleEvent] = []
        for event in events:
            kind = kinds.get(str(event.get("type") or ""))
            if kind is None:
                continue
            out.append(
                ScheduleEvent(
                    kind=kind,
                    actor=str((event.get("user") or {}).get("login") or ""),
                    created=str(event.get("created_at") or ""),
                )
            )
        return tuple(out)

    def comment(self, number: int, body: str) -> None:
        """Post *body* on pull request *number* (the issue comments API)."""
        self._client.request(
            f"{self._base}/issues/{number}/comments",
            method="POST",
            data={"body": body},
        )


class _GiteaChecks:
    """The CI operations of one Gitea repository."""

    def __init__(self, forge: GiteaForge, client: JsonClient, base: str) -> None:
        self._forge = forge
        self._client = client
        self._base = base

    def status(self, sha: str) -> CombinedStatus:
        """The combined commit status, ``none`` kept distinct from pending."""
        data = (
            self._client.request(
                f"{self._base}/commits/{quote(sha, safe='')}/status", none_on=(404,)
            )
            or {}
        )
        statuses = data.get("statuses") or []
        return CombinedStatus(
            state=_check_state(str(data.get("state", "")), len(statuses)),
            contexts=len(statuses),
        )

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
                    f"{self._base}/actions/runs?page={page}&limit=50{query}"
                ).get("workflow_runs")
                or []
            ),
            subject=f"{self._base}/actions/runs",
        )
        runs = []
        for entry in raw:
            status, conclusion = _run_state(
                str(entry.get("status", "")), str(entry.get("conclusion", ""))
            )
            runs.append(
                Run(
                    id=int(entry["id"]),
                    workflow=str(entry.get("path", "")).split("@", 1)[0],
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
                    f"{self._base}/actions/runs/{run}/jobs?page={page}&limit=50"
                ).get("jobs")
                or []
            ),
            subject=f"{self._base}/actions/runs/{run}/jobs",
        )
        jobs = []
        for entry in raw:
            status, conclusion = _run_state(
                str(entry.get("status", "")), str(entry.get("conclusion", ""))
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
        """The raw log text of one job."""
        text = self._client.text(f"{self._base}/actions/jobs/{job}/logs")
        return text or ""

    def rerun(self, run: int, *, failed_only: bool = True) -> None:
        """Re-run failed jobs, or the whole run; 409 while live passes through."""
        endpoint = "rerun-failed-jobs" if failed_only else "rerun"
        self._client.request(
            f"{self._base}/actions/runs/{run}/{endpoint}", method="POST"
        )

    def cancel_run(self, run: int, *, force: bool = False) -> None:
        """Cancel the run; both endpoints need the 1.28 floor."""
        self._forge._require_cancel_floor()
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


class _GiteaReleases:
    """The release operations of one Gitea repository."""

    def __init__(self, client: JsonClient, base: str) -> None:
        self._client = client
        self._base = base

    def create(
        self, tag: str, *, name: str, body: str = "", prerelease: bool = False
    ) -> Release:
        """Create the release for *tag*; an existing release is refused."""
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
    """Gitea's release JSON, normalised."""
    return Release(
        tag=str(data.get("tag_name", "")),
        name=str(data.get("name", "")),
        body=str(data.get("body") or ""),
        prerelease=bool(data.get("prerelease", False)),
        url=str(data.get("html_url", "")),
    )


#: Resolved at module scope: inside the issues classes the method
#: named `list` shadows the builtin in annotation scope.
_Rows = list[dict[str, Any]]


def _as_issue(data: Mapping[str, Any]) -> Issue:
    """Gitea's issue JSON, normalised."""
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


class _GiteaIssues:
    """The issue operations of one Gitea repository.

    Gitea's issue endpoints serve pull requests too; every listing here
    passes ``type=issues`` and livery.forge.Issues.get answers None for
    a number that names a pull request, so the two spaces never mix.
    """

    def __init__(self, forge: GiteaForge, client: JsonClient, base: str) -> None:
        self._forge = forge
        self._client = client
        self._base = base

    def _listing(self, query: str, *, state: StateFilter) -> _Rows:
        return self._client.paginate(
            lambda page: (
                self._client.request(
                    f"{self._base}/issues?state={state}&type=issues"
                    f"{query}&page={page}&limit=50"
                )
                or []
            ),
            subject=f"{self._base}/issues",
        )

    def create(
        self,
        title: str,
        *,
        body: str = "",
        labels: tuple[str, ...] = (),
        assignee: str = "",
    ) -> Issue:
        """Open an issue; label names resolve to ids at this boundary."""
        ids = _label_ids(self._client, self._base)
        missing = [label for label in labels if label not in ids]
        if missing:
            raise ForgeError(
                f"labels do not exist on the repository: {', '.join(missing)}."
                " Configure them first."
            )
        data = self._client.request(
            f"{self._base}/issues",
            method="POST",
            data={
                "title": title,
                "body": body,
                "labels": [ids[label] for label in labels],
                "assignees": [assignee] if assignee else [],
            },
        )
        return _as_issue(data)

    def get(self, number: int) -> Issue | None:
        """The issue *number*, or None; a pull request's number answers None."""
        data = self._client.request(f"{self._base}/issues/{number}", none_on=(404,))
        if data is None or data.get("pull_request"):
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

        The label filter is server-side; the text match is client-side
        over the complete listing, because the protocol promises a
        body match and Gitea's ``q`` filter does not.
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
        """Add *assignee* to the issue's assignees.

        Gitea's PATCH replaces the whole list, so the add is a
        read-modify-write over the current assignees: two adds racing
        can lose one, and the caller's assignee policy is what keeps
        concurrent assignment rare.
        """
        issue = self.get(number)
        if issue is None:
            raise ForgeError(f"no issue {number} at {self._base}", status=404)
        assignees = list(issue.assignees)
        if assignee not in assignees:
            assignees.append(assignee)
        self._client.request(
            f"{self._base}/issues/{number}",
            method="PATCH",
            data={"assignees": assignees},
        )

    def unassign(self, number: int) -> None:
        """Remove the authenticated user from the issue's assignees."""
        issue = self.get(number)
        if issue is None:
            raise ForgeError(f"no issue {number} at {self._base}", status=404)
        me = self._forge.whoami()
        if me not in issue.assignees:
            return
        self._client.request(
            f"{self._base}/issues/{number}",
            method="PATCH",
            data={"assignees": [name for name in issue.assignees if name != me]},
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
        self._client.request(
            f"{self._base}/issues/{number}/comments",
            method="POST",
            data={"body": body},
        )

    def close(self, number: int) -> None:
        """Close issue *number*; a closed issue stays closed."""
        if self.get(number) is None:
            raise ForgeError(f"no issue {number} at {self._base}", status=404)
        self._client.request(
            f"{self._base}/issues/{number}",
            method="PATCH",
            data={"state": "closed"},
        )
