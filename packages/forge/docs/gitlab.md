# GitLab mapping

How every protocol method maps onto GitLab's REST v4 API: the
contract the `_gitlab` backend implements, written down before the
backend so the odd one out shapes the protocol instead of surprising
it. Everything here is verified against the local GitLab container by
the conformance suite; what the container corrected is folded in, and
the asynchronous behaviours it taught are in
[quirks.md](quirks.md).

## Addressing

- **A project is addressed by its URL-encoded path**:
  `GET /projects/owner%2Fname`. The owner may be a group or a
  subgroup path (`group/subgroup`), which is why the protocol's
  `owner` is a string with no structure imposed.
- **iid versus global id, resolved once**: merge requests and issues
  are addressed by their per-project `iid`, which is the number a
  person sees, so the protocol's `number` **is** the iid and the
  global `id` never crosses the boundary. Pipelines and jobs have no
  iid worth speaking; the protocol's run and job ids **are** their
  `id` fields.
- **Pagination**: every listing walks `per_page=100` pages by
  `x-next-page` until empty. A listing that cannot be completed
  raises, per the protocol's completeness rule.
- **A tag in a URL is URL-encoded whole**: the path-tag grammar puts
  `/` in tag names, so `packages/forge/v0.0.1` travels as
  `packages%2Fforge%2Fv0.0.1`.

## Capabilities on GitLab

| Capability | Answer | Why |
| --- | --- | --- |
| `auto_merge` | yes | merge when pipeline succeeds, on the merge request itself, every tier |
| `force_cancel` | no | pipelines have plain cancel only; declined by name |
| `required_contexts` | no | protection cannot name required check contexts; external status checks are tier-gated (Ultimate) and out of protocol |
| `ci_secrets` | yes | one variables API serves both; a secret is stored masked where the value satisfies the masking rules |
| `min_approvals` | per instance | GitLab licence-gates approval rules; `supports` probes a visible project's rules endpoint once per connection (404 on an unlicensed server, the observed CE answer) and caches the answer. An unlicensed instance's `configure` declines by name before anything applies |
| `schedule_events` | yes | reconstructed: the system notes carry the schedule and the resource state events carry merged, closed, and reopened, merged oldest first by timestamp. The note wording is a parsing contract with the server |

Approval rules are implemented and licence-probed: on a licensed
instance `configure` writes the count and the codeowner requirement
and `protection` reads them back; on an unlicensed one `supports`
answers no and `configure` declines by name before anything applies.
Merge trains (Premium) and external status checks (Ultimate) stay
out of the protocol entirely. The container the backend develops
against is CE, so the unlicensed arms are recorded live and the
licensed arms are pinned replay tests from the documented API,
awaiting a licensed surface to record against.

## Forge

| Method | GitLab | Semantics |
| --- | --- | --- |
| `whoami` | `GET /user` | `username` |
| `server_version` | `GET /version` | `version`; needs authentication |
| `create_repo` | `POST /projects` | `visibility` from `private`; when the owner is a group, `namespace_id` is resolved first (`GET /namespaces?search=`); `initialize_with_readme` so the default branch exists, as the protocol requires |
| `get_repo` | `GET /projects/:path` | 404 is None; a redirect route can serve a renamed corpse with 200 for the old path, so the answer's `path_with_namespace` is verified before it counts |
| `delete_repo` | `DELETE /projects/:path` | 404 is success, and so are the two shapes of "already deleting": 400 "marked for deletion" and 405 "moved". Deletion is asynchronous and the path stays reserved until it lands; a creator reusing a path renames the leftover aside first, because rename is synchronous |
| `members` | `GET /groups/:owner/members` | usernames; a personal namespace 404s and answers its one login |
| `teams` | `GET /groups/:owner/subgroups` | GitLab's teams are its subgroups, answered as full paths; a personal namespace answers empty |

## Repository

