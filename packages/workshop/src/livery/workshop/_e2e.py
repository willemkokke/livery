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
from pathlib import Path
from typing import TYPE_CHECKING

import livery.footman as footman
from livery.footman import fail
from livery.workshop._ci_tasks import ci

if TYPE_CHECKING:
    from livery.forge import Forge, Job, Repository, Run

#: The seeded organisation and the loop's one scratch repository.
E2E_OWNER = "livery"
E2E_REPO = "ci-e2e-loop"

#: The workshop's own test suite, present only in a source checkout:
#: this file is src/livery/workshop/_e2e.py, so the package directory
#: holding tests/ is three parents up.
_WORKSHOP_TESTS = Path(__file__).resolve().parents[3] / "tests"

#: The template source the loop renders from: this package's own
#: tree, so the loop tests the templates being edited.
_TEMPLATES = Path(__file__).parent / "templates"

#: The members whose dev wheels the loop eats, in the act's order.
DEV_MEMBERS = ("workshop", "forge", "toolroom", "footman")


def _dev_forge(kind: str) -> tuple[Forge, str]:
    """The seeded local forge and its token; refusal teaches.

    The credentials are the ones ``fm forge.dev.up`` writes into the
    shared env file the cascade reads, so a warm machine needs
    nothing beyond the containers being up.
    """
    if kind != "gitea":
        fail(
            f"--forge={kind} is not built: gitea is the one local lane"
            " today, and gitlab follows once gitea is in a good state"
        )
    url = os.environ.get("GITEA_URL", "")
    token = os.environ.get("GITEA_TOKEN", "")
    if not url or not token:
        fail(
            "the local Gitea's credentials are not in the environment."
            f" Run `{footman.prog()} forge.dev.up --profile=gitea`: it"
            " starts and seeds"
            " the containers and writes GITEA_URL and GITEA_TOKEN into"
            " the shared env file the cascade reads"
        )
    from livery.forge import GiteaForge

    return GiteaForge.connect(url=url, token=token), token


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
    # Three secrets, all the lane token: the registry credential the
    # publish reads, the forge lane's everyday token, and the admin
    # ladder's, because the emitted governance job asks for it and
    # the seeded admin account holds every grant anyway.
    repo.configure(
        RepoConfig(
            secrets={
                "UV_PUBLISH_TOKEN": token,
                "FORGE_TOKEN": token,
                "FORGE_ADMIN_TOKEN": token,
            }
        )
    )
    print("  secrets set: UV_PUBLISH_TOKEN, FORGE_TOKEN, FORGE_ADMIN_TOKEN")


#: One forge URL true on both sides: the compose hostname, which
#: the runner resolves natively and the host through its taught
#: /etc/hosts alias. The contract carries it, so in-container verbs
#: reach the forge the same way host-side ones do.
ALIAS_URL = "http://gitea:3000"

#: The registry as CI sees it: the compose network's service name,
#: which the runner resolves and the host does not. The host-side
#: half of the same registry is GITEA_URL.
LOOP_INDEX = "http://gitea:3000/api/packages/livery/pypi/simple"

#: The upload base the loop's releases publish to; the read index
#: above is this plus /simple.
LOOP_PUBLISH = "http://gitea:3000/api/packages/livery/pypi"

#: The member's distribution name: new.package prefixes the
#: project's namespace (ci_e2e_loop), so the dist is never
#: "livery-loop-echo".
LOOP_MEMBER_DIST = "ci-e2e-loop-loop-echo"

#: The loop's members, name and template kind (empty for the default
#: python kind). The nanobind member forces the wheels path on
#: every pass: its wheel is built on the runner through cibuildwheel,
#: published, and installed in the isolated leg.
LOOP_MEMBERS: tuple[tuple[str, str], ...] = (
    ("loop-echo", ""),
    ("loop-native", "package-python-nanobind"),
)


def _member_dist(name: str) -> str:
    """The distribution name ``new.package`` gives the loop member *name*."""
    return f"ci-e2e-loop-{name}"


