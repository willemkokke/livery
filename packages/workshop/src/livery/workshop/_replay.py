"""The released-wheels replay: a member's published wheel against its own tests.

The pairing under test is the one a consumer gets: the wheel the
index serves, installed into a plain virtual environment beside the
tests recorded for that same version, so the tree is checked out at
the release tag, never at main, and the package must import from
site-packages, never from the workspace. A red replay means a
dependency release broke the installed configuration or the index
artifact drifted; the failure path files or extends the marker
issue through the forge, searching for the marker first, so a red
night is one issue with one comment per failure, never a pile.

Reach for [livery.workshop._replay.replay_flow][]; `fm release.replay`
is its verb, and the nightly point schedules it through the
contract's ``[[ci.schedule]]`` seam.
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from livery.footman import fail

if TYPE_CHECKING:
    from livery.forge import Repository
    from livery.workshop._git_ops import GitOps
    from livery.workshop._packages import Package

#: The issue the failure path files or extends; the search key.
MARKER = "nightly: released-wheels replay failed"

_RELEASE_RE = re.compile(r"^\d+\.\d+\.\d+$")


class Registry(Protocol):
    """The one probe the replay needs; livery.forge.SimpleRegistry fits."""

    def versions(self, name: str) -> tuple[str, ...]:
        """The published versions of *name*."""
        ...


@dataclass(frozen=True)
class Replay:
    """What one replay is about.

    Attributes:
        member: The member's directory name under ``packages/``.
        dist: Its distribution name.
        version: The released version under test.
        extras: The extras to install with it, comma-joined; empty for none.
    """

    member: str
    dist: str
    version: str
    extras: str = ""

    @property
    def tag(self) -> str:
        """The receipt tag the tree is checked out at."""
        return f"packages/{self.member}/v{self.version}"

    @property
    def requirement(self) -> str:
        """The exact requirement the plain environment installs."""
        extras = f"[{self.extras}]" if self.extras else ""
        return f"{self.dist}{extras}=={self.version}"


def latest_release(registry: Registry, dist: str) -> str:
    """The newest final release the index serves for *dist*; refuses when none.

    Only ``x.y.z`` versions count: a dev or pre-release is never the
    consumer's pairing.
    """
    finals = [v for v in registry.versions(dist) if _RELEASE_RE.match(v)]
    if not finals:
        fail(f"the index serves no released version of {dist}: nothing to replay")
    return max(finals, key=lambda v: tuple(int(part) for part in v.split(".")))


def import_name(src: Path) -> str:
    """The module a member's ``src`` tree exposes: ``<namespace>.<name>`` or ``<name>``.

    Refuses when no package directory is found, naming the tree.
    """
    for first in sorted(p for p in src.iterdir() if p.is_dir()):
        if (first / "__init__.py").is_file():
            return first.name
        for second in sorted(p for p in first.iterdir() if p.is_dir()):
            if (second / "__init__.py").is_file():
                return f"{first.name}.{second.name}"
    fail(f"{src} holds no package directory: nothing to import")


def run_url() -> str:
    """The run's page from the runner's environment; empty outside CI."""
    server = os.environ.get("GITHUB_SERVER_URL", "")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if server and repository and run_id:
        return f"{server}/{repository}/actions/runs/{run_id}"
    return ""


def report_failure(repo: Repository, replay: Replay, *, url: str) -> str:
    """File or extend the marker issue; the line saying which, or the refusal."""
    from livery.forge import ForgeError

    evidence = f"{replay.requirement} failed its replay" + (f": {url}" if url else "")
    try:
        existing = repo.issue.search(MARKER)
        if existing:
            repo.issue.comment(existing[0].number, f"Failed again: {evidence}")
            return f"  commented on #{existing[0].number}: {evidence}"
        created = repo.issue.create(
            MARKER,
            body=(
                "The released-wheels replay is red: the wheel on the index no"
                " longer passes its own recorded tests in the consumer"
                f" configuration.\n\n{evidence}\n\nThe replay installs the"
                " released wheel into a plain virtual environment and runs the"
                " member's tests at the release tag. A red replay means a"
                " dependency release broke the installed configuration, or the"
                " index artifact drifted; the run's logs carry the failing"
                " tests."
            ),
        )
        return f"  filed #{created.number}: {MARKER}"
    except ForgeError as error:
        return f"  the forge refused the issue: {error}"


def install(venv: Path, python: str, requirement: str, index: str) -> int:
    """Create the plain environment and install *requirement* into it; the exit code.

    ``uv`` makes the environment and installs, but the environment is
    a plain one outside the workspace: no lock, no editable members,
    so the package can only come from the index.
    """
    from livery import toolroom

    made = toolroom.uv.opts(nofail=True)("venv", str(venv), "--python", python)
    if made.code != 0:
        return made.code
    args = ["pip", "install", "--python", str(venv / "bin" / "python")]
    if index:
        args += ["--index", index]
    args += [requirement, "pytest", "pytest-xdist"]
    return toolroom.uv.opts(nofail=True)(*args).code


def run_tests(venv: Path, tree: Path, member: str, module: str) -> int:
    """Prove the import comes from site-packages, then run the member's tests."""
    from livery.footman import run

    python = str(venv / "bin" / "python")
    probe = run(
        [
            python,
            "-c",
            f"import {module} as m, pathlib; p = pathlib.Path(m.__file__);"
            " assert 'site-packages' in str(p), p; print('testing', p)",
        ],
        cwd=tree,
        nofail=True,
    )
    if probe.code != 0:
        print(
            f"  {module} does not import from site-packages; the replay proves nothing"
        )
        return probe.code
    return run(
        [python, "-m", "pytest", f"packages/{member}/tests", "-p", "no:cacheprovider"],
        cwd=tree,
        nofail=True,
    ).code


