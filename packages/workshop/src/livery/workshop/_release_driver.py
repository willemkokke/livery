"""``workflow.release``: the release train's driver on the engine.

One release shape for a set of N >= 1 packages: derive each member's
version from its commits and its entry through the release notes'
provider, bump floors within the set only, stamp, commit per member in
dependency order, and hand the branch to the shared engine. The branch
decides the act: main-family (``main`` and the engine's ``workflow/``
namespace) runs the real train, any other branch is the dev act, a wheel
straight from the branch. ``--local`` is everything that stays on this
machine: derive, build, validate, report, then roll the stamps back like
a failed prepare, leaving ``dist/`` and the report.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Annotated

import livery.footman as footman
import livery.toolroom.tools as tools
from livery.footman import doc, fail
from livery.forge import ForgeError, Repository, Run
from livery.workshop._backends import _python, backend_for
from livery.workshop._git_ops import GitError, GitOps
from livery.workshop._graph import order_topologically
from livery.workshop._packages import Package, discover_packages, receipt_member
from livery.workshop._release import prepare_release
from livery.workshop._verdict import Transient
from livery.workshop._versions import derive_version
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


#: The longest the members' joined directories may run in a release's
#: name. The name is a branch, and a checkout keeps a branch as a file
#: under ``.git/refs/remotes/origin/``: past Windows' path limit there,
#: a CI checkout fails before anything runs.
NAME_LIMIT = 64


def release_name(members: tuple[str, ...]) -> str:
    """The workflow name a set earns: its directories, sorted and ``+``-joined.

    A set whose joined directories run past `NAME_LIMIT` is named by
    its size and a digest of them instead, so the branch stays short
    and the same set always earns the same name.
    """
    joined = "+".join(sorted(members))
    if len(joined) <= NAME_LIMIT:
        return f"release/{joined}"
    import hashlib

    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]
    return f"release/{len(members)}-packages-{digest}"


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
        derived = derive_version(root, package, released=released)
        if not derived or derived == released:
            unchanged.append(package.member)
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


def _write_manifest(
    root: Path, plans: tuple[MemberPlan, ...], *, mined_at: str
) -> None:
    """Write the release set's record for the squash's discovery.

    The publish wave and the title check read it at the ref, so a
    recovery re-prepare whose stamps already sit on the base still
    names every member; see [livery.workshop._publish.MANIFEST][].
    The record carries *mined_at*, which differs for every prepare,
    so every release squash touches the file: the dispatch finds the
    squash as the last commit touching it, and a prepare that takes
    over an abandoned squash at the same versions still has a commit.
    """
    import json

    from livery.workshop._publish import MANIFEST

    payload = {
        "schema": 1,
        "mined-at": mined_at,
        "members": [
            {
                "dir": plan.package.member,
                "name": plan.package.name,
                "version": plan.version,
            }
            for plan in plans
        ],
    }
    (root / MANIFEST).write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")


def member_pairs_at(git: GitOps, ref: str) -> list[tuple[str, str]] | None:
    """(distribution, version) for each member in the member list at *ref*.

    Each name is read from the member's contract as committed at
    *ref*, so the answer describes that commit alone. None when *ref*
    carries no readable list.
    """
    import tomllib

    from livery.workshop._publish import MANIFEST, read_manifest

    recorded = read_manifest(git.file_at(ref, MANIFEST))
    if recorded is None:
        return None
    pairs: list[tuple[str, str]] = []
    for directory, version in recorded:
        try:
            contract = tomllib.loads(
                git.file_at(ref, f"packages/{directory}/workshop.toml")
            )
            name = str(contract.get("name") or directory)
        except tomllib.TOMLDecodeError:
            name = directory
        pairs.append((name, version))
    return pairs


def bump_set_floors(root: Path, plans: tuple[MemberPlan, ...]) -> list[str]:
    """Raise floors between co-released members; the files changed.

    Within the set only (contract: a solo release never moves a
    floor): a member depending on a co-member gets the co-released
    version, in every home it is declared, the pyproject
    requirement, the ``[[depends]]`` floor, and a conan reference's
    version range.
    """
    import re

    versions = {plan.package.name: plan.version for plan in plans}
    members = {plan.package.name: plan.package.member for plan in plans}
    changed: list[str] = []
    for plan in plans:
        for home in ("pyproject.toml", "workshop.toml", "conanfile.py"):
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
                # The conan range form the lint teaches:
                # "name/[>=version]".
                text = re.sub(
                    rf'("{re.escape(name)}/\[>=)[^\]"]+(\]")',
                    rf"\g<1>{version}\g<2>",
                    text,
                )
                member = re.escape(members[name])
                text = re.sub(
                    rf'(path = "packages/{member}"'
                    rf'[^[]*?floor = ")[^"]+(")',
                    rf"\g<1>{version}\g<2>",
                    text,
                    flags=re.S,
                )
            if text != original:
                path.write_text(text, encoding="utf-8")
                changed.append(path.relative_to(root).as_posix())
    return changed


def rollback_prepare(root: Path, members: tuple[Package, ...]) -> None:
    """Restore exactly the files prepare writes; never raises.

    The error being unwound is the one worth seeing, so this cleans
    quietly: each member's changelog, pyproject, contract, and
    version files return to HEAD, and so do the set's manifest and the
    workspace lock the stamp refreshed. Each restore reads HEAD, never
    the index: a commit that failed (a signing agent that refused) has
    already staged every change, and a restore from the index would
    keep them.
    """
    from livery.workshop._publish import MANIFEST

    paths: list[str] = []
    for package in members:
        base = package.directory.relative_to(root)
        # Only files the member actually has: one pathspec absent
        # from HEAD refuses the whole checkout, taking every other
        # restore down with it (the same trap the lock note below
        # names). prepare edits existing files, so presence on disk
        # is the right filter.
        paths.extend(
            str(base / name)
            for name in (
                "CHANGELOG.md",
                "pyproject.toml",
                "workshop.toml",
                "conanfile.py",
            )
            if (package.directory / name).is_file()
        )
        # Every file the kind's stamper writes a version into. A kind
        # that cannot name them restores the rest regardless.
        with contextlib.suppress(Exception):
            paths.extend(
                home.relative_to(root).as_posix()
                for home in backend_for(package).stamp_version(package).homes()
                if home.is_file()
            )
    tools.git.opts(cwd=root, nofail=True, recorded=False)(
        "checkout", "HEAD", "--", *paths
    )
    # The lock the stamp refreshed and the manifest, each restored
    # alone: a file HEAD lacks in the same pathspec would refuse the
    # whole checkout, taking every other restore down with it.
    for alone in ("uv.lock", MANIFEST):
        tools.git.opts(cwd=root, nofail=True, recorded=False)(
            "checkout", "HEAD", "--", alone
        )
    # A first release's manifest has no HEAD to return to: it goes, so
    # the switch back to the base does not carry it along.
    tracked = tools.git.opts(cwd=root, nofail=True, recorded=False)(
        "cat-file", "-e", f"HEAD:{MANIFEST}"
    )
    if tracked.code != 0:
        tools.git.opts(cwd=root, nofail=True, recorded=False)(
            "rm", "--cached", "--quiet", "--ignore-unmatch", "--", MANIFEST
        )
        (root / MANIFEST).unlink(missing_ok=True)


def _wheel_dists(plans: tuple[MemberPlan, ...]) -> tuple[Path, ...]:
    """The find-links dirs: only members whose kind builds wheels.

    A conan member's build leaves nothing in ``dist/``, and a
    nonexistent find-links path fails the sibling legs outright.
    """
    from livery.workshop._kinds import kind_for

    return tuple(
        plan.package.directory / "dist"
        for plan in plans
        if kind_for(plan.package.kind).wheel_identity
    )


#: The setting that decides whether the floor leg runs, in `[release]`.
PROVE_FLOORS = "prove-floors"


def proves_floors(root: Path, package: Package) -> tuple[bool, str]:
    """Whether *package*'s floors are proved, and the file that decided it.

    The package's own `[release] prove-floors` wins; the workspace's
    is the default; without either the floors are proved.
    """
    from livery.workshop._contract import load_contract

    for contract in (package.directory / "workshop.toml", root / "workshop.toml"):
        if not contract.is_file():
            continue
        release = load_contract(contract).get("release") or {}
        if PROVE_FLOORS in release:
            where = contract.relative_to(root).as_posix()
            return bool(release[PROVE_FLOORS]), where
    return True, ""


def validate_member(
    root: Path,
    plan: MemberPlan,
    release_dirs: tuple[Path, ...],
    *,
    legs: tuple[str, ...] = ("lowest-direct", "highest"),
) -> None:
    """Build and run the isolated legs for one member, reporting each.

    The floor leg first (a floor lying about compatibility fails
    before anything else is spent), then the latest leg. The floor leg
    runs while `[release] prove-floors` is on for the member
    ([livery.workshop._release_driver.proves_floors][]); off, the leg
    is skipped and the skip is named with the file that turned it off. The report
    names what each leg resolved per co-member and sibling. The
    caller builds every set member's wheel first: the legs'
    find-links name each sibling's ``dist/``, so a later member's
    wheel must exist before an earlier member's floor leg resolves.

    A member whose kind publishes no wheels (conan) has no venv to
    isolate: its own gate already built and ran ctest, so the legs
    skip saying why.
    """
    from livery.workshop._kinds import kind_for

    if not kind_for(plan.package.kind).wheel_identity:
        print(
            f"  {plan.package.name}: isolated legs skip"
            f" ({plan.package.kind} kind publishes no wheels; its own"
            " gate builds and tests)"
        )
        return
    proved, where = proves_floors(root, plan.package)
    if not proved and "lowest-direct" in legs:
        legs = tuple(leg for leg in legs if leg != "lowest-direct")
        print(
            f"  {plan.package.name} floor leg: skipped, `[release] {PROVE_FLOORS}"
            f" = false` in {where}"
        )
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


def _base_tree_verified(git: GitOps, base: str) -> bool:
    """Whether the verified record already vouches for *base*'s tip tree.

    A gate that proved this exact tree green in full, on the pull
    request whose squash became the tip, lets the release start at
    once instead of waiting for the tip's own push run. Anything the
    record cannot decide says why, answers False, and the wait proceeds.
    """
    from livery.workshop import _verified

    try:
        git.fetch()
        sha = git.remote_head(base)
        tree = _verified.tree_id(git, sha) if sha else ""
    except Exception as error:
        print(
            f"  {base}: its tip cannot be read for the verified record: {error};"
            " waiting for the tip's own run"
        )
        return False
    if not tree:
        print(f"  {base}: no tree at origin's tip to verify; waiting for its run")
        return False
    found, why = _verified.record(git.root, tree)
    if found is None:
        if why:
            print(f"  {base}: tree {tree[:12]}: {why}; waiting for the tip's own run")
        return False
    if found.scope != _verified.FULL:
        print(
            f"  {base}: tree {tree[:12]} was proved at scope {found.scope}, not"
            " full; waiting for the tip's own run"
        )
        return False
    print(
        f"  {base} verified: tree {tree[:12]} proved green by run {found.run};"
        " the release starts now"
    )
    return True


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
    if _base_tree_verified(git, base):
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
        wave_timeout: float = 1800.0,
    ) -> None:
        self._root = root
        self._repo = repo
        self._git = git
        self._members = members
        self.members = tuple(p.member for p in members)
        self.name = release_name(self.members)
        self.armed = armed
        self._force = force_unverified_base
        self._wave_timeout = wave_timeout
        # An armed release follows its pull request for as long as it
        # would wait for the wave; the engine's own tests pass zero.
        self.watch = wave_timeout
        # Set by `discard`: the next prepare starts from the base, and
        # the given-up branch's manifest says which members' legs
        # still hold.
        self._fresh = False
        self._previous: tuple[str, tuple[tuple[str, str], ...]] | None = None

    @property
    def branch(self) -> str:
        return f"workflow/{self.name}"

    @property
    def base(self) -> str:
        return "main"

    def discard(self) -> None:
        """Give up the prepared branch: the base moved in the set's paths.

        The next prepare derives the set again from the base and
        force-pushes the branch, so the open pull request carries the
        new stamps, entries and ``Mined-At``. The given-up branch's
        manifest is kept: a member whose directory the base did not
        move since that prepare, at the same versions for the whole
        set, keeps its legs' verdict instead of running them again.
        """
        import json

        from livery.workshop._publish import MANIFEST, read_manifest

        git = self._git
        git.fetch()
        text = git.file_at(f"origin/{self.branch}", MANIFEST)
        pairs = read_manifest(text) if text else None
        mined = ""
        if pairs:
            with contextlib.suppress(ValueError, TypeError, AttributeError):
                mined = str(json.loads(text).get("mined-at") or "")
        self._previous = (mined, pairs) if pairs and mined else None
        if git.current_branch() == self.branch:
            git.switch(self.base)
        if git.local_branch_exists(self.branch):
            git.delete_local_branch(self.branch)
        self._fresh = True

    def legs_kept(self, plans: tuple[MemberPlan, ...], mined_at: str) -> set[str]:
        """The members whose legs from the discarded prepare still hold.

        Only when the whole set keeps its versions (a co-member's new
        version moves the floors every other member is tested at), and
        then each member the base did not touch between the two mining
        points.
        """
        if self._previous is None:
            return set()
        previous_mined, previous_pairs = self._previous
        now = tuple((plan.package.member, plan.version) for plan in plans)
        if tuple(sorted(previous_pairs)) != tuple(sorted(now)):
            return set()
        kept: set[str] = set()
        for plan in plans:
            try:
                moved = self._git.log_paths(
                    f"{previous_mined}..{mined_at}", (plan.package.path,)
                )
            except GitError:
                continue  # an unreadable span proves nothing: run the legs
            if not moved:
                kept.add(plan.package.member)
        return kept

    def prepare(self) -> Submission | None:
        """Derive, stamp, and commit the set; recover an existing branch.

        A branch already alive locally or on the remote is recovery:
        nothing is rebuilt, the engine re-submits what the branch
        holds, and titles come from the ref, never the working tree.
        """
        git = self._git
        if not self._fresh and (
            git.local_branch_exists(self.branch)
            or self._repo.branch_exists(self.branch)
        ):
            if git.current_branch() != self.branch:
                if not git.local_branch_exists(self.branch):
                    git.fetch()
                    git._run("checkout", "-b", self.branch, f"origin/{self.branch}")
                else:
                    git.switch(self.branch)
            print(f"  recovering the prepared {self.name} from its branch")
            self._require_fresh_base()
            return self._submission_from_ref()

        require_verified_base(self._repo, git, self.base, force=self._force)
        plans = derive_plans(self._root, self._members)
        mined_at = git.head_sha()
        kept = self.legs_kept(plans, mined_at)
        git.create_branch(self.branch)
        prepared = False
        try:
            floor_changes = bump_set_floors(self._root, plans)
            if floor_changes:
                print(f"  floors raised within the set: {', '.join(floor_changes)}")
            release_dirs = _wheel_dists(plans)
            _write_manifest(self._root, plans, mined_at=mined_at)
            for plan in plans:
                prepare_release(self._root, plan.package.path, plan.version)
                backend_for(plan.package).build(plan.package, self._root)
                if git.is_clean():
                    # Already stamped: a release squash-merged but never
                    # published leaves main carrying exactly these
                    # entries and versions, and re-preparing is the
                    # recovery. A clean tree is that state, not a fault.
                    print(
                        f"  {plan.package.name} v{plan.version}:"
                        " already stamped on the base; nothing to commit"
                    )
                    continue
                git.commit_all(f"chore(release): {plan.package.name} v{plan.version}")
            if not git.is_clean():
                # Only the manifest changed: every member was already
                # stamped on the base (a merged release whose publish
                # failed), and the manifest alone is what lets the
                # squash's discovery still name the whole set.
                git.commit_all("chore(release): the set manifest")
            # Every member's wheel exists before any leg runs; a
            # failed leg still tears the whole branch down, commits
            # included, so nothing unvalidated survives.
            for plan in plans:
                if plan.package.member in kept:
                    print(
                        f"  {plan.package.name} v{plan.version}: legs kept from the"
                        " discarded prepare; the base did not move its directory"
                    )
                    continue
                validate_member(self._root, plan, release_dirs)
            prepared = True
        finally:
            if not prepared:
                rollback_prepare(self._root, self._members)
                git.switch(self.base)
                git.delete_local_branch(self.branch)
        title = release_title([(plan.package.name, plan.version) for plan in plans])
        body = (
            "## Release summary\n"
            + "\n".join(f"- **{plan.package.name}** v{plan.version}" for plan in plans)
            + f"\n\nMined-At: {mined_at}"
        )
        return Submission(title=title, body=body)

    def _require_fresh_base(self) -> None:
        """Refuse a prepared branch whose base moved under it.

        hse merges the default branch into a stale release branch and
        re-verifies; livery's entries carry a mining point instead,
        and commits merged in beneath it would ship under-documented,
        so the stale branch is refused outright: abandon and re-run,
        and the fresh prepare mines everything. Deviation from hse
        ruled 2026-09-04 with issue #161.
        """
        git = self._git
        git.fetch()
        base_sha = git._run("rev-parse", f"origin/{self.base}").strip()
        if git.is_ancestor(base_sha, "HEAD"):
            return
        fail(
            f"{self.base} has moved past this prepared release: commits"
            " it gained are not mined into the branch's entries, and"
            " releasing them under-documented is worse than re-preparing."
            f"\n  Run `{footman.prog()} abandon` on the branch, then"
            f" `{footman.prog()} workflow.release` again."
        )

    def _submission_from_ref(self) -> Submission:
        """The title and body the branch's member list states.

        Recovery reads the branch's committed member list, never the
        working tree and never commit subjects: a checkout standing
        elsewhere holds a different tree, and a rider commit is not
        part of what was prepared, so the rebuilt title stays
        consistent with the content the squash will carry.
        """
        git = self._git
        mined_at = git._run("merge-base", "HEAD", f"origin/{self.base}").strip()
        pairs = member_pairs_at(git, "HEAD") or []
        body = (
            "## Release summary\n"
            + "\n".join(f"- **{name}** v{version}" for name, version in pairs)
            + f"\n\nMined-At: {mined_at}"
        )
        return Submission(title=release_title(pairs), body=body)

    def on_merged(self) -> None:
        """The merge point dispatches the wave; follow it to its verdict.

        The wave's run is awaited by its id, then followed the way
        ``ci.status --point=release --wait`` follows it, so the act
        ends with the wave's own verdict and exit; the receipt tags
        say when each member is done. A wave timeout of zero reports
        the merge and names the dispatch verb as the confirmation
        instead of waiting: the engine's own tests run against a
        forge with no merge point.
        """
        if self._wave_timeout <= 0:
            print(
                "  merged; the merge point dispatches the wave, confirm it with"
                f" `{footman.prog()} workflow.release.dispatch`"
            )
            return
        transient = Transient(interval=_WAVE_POLL)
        before = {run.id for run in _wave_runs_answered(self._repo, transient)}
        run_id = await_wave(
            self._repo,
            before=before,
            timeout=self._wave_timeout,
            transient=transient,
        )
        if run_id is None:
            fail(
                "merged, but no release wave appeared within"
                f" {self._wave_timeout:.0f}s: the merge point's dispatch job"
                " runs after main's own verdict, so a red main or a slow"
                " queue is the usual cause. The recovery is"
                f" `{footman.prog()} workflow.release.dispatch` from any"
                " checkout, which answers green once the wave is up."
            )
        print(
            f"  merged; the merge point dispatched the wave, run {run_id}; the"
            " receipt tags say when each member is done"
        )
        run = next(
            (r for r in _wave_runs_answered(self._repo, transient) if r.id == run_id),
            None,
        )
        if run is None:
            return
        from livery.workshop._ci_tasks import follow_run

        code = follow_run(self._repo, "release", run, timeout=self._wave_timeout)
        self._trace_the_chain()
        if code:
            raise SystemExit(code)

    def _trace_the_chain(self) -> None:
        """Fold everything this release caused into the command's own trace.

        A release is two commits and one hop. The branch commit carries
        the pull request's run, and the commit its merge made carries
        the base's own run and the wave dispatched at it. One walk from
        the branch holds the whole story, and a red wave's is the one
        most worth having, so this runs before the exit code does.

        The branch is already gone from a checkout that recovered a
        release it did not prepare, and then the release squash on the
        base is where the walk starts instead.
        """
        from livery.workshop._traces import drop_chain

        git = self._git
        at = git.sha_of(self.branch)
        if not at:
            at, _subject = newest_release_squash(git, self.base)
        if line := drop_chain(self._repo, git, commit=at):
            print(f"  {line}")


#: The longest release title. GitHub's auto-merge refuses a squash
#: headline it judges too long, and its refusal names 80 characters.
TITLE_LIMIT = 80


def release_title(pairs: Sequence[tuple[str, str]]) -> str:
    """The release pull request's title: each member with its version, or a count.

    The title is the squash commit's headline, and arming hands it to
    GitHub's auto-merge, which refuses a long one. A set whose title
    would pass `TITLE_LIMIT` characters is counted instead; the pull
    request's body and the commit's body list every member.
    """
    listed = ", ".join(f"{name} v{version}" for name, version in pairs)
    title = f"chore(release): released {listed}"
    if len(title) <= TITLE_LIMIT:
        return title
    counted = "1 package" if len(pairs) == 1 else f"{len(pairs)} packages"
    return f"chore(release): released {counted}"


def fresh_receipts(root: Path) -> None:
    """Fetch origin's tags, so a derivation reads every receipt a wave cut.

    A release wave tags its receipts on the remote, and this checkout
    holds them only after a fetch: a derivation from stale tags names
    versions the index already has. A checkout with no origin has only
    its own tags to read; one that cannot reach origin derives from its
    own and says so.
    """
    git = GitOps(root)
    if not git.has_remote("origin"):
        return
    try:
        git.fetch_tags()
    except GitError as error:
        print(
            "  origin could not be fetched, so the versions come from this"
            f" checkout's own tags: {error}"
        )


def local_release(root: Path, members: tuple[Package, ...]) -> None:
    """Everything that stays on this machine, then the stamps roll back.

    Derive, stamp (so the built wheels carry the would-be versions),
    build, validate both legs, print the would-be release, restore
    the tree; ``dist/`` and the report remain. Publishing consent is
    never asked because nothing leaves the machine. The derivation
    reads origin's receipts, fetched first, so it names the versions
    the armed act would ([livery.workshop._release_driver.fresh_receipts][]).
    """
    fresh_receipts(root)
    plans = derive_plans(root, members)
    print("  act: local; nothing leaves this machine")
    try:
        bump_set_floors(root, plans)
        release_dirs = _wheel_dists(plans)
        for plan in plans:
            prepare_release(root, plan.package.path, plan.version)
            backend_for(plan.package).build(plan.package, root)
        for plan in plans:
            validate_member(root, plan, release_dirs)
        listed = ", ".join(f"{plan.package.name} v{plan.version}" for plan in plans)
        print(f"  would release: {listed}")
        print("  wheels in each member's dist/; the tree is restored")
    finally:
        rollback_prepare(root, members)


#: The release workflow, as the forges address one for a dispatch and
#: name one on a run (GitHub lists the path, Gitea the file; the
#: basename is the comparison).
RELEASE_WORKFLOW = "release.yml"


def _wave_runs(repo: Repository) -> tuple[Run, ...]:
    """The release workflow's newest dispatched runs, newest first.

    Asked of the workflow's own listing, so every poll of a wave reads
    one page whatever else was dispatched.
    """
    from livery.workshop._ci_tasks import RECENT_RUNS

    return tuple(
        run
        for run in repo.checks.runs(
            event="workflow_dispatch", workflow=RELEASE_WORKFLOW, limit=RECENT_RUNS
        )
        if run.workflow.rsplit("/", 1)[-1] == RELEASE_WORKFLOW
    )


#: Seconds between the train's polls of the wave's runs.
_WAVE_POLL = 5.0


def _wave_runs_answered(
    repo: Repository, transient: Transient, *, interval: float = _WAVE_POLL
) -> tuple[Run, ...]:
    """The wave's runs, asked again while the forge does not answer.

    An unreadable poll is a retry, as ``fm ci.status --wait`` treats it:
    *transient* counts the run of them, and a spent budget ends the train
    with the forge's words and the command that follows the wave, which
    runs on in CI whatever happens here.
    """
    while True:
        try:
            runs = _wave_runs(repo)
        except ForgeError as error:
            if transient.note(error):
                fail(
                    transient.giving_up(
                        "the wave runs on in CI; follow it with"
                        f" `{footman.prog()} ci.status --point=release --wait`"
                    ).strip()
                )
            time.sleep(interval)
            continue
        transient.reset()
        return runs


def await_wave(
    repo: Repository,
    *,
    before: set[int],
    timeout: float = 1800.0,
    interval: float = _WAVE_POLL,
    transient: Transient | None = None,
) -> int | None:
    """The id of the newest wave run not in *before*, or ``None`` when none appears.

    Any state counts: a wave that already finished by the time the
    poll saw it is still the wave that ran, and a confirmation that
    demanded a live run would miss a fast forge. An unreadable poll is
    retried within *transient*'s budget.
    """
    if transient is None:
        transient = Transient(interval=interval)
    deadline = time.monotonic() + timeout
    while True:
        new = [
            run
            for run in _wave_runs_answered(repo, transient, interval=interval)
            if run.id not in before
        ]
        if new:
            return new[0].id
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval)


def dispatch_flow(
    root: Path,
    repo: Repository,
    git: GitOps,
    *,
    base: str = "main",
    at: str = "",
    workshop: str = "",
    timeout: float = 60.0,
    interval: float = 2.0,
) -> list[str]:
    """Dispatch the wave for a merged, unpublished release, or say why not.

    Reads the manifest on ``origin/<base>`` after a fetch, whatever
    commit this checkout stands on, or at *at*, an older release squash
    a later release has moved past. A checkout behind the base would
    otherwise send the wave to the squash a later release took over.
    *workshop* names a released driver for the wave instead of the
    squash's own, the recovery for a wave whose workshop was the fault;
    empty runs the squash's. Green when there is none, or when
    every member's receipt tag is already on the remote: the
    manifest is permanent residue of the last release, so presence
    is not the signal, missing receipts are. A wave already in
    flight is reported green with its run id. Otherwise the wave is
    dispatched on *base* with the commit that stamped the manifest
    as its ``ref`` input, never this run's own sha, so an unrelated
    merge landing first cannot move the wave onto another tree; the
    dispatch is confirmed by the run id that appears, or refused
    with the reason. Remote state only, receipts by ``ls-remote`` and
    runs by the forge, so any checkout answers the same as CI.
    """
    from livery.workshop._publish import MANIFEST, read_manifest

    where = at or _base_ref(git, base)
    text = git.file_at(where, MANIFEST)
    recorded = read_manifest(text) if text else None
    if not recorded:
        return [f"  no release manifest at {where}: nothing to dispatch"]
    cut = set(git.remote_tags())
    receipts = [f"packages/{directory}/v{version}" for directory, version in recorded]
    missing = [tag for tag in receipts if tag not in cut]
    if not missing:
        return [f"  every receipt is cut ({', '.join(receipts)}): nothing to dispatch"]
    live = [run for run in _wave_runs(repo) if run.status != "completed"]
    if live:
        return [
            f"  the wave is already in flight: run {live[0].id};"
            f" uncut so far: {', '.join(missing)}"
        ]
    stamping = at or git.last_commit_touching(MANIFEST, ref=where)
    if not stamping:
        return [
            f"  {MANIFEST} is at {where} but no commit touches it: nothing to dispatch"
        ]
    _require_release_squash(git, stamping, base)
    seen = {run.id for run in _wave_runs(repo)}
    inputs = {"ref": stamping}
    if workshop:
        inputs["workshop"] = workshop
    try:
        repo.checks.dispatch(RELEASE_WORKFLOW, ref=base, inputs=inputs)
    except ForgeError as error:
        # Red, never a printed line: the merge point's verdict is the
        # exit code, and a wave that did not start leaves the release
        # uncut until someone reads the log.
        fail(
            f"the forge refused the dispatch: {error}; uncut:"
            f" {', '.join(missing)}. Nothing started; on GitHub the job"
            " needs the actions: write grant, and the recovery by hand"
            f" is `{footman.prog()} workflow.release.dispatch`"
        )
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        new = [run for run in _wave_runs(repo) if run.id not in seen]
        if new:
            driver = f" driven by {workshop}" if workshop else ""
            return [
                f"  dispatched the wave at {stamping[:12]}{driver}: run {new[0].id};"
                f" uncut: {', '.join(missing)}"
            ]
        time.sleep(interval)
    return [
        "  the dispatch was accepted but no wave run appeared within"
        f" {timeout:.0f}s; re-run `{footman.prog()} workflow.release.dispatch`,"
        " which reports a wave that has since appeared"
    ]


def _require_release_squash(git: GitOps, stamping: str, base: str) -> None:
    """Refuse to send the wave to a commit the wave itself would refuse.

    The wave publishes only from a release squash, recognised by the
    ``Mined-At`` line the pull request body becomes at the merge. The
    last commit touching the manifest on a release branch is the
    branch's own stamp commit, which carries no such line, so a
    dispatch from a checkout left on that branch would send a wave
    that refuses. The refusal names the squash on the base instead.
    """
    from livery.workshop._publish import mined_at

    if mined_at(git.commit_message(stamping)):
        return
    squash, subject = newest_release_squash(git, base)
    prog = footman.prog()
    if squash:
        where = (
            f"the newest release squash on origin/{base} is {squash[:12]}"
            f" ({subject}); run `{prog} workflow.release.dispatch` from {base},"
            f" or `{prog} workflow.release.dispatch --at={squash[:12]}` here"
        )
    else:
        where = f"origin/{base} holds no release squash in its recent history"
    fail(
        f"{stamping[:12]} carries no Mined-At line: it is a branch's stamp"
        " commit, not a release squash, and the wave would refuse it."
        f" {where}"
    )


def newest_release_squash(git: GitOps, base: str = "main") -> tuple[str, str]:
    """The newest release squash on the base, as (sha, subject); ("", "") when none."""
    from livery.workshop._publish import mined_at

    for sha, subject in git.recent_commits(50, ref=_base_ref(git, base)):
        if subject.startswith("chore(release): released") and mined_at(
            git.commit_message(sha)
        ):
            return sha, subject
    return "", ""


def _base_ref(git: GitOps, base: str) -> str:
    """``origin/<base>`` after a fetch when the clone knows it, else HEAD.

    The release squashes live on the base. HEAD equals the base on the
    merge point and on a person's main, and differs on a release
    branch, whose history lacks the squash it was merged as; a clone
    without the remote (a rig) has only HEAD.
    """
    with contextlib.suppress(GitError):
        git.fetch()
    try:
        git.object_id(f"origin/{base}")
    except GitError:
        return "HEAD"
    return f"origin/{base}"


def pending_release_waves(
    root: Path, git: GitOps, base: str = "main"
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Every recent release squash whose receipts are not all cut, oldest first.

    A merged release whose wave died leaves the squash on the base
    with the stamps and changelogs landed and receipt tags missing.
    Re-preparing from that state derives an empty release (measured:
    an empty pull request the forge never agrees to merge), so the
    recovery is the wave at the squash, never a new pull request.
    Every release squash in the base's recent history is consulted, so
    a later release never strands an earlier died wave, and a checkout
    standing on a release branch still finds the squash it was merged
    as. A later release squash naming a package takes over that
    package's uncut receipt, so a squash abandoned for a new release
    (`fm workflow.release --abandon`) is never offered again. Each
    entry is the squash sha and its missing receipt tags.
    """
    from livery.workshop._publish import discover_release

    cut = set(git.remote_tags())
    pending: list[tuple[str, tuple[str, ...]]] = []
    # Newest first: the packages a later release squash names.
    taken: set[str] = set()
    for sha, subject in git.recent_commits(50, ref=_base_ref(git, base)):
        if not subject.startswith("chore(release): released"):
            continue
        released = discover_release(root, git, sha)
        missing = tuple(
            f"{package.path}/v{version}"
            for package, version in released
            if f"{package.path}/v{version}" not in cut and package.path not in taken
        )
        taken.update(package.path for package, _version in released)
        if missing:
            pending.append((sha, missing))
    pending.reverse()
    return tuple(pending)


