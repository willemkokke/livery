# The local loop: fast by default, scenarios and setups by choice

Status: written 2026-09-30 from Willem's rulings on the loop runner's
seven decisions (issues #930 and #931); no phase started. Phase 1
(scenarios by name) touches no infrastructure and comes first; phase
2 (the runner as a host process) is where the time goes.

## The prompt (Willem)

> The local loop is not about testing the hosted CI exactly. It is
> about testing development workflows and scenarios end to end as
> quickly as possible, as well as testing against various
> infrastructure setups. So we want to target a much narrower subset
> of what the loop does now. The goal is to develop as fast as
> possible; I cannot wait 80 minutes every iteration. Setups are
> configurable scenarios, emulation defaults to no, and the default
> local loop is probably Gitea in host mode with uv and footman data
> cached. Both the cache server and the volumes: whatever is fastest
> for the infrastructure at hand, checked against how hse sets up its
> host runners; the uv cache is saved too; faster on GitHub does not
> mean best everywhere. A built-in workflow benchmarks the common
> setups against no caches and configures the fastest, on a schedule
> if wanted. Add `--purge-cache`. The job image was chosen
> arbitrarily: the smallest, fastest image that works, ideally built
> dynamically with only the tools the workspace would materialise,
> so a job's sync is a no-op, with a schedulable auto-update in
> production; the same for the Gitea docker runners. Live with the
> index deploy window for now; the tool store becomes a service
> later. I am going to want the ability to bring up persistent local
> development environments, both in docker and on the host directly
> (only Gitea makes that easy). They should become as disposable as
> uv has made venvs. Can we implement the e2e on top of a local
> devenv then? Willem, 2026-09-30

## What exists

- `fm ci.e2e` births a workspace on the dev forge and runs one fixed
  pass: the setup pull request, the verified skip, the members'
  landing, the ratchet, the scoped, prose and tests legs, the
  release act, the nightly, the dispatched gate and the contributed
  point (`livery.workshop._e2e`). Pass 18 took about 80 minutes to
  the release pull request's merge: 15 runner runs of 3 to 13
  minutes.
- The runner is a container built from `runner.Dockerfile` (node's
  Debian trixie image with the runner binaries), pinned to
  `linux/amd64` and emulated on this machine, because the two clang
  records ship no linux-arm build. Jobs run inside it (`:host`
  labels); the host's docker socket reaches it through `fm
  forge.dev.up --with-docker`.
- Every job downloads every tool object: the entry places the tool
  store, uv's cache and conan's home under the job's temp
  (`livery.workshop._env_tasks.runner_placements`), and the Gitea
  workflow emits no cache steps, where the GitHub one restores the
  store and conan's home through `actions/cache`. The runner's
  built-in cache server is on by default and unused.
- The wheels job cannot bind the runner container's own paths into
  cibuildwheel's manylinux container through the host's socket
  (#931), and cibuildwheel 3.4.1 spins forever on the failed start.
- The GitLab lane runs a second container from the same image with
  the shell executor; the emitted GitLab job image is
  `ghcr.io/astral-sh/uv:<pin>-python3.14-bookworm`, whose glibc is
  older than pyrefly's binary wants.
- hse's reference (`docs/admin/ci/runners/`): its host runners are
  `gitea-runner` processes on the machine (a launchd daemon on
  macOS, NSSM on Windows) with `:host` labels, a persistent tool
  store (`TOOLS_FOLDER`) and an explicit `host.workdir_parent`; no
  workflow uses `actions/cache`, the persistent store takes its
  place. Its containerised e2e runner mounts one volume carrying
  the tools cache and the workspace together, so uv stays in
  hardlink mode, and enables the cache server on a fixed address
  for job containers.

## Ground-truth contracts (do not violate)

1. **The loop's purpose.** Development workflows and scenarios end to
   end, as fast as possible, and infrastructure setups on demand. It
   is not a replica of the hosted CI. A pass names what it covers.
2. **Scenarios by name.** A pass runs the scenarios named, each
   declaring what it needs (members, a release, a runner with a
   daemon); the default set is the narrow, fast one; `all` is
   today's pass. Every scenario is idempotent: re-running is the
   recovery.
3. **Setups by name.** A setup is a forge (gitea, gitlab), a runner
   mode (a host process on the machine; a container in host mode; a
   container running docker jobs), what persists (the tool store,
   uv's cache, footman's data, conan's home; or nothing) and an
   architecture. The default is Gitea, the runner as a host process,
   native, with the store, uv's cache and footman's data persistent.
4. **Emulation is opt-in, per runner.** No `platform: linux/amd64`
   unless a runner is asked for it; an environment may run one
   emulated runner beside a native one, for an x64-only build or
   fault, never for speed.
5. **Caches are the rig's.** The tool store, uv's cache, conan's
   home and footman's data live in one directory of the rig, shared
   by every environment on the machine, and survive `--fresh` and
   the removal of any environment. `--purge-cache` removes them, and
   names each thing it removed.
6. **The bench measures caching where CI runs a lot.** On the hosted
   lane it runs the gate's legs under each cache variant the emitter
   can write (the tool store's archive restored or downloaded, uv's
   cache on or off, conan's home), one variant per run of a scheduled
   point, with `fm ci.timings` and the metrics series as the record,
   and the winning variant becomes what the emitter writes. Locally
   the host setup with the persistent store on one disk is the
   default without a bench, since nothing beats a store that is never
   deleted or restored; `fm ci.e2e.bench` stays a hand tool for the
   container setups.
11. **The layer under test is the loop's second axis.** `fm ci.e2e
    --layer=<name>` births the members of that layer's kinds,
    publishes the layer's own dev wheel to the environment's
    registry, and runs the scenarios against those members, release
    included. A plain pass runs `develop` on the base's python
    member; a pass with `--layer` runs the `release` set.
7. **A job image is built from the lock.** When a setup runs jobs in
   containers, the image carries the tools the workspace would
   materialise, at their locked digests and in the store's own
   layout, so a job's `fm sync` finds them and installs nothing. The
   image's tag carries the lock's digest; a moved lock rebuilds it.
   The same builder makes the GitLab job image and the Gitea
   docker-mode job image; in production a scheduled point rebuilds
   and pushes them.
8. **The index deploy window stays** (#929). The store becomes a
   service later; no phase here.
9. **Everything through `fm`.** The runner's registration, config and
   lifecycle are `fm forge.dev.up` and `fm forge.dev.down`; nothing
   is set up by hand and the loop never asks a person.
10. **Environments are named, persistent and disposable.** A local
    development environment is a forge with its runner and its
    seeded accounts, in docker or directly on the host, brought up by
    name from the store's records and the rig's seed, kept until
    removed, and removed whole by one verb. Its state is one
    directory; nothing else on the machine changes: no system
    service, no daemon registered with the operating system. Gitea
    comes in both modes; GitLab in docker only. Bringing one up from
    nothing costs seconds, not minutes, the way `uv venv` does. The
    loop runs on an environment: `fm ci.e2e --env=<name>`, by default
    a disposable `e2e` that `--fresh` removes and recreates. An
    environment has a configurable number of runners, each labelled
    `<env>-<host>-<arch>-<nn>` (`dev-macos-arm-01`,
    `dev-linux-x64-02`), the host and architecture the runner's own,
    so a job's `runs-on` names one runner shape of one environment.

## The design

### Scenarios

A registry in `livery.workshop._e2e`, one entry per scenario with
its name, what it needs and the function that proves it:

| Scenario | Needs | Proves |
|---|---|---|
| `birth` | the forge | `fm new.project`, protection, the setup pull request |
| `verified-skip` | birth | the proved tree skips |
| `members` | birth | the closure's members land through the gate |
| `ratchet` | members | the coverage ratchet |
| `scoped-leg` | members | a scoped gate on a branch |
| `prose-leg` | members | a prose-only branch |
| `tests-leg` | members | a tests-only branch |
| `release` | members, a daemon | the release act, the wave, the registries |
| `nightly` | members | the nightly point |
| `dispatched-gate` | members | a dispatched gate |
| `contributed-point` | members | a point a layer contributes |

`fm ci.e2e --scenario=<names>` runs those, in dependency order, and
refuses a name it does not know with the list. Named sets: `develop`
(birth, verified-skip, members, scoped-leg), `release` (develop plus
release), `points` (nightly, dispatched-gate, contributed-point),
`all`. The default set is `develop`. Each pass prints a table at the
end: scenario, wall time, runner runs, and writes the same rows to
the metrics series, so a slow scenario is a number.

### The layer under test

`fm ci.e2e --layer=<name>` (repeatable) is the second axis. The
members the pass births are the layer's: one member per kind the
layer registers (the cpp layer's cpp-conan member, the nanobind
layer's extension beside it), each from the layer's own template,
and the loop publishes the layer's dev wheel with the workshop's
closure so the environment's forge runs the layer as it is in the
tree. The scenarios then run against those members, the `release`
set by default, since testing a layer end to end means its release:
the gate on the layer's checks, the wave through the layer's
artifact kind, the receipts in the environment's registries. Without
`--layer` the pass births the base's python member and runs
`develop`. The set is overridable either way with `--scenario`.

### Setups

A table in code, `SETUPS`, one row per name:

| Name | Forge | Runner | Persists | Arch |
|---|---|---|---|---|
| `host` (default) | gitea | a host process | store, uv, data | native |
| `host-bare` | gitea | a host process | nothing | native |
| `container` | gitea | container, host mode | store, uv, data (cache server) | native |
| `container-bare` | gitea | a container, host mode | nothing | native |
| `docker-jobs` | gitea | a container running docker jobs | the cache server | native |
| `gitlab-host` | gitlab | a host process (shell executor) | store, uv, data | native |
| `amd64` | gitea | a container, host mode | as `container` | emulated |

`fm forge.dev.up --setup=<name>` brings the rig up in that shape and
records the name in the rig's environment file; `fm ci.e2e` reads it,
and `--setup=<name>` overrides for one pass. A setup that needs the
daemon (the `release` scenario builds wheels through cibuildwheel)
says so, and the pass refuses before the minutes are spent.

### Environments

`fm forge.dev.up --env=<name> --mode=docker|host` brings an
environment up by name; the default name is `dev` and the default
mode is `host`. Each environment owns one directory under the rig's
data directory (`forge-dev/envs/<name>/`): Gitea's data, the
runner's registration and working directory, the seed's credentials,
the ports it took, and the pid files of its processes. The caches
are not there: `forge-dev/cache/` holds the tool store, uv's cache,
conan's home and footman's data for every environment on the
machine, on one filesystem with the workspaces so uv keeps
hardlinking. A new environment is warm from its first job, and
removing one removes no cache. A GitLab environment shares it the
same way: GitLab itself runs in docker, since it ships as an omnibus
of services for Linux and no single binary, and its runner is a host
process with the shell executor, so its jobs read the rig's cache
directly. GitLab on the host is not offered.
`fm forge.dev.ls` lists them with their mode, ports and state;
`fm forge.dev.down --env=<name>` stops one and keeps its directory;
`fm forge.dev.rm --env=<name>` stops it and removes the directory
whole. Two environments run side by side on different ports, so a
loop pass and a hand experiment never share a forge.

In `host` mode Gitea is its own binary from a `download` record
(Gitea ships one per platform: darwin-arm64, linux-amd64,
linux-arm64, windows-amd64), started as a child of the rig with an
`app.ini` the verb writes (the install lock, the data path, the
ports), seeded by the same API calls the docker mode uses, with the
runner registered against it as described below. Nothing is
installed into the system: the environment is its directory and the
processes it starts, and `fm forge.dev.rm` leaves no trace. In
`docker` mode the compose project is named after the environment
(`livery-forge-dev-<name>`), its volumes with it, so `rm` removes
the project and its volumes and nothing else. The GitLab lane exists
in docker mode only.

Disposable means the cost of creating one is bounded by the store:
the binaries are records the store already holds after the first
environment, the seed is API calls, and a fresh environment is up in
seconds. A pass that wants a clean forge takes a new environment
instead of purging an old one.

The loop runs on an environment. `fm ci.e2e --env=<name>` births its
workspace on that environment's forge, publishes its dev wheels to
that forge's registry and follows its runs there; the default is a
disposable `e2e` environment, and `--fresh` removes it and creates it
again, which is the whole of what fresh means once environments
exist. A named environment kept between passes (`--env=dev`) is the
iterating case: the repository and the registry persist, and the
pass resumes where the last one stopped, as it does today.

### The runner as a host process

`gitea-runner` becomes a `download` record (its releases ship a raw
binary per host: darwin-arm64, linux-x64, linux-arm64, windows-x64),
so the store supplies it and `fm forge.dev.up --setup=host` registers
it against the local Gitea labelled `<env>-<host>-<arch>-<nn>:host`
(`dev-macos-arm-01`; one hardcoded name until environments exist),
which the loop's contract names as its runner, and starts it as a
child of the rig, its config written by the verb:
`host.workdir_parent` under the environment's directory and
`runner.envs` naming the rig's cache: `FOOTMAN_DATA_DIR`,
`UV_CACHE_DIR` and `CONAN_HOME` under `forge-dev/cache/`, on one
filesystem with the workspaces, hse's rule for uv's hardlinks. The
entry keeps a placement that is already set and places
under the job's temp only where nothing is set, which is the one code
change the hosted lane sees, and a no-op there. `fm forge.dev.down`
stops the process; `--purge-cache` removes the directory.

What this gives: no emulation, no image build, the machine's own
docker daemon and filesystem, so cibuildwheel's bind mounts resolve
and the manylinux images are native arm64, and every job after the
first finds the store, the wheels and conan's cache warm. What it does
not cover: a job's Linux-shaped steps (`apt-get` for docs
requirements) run on macOS and fail there, so a scenario needing them
names a container setup; a job's `runs-on` names the environment,
never a platform the machine is not.

The GitLab lane's runner follows in the same phase: `gitlab-runner`
as a record and a host process with the shell executor.

`fm forge.dev.up --env=dev --runners=<spec>` says how many runners
the environment has and of which shape, `host`, `container` or
`container:linux-x64` (emulated), one label per runner numbered from
`01`; the default is one host runner. Two runners of one shape share
the rig's cache; an emulated one has its own store directory, since
its objects are another host's.

### The container setups

The container runner stays for the setups that want it, built native
and without the pin once the clang records have a linux-arm build
(#930: the PyPI wheels of clang-format 23.1.1 and clang-tidy 22.1.8).
Persistence through the runner's cache server plus the Gitea
workflow's restore-and-save steps, emitted for the Gitea kind as they
are for GitHub, and the cache directory on a volume; or through one
volume mounted at the same absolute path on the host and in the
container, which also makes the bind mounts of #931 resolve. The
bench decides which the machine at hand prefers; both are setups.

### The bench

Two benches, one question: what is worth caching where CI runs a lot.

On the hosted lane, a `[[ci.schedule]]` point runs the gate's legs
under one cache variant per run: the tool store restored from the
`actions/cache` archive or downloaded from the index, uv's cache
restored or off, conan's home restored or rebuilt, per runner OS.
`fm ci.timings` reads the runs and the metrics series keeps them, so
the answer is a table of measured seconds per leg and variant; the
winning variant becomes what the emitter writes for that lane. The
claim the emitter makes today, that wheels download faster than an
archive restores, is the first thing it measures.

Locally, `fm ci.e2e.bench --setups=<names> --scenario=<set>` runs,
per setup: the rig up in that shape, a cold pass (`--fresh
--purge-cache`), a warm pass, and records both; then prints setup by
scenario, cold and warm, and the runner runs per scenario. It is a
hand tool for the container setups; the host setup with the
persistent store is the default without it.

### Images from the lock

`fm forge.dev.image` builds, from the workspace's lock and the store's
records, an image whose store already holds every tool the workspace
would materialise for linux-<arch>, at its locked digest and in the
store's layout, and whose base is the smallest that runs them
(Debian trixie-slim for pyrefly's glibc, with node only where the JS
actions need it: the Gitea runner and its docker-mode jobs, not the
GitLab jobs). The tag carries the lock's digest; `fm forge.dev.up`
rebuilds when the lock moved. The emitted GitLab `image:` and the
Gitea docker-mode label name that image instead of `uv`'s. In
production a scheduled point rebuilds and pushes the images to the
forge's registry when the lock moved, and the emitted workflows name
them by digest.

### cibuildwheel

The pin `cibuildwheel>=3.0,<4` resolves to 3.4.1. The 4 line raises on
a failed container start, verifies interpreter downloads, retries
downloads, pins container images by digest, and builds CPython 3.15
by default; it also makes `delvewheel` the default repair step on
Windows, so a Windows wheel carries the DLLs its extension needs,
which is wanted. cibuildwheel becomes a `pypi` record of the tool
store with the floor 4.2.1, in the nanobind kind's tool list beside
cmake, ninja and conan (Willem: in toolroom, like everything that can
be); the lock pins the newest version at each `fm tools.lock`, every
run installs that digest, and the backend calls the typed handle
instead of `uv tool run`. It is the one tool the code runs outside
the store today. Two things ride the same change: delvewheel
patches or creates `__init__.py` in each top-level package to add the
vendored DLLs' directory to the search path, and a nanobind package
lives in a PEP 420 namespace, so the repair command passes
`--namespace-pkg <namespace>` or the namespace gains an `__init__.py`
that breaks every sibling; and a test pins the wheel's file list on
the Windows leg. A wheel whose conan dependencies are static carries
nothing extra and the step is a no-op. A major 5 arrives through a
lock change, visible in review, never through a run.

## Phases

### Phase 1: scenarios by name

Deliverables: the registry and the sets; `--scenario`; the refusal
for an unknown name; the timing table and the metrics rows; `all`
runs today's pass unchanged. Tests, refusals first: an unknown
scenario refuses with the list; a scenario whose need is unmet
(`release` without a daemon) refuses before the pass; the sets
resolve in dependency order; the table has one row per scenario run.

**Acceptance**

- `fm ci.e2e --scenario=develop` on the current rig runs the four and
  prints their times; `fm store.show metrics` shows the rows.
- `fm ci.e2e --scenario=nonesuch` exits non-zero naming the list.

### Phase 2: environments, and the runner as a host process

Deliverables: the `gitea`, `gitea-runner` and `gitlab-runner`
records; `fm forge.dev.up --env --mode`, `fm forge.dev.ls`, `fm
forge.dev.down --env`, `fm forge.dev.rm --env`; `fm ci.e2e --env`
with the disposable `e2e` default and `--fresh` as remove-and-create;
the rig's cache directory; the environment's directory with its
ports and pid files; Gitea as a host process with
its written `app.ini`; the setups `host` and `gitlab-host`; the
runner's config and registration written by the verb; the persistent
placements; the entry's keep-what-is-set rule; `--purge-cache`; the
`linux/amd64` pin removed from the container setups once #930's
records land. Tests, refusals first: a setup name unknown refuses
with the table; an environment name that exists in the other mode
refuses naming the mode; a host runner already registered is reused,
not registered twice; `rm` of a running environment stops it first
and a second `rm` removes nothing; two environments take different
ports; `--purge-cache` names what it removed and a second purge
removes nothing; the entry leaves a set placement alone and places
under the temp otherwise.

**Acceptance**

- `fm forge.dev.up --env=scratch --mode=host` from nothing, on a
  machine whose store holds the records, is up and seeded within 30
  seconds, measured and quoted in the decision record; `fm
  forge.dev.rm --env=scratch` leaves no file and no process.
- `fm ci.e2e --scenario=develop` on the `host` setup, warm, completes
  in the time the bench records, quoted in the decision record with
  the cold time beside it.
- `fm ci.e2e --scenario=release` on the `host` setup builds the
  nanobind member's wheel through the machine's daemon and publishes;
  the loop pins the printed lines.

### Phase 3: the bench

Deliverables: the hosted lane's scheduled point over the cache
variants, the emitter's variant switch, the metrics rows and their
table through `fm ci.timings`; locally `fm ci.e2e.bench` with the
setups table. Tests, refusals first: a variant the emitter cannot
write refuses naming it; a setup the machine cannot provide (the
daemon absent for `docker-jobs`) is reported and skipped, never a
red bench; the table is one row per variant or setup and leg or
scenario, cold and warm.

**Acceptance**

- The scheduled point's first run on GitHub prints the table for the
  three gated runners; its rows are quoted in the decision record with
  the variant the emitter then writes.
- `fm ci.e2e.bench --setups=container,container-bare
  --scenario=develop` prints the table on this machine.

### Phase 3b: the layer under test

Deliverables: `--layer` on `fm ci.e2e`, the members per layer kind
from the layer's templates, the layer's dev wheel published with the
closure, the `release` set as the default under `--layer`. Tests,
refusals first: a layer not mounted refuses naming the mounted ones;
a layer with no kind refuses naming it; the members born match the
layer's kinds one to one. Waits for the cpp layer's extraction (the
extensible gate plan's open item 17) to have a layer to test.

**Acceptance**

- `fm ci.e2e --layer=livery.workshop.cpp` on the `host` setup births
  the cpp member, lands it and releases it; the loop pins the printed
  lines.

### Phase 4: images from the lock

Deliverables: `fm forge.dev.image`; the runner, the GitLab job and
the docker-mode job images; the rebuild on a lock move; the emitted
GitLab `image:` and docker-mode label naming the built image; the
production point that rebuilds and pushes by digest. Tests, refusals
first: a lock without a linux build of a tool refuses naming it; the
image's store passes `fm sync` with nothing installed; a moved lock
changes the tag.

**Acceptance**

- A job on the built image logs `tools: N receipt(s), all present`
  and installs nothing; the GitLab lane's develop set runs on it.

## Temporary, replaced by

| Temporary | Replaced by |
|---|---|
| the fixed pass of `fm ci.e2e` | the scenario registry and sets (phase 1) |
| the `linux/amd64` pin on the runner image | native container setups (phase 2, #930) |
| the runner container as the only runner | the host-process setups (phase 2) |
| one pinned compose project as the only environment | named environments (phase 2) |
| `uv`'s image as the GitLab job image | the image built from the lock (phase 4) |
| the wheels job's binds refused on the loop (#931) | the host setup (phase 2) |

## Decision record

- 2026-09-30, Willem, on the runner's store: both the cache server
  and the volumes, whichever is fastest for the infrastructure at
  hand, checked against hse's host runners; the uv cache is saved
  too; a built-in benchmark of common setups against no caches
  configures the fastest, on a schedule if wanted. Contracts 3 and 6.
- 2026-09-30, Willem: `--purge-cache`. Contract 5.
- 2026-09-30, Willem: setups are configurable scenarios; emulation
  defaults to no; the default is Gitea in host mode with uv and
  footman data cached; the loop tests development workflows and
  infrastructure setups, not the hosted CI exactly, and targets a
  narrower subset by default. Contracts 1 to 4.
- 2026-09-30, Willem, on the wheels job's binds: whatever fits the
  default setup best; 80 minutes an iteration is not acceptable. The
  host process's shared filesystem resolves them.
- 2026-09-30, Willem: the cibuildwheel pin was not a deliberate
  limit; the pros and cons of the 4 line are asked. Listed under
  "cibuildwheel"; the ruling is pending.
- 2026-09-30, Willem: the GitLab job image was arbitrary; the
  smallest, fastest image that works, ideally built with only the
  tools the workspace would materialise, with a schedulable
  auto-update in production, the same for the Gitea docker runners.
  Contract 7, phase 4.
- 2026-09-30, Willem: live with the index deploy window; the tool
  store becomes a separate service later. Contract 8.
- 2026-09-30, Willem: persistent local development environments,
  both in docker and directly on the host, which only Gitea makes
  easy; as disposable as uv has made venvs. Contract 10 and the
  "Environments" section; phase 2 carries them.
- 2026-09-30, Willem: a macOS host runner's label is based on the
  name of the persistent environment it serves; until environments
  exist the label is hardcoded to one name. So a job's `runs-on`
  names the environment, never a platform it is not, and the loop's
  contract carries the environment's name as its runner.
- 2026-09-30, Willem, on open item 2: the bench is for optimal CI
  caching wherever CI runs a lot, GitHub free for livery and locally
  too; locally, host mode with a persistent cache on the same disk,
  clonable, never deleted or restored, has nothing to beat. Contract
  6 and phase 3 restated: the hosted lane's cache variants under a
  scheduled point, the local bench a hand tool.
- 2026-09-30, Willem, on open item 1: when layers such as cpp are
  split out, the loop must test a new layer end to end including its
  release; that axis was missing. Contract 11 and phase 3b: `--layer`
  births the layer's members and runs the `release` set. The plain
  pass's default of `develop` is the agent's call, said so in the
  reply, open to his change.
- 2026-09-30, Willem: runners are named `<env>-<host>-<arch>-<nn>`,
  the number of runners per environment is configurable, and one may
  run in emulation beside a native one, for testing an x64-specific
  issue or build rather than for speed. Contract 4 and the runner
  section carry it; open item 4 restated.
- 2026-09-30, Willem's question, answered yes: the loop runs on top
  of a local environment. The consequence taken with it: the caches
  are the rig's and shared, an environment is the forge's state
  alone, and a fresh pass is a new environment. Contracts 5 and 10.
- 2026-09-30, Willem: shipping the dependency DLLs on Windows is
  wanted; cibuildwheel moves to `>=latest`, and it lives in toolroom
  like everything that can be: a `pypi` record with the floor 4.2.1,
  the lock pinning the newest. The "cibuildwheel" section carries the
  namespace flag and the wheel-list test that ride with it; open item
  5 closes.
- 2026-09-30, Willem: sharing the cache between all environments is
  the goal; GitLab environments too, in docker, with GitLab on the
  host not worth the complexity. The "Environments" section states
  how a GitLab environment shares the cache: its runner is the host
  process, GitLab stays a container.

## Open

1. Resolved 2026-09-30: `develop` for a plain pass, the `release` set
   under `--layer`; the layer under test is the second axis (contract
   11, phase 3b).
2. Resolved 2026-09-30: the bench measures caching where CI runs a
   lot, as a scheduled point on the hosted lane; locally the host
   setup with the persistent store needs no bench (contract 6).
3. Whether the host runner's store is the machine's own (fastest, and
   a job's sweep then judges the desk's objects) or its own
   directory warmed once. The bench measures both; the sweep's
   safety decides. Owner: the phase, with a line here.
4. Resolved 2026-09-30: a runner's label is
   `<env>-<host>-<arch>-<nn>`, the loop's contract names the label it
   wants, the count and the shape per runner are configured on the
   environment, and until environments exist one label is hardcoded.
   Decision record.
5. Resolved 2026-09-30: cibuildwheel is a `pypi` record with the
   floor 4.2.1, the lock pinning the newest, with the namespace flag
   on the Windows repair step and a wheel-list test. Decision record.
6. Gitea's version in host mode: the docker mode pins a nightly by
   digest for run cancellation; the record pins a release binary by
   version and digest, so the two modes may differ until 1.28 ships.
   Owner: the phase, with a line here.
