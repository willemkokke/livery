"""``workflow.release``: the release train's driver on the engine.

One release shape for a set of N >= 1 packages: derive each member's
version and entry through its ``cliff.toml``, bump floors within the
set only, stamp, commit per member in dependency order, and hand the
branch to the shared engine. The branch decides the act: main-family
runs the real train, any other branch is the dev act (a later
phase). ``--local`` is everything that stays on this machine:
derive, build, validate, report, then roll the stamps back like a
failed prepare, leaving ``dist/`` and the report.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from footman import doc, fail

from livery.forge import ForgeError, Repository
from livery.workshop import _cliff
from livery.workshop._backends import _python
from livery.workshop._git_ops import GitOps
from livery.workshop._graph import order_topologically
from livery.workshop._packages import Package, discover_packages
from livery.workshop._release import prepare_release
from livery.workshop._workflow_engine import Submission, run_workflow
from livery.workshop._workflow_state import WorkflowKind
from livery.workshop._workflow_tasks import workflow

#: The base gate's patience and cadence.
BASE_TIMEOUT = 30 * 60
BASE_POLL = 15
#: A tip with zero reported contexts this long gets one nudge: run
#: creation itself failed, and patience cannot fix that.
SILENT_NUDGE_AFTER = 3 * 60


@dataclass(frozen=True)
class MemberPlan:
    """One member's derived release: the package and its next version."""

    package: Package
    version: str


def release_name(members: tuple[str, ...]) -> str:
    """The workflow name a set earns: sorted directories, ``+``-joined."""
    return "release/" + "+".join(sorted(members))


def resolve_set(root: Path, paths: tuple[str, ...]) -> tuple[Package, ...]:
    """The named packages, in dependency order; every name must exist."""
    packages = {p.path: p for p in discover_packages(root)}
    chosen: list[Package] = []
    for path in paths:
        package = packages.get(path) or packages.get(f"packages/{path}")
        if package is None:
            known = ", ".join(sorted(packages))
            fail(f"{path} is not a workspace package; members: {known}")
        chosen.append(package)
    return order_topologically(tuple(chosen))


def derive_plans(root: Path, members: tuple[Package, ...]) -> tuple[MemberPlan, ...]:
    """Each member's next version, every member refused when unchanged.

    Runs before the branch is cut, from read-only state: a refusal
    here has nothing to undo. A member whose bump equals its current
    version has nothing unreleased, and minting a number the index
    already has is refused with the options.
    """
    plans: list[MemberPlan] = []
    unchanged: list[str] = []
    for package in members:
        current = _python.current_version(package)
        derived = _cliff.bumped_version(root, package)
        if not derived or derived == current:
            unchanged.append(package.directory.name)
            continue
        plans.append(MemberPlan(package=package, version=derived))
    if unchanged:
        names = ", ".join(unchanged)
        fail(
            f"nothing unreleased touches: {names}. Options: drop them from"
            " the set (release the rest now); or, if a hollow re-release is"
            " truly meant, stamp a version explicitly with"
            " `fm release.prepare <path> <version>`."
        )
    return tuple(plans)


def bump_set_floors(root: Path, plans: tuple[MemberPlan, ...]) -> list[str]:
    """Raise floors between co-released members; the files changed.

    Within the set only (contract: a solo release never moves a
    floor): a member depending on a co-member gets the co-released
    version, in both homes, the pyproject requirement and the
    ``[[depends]]`` floor.
    """
    import re

    versions = {plan.package.name: plan.version for plan in plans}
    dirs = {plan.package.name: plan.package.directory for plan in plans}
    changed: list[str] = []
    for plan in plans:
        for home in ("pyproject.toml", "livery.toml"):
            path = plan.package.directory / home
            if not path.is_file():
                continue
            text = original = path.read_text("utf-8")
            for name, version in versions.items():
                if name == plan.package.name:
                    continue
                text = re.sub(
                    rf'("{re.escape(name)}\s*>=\s*)[0-9][^"]*(")',
                    rf"\g<1>{version}\g<2>",
                    text,
                )
                directory = re.escape(dirs[name].name)
                text = re.sub(
                    rf'(path = "packages/{directory}"'
                    rf'[^[]*?floor = ")[^"]+(")',
                    rf"\g<1>{version}\g<2>",
                    text,
                    flags=re.S,
                )
            if text != original:
                path.write_text(text, encoding="utf-8")
                changed.append(str(path.relative_to(root)))
    return changed


