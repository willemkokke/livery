# Releases

The train: push a tag shaped `packages/<pkg>/v<semver>` and only
that package releases. `fm release.prepare <path>` without a version
derives it from the conventional commits since the package's last
release tag (after 1.0 a break bumps major, a feature minor,
everything else patch; before 1.0 a feature bumps minor with breaks
riding along) and writes the grouped changelog entry, pull requests
linked and contributors credited, for review before the tag. Tags are immutable and pushed alone.
`fm release.prepare` stamps the version into the three places that
must agree, and `fm release.verify` refuses the tag when they do
not, or when a dependency floor names an unreleased version.

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
