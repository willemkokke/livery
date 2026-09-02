"""Publish, probe, tag: the release train's receipt-cutting half.

Runs where the release PR's squash landed, discovery reads the ref
and nothing else: the squash title is the manifest
(``chore(release): released livery-forge v0.2.0, ...``), versions
verified against the tree at that ref, and HEAD having moved on is
exactly why ``--ref`` exists.

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

from livery.workshop._backends import _python
from livery.workshop._git_ops import GitOps
from livery.workshop._packages import Package, discover_packages

_MANIFEST_RE = re.compile(r"^chore\(release\): released (.+)$")
_MEMBER_RE = re.compile(r"^(\S+) v(\d+\.\d+\.\d+)$")

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


def parse_manifest(title: str) -> tuple[tuple[str, str], ...]:
    """(distribution, version) pairs from a squash title, or fail teaching.

    The title is the manifest by convention, and the convention is
    CI-asserted on release PRs, so an unparsable title here means the
    checkout is not at a release squash at all.
    """
    match = _MANIFEST_RE.match(title.strip().splitlines()[0] if title else "")
    if match is None:
        fail(
            "this commit is not a release squash: its title does not read"
            " `chore(release): released <name> v<version>, ...`. Publish"
            " runs on the merge commit of a workflow.release PR; pass"
            " --ref=<squash sha> when HEAD has moved past it."
        )
    pairs: list[tuple[str, str]] = []
    for part in match.group(1).split(", "):
        member = _MEMBER_RE.match(part.strip())
        if member is None:
            fail(
                f"the release manifest carries {part.strip()!r}, which does"
                " not read `<name> v<version>`; the title job should have"
                " refused this squash"
            )
        pairs.append((member.group(1), member.group(2)))
    return tuple(pairs)


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
    registry: Registry,
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
        served = registry.versions(dependency.name)
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
) -> tuple[Receipt, ...]:
    """The wave: verify, then publish, probe, tag per member.

    Eligibility is dependency tags: a member starts when every in-set
    dependency's receipt exists, independent members run abreast, a
    failure stops only its dependents. Every read is *ref*-scoped.
    """
    from livery.workshop._graph import order_topologically
    from livery.workshop._release import verify_release

    ensure_git_identity(git)
    resolved_ref = ref or git.head_sha()
    title = git.commit_message(resolved_ref).splitlines()[0]
    manifest = dict(parse_manifest(title))
    by_name = {p.name: p for p in discover_packages(root)}
    members: list[Package] = []
    for name in manifest:
        package = by_name.get(name)
        if package is None:
            fail(f"the manifest names {name}, which is not a workspace package")
        members.append(package)
    ordered = order_topologically(tuple(members))
    epoch = int(git._run("log", "-1", "--format=%ct", resolved_ref).strip() or "0")

    by_path = {p.path: p for p in discover_packages(root)}
    coreleased = frozenset(f"{p.path}/v{manifest[p.name]}" for p in ordered)
    for package in ordered:
        tag = f"{package.path}/v{manifest[package.name]}"
        verify_release(root, tag, coreleased=coreleased)
        movement_check(root, git, package, resolved_ref)
        floor_probe(package, by_path, registry_for(package), coreleased=coreleased)

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
            _python.build(package, root, epoch=epoch)
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