def replay_flow(
    root: Path,
    git: GitOps,
    package: Package,
    *,
    python: str,
    registry: Registry,
    index: str,
    extras: str = "",
    repo: Repository | None = None,
    installer: Callable[[Path, str, str, str], int] = install,
    tester: Callable[[Path, Path, str, str], int] = run_tests,
) -> None:
    """Replay *package*'s latest release on *python*; red files the issue and fails.

    The release is the newest final version *registry* serves; the
    tree is a temporary worktree at its receipt tag, refused when
    the checkout lacks the tag. *installer* and *tester* are the two
    heavy steps, injectable so the orchestration is testable without
    an index or an interpreter. With *repo*, a red replay files or
    extends the marker issue before failing.
    """
    from livery import toolroom

    member = package.directory.name
    replay = Replay(
        member, package.name, latest_release(registry, package.name), extras
    )
    if replay.tag not in git.tags():
        fail(
            f"the checkout carries no tag {replay.tag}: fetch the tags"
            " (git fetch --tags), or the release is not this repository's"
        )
    print(f"  replaying {replay.requirement} at {replay.tag} on python {python}")
    scratch = Path(tempfile.mkdtemp(prefix="fm-replay-"))
    tree = scratch / "tree"
    added = toolroom.git.opts(cwd=root, nofail=True)(
        "worktree", "add", "--detach", str(tree), replay.tag
    )
    if added.code != 0:
        fail(f"git worktree add at {replay.tag} exited {added.code}:\n{added.stderr}")
    try:
        module = import_name(tree / "packages" / member / "src")
        code = installer(scratch / ".replay", python, replay.requirement, index)
        if code == 0:
            code = tester(scratch / ".replay", tree, member, module)
    finally:
        toolroom.git.opts(cwd=root, nofail=True)(
            "worktree", "remove", "--force", str(tree)
        )
    if code == 0:
        print(f"  green: {replay.requirement} passes its own tests from site-packages")
        return
    if repo is not None:
        print(report_failure(repo, replay, url=run_url()))
    fail(f"the replay of {replay.requirement} is red (exit {code})")