def rollback_prepare(root: Path, members: tuple[Package, ...]) -> None:
    """Restore exactly the files prepare writes; never raises.

    The error being unwound is the one worth seeing, so this cleans
    quietly: each member's changelog, pyproject, contract, and
    version files return to HEAD.
    """
    import subprocess

    paths: list[str] = []
    for package in members:
        base = package.directory.relative_to(root)
        paths.extend(
            str(base / name)
            for name in ("CHANGELOG.md", "pyproject.toml", "livery.toml")
        )
        src = package.directory / "src"
        if src.is_dir():
            paths.extend(str(p.relative_to(root)) for p in src.rglob("__init__.py"))
    subprocess.run(
        ["git", "checkout", "--", *paths],
        cwd=root,
        capture_output=True,
        check=False,
    )


def validate_member(
    root: Path,
    plan: MemberPlan,
    release_dirs: tuple[Path, ...],
    *,
    legs: tuple[str, ...] = ("lowest-direct", "highest"),
) -> None:
    """Build and run the isolated legs for one member, reporting each.

    The floor leg first (a floor lying about compatibility fails
    before anything else is spent), then the latest leg. The report
    names what each leg resolved per co-member and sibling.
    """
    _python.build(plan.package, root)
    for leg in legs:
        resolved = _python.run_isolated_test(
            plan.package, root, release_dirs=release_dirs, resolution=leg
        )
        siblings = {
            name: version
            for name, version in sorted(resolved.items())
            if name.startswith("livery-") and name != plan.package.name
        }
        listed = ", ".join(f"{n} {v}" for n, v in siblings.items()) or "no siblings"
        label = "floor leg" if leg == "lowest-direct" else "latest leg"
        print(f"  {plan.package.name} {label}: {listed}")


def require_verified_base(
    repo: Repository,
    git: GitOps,
    base: str,
    *,
    force: bool = False,
    timeout: float = BASE_TIMEOUT,
    poll: float = BASE_POLL,
) -> None:
    """Wait for the base's own push CI to be green; stop on red or skip.

    Branch protection only ever judged PR heads, and a release
    publishes the base's tree, so the base's push run is the only
    vouching there is. Green, not merely not-red; red is a verdict
    waiting cannot improve; a skip-ci tip will never get a run; an
    unreachable forge fails open, the engine's UNKNOWN story says it
    better. A tip with zero reported contexts gets one nudge, an
    empty commit minting a fresh push event, refused harmlessly where
    protection forbids it.
    """
    if force:
        print("  base verification skipped (--force-unverified-base)")
        return
    deadline = time.monotonic() + timeout
    announced = False
    silent_since: float | None = None
    nudged = False
    while True:
        try:
            git.fetch()
            sha = git.remote_head(base)
            status = repo.checks.status(sha) if sha else None
        except (ForgeError, Exception):
            return
        if status is None:
            return
        if status.state == "success":
            return
        if status.state == "failure":
            fail(
                f"{base}'s own CI is red, and a release publishes that tree."
                f" Fix {base} first (`fm ci.logs` on it names the job), or"
                " release with --force-unverified-base if you accept an"
                " unvouched tree."
            )
        if "[skip ci]" in git.commit_message(sha):
            fail(
                f"{base}'s tip carries a skip-ci marker, so CI will never"
                " report on it. Push a commit CI can run, or"
                " --force-unverified-base to accept it unverified."
            )
        now = time.monotonic()
        if status.contexts:
            silent_since = None
        elif silent_since is None:
            silent_since = now
        elif not nudged and now - silent_since >= SILENT_NUDGE_AFTER:
            nudged = True
            print(
                f"  {base}'s tip has had no CI run for"
                f" {SILENT_NUDGE_AFTER // 60} min; a fresh push event may be"
                " needed (an empty commit mints one)."
            )
        if now >= deadline:
            fail(
                f"{base} did not report green within {timeout / 60:.0f}"
                " minutes. Check the runners (`fm status --watch` on a"
                " branch follows a run), or --force-unverified-base."
            )
        if not announced:
            announced = True
            print(
                f"  waiting for {base}'s own CI before releasing (up to"
                f" {BASE_TIMEOUT // 60} min)"
            )
        time.sleep(poll)


