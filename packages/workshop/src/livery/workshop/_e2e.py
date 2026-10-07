"""``fm ci.e2e``: the CI and release story against a local forge.

The workshop's own development substrate: provision a scratch
repository on the seeded local forge, and drive the emitted
workflows through a real runner against the forge's own package
registry, so the whole second world runs consequence-free before
anything reaches a public forge.

The verb registers only where the workshop develops itself, the way
``fm forge.fixtures.record`` does: a consumer checkout does not
carry it at all.
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

import livery.footman as footman
from livery.footman import fail
from livery.forge import ForgeError
from livery.workshop._ci_tasks import ci
from livery.workshop._series import LOOP
from livery.workshop._verdict import Transient

if TYPE_CHECKING:
    from livery.forge import Forge, Job, Repository, Run
    from livery.workshop._checks import CheckRecord
    from livery.workshop._git_ops import GitOps
    from livery.workshop._packages import Package

#: The seeded organisation and the loop's one scratch repository.
E2E_OWNER = "livery"
E2E_REPO = "ci-e2e-loop"

#: The workshop's own test suite, present only in a source checkout:
#: this file is src/livery/workshop/_e2e.py, so the package directory
#: holding tests/ is three parents up.
_WORKSHOP_TESTS = Path(__file__).resolve().parents[3] / "tests"

#: The member the loop eats the dev wheels of; its dependency closure
#: rides along, read from the contracts by `dev_members`.
DEV_MEMBER = "packages/workshop"

#: What a reset of the loop's tree keeps: the directories `fm sync`
#: materialises, gitignored and read by the gate on the desk.
KEPT_BY_SYNC = (".venv", "typings", ".workshop")


@dataclass
class RunCount:
    """How many runner runs the pass has followed so far; the timing table reads it."""

    count: int = 0


#: The pass-wide count every wait on a run adds to, so a scenario's
#: row says how many runs it cost, not only how long it took.
RUNS = RunCount()


@dataclass
class Current:
    """The environment a pass runs on, as the loop reads its forge from it.

    Attributes:
        name: The environment's name; empty outside a pass.
        mode: `host` or `docker`.
        url: The forge's URL in host mode; empty in docker mode, where
            the cascade's shared file names it.
        token: The seeded API token in host mode.
        label: The runner label the loop's workspace names as its
            runner and its wheel platform; the hosted name in docker
            mode, the environment's own label in host mode.
    """

    name: str = ""
    mode: str = ""
    url: str = ""
    token: str = ""
    label: str = "ubuntu-latest"


#: The environment of the running pass. `ci.e2e` sets it from
#: `--env` before anything reads the forge; the tests set it by hand.
CURRENT = Current()


def with_label(lines: tuple[str, ...]) -> tuple[str, ...]:
    """*lines* with the hosted runner's name replaced by the pass's label.

    The proofs are written against `ubuntu-latest`, the name a born
    workspace's contract carries; on an environment whose runner has
    its own label, the emitted jobs and the coverage legs carry that
    label instead, and the proof reads the same lines with it.
    """
    if CURRENT.label == "ubuntu-latest":
        return lines
    return tuple(line.replace("ubuntu-latest", CURRENT.label) for line in lines)


def unsigned_environment(environ: Mapping[str, str]) -> dict[str, str]:
    """The variables that turn commit signing off for every git a pass runs.

    The loop's commits are scratch on a local forge and prove nothing
    by their signature, while a developer's signer (1Password's
    `op-ssh-sign` waits for a person) fails every commit of an
    unattended pass. git reads `GIT_CONFIG_COUNT` with a key and a
    value per index, so the setting rides the environment into the
    driver's own git, the birth's, and the loop's fm; an entry the
    outer environment already carries keeps its index and this one
    follows it.
    """
    count = 0
    raw = environ.get("GIT_CONFIG_COUNT", "")
    if raw.isdigit():
        count = int(raw)
    return {
        "GIT_CONFIG_COUNT": str(count + 1),
        f"GIT_CONFIG_KEY_{count}": "commit.gpgsign",
        f"GIT_CONFIG_VALUE_{count}": "false",
    }


def dev_members(root: Path, extensions: Sequence[str] | None = None) -> tuple[str, ...]:
    """The members whose dev wheels the loop eats: the workshop and what a birth lists.

    Directory names, the workshop first, then each member that ships
    an extension a birth lists, and each dependency after the first
    member that names it, once. An edge of every kind counts: the
    wheel the runner installs resolves all of them from the loop's
    registry, and a member the closure does not reach publishes
    nothing there. An extension depends on the workshop, never the
    other way round, so the workshop's closure alone leaves the
    newborn's listed extensions nothing to install. *extensions* is
    the stack a birth lists, the stock list when None.

    Raises:
        Failed: when *root* has no workshop member to eat.
    """
    from livery.workshop._extensions import SELF, distribution_of, listing
    from livery.workshop._new_project import birth_extensions
    from livery.workshop._packages import discover_packages

    by_path = {package.path: package for package in discover_packages(root)}
    workshop = by_path.get(DEV_MEMBER)
    if workshop is None:
        fail(f"{root}: no {DEV_MEMBER} member; the loop eats the workshop's dev wheels")
    listed = birth_extensions([SELF]) if extensions is None else extensions
    stock = {distribution_of(listing(entry).name) for entry in listed}
    shipping = sorted(
        (p for p in by_path.values() if p.name in stock and p is not workshop),
        key=lambda package: package.path,
    )
    ordered: list[str] = []
    pending = [workshop, *shipping]
    while pending:
        package = pending.pop(0)
        if package.member in ordered:
            continue
        ordered.append(package.member)
        pending.extend(
            by_path[edge.path] for edge in package.depends if edge.path in by_path
        )
    return tuple(ordered)


@dataclass(frozen=True)
class Lane:
    """One local forge the loop runs against, as the host and the runner reach it.

    Attributes:
        kind: The forge kind, ``gitea`` or ``gitlab``.
        alias: The forge as CI sees it, the compose service name and
            port, true on the host too through its hosts entry.
        url_var: The shared env key the seed writes the host-side URL
            to.
        token_var: The shared env key the seed writes the lane token to.
        version_path: The API path the hosts-entry probe reads.
        runner: The compose service of the lane's runner.
    """

    kind: str
    alias: str
    url_var: str
    token_var: str
    version_path: str
    runner: str

    @property
    def host(self) -> str:
        """The compose hostname alone, for the hosts entry."""
        return self.alias.removeprefix("http://").rsplit(":", 1)[0]

    def publish(self, base: str = "") -> str:
        """The PyPI upload base for the loop's packages at *base*, the alias by default.

        Gitea keeps an owner's registry; GitLab a project's, addressed
        by its URL-encoded path.
        """
        base = base or self.alias
        if self.kind == "gitlab":
            from urllib.parse import quote

            project = quote(f"{E2E_OWNER}/{E2E_REPO}", safe="")
            return f"{base}/api/v4/projects/{project}/packages/pypi"
        return f"{base}/api/packages/{E2E_OWNER}/pypi"

    def index(self, base: str = "") -> str:
        """The simple index the loop's packages are read from."""
        return f"{self.publish(base)}/simple"


#: The lanes, by forge kind.
LANES: dict[str, Lane] = {
    "gitea": Lane(
        "gitea",
        "http://gitea:3000",
        "GITEA_URL",
        "GITEA_TOKEN",
        "/api/v1/version",
        "act_runner",
    ),
    "gitlab": Lane(
        "gitlab",
        "http://gitlab:8929",
        "GITLAB_URL",
        "GITLAB_TOKEN",
        "/api/v4/version",
        "gitlab-runner",
    ),
}

#: The compose project the dev containers belong to; a container is
#: named ``<project>-<service>-1``.
_DEV_PROJECT = "livery-forge-dev"


def _require_runner_docker(kind: str) -> None:
    """Refuse until the lane's runner has the host's docker socket.

    The loop's native member builds its wheel through cibuildwheel on
    the runner, which needs a daemon, and the runners get the socket
    only from ``fm forge.dev.up --with-docker``. A runner that is not
    up at all is named the same way, so the refusal comes before the
    minutes a pass spends reaching the release act.
    """
    import json

    import livery.toolroom.tools as tools

    if CURRENT.mode == "host":
        # The runner is a process of this machine, and cibuildwheel
        # reaches the machine's own daemon: the one question is
        # whether it answers.
        info = tools.docker.opts(nofail=True, recorded=False)("info")
        if info.code != 0:
            fail(
                "the machine's docker daemon does not answer, and the loop's"
                " native member builds its wheel through cibuildwheel on it:"
                " start Docker Desktop, or the daemon"
            )
        return
    lane = _lane(kind)
    container = f"{_DEV_PROJECT}-{lane.runner}-1"
    result = tools.docker.opts(nofail=True, recorded=False)(
        "inspect", "--format", "{{json .Mounts}}", container
    )
    if result.code != 0:
        fail(
            f"the {kind} runner ({container}) is not up:"
            f" `{footman.prog()} forge.dev.up --profile={kind} --with-docker`"
        )
    try:
        mounts = json.loads(result.stdout or "[]")
    except ValueError:
        mounts = []
    destinations = {
        str(mount.get("Destination", "")) for mount in mounts if isinstance(mount, dict)
    }
    if "/var/run/docker.sock" not in destinations:
        fail(
            f"the {kind} runner has no docker socket, and the loop's native"
            " member builds its wheel through cibuildwheel on it:"
            f" `{footman.prog()} forge.dev.up --profile={kind} --with-docker`"
        )


def _lane(kind: str) -> Lane:
    """The lane for *kind*; refuses a kind with no local containers.

    On a host-mode environment the lane's alias is the environment's
    own URL: the forge and the runner are processes of this machine,
    so one localhost URL is true on both sides of the loop.
    """
    from dataclasses import replace

    lane = LANES.get(kind)
    if lane is None:
        fail(
            f"--forge={kind} is not a local lane: the loop runs against"
            f" {', '.join(LANES)}"
        )
    if CURRENT.mode == "host" and CURRENT.url:
        return replace(lane, alias=CURRENT.url)
    return lane


def _enter_host(values: Mapping[str, str], env: str) -> None:
    """Run the pass on the host-mode environment *env*, whose *values* these are.

    The forge's URL and token go into the pass's own environment as
    well as `CURRENT`. The cascade filled them when the pass started,
    before the bring-up seeded the environment, and every child the
    pass starts (the birth, the loop's own fm) copies that
    environment, so each would otherwise reach the forge with a token
    the environment no longer holds.
    """
    CURRENT.name, CURRENT.mode = env, "host"
    CURRENT.url, CURRENT.token = values["GITEA_URL"], values["GITEA_TOKEN"]
    CURRENT.label = values["LABELS"].split(",")[0]
    lane = LANES["gitea"]
    os.environ[lane.url_var], os.environ[lane.token_var] = CURRENT.url, CURRENT.token


def _dev_forge(kind: str) -> tuple[Forge, str]:
    """The seeded local forge and its token; refusal teaches.

    The credentials are the ones ``fm forge.dev.up`` writes into the
    shared env file the cascade reads, so a warm machine needs
    nothing beyond the containers being up.
    """
    lane = _lane(kind)
    url = CURRENT.url or os.environ.get(lane.url_var, "")
    token = CURRENT.token or os.environ.get(lane.token_var, "")
    if not url or not token:
        fail(
            f"the local {kind}'s credentials are not in the environment."
            f" Run `{footman.prog()} devenv.up`: it starts and seeds the"
            f" environment and writes {lane.url_var} and {lane.token_var}"
            " into the shared env file the cascade reads"
        )
    from livery.workshop._forge_lane import _connect

    return _connect(kind, url, token), token


def provision(kind: str = "gitea") -> None:
    """Ensure the loop's repository, idempotently, with its secrets.

    The repository is created on the seeded organisation when absent
    and reused when present. ``UV_PUBLISH_TOKEN`` is written as an
    Actions secret with the forge token's value, because the forge's
    own package registry authenticates with the same credential as
    its API. Re-running is the recovery procedure.
    """
    from livery.forge import ForgeError, RepoConfig

    forge, token = _dev_forge(kind)
    try:
        existing = forge.get_repo(E2E_OWNER, E2E_REPO)
    except ForgeError as error:
        fail(
            f"the local forge did not answer: {error}. Is the container"
            f" up? `{footman.prog()} forge.dev.up --profile=gitea`"
        )
    if existing is None:
        forge.create_repo(
            E2E_OWNER,
            E2E_REPO,
            private=False,
            description="The workshop's local CI loop. Scratch; recreated freely.",
        )
        print(f"  created {E2E_OWNER}/{E2E_REPO}")
    else:
        print(f"  reusing {E2E_OWNER}/{E2E_REPO}")
    repo = forge.repository(E2E_OWNER, E2E_REPO)
    if kind == "gitlab":
        _make_project_public(os.environ.get(_lane(kind).url_var, ""), token)
        print("  project: public, so its registry reads without a credential")
    # Three secrets, all the lane token: the registry credential the
    # publish reads, the forge lane's everyday token, and the admin
    # ladder's, because the emitted governance job asks for it and
    # the seeded admin account holds every grant anyway. GitLab's jobs
    # push through a fourth, a project access token, since the job
    # token cannot push.
    secrets = {
        "UV_PUBLISH_TOKEN": token,
        "FORGE_TOKEN": token,
        "FORGE_ADMIN_TOKEN": token,
    }
    if kind == "gitlab":
        secrets["GITLAB_PUSH_TOKEN"] = _mint_push_token(
            os.environ.get(_lane(kind).url_var, ""), token
        )
    repo.configure(RepoConfig(secrets=secrets))
    print(f"  secrets set: {', '.join(secrets)}")


#: One GitLab API call for the loop's provisioning: (method, path,
#: token, body) to (status, parsed body or text).
GitlabCall = Callable[[str, str, str, "dict[str, object] | None"], "tuple[int, object]"]