def pending_release_wave(root: Path, git: GitOps) -> tuple[str, tuple[str, ...]] | None:
    """The oldest release squash with an uncut receipt, or None.

    The set-blind selection, for a dispatch with no set named:
    recoveries run in order, oldest first.
    """
    waves = pending_release_waves(root, git)
    return waves[0] if waves else None


def pending_release_wave_for(
    root: Path, git: GitOps, members: tuple[Package, ...]
) -> tuple[str, tuple[str, ...]] | None:
    """The oldest uncut squash whose receipts touch *members*, or None.

    A release of one set must find its own died wave even when an
    older squash of another set is uncut too: that squash is the
    other set's recovery, named by the caller, never a reason to
    prepare this set again from a tree already stamped.
    """
    for sha, missing in pending_release_waves(root, git):
        if uncut_in_set(missing, members):
            return (sha, missing)
    return None


def uncut_in_set(
    missing: tuple[str, ...], members: tuple[Package, ...]
) -> tuple[str, ...]:
    """The uncut receipts, `packages/<member>/v<x>`, that belong to *members*."""
    names = {member.member for member in members}
    return tuple(tag for tag in missing if receipt_member(tag) in names)


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
    workshop: Annotated[
        str, doc("a released livery-workshop version to drive a re-dispatched wave")
    ] = "",
    abandon: Annotated[
        bool,
        doc(
            "prepare a new release over a died wave of this set instead of"
            " re-dispatching it"
        ),
    ] = False,
) -> None:
    """Release a set of packages: the branch decides the act.

    Names the set positionally (``packages/forge`` or just ``forge``).
    On a main-family branch this is the release train: one PR,
    receipts per member, re-running the recovery at every step;
    ``--armed`` follows the pull request to its squash and the wave
    to its verdict, from main, the way ``submit --armed`` follows a
    feature pull request, and an unarmed run returns at once. On
    any other branch it is the dev act: a wheel straight from the
    branch at a dev version, published only to the configured custom
    index after a confirmation, no reserved branch, no PR, no tags.
    ``--local`` is everything that stays on this machine, on either
    branch mode. ``--workshop`` applies only to the recovery of a
    died wave: the named release drives the wave in place of the
    squash's own workshop. ``--abandon`` gives up that recovery: when
    no released workshop can drive the died wave, a new release of
    the set takes its uncut receipts over.
    """
    from livery.workshop._dev_release import dev_release
    from livery.workshop._extensions import workspace_root
    from livery.workshop._forge_lane import this_repository

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
    print(f"  act: release train, from '{branch}'")
    pending = pending_release_wave_for(root, git, members)
    repo = this_repository(root)
    if abandon:
        if pending is None:
            fail(
                "--abandon gives up a died wave of this set, and no release"
                " squash has an uncut receipt in it; drop the flag to prepare"
                " a release"
            )
        squash, missing = pending
        print(
            f"  release squash {squash[:12]} abandoned; this release takes over"
            f" its uncut receipts: {', '.join(uncut_in_set(missing, members))}"
        )
        pending = None
    if pending is not None:
        squash, missing = pending
        print(f"  release squash {squash[:12]} has uncut receipts:")
        for name in missing:
            print(f"    {name}")
        # CI publishes, never this machine: the wave is dispatched
        # at the squash, the duplicate-tolerant wave walks past what
        # an earlier attempt already did, and nothing new prepares.
        print("  dispatching the wave at the squash; nothing new prepares")
        for line in dispatch_flow(root, repo, git, at=squash, workshop=workshop):
            print(line)
        print("  re-run to release work newer than the squash")
        return
    for squash, missing in pending_release_waves(root, git):
        # An uncut receipt outside this set is that set's recovery,
        # named so a person can run it; this release goes ahead.
        inside = uncut_in_set(missing, members)
        missing = tuple(tag for tag in missing if tag not in inside)
        if not missing:
            continue
        others = " ".join(sorted({receipt_member(tag) for tag in missing}))
        print(
            f"  release squash {squash[:12]} has uncut receipts outside this"
            f" set: {', '.join(missing)}; `{footman.prog()} workflow.release"
            f" {others}` re-dispatches its wave"
        )
    if workshop:
        fail(
            "--workshop pins the driver of a re-dispatched wave, and no"
            " release squash has an uncut receipt; drop the flag to prepare"
            " a release"
        )
    run_release(
        partial(
            ReleaseDriver,
            root,
            repo,
            git,
            members,
            armed=armed,
            force_unverified_base=force_unverified_base,
        ),
        repo,
        git,
        armed=armed,
    )


