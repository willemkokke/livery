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
from pathlib import Path
from typing import TYPE_CHECKING

import livery.footman as footman
from livery.footman import fail
from livery.workshop._ci_tasks import ci

if TYPE_CHECKING:
    from livery.forge import Forge

#: The seeded organisation and the loop's one scratch repository.
E2E_OWNER = "livery"
E2E_REPO = "ci-e2e-loop"

#: The workshop's own test suite, present only in a source checkout:
#: this file is src/livery/workshop/_e2e.py, so the package directory
#: holding tests/ is three parents up.
_WORKSHOP_TESTS = Path(__file__).resolve().parents[3] / "tests"


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


def _publish_dev_wheels(kind: str) -> None:
    """Publish the workspace's dev wheels to the loop's registry.

    The loop installs the workshop being edited, so every pass
    publishes fresh dev wheels first: a lock pinned to an older dev
    version would test yesterday's code with today's templates. The
    dev act is idempotent at a given commit, and a re-publish of the
    same version walks past.
    """
    from livery.footman import run
    from livery.workshop._layers import workspace_root

    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    _, token = _dev_forge(kind)
    run(
        ["fm", "--yes", "workflow.release", "workshop", "forge", "toolroom", "footman"],
        cwd=root,
        # The whole environment, extended: env= replaces, and a bare
        # pair would strip PATH from under the child fm.
        env={
            **os.environ,
            "PYTHON_PUBLISH_INDEX": ALIAS_URL + "/api/packages/" + E2E_OWNER + "/pypi",
            "UV_PUBLISH_TOKEN": token,
        },
    )
    print("  dev wheels: published to the loop's registry")


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
    templates = Path(__file__).parent / "templates"
    with contextlib.chdir(home):
        new_project(
            E2E_REPO,
            forge=kind,
            owner=E2E_OWNER,
            url=ALIAS_URL,
            templates=str(templates),
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


def _eat_dev_wheels(root: Path) -> str:
    """Point the workspace at the dev wheels; the pushed head sha.

    The registry joins the contract, the template renders it into
    uv's config, and the lock goes: the in-container sync then
    resolves the workshop's own dev wheels fresh, so the installed
    workshop matches the emitter that rendered the workflows. Measured necessity: a released workshop predating
    the emitted verbs fails the docs job with "no task named".
    Idempotent: an already-wired workspace pushes nothing.
    """
    import livery.toolroom as toolroom
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
    contract_text = contract_file.read_text("utf-8")
    # Policy if-necessary, never a global allow: uv's first-index
    # strategy resolves the workshop from the loop index, where only
    # prereleases exist, so if-necessary admits the dev wheels there
    # while every PyPI-served package keeps its stable release
    # (measured: a global allow resolved mkdocs 2.0.dev3 and the
    # docs job lost mkdocs.exceptions).
    import re

    policy = 'prerelease = "if-necessary"\n'
    if "[registries.python]" not in contract_text:
        contract_file.write_text(
            contract_text.rstrip("\n")
            + "\n\n# The loop eats the workshop's dev wheels from the local\n"
            + "# registry; the compose hostname is true on both sides,\n"
            + "# the host through its /etc/hosts alias.\n"
            + "[registries.python]\n"
            + f'url = "{LOOP_INDEX}"\n'
            + policy,
            "utf-8",
        )
    elif policy not in contract_text:
        # An earlier pass declared another mode; settle on this one.
        contract_file.write_text(
            re.sub(r'prerelease = "[^"]*"\n', policy, contract_text, count=1),
            "utf-8",
        )
    # The loop tracks the worktree's templates live, so each pass
    # re-applies the render before judging cleanliness: worktree
    # edits reach the loop's rendered files here, not as drift reds
    # inside the loop's own gate.
    _loop_fm(root, "template.apply")
    pyproject = root / "pyproject.toml"
    text = pyproject.read_text("utf-8")
    marker = "[[tool.uv.index]]"
    if marker not in text:
        # Bootstrap only: a stale venv's workshop renders the old
        # template, which does not read [registries]. The edit holds
        # until the re-lock below installs the dev workshop; from
        # then on the template itself renders the wiring and this
        # branch never fires again.
        # Two inserts with TOML's structure respected: the scalar
        # stays inside [tool.uv] (a key after an array-of-tables
        # header would silently join that table instead, and
        # default-groups once vanished exactly that way), and the
        # index table lands before the next table header.
        anchor = "[tool.uv]\n"
        wired = text.replace(
            anchor,
            anchor
            + "# The loop eats the workshop's dev wheels from the local\n"
            + "# registry; the compose hostname is true on both sides,\n"
            + "# the host through its /etc/hosts alias.\n"
            + policy,
            1,
        )
        next_table = "\n[tool.uv.workspace]"
        wired2 = wired.replace(
            next_table,
            f'\n{marker}\nname = "loop"\nurl = "{LOOP_INDEX}"\n' + next_table,
            1,
        )
        if wired2 in (text, wired) or wired == text:
            fail(
                "the workspace's pyproject has no [tool.uv] table to"
                " anchor the loop index on; the template moved and this"
                " wiring must follow it"
            )
        pyproject.write_text(wired2, "utf-8")
    # Re-lock when the tree moved or the lock is absent: birth's
    # resume re-renders pyproject (a dirty tree), and the wiring
    # above dirties it too, so the lock is rebuilt exactly when it
    # could be stale. A clean, locked workspace relocks nothing,
    # which keeps the re-run a true no-op. The alias makes the
    # compose hostname true on the host, so the lock's URL holds
    # on both sides.
    # Always relock: every pass publishes fresh dev wheels, and a
    # lock pinning the previous pass's version would keep installing
    # yesterday's bytes (measured: the loop stayed red on a bug the
    # worktree had already fixed). An unchanged worktree republishes
    # the same version, the lock re-resolves identically, and the
    # cleanliness check below still yields the no-op.

    result = toolroom.uv.opts(cwd=root, nofail=True)("lock", "--upgrade")
    if result.code != 0:
        fail(f"uv lock in the loop workspace exited {result.code}")
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


def _watch(kind: str, url: str, sha: str, *, timeout: float = 900.0) -> None:
    """Follow *sha*'s runs to their verdicts; red fails verbatim.

    The one deliberate tolerance: the docs deploy, which publishes a
    container image and the loop's runner carries no docker. The dev
    shape stubs docs entirely once the workspace declares it; until
    then the deploy's red is named, never counted.
    """
    import time

    forge, _ = _dev_forge(kind)
    repo = forge.repository(E2E_OWNER, E2E_REPO)
    deadline = time.monotonic() + timeout
    while True:
        runs = repo.checks.runs(head_sha=sha)
        if runs and all(r.status == "completed" for r in runs):
            break
        if time.monotonic() >= deadline:
            fail(
                f"the loop's runs did not complete within {timeout:.0f}s:"
                f" {repo.web_url()}/actions"
            )
        time.sleep(5)
    failed = []
    for run in runs:
        verdict = run.conclusion or run.status
        print(f"  {run.workflow:14} {verdict}")
        if run.conclusion == "failure":
            if run.workflow == "docs.yml":
                print("    tolerated: the deploy seam wants docker, and")
                print("    the dev shape stubs docs once declared")
                continue
            failed.append(run)
    if failed:
        names = ", ".join(r.workflow for r in failed)
        fail(f"red runs on the loop: {names}; logs: {repo.web_url()}/actions")


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


def _fresh_branch(root: Path, name: str) -> None:
    """A pass-owned branch, recreated from an aligned main.

    ``switch -C`` from a dirty tree drags or clobbers state, so the
    alignment and the switch are one operation here, never two calls
    a future edit can separate: the tree is reset to origin and
    cleaned before the branch is recreated on it.
    """
    import livery.toolroom as toolroom

    _align_main(root)
    stale = toolroom.git.opts(cwd=root, nofail=True)(
        "log", "--oneline", f"main..{name}"
    )
    if stale.code == 0 and stale.stdout.strip():
        for line in stale.stdout.strip().splitlines():
            print(f"  superseded branch commit: {line}")
    toolroom.git.opts(cwd=root)("switch", "-C", name)


def _loop_fm(root: Path, *args: str, timeout: float = 900.0) -> None:
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
        fail(
            f"the loop's `{footman.prog()} {' '.join(args)}` exited"
            f" {result.code}:\n{result.stdout}{result.stderr}"
        )


def _ensure_member(root: Path) -> None:
    """Give the loop one member package, landed through its own gate.

    ``new.package`` renders and wires it, a `[release] baseline`
    seeds the first release's number, and the loop's own
    ``fm submit --armed`` lands it: the branch, the pull request,
    the gate on the runner, and the merge, all on the dev wheels.
    Idempotent: an existing member skips everything.
    """
    from livery.workshop._git_ops import GitOps

    # Landed means on main: a failed pass leaves the working tree on
    # the feature branch with the member present, and judging that
    # tree would skip straight to the release act with nothing
    # merged. The alignment makes the glob read main's truth.
    _align_main(root)
    if list(root.glob("packages/*/workshop.toml")):
        print("  member: already landed")
        return
    git = GitOps(root)
    _fresh_branch(root, "feat/loop-echo")
    _loop_fm(root, "new.package", "loop-echo")
    member = root / "packages" / "loop-echo" / "workshop.toml"
    body = member.read_text("utf-8")
    if "[release]" not in body:
        member.write_text(
            body.rstrip("\n")
            + "\n\n[release]\n# The first release lands at the baseline.\n"
            + 'baseline = "0.1.0"\n',
            "utf-8",
        )
    # The baseline is a render input: cliff.toml was rendered before
    # the append, so it must settle again or the gate names it as
    # drift.
    _loop_fm(root, "template.apply")
    git.commit_all(
        "feat(loop-echo): the loop's one member\n\nBorn through"
        " new.package on the dev wheels, with the release baseline"
        " seeded so the first release lands at 0.1.0."
    )
    # Force for the same reason the setup branch pushes force: the
    # branch is pass-owned, rebuilt from main every time, so the
    # remote's copy is always superseded.
    _loop_fm(root, "submit", "--force", "--armed")
    _align_main(root)
    print("  member: landed through the loop's own gate")


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

    tag = "packages/loop-echo/v0.1.0"
    listed = toolroom.git.opts(cwd=root, nofail=True)(
        "ls-remote", "--tags", "origin", tag
    )
    if listed.code == 0 and tag in listed.stdout:
        print(f"  release: receipt {tag} already on the loop")
        return
    _align_main(root)
    _loop_fm(root, "workflow.release", "loop-echo", "--armed", timeout=1800.0)
    _, token = _dev_forge(kind)
    registry = SimpleRegistry(
        f"{ALIAS_URL}/api/packages/{E2E_OWNER}/pypi/simple", token=token
    )
    import time

    deadline = time.monotonic() + 300
    while "0.1.0" not in registry.versions("livery-loop-echo"):
        if time.monotonic() >= deadline:
            fail(
                "the wave reported done but the registry never served"
                " livery-loop-echo 0.1.0"
            )
        time.sleep(5)
    listed = toolroom.git.opts(cwd=root, nofail=True)(
        "ls-remote", "--tags", "origin", tag
    )
    if tag not in listed.stdout:
        fail(f"served, but the receipt tag {tag} is not on the loop")
    print(f"  release: livery-loop-echo 0.1.0 served, receipt {tag} cut")


def _merge_setup(kind: str, sha: str) -> None:
    """Merge the setup pull request when its green head is *sha*.

    Birth's own done-line prescribes it: merging the setup PR proves
    the gate and the protection wiring end to end. One retry after a
    pause, because the forge recomputes mergeability after a status
    lands and answers "try again later" in the window (measured).
    Already merged, or a head that moved on, is a quiet skip.
    """
    import time

    from livery.forge import ForgeError
    from livery.workshop._new_project import _SETUP_BRANCH

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
    deadline = time.monotonic() + 90
    while True:
        try:
            repo.pr.merge_now(pr.number, title=pr.title)
            break
        except ForgeError as error:
            # The forge recomputes mergeability after a status lands
            # and answers "try again later" through the window, which
            # can outlast one polite retry (measured at over ten
            # seconds). Only that class retries; anything else is a
            # real refusal and surfaces verbatim.
            if "try again later" not in str(error).lower():
                raise
            if time.monotonic() >= deadline:
                fail(
                    f"the forge kept answering 'try again later' for"
                    f" 90s on merging setup PR #{pr.number}"
                )
            time.sleep(5)
    print(f"  setup PR #{pr.number}: merged; the gate is proven")


if _WORKSHOP_TESTS.is_dir():
    # serial: the driver births into and pushes from its own
    # directories, so it owns the process globals for the run.
    @ci.task(name="e2e", serial=True)
    def e2e(forge: str = "gitea") -> None:
        """Exercise the CI and release story on the local forge.

        Births or resumes the loop's workspace through
        ``fm new.project`` (repository, protection, setup pull
        request), provisions the secrets, wires the workspace to the
        workshop's own dev wheels, and follows the pushed head's
        workflow runs to their verdicts on the real runner. Re-running
        is the recovery procedure at every step. The release act into
        the local registry follows in this phase; the verb always says
        exactly what it covers.
        """
        import os

        url = os.environ.get("GITEA_URL", "")
        _require_host_alias()
        _, lane_token = _dev_forge(forge)
        _publish_dev_wheels(forge)
        root = _loop_home() / E2E_REPO
        if (root / ".git").is_dir():
            # A resumed birth pushes before it returns, so an
            # existing workspace authenticates first; birth resets
            # the remote, so it authenticates again after. Main then
            # fast-forwards onto the merges the loop itself made:
            # without the reconcile, birth's foreign-repo guard reads
            # our own squash as a stranger's history and refuses.

            _authenticate_remote(root, lane_token)
            _align_main(root)
        root = _birth(forge, url)
        _authenticate_remote(root, lane_token)
        provision(forge)
        sha = _eat_dev_wheels(root)
        print(f"  watching {sha[:12]} on the runner")
        _watch(forge, url, sha)
        print("  green: the loop's gate ran on the real runner")
        _merge_setup(forge, sha)
        _ensure_member(root)
        _release_act(root, forge)
        print("  the loop is whole: gate, merge, release, receipt")