def _require_host_alias() -> None:
    """Refuse until the host resolves the compose hostname.

    One registry URL must be true on both sides of the loop: the
    runner resolves the compose service name ``gitea``, the host
    does not, and a split URL forces an unlocked workspace and a
    hostname fork through every file. The one-line alias makes the
    compose name true on the host too, and the lock then carries a
    URL both sides can read.
    """
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen("http://gitea:3000/api/v1/version", timeout=2):
            return
    except (urllib.error.URLError, TimeoutError, OSError):
        fail(
            "the host cannot reach http://gitea:3000, the compose"
            " hostname CI uses for the registry (a network resolving"
            " 'gitea' elsewhere fails the same way; /etc/hosts wins"
            " over DNS). Add the alias once:"
            " sudo sh -c 'echo \"127.0.0.1 gitea\" >> /etc/hosts'"
        )


def _loop_home() -> Path:
    """Where the loop's workspace lives: durable, per machine."""
    from livery.footman.context import data_dir

    return data_dir() / "workshop-e2e"


def _dev_pins(
    root: Path, head: str, members: tuple[str, ...] = DEV_MEMBERS
) -> dict[str, str]:
    """The dev versions this pass built, by distribution name.

    Read from the newest wheel in each member's ``dist``, which the
    dev act just filled, and checked against *head*: a dev version's
    local segment is ``<branch>.<sha>.<date>``, ``.dirty`` appended
    for a tree no commit describes, so a wheel a previous pass left
    behind can never pin the loop. Refuses, naming the member, when
    a dist holds no wheel or its newest wheel is another commit's.
    """
    pins: dict[str, str] = {}
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
        pins[name.replace("_", "-")] = version
    return pins


def _publish_dev_wheels(kind: str) -> dict[str, str]:
    """Publish the workspace's dev wheels to the loop's registry; the pins.

    The loop installs the workshop being edited, so every pass
    publishes fresh dev wheels first: a lock pinned to an older dev
    version would test yesterday's code with today's templates. The
    dev act is idempotent at a given commit, and a re-publish of the
    same version walks past. Returns the published versions by
    distribution name, for the loop's lock to pin exactly.
    """
    from livery.footman import run
    from livery.forge._registry import purge_packages
    from livery.workshop._dev_release import unchanged_since_release
    from livery.workshop._git_ops import GitOps
    from livery.workshop._layers import workspace_root
    from livery.workshop._packages import discover_packages

    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    _, token = _dev_forge(kind)
    git = GitOps(root)
    # A member nothing unreleased touches cannot be built as a dev
    # wheel: that number would sort below its release and satisfy no
    # floor naming it. The loop pins the release instead, and drops
    # the member's stale rehearsal wheels from the registry, which a
    # first-index resolve would otherwise pick over the release.
    packages = {package.directory.name: package for package in discover_packages(root)}
    changed: list[str] = []
    released: dict[str, str] = {}
    for member in DEV_MEMBERS:
        version = unchanged_since_release(root, git, packages[member])
        if version:
            released[packages[member].name] = version
            print(
                f"  {member}: nothing unreleased since {version}; the loop pins"
                " the release"
            )
        else:
            changed.append(member)
    if released:
        purged = purge_packages(
            os.environ.get("GITEA_URL", ""), E2E_OWNER, token=token, names=released
        )
        print(
            f"  registry: {len(purged)} stale rehearsal release(s) of the pinned"
            " member(s) dropped"
        )
    if changed:
        run(
            ["fm", "--yes", "workflow.release", *changed],
            cwd=root,
            # The whole environment, extended: env= replaces, and a bare
            # pair would strip PATH from under the child fm.
            env={
                **os.environ,
                "PYTHON_PUBLISH_INDEX": ALIAS_URL
                + "/api/packages/"
                + E2E_OWNER
                + "/pypi",
                "UV_PUBLISH_TOKEN": token,
            },
        )
    pins = _dev_pins(root, git.head_sha(), tuple(changed)) | released
    print("  dev wheels: published to the loop's registry")
    return pins