| Method | GitLab | Semantics |
| --- | --- | --- |
| `configure`: `default_branch` | `PUT /projects/:path` | `default_branch` |
| `configure`: `squash_only` | `PUT /projects/:path` | `squash_option: "always"` |
| `configure`: `delete_branch_on_merge` | `PUT /projects/:path` | `remove_source_branch_after_merge` |
| `configure`: `allow_auto_merge` | none needed | merge when pipeline succeeds is always available; the field is a no-op here |
| `configure`: `required_contexts` | none | raises `Unsupported`; the nearest CE fact, `only_allow_merge_if_pipeline_succeeds`, is a boolean and cannot name contexts |
| `configure`: `min_approvals` | `GET`/`POST /projects/:path/approval_rules`, `PUT /projects/:path/approval_rules/:id` | the any-approver rule, created or updated after a read; an unlicensed instance raises `Unsupported` naming `min_approvals` before anything applies |
| `configure`: `require_codeowner_review` | `POST` / `PATCH /projects/:path/protected_branches` | the `code_owner_approval_required` field on the protected default branch, protecting it first when it is not yet; only that field is written, so existing access levels stay |
| `configure`: protection | `POST /projects/:path/protected_branches` | the default branch, direct pushes to maintainers-and-up; re-running with the same levels is a no-op |
| `configure`: `secrets` / `variables` | `POST` / `PUT /projects/:path/variables` | one variables API serves both; a secret is stored `masked` where its value satisfies GitLab's masking rules (single line, 8 characters or more, base64 alphabet) and stored unmasked otherwise, which the backend reports rather than hides |
| `configure`: `labels` | `POST` / `PUT /projects/:path/labels` | by name; create when missing, update colour and description when present |
| `tags` | `GET /projects/:path/repository/tags` | names only |
| `branch_exists` | `GET /projects/:path/repository/branches/:branch` | 404 is False; the branch name is URL-encoded |
| `protection` | `GET /projects/:path/protected_branches/:branch` plus `GET /projects/:path/approval_rules` | the guarded flag and the codeowner field from the record, the approval count as the highest over the rules; an unlicensed server's rules 404 and read as zero |
| `delete_branch` | `DELETE /projects/:path/repository/branches/:branch` | 404 is success |


## Codeowners

The dialect is `.gitlab/CODEOWNERS` with sections: a `[name][n]`
heading carries a per-path approval count in the file itself, the
one dialect that can. A heading owns every entry after it until the
next heading, so the plain entries render first and each counted
entry gets its own trailing section. Enforcing the counts needs a
paid tier; on CE the file is documentation and reviewer routing,
and on a licensed instance `configure` turns enforcement on through
`code_owner_approval_required`.

## Pull requests (merge requests)

| Method | GitLab | Semantics |
| --- | --- | --- |
| `open` | `POST /projects/:path/merge_requests` | `source_branch`, `target_branch`, `title`, `description`; 409 when an open MR for the source exists |
| `find_by_head` | `GET /projects/:path/merge_requests?source_branch=` | GitLab states are `opened` / `closed` / `merged` / `locked`; the protocol's `closed` filter takes both `closed` and `merged`, so the backend queries `state=all` and filters. GitLab keeps `source_branch` on merged MRs, which exceeds the contract; nothing may rely on that |
| `find_by_head_sha` | `GET /projects/:path/merge_requests?state=all` | no sha filter exists; the backend matches the `sha` field client-side, complete-or-raise |
| `get` | `GET /projects/:path/merge_requests/:iid` | the protocol number is the iid |
| `update_title` | `PUT /projects/:path/merge_requests/:iid` | `title` |
| `close` / `reopen` | `PUT /projects/:path/merge_requests/:iid` | `state_event: "close"` / `"reopen"`; reopening a merged MR fails and maps to `ForgeError` |
| `merge_now` | `PUT /projects/:path/merge_requests/:iid/merge` | `squash`, `squash_commit_message` from title and message, and `should_remove_source_branch` read from the project setting, because the setting alone only pre-fills the UI checkbox; GitLab answers 405 while the mergeability recompute runs, which matches the protocol's 405-shaped refusal as-is |
| `arm` | `PUT /projects/:path/merge_requests/:iid/merge` with `merge_when_pipeline_succeeds: true` | 405 until the mergeability recompute finishes and the head pipeline associates with the MR, both asynchronous; the backend surfaces the 405 verbatim and callers retry. The scheduled merge and its branch deletion land asynchronously after the pipeline succeeds: always observed, never assumed |
| `disarm` | `POST /projects/:path/merge_requests/:iid/cancel_merge_when_pipeline_succeeds` | a refusal because nothing is scheduled maps to False, not an error |
| `is_armed` | `GET /projects/:path/merge_requests/:iid` | the `merge_when_pipeline_succeeds` field: readable state, no timeline walk |
| `schedule_events` | `GET /projects/:path/merge_requests/:iid/notes` plus `GET .../resource_state_events` | reconstructed: system notes beginning "enabled an automatic merge" / "canceled the automatic merge" / "aborted the automatic merge" / "added N commit" map to scheduled, unscheduled, and pushed; state events carry merged, closed, reopened; merged oldest first by timestamp |
| `comment` | `POST /projects/:path/merge_requests/:iid/notes` | `body` |