def _gitlab_call(
    method: str, url: str, token: str, body: dict[str, object] | None = None
) -> tuple[int, object]:
    """One GitLab API call for the loop's provisioning; (status, parsed body)."""
    import json
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"PRIVATE-TOKEN": token, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode()
            return int(response.status), (json.loads(text) if text else None)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")


def _make_project_public(url: str, token: str, api: GitlabCall | None = None) -> None:
    """Make the loop's GitLab project public, so its registry reads anonymously.

    The runner installs the dev wheels from the project's PyPI index
    and the loop locks against it here, both without a credential, as
    both read Gitea's owner registry. A private project's index
    answers 401 to both, whatever the group's visibility. *api* is the
    call, the live one by default.
    """
    from urllib.parse import quote

    api = api or _gitlab_call
    path = f"{url}/api/v4/projects/{quote(f'{E2E_OWNER}/{E2E_REPO}', safe='')}"
    status, body = api("PUT", path, token, {"visibility": "public"})
    if status != 200:
        fail(f"making {E2E_OWNER}/{E2E_REPO} public answered HTTP {status}: {body}")


def _mint_push_token(url: str, token: str, api: GitlabCall | None = None) -> str:
    """A project access token that may push to the loop's project; its value.

    GitLab's job token cannot push, so the state store, the receipt
    tags and the template artifact ride a project access token with
    ``write_repository``. A token's value is readable at minting
    alone, so every provisioning mints one and revokes the ones it
    minted before, by name. *api* is the call, the live one by
    default.
    """
    from datetime import UTC, datetime, timedelta
    from urllib.parse import quote

    api = api or _gitlab_call
    base = f"{url}/api/v4/projects/{quote(f'{E2E_OWNER}/{E2E_REPO}', safe='')}"
    name = "livery-loop-push"

    def call(
        path: str, *, method: str = "GET", body: dict[str, object] | None = None
    ) -> tuple[int, object]:
        return api(method, base + path, token, body)

    status, listed = call("/access_tokens")
    if status == 200 and isinstance(listed, list):
        for item in listed:
            if (
                isinstance(item, dict)
                and item.get("name") == name
                and item.get("active")
            ):
                call(f"/access_tokens/{item['id']}", method="DELETE")
    expires = (datetime.now(UTC) + timedelta(days=30)).date().isoformat()
    status, made = call(
        "/access_tokens",
        method="POST",
        body={
            "name": name,
            "scopes": ["api", "write_repository"],
            "access_level": 40,
            "expires_at": expires,
        },
    )
    if status != 201 or not isinstance(made, dict) or not made.get("token"):
        fail(f"the loop's push token was not minted: HTTP {status}: {made}")
    return str(made["token"])


#: Gitea's forge URL as both sides see it: the compose hostname,
#: which the runner resolves natively and the host through its
#: taught /etc/hosts alias. Every lane has one; `Lane.alias` is the
#: general form, and this name serves the Gitea-only callers.
ALIAS_URL = "http://gitea:3000"

#: The member's distribution name: new.package prefixes the
#: project's namespace (ci_e2e_loop), so the dist is never
#: "livery-loop-echo".
LOOP_MEMBER_DIST = "ci-e2e-loop-loop-echo"

#: The loop's members, name and template kind (empty for the default
#: python kind). The nanobind member forces the wheels path on
#: every pass: its wheel is built on the runner through cibuildwheel,
#: published, and installed in the isolated leg. The cpp-conan member
#: forces the native measurement and the conan path: its gate build
#: is measured by gcov beside the runner image's gcc and its lines
#: join the union, the release leg creates it into conan's cache, and
#: the wave publishes it through the forge's conan registry.
LOOP_MEMBERS: tuple[tuple[str, str], ...] = (
    ("loop-echo", "package-python"),
    ("loop-native", "package-python-nanobind"),
    ("loop-cpp", "package-cpp-conan"),
)

#: The member that contributes a point, and the point's name.
CONTRIBUTING_MEMBER = "loop-echo"
CONTRIBUTED_POINT = "echo-audit"


def _member_dist(name: str) -> str:
    """The distribution name ``new.package`` gives the loop member *name*."""
    return f"ci-e2e-loop-{name}"


def _serving_probe(root: Path, kind: str) -> Callable[[str], tuple[str, ...]]:
    """What the loop's registries serve of a member, by name: its versions.

    A python member is probed on the lane's simple index, a conan
    member through the conan target the loop's workspace resolves,
    the forge's own conan registry; each registry is built once and
    polled through the wave's wait.
    """
    from livery.forge import SimpleRegistry
    from livery.workshop._backends import _cpp_conan

    _, token = _dev_forge(kind)
    python = SimpleRegistry(_lane(kind).index(), token=token)
    conan: dict[str, _cpp_conan.ConanRegistry] = {}
    kinds = dict(LOOP_MEMBERS)

    def versions(name: str) -> tuple[str, ...]:
        if kinds.get(name) != "package-cpp-conan":
            return python.versions(_member_dist(name))
        if "conan" not in conan:
            from livery.workshop._registries import resolve_registry

            target = resolve_registry(root, "conan")
            conan["conan"] = _cpp_conan.ConanRegistry(target, root=root)
        return conan["conan"].versions(_member_dist(name))

    return versions


def _require_host_alias(kind: str = "gitea") -> None:
    """Refuse until the host resolves the lane's compose hostname.

    One registry URL must be true on both sides of the loop: the
    runner resolves the compose service name, the host does not, and
    a split URL forces an unlocked workspace and a hostname fork
    through every file. The one-line alias makes the compose name
    true on the host too, and the lock then carries a URL both sides
    can read.
    """
    import urllib.error
    import urllib.request

    if CURRENT.mode == "host":
        # A localhost URL needs no alias: the same address is true for
        # the driver, the forge and the runner, all on this machine.
        return
    lane = _lane(kind)
    try:
        with urllib.request.urlopen(lane.alias + lane.version_path, timeout=2):
            return
    except urllib.error.HTTPError:
        # The host answered: GitLab's version endpoint refuses an
        # anonymous read with 401, and the alias resolved all the same.
        return
    except (urllib.error.URLError, TimeoutError, OSError):
        fail(
            f"the host cannot reach {lane.alias}, the compose"
            " hostname CI uses for the registry (a network resolving"
            f" '{lane.host}' elsewhere fails the same way; /etc/hosts wins"
            " over DNS). Add the alias once:"
            f" sudo sh -c 'echo \"127.0.0.1 {lane.host}\" >> /etc/hosts'"
        )


def _loop_home(kind: str) -> Path:
    """Where the lane's workspace lives: durable, per machine and per forge.

    One directory per lane: the workspaces have different remotes,
    different registries and different histories, and one pass must
    never adopt the other lane's checkout.
    """
    from livery.footman import data_dir

    home = data_dir() / "workshop-e2e"
    if CURRENT.name:
        home = home / CURRENT.name
    return home / kind


def _dev_pins(root: Path, head: str, members: tuple[str, ...]) -> dict[str, str]:
    """The dev versions this pass built for *members*, by distribution name.

    Read from the wheels [livery.workshop._e2e._dev_wheels][] finds.
    """
    return {
        name: wheel.name.split("-", 2)[1]
        for name, wheel in _dev_wheels(root, head, members).items()
    }


def _dev_wheels(root: Path, head: str, members: tuple[str, ...]) -> dict[str, Path]:
    """The wheel this pass built for each of *members*, by distribution name.

    The newest wheel in each member's ``dist``, which the dev act just
    filled, checked against *head*: a dev version's local segment is
    ``<branch>.<sha>.<date>``, ``.dirty`` appended for a tree no
    commit describes, so a wheel a previous pass left behind can never
    pin the loop. Refuses, naming the member, when a dist holds no
    wheel or its newest wheel is another commit's.
    """
    found: dict[str, Path] = {}
    for member in members:
        dist = root / "packages" / member / "dist"
        wheels = sorted(dist.glob("*.whl"), key=lambda wheel: wheel.stat().st_mtime)
        if not wheels:
            fail(f"no dev wheel in {dist}: the dev act built nothing for {member}")
        name, version = wheels[-1].name.split("-", 2)[:2]
        local = version.partition("+")[2].split(".")
        if local and local[-1] == "dirty":
            local.pop()
        # The sha as git describe spells it, ``g`` first; a hex sha
        # never starts with a ``g`` of its own.
        sha = local[-2].removeprefix("g") if len(local) >= 2 else ""
        if not sha or not head.startswith(sha):
            fail(
                f"the newest wheel in {dist} is {wheels[-1].name}, built from"
                f" {sha or 'no commit'}, not from HEAD {head[:12]}: the dev"
                f" act did not build this pass's {member}"
            )
        found[name.replace("_", "-")] = wheels[-1]
    return found


def _dev_split(
    root: Path, git: GitOps, stack: Sequence[str] = ()
) -> tuple[list[str], dict[str, str]]:
    """The dev members this pass builds, and the release each other one pins, by member.

    A member nothing unreleased touches cannot be built as a dev
    wheel: that number would sort below its release and satisfy no
    floor naming it. The loop pins the release instead, and drops the
    member's stale rehearsal wheels from the registry, which a
    first-index resolve would otherwise pick over the release.
    """
    from livery.workshop._dev_release import unchanged_since_release
    from livery.workshop._packages import discover_packages

    packages = {package.member: package for package in discover_packages(root)}
    changed: list[str] = []
    released: dict[str, str] = {}
    for member in dev_members(root, tuple(stack) or None):
        version = unchanged_since_release(root, git, packages[member])
        if version:
            released[member] = version
        else:
            changed.append(member)
    return changed, released


def write_dev_index(folder: Path, wheels: Collection[Path]) -> Path:
    """Lay *wheels* out at *folder* as a simple index uv reads; *folder*.

    One directory per project, named as PEP 503 normalises it, holding
    the project's wheels and a page that links them, and a root page
    that links the projects. Whatever *folder* held before goes, so a
    wheel an earlier pass built is never offered again.
    """
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    projects: dict[str, list[Path]] = {}
    for wheel in wheels:
        name = re.sub(r"[-_.]+", "-", wheel.name.split("-", 1)[0]).lower()
        projects.setdefault(name, []).append(wheel)
    for name, files in sorted(projects.items()):
        (folder / name).mkdir()
        for wheel in files:
            shutil.copy2(wheel, folder / name / wheel.name)
        _index_page(folder / name, sorted(wheel.name for wheel in files))
    _index_page(folder, [f"{name}/" for name in sorted(projects)])
    return folder


def _index_page(directory: Path, targets: list[str]) -> None:
    """Write *directory*'s ``index.html``: one link per target."""
    links = "".join(f'<a href="{target}">{target}</a>\n' for target in targets)
    (directory / "index.html").write_text(
        f"<!DOCTYPE html>\n<html><body>\n{links}</body></html>\n", "utf-8"
    )


def checkout_index(
    root: Path, folder: Path, extensions: Sequence[str] | None = None
) -> str:
    """Build what a newborn installs from *root* as a local index at *folder*; its URL.

    The members are [livery.workshop._e2e.dev_members][]: the workshop,
    what it depends on, and the extensions a birth lists. Each is built
    with ``uv build`` at the version its manifest declares, and
    [livery.workshop._e2e.write_dev_index][] lays the wheels out. Named
    first in ``UV_INDEX``, the index serves every distribution it holds,
    whatever its version, because uv takes a package from the first
    index that has it; every other package comes from the next.
    ``UV_FIND_LINKS`` would not do: a folder named there only adds
    candidates, and a stable release on PyPI beats a dev wheel
    (measured). This is how a workspace installs this checkout's code
    before a release, never the dev act, which on a main-family branch
    is the release train. *extensions* is the stack the birth lists,
    the stock list when None.
    """
    import tempfile

    import livery.toolroom.tools as toolroom
    from livery.workshop._packages import discover_packages

    members = set(dev_members(root, extensions))
    skipped = shutil.ignore_patterns(
        ".venv", "dist", "build", "__pycache__", "_docs", "*.egg-info"
    )
    with tempfile.TemporaryDirectory() as scratch:
        built = Path(scratch) / "wheels"
        for package in discover_packages(root):
            if package.member not in members:
                continue
            copy = Path(scratch) / "copies" / package.member
            shutil.copytree(package.directory, copy, ignore=skipped)
            _stamp_content(replace(package, directory=copy))
            toolroom.uv.opts(cwd=root, recorded=False)(
                "build", "--wheel", "--out-dir", str(built), str(copy)
            )
        write_dev_index(folder, sorted(built.glob("*.whl")))
    return folder.as_uri()


