"""The GitLab backend: livery.forge.Forge over REST v4.

The odd one out, and the reason several protocol rules exist: merge
requests and issues are addressed by their per-project iid (which is
the number a person sees, so the protocol's number is the iid and the
global id never crosses the boundary), pipelines are the canonical CI
answer rather than commit statuses, and the owner may be a group or
subgroup path. The full endpoint mapping is the package's
``docs/gitlab.md``.

Construction and the token rule: livery.forge.GitlabForge.connect
resolves the server once, an explicit ``url`` beating the configured
``GITLAB_URL``, and reads ``GITLAB_TOKEN`` unless a token is passed.
The token belongs to the configured host and no other.

Capabilities: ``auto_merge`` (merge when pipeline succeeds),
``ci_secrets`` (masked variables), and ``schedule_events``
(reconstructed from system notes and state events) are supported;
``min_approvals`` is probed per instance, because GitLab
licence-gates approval rules. ``force_cancel`` and
``required_contexts`` are declined by name: pipelines have plain
cancel only, and no tier's protection names required check contexts.
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
    Run,
    RunStatus,
    ScheduleEvent,
    ScheduleEventKind,
    StateFilter,
)


def gitlab_configured_host() -> str:
    """The host ``GITLAB_TOKEN`` belongs to, from ``GITLAB_URL``, lowercased."""
    url = os.environ.get("GITLAB_URL", "")
    return url.split("://", 1)[-1].strip("/").lower()


def gitlab_is_configured_host(host: str) -> bool:
    """Whether *host* is the server this environment holds a token for."""
    return bool(host) and host.lower() == gitlab_configured_host()


_TERMINAL = {"success", "failed", "canceled", "skipped"}

_CONCLUSIONS: dict[str, Conclusion] = {
    "success": "success",
    "failed": "failure",
    "canceled": "cancelled",
    "skipped": "skipped",
}


def _run_state(raw_status: str) -> tuple[RunStatus, Conclusion]:
    """GitLab's pipeline or job status word, normalised to the protocol's.

    Cancellation is asynchronous: a pipeline reads ``canceling``
    until its jobs acknowledge, and that is a live state, not a
    verdict.
    """
    if raw_status in _TERMINAL:
        return ("completed", _CONCLUSIONS[raw_status])
    if raw_status in ("running", "canceling"):
        return ("running", "")
    return ("queued", "")


_EVENTS = {
    "api": "workflow_dispatch",
    "trigger": "workflow_dispatch",
    "web": "workflow_dispatch",
}


def _maskable(value: str) -> bool:
    """Whether GitLab's masking rules accept *value* as a masked variable."""
    return len(value) >= 8 and re.fullmatch(r"[A-Za-z0-9+/=@:.~_-]+", value) is not None