def run_release(
    make_driver: Callable[[], ReleaseDriver],
    repo: Repository,
    git: GitOps,
    *,
    armed: bool,
) -> None:
    """Drive a release through the engine; an armed one re-derives a stale set.

    An armed release whose follow ends red because the base moved
    under its set prepares again on the moved base and follows again,
    up to [livery.workshop._release_driver.REDERIVE_LIMIT] prepares
    in all; any other red is the release's verdict and exits with it.
    """
    for attempt in range(1, REDERIVE_LIMIT + 1):
        driver = make_driver()
        try:
            run_workflow(driver, repo, git)
            return
        except SystemExit:
            if (
                not armed
                or attempt == REDERIVE_LIMIT
                or not set_moved(repo, git, driver.name)
            ):
                raise
            print(
                f"  {driver.branch}: the base moved under the set while its pull"
                f" request waited; re-deriving ({attempt} of"
                f" {REDERIVE_LIMIT - 1} re-derives)"
            )


#: How many times an armed release prepares in all: the first prepare
#: and its re-derives on a base that keeps moving under the set. A
#: base that moves faster than a release can prepare is a repository
#: where releases need merges held, and saying so beats looping.
REDERIVE_LIMIT = 3


def set_moved(repo: Repository, git: GitOps, name: str) -> bool:
    """Whether release *name*'s open pull request is stale in its set's paths."""
    from livery.workshop._workflow_state import workflow_states

    git.fetch()
    return any(
        wf.name == name and wf.blocker.name == "STALE_SET"
        for wf in workflow_states(repo, git)
    )


