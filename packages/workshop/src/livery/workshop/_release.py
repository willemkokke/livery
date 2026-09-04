"""The release train's verbs: prepare, verify, and the template snapshot.

``fm release.verify`` is the train's gate, run by the release
workflow before anything builds: the tag, the ``pyproject`` version,
the ``__version__``, and the changelog must all agree, and every
``[[depends]]`` floor must resolve to a tag that has actually been
released. ``fm release.prepare`` stamps a version into those same
places, idempotently, so the human act is one command plus one tag.

``fm release.templates`` is the workshop release's aftermath: the
``templates/`` tree at the tagged commit is published to the artifact
repository and tagged ``vX.Y.Z`` in lockstep with
``packages/workshop/vX.Y.Z``. Idempotent: the same version with the
same content is a quiet success, and the same version with different
content refuses, because a published tag is immutable.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import toolroom
from footman import doc, fail, group

from livery.workshop import _cliff
from livery.workshop._backends import backend_for
from livery.workshop._git_ops import GitOps
from livery.workshop._layers import workspace_root
from livery.workshop._packages import Package, discover_packages

release = group("release", help="The release train's CI entries")

_TAG_RE = re.compile(r"^(packages/[^/]+)/v(\d+\.\d+\.\d+)$")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")

#: The artifact repository the workshop's template snapshot lands in.


def _root() -> Path:
    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    return root


@dataclass(frozen=True)
class ReleasePlan:
    """One verified release: the package and the version the tag names.

    Attributes:
        package: The workspace package being released.
        version: The semver the tag carries.
    """

    package: Package
    version: str


def verify_release(
    root: Path, tag: str, *, coreleased: frozenset[str] = frozenset()
) -> ReleasePlan:
    """Check *tag* against the tree; every finding fails verbatim.

    The agreements checked: the tag names an existing package; the
    kind's own version homes (pyproject and one ``__version__`` for
    a python kind, the recipe's ``version`` for conan) carry the
    tag's version; a ``## <version>`` changelog entry exists; and
    every ``[[depends]]`` floor names a version whose release tag
    exists, so nothing ships depending on an unreleased floor.
    """
    from livery.workshop._kinds import requires_pyproject

    match = _TAG_RE.fullmatch(tag)
    if match is None:
        fail(f"tag {tag!r} does not match packages/<pkg>/v<semver>")
    path, version = match.group(1), match.group(2)
    packages = {package.path: package for package in discover_packages(root)}
    package = packages.get(path)
    if package is None:
        fail(f"tag names {path}, which is not a workspace package")
    problems = []
    declared = backend_for(package).current_version(package)
    if declared != version:
        problems.append(f"tag says {version}, the package declares {declared}")
    changelog = package.directory / "CHANGELOG.md"
    body = changelog.read_text("utf-8") if changelog.is_file() else ""
    if f"## {version}" not in body and f"## [{version}]" not in body:
        problems.append(f"CHANGELOG.md has no '## {version}' entry")
    if requires_pyproject(package.type):
        inits = list((package.directory / "src").rglob("__init__.py"))
        stamp = f'__version__ = "{version}"'
        if not any(stamp in init.read_text("utf-8") for init in inits):
            problems.append(f"no __init__.py under src/ declares {stamp}")
    released = set(GitOps(root).tags())
    for edge in package.depends:
        if not edge.floor:
            continue
        wanted = f"{edge.path}/v{edge.floor}"
        if wanted in coreleased:
            # An atomic set's intra-set floor: the wave cuts the
            # dependency's receipt before this member publishes, so
            # the tag it names exists by the time any consumer looks.
            continue
        if wanted not in released:
            problems.append(
                f"the floor on {edge.path} is {edge.floor}, and no tag"
                f" {wanted} exists: floors must name released versions"
            )
    if problems:
        fail(f"release {tag} refused:\n  " + "\n  ".join(problems))
    return ReleasePlan(package=package, version=version)


@release.task(name="verify", hidden=True)
def release_verify(
    tag: Annotated[str, doc("the release tag, packages/<pkg>/v<semver>")],
) -> None:
    """Verify *tag* against the tree; the train's gate before building.

    In GitHub Actions the verified package name is appended to
    ``$GITHUB_OUTPUT`` as ``package=<name>`` for the build step.
    """
    plan = verify_release(_root(), tag)
    print(f"  verified: {plan.package.name} {plan.version} from {tag}")
    output = os.environ.get("GITHUB_OUTPUT", "")
    if output:
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(f"package={plan.package.name}\n")


def _last_released(root: Path, package: Package) -> str:
    """The newest released version *package*'s tags carry, or empty.

    Tags are the release identity and its receipt; a stamped version
    without its tag is an unfinished release, not a finished one.
    """
    from livery.workshop._git_ops import GitOps
    from livery.workshop._update import latest_released

    return latest_released(GitOps(root).tags()).get(package.path, "")


def prepare_release(root: Path, path: str, version: str = "") -> list[str]:
    """Stamp a release into *path*'s places; what changed.

    Without *version*, git-cliff derives it from the conventional
    commits under the package's paths since its last release tag, and
    writes the entry the package's ``cliff.toml`` shapes. A given
    *version* wins and gets an empty entry for the human to write.
    Idempotent either way: a place already carrying the version is
    left alone.
    """
    packages = {package.path: package for package in discover_packages(root)}
    package = packages.get(path)
    if package is None:
        fail(f"{path} is not a workspace package")
    entry_body = ""
    if not version:
        derived = _cliff.bumped_version(root, package)
        released = _last_released(root, package)
        if not derived or derived == released:
            # git-cliff answers with the last tag's version when
            # nothing unreleased touches the package. The tag is the
            # receipt (never the stamped pyproject: a stamp can land
            # without its release cutting, and judging by it would
            # strand that release forever). Releasing again would
            # republish the same code under a version the index
            # already has.
            print(f"  nothing to release: no unreleased commits touch {path}")
            return []
        version = derived
        entry_body = _cliff.unreleased_entry(root, package, version)
        since = released or "the beginning"
        print(f"  derived {version} from the commits since {since}")
    if not _SEMVER_RE.fullmatch(version):
        fail(f"version {version!r} is not <major>.<minor>.<patch>")
    if not entry_body and version != _last_released(root, package):
        # An explicitly passed version regenerates a stranded entry
        # too: the driver hands prepare the derived version, and a
        # heading without its tag under-documents what actually
        # ships either way.
        entry_body = _cliff.unreleased_entry(root, package, version)
    changed = backend_for(package).stamp_version(package).stamp(version)
    changelog = package.directory / "CHANGELOG.md"
    text = changelog.read_text("utf-8") if changelog.is_file() else "# Changelog\n"
    heading_present = f"## {version}" in text or f"## [{version}]" in text
    if not heading_present:
        # A blank line on each side, so the new entry and the one it
        # sits above stay separate blocks.
        insert = "\n" + (entry_body or f"## [{version}]\n\n-").strip() + "\n"
        first_entry = text.find("\n## ")
        if first_entry == -1:
            text = text.rstrip("\n") + "\n" + insert
        else:
            text = text[:first_entry] + insert + text[first_entry:]
        changelog.write_text(text, encoding="utf-8")
        changed.append("CHANGELOG.md (review the entry before tagging)")
    elif entry_body:
        # The stranded shape: the heading exists but the tag never
        # cut, and this derived entry covers everything since the
        # last receipt, so the stale entry regenerates rather than
        # under-documenting what actually ships.
        rewritten = _replace_entry(text, version, entry_body)
        if rewritten != text:
            changelog.write_text(rewritten, encoding="utf-8")
            changed.append("CHANGELOG.md (the stranded entry regenerated; review it)")
    lock = root / "uv.lock"
    if changed and lock.is_file():
        # The lock records every member's version, so a stamp without
        # a refresh leaves the lock claiming the old one: the first
        # sync after the release rewrites it, and the dirty tree then
        # blocks the train's own re-run. Refreshing here puts the lock
        # line inside the commit that stamps the version.
        before_lock = lock.read_bytes()
        result = toolroom.uv.opts(cwd=root, nofail=True, recorded=False)("lock")
        if result.code != 0:
            fail(
                f"uv lock after stamping {version} failed:"
                f"\n{result.stdout}{result.stderr}"
            )
        if lock.read_bytes() != before_lock:
            changed.append("uv.lock")
    return changed


def _replace_entry(text: str, version: str, entry_body: str) -> str:
    """*text* with *version*'s entry block replaced by *entry_body*."""
    import re as _re

    pattern = _re.compile(
        rf"^## \[?{_re.escape(version)}\]?[^\n]*\n.*?(?=^## |\Z)",
        flags=_re.M | _re.S,
    )
    replacement = entry_body.strip() + "\n\n"
    rewritten, count = pattern.subn(lambda _m: replacement, text, count=1)
    return rewritten if count else text


