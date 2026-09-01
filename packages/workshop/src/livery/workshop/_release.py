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
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from footman import doc, fail, group

from livery.workshop import _cliff
from livery.workshop._backends import _python
from livery.workshop._git_ops import GitOps
from livery.workshop._layers import workspace_root
from livery.workshop._packages import Package, discover_packages

release = group("release", help="The release train's CI entries")

_TAG_RE = re.compile(r"^(packages/[^/]+)/v(\d+\.\d+\.\d+)$")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")

#: The artifact repository the workshop's template snapshot lands in.
TEMPLATES_REMOTE = "git@github.com:willemkokke/workshop-templates.git"


def _root() -> Path:
    root = workspace_root()
    if root is None:
        fail("no workspace: no livery.toml above the working directory")
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
    package's ``pyproject.toml`` version, one ``__version__`` under
    ``src/``, and a ``## <version>`` changelog entry all carry the
    tag's version; and every ``[[depends]]`` floor names a version
    whose release tag exists, so nothing ships depending on an
    unreleased floor.
    """
    match = _TAG_RE.fullmatch(tag)
    if match is None:
        fail(f"tag {tag!r} does not match packages/<pkg>/v<semver>")
    path, version = match.group(1), match.group(2)
    packages = {package.path: package for package in discover_packages(root)}
    package = packages.get(path)
    if package is None:
        fail(f"tag names {path}, which is not a workspace package")
    problems = []
    pyproject = tomllib.loads((package.directory / "pyproject.toml").read_text("utf-8"))
    declared = str(pyproject.get("project", {}).get("version", ""))
    if declared != version:
        problems.append(f"tag says {version}, pyproject.toml says {declared}")
    changelog = package.directory / "CHANGELOG.md"
    body = changelog.read_text("utf-8") if changelog.is_file() else ""
    if f"## {version}" not in body and f"## [{version}]" not in body:
        problems.append(f"CHANGELOG.md has no '## {version}' entry")
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
        current = _python.current_version(package)
        derived = _cliff.bumped_version(root, package)
        if not derived or derived == current:
            # git-cliff answers with the current version when nothing
            # unreleased touches the package. Releasing it again would
            # republish the same code under a version the index
            # already has.
            print(f"  nothing to release: no unreleased commits touch {path}")
            return []
        version = derived
        entry_body = _cliff.unreleased_entry(root, package, version)
        print(f"  derived {version} from the commits since {current}")
    if not _SEMVER_RE.fullmatch(version):
        fail(f"version {version!r} is not <major>.<minor>.<patch>")
    changed = _python.stamp_version(package).stamp(version)
    changelog = package.directory / "CHANGELOG.md"
    text = changelog.read_text("utf-8") if changelog.is_file() else "# Changelog\n"
    if f"## {version}" not in text and f"## [{version}]" not in text:
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
    return changed


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


def _run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    )


def _git_or_fail(cwd: Path, *args: str) -> str:
    result = _run_git(cwd, *args)
    if result.returncode != 0:
        fail(
            f"git {' '.join(args)} exited {result.returncode}:"
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
            if diff.returncode == 0:
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


@release.task(name="templates", hidden=True)
def release_templates(
    version: Annotated[str, doc("the workshop version being released")],
    remote: Annotated[str, doc("artifact repository url")] = TEMPLATES_REMOTE,
) -> None:
    """Publish the template snapshot for one workshop release.

    Runs from the tagged checkout in the release workflow, with the
    deploy key in the ssh agent; ``templates/`` becomes the artifact
    repository's tree, tagged ``v<version>`` in lockstep with
    ``packages/workshop/v<version>``.
    """
    root = _root()
    outcome = publish_templates(
        root / "templates",
        version,
        remote,
        author="livery release train <mail@willem.net>",
    )
    print(f"  {outcome}")