@release_group.task(name="dispatch")
def workflow_release_dispatch(
    at: Annotated[
        str, doc("an older release squash to dispatch instead of HEAD's")
    ] = "",
    workshop: Annotated[
        str, doc("a released livery-workshop version to drive the wave")
    ] = "",
) -> None:
    """Dispatch the release wave for a merged, unpublished release; green otherwise.

    The merge point's last task, and the recovery gesture by hand:
    it reads the manifest at HEAD and the receipts on the remote, so
    on a normal day it prints green, after a died merge run it
    dispatches, and a wave in flight is reported with its run id.
    ``--at`` names an older release squash a later release has moved
    past; ``--workshop`` names a released driver for a wave whose own
    workshop was the fault.
    """
    from livery.workshop._extensions import workspace_root
    from livery.workshop._forge_lane import this_repository

    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    for line in dispatch_flow(
        root, this_repository(root), GitOps(root), at=at, workshop=workshop
    ):
        print(line)


@release_group.task(name="check-title", hidden=True)
def workflow_release_check_title(
    title: Annotated[str, doc("the PR title CI observed")] = "",
) -> None:
    """Verify a release PR's title against the release's member list.

    The publish wave discovers a release from the squash's member
    list; the title is presentation. This job keeps the two
    consistent: the gate job runs this first, so a title that no
    longer names what the list prepared is refused before the
    union and the verdict, and the merge waits for a person to fix
    the title. Without ``--title`` the title
    comes from the runner's event payload; a run that is not a pull
    request, or a pull request off a release branch, is green here
    and says so.
    """
    from livery.workshop._extensions import workspace_root

    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    if not title:
        # The shell passes no title: the event payload names it, and
        # names the head branch too. An explicit --title is always
        # checked, whatever branch the runner is on.
        from livery.workshop._state import event_payload

        pull = (event_payload() or {}).get("pull_request") or {}
        title = str(pull.get("title") or "")
        if not title:
            print("  not a pull request: no title to check")
            return
        head_ref = str((pull.get("head") or {}).get("ref") or "")
        if head_ref and not head_ref.startswith("workflow/release/"):
            print(f"  {head_ref} is not a release branch: nothing to check")
            return
    git = GitOps(root)
    branch = git.current_branch()
    recorded = member_pairs_at(git, "HEAD")
    if not recorded:
        print(f"  {branch}: no release member list; nothing to check")
        return
    expected = release_title(recorded)
    if title and title != expected:
        fail(
            f"the PR title does not match what the member list prepared:\n"
            f"    title:    {title}\n    prepared: {expected}\n"
            "  The title is presentation rebuilt from the branch's"
            " member list; restore it or re-run workflow.release."
        )
    print(f"  title matches the prepared release: {expected}")