def _stamp_content(package: Package) -> None:
    """Give *package*'s version a local segment naming its tree's content.

    A rebuilt wheel at an unchanged version carries the last one's file
    name, and uv installs a cached wheel by its name: a birth installed
    the code from before a fix (measured). The segment is a digest of
    the package's files, so a change gets a new name and an unchanged
    tree the same one, and every floor the version met still holds, a
    local version sorting with its public one. The kind's own stamper
    writes it, where the kind keeps its version.

    Raises:
        Failed: when the kind's stamper finds no version to stamp.
    """
    import hashlib

    from livery.workshop._backends import backend_for

    backend = backend_for(package)
    tree = package.directory
    digest = hashlib.sha256()
    for path in sorted(path for path in tree.rglob("*") if path.is_file()):
        digest.update(path.relative_to(tree).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    version = backend.current_version(package)
    backend.stamp_version(package).stamp(
        f"{version}+checkout.{digest.hexdigest()[:12]}"
    )


def _dev_index(kind: str, stack: Sequence[str] = ()) -> str:
    """This checkout's index under the lane's home, for the birth to read; its URL.

    The birth locks the newborn before the pass publishes anything, and
    every extension the birth lists is in that first lock, released or
    not: [livery.workshop._e2e.checkout_index][] builds them.
    """
    from livery.workshop._extensions import workspace_root

    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    folder = _loop_home(kind).parent / f"{kind}-dev-index"
    url = checkout_index(root, folder, tuple(stack) or None)
    held = sum(1 for entry in folder.iterdir() if entry.is_dir())
    print(
        f"  checkout index: {held} distributions at {folder}, read first by the birth"
    )
    return url


def _publish_dev_wheels(kind: str, stack: Sequence[str] = ()) -> dict[str, str]:
    """Publish the workspace's dev wheels to the loop's registry; the pins.

    The loop installs the workshop being edited, so every pass
    publishes fresh dev wheels first: a lock pinned to an older dev
    version would test yesterday's code with today's templates. The
    dev act is idempotent at a given commit, and a re-publish of the
    same version walks past. A dirty tree is the exception: its dev
    version is the same for every edit, so the wheels it published
    before are dropped first, and the pass installs the tree as it is
    now. Returns the published versions by distribution name, for the
    loop's lock to pin exactly.
    """
    from livery.footman import run
    from livery.workshop._extensions import workspace_root
    from livery.workshop._git_ops import GitOps
    from livery.workshop._packages import discover_packages

    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    _, token = _dev_forge(kind)
    git = GitOps(root)
    packages = {package.member: package for package in discover_packages(root)}
    changed, pinned = _dev_split(root, git, stack)
    released = {packages[member].name: version for member, version in pinned.items()}
    for member, version in pinned.items():
        print(
            f"  {member}: nothing unreleased since {version}; the loop pins the release"
        )
    if released:
        purged = _purge(
            kind, os.environ.get(_lane(kind).url_var, ""), token, names=released
        )
        print(
            f"  registry: {len(purged)} stale rehearsal release(s) of the pinned"
            " member(s) dropped"
        )
    if changed and not git.is_clean():
        purged = _purge(
            kind,
            os.environ.get(_lane(kind).url_var, ""),
            token,
            names={packages[member].name for member in changed},
        )
        print(
            f"  registry: {len(purged)} dev wheel(s) of the dirty tree dropped,"
            " so this pass installs the tree as it is now"
        )
    if changed:
        run(
            # The runner under its own name: a branded instance is not
            # called `fm`, and a literal would spawn nothing there.
            [footman.prog(), "--yes", "workflow.release", *changed],
            cwd=root,
            # The whole environment, extended: env= replaces, and a bare
            # pair would strip PATH from under the child fm.
            env={
                **os.environ,
                "PYTHON_PUBLISH_INDEX": _lane(kind).publish(),
                "UV_PUBLISH_TOKEN": token,
            },
        )
    pins = _dev_pins(root, git.head_sha(), tuple(changed)) | released
    print("  dev wheels: published to the loop's registry")
    return pins


def _purge(
    kind: str, url: str, token: str, *, names: Collection[str] | None = None
) -> list[str]:
    """Drop the loop's packages from the registry at *url*: every one, or *names*.

    Gitea keeps them under the owner, GitLab under the project.
    """
    from livery.forge._registry import purge_gitlab_packages, purge_packages

    project = f"{E2E_OWNER}/{E2E_REPO}"
    if names is None:
        if kind == "gitlab":
            return purge_gitlab_packages(url, project, token=token)
        return purge_packages(url, E2E_OWNER, token=token)
    if kind == "gitlab":
        return purge_gitlab_packages(url, project, token=token, names=names)
    return purge_packages(url, E2E_OWNER, token=token, names=names)


def start_over(
    lane: Forge,
    token: str,
    root: Path,
    *,
    url: str,
    wait: float = 120.0,
    kind: str = "gitea",
) -> list[str]:
    """Delete the loop's repository, its registry releases, and *root*; the lines.

    Refuses while *root* holds commits its origin has not seen, since
    the repository they would land in is about to go. A repository or
    a release already gone is not an error: the next birth wants them
    absent, and a re-run of the reset is the recovery procedure. A
    delete the forge answers too slowly for the client completes on
    the server anyway, so the reset waits up to *wait* seconds for
    the repository to be gone before it refuses.
    """
    if (root / ".git").is_dir():
        unpushed = _unpushed_commits(root)
        if unpushed:
            listed = "\n".join(f"    {line}" for line in unpushed)
            fail(
                f"{root} holds commits its origin has not seen:\n{listed}\n"
                "  push or discard them before starting over"
            )
    lines: list[str] = []
    try:
        lane.delete_repo(E2E_OWNER, E2E_REPO)
    except ForgeError as error:
        if not _gone_within(lane, wait):
            raise ForgeError(
                f"{E2E_OWNER}/{E2E_REPO} is still on the dev forge after the"
                f" delete's wait: {error}"
            ) from error
        lines.append(
            f"  deleted {E2E_OWNER}/{E2E_REPO} on the dev forge (the delete"
            " outran the client's wait and finished on the server)"
        )
    else:
        lines.append(f"  deleted {E2E_OWNER}/{E2E_REPO} on the dev forge")
    purged = _purge(kind, url, token)
    lines.append(
        f"  purged {len(purged)} release(s) from the registry"
        + (f": {', '.join(purged)}" if purged else "")
    )
    if root.exists():
        _rmtree(root)
        lines.append(f"  removed {root}")
    return lines


def _gone_within(lane: Forge, wait: float) -> bool:
    """Whether the loop's repository is gone from the forge within *wait* seconds."""
    import time

    deadline = time.monotonic() + wait
    while True:
        if lane.get_repo(E2E_OWNER, E2E_REPO) is None:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(5.0, max(0.0, deadline - time.monotonic())))


def _rmtree(path: Path) -> None:
    """Remove *path* whole; git's pack files are read-only, which Windows honours."""
    import stat

    for entry in path.rglob("*"):
        if entry.is_file() and not entry.is_symlink():
            entry.chmod(entry.stat().st_mode | stat.S_IWRITE)
    shutil.rmtree(path)


def _unpushed_commits(root: Path) -> list[str]:
    """The commits on any local branch of *root* that its origin does not hold."""
    import livery.toolroom.tools as toolroom

    fetched = toolroom.git.opts(cwd=root, nofail=True, recorded=False)(
        "fetch", "--quiet", "origin"
    )
    if fetched.code != 0:
        return []  # no reachable origin: nothing there could hold them anyway
    listed = toolroom.git.opts(cwd=root, nofail=True, recorded=False)(
        "log", "--oneline", "--branches", "--not", "--remotes=origin"
    )
    return [line for line in listed.stdout.splitlines() if line.strip()]


def _birth(kind: str, url: str, index: str = "", stack: Sequence[str] = ()) -> Path:
    """Birth or resume the loop's workspace; the root it lives at.

    ``fm new.project`` owns the whole half: seeds, git, repository,
    protection, and the setup pull request. It runs as a child of the
    pass, this interpreter's own footman with the pass's environment:
    a task run in-process starts from the run's pinned environment,
    and the pass's own settings, its unsigned commits, would never
    reach the birth's git. A workspace already there is resumed, so
    this is the recovery procedure too. *index*, when given, is
    searched before any other index the environment names
    ([livery.workshop._e2e._dev_index][]); *stack*, when given, is the
    extensions the workspace lists instead of the stock list.
    """
    import sys

    home = _loop_home(kind)
    env = {**os.environ, **unsigned_environment(os.environ)}
    if index:
        env["UV_INDEX"] = " ".join(filter(None, (index, os.environ.get("UV_INDEX"))))
    home.mkdir(parents=True, exist_ok=True)
    # A workspace already there is a birth to resume: the verb refuses
    # to start a second one in its folder.
    resume = ["--resume"] if (home / E2E_REPO / "workshop.toml").is_file() else []
    result = footman.run(
        [
            sys.executable,
            "-m",
            "livery.footman",
            "--yes",
            "new.project",
            E2E_REPO,
            f"--forge={kind}",
            f"--owner={E2E_OWNER}",
            f"--url={_lane(kind).alias}",
            "--description=The workshop's local CI loop. Scratch; recreated freely.",
            *([f"--stack={','.join(stack)}"] if stack else []),
            *resume,
        ],
        cwd=home,
        env=env,
        nofail=True,
        timeout=900.0,
    )
    print(result.stdout.rstrip("\n"))
    if result.code != 0:
        fail(f"the loop's birth exited {result.code}:\n{result.stderr}")
    return home / E2E_REPO


def _has_remote(root: Path) -> bool:
    """Whether the loop's workspace at *root* has its forge remote."""
    import livery.toolroom.tools as toolroom

    probe = toolroom.git.opts(cwd=root, nofail=True, recorded=False)
    return probe("remote", "get-url", "origin").code == 0


def _authenticate_remote(root: Path, token: str, kind: str = "gitea") -> None:
    """Embed the lane token in the scratch workspace's remote.

    Birth writes a credential-free remote by design and a human's
    pushes ride their credential helper; the loop is automation on a
    scratch workspace, so the token rides the remote URL instead.
    Unrecorded, so the credential never reaches a receipt. Gitea
    validates the token and ignores the username (measured), and
    ``oauth2`` is the conventional stand-in.
    """
    import livery.toolroom.tools as toolroom

    bare = _lane(kind).alias.removeprefix("http://")
    url = f"http://oauth2:{token}@{bare}/{E2E_OWNER}/{E2E_REPO}.git"
    result = toolroom.git.opts(cwd=root, nofail=True, recorded=False)(
        "remote", "set-url", "origin", url
    )
    if result.code != 0:
        fail(f"git remote set-url exited {result.code}")


def _lock_pins(root: Path, pins: dict[str, str]) -> None:
    """Lock the loop onto exactly the dev wheels this pass published.

    The dev number counts commits since the release tag, so a longer
    branch publishes a higher number and a lock left to upgrade
    freely keeps resolving that branch's wheels (measured: the loop
    ran one branch's emitter under another branch's wheels). The
    pins name the four versions outright; everything else keeps its
    locked version.
    """
    import livery.toolroom.tools as toolroom

    args = [f"--upgrade-package={name}=={version}" for name, version in pins.items()]
    result = toolroom.uv.opts(cwd=root, nofail=True)("lock", *args)
    if result.code != 0:
        fail(
            f"uv lock in the loop workspace exited {result.code}:"
            f"\n{result.stdout}{result.stderr}"
        )


