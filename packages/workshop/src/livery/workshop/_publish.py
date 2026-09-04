"""Publish, probe, tag: the release train's receipt-cutting half.

Runs where the release PR's squash landed, discovery reads the ref
and nothing else: the members are the ``packages/*/CHANGELOG.md``
paths the squash touched, each version the top heading of that
changelog as committed at the ref. Names and versions come from the
same place on purpose: reading names from the commit and versions
from the working tree would pair this release's members with
whatever a later release left in the tree, and the ``--ref``
recovery runs from exactly such a checkout. The squash title is
presentation, rebuilt from the same changelogs, never parsed.

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
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import footman
import toolroom
from footman import fail

from livery.workshop._git_ops import GitOps
from livery.workshop._packages import Package, discover_packages

_CHANGELOG_RE = re.compile(r"^packages/([^/]+)/CHANGELOG\.md$")
_HEADING_RE = re.compile(r"^## \[?(\d+\.\d+\.\d+)\]?", re.M)

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


def changelog_version(text: str) -> str:
    """The top version heading of a changelog body; empty when none."""
    match = _HEADING_RE.search(text)
    return match.group(1) if match else ""


def discover_release(
    root: Path, git: GitOps, ref: str
) -> tuple[tuple[Package, str], ...]:
    """What the squash at *ref* releases, in topological order.

    Members are the ``packages/*/CHANGELOG.md`` paths the commit
    touched; each version is the top heading of that changelog as
    committed at the ref. A commit touching no member changelog is
    not a release squash and fails teaching the recovery flag. A
    rider file in the squash changes nothing here, which is what
    makes hand-editing an entry on the release branch safe.
    """
    from livery.workshop._graph import order_topologically

    names: list[str] = []
    for path in git.files_in_commit(ref):
        match = _CHANGELOG_RE.match(path)
        if match:
            names.append(match.group(1))
    if not names:
        fail(
            "this commit is not a release squash: it touches no"
            " packages/*/CHANGELOG.md. Publish runs on the merge commit"
            " of a workflow.release PR; pass --ref=<squash sha> when"
            " HEAD has moved past it."
        )
    by_dir = {p.directory.name: p for p in discover_packages(root)}
    members: list[Package] = []
    versions: dict[str, str] = {}
    for name in names:
        package = by_dir.get(name)
        if package is None:
            fail(
                f"the squash touches packages/{name}/CHANGELOG.md, but"
                f" packages/{name} is not a workspace package"
            )
        version = changelog_version(git.file_at(ref, f"packages/{name}/CHANGELOG.md"))
        if not version:
            fail(
                f"packages/{name}/CHANGELOG.md at {ref[:10]} has no"
                " `## <version>` heading, so the release it states is"
                " unreadable"
            )
        members.append(package)
        versions[package.path] = version
    ordered = order_topologically(tuple(members))
    return tuple((package, versions[package.path]) for package in ordered)


_MINED_AT_RE = re.compile(r"^Mined-At: ([0-9a-f]{7,40})$", re.M)


def movement_check(root: Path, git: GitOps, package: Package, ref: str) -> None:
    """Refuse when commits the entry never saw ride in this squash.

    The movement analysis's backstop. The driver records the mining
    point in the squash body (``Mined-At: <sha>``), so the moved
    range is exact: commits between the mining point and the squash's
    parent touching this member's paths are code the stamped entry
    does not cover. A squash without the line is not the driver's,
    and publishing it is refused for the same reason.
    """
    message = git.commit_message(ref)
    match = _MINED_AT_RE.search(message)
    if match is None:
        fail(
            f"{package.name}: the squash at {ref[:10]} carries no Mined-At"
            " line, so the movement backstop cannot run. Releases publish"
            " only from workflow.release squashes; re-run the release to"
            " produce one."
        )
    span = f"{match.group(1)}..{ref}^"
    moved = git.log_paths(span, (f"packages/{package.directory.name}",))
    if moved:
        listed = "\n".join(f"    {s}" for s in moved)
        fail(
            f"{package.name}: commits the stamped entry never saw are in"
            f" this squash:\n{listed}\n  the release went stale after"
            f" prepare. Re-run `{footman.prog()} workflow.release` to re-derive on the"
            " moved base; publishing this squash would ship code the"
            " changelog does not cover."
        )


def ensure_git_identity(git: GitOps) -> None:
    """Give a fresh CI checkout an identity, a developer's kept.

    Only when ``user.email`` is unset: the environment's GIT_* wins
    anyway, and a person's own configuration is never touched.
    """
    probe = toolroom.git.opts(cwd=git.root, nofail=True, recorded=False)(
        "config", "user.email"
    )
    if probe.code == 0 and probe.stdout.strip():
        return
    for key, value in (
        ("user.email", "release@livery.local"),
        ("user.name", "livery release"),
    ):
        toolroom.git.opts(cwd=git.root, nofail=True, recorded=False)(
            "config", key, value
        )


def assert_wheel_identity(package: Package) -> None:
    """Refuse a wheel whose tag contradicts its kind, naming both.

    A native kind's ``none-any`` wheel was built without its
    extension; a pure kind's platform-tagged wheel smuggled native
    code past every leg that never compiled it. A kind with no
    wheel identity (conan) skips.
    """
    from livery.workshop._kinds import kind_for

    identity = kind_for(package.type).wheel_identity
    if not identity:
        return
    wheels = sorted((package.directory / "dist").glob("*.whl"))
    if not wheels:
        fail(f"{package.name}: no wheel in dist/ to publish")
    for wheel in wheels:
        pure = "none-any" in wheel.name
        if identity == "platform" and pure:
            fail(
                f"{package.name} is a {package.type} package and"
                f" {wheel.name} is pure-tagged: the extension did not"
                " compile into the wheel"
            )
        if identity == "pure" and not pure:
            fail(
                f"{package.name} is a {package.type} package and"
                f" {wheel.name} carries a platform tag: a pure kind"
                " must ship none-any, or its declared kind is wrong"
            )


