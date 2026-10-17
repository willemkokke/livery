"""Publish, probe, tag: the release train's receipt-cutting half.

Runs where the release PR's squash landed, discovery reads the ref
and nothing else: the members and their versions are the release's
member list as committed at the ref. Names and versions come from the
same place on purpose: reading names from the commit and versions
from the working tree would pair this release's members with
whatever a later release left in the tree, and the ``--ref``
recovery runs from exactly such a checkout. The squash title is
presentation, rebuilt from the same list, never parsed.

The wave: a member becomes eligible when every in-set dependency it
floors on has its receipt tag cut, and every eligible member runs
its publish, index probe, tag chain concurrently. A failed member
stops only its dependents; siblings complete and keep their tags,
and a re-run walks past everything already tagged, the
duplicate-tolerant publish making that safe.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import livery.footman as footman
from livery import git
from livery.footman import fail
from livery.workshop import _lifecycle
from livery.workshop._packages import Package, discover_packages

if TYPE_CHECKING:
    from livery.workshop._registries import RegistryTarget

#: The release set's own record, committed on the release branch by
#: prepare: every member's directory, name and version. Discovery,
#: the title check and the recovery read it at the squash, so a
#: recovery re-prepare whose stamps are already on the base (and so
#: touch nothing else) still names every member.
MANIFEST = ".release-manifest.json"


def read_manifest(text: str) -> tuple[tuple[str, str], ...] | None:
    """(package dir, version) pairs from manifest *text*, or None.

    None for unreadable content, which the caller refuses: without
    the list there is no record of what the release contains.
    """
    import json

    try:
        data = json.loads(text)
        members = data["members"]
        pairs = tuple((str(m["dir"]), str(m["version"])) for m in members)
    except (ValueError, KeyError, TypeError):
        return None
    return pairs or None


#: How long a member waits for the index to serve its version.
PROBE_TIMEOUT = 300.0
PROBE_POLL = 5.0


class Registry(Protocol):
    """The one probe the wave needs; livery.forge.SimpleRegistry fits."""

    def versions(self, name: str) -> tuple[str, ...]:
        """The published versions of *name*."""
        ...


@dataclass(frozen=True)
class Receipt:
    """One member's publish outcome."""

    package: Package
    version: str
    tag: str
    published: bool  # False when the duplicate tolerance skipped it


def discover_release(root: Path, ref: str) -> tuple[tuple[Package, str], ...]:
    """The members the release squash at *ref* releases, each with its version.

    The members and their versions are the release's member list,
    ``.release-manifest.json``, as committed at *ref*: the record the
    release branch writes when it prepares the set. They come in
    release order, every dependency before its dependents
    ([livery.workshop.order_topologically][]). A rider file in the
    squash changes nothing here, which is what makes hand-editing a
    release branch safe.

    Args:
        root: The workspace root.
        ref: The commit whose member list is read, as git names it.

    Returns:
        Each released member and the version the list states for it.

    Raises:
        Failed: when the commit carries no readable member list, since
            it is then no release squash, naming the flag that points
            the publish at the squash; and when the list names a member
            the workspace does not hold.
    """
    from livery.workshop._graph import order_topologically

    pairs = read_manifest(git.file_at(root, ref, MANIFEST))
    if pairs is None:
        fail(
            f"this commit is not a release squash: it carries no readable"
            f" {MANIFEST}, the member list a release branch commits. Publish"
            " runs on the merge commit of a workflow.release PR; pass"
            " --ref=<squash sha> when HEAD has moved past it."
        )
    listed = {p.member: p for p in discover_packages(root)}
    chosen: list[Package] = []
    stated: dict[str, str] = {}
    for name, version in pairs:
        member = listed.get(name)
        if member is None:
            fail(
                f"the release manifest at {ref[:10]} names"
                f" packages/{name}, which is not a workspace package"
            )
        chosen.append(member)
        stated[member.path] = version
    in_order = order_topologically(tuple(chosen))
    return tuple((member, stated[member.path]) for member in in_order)


_MINED_AT_RE = re.compile(r"^Mined-At: ([0-9a-f]{7,40})$", re.M)


def mined_at(message: str) -> str:
    """The mining point a release squash's *message* records, or ``""``.

    The driver writes ``Mined-At: <sha>`` into the pull request body,
    which becomes the squash's message at the merge. A commit without
    the line is not a release squash: a branch's own stamp commit, or
    a squash whose body was rewritten. The wave and the dispatch judge
    a commit by this one rule.
    """
    match = _MINED_AT_RE.search(message)
    return match.group(1) if match else ""