@release_group.task(name="check-fresh", hidden=True)
def workflow_release_check_fresh(
    head: Annotated[str, doc("the pull request's head branch")] = "",
    base: Annotated[str, doc("the branch it merges into")] = "",
) -> None:
    """Refuse a release pull request whose base moved in its set's paths.

    The gate job runs this last, so the time between the check and
    the merge it allows is as short as the forge makes it. The
    release's manifest names its members and the commit the prepare
    mined at; a commit the base gained since then under a member's
    directory is code the stamped changelog does not cover, and the
    squash's publish would refuse it after the merge, where nothing
    can fix the squash. Refused here, the pull request waits while
    `workflow.release` re-derives it in place. Without ``--head`` the
    branches come from the runner's event payload; a run that is not
    a pull request, or one off a release branch, is green and says so.
    """
    import json

    from livery.workshop._extensions import workspace_root
    from livery.workshop._publish import MANIFEST, read_manifest

    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    if not head:
        from livery.workshop._state import event_payload

        pull = (event_payload() or {}).get("pull_request") or {}
        if not pull:
            print("  not a pull request: nothing to check")
            return
        head = str((pull.get("head") or {}).get("ref") or "")
        base = base or str((pull.get("base") or {}).get("ref") or "")
    if not head.startswith("workflow/release/"):
        print(f"  {head or 'this branch'} is not a release branch: nothing to check")
        return
    base = base or "main"
    git = GitOps(root)
    text = git.file_at("HEAD", MANIFEST)
    pairs = read_manifest(text) if text else None
    mined = ""
    if pairs:
        with contextlib.suppress(ValueError, TypeError, AttributeError):
            mined = str(json.loads(text).get("mined-at") or "")
    if not pairs or not mined:
        print(f"  {MANIFEST} records no mining point: nothing to check")
        return
    git.fetch()
    paths = tuple(f"packages/{directory}" for directory, _ in pairs)
    moved = git.log_paths(f"{mined}..origin/{base}", paths)
    if moved:
        listed = "\n".join(f"    {subject}" for subject in moved)
        members = " ".join(directory for directory, _ in pairs)
        fail(
            f"{base} moved under this release since its prepare at"
            f" {mined[:12]}:\n{listed}\n  Merged, the squash would ship code"
            " its changelog does not cover. `"
            f"{footman.prog()} workflow.release {members}` re-derives it in"
            " place; an armed release does that itself."
        )
    print(f"  fresh: {base} has not moved under {', '.join(paths)} since {mined[:12]}")