def publish_wheels(package: Package, *, index_url: str = "", token: str = "") -> bool:
    """Upload ``dist/*``; False when everything was already published.

    ``uv publish``, with the already-published duplicate the one
    tolerated failure: a re-run must walk past what an earlier
    attempt landed. Anything else, a rejected credential, an
    unreachable index, surfaces verbatim.
    """
    command = ["publish"]
    if index_url:
        command += ["--publish-url", index_url]
    if token:
        # recorded=False keeps the credential-carrying argv out of
        # receipts, --json, and recordings alike.
        command += ["--token", token]
    command += [str(path) for path in sorted((package.directory / "dist").glob("*"))]
    result = toolroom.uv.opts(cwd=package.directory, nofail=True, recorded=False)(
        *command
    )
    if result.code == 0:
        return True
    output = f"{result.stdout}{result.stderr}"
    if "already exists" in output or "duplicate" in output.lower():
        print(f"  {package.name}: already published; walking past")
        return False
    fail(f"uv publish ({package.name}) exited {result.code}:\n{output}")
    return False  # unreachable; fail raises


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
    deadline = time.monotonic() + timeout
    while True:
        if version in registry.versions(name):
            return
        if time.monotonic() >= deadline:
            fail(
                f"{name} {version} was uploaded but the index never served"
                f" it within {timeout:.0f}s; re-run the publish once the"
                " index settles, everything already tagged is walked past"
            )
        time.sleep(poll)


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
        # The dependency's own registry: a cross-kind floor (the
        # extension on the library) lives in the conan target, not
        # the python index.
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


def cut_tag(git: GitOps, tag: str, ref: str) -> None:
    """Cut and push the receipt, annotated, by name, idempotent."""
    existing = git.tags()
    if tag not in existing:
        git._run("tag", "-a", tag, ref, "-m", tag)
    git._run("push", "origin", tag)


def publish_release(
    root: Path,
    git: GitOps,
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
    Each member builds through its kind's backend and publishes to
    its kind's artifact target; the wheel identity guard runs both
    ways before anything uploads. *prebuilt* trusts a ``dist/``
    already collected (the per-OS wheels matrix) for members whose
    kind builds platform wheels, and refuses an empty one naming
    the collection.
    """
    from livery.workshop._release import verify_release

    ensure_git_identity(git)
    resolved_ref = ref or git.head_sha()
    discovered = discover_release(root, git, resolved_ref)
    ordered = tuple(package for package, _version in discovered)
    manifest = {package.name: version for package, version in discovered}
    epoch = int(git._run("log", "-1", "--format=%ct", resolved_ref).strip() or "0")

    by_path = {p.path: p for p in discover_packages(root)}
    coreleased = frozenset(f"{p.path}/v{manifest[p.name]}" for p in ordered)
    from livery.workshop._kinds import kind_for as _kind_for

    conan_target = None
    if any(_kind_for(p.type).artifact == "conan" for p in ordered):
        # Resolved once, before anything uploads: a ladder refusal
        # (no conan target anywhere) must stop the wave while there
        # is still nothing to undo.
        from livery.workshop._registries import resolve_registry

        conan_target = resolve_registry(root, "conan")
    for package in ordered:
        tag = f"{package.path}/v{manifest[package.name]}"
        verify_release(root, tag, coreleased=coreleased)
        movement_check(root, git, package, resolved_ref)
        floor_probe(package, by_path, registry_for, coreleased=coreleased)

    chosen = {p.path for p in ordered}
    done: dict[str, threading.Event] = {p.path: threading.Event() for p in ordered}
    receipts: dict[str, Receipt] = {}
    failures: dict[str, BaseException] = {}
    lock = threading.Lock()

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
            if tag in git.tags():
                print(f"  {package.name} v{version}: already tagged; done")
                with lock:
                    receipts[package.path] = Receipt(
                        package, version, tag, published=False
                    )
                return
            from livery.workshop._kinds import backend_for, kind_for

            record = kind_for(package.type)
            if prebuilt and record.wheel_identity == "platform":
                # The matrix already built and collected this
                # member's wheels; a rebuild here would clobber them
                # with one platform's.
                if not list((package.directory / "dist").glob("*.whl")):
                    fail(
                        f"{package.name}: --prebuilt, and dist/ holds"
                        " no collected wheels; the wheels matrix did"
                        " not feed this wave"
                    )
            else:
                backend_for(package).build(package, root, epoch=epoch)
            assert_wheel_identity(package)
            if record.artifact == "conan":
                from livery.workshop._backends import _cpp_conan

                assert conan_target is not None
                published = _cpp_conan.publish(
                    package,
                    conan_target.url,
                    version=version,
                    local=conan_target.local,
                )
            else:
                published = publish_wheels(package, index_url=index_url, token=token)
            probe_until_served(
                registry_for(package),
                package.name,
                version,
                timeout=probe_timeout,
                poll=probe_poll,
            )
            cut_tag(git, tag, resolved_ref)
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
