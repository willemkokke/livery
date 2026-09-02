# The protocol

One interface to GitHub, Gitea, and GitLab: `livery.forge.Forge` for
one server, `livery.forge.Repository` for one repository on it, and
`livery.forge.Registry` for one package index. The protocols are
frozen: every backend, the verified fake included, passes the one
conformance suite in `livery.forge.testing`, and a change to a verb
now is a compatibility event.

Every verb exists because a development workflow uses it, and a verb
no workflow uses is removed. There is deliberately no
repository-listing verb: fleet enumeration is configuration-driven,
and discovery joins the protocol only when a workflow demands it.

## The rules every method obeys

- **One handle.** A pull request, issue, run, or job is identified by
  the number or id the protocol returned for it. A backend whose
  server keeps two identities (GitLab's iid and global id) translates
  at the boundary and never leaks the other one. Pull request and
  issue numbers count in separate spaces: never use one as the other.
- **Listings are complete or they raise.** A method that returns a
  sequence returns everything the query matches, or raises
  `livery.forge.ForgeError`. A truncated prefix is never returned as
  the answer, because "not in this list" is an answer callers act on.
- **Probe before acting.** Re-running a workflow is its recovery
  procedure. Where a server operation is not idempotent (creating a
  repository, a release, a pull request), the protocol pairs it with
  the read that makes probe-then-act idempotent as a whole.
- **Capabilities, not pretence.** Where forges differ, the
  difference is a named capability. `forge.supports(name)` answers
  honestly, and an operation a forge declines raises
  `livery.forge.Unsupported` naming the capability.
- **Failures carry the server's words.** Every `ForgeError` message
  quotes what the server said; `status`, `method`, and `endpoint`
  attributes carry what a caller branches on. No failure is reduced
  to a boolean.

## Capabilities

| Name | Meaning | GitHub | Gitea (1.28 floor) | GitLab |
| --- | --- | --- | --- | --- |
| `auto_merge` | a merge can be scheduled to fire when checks go green | yes | yes | yes (merge when pipeline succeeds) |
| `force_cancel` | a run whose runner stopped answering can be cancelled immediately | yes | yes | no |
| `required_contexts` | branch protection names the check contexts that must pass | yes | yes | no |
| `ci_secrets` | `RepoConfig.secrets` can be stored through the backend | with the `github-secrets` extra (sealed-box encryption via PyNaCl); a bare install declines by name | yes | yes (masked variables) |
| `min_approvals` | protection can require approving reviews (and a codeowner's) before merge | yes | yes | per instance: GitLab licence-gates approval rules, and `supports` probes the licence once per connection |
| `schedule_events` | the merge-scheduling history of a pull request can be read | yes | yes | yes (reconstructed from system notes and state events) |

## The verbs

### `Forge`: one server

| Verb | Does |
| --- | --- |
| `whoami()` | the token's login name; the first probe of anything unattended |
| `server_version()` | the server's version; backends with a floor raise `Unsupported` naming it |
| `supports(capability)` | the honesty valve |
| `repository(owner, name)` | the `Repository` view; cheap, no network |
| `create_repo(owner, name, *, private, description)` | create, initialised with a default branch; raises when it exists |
| `get_repo(owner, name)` | settings or None; the probe that makes creation re-runnable |
| `delete_repo(owner, name)` | idempotent |
| `user_url(login)` | the profile address; string building, nothing on the wire |
| `members(owner)` | the org's member logins; a user namespace answers its one login (the endpoints 404 there) |
| `teams(owner)` | the org's team names; a user namespace answers empty |
| `codeowners(entries)` | renders the forge's CODEOWNERS dialect from neutral entries; pure string building, offline |

### `Repository`: repository-level

| Verb | Does |
| --- | --- |
| `configure(config)` | idempotent drift repair; None fields untouched; see `livery.forge.RepoConfig` |
| `tags()` | every tag name; the release train's existence probe |
| `branch_exists(branch)` | existence |
| `protection(branch)` | the branch's `livery.forge.Protection`, or None; what a backend cannot read reads as inert |
| `delete_branch(branch)` | idempotent; the abort path's cleanup (merge-path deletion is configuration) |
| `web_url()` | the repository's home page |
| `pr_url(number)` | the pull request's address |
| `issue_url(number)` | the issue's address |
| `commit_url(sha)` | the commit's address |
| `compare_url(base, head)` | the comparison's address |
| `tag_url(tag)` | the tag or release view's address |

Labels are spoken by name everywhere. `configure` ensures they exist;
no separate label verbs exist.

The address family is pure string building: nothing goes on the wire
and no address is probed for existence, so a changelog or a message
can carry links without spending a request. Each backend writes its
own path shapes (`/pull/N` on GitHub, `/pulls/N` on Gitea,
`/-/merge_requests/N` on GitLab), which is why the shapes live here
rather than in every caller. A built address is compared with a
reported one by path, never by host: a server's links follow its own
configured external address, which need not be the host the API was
reached on (see `quirks.md`).

### `pr`: pull requests

| Verb | Does |
| --- | --- |
| `open(head, base, title, body)` | open; refuses a duplicate open head |
| `find_by_head(branch, *, state)` | reliable for open pull requests |
| `find_by_head_sha(sha)` | finds a merged pull request after its branch is gone |
| `get(number)` | the pull request or None |
| `update_title(number, title)` | the review-facing name and the future squash subject |
| `close(number)` / `reopen(number)` | reopen reuses the pull request; a merged one refuses |
| `merge_now(number, *, title, message)` | immediate merge; 405-shaped refusal when not green |
| `arm(number, *, title, message)` | merge when green, server-side; disarm before any push |
| `disarm(number)` | cancels the schedule; True when one existed |
| `is_armed(number)` | the schedule's state; a non-open pull request reads unarmed |
| `reviews(number)` | the submitted review verdicts; drafts never arrive |
| `schedule_events(number)` | the merge-scheduling history, oldest first; capability-gated (`schedule_events`) |
| `comment(number, body)` | the evidence channel |

The arming contract: pushing to an armed pull request races the
server-side merge, and the merge can take the pre-push head. Disarm,
push, re-arm. The merge itself is always observed, never performed by
the waiting caller.

### `checks`: CI

| Verb | Does |
| --- | --- |
| `status(sha)` | the one combined verdict; `none` (nothing reported) is distinct from `pending` |
| `runs(*, head_sha, event)` | newest first |
| `jobs(run)` | a run's jobs |
| `job_log(job)` | the raw log; quoted verbatim in triage, never boolean-ised |
| `rerun(run, *, failed_only=True)` | failed jobs by default; refuses a live run |
| `cancel_run(run, *, force=False)` | required everywhere; refuses a terminal run; `force` is capability-gated |
| `dispatch(workflow, *, ref, inputs)` | trigger a workflow |

### `release`: releases by tag

| Verb | Does |
| --- | --- |
| `create(tag, *, name, body, prerelease)` | refuses a tag that already has a release |
| `get(tag)` | the probe that makes release creation re-runnable |

### `issue`: text in both directions

| Verb | Does |
| --- | --- |
| `create(title, *, body, labels, assignee)` | the body is the work order; labels by name |
| `get(number)` | body included, or None |
| `list(*, state)` | issues only, never pull requests |
| `search(text, *, state, labels)` | matches title and body; the deduplication probe |
| `assign(number, assignee)` | adds to the assignees; a colleague's stays. How many an issue may carry is the caller's policy |
| `unassign(number)` | removes only the authenticated user; not being assigned is a no-op |
| `assigned_to_me()` | the open issues assigned to the token's user |
| `comment(number, body)` | evidence the body cannot carry |
| `close(number)` | closes; closing a closed issue is a no-op, so re-running is the recovery |

### `Registry`: one method, apart from the forge

| Verb | Does |
| --- | --- |
| `versions(name)` | the published versions of a package name; empty when unpublished |

A forge and a registry are only sometimes the same server, so the
index question is its own interface with a backend per ecosystem.

## Testing against the protocol

Consumers test against `livery.forge.testing.FakeForge`, which passes
the same conformance suite the real backends must pass. See
[fixtures.md](fixtures.md) for the record and replay layer, and
[quirks.md](quirks.md) for the fault modes the fake injects. The
GitLab endpoint mapping is in [gitlab.md](gitlab.md).