class ReleaseDriver:
    """The release kind's driver for the shared engine."""

    kind = WorkflowKind.RELEASE

    def __init__(
        self,
        root: Path,
        repo: Repository,
        git: GitOps,
        members: tuple[Package, ...],
        *,
        armed: bool,
        force_unverified_base: bool = False,
    ) -> None:
        self._root = root
        self._repo = repo
        self._git = git
        self._members = members
        self.members = tuple(p.directory.name for p in members)
        self.name = release_name(self.members)
        self.armed = armed
        self._force = force_unverified_base

    @property
    def branch(self) -> str:
        return f"workflow/{self.name}"

    @property
    def base(self) -> str:
        return "main"

    def prepare(self) -> Submission | None:
        """Derive, stamp, and commit the set; recover an existing branch.

        A branch already alive locally or on the remote is recovery:
        nothing is rebuilt, the engine re-submits what the branch
        holds, and titles come from the ref, never the working tree.
        """
        git = self._git
        if git.local_branch_exists(self.branch) or self._repo.branch_exists(
            self.branch
        ):
            if git.current_branch() != self.branch:
                if not git.local_branch_exists(self.branch):
                    git.fetch()
                    git._run("checkout", "-b", self.branch, f"origin/{self.branch}")
                else:
                    git.switch(self.branch)
            print(f"  recovering the prepared {self.name} from its branch")
            return self._submission_from_ref()

        require_verified_base(self._repo, git, self.base, force=self._force)
        plans = derive_plans(self._root, self._members)
        mined_at = git.head_sha()
        git.create_branch(self.branch)
        prepared = False
        try:
            floor_changes = bump_set_floors(self._root, plans)
            if floor_changes:
                print(f"  floors raised within the set: {', '.join(floor_changes)}")
            release_dirs = tuple(plan.package.directory / "dist" for plan in plans)
            for plan in plans:
                changed = prepare_release(self._root, plan.package.path, plan.version)
                _ = changed
                validate_member(self._root, plan, release_dirs)
                git.commit_all(f"chore(release): {plan.package.name} v{plan.version}")
            prepared = True
        finally:
            if not prepared:
                rollback_prepare(self._root, self._members)
                git.switch(self.base)
                git.delete_local_branch(self.branch)
        listed = ", ".join(f"{plan.package.name} v{plan.version}" for plan in plans)
        title = f"chore(release): released {listed}"
        body = (
            "## Release summary\n"
            + "\n".join(f"- **{plan.package.name}** v{plan.version}" for plan in plans)
            + f"\n\nMined-At: {mined_at}"
        )
        return Submission(title=title, body=body)

    def _submission_from_ref(self) -> Submission:
        """The title and body the branch's own commits state.

        Recovery reads the ref, never the working tree: a checkout
        standing elsewhere holds a different tree, and the member
        commits are the record of what was prepared.
        """
        stamped: list[str] = []
        for subject in reversed(self._git.subjects_ahead(self.base)):
            _, _, rest = subject.partition("chore(release): ")
            if rest:
                stamped.append(rest)
        listed = ", ".join(stamped)
        mined_at = self._git._run("merge-base", "HEAD", f"origin/{self.base}").strip()
        body = (
            "## Release summary\n"
            + "\n".join(
                f"- **{entry.split(' v')[0]}** v{entry.split(' v')[1]}"
                for entry in stamped
                if " v" in entry
            )
            + f"\n\nMined-At: {mined_at}"
        )
        return Submission(title=f"chore(release): released {listed}", body=body)

    def on_merged(self) -> None:
        """Publishing is the merge-triggered workflow's act (phase 4)."""
        print(
            "  merged; the publish workflow takes it from here and the"
            " receipt tags say when each member is done"
        )


