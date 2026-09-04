"""``workflow.release``: the release train's driver on the engine.

One release shape for a set of N >= 1 packages: derive each member's
version and entry through its ``cliff.toml``, bump floors within the
set only, stamp, commit per member in dependency order, and hand the
branch to the shared engine. The branch decides the act: main-family
(``main`` and the engine's ``workflow/`` namespace) runs the real
train, any other branch is the dev act, a wheel straight from the
branch. ``--local`` is everything that stays on this machine:
derive, build, validate, report, then roll the stamps back like a
failed prepare, leaving ``dist/`` and the report.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import footman
import toolroom
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
    here has nothing to undo. A member whose bump equals its newest
    released tag has nothing unreleased, and minting a number the
    index already has is refused with the options. The tag is the
    judge, never the stamped version: a stamp can land without its
    release cutting, and judging by it would strand that release
    (the same rule livery.workshop._release.prepare_release keeps).
    """
    from livery.workshop._release import _last_released

    plans: list[MemberPlan] = []
    unchanged: list[str] = []
    for package in members:
        released = _last_released(root, package)
        derived = _cliff.bumped_version(root, package)
        if not derived or derived == released:
            unchanged.append(package.directory.name)
            continue
        plans.append(MemberPlan(package=package, version=derived))
    if unchanged:
        names = ", ".join(unchanged)
        fail(
            f"nothing unreleased touches: {names}. Options: drop them from"
            " the set (release the rest now); or, if a hollow re-release is"
            " truly meant, stamp a version explicitly with"
            f" `{footman.prog()} release.prepare <path> <version>`."
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
        for home in ("pyproject.toml", "workshop.toml"):
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
    paths: list[str] = []
    for package in members:
        base = package.directory.relative_to(root)
        paths.extend(
            str(base / name)
            for name in ("CHANGELOG.md", "pyproject.toml", "workshop.toml")
        )
        src = package.directory / "src"
        if src.is_dir():
            paths.extend(str(p.relative_to(root)) for p in src.rglob("__init__.py"))
    toolroom.git.opts(cwd=root, nofail=True, recorded=False)("checkout", "--", *paths)


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
    names what each leg resolved per co-member and sibling. The
    caller builds every set member's wheel first: the legs'
    find-links name each sibling's ``dist/``, so a later member's
    wheel must exist before an earlier member's floor leg resolves.
    """
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
    better.

    Zero reported contexts after minutes means run creation itself
    failed, and every forge can do it: Gitea's actions queue could
    wedge before 1.27.3, GitHub silently drops a push's workflow-run
    creation under load, and GitLab can never mint the pipeline (a
    job-queue backlog, or workflow rules evaluating to nothing). The
    combined-status read
    reports all three identically, so detection is uniform; the safe
    remedy is not (whether a tool may push to the base varies per
    forge and protection), so the silent tip gets a printed teaching
    naming the fresh-push-event remedy, never an automatic push. The
    timeout tells the two conditions apart: a run that reported and
    hung sends the reader to the runners, a tip that never got a run
    names run creation.
    """
    if force:
        print("  base verification skipped (--force-unverified-base)")
        return
    deadline = time.monotonic() + timeout
    announced = False
    silent_since: float | None = None
    nudged = False
    ever_reported = False
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
                f" Fix {base} first (`{footman.prog()} ci.logs` on it names"
                " the job), or"
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
            ever_reported = True
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
            # Not started and hanging are different problems with
            # different remedies, so the timeout names the one that
            # actually happened.
            if ever_reported:
                fail(
                    f"{base} reported a run but never went green within"
                    f" {timeout / 60:.0f} minutes. Check the runners"
                    f" (`{footman.prog()} status --watch` on a branch follows"
                    " a run), or"
                    " --force-unverified-base."
                )
            fail(
                f"{base}'s tip never got a CI run in {timeout / 60:.0f}"
                " minutes: run creation itself failed, and waiting cannot"
                " fix that. A fresh push event mints a run (an empty"
                " commit, or its PR merged), or --force-unverified-base."
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
                prepare_release(self._root, plan.package.path, plan.version)
                _python.build(plan.package, self._root)
                git.commit_all(f"chore(release): {plan.package.name} v{plan.version}")
            # Every member's wheel exists before any leg runs; a
            # failed leg still tears the whole branch down, commits
            # included, so nothing unvalidated survives.
            for plan in plans:
                validate_member(self._root, plan, release_dirs)
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
        """The title and body the branch's changelogs state.

        Recovery reads the branch's committed changelogs, never the
        working tree and never commit subjects: a checkout standing
        elsewhere holds a different tree, a rider commit is not part
        of what was prepared, and a hand-edited entry keeps its
        member's heading, so the rebuilt title stays consistent with
        the content the squash will carry.
        """
        from livery.workshop._publish import changelog_version

        git = self._git
        mined_at = git._run("merge-base", "HEAD", f"origin/{self.base}").strip()
        pairs: list[tuple[str, str]] = []
        for path in git._run("diff", "--name-only", mined_at, "HEAD").splitlines():
            parts = path.split("/")
            if (
                len(parts) == 3
                and parts[0] == "packages"
                and parts[2] == "CHANGELOG.md"
            ):
                version = changelog_version(git.file_at("HEAD", path))
                contract = git.file_at("HEAD", f"packages/{parts[1]}/workshop.toml")
                name = ""
                for line in contract.splitlines():
                    if line.startswith("name = "):
                        name = line.split("=", 1)[1].strip().strip('"')
                        break
                if name and version:
                    pairs.append((name, version))
        listed = ", ".join(f"{name} v{version}" for name, version in pairs)
        body = (
            "## Release summary\n"
            + "\n".join(f"- **{name}** v{version}" for name, version in pairs)
            + f"\n\nMined-At: {mined_at}"
        )
        return Submission(title=f"chore(release): released {listed}", body=body)

    def on_merged(self) -> None:
        """Publishing is the merge-triggered workflow's act."""
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
            _python.build(plan.package, root)
        for plan in plans:
            validate_member(root, plan, release_dirs)
        listed = ", ".join(f"{plan.package.name} v{plan.version}" for plan in plans)
        print(f"  would release: {listed}")
        print("  wheels in each member's dist/; the tree is restored")
    finally:
        rollback_prepare(root, members)


release_group = workflow.group("release", help="The release train")


@release_group.default(interactive=True)
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
    """Release a set of packages: the branch decides the act.

    Names the set positionally (``packages/forge`` or just ``forge``).
    On a main-family branch this is the release train: one PR,
    receipts per member, re-running the recovery at every step. On
    any other branch it is the dev act: a wheel straight from the
    branch at a dev version, published only to the configured custom
    index after a confirmation, no reserved branch, no PR, no tags.
    ``--local`` is everything that stays on this machine, on either
    branch mode.
    """
    from livery.workshop._dev_release import dev_release
    from livery.workshop._forge_lane import this_repository
    from livery.workshop._layers import workspace_root

    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    if not paths:
        fail(
            f"name the set: `{footman.prog()} workflow.release forge` (one package) or"
            f" `{footman.prog()} workflow.release forge workshop` (an atomic set)"
        )
    members = resolve_set(root, tuple(paths))
    git = GitOps(root)
    branch = git.current_branch()
    if not branch:
        fail(
            "HEAD is detached, and the branch decides the act. Check out a"
            " branch (main for the release train, any feature branch for a"
            " dev build), then run this again."
        )
    if branch != "main" and not branch.startswith("workflow/"):
        dev_release(root, git, members, local=local)
        return
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


@release_group.task(name="check-title", hidden=True)
def workflow_release_check_title(
    title: Annotated[str, doc("the PR title CI observed")] = "",
) -> None:
    """Verify a release PR's title against the members' changelogs.

    The publish wave discovers a release from the squash's changed
    changelogs; the title is presentation. This job keeps the two
    consistent, so a title that no longer names what the changelogs
    prepared is refused here, in a non-required CI job, before it
    can mislead a reader. Not a required context: a red here wants a
    human look, never a parked merge.
    """
    from livery.workshop._layers import workspace_root

    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    from livery.workshop._publish import changelog_version

    git = GitOps(root)
    branch = git.current_branch()
    base = git._run("merge-base", "HEAD", "origin/main").strip()
    pairs: list[str] = []
    for path in git._run("diff", "--name-only", base, "HEAD").splitlines():
        parts = path.split("/")
        if len(parts) == 3 and parts[0] == "packages" and parts[2] == "CHANGELOG.md":
            version = changelog_version(git.file_at("HEAD", path))
            contract = git.file_at("HEAD", f"packages/{parts[1]}/workshop.toml")
            for line in contract.splitlines():
                if line.startswith("name = "):
                    name = line.split("=", 1)[1].strip().strip('"')
                    if name and version:
                        pairs.append(f"{name} v{version}")
                    break
    if not pairs:
        print(f"  {branch}: no changelog changes; nothing to check")
        return
    expected = "chore(release): released " + ", ".join(pairs)
    if title and title != expected:
        fail(
            f"the PR title does not match what the changelogs prepared:\n"
            f"    title:    {title}\n    prepared: {expected}\n"
            "  The title is presentation rebuilt from the branch's"
            " changelogs; restore it or re-run workflow.release."
        )
    print(f"  title matches the prepared release: {expected}")


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
        fail("no workspace: no workshop.toml above the working directory")
    git = GitOps(root)
    registry = SimpleRegistry(
        os.environ.get("PYTHON_REGISTRY_URL", "https://pypi.org/simple")
    )
    receipts = publish_release(
        root,
        git,
        lambda _package: registry,
        ref=ref,
        index_url=os.environ.get("PYTHON_PUBLISH_INDEX", ""),
        token=os.environ.get("UV_PUBLISH_TOKEN", ""),
    )
    output = os.environ.get("GITHUB_OUTPUT", "")
    if output:
        names = " ".join(receipt.package.name for receipt in receipts)
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(f"members={names}\n")