@release.task(name="prepare", hidden=True)
def release_prepare(
    path: Annotated[str, doc("the package, e.g. packages/workshop")],
    version: Annotated[str, doc("the semver to stamp; empty derives it")] = "",
) -> None:
    """Stamp a release: version derived from the commits unless given.

    The derived path asks git-cliff what the unreleased commits earn,
    through the package's own ``cliff.toml``, and writes the entry
    they make: sections grouped, pull requests linked, authors
    credited, for review. A package with nothing unreleased is
    refused rather than given a new number. A given version wins and
    leaves the entry for the human. ``fm release.verify`` is the
    check that everything agrees before the tag is cut.
    """
    for name in prepare_release(_root(), path, version):
        print(f"  stamped: {name}")


def _run_git(cwd: Path, *args: str) -> toolroom.Result:
    return toolroom.git.opts(cwd=cwd, nofail=True, recorded=False)(*args)


def _git_or_fail(cwd: Path, *args: str) -> str:
    result = _run_git(cwd, *args)
    if result.code != 0:
        fail(
            f"git {' '.join(args)} exited {result.code}:"
            f"\n{result.stdout}{result.stderr}"
        )
    return result.stdout


def publish_templates(
    templates: Path, version: str, remote: str, *, author: str = ""
) -> str:
    """Publish *templates* to *remote* as ``v<version>``; the outcome line.

    W6, idempotent by construction: an existing ``v<version>`` tag
    whose tree matches *templates* is a quiet success; one whose tree
    differs refuses, because a published tag is immutable and a
    different tree under the same number would lie to every instance.
    Otherwise the remote's default branch becomes exactly *templates*
    (deletions included) and the tag is pushed with the commit.
    """
    if not _SEMVER_RE.fullmatch(version):
        fail(f"version {version!r} is not <major>.<minor>.<patch>")
    if not templates.is_dir():
        fail(f"{templates} is not a directory")
    remote = _authenticated_remote(remote)
    with tempfile.TemporaryDirectory() as scratch:
        clone = Path(scratch) / "artifact"
        _git_or_fail(Path(scratch), "clone", remote, "artifact")
        if author:
            name, _, email = author.partition(" <")
            _git_or_fail(clone, "config", "user.name", name)
            _git_or_fail(clone, "config", "user.email", email.rstrip(">"))
        tags = _git_or_fail(clone, "tag", "-l").split()
        _replace_tree(clone, templates)
        _git_or_fail(clone, "add", "-A")
        if f"v{version}" in tags:
            # --cached against the tag sees additions and deletions the
            # worktree diff would miss.
            diff = _run_git(clone, "diff", "--cached", "--quiet", f"v{version}", "--")
            if diff.code == 0:
                return f"v{version} already published with this content"
            fail(
                f"v{version} is already published with different content:"
                " a released tag is immutable, bump the version"
            )
        if _git_or_fail(clone, "status", "--porcelain").strip():
            _git_or_fail(clone, "commit", "-m", f"templates v{version}")
        _git_or_fail(clone, "tag", "-a", f"v{version}", "-m", f"templates v{version}")
        _git_or_fail(clone, "push", "origin", "HEAD", f"v{version}")
        return f"published v{version}"