def _eat_dev_wheels(root: Path, pins: dict[str, str], kind: str = "gitea") -> str:
    """Point the workspace at this pass's dev wheels; the pushed head sha.

    The registry joins the contract, the lock pins the wheels the
    pass just published, and only then does the workspace re-render
    through its own fm, so the files the runner executes come from
    the templates and the emitter under test. Measured necessity: a
    released workshop predating the emitted verbs fails the docs job
    with "no task named", and a render before the re-lock ships the
    previous pass's workflows. Idempotent: an already-wired workspace
    pushes nothing.
    """
    from livery.workshop._git_ops import GitOps
    from livery.workshop._new_project import _SETUP_BRANCH

    git = GitOps(root)
    # main is protected by the birth's own assertion, so the wiring
    # rides the setup branch and its pull request proves the gate.
    # The branch is pass-owned: rebuilt from current main every
    # time, because a reused branch atop stale bases conflicts with
    # the very squashes it produced (measured: green statuses,
    # mergeable false, the merge API answering 'try again later'
    # forever).
    _fresh_branch(root, _SETUP_BRANCH)
    lane = _lane(kind)
    host_url = os.environ.get(lane.url_var, "")
    contract = root / "workshop.toml"
    if contract.is_file():
        body = contract.read_text("utf-8")
        if host_url and host_url in body:
            contract.write_text(body.replace(host_url, lane.alias), "utf-8")
    loop_index, loop_publish = lane.index(), lane.publish()
    # The registry lives in the contract, and the template renders it
    # into pyproject from there: every re-render preserves the wiring
    # (a roster change re-renders pyproject too, and an unwired
    # render re-locks the workshop back to the released index).
    contract_file = root / "workshop.toml"
    original = contract_file.read_text("utf-8")
    contract_text = original
    # No prerelease declaration: uv's default admits a prerelease
    # where only prereleases exist or a requirement names one, and
    # the first-index strategy serves the workshop from the loop
    # index alone, so the dev wheels resolve with nothing declared
    # (measured). A global "allow" is the measured trap: it resolved
    # mkdocs 2.0.dev3 from PyPI and the docs job lost
    # mkdocs.exceptions. The heal below removes any mode an earlier
    # pass declared.
    if "[registries.python]" not in contract_text:
        contract_text = (
            contract_text.rstrip("\n")
            + "\n\n# The loop eats the workshop's dev wheels from the local\n"
            + "# registry; the compose hostname is true on both sides,\n"
            + "# the host through its /etc/hosts alias. The publish base\n"
            + "# is where the release wave uploads; without it the wave\n"
            + "# refuses rather than default an upload endpoint.\n"
            + "[registries.python]\n"
            + f'url = "{loop_index}"\n'
            + f'publish = "{loop_publish}"\n'
        )
    elif f'publish = "{loop_publish}"' not in contract_text:
        contract_text = contract_text.replace(
            f'url = "{loop_index}"\n',
            f'url = "{loop_index}"\npublish = "{loop_publish}"\n',
            1,
        )
    contract_text = re.sub(r'prerelease = "[^"]*"\n', "", contract_text)
    if "[docs]" not in contract_text and "docs" in _listed_extensions(root):
        # The loop builds the site for real and publishes nowhere:
        # the runner container has no docker, and the release act
        # needs main green (an undeclared seam defaults to the forge
        # kind's, container on gitea, which dies on the missing
        # docker).
        contract_text = (
            contract_text.rstrip("\n")
            + "\n\n# The site builds for real; nothing serves it here.\n"
            + "[docs]\n"
            + 'publish = "none"\n'
        )
    if "python-versions" not in contract_text:
        # The fast shape: one python per point, the loop's whole
        # matrix; livery's own contract keeps the derived pair.
        marker = "\n[ci]\n"
        if marker not in contract_text:
            fail(
                "the loop's contract has no [ci] table to declare its"
                " python on; birth seeds one, so this workspace was not"
                " born by the loop"
            )
        contract_text = contract_text.replace(
            marker, marker + 'python-versions = ["3.14"]\naffected-legs = true\n', 1
        )
    if CURRENT.label != "ubuntu-latest":
        # The environment's runner answers to its own label, so the
        # workspace names it: the check legs run there, and the wheels
        # job builds there.
        contract_text = contract_text.replace(
            'runners = ["ubuntu-latest"]', f'runners = ["{CURRENT.label}"]', 1
        )
    if "speed-marks" not in contract_text:
        # Seeded on its own: an adopted loop born before the key
        # exists keeps its contract, and the judge stays off without
        # it, which the scoped-leg proof pins.
        contract_text = contract_text.replace(
            "\n[ci]\n", "\n[ci]\nspeed-marks = true\n", 1
        )
    if "host-allowed" not in contract_text:
        # The allowance for the build tools: a runner with cmake and
        # ninja on PATH serves them from the host, one without takes
        # the store's, and the sync says which. The loop's container
        # runner has neither, so the pass proves the store's path.
        marker = "\n[tools]\n"
        if marker not in contract_text:
            fail(
                "the loop's contract has no [tools] table to allow host"
                " tools in; birth seeds one, so this workspace was not"
                " born by the loop"
            )
        contract_text = contract_text.replace(
            marker, marker + 'host-allowed = ["cmake", "ninja"]\n', 1
        )
    if "[[ci.schedule]]" not in contract_text:
        # The schedule seam's first entry: the nightly point replays
        # the member's released wheel, so a dispatched nightly proves
        # a task scheduled through the contract with no YAML change.
        contract_text = (
            contract_text.rstrip("\n")
            + "\n\n# The nightly point replays the released member.\n"
            + "[[ci.schedule]]\n"
            + 'point = "nightly"\n'
            + 'task = "release.replay"\n'
            + 'args = ["loop-echo", "--python={python}"]\n'
        )
    if contract_text != original:
        contract_file.write_text(contract_text, "utf-8")
    # Birth's pyproject predates the registry in the contract, and the
    # lock below must already read the loop index to find the dev
    # wheels, before the loop's own fm can run them. The pass's own
    # workshop renders it, the code the dev wheels are built from, as
    # it rendered the birth. From nothing: an edit an earlier pass
    # committed would be a local override the render keeps and drift
    # refuses.
    (root / "pyproject.toml").unlink(missing_ok=True)
    _render_with_the_pass(root)
    # Lock first, render second: the loop's fm syncs its venv from
    # the lock, so the render runs the workshop this pass published,
    # and the workflows it emits are the emitter under test. The
    # render may rewrite pyproject (the registry wiring, the roster),
    # so the lock settles on the same pins once more afterwards; an
    # unchanged pyproject makes that a no-op.
    _lock_pins(root, pins)
    _loop_fm(root, "drift.check", "--fix")
    _lock_pins(root, pins)
    # Cleanliness is the truth, not this run's edits: a resumed
    # half-wired workspace still commits and pushes here.
    if git.is_clean():
        print("  dev wheels: already wired")
    else:
        git.commit_all(
            "chore: the loop eats the dev wheels\n\nThe registry index"
            " joins uv's config on the compose hostname, true on both"
            " sides, and the lock pins the workshop's own dev wheels,"
            " so the installed workshop matches the emitter that"
            " rendered these workflows."
        )
        # Force: the branch is rebuilt from main each pass, so the
        # remote's copy is always superseded, like everything scratch.
        # The head this push supersedes keeps its runs moving with
        # nothing left to read them: they are cancelled after the push.
        previous = ""
        try:
            previous = git.remote_head(_SETUP_BRANCH)
        except Exception:  # no such branch on origin yet
            previous = ""
        git.push_force(_SETUP_BRANCH)
        if previous and previous != git.head_sha():
            from livery.workshop._ci_tasks import cancel_superseded_runs

            forge_lane, _ = _dev_forge(kind)
            for line in cancel_superseded_runs(
                forge_lane.repository(E2E_OWNER, E2E_REPO), previous
            ):
                print(line)
        print("  dev wheels: wired and pushed")
    return git.head_sha()


def _runs_patiently(
    repo: Repository,
    transient: Transient,
    *,
    head_sha: str = "",
    subject: str,
) -> tuple[Run, ...] | None:
    """One poll of the runs; ``None`` for a transport error within the budget.

    A loaded local forge answers a runs listing in minutes, and the
    client's timeout is shorter: the poll is retried like the ``ci``
    verbs retry theirs, and only a spent budget is the verdict.
    """
    try:
        runs = repo.checks.runs(head_sha=head_sha)
    except ForgeError as error:
        if transient.note(error):
            fail(transient.giving_up(subject))
        return None
    transient.reset()
    return runs


def _watch_latest(kind: str, workflow: str, *, timeout: float = 900.0) -> None:
    """Follow the newest run of *workflow* to its verdict; red fails verbatim."""
    import time

    forge, _ = _dev_forge(kind)
    repo = forge.repository(E2E_OWNER, E2E_REPO)
    deadline = time.monotonic() + timeout
    transient = Transient(interval=5)
    while True:
        listed = _runs_patiently(repo, transient, subject=f"{workflow}'s runs")
        if listed is None:
            time.sleep(5)
            continue
        runs = [run for run in listed if run.workflow.endswith(workflow)]
        latest = max(runs, key=lambda run: run.id, default=None)
        if latest is not None and latest.status == "completed":
            if latest.conclusion != "success":
                fail(
                    f"{workflow} run {latest.id} ended {latest.conclusion}:"
                    f" {repo.web_url()}/actions/runs/{latest.id}"
                )
            print(f"  {workflow:<14} {latest.conclusion}")
            RUNS.count += 1
            return
        if time.monotonic() >= deadline:
            fail(
                f"{workflow} did not complete within {timeout:.0f}s:"
                f" {repo.web_url()}/actions"
            )
        time.sleep(5)


def judge_runs(
    repo: Repository, runs: tuple[Run, ...], *, branch: str = ""
) -> list[str]:
    """One line per run; red fails with every red run named.

    A failed run is red. A cancelled run is red too, unless a twin
    for the same head took over and completed green, in which case
    the twin's verdict stands and the line says so; a successor for a
    moved head means the watched sha is no longer the branch's and is
    named as red, since the loop watches a sha of its own. A
    cancellation nobody took over stays red rather than quietly
    passing.
    """
    from livery.workshop._runs import successor

    lines: list[str] = []
    failed: list[str] = []
    for run in runs:
        verdict: str = run.conclusion or run.status
        if run.conclusion == "cancelled":
            found = successor(repo, run, branch=branch)
            if found is None:
                verdict = "cancelled; no newer run for its head is known"
                failed.append(run.workflow)
            elif found.same_head and found.run.conclusion == "success":
                verdict = (
                    f"cancelled, superseded by run {found.run.id} for the same"
                    " head (success)"
                )
            elif found.same_head:
                verdict = (
                    f"cancelled, superseded by run {found.run.id} for the same head"
                    f" ({found.run.conclusion or found.run.status})"
                )
                failed.append(run.workflow)
            else:
                verdict = (
                    f"cancelled, superseded by run {found.run.id} for the moved head"
                    f" {found.run.head_sha[:12]}; the watched sha is no longer"
                    " the branch's"
                )
                failed.append(run.workflow)
        elif run.conclusion == "failure":
            failed.append(run.workflow)
        lines.append(f"  {run.workflow:14} {verdict}")
    if failed:
        names = ", ".join(failed)
        fail(f"red runs on the loop: {names}; logs: {repo.web_url()}/actions")
    return lines


def _retry_red_once(repo: Repository, runs: tuple[Run, ...]) -> list[str]:
    """Re-run the failed jobs of every red run in *runs*; the runs re-queued.

    The loop's runner has failed a job on a build that left nothing
    and passed the same job on the re-run, so a red run gets one more
    attempt before it is judged. Returns the workflows re-run, empty
    when nothing was red.
    """
    import time

    retried: list[str] = []
    for run in runs:
        if run.conclusion != "failure":
            continue
        # A job of the old attempt still running would collect the new
        # attempt's halves: the re-run waits until every job has
        # completed, naming the one it waits on.
        deadline = time.monotonic() + 600
        while True:
            moving = [
                job.name
                for job in repo.checks.jobs(run.id)
                if job.status != "completed"
            ]
            if not moving:
                break
            if time.monotonic() >= deadline:
                fail(
                    f"run {run.id} ended failure but {', '.join(moving)} still"
                    " moved 600s later; the re-run would race it"
                )
            print(f"  waiting for {moving[0]} of run {run.id} before its re-run")
            time.sleep(5)
        repo.checks.rerun(run.id, failed_only=True)
        print(f"  re-running {run.workflow} (run {run.id}) once: it ended failure")
        retried.append(run.workflow)
    return retried


def _watch(
    kind: str,
    url: str,
    sha: str,
    *,
    timeout: float = 900.0,
    require: tuple[str, ...] = (),
    branch: str = "",
    interval: float = 5.0,
) -> None:
    """Follow *sha*'s runs to their verdicts; red fails verbatim after one re-run.

    *require* names workflows that must appear before the wait ends:
    a workflow triggered by the push registers its run a beat after
    the others, and a watch that settles on the early arrivals would
    call the commit green while the required one is still unstarted.
    A run that ended failure is re-run once and followed again; red
    twice is the verdict.
    """
    import time

    forge, _ = _dev_forge(kind)
    repo = forge.repository(E2E_OWNER, E2E_REPO)
    deadline = time.monotonic() + timeout
    retried = False
    transient = Transient(interval=interval)
    while True:
        listed = _runs_patiently(
            repo, transient, head_sha=sha, subject=f"{sha[:10]}'s runs"
        )
        if listed is None:
            time.sleep(interval)
            continue
        runs = listed
        present = {run.workflow for run in runs}
        if (
            runs
            and all(r.status == "completed" for r in runs)
            and all(name in present for name in require)
        ):
            if retried or not _retry_red_once(repo, runs):
                break
            retried = True
        if time.monotonic() >= deadline:
            missing = ", ".join(sorted(set(require) - present))
            waited = f"; never appeared: {missing}" if missing else ""
            fail(
                f"the loop's runs did not complete within {timeout:.0f}s"
                f"{waited}: {repo.web_url()}/actions"
            )
        time.sleep(interval)
    RUNS.count += len(runs)
    for line in judge_runs(repo, runs, branch=branch):
        print(line)


def _align_main(root: Path) -> None:
    """Align the loop's main hard onto origin, superseded commits named.

    Origin is the loop's truth: every local byte is regenerable
    render output, and local main gathers aftercare commits the
    squashes supersede, so a fast-forward regularly cannot. What
    goes is printed, never silently vanished.
    """
    import livery.toolroom.tools as toolroom

    toolroom.git.opts(cwd=root)("fetch", "origin")
    gone = toolroom.git.opts(cwd=root, nofail=True)(
        "log", "--oneline", "origin/main..main"
    )
    if gone.code == 0 and gone.stdout.strip():
        for line in gone.stdout.strip().splitlines():
            print(f"  superseded local commit: {line}")
    toolroom.git.opts(cwd=root)("switch", "main")
    toolroom.git.opts(cwd=root)("reset", "--hard", "origin/main")
    # Residue too: a failed pass's branch leaves untracked leftovers
    # (a member directory without its contract refuses discovery).
    # What `fm sync` materialises survives: the venv, the stubs under
    # typings/ and the receipts and fragments under .workshop/, which
    # the gate reads and no verb between two syncs rewrites (a member
    # is wired with `uv sync` alone); everything else is regenerable
    # by charter.
    kept = [flag for name in KEPT_BY_SYNC for flag in ("-e", name)]
    residue = toolroom.git.opts(cwd=root, nofail=True)("clean", "-ndx", *kept)
    if residue.code == 0 and residue.stdout.strip():
        for line in residue.stdout.strip().splitlines():
            print(f"  cleaned: {line.removeprefix('Would remove ')}")
        toolroom.git.opts(cwd=root)("clean", "-fdx", *kept)
    # Local branches too: main is the loop's only durable ref, and
    # every other local branch is a past pass's residue (a prepared
    # release branch whose PR already merged makes the driver refuse
    # as stale). What goes is printed, never silently vanished.
    heads = toolroom.git.opts(cwd=root, nofail=True)(
        "for-each-ref", "--format=%(refname:short)", "refs/heads/"
    )
    for name in heads.stdout.split() if heads.code == 0 else []:
        if name == "main":
            continue
        stale = toolroom.git.opts(cwd=root, nofail=True)(
            "log", "--oneline", f"main..{name}"
        )
        if stale.code == 0 and stale.stdout.strip():
            for line in stale.stdout.strip().splitlines():
                print(f"  superseded branch commit: {line} ({name})")
        toolroom.git.opts(cwd=root, nofail=True)("branch", "-D", name)
        print(f"  pruned pass-owned branch: {name}")


def _fresh_branch(root: Path, name: str) -> None:
    """A pass-owned branch, recreated from an aligned main.

    ``switch -C`` from a dirty tree drags or clobbers state, so the
    alignment and the switch are one operation here, never two calls
    a future edit can separate: the tree is reset to origin, cleaned,
    and swept of stale local branches before this one is recreated.
    """
    import livery.toolroom.tools as toolroom

    _align_main(root)
    toolroom.git.opts(cwd=root)("switch", "-C", name)


def _render_with_the_pass(root: Path) -> None:
    """Render the loop's files at *root* with this pass's own workshop.

    A child of the pass's interpreter, as the birth is, with the uv
    handoff off: the loop's lock pins the workshop the loop last
    locked, and the handoff would run that one instead.
    """
    import sys

    result = footman.run(
        [sys.executable, "-m", "livery.footman", "--yes", "drift.check", "--fix"],
        cwd=root,
        env={**os.environ, "FOOTMAN_NO_UV": "1"},
        nofail=True,
        timeout=900.0,
    )
    print(result.stdout.rstrip("\n"))
    if result.code != 0:
        fail(
            f"the pass's render of the loop exited {result.code}:"
            f"\n{result.stdout}{result.stderr}"
        )