def movement_check(root: Path, package: Package, ref: str) -> None:
    """Refuse when commits the entry never saw ride in this squash.

    The movement analysis's backstop. The driver records the mining
    point in the squash body (``Mined-At: <sha>``), so the moved
    range is exact: commits between the mining point and the squash's
    parent touching this member's paths are code the stamped entry
    does not cover. A squash without the line is not the driver's,
    and publishing it is refused for the same reason.
    """
    point = mined_at(git.commit_message(root, ref))
    if not point:
        fail(
            f"{package.name}: the squash at {ref[:10]} carries no Mined-At"
            " line, so the movement backstop cannot run. Releases publish"
            " only from workflow.release squashes; re-run the release to"
            " produce one."
        )
    # The squash's first parent, spelled without a caret, which cmd.exe
    # drops on its way to a git run through a `.cmd` launcher on Windows.
    span = f"{point}..{ref}~1"
    moved = git.log_paths(root, span, (f"packages/{package.member}",))
    if moved:
        listed = "\n".join(f"    {s}" for s in moved)
        fail(
            f"{package.name}: commits the stamped entry never saw are in"
            f" this squash:\n{listed}\n  the release went stale after"
            f" prepare. Re-run `{footman.prog()} workflow.release` to re-derive on the"
            " moved base; publishing this squash would ship code the"
            " changelog does not cover."
        )


def index_target(index_url: str, token: str) -> RegistryTarget:
    """Make the python registry target an upload index names, *token* its credential.

    The release train's wave hands it to each member that releases
    python distributions, and the dev act to each member whose
    distributions are all python's: both read and publish at the one
    address the caller resolved.
    """
    from livery.workshop._registries import RegistryTarget

    return RegistryTarget(
        kind="python", url=index_url, publish_url=index_url, token=token
    )


def collected(package: Package) -> bool:
    """Whether *package*'s ``dist/`` holds a file.

    Inside CI a runner's checkout starts with no ``dist/``, so a file
    there was collected from the artifact of a job that built the
    member before the wave.
    """
    dist = package.directory / "dist"
    return dist.is_dir() and any(path.is_file() for path in dist.iterdir())


def probe_until_served(
    registry: Registry,
    name: str,
    version: str,
    *,
    timeout: float = PROBE_TIMEOUT,
    poll: float = PROBE_POLL,
) -> None:
    """Wait until the index serves *version*; the receipt's condition.

    An accepted upload is not a served wheel, and the tag is a
    receipt, so it waits on the probe, not the exit code.
    """
    from livery.workshop._verdict import FollowTimedOut, Pending, follow

    def served() -> bool | Pending:
        return True if version in registry.versions(name) else Pending()

    try:
        follow(
            None,
            served,
            subject=f"{name} {version} on the index",
            interval=poll,
            timeout=timeout,
            quiet=None,
            retried=(),
        )
    except FollowTimedOut:
        fail(
            f"{name} {version} was uploaded but the index never served"
            f" it within {timeout:.0f}s; re-run the publish once the"
            " index settles, everything already tagged is walked past"
        )


def floor_probe(
    package: Package,
    by_path: dict[str, Package],
    registry_for: Callable[[Package], Registry],
    *,
    coreleased: frozenset[str] = frozenset(),
) -> None:
    """Every declared floor must be servable before anything uploads.

    A floor no index can satisfy strands every consumer at install
    time, and refusing here is cheap where unpublishing is
    impossible.
    """
    for edge in package.depends:
        dependency = by_path.get(edge.path)
        floor = getattr(edge, "floor", "")
        if dependency is None or not floor:
            continue
        if f"{edge.path}/v{floor}" in coreleased:
            continue  # the wave itself serves this floor, receipts first
        # The dependency's own registry: a floor across ecosystems (the
        # extension on the library) lives in the conan target, not the
        # python index.
        served = registry_for(dependency).versions(dependency.name)
        floor_key = tuple(int(x) for x in floor.split("."))
        satisfied = any(
            tuple(int(x) for x in v.split(".")) >= floor_key
            for v in served
            if v.replace(".", "").isdigit()
        )
        if not satisfied:
            fail(
                f"{package.name} floors {dependency.name} at {floor}, and"
                " no served version satisfies it. Release"
                f" {dependency.name} first (or in the same set), or the"
                " uploaded wheel strands every consumer at install time."
            )


def cut_tag(root: Path, tag: str, ref: str) -> None:
    """Cut and push the receipt, annotated, by name, idempotent."""
    existing = git.tags(root)
    if tag not in existing:
        git.run(root, "tag", "-a", tag, ref, "-m", tag)
    git.run(root, "push", "origin", tag)