def _authenticated_remote(remote: str) -> str:
    """*remote* with ``FORGE_TOKEN`` credentials for a bare http URL.

    A CI job pushes the artifact over http with the mounted token; a
    URL already carrying userinfo, an ssh remote, and a local path
    pass through untouched. The credentialled spelling lives only in
    this process: nothing writes it to disk.
    """
    import os
    import re as _re

    token = os.environ.get("FORGE_TOKEN", "")
    if token and _re.match(r"^https?://[^@/]+(?:/|$)", remote):
        return _re.sub(r"^(https?://)", rf"\1x-access-token:{token}@", remote, count=1)
    return remote


def _replace_tree(clone: Path, templates: Path) -> None:
    """Make *clone*'s worktree exactly *templates*, deletions included."""
    for entry in sorted(clone.iterdir()):
        if entry.name == ".git":
            continue
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    shutil.copytree(templates, clone, dirs_exist_ok=True)


@release.task(name="wheels", hidden=True)
def release_wheels(
    ref: Annotated[str, doc("the release squash; empty means HEAD")] = "",
) -> None:
    """Build this platform's wheels for the squash's native members.

    One per-OS matrix job runs this before the wave: cibuildwheel
    builds every supported CPython for this platform (CIBW_BUILD
    widened past the local one-interpreter narrowing, musllinux
    kept), the artifact upload collects each ``dist/``, and the
    wave publishes the union with ``--prebuilt``. A squash with no
    platform-wheel member prints so and builds nothing, so the
    matrix job stays green on a pure release.
    """
    import os

    from livery.workshop._backends import backend_for
    from livery.workshop._kinds import kind_for
    from livery.workshop._publish import discover_release

    root = _root()
    git = GitOps(root)
    resolved_ref = ref or git.head_sha()
    epoch = int(git._run("log", "-1", "--format=%ct", resolved_ref).strip() or "0")
    native = [
        package
        for package, _version in discover_release(root, git, resolved_ref)
        if kind_for(package.type).wheel_identity == "platform"
    ]
    if not native:
        print("  no platform-wheel members in this release; nothing to build")
        return
    # The full set for this platform: every supported CPython, and
    # musllinux kept (an empty CIBW_SKIP reads as no skip, and its
    # presence stops the local narrowing's setdefault).
    os.environ.setdefault("CIBW_BUILD", "cp3*-*")
    os.environ.setdefault("CIBW_SKIP", "")
    for package in native:
        dist = backend_for(package).build(package, root, epoch=epoch)
        wheels = ", ".join(sorted(w.name for w in dist.glob("*.whl")))
        print(f"  {package.name}: {wheels}")