def _loop_fm(
    root: Path, *args: str, timeout: float = 900.0, nofail: bool = False
) -> int:
    """Run the loop's own fm, the dev wheels' one, inside the loop.

    The whole point of the substrate: the workspace under test runs
    the workshop being edited, so submit, release, and every other
    verb exercise the dev wheels end to end. ``--yes`` rides every
    call, because the loop is automation and silence never confirms.
    """
    import livery.toolroom.tools as toolroom

    # The caller's VIRTUAL_ENV points at the worktree; the loop's uv
    # must resolve the loop's own venv, so the variable stays behind.
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    # Named here, not only in the pass's environment: a write to
    # os.environ inside a task is the task's, and the environment this
    # child gets is built from the process's, so the loop's own fm
    # (whose submit can amend a commit) would ask a developer's signer.
    env.update(unsigned_environment(env))
    # No --no-sync: the wiring moves the lock onto each pass's fresh
    # dev wheels, and a frozen venv would keep running yesterday's
    # workshop under today's lock (measured: a fixed bug stayed red
    # in the loop while the fix sat published in the registry).
    result = toolroom.uv.opts(cwd=root, nofail=True, timeout=timeout, env=env)(
        "run", "fm", "--yes", *args
    )
    if result.code != 0:
        if nofail:
            # The evidence prints either way; the caller owns the
            # verdict for one bounded retry, never a silent swallow.
            print(
                f"  the loop's `{footman.prog()} {' '.join(args)}` exited"
                f" {result.code}:\n{result.stdout}{result.stderr}"
            )
            return result.code
        fail(
            f"the loop's `{footman.prog()} {' '.join(args)}` exited"
            f" {result.code}:\n{result.stdout}{result.stderr}"
        )
    return 0


def member_kind(seed: str) -> str:
    """The package kind a loop member's seed renders: its name past ``package-``."""
    return seed.removeprefix("package-")


def members_for(kinds: Sequence[str] = ()) -> tuple[tuple[str, str], ...]:
    """The loop's members of *kinds*, as ``(name, seed)``; every member when empty."""
    return tuple(
        (name, seed)
        for name, seed in LOOP_MEMBERS
        if not kinds or member_kind(seed) in kinds
    )