def start_over(lane: Forge, token: str, root: Path, *, url: str) -> list[str]:
    """Delete the loop's repository, its registry releases, and *root*; the lines.

    Refuses while *root* holds commits its origin has not seen, since
    the repository they would land in is about to go. A repository or
    a release already gone is not an error: the next birth wants them
    absent, and a re-run of the reset is the recovery procedure.
    """
    from livery.forge._registry import purge_packages

    if (root / ".git").is_dir():
        unpushed = _unpushed_commits(root)
        if unpushed:
            listed = "\n".join(f"    {line}" for line in unpushed)
            fail(
                f"{root} holds commits its origin has not seen:\n{listed}\n"
                "  push or discard them before starting over"
            )
    lines: list[str] = []
    lane.delete_repo(E2E_OWNER, E2E_REPO)
    lines.append(f"  deleted {E2E_OWNER}/{E2E_REPO} on the dev forge")
    purged = purge_packages(url, E2E_OWNER, token=token)
    lines.append(
        f"  purged {len(purged)} release(s) from the registry"
        + (f": {', '.join(purged)}" if purged else "")
    )
    if root.exists():
        _rmtree(root)
        lines.append(f"  removed {root}")
    return lines


def _rmtree(path: Path) -> None:
    """Remove *path* whole; git's pack files are read-only, which Windows honours."""
    import stat

    for entry in path.rglob("*"):
        if entry.is_file() and not entry.is_symlink():
            entry.chmod(entry.stat().st_mode | stat.S_IWRITE)
    shutil.rmtree(path)


def _unpushed_commits(root: Path) -> list[str]:
    """The commits on any local branch of *root* that its origin does not hold."""
    import livery.toolroom as toolroom

    fetched = toolroom.git.opts(cwd=root, nofail=True, recorded=False)(
        "fetch", "--quiet", "origin"
    )
    if fetched.code != 0:
        return []  # no reachable origin: nothing there could hold them anyway
    listed = toolroom.git.opts(cwd=root, nofail=True, recorded=False)(
        "log", "--oneline", "--branches", "--not", "--remotes=origin"
    )
    return [line for line in listed.stdout.splitlines() if line.strip()]


def _birth(kind: str, url: str) -> Path:
    """Birth or resume the loop's workspace; the root it lives at.

    ``fm new.project`` owns the whole half: render, git, repository,
    protection, and the setup pull request. Re-running resumes, so
    this is the recovery procedure too. The templates are this
    package's own tree: the loop tests the source being edited.
    """
    import contextlib

    from livery.workshop._new_project import new_project

    home = _loop_home()
    home.mkdir(parents=True, exist_ok=True)
    with contextlib.chdir(home):
        new_project(
            E2E_REPO,
            forge=kind,
            owner=E2E_OWNER,
            url=ALIAS_URL,
            templates=str(_TEMPLATES),
            description="The workshop's local CI loop. Scratch; recreated freely.",
        )
    return home / E2E_REPO


def _authenticate_remote(root: Path, token: str) -> None:
    """Embed the lane token in the scratch workspace's remote.

    Birth writes a credential-free remote by design and a human's
    pushes ride their credential helper; the loop is automation on a
    scratch workspace, so the token rides the remote URL instead.
    Unrecorded, so the credential never reaches a receipt. Gitea
    validates the token and ignores the username (measured), and
    ``oauth2`` is the conventional stand-in.
    """
    import livery.toolroom as toolroom

    bare = ALIAS_URL.removeprefix("http://")
    url = f"http://oauth2:{token}@{bare}/{E2E_OWNER}/{E2E_REPO}.git"
    result = toolroom.git.opts(cwd=root, nofail=True, recorded=False)(
        "remote", "set-url", "origin", url
    )
    if result.code != 0:
        fail(f"git remote set-url exited {result.code}")


_TEMPLATES_LINE = re.compile(r'^templates = ".*"$', re.MULTILINE)


def _point_templates(contract: str, templates: Path) -> str:
    """The contract text with its template source set to *templates*.

    Birth seeds the source once, from the worktree that births; every
    later pass runs from whichever worktree invokes it, and a render
    from the birthing worktree's templates would test that tree's
    files under this tree's wheels. Refuses when the contract names
    no source at all, because birth always seeds one.
    """
    match = _TEMPLATES_LINE.search(contract)
    if match is None:
        fail(
            "the loop's contract names no template source; birth seeds"
            " one, so this workspace was not born by the loop"
        )
    from livery.workshop._contract import toml_string

    return (
        contract[: match.start()]
        + f"templates = {toml_string(templates.as_posix())}"
        + contract[match.end() :]
    )