@release.task(name="templates", hidden=True)
def release_templates(
    version: Annotated[
        str, doc("the publishing layer's version (default: its installed one)")
    ] = "",
    remote: Annotated[
        str, doc("artifact repository url (default: the contract's)")
    ] = "",
) -> None:
    """Publish this home's template artifact for one release.

    Runs from the released checkout in the release workflow. A layer
    home publishes its composed tree (base at the pinned installed
    version plus its overlay), with the composition recorded in the
    artifact; the base home publishes its own tree unchanged, the
    degenerate case. The tag is ``v<version>``, the publishing
    layer's version, in lockstep with that layer's release tag.
    Same version, different content refuses: a released tag is
    immutable.
    """
    import tempfile as _tempfile
    from importlib.metadata import version as installed

    from livery.workshop._compose import layer_template_tree
    from livery.workshop._layers import layer_entries
    from livery.workshop._templates import render_source, templates_artifact

    root = _root()
    remote = remote or templates_artifact(root)
    if not remote:
        fail(
            "this workspace declares no [workspace] templates_artifact:"
            " only a template home publishes; declare the artifact"
            " repository in workshop.toml"
        )
    entries = layer_entries(root)
    publisher = ""
    publisher_layer = ""
    for layer, dist in entries:
        if layer_template_tree(root, layer) is not None:
            publisher, publisher_layer = dist, layer
    if not publisher:
        fail(
            "no layer in the stack ships a template tree: nothing to"
            " publish; `uv sync` installs the base layer's"
        )
    version = version or installed(publisher)
    source, ref, owners = render_source(root)
    if ref is not None:
        fail(
            "the template source resolves to a remote artifact: only a"
            " home (a workspace whose stack ships its trees locally)"
            " publishes"
        )
    with _tempfile.TemporaryDirectory() as scratch:
        staged = Path(scratch) / "tree"
        shutil.copytree(source, staged)
        if owners:
            # A composed tree records what it was composed from: the
            # base and its pinned version, so a reader of the artifact
            # knows which improvements it already carries.
            _base_layer, base_dist = entries[0]
            lines = [
                "# Generated by the workshop's composed release; the",
                "# artifact states its own composition.",
                "[composed]",
                f'base = "{base_dist}"',
                f'base_version = "{installed(base_dist)}"',
                f'publisher = "{publisher}"',
                "layers = [" + ", ".join(f'"{layer}"' for layer, _ in entries) + "]",
            ]
            record = "\n".join(lines) + "\n"
            (staged / "composition.toml").write_text(record, encoding="utf-8")
        outcome = publish_templates(
            staged,
            version,
            remote,
            author=f"{publisher_layer} release train <release@{publisher}.invalid>",
        )
    print(f"  {outcome}")