def extension_under_test(name: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The stack a pass births to test extension *name*, and the member kinds it lands.

    The stack is what *name* requires, each before what needs it, then
    *name* itself with every option it declares, so each of its checks
    registers. The members are the loop's of the kinds *name*'s checks
    declare, so each check has something of its own to judge.

    Raises:
        Failed: when no installed distribution declares *name*, naming
            the installed ones; when *name* registers no check; when its
            requirements form a cycle.
    """
    from livery.toolroom.store import Spec
    from livery.workshop._extensions import (
        declaration,
        declared_options,
        installed_extensions,
    )

    module = declaration(name)
    if module is None:
        installed = ", ".join(installed_extensions()) or "none"
        fail(
            f"extension {name!r}: no installed distribution declares it;"
            f" the installed ones are {installed}"
        )
    checks = cast("tuple[CheckRecord, ...]", tuple(getattr(module, "CHECKS", ())))
    if not checks:
        fail(
            f"extension {name!r} registers no check: a pass would have"
            " nothing of its own to run"
        )
    stack: list[str] = []

    def visit(extension: str, path: tuple[str, ...]) -> None:
        if extension in stack:
            return
        if extension in path:
            fail(f"extensions require each other: {' -> '.join((*path, extension))}")
        declared = declaration(extension)
        for needed in getattr(declared, "REQUIRES", ()) if declared else ():
            visit(str(needed), (*path, extension))
        stack.append(extension)

    visit(name, ())
    stack[-1] = str(Spec(name, tuple(declared_options(name))))
    declared_kinds = {kind for record in checks for kind in record.kinds}
    kinds = tuple(
        member_kind(seed)
        for _member, seed in LOOP_MEMBERS
        if member_kind(seed) in declared_kinds
    )
    return tuple(stack), kinds


def _ensure_members(root: Path, kinds: Sequence[str] = ()) -> None:
    """Give the loop its members of *kinds*, all when empty, each landed by its gate.

    ``new.package`` renders and wires a member, a `[release] baseline`
    seeds the first release's number, the nanobind member's contract
    names the loop's one runner as its wheel platform, and the loop's
    own ``fm submit --armed`` lands it: the branch, the pull request,
    the gate on the runner (which compiles the native member's
    editable install), and the merge, all on the dev wheels.
    Idempotent: a member already on main skips everything.
    """
    from livery.workshop._git_ops import GitOps

    # Landed means on main: a failed pass leaves the working tree on
    # the feature branch with the member present, and judging that
    # tree would skip straight to the release act with nothing
    # merged. The alignment makes the glob read main's truth.
    _align_main(root)
    for name, kind in members_for(kinds):
        if (root / "packages" / name / "workshop.toml").is_file():
            print(f"  member {name}: already landed")
            continue
        git = GitOps(root)
        _fresh_branch(root, f"feat/{name}")
        _loop_fm(root, "new.package", name, f"--kind={kind}")
        member = root / "packages" / name / "workshop.toml"
        body = member.read_text("utf-8")
        # A native seed names the hosted runners as its wheel platforms;
        # the loop's fleet is its one runner, and the leg must be
        # schedulable. Another seed names none, and nothing changes.
        body = body.replace(
            'wheel-platforms = ["ubuntu-latest", "macos-latest", "windows-latest"]',
            f'wheel-platforms = ["{CURRENT.label}"]',
        )
        if "[release]" not in body:
            body = (
                body.rstrip("\n")
                + "\n\n[release]\n# The first release lands at the baseline.\n"
                + 'baseline = "0.1.0"\n'
            )
        if name == CONTRIBUTING_MEMBER and "[[ci.point]]" not in body:
            # The member contributes a point: the loop dispatches it
            # by hand, so the point is proven against the real runner
            # rather than against the renderer's own output.
            body = (
                body.rstrip("\n")
                + "\n\n# A point of this member's own, run by hand in the loop.\n"
                + "[[ci.point]]\n"
                + f'name = "{CONTRIBUTED_POINT}"\n'
                + 'task = "extensions"\n'
                + 'runners = ["ubuntu-latest"]\n'
            )
        member.write_text(body, "utf-8")
        # The baseline is a render input: cliff.toml was rendered before
        # the append, so it must settle again or the gate names it as
        # drift.
        _loop_fm(root, "drift.check", "--fix")
        git.commit_all(
            f"feat({name}): a loop member\n\nBorn through new.package on"
            " the dev wheels, with the release baseline seeded so the"
            " first release lands at 0.1.0."
        )
        # Force for the same reason the setup branch pushes force: the
        # branch is pass-owned, rebuilt from main every time, so the
        # remote's copy is always superseded.
        _loop_fm(root, "submit", "--force", "--armed")
        _align_main(root)
        print(f"  member {name}: landed through the loop's own gate")


def _completed_run(
    repo: Repository,
    sha: str,
    *,
    event: str,
    timeout: float = 900.0,
    interval: float = 5.0,
) -> tuple[Run, tuple[Job, ...]]:
    """The newest completed ``ci.yml`` run of *sha* for *event*, with its jobs.

    Waits for the run to register and complete; a run that ended
    failure is re-run once and waited for again, and a red run then
    fails verbatim, naming its page. The jobs carry their names, the
    check legs by their matrix display (``check (ubuntu-latest,
    3.14)``), so a reader looks for the prefix it needs; the logs are
    read per job on demand, since a job the run skipped has none to
    serve.
    """
    import time

    deadline = time.monotonic() + timeout
    retried = False
    transient = Transient(interval=interval)
    while True:
        listed = _runs_patiently(
            repo, transient, head_sha=sha, subject=f"{sha[:10]}'s {event} run"
        )
        if listed is None:
            time.sleep(interval)
            continue
        runs = [
            run
            for run in listed
            if run.workflow.endswith("ci.yml") and run.event == event
        ]
        run = max(runs, key=lambda run: run.id, default=None)
        if run is not None and run.status == "completed":
            if retried or not _retry_red_once(repo, (run,)):
                break
            retried = True
        if time.monotonic() >= deadline:
            fail(
                f"no completed {event} run for {sha[:10]} within {timeout:.0f}s:"
                f" {repo.web_url()}/actions"
            )
        time.sleep(interval)
    RUNS.count += 1
    if run.conclusion != "success":
        fail(
            f"run {run.id} ({event}, {sha[:10]}) ended {run.conclusion}:"
            f" {repo.web_url()}/actions/runs/{run.id}"
        )
    return run, repo.checks.jobs(run.id)


def _require_lines(
    repo: Repository,
    run: Run,
    jobs: tuple[Job, ...],
    job: str,
    needed: tuple[str, ...],
    *,
    forbidden: tuple[str, ...] = (),
) -> None:
    """Fail unless the log of the job whose name starts with *job* has every line.

    *forbidden* names lines the log must not carry: a proof of what a
    leg skipped reads the same as a proof of what it ran.
    """
    found = next((item for item in jobs if item.name.startswith(job)), None)
    if found is None:
        fail(f"run {run.id} has no {job} job: {repo.web_url()}/actions/runs/{run.id}")
    log = repo.checks.job_log(found.id)
    needed = with_label(needed)
    forbidden = with_label(forbidden)
    missing = [line for line in needed if line not in log]
    if missing:
        fail(
            f"run {run.id}'s {job} job did not say {missing};"
            f" {repo.web_url()}/actions/runs/{run.id}"
        )
    present = [line for line in forbidden if line in log]
    if present:
        fail(
            f"run {run.id}'s {job} job said {present}, which the proof forbids;"
            f" {repo.web_url()}/actions/runs/{run.id}"
        )


def _prove_verified_skip(root: Path, kind: str) -> None:
    """Prove main's run after the setup squash skips the gate and copies the record.

    The setup pull request's check leg ran the full gate, its gate
    job wrote the setup branch's coverage record, and its stamp put
    the tree on the verified record naming the branch; the squash
    lands the same tree on main, so main's push run finds it on the
    record, skips the gate, measures nothing, and its union takes
    every unit from the setup branch's record and copies it into
    main's. A skipped leg reddening the union, passing it with
    nothing counted, or measuring what the branch already measured
    is the failure this proof exists for.
    """
    from livery.workshop._git_ops import GitOps

    _align_main(root)
    head = GitOps(root).head_sha()
    forge, _ = _dev_forge(kind)
    repo = forge.repository(E2E_OWNER, E2E_REPO)
    from livery.workshop._new_project import _SETUP_BRANCH

    run, logs = _completed_run(repo, head, event="push")
    _require_lines(
        repo,
        run,
        logs,
        "check",
        ("skipping the gate",),
        forbidden=("measuring:", "coverage store: packages/loop-echo stored"),
    )
    # The units main's union reuses are the ones the workspace has at
    # this point: its members and its own tests. A fresh birth has
    # neither yet (the members and the tests land through their own
    # pull requests afterwards), and then the union has no unit and
    # judges nothing, which the run says in as many words.
    from livery.workshop._coverage_store import workspace_suite
    from livery.workshop._packages import discover_packages

    members = [package.path for package in discover_packages(root)]
    units = [*members, *(["tests"] if workspace_suite(root) is not None else [])]
    record = (
        f"coverage record: main takes {_SETUP_BRANCH}'s record for the tree it proved"
    )
    if units:
        expected = [
            record,
            *(
                f"coverage: {unit} on check-ubuntu-latest-3.14: reused from run"
                for unit in units
            ),
            *(f"coverage {path}: 100.0% (" for path in members),
            f"coverage: the union of 0 leg(s) and {len(units)} reused suite(s)",
            f"coverage record: main/check-ubuntu-latest-3.14: 0 fresh, {len(units)}"
            " carried, 0 removed",
        ]
    else:
        expected = [
            record,
            "coverage: leg check-ubuntu-latest-3.14 ran 'verified': no suite, no data",
            "coverage: no unit to union; nothing judged",
        ]
    _require_lines(
        repo,
        run,
        logs,
        "gate",
        tuple(expected),
        forbidden=("unjudged this run",),
    )
    print(
        f"  verified skip: proven on main's run {run.id}; the gate skipped,"
        f" nothing was measured, and main copied {_SETUP_BRANCH}'s record"
    )


#: The member the loop keeps under auto-ratchet, so every pass proves
#: the mark's first record, an accepted lowering, and the ratchet up.
RATCHET_MEMBER = "loop-native"


def _prepare_ratchet(root: Path, kind: str) -> None:
    """Put the ratchet member under auto-ratchet, then lower its mark for the proof.

    The member's contract lands through the loop's own gate while it
    still commits a literal floor: that pull request's run records the
    first mark, and main's run after the squash judges it; the pass
    waits for that run, or the accept below would be consumed there
    instead of by the member-only pull request. Then
    `fm coverage.accept` lowers the mark to 90 with a reason, so the
    pull request that follows judges from the accepted row and
    ratchets the mark back up. A refusal (a mark a pass that did not
    finish already lowered) is printed and the mark stands; the proof
    reads the same lines either way.
    """
    import livery.toolroom.tools as toolroom
    from livery.workshop._git_ops import GitOps

    contract = root / "packages" / RATCHET_MEMBER / "workshop.toml"
    body = contract.read_text("utf-8")
    if 'coverage-floor = "auto-ratchet"' not in body:
        _fresh_branch(root, "chore/ratchet-member")
        switched = body.replace(
            "coverage-floor = 100", 'coverage-floor = "auto-ratchet"'
        )
        if switched == body:
            fail(f"{contract}: no literal floor to switch to auto-ratchet")
        contract.write_text(switched, "utf-8")
        GitOps(root).commit_all(
            f"chore({RATCHET_MEMBER}): the member's floor is the mark on the"
            " store\n\nUnder auto-ratchet the first gated run records the"
            " mark, and the loop proves the record, an accepted lowering,"
            " and the ratchet up on every pass."
        )
        toolroom.git.opts(cwd=root, nofail=True)("fetch", "--prune", "origin")
        _loop_fm(root, "submit", "--force", "--armed")
        _align_main(root)
        forge, _ = _dev_forge(kind)
        repo = forge.repository(E2E_OWNER, E2E_REPO)
        run, _jobs = _completed_run(repo, GitOps(root).head_sha(), event="push")
        print(
            f"  ratchet member: {RATCHET_MEMBER} landed under auto-ratchet;"
            f" main's run {run.id} judged the first mark"
        )
    code = _loop_fm(
        root,
        "coverage.accept",
        f"packages/{RATCHET_MEMBER}",
        "90",
        "--reason=the loop proves an accepted lowering",
        nofail=True,
    )
    if code == 0:
        print(f"  ratchet member: {RATCHET_MEMBER}'s mark accepted down to 90")
    else:
        print(
            f"  ratchet member: the accept was refused (exit {code}); the mark stands"
        )


def _prove_scoped_leg(root: Path, kind: str) -> None:
    """Prove the scoped check leg on a member-only pull request.

    The loop's other pull requests all touch the root (``new.package``
    wires the workspace, the release stamps the manifest), so their
    legs pay the full gate by the affected rule. This one rewrites a
    test file inside ``loop-echo`` and nothing else, lands it through
    the loop's gate, and reads the run's logs: the check leg must say
    it narrowed against main to that one member and put that member's
    suite on its ref, and the gate job's union must judge both
    members, the other one reused from main's record, write the
    branch's own record, and compose its stamp with main's verified
    tree. Main's push run after the squash then skips the gate on
    that composed row, measures nothing, and copies the branch's
    record into main's. Re-run on a pass-owned branch with main's tip
    as the stamp, so the diff is never empty.
    """
    from livery.workshop._git_ops import GitOps

    git = GitOps(root)
    _fresh_branch(root, "chore/scoped-leg")
    stamp = git.head_sha()
    probe = root / "packages" / "loop-echo" / "tests" / "test_scoped_leg.py"
    probe.write_text(
        '"""A member-only change: the loop proves the scoped check leg on it."""\n'
        "\n"
        f'STAMP = "{stamp}"\n'
        "\n\n"
        "def test_the_stamp_is_a_commit():\n"
        "    assert len(STAMP) == 40\n",
        "utf-8",
    )
    git.commit_all(
        "chore(loop-echo): a member-only change for the scoped leg\n\nOne file"
        " under one member, so the check leg narrows to that member and"
        " says so."
    )
    head = git.head_sha()
    # The last pass's merge deleted the branch on origin, and the clone's
    # tracking ref still names its final commit; the forced submit pushes
    # with a lease on that ref, which git refuses as stale against a
    # branch that is gone. A pruning fetch drops the stale ref (or
    # refreshes it when a failed pass left the branch behind).
    import livery.toolroom.tools as toolroom

    toolroom.git.opts(cwd=root, nofail=True)("fetch", "--prune", "origin")
    _loop_fm(root, "submit", "--force", "--armed")
    _align_main(root)
    from livery.workshop._coverage_store import workspace_suite

    # The workspace's own tests are a unit once the tests leg landed
    # them, and the member's change moves their closure, so the leg
    # runs them too; a fresh birth has none, and every count is one
    # fewer.
    tests = workspace_suite(root) is not None
    units = 4 if tests else 3
    forge, _ = _dev_forge(kind)
    repo = forge.repository(E2E_OWNER, E2E_REPO)
    run, logs = _completed_run(repo, head, event="pull_request")
    _require_lines(
        repo,
        run,
        logs,
        "check",
        (
            "affected-legs: the scoped gate against origin/main",
            "affected: packages/loop-echo",
            "coverage store: packages/loop-echo stored for closure",
            *(("coverage store: tests stored for closure",) if tests else ()),
        ),
        forbidden=(
            "affected: packages/loop-cpp",
            "packages/loop-echo, packages/loop-native",
            "coverage store: packages/loop-cpp runs",
            "coverage store: packages/loop-native runs",
        ),
    )
    # The union takes the one member the leg ran from the leg and the
    # others from the store, and judges every one.
    _require_lines(
        repo,
        run,
        logs,
        "gate",
        (
            "coverage: packages/loop-native on check-ubuntu-latest-3.14:"
            " reused from run",
            "coverage: packages/loop-cpp on check-ubuntu-latest-3.14: reused from run",
            "coverage packages/loop-echo: 100.0% (floor 100.0%",
            "coverage packages/loop-native: 100.0% (mark 90.0% accept by",
            "coverage packages/loop-cpp: 100.0% (floor 100.0%",
            "accepted: the loop proves an accepted lowering",
            "new mark: 100.0%",
            "coverage: the union of 1 leg(s) and 2 reused suite(s)",
            "coverage record: chore/scoped-leg/check-ubuntu-latest-3.14:"
            f" {units - 2} fresh, 2 carried, 0 removed",
            "speed packages/loop-echo on check-ubuntu-latest-3.14: ",
            "recorded as proved green by run",
            " on top of tree ",
            "is not a release branch: nothing to check",
        ),
        forbidden=("unjudged this run", "not recorded:"),
    )
    print(
        "  scoped leg: proven on a member-only pull request (affected:"
        " packages/loop-echo; the union reused loop-native and loop-cpp from"
        " main's record"
        " and wrote the branch's; the stamp composed with main's tree)"
    )
    # The narrowed run rested on main's verified tree, so its stamp
    # composed naming the branch, and main's push after the squash
    # skips the gate, measures nothing, and copies the branch's
    # record, every unit at the closures the squash lands.
    landed = GitOps(root).head_sha()
    run, logs = _completed_run(repo, landed, event="push")
    _require_lines(
        repo,
        run,
        logs,
        "check",
        ("skipping the gate", " on top of tree "),
        forbidden=("measuring:", "coverage store: packages/loop-echo stored"),
    )
    _require_lines(
        repo,
        run,
        logs,
        "gate",
        (
            "coverage record: main takes chore/scoped-leg's record for the tree"
            " it proved",
            "coverage packages/loop-echo: 100.0% (floor 100.0%",
            "coverage packages/loop-native: 100.0% (mark 100.0% ratchet by run",
            "coverage packages/loop-cpp: 100.0% (floor 100.0%",
            f"coverage: the union of 0 leg(s) and {units} reused suite(s)",
            f"coverage record: main/check-ubuntu-latest-3.14: 0 fresh, {units}"
            " carried, 0 removed",
        ),
        forbidden=("unjudged this run",),
    )
    print(
        f"  composed skip: proven on main's run {run.id} after the member-only"
        " squash; nothing was measured, and main copied the branch's record"
    )


def _prove_prose_leg(root: Path, kind: str) -> None:
    """Prove a note-only pull request pays no package gate.

    The change is one markdown file under ``notes/`` and nothing
    else, landed through the loop's gate on a pass-owned branch with
    main's tip as the stamp so the diff is never empty. The check
    leg must say nothing is affected because only prose changed and
    skip its gate; the gate job must reuse every unit from the store,
    judge every member, and compose the stamp with main's verified
    tree, so main's push run after the squash skips too.
    """
    from livery.workshop._git_ops import GitOps

    git = GitOps(root)
    _fresh_branch(root, "chore/prose-leg")
    stamp = git.head_sha()
    note = root / "notes" / "loop-prose.md"
    note.parent.mkdir(exist_ok=True)
    note.write_text(
        "# The loop's prose probe\n\nA note-only change, so the check legs"
        f" skip their gate.\n\nStamp: {stamp}\n",
        "utf-8",
    )
    git.commit_all(
        "docs(notes): a note-only change for the prose leg\n\nOne markdown"
        " file under notes/, so the check leg finds nothing affected and"
        " skips its gate."
    )
    head = git.head_sha()
    import livery.toolroom.tools as toolroom

    toolroom.git.opts(cwd=root, nofail=True)("fetch", "--prune", "origin")
    _loop_fm(root, "submit", "--force", "--armed")
    _align_main(root)
    forge, _ = _dev_forge(kind)
    repo = forge.repository(E2E_OWNER, E2E_REPO)
    run, logs = _completed_run(repo, head, event="pull_request")
    _require_lines(
        repo,
        run,
        logs,
        "check",
        (
            "affected-legs: the scoped gate against origin/main",
            "nothing affected: only prose and site files changed (1 file(s)"
            " under notes/, markdown, the root docs/ tree, or zensical.toml);"
            " the gate skips",
        ),
        forbidden=("affected: packages/", "coverage store: packages/"),
    )
    _require_lines(
        repo,
        run,
        logs,
        "gate",
        (
            "coverage: packages/loop-echo on check-ubuntu-latest-3.14: reused from run",
            "coverage: packages/loop-native on check-ubuntu-latest-3.14:"
            " reused from run",
            "coverage: packages/loop-cpp on check-ubuntu-latest-3.14: reused from run",
            "coverage: tests on check-ubuntu-latest-3.14: reused from run",
            "coverage packages/loop-echo: 100.0% (floor 100.0%",
            "coverage packages/loop-native: 100.0% (",
            "coverage packages/loop-cpp: 100.0% (floor 100.0%",
            "coverage: the union of 0 leg(s) and 4 reused suite(s)",
            "coverage record: chore/prose-leg/check-ubuntu-latest-3.14: 0 fresh,"
            " 4 carried, 0 removed",
            "recorded as proved green by run",
            " on top of tree ",
        ),
        forbidden=("unjudged this run",),
    )
    print(
        f"  prose leg: proven on a note-only pull request (run {run.id}: the"
        " check leg skipped, the union reused every unit from main's record"
        " and wrote the branch's, the stamp composed)"
    )
    landed = GitOps(root).head_sha()
    run, logs = _completed_run(repo, landed, event="push")
    _require_lines(repo, run, logs, "check", ("skipping the gate", " on top of tree "))
    print(f"  composed skip: proven on main's run {run.id} after the note-only squash")


def _prove_tests_leg(root: Path, kind: str) -> None:
    """Prove a change under the workspace's own tests runs that unit alone.

    A new test file under the root ``tests/`` and nothing else: the
    check leg must narrow to the workspace tests, run and store them
    as a unit, and leave every member's suite to the record; the
    union judges every member from main's record, writes the branch's
    record with the one fresh unit, and the stamp composes with main's
    tree. Re-run on a pass-owned branch with main's tip as the stamp.
    """
    from livery.workshop._git_ops import GitOps

    git = GitOps(root)
    _fresh_branch(root, "chore/tests-leg")
    stamp = git.head_sha()
    probe = root / "tests" / "test_tests_leg.py"
    probe.write_text(
        '"""A workspace-tests change: the loop proves the tests leg on it."""\n'
        "\n"
        f'STAMP = "{stamp}"\n'
        "\n\n"
        "def test_the_stamp_is_a_commit():\n"
        "    assert len(STAMP) == 40\n",
        "utf-8",
    )
    git.commit_all(
        "chore(tests): a workspace-tests change for the tests leg\n\nOne file"
        " under the root tests directory, so the check leg narrows to the"
        " workspace tests and says so."
    )
    head = git.head_sha()
    import livery.toolroom.tools as toolroom

    toolroom.git.opts(cwd=root, nofail=True)("fetch", "--prune", "origin")
    _loop_fm(root, "submit", "--force", "--armed")
    _align_main(root)
    forge, _ = _dev_forge(kind)
    repo = forge.repository(E2E_OWNER, E2E_REPO)
    run, logs = _completed_run(repo, head, event="pull_request")
    _require_lines(
        repo,
        run,
        logs,
        "check",
        (
            "affected-legs: the scoped gate against origin/main",
            "affected: tests",
            "coverage store: tests stored for closure",
        ),
        forbidden=(
            "affected: packages/",
            "coverage store: packages/loop-echo stored",
            "coverage store: packages/loop-native stored",
            "coverage store: packages/loop-cpp stored",
        ),
    )
    _require_lines(
        repo,
        run,
        logs,
        "gate",
        (
            "coverage: packages/loop-echo on check-ubuntu-latest-3.14: reused from run",
            "coverage: packages/loop-native on check-ubuntu-latest-3.14:"
            " reused from run",
            "coverage: packages/loop-cpp on check-ubuntu-latest-3.14: reused from run",
            "coverage packages/loop-echo: 100.0% (floor 100.0%",
            "coverage packages/loop-native: 100.0% (",
            "coverage packages/loop-cpp: 100.0% (floor 100.0%",
            "coverage: the union of 1 leg(s) and 3 reused suite(s)",
            "coverage record: chore/tests-leg/check-ubuntu-latest-3.14: 1 fresh,"
            " 3 carried, 0 removed",
            "recorded as proved green by run",
            " on top of tree ",
        ),
        forbidden=("unjudged this run", "coverage: tests on check-ubuntu-latest-3.14"),
    )
    print(
        f"  tests leg: proven on a workspace-tests pull request (run {run.id}: the"
        " check leg ran the workspace tests alone, the union reused every member"
        " from main's record, the stamp composed)"
    )
    landed = GitOps(root).head_sha()
    run, logs = _completed_run(repo, landed, event="push")
    _require_lines(repo, run, logs, "check", ("skipping the gate", " on top of tree "))
    print(f"  composed skip: proven on main's run {run.id} after the tests-leg squash")


def _prove_nightly(root: Path, kind: str) -> None:
    """Prove the nightly point by hand: dispatched, followed to green, read back.

    The loop's contract schedules the released member's replay at the
    nightly point, so the dispatched run proves a task attached
    through the contract with no YAML change, and the verbs that
    start and read a point run against the real runner. After the
    release act, so the replay has a wheel to install.
    """
    code = _loop_fm(root, "ci.dispatch", "--point=nightly", "--interval=5", nofail=True)
    if code:
        fail(f"the loop's `{footman.prog()} ci.dispatch --point=nightly` exited {code}")
    if _loop_fm(root, "ci.status", "--point=nightly", nofail=True):
        fail(
            f"the loop's `{footman.prog()} ci.status --point=nightly` did not"
            " read the dispatched run green"
        )
    from livery.workshop._ci_tasks import point_runs

    forge, _ = _dev_forge(kind)
    repo = forge.repository(E2E_OWNER, E2E_REPO)
    run = point_runs(repo, "nightly")[0]
    jobs = repo.checks.jobs(run.id)
    _require_lines(
        repo,
        run,
        jobs,
        "nightly",
        ("replaying ", " passes its own tests from site-packages"),
    )
    print(
        f"  nightly: proven by hand (run {run.id}: dispatched, followed to green,"
        " the scheduled replay ran, and the point read back green)"
    )


def _prove_dispatched_gate(root: Path, kind: str) -> None:
    """Prove the gate on command: dispatched on main, full, followed to green.

    A dispatched gate pays the full gate whatever the contract's
    affected-legs key says: the check verb reads the event and
    narrows on a pull request alone, and sets the verified record
    aside the way the nightly does, so a tree main already proved is
    proved again rather than skipped. The check leg says so, and the
    run is read back green through the point's own reader.
    """
    code = _loop_fm(root, "ci.dispatch", "--point=gate", "--interval=5", nofail=True)
    if code:
        fail(f"the loop's `{footman.prog()} ci.dispatch --point=gate` exited {code}")
    if _loop_fm(root, "ci.status", "--point=gate", nofail=True):
        fail(
            f"the loop's `{footman.prog()} ci.status --point=gate` did not"
            " read the dispatched run green"
        )
    from livery.workshop._ci_tasks import point_runs

    forge, _ = _dev_forge(kind)
    repo = forge.repository(E2E_OWNER, E2E_REPO)
    run = point_runs(repo, "gate")[0]
    if run.event != "workflow_dispatch":
        fail(
            f"the newest gate run {run.id} is a {run.event} run, not the"
            f" dispatched one: {run.url}"
        )
    jobs = repo.checks.jobs(run.id)
    _require_lines(
        repo,
        run,
        jobs,
        "check",
        (
            "dispatched: the whole gate, the verified record set aside",
            "affected-legs: a workflow_dispatch run pays the full gate",
        ),
        forbidden=("affected-legs: the scoped gate against", "skipping the gate"),
    )
    print(
        f"  gate: proven by hand (run {run.id}: dispatched, followed to green,"
        " the full gate ran, and the point read back green)"
    )


def _prove_contributed_point(root: Path, kind: str) -> None:
    """Prove the member's point: dispatched, followed to green, its task run.

    The member's contract declares the point; the rendered shell is
    the loop's own output, so the proof is the real runner running
    the declared task through ``ci.run`` and the point reading back
    green through the point's own reader.
    """
    code = _loop_fm(
        root, "ci.dispatch", f"--point={CONTRIBUTED_POINT}", "--interval=5", nofail=True
    )
    if code:
        fail(
            f"the loop's `{footman.prog()} ci.dispatch --point={CONTRIBUTED_POINT}`"
            f" exited {code}"
        )
    if _loop_fm(root, "ci.status", f"--point={CONTRIBUTED_POINT}", nofail=True):
        fail(
            f"the loop's `{footman.prog()} ci.status --point={CONTRIBUTED_POINT}`"
            " did not read the dispatched run green"
        )
    from livery.workshop._ci_tasks import point_runs

    forge, _ = _dev_forge(kind)
    repo = forge.repository(E2E_OWNER, E2E_REPO)
    run = point_runs(repo, CONTRIBUTED_POINT, root)[0]
    jobs = repo.checks.jobs(run.id)
    _require_lines(
        repo,
        run,
        jobs,
        CONTRIBUTED_POINT,
        (
            f"{CONTRIBUTED_POINT}/{CONTRIBUTED_POINT}: extensions"
            f" (packages/{CONTRIBUTING_MEMBER})",
        ),
    )
    print(
        f"  {CONTRIBUTED_POINT}: proven by hand (run {run.id}: the contributed"
        " point dispatched, its task run, and the point read back green)"
    )


def _release_act(root: Path, kind: str) -> None:
    """Release the member through the loop; verify wheel and receipt.

    The armed release drives prepare, the isolated legs, the release
    pull request through the gate, and the merge; the emitted
    release workflow's wave then publishes to the forge's own
    registry and cuts the receipt tag. Done means measured: the
    served version and the annotated tag, never the exit code alone.
    """
    import livery.toolroom.tools as toolroom
    from livery.workshop._release_driver import release_name

    # Only the members without a receipt release: the driver refuses a
    # set with a member nothing unreleased touches, and a cut receipt
    # is exactly that. A fresh loop releases both in one set.
    all_tags = {name: f"packages/{name}/v0.1.0" for name, _kind in LOOP_MEMBERS}
    listed = toolroom.git.opts(cwd=root, nofail=True)(
        "ls-remote", "--tags", "origin", *all_tags.values()
    )
    cut = {
        name
        for name, tag in all_tags.items()
        if listed.code == 0 and tag in listed.stdout
    }
    for name in sorted(cut):
        _require_receipt_protected(root, all_tags[name], kind)
        print(f"  release: receipt {all_tags[name]} already on the loop")
    names = tuple(name for name, _kind in LOOP_MEMBERS if name not in cut)
    if not names:
        return
    tags = {name: all_tags[name] for name in names}
    _align_main(root)
    # A prepared release PR may survive an earlier pass. Fresh (its
    # branch still contains main) it is the driver's own recovery:
    # left alone, the re-run arms and merges it, which is also how
    # the forge's long post-open conflict-check window is ridden out
    # (measured outlasting the merge retry budget). Stale (main has
    # moved past it) the driver refuses and teaches `abandon`; the
    # loop follows the teach through the verb before releasing.
    release_branch = f"workflow/{release_name(names)}"
    forge, _ = _dev_forge(kind)
    survivor = forge.repository(E2E_OWNER, E2E_REPO).pr.find_by_head(release_branch)
    if survivor is not None and survivor.state == "open":
        toolroom.git.opts(cwd=root)("fetch", "origin", release_branch)
        fresh = toolroom.git.opts(cwd=root, nofail=True)(
            "merge-base", "--is-ancestor", "origin/main", f"origin/{release_branch}"
        )
        if fresh.code == 0:
            print(f"  prepared release PR #{survivor.number} is fresh; recovering it")
        else:
            print(f"  abandoning stale prepared release PR #{survivor.number}")
            toolroom.git.opts(cwd=root)(
                "switch", "-C", release_branch, f"origin/{release_branch}"
            )
            _loop_fm(root, "abandon")
            _align_main(root)
    # Bounded self-heal for the forge's post-open window: right after
    # a release PR opens, the arm can answer 405 for minutes while
    # the forge computes mergeability (measured: still refusing at
    # fifty seconds, mergeable when probed later). The verb's own
    # recovery arms the surviving fresh PR, so re-running it is the
    # ride-out; a failure with no fresh survivor is real and final.
    import time

    repo = forge.repository(E2E_OWNER, E2E_REPO)
    before = toolroom.git.opts(cwd=root, nofail=True)(
        "ls-remote", "origin", "refs/heads/main"
    ).stdout.split()
    before_sha = before[0] if before else ""
    for round_ in range(4):
        if round_:
            print("  waiting out the forge's post-open window (45s)")
            time.sleep(45)
        code = _loop_fm(
            root,
            "workflow.release",
            *names,
            "--armed",
            timeout=1800.0,
            nofail=True,
        )
        if code == 0:
            break
        survivor = repo.pr.find_by_head(release_branch)
        if survivor is None or survivor.state != "open":
            fail("the armed release failed with no fresh PR to recover; see above")
        _align_main(root)
        fresh = toolroom.git.opts(cwd=root, nofail=True)(
            "merge-base", "--is-ancestor", "origin/main", f"origin/{release_branch}"
        )
        if fresh.code != 0:
            fail("the armed release failed and its PR went stale; see above")
    else:
        fail("the forge's post-open window never closed across 4 rounds")
    # The armed release returns at the merge, and the forge moves
    # the ref a beat later (measured: an immediate rev-parse watched
    # the pre-squash commit and called the wave skipped). Poll for
    # the movement; a main that never moves means the recovery arm
    # ran, which cuts receipts itself, and the probes below judge.
    squash = ""
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        moved = toolroom.git.opts(cwd=root, nofail=True)(
            "ls-remote", "origin", "refs/heads/main"
        ).stdout.split()
        if moved and moved[0] != before_sha:
            squash = moved[0]
            break
        time.sleep(3)
    if squash:
        # The wave runs on the squash asynchronously; its red must
        # surface verbatim, because a registry poll alone cannot say
        # whether the wave failed or is merely slow.
        _watch(kind, _lane(kind).alias, squash, require=("release.yml",))
    else:
        # The recovery arm dispatched the wave at the stamping commit,
        # which is not main's tip, so the newest release run is the
        # one to follow: its red surfaces verbatim instead of a blind
        # registry wait that expires on a slow wheels leg.
        print("  main unmoved: the recovery arm dispatched; following its wave")
        _watch_latest(kind, "release.yml")
    served_versions = _serving_probe(root, kind)
    deadline = time.monotonic() + 300
    for name in names:
        while "0.1.0" not in served_versions(name):
            if time.monotonic() >= deadline:
                fail(
                    f"the wave is green but the registry never served"
                    f" {_member_dist(name)} 0.1.0"
                )
            time.sleep(5)
    # The receipt push follows the publish inside the wave, so the
    # tag gets the same patience as the serving probe.
    deadline = time.monotonic() + 120
    for tag in tags.values():
        while True:
            listed = toolroom.git.opts(cwd=root, nofail=True)(
                "ls-remote", "--tags", "origin", tag
            )
            if tag in listed.stdout:
                break
            if time.monotonic() >= deadline:
                fail(f"served, but the receipt tag {tag} is not on the loop")
            time.sleep(5)
        _require_receipt_protected(root, tag, kind)
    served = ", ".join(f"{_member_dist(name)} 0.1.0" for name in names)
    print(f"  release: {served} served, receipts {', '.join(tags.values())} cut")


def _require_receipt_protected(root: Path, tag: str, kind: str = "gitea") -> None:
    """Prove the receipt's protection by attempting the crime.

    A refused delete is the strongest proof and ends it; GitLab
    refuses a protected tag's deletion over git to everyone, so there
    it is the only proof. Gitea ties creation, deletion, and movement
    to one whitelist, so the loop's own lane (the whitelisted admin)
    can delete; there the tag is pushed straight back and the proof is
    the protection's presence with the lane on its whitelist, read
    from the API, which is what binds everyone else.
    """
    import fnmatch
    import json
    import urllib.request

    import livery.toolroom.tools as toolroom

    # Forced: a receipt recut by a later wave is a new tag object, and
    # the workspace may still hold the one an earlier pass fetched.
    toolroom.git.opts(cwd=root, nofail=True)("fetch", "--force", "origin", "tag", tag)
    denied = toolroom.git.opts(cwd=root, nofail=True)(
        "push", "origin", f":refs/tags/{tag}"
    )
    if denied.code != 0:
        print(f"  receipt {tag}: delete refused; protection holds")
        return
    toolroom.git.opts(cwd=root, nofail=True)("push", "origin", f"refs/tags/{tag}")
    if kind != "gitea":
        fail(
            f"the receipt tag {tag} was deletable on {kind}, whose protection"
            " refuses every deletion over git: the contract's assertion did"
            " not hold (the receipt was pushed back)"
        )
    url = os.environ.get("GITEA_URL", "")
    token = os.environ.get("GITEA_TOKEN", "")
    request = urllib.request.Request(
        f"{url}/api/v1/repos/{E2E_OWNER}/{E2E_REPO}/tag_protections",
        headers={"Authorization": f"token {token}"},
    )
    with urllib.request.urlopen(request, timeout=10) as answer:
        entries = json.load(answer)
    covering = [
        entry
        for entry in entries
        if fnmatch.fnmatch(tag, str(entry.get("name_pattern", "")))
    ]
    if not covering:
        fail(
            f"the receipt tag {tag} was deletable and no tag protection"
            " covers it: the contract's assertion did not hold (the"
            " receipt was pushed back)"
        )
    print(
        f"  receipt {tag}: protected; the configuring lane stays on the"
        " whitelist (gitea's one-whitelist model), everyone else is bound"
    )


def _merge_setup(kind: str, sha: str) -> None:
    """Merge the setup pull request when its green head is *sha*.

    Birth's own done-line prescribes it: merging the setup PR proves
    the gate and the protection wiring end to end. Patient through
    the forge's whole 405 family: the mergeability recompute's "try
    again later" and the beat where a finished run's required
    context has not propagated yet ("not all required status checks
    successful", measured seconds after the watch saw the run
    green). Already merged, or a head that moved on, is a quiet
    skip.
    """
    from livery.workshop._new_project import _SETUP_BRANCH
    from livery.workshop._submit import _follow_merge_state

    forge, _ = _dev_forge(kind)
    repo = forge.repository(E2E_OWNER, E2E_REPO)
    pr = repo.pr.find_by_head(_SETUP_BRANCH)
    if pr is None or pr.state != "open":
        print("  setup PR: already merged or gone")
        return
    head = getattr(pr, "head_sha", "")
    if head and head != sha:
        print("  setup PR: head moved on; leaving it to the next run")
        return
    _follow_merge_state(
        repo, kind, pr.number, repo.pr.merge_now, pr.number, title=pr.title
    )
    print(f"  setup PR #{pr.number}: merged; the gate is proven")


# --- the scenarios ---------------------------------------------------------------


@dataclass
class Pass:
    """What one pass carries between its scenarios.

    Attributes:
        forge: The lane's kind, ``gitea`` or ``gitlab``.
        url: The forge's host-side URL from the shared environment.
        fresh: Whether the pass starts over from nothing.
        root: The loop's workspace once ``birth`` has run; None before.
        timings: One entry per scenario run, in order.
        extension: The extension under test; empty for a plain pass.
        stack: The extensions the birth lists; empty for the stock list.
        kinds: The member kinds the members scenario lands; empty for all.
    """

    forge: str
    url: str
    fresh: bool = False
    root: Path | None = None
    timings: list[Timing] = field(default_factory=list)
    extension: str = ""
    stack: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()


@dataclass(frozen=True)
class Timing:
    """What one scenario cost.

    Attributes:
        name: The scenario.
        seconds: Its wall time.
        runs: The runner runs it followed.
        failed: Whether it ended in a refusal or an error.
    """

    name: str
    seconds: float
    runs: int
    failed: bool = False


@dataclass(frozen=True)
class Scenario:
    """One thing the loop proves, by name, with what must have run before it.

    Attributes:
        name: The name ``--scenario`` takes.
        needs: The scenarios this one builds on; a pass runs them
            first, once, whether or not they were named.
        run: The proof, given the pass.
        daemon: Whether it needs the runner's docker socket: the
            release builds the native member's wheel through
            cibuildwheel on the runner, and nothing else does.
    """

    name: str
    needs: tuple[str, ...]
    run: Callable[[Pass], None]
    daemon: bool = False


def _born(pass_: Pass) -> None:
    """Birth or resume the loop's workspace and merge its setup pull request.

    The repository, its protection and the setup pull request through
    ``fm new.project``, the secrets, the wiring to the workshop's own
    dev wheels, the pushed head's runs followed to their verdicts on
    the real runner, and the setup squash merged.
    """
    forge = pass_.forge
    lane, lane_token = _dev_forge(forge)
    root = _loop_home(forge) / E2E_REPO
    if _listed_extensions(root) not in ((), _stack_of(pass_)):
        # A workspace born for another stack is not this pass's: an
        # extension pass and a plain one take turns in one environment.
        for line in start_over(lane, lane_token, root, url=pass_.url, kind=forge):
            print(line)
    elif pass_.fresh and CURRENT.mode != "host":
        # A fresh host-mode pass took a new environment: its forge
        # holds nothing yet. The docker rig is long-lived, so its
        # repository, releases and workspace go here.
        for line in start_over(lane, lane_token, root, url=pass_.url, kind=forge):
            print(line)
    elif pass_.fresh and root.exists():
        shutil.rmtree(root, ignore_errors=True)
    if (root / ".git").is_dir() and _has_remote(root):
        # A resumed birth pushes before it returns, so an existing
        # workspace authenticates first; birth resets the remote, so
        # it authenticates again after. Main then fast-forwards onto
        # the merges the loop itself made: without the reconcile,
        # birth's foreign-repo guard reads our own squash as a
        # stranger's history and refuses. A birth that failed before
        # the forge held the repository left no remote, and resumes
        # as a first birth does.
        _authenticate_remote(root, lane_token, forge)
        _align_main(root)
    root = _birth(
        forge,
        pass_.url,
        index=_dev_index(forge, pass_.stack),
        stack=pass_.stack,
    )
    _authenticate_remote(root, lane_token, forge)
    provision(forge)
    # After the project exists: GitLab's registry belongs to the
    # project, and a fresh start's delete is asynchronous, so a
    # publish before the birth lands in the project being deleted.
    pins = _publish_dev_wheels(forge, pass_.stack)
    sha = _eat_dev_wheels(root, pins, forge)
    print(f"  watching {sha[:12]} on the runner")
    from livery.workshop._new_project import _SETUP_BRANCH

    _watch(forge, pass_.url, sha, branch=_SETUP_BRANCH)
    print("  green: the loop's gate ran on the real runner")
    _merge_setup(forge, sha)
    pass_.root = root


def _stack_of(pass_: Pass) -> tuple[str, ...]:
    """The extensions *pass_*'s birth lists: its own stack, else the stock list."""
    from livery.workshop._extensions import SELF
    from livery.workshop._new_project import birth_extensions

    return pass_.stack or tuple(birth_extensions([SELF]))


