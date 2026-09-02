# Gitea backend

`livery.forge.GiteaForge`: the protocol over Gitea's REST v1 API.

## Construction and the token rule

`GiteaForge.connect()` resolves the server once: an explicit `url`
wins, `GITEA_URL` is the configured default. The token defaults to
`GITEA_TOKEN`; a missing token raises at connect time rather than
failing later on the first write. `GITEA_TOKEN` belongs to the host
`GITEA_URL` names and no other: test a foreign host with
`livery.forge.gitea_is_configured_host` and read it anonymously
(`token=""`) instead of sending it a token it never issued.

## The server floor

The 1.28 line. Everything except run cancellation works on earlier
servers; `checks.cancel_run` probes the version once and raises
`livery.forge.Unsupported` naming it when the server predates the
cancel endpoints. Capabilities: `auto_merge`, `force_cancel`,
`required_contexts`, `ci_secrets`, `min_approvals`, and
`schedule_events` are all supported.

## Mapping notes

- Pull requests and issues share Gitea's number space and its issue
  endpoints. The backend keeps the protocol's separation: listings
  pass `type=issues`, and `issue.get` on a number that names a pull
  request answers None.
- `pr.find_by_head` scans the listing client-side: Gitea's
  server-side `head=` filter has been observed returning every open
  pull request when the branch name carries a `/`.
- `pr.is_armed` walks the issue timeline, the one read-only source of
  the schedule; a `close` or `merge_pull` event clears the armed
  state whether or not a cancel event was emitted.
- `issue.search` matches text client-side over the complete listing:
  the protocol promises a body match and Gitea's `q` filter does not.
- Labels resolve names to ids at the boundary; issue creation with a
  label the repository does not carry is refused with the instruction
  to configure it first.
- Releases and tags with `/` in the name travel URL-encoded whole.
- `members` and `teams` speak the org endpoints; on a personal
  namespace they 404, and the fallback answers the login itself and
  an empty team list.
- The codeowners dialect is `.gitea/CODEOWNERS`, the same line shape
  as GitHub's. Gitea has no codeowner-approval toggle:
  `RepoConfig.require_codeowner_review` is written as
  `block_on_official_review_requests`, which the codeowners file
  feeds, and `protection` reads the answer back through the same
  field.

## Local development

`fm forge.dev.up` starts and seeds the compose container (a 1.28
nightly with an act_runner beside it) and writes the credentials to
the shared env file in footman's config directory
(`.repo.shared.env`), which the env cascade reads. The conformance suite runs three ways:

- default: replays the committed cassettes, no container, no network.
- `LIVERY_FORGE_LIVE=1`: live against the container.
- `fm forge.fixtures.record`: live, rewriting the cassettes.
