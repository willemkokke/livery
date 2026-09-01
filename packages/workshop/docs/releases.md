# Releases

The train: push a tag shaped `packages/<pkg>/v<semver>` and only
that package releases. Tags are immutable and pushed alone.

`fm release.prepare <path>` without a version asks
[git-cliff](https://git-cliff.org/) what the unreleased commits earn
and writes the entry they make: sections grouped, pull requests
linked, authors credited. A package with nothing unreleased is
refused rather than given a new number. Each package states its own
answer in its `cliff.toml`, rendered from the template with the
package's tag line, its paths, and the forge its links point at, so
the shape of an entry is changed by changing the template rather
than by editing every package. The version rules there are
footman's: after 1.0 a break bumps major, a feature minor,
everything else patch; before 1.0 a feature bumps minor with breaks
riding along.

`fm release.prepare` stamps the version into the three places that
must agree, and `fm release.verify` refuses the tag when they do
not, or when a dependency floor names an unreleased version.

An entry is written for review, never for trust: read it before the
tag, and edit what a reader needs said differently.

Authors are credited by asking the forge, which a private repository
answers only for a caller it can authenticate. Set the forge's token
variable (`GITHUB_TOKEN`, `GITEA_TOKEN`, `GITLAB_TOKEN`) and the
names appear; without one the entry is written without them and says
so. CI already carries the variable, so a release run credits
authors whether the repository is public or private.

Where the built wheel goes depends on the forge kind the workspace
renders:

- github: trusted publishing to PyPI. No token is stored; the
  workflow's identity is the credential.
- gitea and gitlab: `uv publish` with the `UV_PUBLISH_TOKEN` secret
  to the index the contract's `publish_index` names.

A release of livery-workshop also publishes the template snapshot:
the `templates/` tree at the tagged commit becomes the artifact
repository's content, tagged `v<semver>` in lockstep. The same
version with the same content is a quiet success; the same version
with different content refuses, because a published tag is
immutable.

Not covered today: attestation or signing of the built wheels, a
non-PyPI index for the github kind, and publishing anywhere but the
one configured index per workspace.