def _listed_extensions(root: Path) -> tuple[str, ...]:
    """The entries *root*'s contract lists, options spelled; empty with no contract."""
    from livery.toolroom.store import Spec
    from livery.workshop._extensions import extension_names, extension_options

    if not (root / "workshop.toml").is_file():
        return ()
    options = extension_options(root)
    return tuple(
        str(Spec(name, options.get(name, ()))) for name in extension_names(root)
    )


def _at(pass_: Pass) -> Path:
    """The loop's workspace; a refusal when birth has not run in this pass."""
    if pass_.root is None:
        fail("the loop's workspace is not born yet; `birth` runs before every scenario")
    return pass_.root


#: Every scenario, in dependency order: a scenario's needs come
#: before it, so a chosen subset runs in this order too.
SCENARIOS: tuple[Scenario, ...] = (
    Scenario("birth", (), _born),
    Scenario(
        "verified-skip", ("birth",), lambda p: _prove_verified_skip(_at(p), p.forge)
    ),
    Scenario("members", ("birth",), lambda p: _ensure_members(_at(p), p.kinds)),
    Scenario("ratchet", ("members",), lambda p: _prepare_ratchet(_at(p), p.forge)),
    Scenario("scoped-leg", ("ratchet",), lambda p: _prove_scoped_leg(_at(p), p.forge)),
    Scenario("prose-leg", ("members",), lambda p: _prove_prose_leg(_at(p), p.forge)),
    Scenario("tests-leg", ("members",), lambda p: _prove_tests_leg(_at(p), p.forge)),
    Scenario(
        "release", ("members",), lambda p: _release_act(_at(p), p.forge), daemon=True
    ),
    Scenario("nightly", ("release",), lambda p: _prove_nightly(_at(p), p.forge)),
    Scenario(
        "dispatched-gate",
        ("members",),
        lambda p: _prove_dispatched_gate(_at(p), p.forge),
    ),
    Scenario(
        "contributed-point",
        ("members",),
        lambda p: _prove_contributed_point(_at(p), p.forge),
    ),
)