#: The words PyPI answers a new project's first upload with once one
#: account has created as many projects as its limit allows for now.
#: uv relays the server's message as text, and neither its exit code nor
#: any structured output tells this refusal apart, so this match is the
#: one place it is read; checked with uv 0.12.5.
NEW_PROJECT_LIMIT = "Too many new projects created"


def publish_release(
    root: Path,
    registry_for: Callable[[Package], Registry],
    *,
    ref: str = "",
    index_url: str = "",
    token: str = "",
    probe_timeout: float = PROBE_TIMEOUT,
    probe_poll: float = PROBE_POLL,
    prebuilt: bool = False,
) -> tuple[Receipt, ...]:
    """The wave: verify, then publish, probe, tag per member.

    Eligibility is dependency tags: a member starts when every in-set
    dependency's receipt exists, independent members run abreast, a
    failure stops only its dependents. Every read is *ref*-scoped.
    Each member builds through its lifecycle's build, which judges the
    tags of the wheels it writes, and publishes to the target of the
    ecosystem its extensions release it to: the python target the
    caller resolved, *index_url* with *token*, or the one the registry
    ladder resolves for any other ecosystem. A member whose extensions
    answer no registry, or more than one, refuses the wave before
    anything uploads. With *prebuilt*, a member a job of the run builds
    before the wave (the workspace's ``job-built-members`` answer) is
    not built here: the wave publishes what the run collected into its
    ``dist/``, and refuses one whose ``dist/`` holds nothing, naming
    the job. Such a member's publish phase gets ``prebuilt`` true.
    """
    from livery.workshop._queries import JOB_BUILT_MEMBERS, answer
    from livery.workshop._release import verify_release

    resolved_ref = ref or git.head_sha(root)
    discovered = discover_release(root, resolved_ref)
    ordered = tuple(package for package, _version in discovered)
    manifest = {package.name: version for package, version in discovered}
    epoch = int(git.run(root, "log", "-1", "--format=%ct", resolved_ref).strip() or "0")
    # Each member a job of the run builds before the wave, to the job.
    built = answer(root, JOB_BUILT_MEMBERS) if prebuilt else {}

    by_path = {p.path: p for p in discover_packages(root)}
    coreleased = frozenset(f"{p.path}/v{manifest[p.name]}" for p in ordered)
    from livery.workshop._registries import resolve_registry

    # Each member goes to the target of the ecosystem its extensions
    # release it to, and each target is resolved once, before anything
    # uploads: a member with no registry or more than one, and a ladder
    # that resolves no target, stop the wave while there is still nothing
    # to undo. The python target is the one the caller resolved. A member
    # that opts out of publishing needs no target and asks for none.
    python_target = index_target(index_url, token)
    targets = {python_target.kind: python_target}
    releases_to = {p.path: _lifecycle.ecosystem(p) for p in ordered if p.publish}
    for ecosystem in releases_to.values():
        if ecosystem not in targets:
            targets[ecosystem] = resolve_registry(root, ecosystem)
    for package in ordered:
        tag = f"{package.path}/v{manifest[package.name]}"
        verify_release(root, tag, coreleased=coreleased)
        movement_check(root, package, resolved_ref)
        floor_probe(package, by_path, registry_for, coreleased=coreleased)

    chosen = {p.path for p in ordered}
    done: dict[str, threading.Event] = {p.path: threading.Event() for p in ordered}
    receipts: dict[str, Receipt] = {}
    failures: dict[str, BaseException] = {}
    lock = threading.Lock()
    # A registry may limit how many new projects one account creates in
    # a period, as PyPI does. The members whose project the registry
    # does not know yet upload one at a time, so a refusal stops at the
    # first of them, and the ones after it do not try.
    new_projects = [
        p.name for p in ordered if p.publish and not registry_for(p).versions(p.name)
    ]
    first_upload = threading.Lock()
    refused_new: list[str] = []

    def _build_member(package: Package) -> None:
        """Build the member, or trust what the job that builds it collected."""
        job = built.get(package.path)
        if job is None:
            _lifecycle.build_release(package, root, epoch=epoch)
            return
        # The job built this member on each of its legs and the run
        # collected what every leg made; a build here would replace
        # that with what this runner's host alone can make.
        if not collected(package):
            fail(
                f"{package.name}: --prebuilt, and dist/ holds nothing the {job}"
                " job collected; re-run the release so that job builds it before"
                " the wave"
            )

    def _first_upload(package: Package, version: str, target: RegistryTarget) -> bool:
        """Upload a new project's first version, or name the limit that stops it."""
        if refused_new:
            raise SystemExit(
                f"{package.name}: not uploaded: the registry already refused a"
                f" new project in this wave ({refused_new[0]})"
            )
        try:
            return _lifecycle.publish(
                package,
                root,
                version=version,
                target=target,
                prebuilt=package.path in built,
            )
        except BaseException as exc:
            if NEW_PROJECT_LIMIT not in str(exc):
                raise
            refused_new.append(package.name)
            waiting = [name for name in new_projects if name not in receipts_named()]
            raise SystemExit(
                f"{package.name}: the registry refuses more new projects for now"
                f" ({NEW_PROJECT_LIMIT}): it limits how many one account creates"
                f" in a period. New projects still to create: {', '.join(waiting)}."
                f" Re-dispatch later with `{footman.prog()} workflow.release.dispatch`;"
                " the wave walks past what it already published."
            ) from exc

    def receipts_named() -> set[str]:
        with lock:
            return {receipt.package.name for receipt in receipts.values()}

    def _run_member(package: Package) -> None:
        version = manifest[package.name]
        tag = f"{package.path}/v{version}"
        try:
            for edge in package.depends:
                if edge.path in chosen:
                    done[edge.path].wait()
                    if edge.path in failures:
                        raise SystemExit(
                            f"{package.name}: its dependency"
                            f" {edge.path} failed, so this member never"
                            " started"
                        )
            # A package that opts out of publishing is versioned,
            # built and tagged by the train and never uploaded: the
            # registry is neither probed nor written for it, and a
            # re-run walks past its tag the way it walks past a
            # served version. Its publish phase never runs.
            if not package.publish:
                if tag in git.tags(root):
                    print(
                        f"  {package.name} v{version}: already tagged;"
                        " publish = false, done"
                    )
                else:
                    _build_member(package)
                    cut_tag(root, tag, resolved_ref)
                    print(
                        f"  {package.name} v{version}: publish = false; the"
                        " registry upload skips, the tag and the changelog stand"
                    )
                with lock:
                    receipts[package.path] = Receipt(
                        package, version, tag, published=False
                    )
                return
            # A version the target already serves is walked past
            # before any build or upload: a re-run after a died
            # receipt, from CI or from a machine without a publish
            # credential, must reach the tag. The duplicate rejection
            # at the upload stays as the second net for two waves
            # racing.
            served = version in registry_for(package).versions(package.name)
            if tag in git.tags(root) and served:
                print(f"  {package.name} v{version}: already tagged and served; done")
                with lock:
                    receipts[package.path] = Receipt(
                        package, version, tag, published=False
                    )
                return
            _build_member(package)
            # The member's publish phase owns the upload; the wave only
            # hands it the target of the ecosystem it releases to.
            target = targets[releases_to[package.path]]
            if served:
                print(
                    f"  {package.name} v{version}: already served; walking"
                    " past the upload"
                )
                published = False
            else:
                if target.releases and tag not in git.tags(root):
                    # A release is addressed by its tag, so the tag
                    # exists before the assets do. The receipt is
                    # complete only once the probe sees them, which
                    # is why a tag alone no longer ends the member:
                    # a re-run finishes the upload.
                    cut_tag(root, tag, resolved_ref)
                if package.name in new_projects:
                    with first_upload:
                        published = _first_upload(package, version, target)
                else:
                    published = _lifecycle.publish(
                        package,
                        root,
                        version=version,
                        target=target,
                        prebuilt=package.path in built,
                    )
            probe_until_served(
                registry_for(package),
                package.name,
                version,
                timeout=probe_timeout,
                poll=probe_poll,
            )
            cut_tag(root, tag, resolved_ref)
            with lock:
                receipts[package.path] = Receipt(
                    package, version, tag, published=published
                )
            print(f"  {package.name} v{version}: published, served, tagged")
        except BaseException as exc:
            with lock:
                failures[package.path] = exc
            raise
        finally:
            done[package.path].set()

    with ThreadPoolExecutor(max_workers=max(len(ordered), 1)) as pool:
        futures = [pool.submit(_run_member, p) for p in ordered]
        for future in futures:
            exc = future.exception()
            if exc is not None and not isinstance(exc, SystemExit):
                raise exc
    if failures:
        detail = "; ".join(f"{path}: {failures[path]}" for path in sorted(failures))
        tagged = ", ".join(sorted(receipts)) or "none"
        fail(
            f"the wave stopped: {detail}\n  receipts already cut: {tagged}."
            " Fix the cause and re-run the publish; everything tagged is"
            " walked past."
        )
    return tuple(receipts[p.path] for p in ordered)