## Checks

The canonical CI answer on GitLab is the commit's latest pipeline,
not commit statuses.

| Method | GitLab | Semantics |
| --- | --- | --- |
| `status` | `GET /projects/:path/pipelines?sha=` | no pipelines is `none`; the skipped pipelines do not count as contexts (nothing ran); the newest remaining pipeline decides: `success` is `success`, `failed` and `canceled` are `failure`, every other state (`created`, `pending`, `running`, `canceling`, `manual`, and kin) is `pending` |
| `runs` | `GET /projects/:path/pipelines?sha=&...` | one Run per pipeline: `id` is the pipeline id, `workflow` is empty (one pipeline definition per project), `event` is the pipeline `source` |
| `jobs` | `GET /projects/:path/pipelines/:id/jobs` | job `id`, `name`, status and conclusion mapped as for pipelines |
| `job_log` | `GET /projects/:path/jobs/:id/trace` | plain text |
| `rerun` | `POST /projects/:path/pipelines/:id/retry` | retries failed and cancelled jobs, which is the `failed_only=True` contract; GitLab cannot re-run a whole pipeline under the same id, so `failed_only=False` also maps to retry and the docstring's "all of them" is best-effort here |
| `cancel_run` | `POST /projects/:path/pipelines/:id/cancel` | GitLab answers 200 on an already finished pipeline, so the backend probes the pipeline first and raises the protocol's terminal-run `ForgeError` itself; a pipeline reads `canceling` (a live state) until its jobs acknowledge; `force=True` raises `Unsupported` naming `force_cancel` |
| `dispatch` | `POST /projects/:path/pipeline` | `ref`, plus variables: the protocol's `workflow` travels as the `FORGE_WORKFLOW` variable and `inputs` as further variables, for the pipeline's own rules to route on |

## Releases

| Method | GitLab | Semantics |
| --- | --- | --- |
| `create` | `POST /projects/:path/releases` | `tag_name`, `name`, `description`; 409 when the tag already has a release |
| `get` | `GET /projects/:path/releases/:tag` | the tag URL-encoded whole; 404 is None |

## Issues

| Method | GitLab | Semantics |
| --- | --- | --- |
| `create` | `POST /projects/:path/issues` | `title`, `description`, `labels` as a comma-joined string; the assignee username resolves to an id first (`GET /users?username=`), one extra request the backend hides |
| `get` | `GET /projects/:path/issues/:iid` | the protocol number is the iid; `description` is the body |
| `list` | `GET /projects/:path/issues?state=` | GitLab spells the open state `opened`; the backend translates |
| `search` | `GET /projects/:path/issues?search=&in=title,description&labels=` | server-side text search over title and body |
| `assign` | `PUT /projects/:path/issues/:iid` | `assignee_ids` as a read-modify-write over the current ids: GitLab replaces the whole list, so the add re-sends it with one more |
| `unassign` | `PUT /projects/:path/issues/:iid` | the current ids minus the caller's; `[0]` is the documented clear sentinel, an empty list is ignored by some versions |
| `close` | `PUT /projects/:path/issues/:iid` | `state_event=close`, gated on the current state: GitLab refuses the event on a closed issue |
| `assigned_to_me` | `GET /projects/:path/issues?scope=assigned_to_me&state=opened` | |
| `comment` | `POST /projects/:path/issues/:iid/notes` | `body` |