#: The named sets ``--scenario`` takes beside the scenarios' own names.
SETS: dict[str, tuple[str, ...]] = {
    "develop": ("birth", "verified-skip", "members", "scoped-leg"),
    "release": ("birth", "verified-skip", "members", "scoped-leg", "release"),
    "points": ("nightly", "dispatched-gate", "contributed-point"),
    "extension": ("birth", "members"),
    "all": tuple(scenario.name for scenario in SCENARIOS),
}


def scenarios_for(spec: str) -> tuple[Scenario, ...]:
    """The scenarios *spec* names, their needs added, in the registry's order.

    *spec* is comma-separated set names and scenario names. A name
    that is neither refuses naming both lists; an empty *spec*
    refuses too. A need is run once however many names reach it.
    """
    by_name = {scenario.name: scenario for scenario in SCENARIOS}
    wanted: set[str] = set()
    for token in (part.strip() for part in spec.split(",")):
        if not token:
            continue
        if token in SETS:
            wanted.update(SETS[token])
        elif token in by_name:
            wanted.add(token)
        else:
            fail(
                f"{token!r} is not a scenario or a set; the sets are"
                f" {', '.join(SETS)} and the scenarios {', '.join(by_name)}"
            )
    if not wanted:
        fail(f"--scenario names nothing; the sets are {', '.join(SETS)}")
    pending = list(wanted)
    while pending:
        for need in by_name[pending.pop()].needs:
            if need not in wanted:
                wanted.add(need)
                pending.append(need)
    return tuple(scenario for scenario in SCENARIOS if scenario.name in wanted)


def daemon_needed(scenarios: Sequence[Scenario]) -> bool:
    """Whether any of *scenarios* needs the runner's docker socket."""
    return any(scenario.daemon for scenario in scenarios)


def run_scenario(pass_: Pass, scenario: Scenario) -> None:
    """Run *scenario* for *pass_*, timed; a failure is timed and marked, then raised."""
    import time

    started = time.monotonic()
    runs_before = RUNS.count
    print(f"  scenario {scenario.name}")
    failed = True
    try:
        scenario.run(pass_)
        failed = False
    finally:
        pass_.timings.append(
            Timing(
                scenario.name,
                time.monotonic() - started,
                RUNS.count - runs_before,
                failed,
            )
        )


def timing_table(timings: Sequence[Timing]) -> list[str]:
    """The lines a pass ends with: each scenario's time and runs, then the total."""
    if not timings:
        return ["  timing: nothing ran"]
    width = max(len(timing.name) for timing in timings)
    lines = [
        f"  {timing.name:<{width}}  {timing.seconds:7.1f}s  {timing.runs} run(s)"
        + ("  failed" if timing.failed else "")
        for timing in timings
    ]
    total = sum(timing.seconds for timing in timings)
    runs = sum(timing.runs for timing in timings)
    lines.append(f"  {'total':<{width}}  {total:7.1f}s  {runs} run(s)")
    return lines


#: The local series a pass writes its rows to, declared with the
#: others so `fm store.show loop` reads it and the janitor bounds it.
LOOP_SERIES = LOOP


def record_pass(root: Path, pass_: Pass, asked: str) -> str:
    """Put the pass's timings on the local loop series; the line to print."""
    from datetime import UTC, datetime

    when = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    row: dict[str, object] = {
        "forge": pass_.forge,
        "asked": asked,
        **({"extension": pass_.extension} if pass_.extension else {}),
        "scenarios": [
            {
                "name": timing.name,
                "seconds": round(timing.seconds, 1),
                "runs": timing.runs,
                "failed": timing.failed,
            }
            for timing in pass_.timings
        ],
    }
    why = LOOP_SERIES.put(
        root, {f"pass-{when}.json": row}, message=f"loop: pass {when} ({asked})"
    )
    if why:
        return f"  loop series: {why}; the pass is not recorded"
    return f"  loop series: pass {when} recorded ({len(pass_.timings)} scenario(s))"


if _WORKSHOP_TESTS.is_dir():
    # serial: the driver births into and pushes from its own
    # directories, so it owns the process globals for the run.
    @ci.task(name="e2e", serial=True)
    def e2e(
        forge: str = "gitea",
        fresh: bool = False,
        scenario: str = "",
        env: str = "e2e",
        purge_cache: bool = False,
        extension: str = "",
    ) -> None:
        """Exercise the CI and release story on a local environment, by scenario.

        ``--env`` names the environment the pass runs on, `e2e` by
        default: one that does not exist is brought up in host mode
        with one runner, ``--fresh`` removes it and brings it up
        again, so a fresh pass is a new forge, and ``--purge-cache``
        removes the rig's shared caches first. The docker-mode
        environment ``dev`` is the compose rig, addressed as before.
        ``--scenario`` names what the pass proves: scenario names, set
        names, or both, comma-separated. The sets are ``develop``
        (birth, the verified skip, the members, the scoped leg), the
        default, ``release`` (develop and the release act), ``points``
        (the nightly, the dispatched gate, the contributed point),
        ``extension`` (birth and the members) and ``all``; a scenario's
        needs run first, once. ``--extension`` tests one extension in
        isolation, ``extension`` the default set: the birth lists that
        extension and what it requires instead of the stock list, and
        the members are those of the kinds its checks declare. ``birth`` builds
        this checkout's workshop, its dependencies and the extensions a
        birth lists into a local index, births or resumes the loop's
        workspace through ``fm new.project`` from that index (so an
        extension the birth lists needs no release), with its
        repository, protection and setup pull request, provisions the
        secrets, wires the workspace to the dev wheels in the forge's
        registry, and follows the pushed head's runs to their verdicts
        on the real runner. Re-running is the recovery procedure at every
        step. The pass ends with a table, each scenario's time and
        runs, and records the same rows on the local ``loop`` series.
        ``--fresh`` starts over from nothing: the loop's repository on
        the dev forge, its releases in the forge's registry, and the
        local workspace go, then the pass births everything anew. It
        refuses while the workspace holds commits the forge has not
        seen.
        """
        import os

        from livery.workshop import _devenv
        from livery.workshop._extensions import workspace_root

        asked = scenario or ("extension" if extension else "develop")
        chosen = scenarios_for(asked)
        stack, kinds = extension_under_test(extension) if extension else ((), ())
        if forge != "gitea":
            fail(f"--env addresses a Gitea environment; --forge={forge} has none yet")
        if purge_cache:
            for line in _devenv.purge_cache():
                print(line)
        place = _devenv.environment(env)
        if fresh and place.mode == "host":
            for line in _devenv.remove(place):
                print(line)
            place = _devenv.environment(env)
        if place.mode == "docker":
            CURRENT.name, CURRENT.mode = env, "docker"
        else:
            _devenv.up_host(place, ("host",))
            _enter_host(place.values(), env)
        # Every commit the pass makes, the driver's, the birth's and
        # the loop's own fm's, is unsigned: the setting rides the
        # task's environment into each child.
        os.environ.update(unsigned_environment(os.environ))
        _require_host_alias(forge)
        if daemon_needed(chosen):
            _require_runner_docker(forge)
        pass_ = Pass(
            forge,
            CURRENT.url or os.environ.get(_lane(forge).url_var, ""),
            fresh,
            extension=extension,
            stack=stack,
            kinds=kinds,
        )
        RUNS.count = 0
        try:
            for item in chosen:
                run_scenario(pass_, item)
        finally:
            for line in timing_table(pass_.timings):
                print(line)
            driver = workspace_root()
            if driver is not None:
                print(record_pass(driver, pass_, asked))
        names = ", ".join(item.name for item in chosen)
        print(f"  the loop proved {names}")
