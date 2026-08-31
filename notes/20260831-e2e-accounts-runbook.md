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

## 3. gitea.com: a free organisation

The hosted-stable edge beside the local container's 1.28-dev edge.
Known caveat, stated in the workshop plan: until gitea.com serves the
1.28 line, the required `cancel_run` scenarios fail below the floor
(the backend raises `Unsupported` naming the server version), so this
leg runs partial and marked, or waits for 1.28.

1. Create an account at <https://gitea.com> (or sign in), then a new
   organisation, e.g. `livery-forge-e2e`.
2. Mint a token: Settings → Applications → Generate New Token, with
   read/write on `organization`, `repository`, `issue`, and `user`.
3. Store it:

   ```console
   gh secret set LIVERY_E2E_GITEA_TOKEN --repo willemkokke/livery
   gh variable set LIVERY_E2E_GITEA_OWNER --repo willemkokke/livery --body livery-forge-e2e
   ```

4. Verify: `GITEA_URL=https://gitea.com GITEA_TOKEN=<token> uv run
   python -c "from livery.forge import GiteaForge; f =
   GiteaForge.connect(); print(f.whoami(), f.server_version());
   print('gitea.com ok')"` — the printed version also answers
   whether the 1.28 floor caveat still applies.

## Afterwards

Say the accounts exist and the release-legs workflow lands (workshop
plan phase 7): workflow_dispatch, one job per forge, each skipping
cleanly when its secret is absent, each deleting its scratch at end
of run.