def _lock_pins(root: Path, pins: dict[str, str]) -> None:
    """Lock the loop onto exactly the dev wheels this pass published.

    The dev number counts commits since the release tag, so a longer
    branch publishes a higher number and a lock left to upgrade
    freely keeps resolving that branch's wheels (measured: the loop
    ran one branch's emitter under another branch's wheels). The
    pins name the four versions outright; everything else keeps its
    locked version.
    """
    import livery.toolroom as toolroom

    args = [f"--upgrade-package={name}=={version}" for name, version in pins.items()]
    result = toolroom.uv.opts(cwd=root, nofail=True)("lock", *args)
    if result.code != 0:
        fail(
            f"uv lock in the loop workspace exited {result.code}:"
            f"\n{result.stdout}{result.stderr}"
        )


def _eat_dev_wheels(root: Path, pins: dict[str, str]) -> str:
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
    for name in ("workshop.toml", ".copier-answers.yml"):
        f = root / name
        if f.is_file():
            body = f.read_text("utf-8")
            if "http://localhost:3000" in body:
                f.write_text(body.replace("http://localhost:3000", ALIAS_URL), "utf-8")
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
            + f'url = "{LOOP_INDEX}"\n'
            + f'publish = "{LOOP_PUBLISH}"\n'
        )
    elif f'publish = "{LOOP_PUBLISH}"' not in contract_text:
        contract_text = contract_text.replace(
            f'url = "{LOOP_INDEX}"\n',
            f'url = "{LOOP_INDEX}"\npublish = "{LOOP_PUBLISH}"\n',
            1,
        )
    contract_text = re.sub(r'prerelease = "[^"]*"\n', "", contract_text)
    if "[docs]" not in contract_text:
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
    if "speed-marks" not in contract_text:
        # Seeded on its own: an adopted loop born before the key
        # exists keeps its contract, and the judge stays off without
        # it, which the scoped-leg proof pins.
        contract_text = contract_text.replace(
            "\n[ci]\n", "\n[ci]\nspeed-marks = true\n", 1
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
    contract_text = _point_templates(contract_text, _TEMPLATES)
    if contract_text != original:
        contract_file.write_text(contract_text, "utf-8")
    pyproject = root / "pyproject.toml"
    text = pyproject.read_text("utf-8")
    marker = "[[tool.uv.index]]"
    if marker not in text:
        # Bootstrap only: birth's pyproject predates the registry in
        # the contract, and the lock below must already read the loop
        # index to find the dev wheels. The re-render after the lock
        # replaces this edit with the template's own wiring, and from
        # then on this branch never fires again. The index table
        # lands before the next table header, inside [tool.uv].
        next_table = "\n[tool.uv.workspace]"
        wired = text.replace(
            next_table,
            f'\n{marker}\nname = "loop"\nurl = "{LOOP_INDEX}"\n' + next_table,
            1,
        )
        if wired == text:
            fail(
                "the workspace's pyproject has no [tool.uv.workspace]"
                " table to anchor the loop index on; the template moved"
                " and this wiring must follow it"
            )
        pyproject.write_text(wired, "utf-8")
    # Lock first, render second: the loop's fm syncs its venv from
    # the lock, so the render runs the workshop this pass published,
    # and the workflows it emits are the emitter under test. The
    # render may rewrite pyproject (the registry wiring, the roster),
    # so the lock settles on the same pins once more afterwards; an
    # unchanged pyproject makes that a no-op.
    _lock_pins(root, pins)
    _loop_fm(root, "template.apply")
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
        git.push_force(_SETUP_BRANCH)
        print("  dev wheels: wired and pushed")
    return git.head_sha()


def _watch_latest(kind: str, workflow: str, *, timeout: float = 900.0) -> None:
    """Follow the newest run of *workflow* to its verdict; red fails verbatim."""
    import time

    forge, _ = _dev_forge(kind)
    repo = forge.repository(E2E_OWNER, E2E_REPO)
    deadline = time.monotonic() + timeout
    while True:
        runs = [run for run in repo.checks.runs() if run.workflow.endswith(workflow)]
        latest = max(runs, key=lambda run: run.id, default=None)
        if latest is not None and latest.status == "completed":
            if latest.conclusion != "success":
                fail(
                    f"{workflow} run {latest.id} ended {latest.conclusion}:"
                    f" {repo.web_url()}/actions/runs/{latest.id}"
                )
            print(f"  {workflow:<14} {latest.conclusion}")
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
    retried: list[str] = []
    for run in runs:
        if run.conclusion != "failure":
            continue
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
    while True:
        runs = repo.checks.runs(head_sha=sha)
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
    for line in judge_runs(repo, runs, branch=branch):
        print(line)


def _align_main(root: Path) -> None:
    """Align the loop's main hard onto origin, superseded commits named.

    Origin is the loop's truth: every local byte is regenerable
    render output, and local main gathers aftercare commits the
    squashes supersede, so a fast-forward regularly cannot. What
    goes is printed, never silently vanished.
    """
    import livery.toolroom as toolroom

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
    # The venv survives, everything else is regenerable by charter.
    residue = toolroom.git.opts(cwd=root, nofail=True)("clean", "-ndx", "-e", ".venv")
    if residue.code == 0 and residue.stdout.strip():
        for line in residue.stdout.strip().splitlines():
            print(f"  cleaned: {line.removeprefix('Would remove ')}")
        toolroom.git.opts(cwd=root)("clean", "-fdx", "-e", ".venv")
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
    import livery.toolroom as toolroom

    _align_main(root)
    toolroom.git.opts(cwd=root)("switch", "-C", name)


def _loop_fm(
    root: Path, *args: str, timeout: float = 900.0, nofail: bool = False
) -> int:
    """Run the loop's own fm, the dev wheels' one, inside the loop.

    The whole point of the substrate: the workspace under test runs
    the workshop being edited, so submit, release, and every other
    verb exercise the dev wheels end to end. ``--yes`` rides every
    call, because the loop is automation and silence never confirms.
    """
    import livery.toolroom as toolroom

    # The caller's VIRTUAL_ENV points at the worktree; the loop's uv
    # must resolve the loop's own venv, so the variable stays behind.
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
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


def _ensure_members(root: Path) -> None:
    """Give the loop its members, each landed through its own gate.

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
    for name, kind in LOOP_MEMBERS:
        if (root / "packages" / name / "workshop.toml").is_file():
            print(f"  member {name}: already landed")
            continue
        git = GitOps(root)
        _fresh_branch(root, f"feat/{name}")
        _loop_fm(root, "new.package", name, *([f"--kind={kind}"] if kind else []))
        member = root / "packages" / name / "workshop.toml"
        body = member.read_text("utf-8")
        if kind:
            # The seed names the hosted runners; the loop's fleet is its
            # one linux container, and the leg must be schedulable.
            body = body.replace(
                'wheel-platforms = ["ubuntu-latest", "macos-latest", "windows-latest"]',
                'wheel-platforms = ["ubuntu-latest"]',
            )
        if "[release]" not in body:
            body = (
                body.rstrip("\n")
                + "\n\n[release]\n# The first release lands at the baseline.\n"
                + 'baseline = "0.1.0"\n'
            )
        member.write_text(body, "utf-8")
        # The baseline is a render input: cliff.toml was rendered before
        # the append, so it must settle again or the gate names it as
        # drift.
        _loop_fm(root, "template.apply")
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
    while True:
        runs = [
            run
            for run in repo.checks.runs(head_sha=sha)
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
    _require_lines(
        repo,
        run,
        logs,
        "gate",
        (
            f"coverage record: main takes {_SETUP_BRANCH}'s record for the tree"
            " it proved",
            "coverage: packages/loop-echo on check-ubuntu-latest-3.14: reused from run",
            "coverage: packages/loop-native on check-ubuntu-latest-3.14:"
            " reused from run",
            "coverage: tests on check-ubuntu-latest-3.14: reused from run",
            "coverage packages/loop-echo: 100.0% (floor 100.0%",
            "coverage packages/loop-native: 100.0% (",
            "coverage: the union of 0 leg(s) and 3 reused suite(s)",
            "coverage record: main/check-ubuntu-latest-3.14: 0 fresh, 3 carried,"
            " 0 removed",
        ),
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
    import livery.toolroom as toolroom
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
    import livery.toolroom as toolroom

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
            "affected: packages/loop-echo",
            "coverage store: packages/loop-echo stored for closure",
            "coverage store: tests stored for closure",
        ),
        forbidden=(
            "affected: packages/loop-echo, packages/loop-native",
            "coverage store: packages/loop-native runs",
        ),
    )
    # The union takes the one member the leg ran from the leg and the
    # other from the store, and judges both.
    _require_lines(
        repo,
        run,
        logs,
        "gate",
        (
            "coverage: packages/loop-native on check-ubuntu-latest-3.14:"
            " reused from run",
            "coverage packages/loop-echo: 100.0% (floor 100.0%",
            "coverage packages/loop-native: 100.0% (mark 90.0% accept by",
            "accepted: the loop proves an accepted lowering",
            "new mark: 100.0%",
            "coverage: the union of 1 leg(s) and 1 reused suite(s)",
            "coverage record: chore/scoped-leg/check-ubuntu-latest-3.14: 2 fresh,"
            " 1 carried, 0 removed",
            "speed packages/loop-echo on check-ubuntu-latest-3.14: ",
            "recorded as proved green by run",
            " on top of tree ",
            "is not a release branch: nothing to check",
        ),
        forbidden=("unjudged this run", "not recorded:"),
    )
    print(
        "  scoped leg: proven on a member-only pull request (affected:"
        " packages/loop-echo; the union reused loop-native from main's record"
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
            "coverage: the union of 0 leg(s) and 3 reused suite(s)",
            "coverage record: main/check-ubuntu-latest-3.14: 0 fresh, 3 carried,"
            " 0 removed",
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
    judge both members, and compose the stamp with main's verified
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
    import livery.toolroom as toolroom

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
            "coverage: tests on check-ubuntu-latest-3.14: reused from run",
            "coverage packages/loop-echo: 100.0% (floor 100.0%",
            "coverage packages/loop-native: 100.0% (",
            "coverage: the union of 0 leg(s) and 3 reused suite(s)",
            "coverage record: chore/prose-leg/check-ubuntu-latest-3.14: 0 fresh,"
            " 3 carried, 0 removed",
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
    union judges both members from main's record, writes the branch's
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
    import livery.toolroom as toolroom

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
            "coverage packages/loop-echo: 100.0% (floor 100.0%",
            "coverage packages/loop-native: 100.0% (",
            "coverage: the union of 1 leg(s) and 2 reused suite(s)",
            "coverage record: chore/tests-leg/check-ubuntu-latest-3.14: 1 fresh,"
            " 2 carried, 0 removed",
            "recorded as proved green by run",
            " on top of tree ",
        ),
        forbidden=("unjudged this run", "coverage: tests on check-ubuntu-latest-3.14"),
    )
    print(
        f"  tests leg: proven on a workspace-tests pull request (run {run.id}: the"
        " check leg ran the workspace tests alone, the union reused both members"
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


def _release_act(root: Path, kind: str) -> None:
    """Release the member through the loop; verify wheel and receipt.

    The armed release drives prepare, the isolated legs, the release
    pull request through the gate, and the merge; the emitted
    release workflow's wave then publishes to the forge's own
    registry and cuts the receipt tag. Done means measured: the
    served version and the annotated tag, never the exit code alone.
    """
    import livery.toolroom as toolroom
    from livery.forge import SimpleRegistry
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
        _require_receipt_protected(root, all_tags[name])
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
        _watch(kind, ALIAS_URL, squash, require=("release.yml",))
    else:
        # The recovery arm dispatched the wave at the stamping commit,
        # which is not main's tip, so the newest release run is the
        # one to follow: its red surfaces verbatim instead of a blind
        # registry wait that expires on a slow wheels leg.
        print("  main unmoved: the recovery arm dispatched; following its wave")
        _watch_latest(kind, "release.yml")
    _, token = _dev_forge(kind)
    registry = SimpleRegistry(
        f"{ALIAS_URL}/api/packages/{E2E_OWNER}/pypi/simple", token=token
    )
    deadline = time.monotonic() + 300
    for name in names:
        while "0.1.0" not in registry.versions(_member_dist(name)):
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
        _require_receipt_protected(root, tag)
    served = ", ".join(f"{_member_dist(name)} 0.1.0" for name in names)
    print(f"  release: {served} served, receipts {', '.join(tags.values())} cut")


def _require_receipt_protected(root: Path, tag: str) -> None:
    """Prove the receipt's protection by attempting the crime.

    A refused delete is the strongest proof and ends it. Gitea ties
    creation, deletion, and movement to one whitelist, so the loop's
    own lane (the whitelisted admin) can delete; there the tag is
    pushed straight back and the proof is the protection's presence
    with the lane on its whitelist, read from the API, which is what
    binds everyone else.
    """
    import fnmatch
    import json
    import urllib.request

    import livery.toolroom as toolroom

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
        repo, "gitea", pr.number, repo.pr.merge_now, pr.number, title=pr.title
    )
    print(f"  setup PR #{pr.number}: merged; the gate is proven")


if _WORKSHOP_TESTS.is_dir():
    # serial: the driver births into and pushes from its own
    # directories, so it owns the process globals for the run.
    @ci.task(name="e2e", serial=True)
    def e2e(forge: str = "gitea", fresh: bool = False) -> None:
        """Exercise the CI and release story on the local forge.

        Births or resumes the loop's workspace through
        ``fm new.project`` (repository, protection, setup pull
        request), provisions the secrets, wires the workspace to the
        workshop's own dev wheels, and follows the pushed head's
        workflow runs to their verdicts on the real runner. Re-running
        is the recovery procedure at every step. The release act into
        the local registry follows in this phase; the verb always says
        exactly what it covers. ``--fresh`` starts over from nothing:
        the loop's repository on the dev forge, its releases in the
        forge's registry, and the local workspace go, then the pass
        births everything anew. It refuses while the workspace holds
        commits the forge has not seen.
        """
        import os

        url = os.environ.get("GITEA_URL", "")
        _require_host_alias()
        lane, lane_token = _dev_forge(forge)
        root = _loop_home() / E2E_REPO
        if fresh:
            for line in start_over(lane, lane_token, root, url=url):
                print(line)
        pins = _publish_dev_wheels(forge)
        if (root / ".git").is_dir():
            # A resumed birth pushes before it returns, so an
            # existing workspace authenticates first; birth resets
            # the remote, so it authenticates again after. Main then
            # fast-forwards onto the merges the loop itself made:
            # without the reconcile, birth's foreign-repo guard reads
            # our own squash as a stranger's history and refuses.

            _authenticate_remote(root, lane_token)
            _align_main(root)
            # Birth's resume renders from the contract's template
            # source before the wiring re-points it, and the source a
            # previous pass named may be a worktree that no longer
            # exists; this pass's templates are the source, from here.
            contract = root / "workshop.toml"
            contract.write_text(
                _point_templates(contract.read_text("utf-8"), _TEMPLATES), "utf-8"
            )
        root = _birth(forge, url)
        _authenticate_remote(root, lane_token)
        provision(forge)
        sha = _eat_dev_wheels(root, pins)
        print(f"  watching {sha[:12]} on the runner")
        from livery.workshop._new_project import _SETUP_BRANCH

        _watch(forge, url, sha, branch=_SETUP_BRANCH)
        print("  green: the loop's gate ran on the real runner")
        _merge_setup(forge, sha)
        _prove_verified_skip(root, forge)
        _ensure_members(root)
        _prepare_ratchet(root, forge)
        _prove_scoped_leg(root, forge)
        _prove_prose_leg(root, forge)
        _prove_tests_leg(root, forge)
        _release_act(root, forge)
        _prove_nightly(root, forge)
        print("  the loop is whole: gate, merge, release, receipt, nightly")
