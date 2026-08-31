# Runbook: the e2e accounts for the release legs

Status: instructions for Willem, 2026-08-31. One-time setup for the
per-release live legs (workshop plan phase 7; bootstrap open item 7).
Everything here is manual because it creates accounts and mints
tokens; every name below is a proposal the workflow reads from a
variable, so a different name only changes the variable.

The secret and variable names the release-legs workflow will read:

| Name | Kind | Holds |
| --- | --- | --- |
| `LIVERY_E2E_GITHUB_TOKEN` | secret | classic PAT for the GitHub org |
| `LIVERY_E2E_GITHUB_OWNER` | variable | the org login, e.g. `livery-forge-e2e` |
| `LIVERY_E2E_GITLAB_TOKEN` | secret | gitlab.com personal access token |
| `LIVERY_E2E_GITLAB_GROUP` | variable | the private group path |
| `LIVERY_E2E_GITEA_TOKEN` | secret | gitea.com access token |
| `LIVERY_E2E_GITEA_OWNER` | variable | the gitea.com org name |

A leg whose secret is absent skips, named in the run summary, so the
three sections are independent and can land in any order.

## 1. GitHub: a dedicated organisation

The scratch repositories are public (branch protection on private
repositories needs a paid plan), so the org exists to keep them off
the personal profile.

1. Create the org: <https://github.com/organizations/plan>, free
   plan, name `livery-forge-e2e` (or any free name), owner
   `willemkokke`.
2. Mint a classic PAT at
   <https://github.com/settings/tokens/new> with scopes `repo`,
   `workflow`, `delete_repo`, no expiry shorter than a year. Classic,
   not fine-grained: it is the token shape the backend's live runs
   are verified with.
3. Store it (done 2026-08-31: secret and variable set, verified end
   to end including protection and a sealed-box secret):

   ```console
   gh secret set LIVERY_E2E_GITHUB_TOKEN --repo willemkokke/livery
   gh variable set LIVERY_E2E_GITHUB_OWNER --repo willemkokke/livery --body livery-forge-e2e
   ```

4. Verify: `GITHUB_TOKEN=<pat> uv run python -c "from livery.forge
   import GithubForge; f = GithubForge.connect();
   print(f.whoami()); f.delete_repo('livery-forge-e2e', 'probe');
   f.create_repo('livery-forge-e2e', 'probe', private=False);
   f.delete_repo('livery-forge-e2e', 'probe'); print('org ok')"`.

## 2. gitlab.com: a private group

Private works fully on the free tier: CI, merge when pipeline
succeeds, and the protocol declines `required_contexts` on GitLab
anyway.

1. Create a private group: <https://gitlab.com/groups/new>. Group
   paths are global on gitlab.com, so `livery-forge-e2e` may be
   taken; any path works, it lands in the variable.
2. Shared runners: some accounts must validate (a credit card, not
   charged) before gitlab.com's shared runners run their pipelines;
   without it jobs sit pending forever, which reads as a livery bug
   and is not one. There is no standing settings page for this: the
   card form appears reactively on a blocked pipeline's page
   (`gitlab.com/-/identity_verification`), and accounts verified at
   signup or predating the requirement never see it. The check is
   empirical: push a one-job pipeline and watch it run. Willem's
   account checked out validated on 2026-08-31 (the probe job ran on
   shared runners in seconds), so this step is done. Free tier
   includes 400 compute minutes per month; a full live leg uses a
   few.
3. Mint a personal access token: <https://gitlab.com/-/user_settings/personal_access_tokens>,
   scope `api`, one year. (A group access token would be tidier, but
   those are paid-tier; the personal token is what the container legs
   are verified with anyway.)
4. Store it (done 2026-08-31: secret and variable set, group
   `livery-forge-e2e`):

   ```console
   gh secret set LIVERY_E2E_GITLAB_TOKEN --repo willemkokke/livery
   gh variable set LIVERY_E2E_GITLAB_GROUP --repo willemkokke/livery --body <group-path>
   ```

5. Verify: `GITLAB_URL=https://gitlab.com GITLAB_TOKEN=<pat> uv run
   python -c "from livery.forge import GitlabForge; f =
   GitlabForge.connect(); print(f.whoami());
   f.create_repo('<group-path>', 'probe'); f.delete_repo('<group-path>',
   'probe'); print('group ok')"`.

## 3. gitea.com: a private organisation, bringing its own runner

The hosted-stable edge beside the local container's 1.28-dev edge.
All done 2026-08-31; what the probes established, kept here because
each fact cost a wrong assumption:

- The org and every scratch repo are fully **private**: Gitea gates
  nothing behind payment, protection included.
- gitea.com serves `1.27.0+dev`, below the 1.28 cancel floor, so the
  `cancel_run` scenarios fail by design there until 1.28 lands (the
  backend raises `Unsupported` naming the version); the leg runs
  partial and marked.
- gitea.com has **no usable shared runner pool** for org repos: a
  pushed workflow queues forever and the org's Runners page shows
  zero, which is that page's normal state (it lists org-registered
  runners only). The leg brings its own runner.
- The runner is minted **through the API**, not the UI: the UI's
  Create-new-Runner token is single-use, but
  `POST /orgs/<org>/actions/runners/registration-token` (with the
  ordinary API token) mints a fresh registration token any time, so
  the release-legs workflow stores no runner token at all - it boots
  a disposable `act_runner`, registers, runs the leg, and deletes the
  registration (`DELETE /orgs/<org>/actions/runners/<id>`).
- Against gitea.com use `docker.gitea.com/act_runner:latest`, not
  `:nightly`: the nightly runner registers and is then rejected as
  unregistered by the 1.27 server, consuming the token on the way
  down.
- Proven end to end: a private probe repo's push-triggered job ran
  green on a disposable stable runner registered with an API-minted
  token; repo, container, and registration all cleaned after.

The stored credentials (done 2026-08-31): `LIVERY_E2E_GITEA_TOKEN`
(Settings → Applications token with read/write on `organization`,
`repository`, `issue`, `user`) and
`LIVERY_E2E_GITEA_OWNER=livery-forge-e2e`.

## Afterwards

All three accounts exist and their credentials are stored and
verified (2026-08-31). The release-legs workflow lands with workshop
plan phase 7: workflow_dispatch, one job per forge, each skipping
cleanly when its secret is absent, each deleting its scratch at end
of run; the gitea.com job boots its disposable runner first. The
plaintext token file in the planning repository can be deleted, and
rotating its tokens costs nothing if wanted.
