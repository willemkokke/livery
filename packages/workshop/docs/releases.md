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

Before the tag, each member is validated in two isolated
environments: at every direct dependency's floor, and at the newest
each allows. The toolchain the tests need comes from the lock, minus
whatever the leg resolved, so the floor leg proves the floor and the
latest leg proves the newest, and the lock may sit anywhere between.
A toolchain tool that needs another version of a dependency the
member declares is refused by name.

The wave runs at the release squash with the squash's own workshop:
the checkout, the wheel, the receipt and the driver are all the
squash's, so a re-run does what the first run did. When the driver
itself was the fault, a re-dispatch names a released workshop to run
in its place, `fm workflow.release <package> --workshop=<version>`,
and the job installs that release over the workshop the checkout
synced; the checkout, the ref and the wheel stay the squash's. A
later release never strands an earlier died wave: the recovery
consults every recent release squash and re-dispatches the oldest
with an uncut receipt for a package of the requested set, naming
any other set's uncut squash for its own recovery. A dispatch with
no set named takes the oldest uncut squash overall.

Authors are credited by asking the forge, which a private repository
answers only for a caller it can authenticate. Set the forge's token
variable (`GITHUB_TOKEN`, `GITEA_TOKEN`, `GITLAB_TOKEN`) and the
names appear; without one the entry is written without them and says
so. CI already carries the variable, so a release run credits
authors whether the repository is public or private.

Where the built wheel goes depends on the forge kind the workspace
renders:

- github: trusted publishing to PyPI. No token is stored; the
  workflow's identity is the credential. With nothing declared, reads
  come from PyPI's simple index and uploads go to its upload endpoint;
  a `[registries.python]` declaration that names only a read index
  refuses to publish, because an upload endpoint is never defaulted
  from a read address.
- gitea and gitlab: `uv publish` with the `UV_PUBLISH_TOKEN` secret
  to the registry the contract's `[registries]` table names for the
  kind, else the forge's own package registry.

A member whose kind builds platform wheels (the nanobind kind) names
the runner labels that build them under `[ci] wheel-platforms` in its
own `workshop.toml`. The release workflow runs one wheels leg per
label before the wave, each building that platform's wheels for the
workspace's python matrix through cibuildwheel, and the publish job
ships the collected set. A pure member's one wheel is built by the
publish job itself, and the key on such a member refuses.

A release of livery-workshop also publishes the template snapshot:
the `templates/` tree at the tagged commit becomes the artifact
repository's content, tagged `v<semver>` in lockstep. The same
version with the same content is a quiet success; the same version
with different content refuses, because a published tag is
immutable.

Not covered today: attestation or signing of the built wheels, a
non-PyPI index for the github kind, and publishing anywhere but the
one configured index per workspace.
