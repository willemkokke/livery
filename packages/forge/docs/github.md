# GitHub backend

`livery.forge.GithubForge`: the protocol over REST v3 plus the one
GraphQL pair auto-merge requires.

## Construction and the token rule

`GithubForge.connect()` speaks to github.com unless a GitHub
Enterprise URL is given (its API root is `<url>/api/v3`). The token
resolves as `GITHUB_TOKEN` first, then `gh auth token`, so a machine
with a signed-in gh CLI needs no configuration; nothing found raises
at connect time. Pass `token=""` to read anonymously on purpose.

Full scratch-and-release development needs the `repo`, `workflow`,
and `delete_repo` scopes; a token missing one gets GitHub's refusal
verbatim.

## Capabilities

`auto_merge`, `force_cancel`, and `required_contexts` are supported.
`ci_secrets` depends on the `github-secrets` extra: GitHub's secrets
API takes only values sealed to the repository's public key
(libsodium), so `livery-forge[github-secrets]` installs PyNaCl and
turns the capability on, and a bare install declines by name with the
extra's name in the message. The public key and its id come from the
API; nothing is configured by hand. The import is lazy: nothing
outside the secrets path loads it, and `forge.supports("ci_secrets")`
answers for the running install.

## Mapping notes

- Auto-merge is GraphQL only (`enablePullRequestAutoMerge` /
  `disablePullRequestAutoMerge`); everything else is REST. GraphQL
  failures answer 200 with an `errors` array, which the backend
  raises as `livery.forge.ForgeError` with GitHub's words verbatim.
- GitHub only arms a pull request that something blocks: the
  repository needs `allow_auto_merge` on and branch protection naming
  required contexts (the check context is the job name). Re-arming
  replaces the schedule by disarm-then-enable, because GitHub cannot
  update an armed schedule's commit headline.
- `checks.status` folds GitHub's two reporting systems, commit
  statuses and check runs, into the one combined verdict.
- `checks.job_log` answers with a redirect to a signed, short-lived
  URL. The refused redirect carries the location in the error's
  detail and is followed once, bare, with no token attached: the one
  redirect this package ever follows.
- Pull requests and issues share the number space and the issue
  endpoints; listings filter pull requests out and `issue.get` on a
  pull request's number answers None.
- `issue.search` matches text client-side over the complete listing:
  GitHub's search API indexes asynchronously, and a probe that can
  miss what was just created is not a probe. Issue listings
  themselves run several seconds behind writes, which is the
  conformance driver's `await_issue` bound.
- `mergeable` is computed asynchronously after a pull request opens;
  merging inside that window is refused and callers retry.

## Fixtures

Recording runs against scratch repositories named
`livery-forge-conf-*` under the signed-in user, created public
(branch protection on private repositories needs a paid plan) and
deleted as scenarios re-run. `fm forge.fixtures.record` re-records;
the replayed cassettes gate merges with no network and no credential.
