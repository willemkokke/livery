# Releases

The train: push a tag shaped `packages/<pkg>/v<semver>` and only
that package releases. Tags are immutable and pushed alone.

`fm release.prepare <path>` without a version derives the next one
from the conventional commits under the package's paths since its
last release tag. The version rules are footman's: after 1.0 a break
bumps major, a feature minor, everything else patch; before 1.0 a
feature bumps minor with breaks riding along. A package with nothing
unreleased is refused rather than given a new number.

The entry the commits earn is written by the release notes'
provider, the one answer to the base's `release-notes` query: the
changelog extension, `livery-extensions-changelog`, answers it and
writes each package's `CHANGELOG.md` with
[git-cliff](https://git-cliff.org/). A workspace that lists no
extension answering releases without notes, and the train says so
where it would have written them.

`fm release.prepare` stamps the version into the three places that
must agree, and `fm release.check-tag` refuses the tag when they do
not, or when a dependency floor names an unreleased version; the
release wave runs the same check for every member before anything
uploads.

An entry is written for review, never for trust: read it before the
tag, and edit what a reader needs said differently.

The train builds each member on the build phase's isolated side, in
the `release` configuration, then runs its test phase on the isolated
side once per resolution the release proves: the built wheel in a
fresh environment, its own tests run against the installed copy.
Three resolutions exist. `floor` puts every direct dependency at its
declared floor, so a floor lying about compatibility fails there;
`latest` resolves the newest each allows, the world a fresh consumer
gets; `head` installs at the versions the workspace's `uv.lock` pins,
read as it stands. The legs run in that order, the floor first. Each
leg finds a sibling's unpublished wheel only in the co-released set's
`dist/` directories, and reports what it resolved for each sibling.
The toolchain the tests need comes from the lock, minus whatever the
leg resolved, so the floor leg proves the floor and the latest leg
proves the newest. A toolchain tool that needs another version of a
dependency the member declares is refused by name.

`[release] prove` lists the resolutions: the workspace's
`workshop.toml` sets the list for every member, and a package's own
`workshop.toml` replaces it for that package. Without a list anywhere
a release proves `floor` and `latest`.

```toml
[release]
prove = ["head"]
```

A list from a contract is named with its file when the legs run. A
word other than the three, or one listed twice, is refused when the
contract is read. A sibling released in the same wave counts at its
new version: its floor in every co-released member rises to that
version first.

After a release, `fm release.verify <member>` runs the member's verify
phase: the newest released version the index serves, installed alone
into a plain environment beside the tests checked out at its release
tag, imported from site-packages. A red verification fails, and inside
CI, or with `--report`, files the marker issue through the forge or
comments on the one already open. The nightly point runs it when the
contract schedules it under `[[ci.schedule]]`.

The wave runs at the release squash with the squash's own workshop:
the checkout, the wheel, the receipt and the driver are all the
squash's, so a re-run does what the first run did. When the driver
itself was the fault, a re-dispatch names a released workshop to run
in its place, `fm workflow.release <package> --workshop=<version>`,
and the job installs that release over the workshop the checkout
synced; the checkout, the ref and the wheel stay the squash's. A
later release never strands an earlier died wave: the recovery
consults every recent release squash on the base and re-dispatches
the oldest with an uncut receipt for a package of the requested set,
naming any other set's uncut squash for its own recovery. A dispatch
with no set named takes the oldest uncut squash overall. The wave is
sent only to a release squash, recognised by the `Mined-At` line the
pull request body becomes at the merge: a checkout standing on a
release branch, whose last stamp commit carries no such line, is
refused with the squash on the base named, so a wave that would
refuse is never sent. The release act itself returns to the branch
it started from once its pull request is opened, and its local copy
of the reserved branch goes; the pull request holds the branch on
the remote, and a re-run resumes from there. Armed, the act then
follows the pull request to its squash and the wave to its verdict,
the way `fm submit --armed` follows a feature pull request, and ends
with the wave's exit; unarmed, it returns at once.

A release pull request merges only while its base has not moved
under the set. The gate job's last step, `workflow.release.check-fresh`,
reads the release's manifest, which names the members and the commit
the prepare mined at, and refuses the pull request when the base has
gained a commit under a member's directory since then: merged, that
squash would ship code its changelog does not cover, and its publish
would refuse it where nothing can change it. A move elsewhere in the
repository is no reason to wait. An armed release then re-derives in
place: it prepares again from the moved base and force-pushes the
branch, so the same pull request carries the new stamps, entries and
mining point, and follows it again. A member the base did not move,
in a set whose versions all stay the same, keeps its legs' verdict,
so only the moved members build and test again. A base that keeps
moving under the set stops the act after three prepares, naming the
commits; an unarmed release re-derives when `fm workflow.release
<set>` runs again.

When no released workshop can drive a died wave (its own workshop
was the fault, and releasing a fixed one needs the packages that
wave was to publish), `fm workflow.release <set> --abandon` gives
the wave up: a new release of the set prepares over it and takes
over its uncut receipts. From then on the abandoned squash is
pending for nothing a later release squash names.

Authors are credited by asking the forge, which a private repository
answers only for a caller it can authenticate. Set the forge's token
variable (`GITHUB_TOKEN`, `GITEA_TOKEN`, `GITLAB_TOKEN`) and the
names appear; without one the entry is written without them and says
so. CI already carries the variable, so a release run credits
authors whether the repository is public or private.

Where the built wheel goes depends on the forge kind the workspace
renders:

- github: PyPI, with a token when the repository has a `PYPI_TOKEN`
  secret and trusted publishing otherwise. Trusted publishing stores
  no token, the workflow's identity is the credential, but a new
  name publishes only after a pending publisher is registered for it
  on PyPI by hand; an account-scoped token in `PYPI_TOKEN` creates
  the project on the first upload instead. The local publish verb
  reads the same `UV_PUBLISH_TOKEN` through the env cascade
  (`fm env.set UV_PUBLISH_TOKEN --scope=shared`). With nothing
  declared, reads
  come from PyPI's simple index and uploads go to its upload endpoint;
  a `[registries.python]` declaration that names only a read index
  refuses to publish, because an upload endpoint is never defaulted
  from a read address.
- gitea and gitlab: `uv publish` with the `UV_PUBLISH_TOKEN` secret
  to the registry the contract's `[registries]` table names for the
  kind, else the forge's own package registry.

A conan package resolves the same ladder for its own kind: the
`[registries.conan]` declaration first, then a conan registry the
forge hosts, and on a forge that hosts none, the forge's releases.
On that route each wheel-platform leg creates the package and saves
its cache as `dist/<name>-<version>-<host>.tgz`, and the wave
attaches every collected file to the member's own release. The tag
is pushed before the upload, because a release is addressed by its
tag, so a tag alone is not a receipt there: the probe reads the
attached caches, and a re-run after a half-finished upload finishes
it. A consumer lists the release, checks the bytes against the
digest the forge reports, and restores the cache into its conan
home; a forge that reports no digest says so rather than passing
silently.

A release leg builds the packages the release ships, so it sets the
workspace's `conanws.yml` aside while it builds: a member's
`conan create` and an extension's wheel resolve a sibling to the
package the leg created, never to its source tree. The file comes
back when the builds end, a failure included.

Every floor a member declares on a conan member is proved in the
leg that builds it: the floor's cache is restored from its own
release, one wheel is built for that machine with a conan profile
replacing the requirement with the floor, and the package's tests
run against it. A floor equal to the version the wave is releasing
is the package the main build already linked, and the leg says so
instead of building twice.

A member that holds the nanobind extension, as a member of the
nanobind kind does, names the runner labels that build its platform
wheels under `[nanobind] wheel-platforms` in its own `workshop.toml`;
the key refuses in a member that holds no nanobind. The extension
adds a wheels job to the release workflow: before the wave, one leg
per label builds that platform's wheels for the workspace's python
matrix through cibuildwheel, and the publish job waits for the legs
and ships the collected set. A workspace whose members hold no
nanobind has no wheels job.

Which members a job builds before the wave is the workspace's
`job-built-members` answer. nanobind answers it with every member that
holds nanobind and every member that releases a conan package, which
each leg creates for its host. In CI the wave publishes what the run
collected into those members' `dist/` and builds every other member
itself; a job-built member whose `dist/` holds nothing refuses the
wave, naming the job. A build refuses a wheel whose tag says the
wrong thing: python's build refuses a wheel tagged for a platform, and
nanobind's a pure one, so no wheel reaches the wave with a tag that
contradicts what built it.

Not covered today: attestation or signing of the built wheels, a
non-PyPI index for the github kind, and publishing anywhere but the
one configured index per workspace.