@release_group.task(name="publish", hidden=True)
def workflow_release_publish(
    ref: Annotated[str, doc("the release squash; empty means HEAD")] = "",
    prebuilt: Annotated[
        bool, doc("trust the collected dist/ for the members the matrix builds")
    ] = False,
) -> None:
    """Publish the squash at --ref: the wave, receipts cut per member.

    The CI entry point after a release PR merges, and the recovery
    entry when a publish died mid-wave: everything already tagged is
    walked past. ``--ref`` exists because HEAD usually moves past the
    squash before a recovery runs. Each member probes and publishes
    through its kind's artifact target: python members through the
    resolved index, conan members through the resolved conan target.
    ``--prebuilt`` is the wheels matrix handing over: a
    platform-wheel member's dist/ was collected by the per-OS jobs,
    and the wave publishes it instead of rebuilding one platform's.
    A conan member's saved caches arrive the same way, one per host,
    and the wave attaches them instead of creating the package
    again.
    Inside CI the wave decides that for itself: a platform-wheel
    member whose dist/ already holds wheels was fed by the matrix,
    since a runner's checkout starts with none.
    """
    import os

    from livery.forge import SimpleRegistry
    from livery.workshop._extensions import workspace_root
    from livery.workshop._kinds import kind_for
    from livery.workshop._publish import Registry, publish_release
    from livery.workshop._registries import resolve_registry

    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    git = GitOps(root)
    target = resolve_registry(root, "python")
    if not target.publish_url:
        from livery.workshop._dev_release import INDEX_VAR

        fail(
            "no publish address resolved for python: the declaration"
            " names only a read index, and an upload endpoint is"
            " never defaulted. Declare [registries.python] publish in"
            f" workshop.toml, or set {INDEX_VAR}."
        )
    # The probe carries the resolved credential: an authenticated
    # index, a forge's own registry or a private owner, answers only
    # with it, and an anonymous probe would time out waiting for a
    # wheel the index is already serving.
    registry = SimpleRegistry(target.url, token=target.token)
    registries: dict[str, Registry] = {"python": registry}
    if not prebuilt:
        prebuilt = collected_wheels(root, git, ref)

    def registry_for(package: Package) -> Registry:
        artifact = kind_for(package.kind).artifact
        cached = registries.get(artifact)
        if cached is None:
            if artifact != "conan":
                fail(
                    f"{package.name}: kind {package.kind!r} publishes"
                    f" to {artifact!r}, and the wave has no probe for"
                    " that artifact kind"
                )
            from livery.workshop._backends import _cpp_conan

            assert root is not None  # narrowed before the closure
            conan = resolve_registry(root, "conan")
            cached = _cpp_conan.ConanRegistry(conan, root=root)
            registries[artifact] = cached
        return cached

    receipts = publish_release(
        root,
        git,
        registry_for,
        ref=ref,
        index_url=target.publish_url,
        # UV_PUBLISH_TOKEN is the explicit override; the resolved
        # target's credential is the same seam the probe reads, so a
        # forge registry publishes with the lane token unprompted.
        token=os.environ.get("UV_PUBLISH_TOKEN", "") or target.token,
        prebuilt=prebuilt,
    )
    print(f"  wave: {len(receipts)} member(s) published")


def collected_wheels(root: Path, git: GitOps, ref: str) -> bool:
    """Whether the wheels matrix fed this wave, judged inside CI alone.

    A runner's checkout starts with an empty dist/, so wheels there
    were collected from the matrix's artifacts; outside CI a dist/
    may hold a stale local build, and the wave builds as asked.
    """
    from livery.workshop._kinds import kind_for
    from livery.workshop._publish import discover_release
    from livery.workshop._state import run_context

    if run_context() is None:
        return False
    fed = [
        package.name
        for package, _version in discover_release(root, git, ref or git.head_sha())
        if kind_for(package.kind).wheel_identity == "platform"
        and any((package.directory / "dist").glob("*.whl"))
    ]
    if fed:
        print(f"  prebuilt: the wheels matrix collected wheels for {', '.join(fed)}")
    return bool(fed)