class GitlabForge:
    """One GitLab server, spoken to through livery.forge.Forge's verbs.

    Build with livery.forge.GitlabForge.connect; the constructor takes
    the resolved values and applies no environment fallbacks.

    Args:
        api_base: The API root, ``<server>/api/v4``.
        token: The token sent on every request; empty reads anonymously.
        opener: The network seam; the redirect-refusing default when
            omitted.
    """

    def __init__(
        self, api_base: str, *, token: str, opener: Opener | None = None
    ) -> None:
        """Bind the client to *api_base* with *token*.

        The timeout is generous: a single-node GitLab under load
        legitimately takes tens of seconds for its heaviest writes
        (project creation), and "slow" must stay distinguishable from
        "unreachable".
        """
        headers = {"PRIVATE-TOKEN": token} if token else {}
        self._client = JsonClient(api_base, headers=headers, opener=opener, timeout=120)
        # GitLab serves the API under /api/v4 on the web host.
        self._web_root = api_base.rstrip("/").removesuffix("/api/v4")
        #: The licence probe's cached answer; None until first asked.
        self._min_approvals: bool | None = None

    @classmethod
    def connect(
        cls,
        *,
        url: str = "",
        token: str | None = None,
        opener: Opener | None = None,
    ) -> GitlabForge:
        """The server, resolved once, never inferred from ambient state.

        An explicit *url* wins; ``GITLAB_URL`` is the configured
        default. *token* defaults to ``GITLAB_TOKEN``; a missing token
        raises rather than silently reading anonymously. Pass
        ``token=""`` to read anonymously on purpose.
        """
        web = url or os.environ.get("GITLAB_URL", "")
        if not web:
            raise ForgeError(
                "no GitLab server to connect to: pass url= or set GITLAB_URL"
            )
        resolved = os.environ.get("GITLAB_TOKEN", "") if token is None else token
        if token is None and not resolved:
            raise ForgeError(
                'GITLAB_TOKEN is not set: set it, or pass token="" to read'
                " anonymously on purpose"
            )
        return cls(f"{web.rstrip('/')}/api/v4", token=resolved, opener=opener)

    def whoami(self) -> str:
        """The authenticated user's login name (``GET /user``)."""
        data = self._client.request("/user")
        return str(data.get("username", ""))

    def server_version(self) -> str:
        """The server's version string (``GET /version``)."""
        data = self._client.request("/version")
        return str(data.get("version", ""))

    def supports(self, capability: Capability) -> bool:
        """Honest per instance: approvals depend on the licence.

        ``auto_merge``, ``ci_secrets``, and ``schedule_events`` are
        every tier's. ``min_approvals`` is probed once per connection
        and cached: GitLab licence-gates approval rules, so the first
        ask reads a visible project's approval-rules endpoint, which
        an unlicensed server answers with 404. ``force_cancel`` and
        ``required_contexts`` are declined at every tier: no tier
        cancels a run harder than plain cancel, and no tier's
        protection names check contexts.
        """
        if capability == "min_approvals":
            return self._probe_min_approvals()
        return capability in ("auto_merge", "ci_secrets", "schedule_events")

    def _probe_min_approvals(self) -> bool:
        """Whether this instance's licence grants approval rules.

        The rules API is project-scoped, so the probe reads through
        the first project the token can see. A token that sees no
        project answers no: the capability cannot be exercised where
        it cannot even be probed.
        """
        answer = self._min_approvals
        if answer is None:
            projects = (
                self._client.request("/projects?membership=true&per_page=1") or []
            )
            if not projects:
                answer = False
            else:
                rules = self._client.request(
                    f"/projects/{int(projects[0]['id'])}/approval_rules?per_page=1",
                    none_on=(403, 404),
                )
                answer = rules is not None
            self._min_approvals = answer
        return answer

    def repository(self, owner: str, name: str) -> Repository:
        """The view onto one repository. Cheap, no network."""
        return _GitlabRepository(self, self._client, owner, name)

    def members(self, owner: str) -> tuple[str, ...]:
        """The group's member usernames; a user namespace is its login."""
        try:
            rows = self._client.paginate(
                lambda page: self._client.request(
                    f"/groups/{quote(owner, safe='')}/members?per_page=100&page={page}"
                ),
                subject=f"/groups/{owner}/members",
            )
        except ForgeError as error:
            if error.status == 404:
                return (owner,)
            raise
        return tuple(sorted(str(row.get("username", "")) for row in rows))

    def teams(self, owner: str) -> tuple[str, ...]:
        """GitLab's teams are its subgroups: their full paths.

        A personal namespace has no subgroups and answers empty.
        """
        try:
            rows = self._client.paginate(
                lambda page: self._client.request(
                    f"/groups/{quote(owner, safe='')}/subgroups?per_page=100"
                    f"&page={page}"
                ),
                subject=f"/groups/{owner}/subgroups",
            )
        except ForgeError as error:
            if error.status == 404:
                return ()
            raise
        return tuple(sorted(str(row.get("full_path", "")) for row in rows))

    def codeowners(self, entries: tuple[CodeownersEntry, ...]) -> Codeowners:
        """GitLab's dialect: ``.gitlab/CODEOWNERS`` with sections.

        A section headed ``[name][n]`` carries the per-path approval
        count in the file itself, the one dialect that can; nothing
        is approximated. Every entry after a heading belongs to that
        section until the next one, so the plain entries render
        first and each counted entry gets its own section after
        them: order in the input can never leak a count onto a
        neighbour. Enforcing the counts still needs a paid tier,
        which the ``min_approvals`` capability declines.
        """
        plain = [entry for entry in entries if entry.min_approvals <= 1]
        counted = [entry for entry in entries if entry.min_approvals > 1]
        lines = []
        for entry in plain:
            owners = " ".join(f"@{name}" for name in entry.owners)
            lines.append(f"{entry.path} {owners}")
        for index, entry in enumerate(counted, 1):
            owners = " ".join(f"@{name}" for name in entry.owners)
            lines.append(f"[owners-{index}][{entry.min_approvals}]")
            lines.append(f"{entry.path} {owners}")
        return Codeowners(
            path=".gitlab/CODEOWNERS",
            content="\n".join(lines) + "\n" if lines else "",
            notes=(),
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
        """Create the project under a group path or the token's own user.

        ``initialize_with_readme`` gives it the default branch the
        protocol requires. A group or subgroup owner resolves to its
        namespace id first.
        """
        body: dict[str, Any] = {
            "name": name,
            "path": name,
            "visibility": "private" if private else "public",
            "description": description,
            "initialize_with_readme": True,
        }
        if owner != self.whoami():
            body["namespace_id"] = self._namespace_id(owner)
        self._client.request("/projects", method="POST", data=body)
        return self.repository(owner, name)

    def _namespace_id(self, owner: str) -> int:
        """The namespace id for a group or subgroup path."""
        matches = self._client.request(f"/namespaces?search={quote(owner)}")
        for entry in matches or []:
            if entry.get("full_path") == owner:
                return int(entry["id"])
        raise ForgeError(f"no namespace with path {owner} on this GitLab")

    def get_repo(self, owner: str, name: str) -> RepoInfo | None:
        """The project's settings, or None when it does not exist.

        A deleting project is renamed to a corpse path and the old
        path answers as moved for a moment; a redirect here therefore
        means "no project at this path" and reads as None, never
        followed.
        """
        try:
            data = self._client.request(
                f"/projects/{_path(owner, name)}", none_on=(404,)
            )
        except ForgeError as exc:
            if exc.status is not None and 300 <= exc.status < 400:
                return None
            raise
        if data is None:
            return None
        if str(data.get("path_with_namespace")) != f"{owner}/{name}":
            # A redirect route answered for the old path: the project
            # that came back is a renamed corpse, not the one asked
            # for. The subject of the answer is verified, never
            # assumed.
            return None
        return RepoInfo(
            owner=owner,
            name=name,
            default_branch=str(data.get("default_branch") or ""),
            private=str(data.get("visibility")) == "private",
        )

    def delete_repo(self, owner: str, name: str) -> None:
        """Delete the project; one already gone is success.

        GitLab deletes asynchronously: the project is renamed to a
        corpse path, the old path answers 405 ("Non GET methods are
        not allowed for moved projects") until the rename lands, and
        the path can stay reserved for a short while after. The 405 on
        a moved project IS the already-deleting case, so it reads as
        success; a creator reusing a just-deleted path retries on the
        refusal.
        """
        try:
            self._client.request(
                f"/projects/{_path(owner, name)}", method="DELETE", none_on=(404,)
            )
        except ForgeError as exc:
            already_deleting = exc.status == 405 and "moved" in exc.detail
            already_marked = exc.status == 400 and "marked for deletion" in exc.detail
            if already_deleting or already_marked:
                return
            raise


def _path(owner: str, name: str) -> str:
    """The URL-encoded project path GitLab addresses everything by."""
    return quote(f"{owner}/{name}", safe="")


class _GitlabRepository:
    """The livery.forge.Repository view onto one GitLab project."""

    def __init__(
        self, forge: GitlabForge, client: JsonClient, owner: str, name: str
    ) -> None:
        self._owner = owner
        self._name = name
        self._forge = forge
        self._client = client
        self._base = f"/projects/{_path(owner, name)}"
        self.pr: PullRequests = _GitlabPullRequests(client, self._base)
        self.checks: Checks = _GitlabChecks(client, self._base)
        self.issue: Issues = _GitlabIssues(forge, client, self._base)
        self.release: Releases = _GitlabReleases(client, self._base)

    @property
    def owner(self) -> str:
        """The group path or user the view is bound to."""
        return self._owner

    @property
    def name(self) -> str:
        """The project name the view is bound to."""
        return self._name

    def ensure_pages(self, *, build_type: str = "workflow") -> None:
        """Decline: GitLab Pages rides pipeline artifacts, not a config API.

        The capability is ``pages_config``.
        """
        raise Unsupported(
            "gitlab cannot enable pages hosting through the API"
            " (capability: pages_config)"
        )

    def configure(self, config: RepoConfig) -> None:
        """Assert the stated settings; every step probes before acting."""
        if config.min_approvals is not None or (
            config.require_codeowner_review is not None
        ):
            # Refused before anything else applies: assert nothing
            # you will refuse, and the verb stays idempotent for a
            # caller that probes supports() first.
            if not self._forge.supports("min_approvals"):
                raise Unsupported(
                    "this instance cannot require approving reviews"
                    " (capability: min_approvals): GitLab licence-gates"
                    " approval rules, and this server's licence does not"
                    " grant them"
                )
            self._configure_approvals(config)
        patch: dict[str, Any] = {}
        if config.default_branch is not None:
            patch["default_branch"] = config.default_branch
        if config.squash_only is not None and config.squash_only:
            patch["squash_option"] = "always"
        if config.delete_branch_on_merge is not None:
            patch["remove_source_branch_after_merge"] = config.delete_branch_on_merge
        # allow_auto_merge needs no switch: merge when pipeline
        # succeeds is always available on GitLab.
        if patch:
            self._client.request(self._base, method="PUT", data=patch)
        if config.required_contexts is not None:
            raise Unsupported(
                "GitLab protection cannot name required check contexts"
                " (capability: required_contexts): the nearest fact, only"
                " allowing merges when the pipeline succeeds, is a boolean"
            )
        if config.secrets is not None:
            for key, value in config.secrets.items():
                self._put_variable(key, value, masked=_maskable(value))
        if config.variables is not None:
            for key, value in config.variables.items():
                self._put_variable(key, value, masked=False)
        if config.labels is not None:
            self._ensure_labels(config.labels)

    def _configure_approvals(self, config: RepoConfig) -> None:
        """Apply the approvals half on a licensed instance.

        The count lands on the project's any-approver approval rule,
        created or updated after a read. The codeowner requirement is
        a field on the protected default branch, which is protected
        first when it is not yet; only that field is ever written, so
        an existing protection's access levels stay untouched.
        """
        if config.min_approvals is not None:
            rules = self._client.request(f"{self._base}/approval_rules") or []
            any_rule = next(
                (
                    rule
                    for rule in rules
                    if str(rule.get("rule_type")) == "any_approver"
                ),
                None,
            )
            if any_rule is None:
                self._client.request(
                    f"{self._base}/approval_rules",
                    method="POST",
                    data={
                        "name": "Any approver",
                        "rule_type": "any_approver",
                        "approvals_required": config.min_approvals,
                    },
                )
            elif int(any_rule.get("approvals_required") or 0) != config.min_approvals:
                self._client.request(
                    f"{self._base}/approval_rules/{int(any_rule['id'])}",
                    method="PUT",
                    data={"approvals_required": config.min_approvals},
                )
        if config.require_codeowner_review is not None:
            info = self._forge.get_repo(self._owner, self._name)
            if info is None:
                raise ForgeError(f"{self._owner}/{self._name} does not exist")
            branch = info.default_branch
            record = self._client.request(
                f"{self._base}/protected_branches/{quote(branch, safe='')}",
                none_on=(404,),
            )
            wanted = config.require_codeowner_review
            if record is None:
                self._client.request(
                    f"{self._base}/protected_branches",
                    method="POST",
                    data={"name": branch, "code_owner_approval_required": wanted},
                )
            elif bool(record.get("code_owner_approval_required")) != wanted:
                self._client.request(
                    f"{self._base}/protected_branches/{quote(branch, safe='')}",
                    method="PATCH",
                    data={"code_owner_approval_required": wanted},
                )

    def _put_variable(self, key: str, value: str, *, masked: bool) -> None:
        """Create or update one CI variable; masked when the rules allow.

        A secret whose value fails GitLab's masking rules is stored
        unmasked by construction, never downgraded silently at
        request time: the maskability decision is taken here, once.
        """
        exists = self._client.request(
            f"{self._base}/variables/{quote(key, safe='')}", none_on=(404,)
        )
        body = {"key": key, "value": value, "masked": masked}
        if exists is None:
            self._client.request(f"{self._base}/variables", method="POST", data=body)
        else:
            self._client.request(
                f"{self._base}/variables/{quote(key, safe='')}",
                method="PUT",
                data=body,
            )

    def _ensure_labels(self, labels: tuple[Label, ...]) -> None:
        """Create or update each label by name; none are ever deleted."""
        existing = {
            str(label["name"]): int(label["id"])
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
                "color": f"#{label.color.lstrip('#')}",
                "description": label.description,
            }
            if label.name in existing:
                self._client.request(
                    f"{self._base}/labels/{existing[label.name]}",
                    method="PUT",
                    data=body,
                )
            else:
                self._client.request(f"{self._base}/labels", method="POST", data=body)

    def tags(self) -> tuple[str, ...]:
        """Every tag name, complete or raising."""
        tags = self._client.paginate(
            lambda page: (
                self._client.request(
                    f"{self._base}/repository/tags?page={page}&per_page=50",
                    none_on=(404,),
                )
                or []
            ),
            subject=f"{self._base}/repository/tags",
        )
        return tuple(str(tag["name"]) for tag in tags)

    def branch_exists(self, branch: str) -> bool:
        """Whether *branch* exists."""
        return (
            self._client.request(
                f"{self._base}/repository/branches/{quote(branch, safe='')}",
                none_on=(404,),
            )
            is not None
        )

    def protection(self, branch: str) -> Protection | None:
        """The protection on *branch*, or None when the branch is open.

        GitLab splits the story across endpoints and licence tiers:
        the protected-branch record says the branch is guarded and
        carries the codeowner requirement, the approval rules say how
        many approvals a merge needs (the highest count over the
        rules; an unlicensed server has none and reads as zero).
        What cannot be read reads as inert, per
        livery.forge.Protection.
        """
        record = self._client.request(
            f"{self._base}/protected_branches/{quote(branch, safe='')}",
            none_on=(404,),
        )
        if record is None:
            return None
        rules = (
            self._client.request(f"{self._base}/approval_rules", none_on=(403, 404))
            or []
        )
        return Protection(
            required_approvals=max(
                (int(rule.get("approvals_required") or 0) for rule in rules),
                default=0,
            ),
            require_codeowner_review=bool(record.get("code_owner_approval_required"))
            or None,
            block_on_outdated=False,
            block_on_rejected=False,
            required_contexts=(),
        )

    def web_url(self) -> str:
        """The project's home page; string building, nothing on the wire."""
        return f"{self._forge._web_root}/{self._owner}/{self._name}"

    def pr_url(self, number: int) -> str:
        """The address of merge request *number*."""
        return f"{self.web_url()}/-/merge_requests/{number}"

    def issue_url(self, number: int) -> str:
        """The address of issue *number*."""
        return f"{self.web_url()}/-/issues/{number}"

    def commit_url(self, sha: str) -> str:
        """The address of commit *sha*."""
        return f"{self.web_url()}/-/commit/{sha}"

    def compare_url(self, base: str, head: str) -> str:
        """The address comparing *base* to *head*."""
        return f"{self.web_url()}/-/compare/{base}...{head}"

    def tag_url(self, tag: str) -> str:
        """The address of *tag*'s tag view."""
        return f"{self.web_url()}/-/tags/{tag}"

    def delete_branch(self, branch: str) -> None:
        """Delete *branch*; one already gone is success."""
        self._client.request(
            f"{self._base}/repository/branches/{quote(branch, safe='')}",
            method="DELETE",
            none_on=(404,),
        )


def _as_pull_request(data: Mapping[str, Any]) -> PullRequest:
    """GitLab's merge request JSON, normalised; the number is the iid."""
    raw_state = str(data.get("state", ""))
    state: ItemState = "open" if raw_state == "opened" else "closed"
    return PullRequest(
        number=int(data["iid"]),
        title=str(data.get("title", "")),
        body=str(data.get("description") or ""),
        state=state,
        merged=raw_state == "merged",
        head_branch=str(data.get("source_branch") or ""),
        head_sha=str(data.get("sha") or ""),
        base_branch=str(data.get("target_branch") or ""),
        url=str(data.get("web_url", "")),
        author=str((data.get("author") or {}).get("username") or ""),
    )


class _GitlabPullRequests:
    """The merge request operations of one GitLab project."""

    def __init__(self, client: JsonClient, base: str) -> None:
        self._client = client
        self._base = base

    def _delete_source_branch(self) -> bool:
        """Whether the project wants source branches deleted on merge.

        The project setting only pre-fills the checkbox for merge
        requests created in the UI; an API merge deletes the branch
        only when the merge call itself says so, so the setting is
        read here and passed through.
        """
        data = self._client.request(self._base) or {}
        return bool(data.get("remove_source_branch_after_merge"))

    def open(self, head: str, base: str, title: str, body: str = "") -> PullRequest:
        """Open a merge request; a duplicate open source branch is refused."""
        data = self._client.request(
            f"{self._base}/merge_requests",
            method="POST",
            data={
                "source_branch": head,
                "target_branch": base,
                "title": title,
                "description": body,
            },
        )
        return _as_pull_request(data)

    def _scan(self, query: str) -> list[dict[str, Any]]:
        return self._client.paginate(
            lambda page: (
                self._client.request(
                    f"{self._base}/merge_requests?state=all"
                    f"{query}&page={page}&per_page=50"
                )
                or []
            ),
            subject=f"{self._base}/merge_requests",
        )

    def find_by_head(
        self, branch: str, *, state: StateFilter = "open"
    ) -> PullRequest | None:
        """The merge request whose source branch is *branch*, or None.

        The protocol's ``closed`` filter takes GitLab's closed and
        merged states both.
        """
        for raw in self._scan(f"&source_branch={quote(branch)}"):
            candidate = _as_pull_request(raw)
            if candidate.head_branch == branch and _matches(candidate, state):
                return candidate
        return None

    def find_by_head_sha(self, sha: str) -> PullRequest | None:
        """The merge request whose head commit is *sha*, or None."""
        for raw in self._scan(""):
            if raw.get("sha") == sha:
                return _as_pull_request(raw)
        return None

    def get(self, number: int) -> PullRequest | None:
        """The merge request *number* (its iid), or None."""
        data = self._client.request(
            f"{self._base}/merge_requests/{number}", none_on=(404,)
        )
        return None if data is None else _as_pull_request(data)

    def update_title(self, number: int, title: str) -> None:
        """Retitle merge request *number*."""
        self._client.request(
            f"{self._base}/merge_requests/{number}",
            method="PUT",
            data={"title": title},
        )

    def close(self, number: int) -> None:
        """Close merge request *number* without merging."""
        self._client.request(
            f"{self._base}/merge_requests/{number}",
            method="PUT",
            data={"state_event": "close"},
        )

    def reopen(self, number: int) -> None:
        """Reopen the closed, unmerged merge request *number*."""
        self._client.request(
            f"{self._base}/merge_requests/{number}",
            method="PUT",
            data={"state_event": "reopen"},
        )

    def merge_now(self, number: int, *, title: str, message: str = "") -> None:
        """Squash-merge now; merging an already merged request is success.

        GitLab refuses the second merge with a 405, so the refusal is
        absorbed only after verifying the merge request really merged;
        every other 405 passes through verbatim. The head sha rides
        along: newer GitLab refuses a merge without one ("SHA must be
        provided when merging"), and pinning it also means the merge
        takes exactly the head this call read, never a racing push.
        """
        squash_message = f"{title}\n\n{message}" if message else title
        current = self.get(number)
        if current is None:
            raise ForgeError(f"merge request {number} does not exist", status=404)
        try:
            self._client.request(
                f"{self._base}/merge_requests/{number}/merge",
                method="PUT",
                data={
                    "sha": current.head_sha,
                    "squash": True,
                    "squash_commit_message": squash_message,
                    "should_remove_source_branch": self._delete_source_branch(),
                },
            )
        except ForgeError as exc:
            already = self.get(number)
            if exc.status == 405 and already is not None and already.merged:
                return
            raise

    def arm(self, number: int, *, title: str, message: str = "") -> None:
        """Merge when the pipeline succeeds, on the merge request itself.

        GitLab refuses the schedule while no pipeline is running (a
        405), so arming a just-pushed merge request can race the
        pipeline's creation; the refusal passes through verbatim for
        the caller to act on. The head sha rides along, as on
        livery.forge.PullRequests.merge_now.
        """
        squash_message = f"{title}\n\n{message}" if message else title
        current = self.get(number)
        if current is None:
            raise ForgeError(f"merge request {number} does not exist", status=404)
        self._client.request(
            f"{self._base}/merge_requests/{number}/merge",
            method="PUT",
            data={
                "sha": current.head_sha,
                "merge_when_pipeline_succeeds": True,
                "squash": True,
                "squash_commit_message": squash_message,
                "should_remove_source_branch": self._delete_source_branch(),
            },
        )

    def disarm(self, number: int) -> bool:
        """Cancel the schedule; False when nothing was scheduled."""
        if not self.is_armed(number):
            return False
        self._client.request(
            f"{self._base}/merge_requests/{number}/cancel_merge_when_pipeline_succeeds",
            method="POST",
        )
        return True

    def is_armed(self, number: int) -> bool:
        """Readable state on the merge request itself: no timeline walk."""
        data = self._client.request(
            f"{self._base}/merge_requests/{number}", none_on=(404,)
        )
        if data is None:
            raise ForgeError(f"no merge request {number} at {self._base}", status=404)
        return data.get("state") == "opened" and bool(
            data.get("merge_when_pipeline_succeeds")
        )

    def reviews(self, number: int) -> tuple[Review, ...]:
        """The approvals on merge request *number*, as reviews.

        GitLab's review model is approvals: an approver appears here
        as an ``approved`` review. Requested changes have no
        first-class shape on GitLab, so none are ever reported.
        """
        data = self._client.request(
            f"{self._base}/merge_requests/{number}/approvals", none_on=(404,)
        )
        if data is None:
            return ()
        out: list[Review] = []
        for row in data.get("approved_by") or []:
            login = str((row.get("user") or {}).get("username") or "")
            if login:
                out.append(Review(author=login, state="approved"))
        return tuple(out)

    def schedule_events(self, number: int) -> tuple[ScheduleEvent, ...]:
        """The merge-scheduling history, from notes and state events.

        GitLab keeps no dedicated timeline; the system notes carry
        the schedule ("enabled an automatic merge ..." when set,
        "canceled ..." or "aborted ..." when lost) and the resource
        state events carry merged, closed, and reopened. Both are
        read and merged oldest first by timestamp. The note wording
        is a parsing contract with the server; a wording change
        breaks this read, never the schedule itself.
        """
        notes = self._client.paginate(
            lambda page: (
                self._client.request(
                    f"{self._base}/merge_requests/{number}/notes"
                    f"?page={page}&per_page=50&sort=asc"
                )
                or []
            ),
            subject=f"{self._base}/merge_requests/{number}/notes",
        )
        out: list[tuple[str, ScheduleEvent]] = []
        for note in notes:
            if not note.get("system"):
                continue
            body = str(note.get("body") or "")
            kind: ScheduleEventKind | None = None
            if body.startswith("enabled an automatic merge"):
                kind = "scheduled"
            elif body.startswith(
                ("canceled the automatic merge", "aborted the automatic merge")
            ):
                kind = "unscheduled"
            elif body.startswith("added ") and "commit" in body:
                kind = "pushed"
            if kind is None:
                continue
            created = str(note.get("created_at") or "")
            out.append(
                (
                    created,
                    ScheduleEvent(
                        kind=kind,
                        actor=str((note.get("author") or {}).get("username") or ""),
                        created=created,
                    ),
                )
            )
        states = self._client.paginate(
            lambda page: (
                self._client.request(
                    f"{self._base}/merge_requests/{number}/resource_state_events"
                    f"?page={page}&per_page=50"
                )
                or []
            ),
            subject=f"{self._base}/merge_requests/{number}/resource_state_events",
        )
        kinds: dict[str, ScheduleEventKind] = {
            "merged": "merged",
            "closed": "closed",
            "reopened": "reopened",
        }
        for event in states:
            kind_mapped = kinds.get(str(event.get("state") or ""))
            if kind_mapped is None:
                continue
            created = str(event.get("created_at") or "")
            out.append(
                (
                    created,
                    ScheduleEvent(
                        kind=kind_mapped,
                        actor=str((event.get("user") or {}).get("username") or ""),
                        created=created,
                    ),
                )
            )
        out.sort(key=lambda pair: pair[0])
        return tuple(event for _, event in out)

    def comment(self, number: int, body: str) -> None:
        """Post *body* as a note on merge request *number*."""
        self._client.request(
            f"{self._base}/merge_requests/{number}/notes",
            method="POST",
            data={"body": body},
        )


def _matches(pr: PullRequest, wanted: StateFilter) -> bool:
    """Whether *pr* passes the protocol's state filter."""
    return wanted == "all" or pr.state == wanted


class _GitlabChecks:
    """The CI operations of one GitLab project: pipelines are the answer."""

    def __init__(self, client: JsonClient, base: str) -> None:
        self._client = client
        self._base = base

    def _pipelines(self, query: str) -> list[dict[str, Any]]:
        return self._client.paginate(
            lambda page: (
                self._client.request(
                    f"{self._base}/pipelines?page={page}&per_page=50{query}"
                )
                or []
            ),
            subject=f"{self._base}/pipelines",
        )

    def status(self, sha: str) -> CombinedStatus:
        """The newest relevant pipeline for *sha* decides the verdict.

        Skipped pipelines are not verdicts: nothing ran, so they do
        not count as contexts, and a commit with only skipped
        pipelines reads as unreported.
        """
        pipelines = [
            entry
            for entry in self._pipelines(f"&sha={quote(sha, safe='')}")
            if str(entry.get("status")) != "skipped"
        ]
        if not pipelines:
            return CombinedStatus(state="none", contexts=0)
        newest = max(pipelines, key=lambda entry: int(entry["id"]))
        status, conclusion = _run_state(str(newest.get("status", "")))
        if status != "completed":
            return CombinedStatus(state="pending", contexts=len(pipelines))
        if conclusion in ("success", "skipped"):
            return CombinedStatus(state="success", contexts=len(pipelines))
        return CombinedStatus(state="failure", contexts=len(pipelines))

    def runs(self, *, head_sha: str = "", event: str = "") -> tuple[Run, ...]:
        """One Run per pipeline, newest first.

        The pipeline ``source`` maps into the protocol's event
        vocabulary: an API-, trigger-, or web-created pipeline reads
        as ``workflow_dispatch``, a push as ``push``, anything else
        verbatim.
        """
        query = f"&sha={quote(head_sha, safe='')}" if head_sha else ""
        runs = []
        for entry in self._pipelines(query):
            source = str(entry.get("source", ""))
            mapped = _EVENTS.get(source, source)
            if event and mapped != event:
                continue
            status, conclusion = _run_state(str(entry.get("status", "")))
            runs.append(
                Run(
                    id=int(entry["id"]),
                    workflow="",
                    head_sha=str(entry.get("sha", "")),
                    event=mapped,
                    status=status,
                    conclusion=conclusion,
                    url=str(entry.get("web_url", "")),
                )
            )
        runs.sort(key=lambda run: run.id, reverse=True)
        return tuple(runs)

    def _pipeline(self, run: int) -> dict[str, Any]:
        data = self._client.request(f"{self._base}/pipelines/{run}", none_on=(404,))
        if data is None:
            raise ForgeError(f"no pipeline {run} at {self._base}", status=404)
        result: dict[str, Any] = data
        return result

    def jobs(self, run: int) -> tuple[Job, ...]:
        """Every job of one pipeline."""
        raw = self._client.paginate(
            lambda page: (
                self._client.request(
                    f"{self._base}/pipelines/{run}/jobs?page={page}&per_page=50"
                )
                or []
            ),
            subject=f"{self._base}/pipelines/{run}/jobs",
        )
        jobs = []
        for entry in raw:
            status, conclusion = _run_state(str(entry.get("status", "")))
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
        """The raw trace of one job."""
        text = self._client.text(f"{self._base}/jobs/{job}/trace")
        return text or ""

    def rerun(self, run: int, *, failed_only: bool = True) -> None:
        """Retry the pipeline's failed jobs.

        GitLab's retry covers failed and cancelled jobs, which is the
        ``failed_only`` contract; a whole-pipeline re-run under the
        same id does not exist, so ``failed_only=False`` maps to the
        same retry. A live pipeline is refused here, because GitLab
        would accept the retry and the protocol promises a refusal.
        """
        status, _ = _run_state(str(self._pipeline(run).get("status", "")))
        if status != "completed":
            raise ForgeError(
                f"pipeline {run} is still in progress: nothing to re-run yet",
                status=409,
            )
        self._client.request(f"{self._base}/pipelines/{run}/retry", method="POST")

    def cancel_run(self, run: int, *, force: bool = False) -> None:
        """Cancel the pipeline; ``force`` is declined by name.

        GitLab answers 200 when cancelling an already finished
        pipeline, so the terminal probe lives here: the protocol
        promises the refusal.
        """
        if force:
            raise Unsupported(
                "GitLab pipelines have plain cancel only (capability: force_cancel)"
            )
        status, _ = _run_state(str(self._pipeline(run).get("status", "")))
        if status == "completed":
            raise ForgeError(
                f"pipeline {run} is already terminal and cannot be cancelled",
                status=409,
            )
        self._client.request(f"{self._base}/pipelines/{run}/cancel", method="POST")

    def dispatch(
        self, workflow: str, *, ref: str, inputs: Mapping[str, str] | None = None
    ) -> None:
        """Trigger the project's pipeline on *ref*.

        GitLab has one pipeline definition per project; *workflow*
        travels as the ``FORGE_WORKFLOW`` variable for the pipeline's
        own rules to route on, and *inputs* as further variables.
        """
        variables = [{"key": "FORGE_WORKFLOW", "value": workflow}]
        variables += [
            {"key": key, "value": value} for key, value in (inputs or {}).items()
        ]
        self._client.request(
            f"{self._base}/pipeline",
            method="POST",
            data={"ref": ref, "variables": variables},
        )


class _GitlabReleases:
    """The release operations of one GitLab project."""

    def __init__(self, client: JsonClient, base: str) -> None:
        self._client = client
        self._base = base

    def create(
        self, tag: str, *, name: str, body: str = "", prerelease: bool = False
    ) -> Release:
        """Create the release for *tag*; an existing release is refused.

        GitLab has no prerelease flag; the field is carried in the
        record and not expressed on the server.
        """
        data = self._client.request(
            f"{self._base}/releases",
            method="POST",
            data={"tag_name": tag, "name": name, "description": body},
        )
        release = _as_release(data)
        if prerelease:
            release = Release(
                tag=release.tag,
                name=release.name,
                body=release.body,
                prerelease=True,
                url=release.url,
            )
        return release

    def get(self, tag: str) -> Release | None:
        """The release for *tag*, or None; the tag is URL-encoded whole."""
        data = self._client.request(
            f"{self._base}/releases/{quote(tag, safe='')}", none_on=(404,)
        )
        return None if data is None else _as_release(data)


def _as_release(data: Mapping[str, Any]) -> Release:
    """GitLab's release JSON, normalised."""
    links = data.get("_links") or {}
    return Release(
        tag=str(data.get("tag_name", "")),
        name=str(data.get("name") or ""),
        body=str(data.get("description") or ""),
        prerelease=False,
        url=str(links.get("self", "")),
    )


#: Resolved at module scope: inside the issues classes the method
#: named `list` shadows the builtin in annotation scope.
_Rows = list[dict[str, Any]]


def _as_issue(data: Mapping[str, Any]) -> Issue:
    """GitLab's issue JSON, normalised; the number is the iid."""
    state: ItemState = "open" if data.get("state") == "opened" else "closed"
    return Issue(
        number=int(data["iid"]),
        title=str(data.get("title", "")),
        body=str(data.get("description") or ""),
        state=state,
        labels=tuple(str(label) for label in (data.get("labels") or [])),
        assignees=tuple(
            str(person.get("username", "")) for person in (data.get("assignees") or [])
        ),
        url=str(data.get("web_url", "")),
    )


class _GitlabIssues:
    """The issue operations of one GitLab project."""

    def __init__(self, forge: GitlabForge, client: JsonClient, base: str) -> None:
        self._forge = forge
        self._client = client
        self._base = base

    def _listing(self, query: str, *, state: StateFilter) -> _Rows:
        mapped = {"open": "opened", "closed": "closed", "all": "all"}[state]
        state_query = f"&state={mapped}" if mapped != "all" else ""
        return self._client.paginate(
            lambda page: (
                self._client.request(
                    f"{self._base}/issues?page={page}&per_page=50{state_query}{query}"
                )
                or []
            ),
            subject=f"{self._base}/issues",
        )

    def _user_id(self, username: str) -> int:
        matches = self._client.request(f"/users?username={quote(username)}")
        for entry in matches or []:
            if entry.get("username") == username:
                return int(entry["id"])
        raise ForgeError(f"no user named {username} on this GitLab")

    def create(
        self,
        title: str,
        *,
        body: str = "",
        labels: tuple[str, ...] = (),
        assignee: str = "",
    ) -> Issue:
        """Open an issue; the assignee username resolves to an id here."""
        payload: dict[str, Any] = {
            "title": title,
            "description": body,
            "labels": ",".join(labels),
        }
        if assignee:
            payload["assignee_ids"] = [self._user_id(assignee)]
        data = self._client.request(f"{self._base}/issues", method="POST", data=payload)
        return _as_issue(data)

    def get(self, number: int) -> Issue | None:
        """The issue *number* (its iid), body included, or None."""
        data = self._client.request(f"{self._base}/issues/{number}", none_on=(404,))
        return None if data is None else _as_issue(data)

    def list(self, *, state: StateFilter = "open") -> tuple[Issue, ...]:
        """The project's issues in *state*, oldest first."""
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
        over the complete listing, as every backend does, so the
        answer does not depend on a search index's freshness.
        """
        query = f"&labels={quote(','.join(labels))}" if labels else ""
        issues = [
            _as_issue(raw)
            for raw in self._listing(query, state=state)
            if text in str(raw.get("title", ""))
            or text in str(raw.get("description") or "")
        ]
        issues.sort(key=lambda issue: issue.number)
        return tuple(issues)

    def _assignee_ids(self, number: int) -> tuple[int, ...]:
        """The issue's current assignees, as GitLab user ids.

        A tuple, because inside this class ``list`` names the issue
        listing, not the builtin.
        """
        data = self._client.request(f"{self._base}/issues/{number}")
        return tuple(
            int(person["id"])
            for person in (data or {}).get("assignees") or []
            if person.get("id") is not None
        )

    def assign(self, number: int, assignee: str) -> None:
        """Add *assignee* to the issue's assignees.

        GitLab's PUT replaces the whole list, so the add is a
        read-modify-write over the current assignee ids: two adds
        racing can lose one. The free tier keeps one assignee and
        silently drops the rest; the caller's policy limit is what
        makes both honest.
        """
        if self.get(number) is None:
            raise ForgeError(f"no issue {number} at {self._base}", status=404)
        ids = self._assignee_ids(number)
        wanted = self._user_id(assignee)
        if wanted not in ids:
            ids = (*ids, wanted)
        self._client.request(
            f"{self._base}/issues/{number}",
            method="PUT",
            data={"assignee_ids": list(ids)},
        )

    def unassign(self, number: int) -> None:
        """Remove the authenticated user from the issue's assignees."""
        if self.get(number) is None:
            raise ForgeError(f"no issue {number} at {self._base}", status=404)
        ids = self._assignee_ids(number)
        mine = self._user_id(self._forge.whoami())
        if mine not in ids:
            return
        # 0 is GitLab's documented clear-all sentinel; an empty list
        # is ignored by some versions, which would leave the caller
        # assigned while reporting success.
        self._client.request(
            f"{self._base}/issues/{number}",
            method="PUT",
            data={"assignee_ids": [i for i in ids if i != mine] or [0]},
        )

    def close(self, number: int) -> None:
        """Close issue *number*; a closed issue stays closed.

        ``state_event=close`` on an already-closed issue is refused
        by GitLab, so the current state gates the write.
        """
        issue = self.get(number)
        if issue is None:
            raise ForgeError(f"no issue {number} at {self._base}", status=404)
        if issue.state == "closed":
            return
        self._client.request(
            f"{self._base}/issues/{number}",
            method="PUT",
            data={"state_event": "close"},
        )

    def assigned_to_me(self) -> tuple[Issue, ...]:
        """The open issues assigned to the token's user."""
        return tuple(
            _as_issue(raw)
            for raw in self._listing("&scope=assigned_to_me", state="open")
        )

    def comment(self, number: int, body: str) -> None:
        """Post *body* as a note on issue *number*."""
        self._client.request(
            f"{self._base}/issues/{number}/notes",
            method="POST",
            data={"body": body},
        )