def local_release(root: Path, members: tuple[Package, ...]) -> None:
    """Everything that stays on this machine, then the stamps roll back.

    Derive, stamp (so the built wheels carry the would-be versions),
    build, validate both legs, print the would-be release, restore
    the tree; ``dist/`` and the report remain. Publishing consent is
    never asked because nothing leaves the machine.
    """
    plans = derive_plans(root, members)
    try:
        bump_set_floors(root, plans)
        release_dirs = tuple(plan.package.directory / "dist" for plan in plans)
        for plan in plans:
            prepare_release(root, plan.package.path, plan.version)
            validate_member(root, plan, release_dirs)
        listed = ", ".join(f"{plan.package.name} v{plan.version}" for plan in plans)
        print(f"  would release: {listed}")
        print("  wheels in each member's dist/; the tree is restored")
    finally:
        rollback_prepare(root, members)


release_group = workflow.group("release", help="The release train")


@release_group.default
def workflow_release(
    *paths: str,
    armed: Annotated[bool, doc("arm the release PR to merge on green")] = False,
    local: Annotated[
        bool, doc("derive, build, validate, report; nothing leaves the machine")
    ] = False,
    force_unverified_base: Annotated[
        bool, doc("release without waiting for the base's own CI")
    ] = False,
) -> None:
    """Release a set of packages: one PR, receipts per member.

    Names the set positionally (``packages/forge`` or just ``forge``);
    the branch decides the act, and this driver is the main-family
    one. Re-running is the recovery at every step.
    """
    from livery.workshop._forge_lane import this_repository
    from livery.workshop._layers import workspace_root

    root = workspace_root()
    if root is None:
        fail("no workspace: no livery.toml above the working directory")
    if not paths:
        fail(
            "name the set: `fm workflow.release forge` (one package) or"
            " `fm workflow.release forge workshop` (an atomic set)"
        )
    members = resolve_set(root, tuple(paths))
    git = GitOps(root)
    if local:
        local_release(root, members)
        return
    repo = this_repository(root)
    driver = ReleaseDriver(
        root,
        repo,
        git,
        members,
        armed=armed,
        force_unverified_base=force_unverified_base,
    )
    run_workflow(driver, repo, git)


@release_group.task(name="publish", hidden=True)
def workflow_release_publish(
    ref: Annotated[str, doc("the release squash; empty means HEAD")] = "",
) -> None:
    """Publish the squash at --ref: the wave, receipts cut per member.

    The CI entry point after a release PR merges, and the recovery
    entry when a publish died mid-wave: everything already tagged is
    walked past. ``--ref`` exists because HEAD usually moves past the
    squash before a recovery runs.
    """
    import os

    from livery.forge import SimpleRegistry
    from livery.workshop._layers import workspace_root
    from livery.workshop._publish import publish_release

    root = workspace_root()
    if root is None:
        fail("no workspace: no livery.toml above the working directory")
    git = GitOps(root)
    registry = SimpleRegistry(
        os.environ.get("LIVERY_REGISTRY_URL", "https://pypi.org/simple")
    )
    receipts = publish_release(
        root,
        git,
        lambda _package: registry,
        ref=ref,
        index_url=os.environ.get("LIVERY_PUBLISH_INDEX", ""),
        token=os.environ.get("UV_PUBLISH_TOKEN", ""),
    )
    output = os.environ.get("GITHUB_OUTPUT", "")
    if output:
        names = " ".join(receipt.package.name for receipt in receipts)
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(f"members={names}\n")
