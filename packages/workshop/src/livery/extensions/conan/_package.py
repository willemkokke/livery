"""A conan package's recipe, its build into the conan cache, and its release.

The recipe, ``conanfile.py``, declares the version and the
requirements. ``conan create`` builds the package into the conan
cache. A release saves the cache per host and uploads it to a conan
remote, or attaches it to the member's release on a forge that hosts no
conan registry, and a consumer restores it from there. The workspace's
conan members resolve one another from their sources through the conan
workspace file at the root (``WORKSPACE_FILE``).
"""

from __future__ import annotations

import json
import os
import platform
import re
import tempfile
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import livery.toolroom.tools as tools
from livery.footman import fail

if TYPE_CHECKING:
    from livery.forge import Repository
    from livery.toolroom.tools import Result
    from livery.workshop._packages import Package
    from livery.workshop._registries import RegistryTarget


def conan_requirements(conanfile: Path) -> dict[str, str]:
    """The conan references a recipe declares, name to version text.

    Reads the ``requires`` class attribute (a string or a tuple) and
    ``self.requires("...")`` calls, without importing conan: the
    recipe is parsed as source. ``"acme-native/[>=0.1.0]"`` answers
    ``{"acme-native": "[>=0.1.0]"}``.
    """
    import ast

    refs: list[str] = []
    tree = ast.parse(conanfile.read_text("utf-8"))

    def _constants(node: ast.AST) -> list[str]:
        found = []
        for inner in ast.walk(node):
            if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                found.append(inner.value)
        return found

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "requires":
                    refs.extend(_constants(node.value))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "requires"
        ):
            refs.extend(_constants(node.args[0]) if node.args else [])
    entries: dict[str, str] = {}
    for ref in refs:
        name, _, version = ref.partition("/")
        if name and version:
            entries[name] = version
    return entries


def declare_requirement(package: Package, dependency: Package, floor: str) -> list[str]:
    """Add ``<dependency>/[>=<floor>]`` to the recipe's requires; the files changed.

    The reference joins the ``requires`` tuple the template seeds,
    as text, so the recipe's own layout stays. A recipe that declares
    its requirements another way (``self.requires`` calls, no tuple)
    refuses naming the line to add, rather than guessing where a
    method body wants it. Already declared means nothing to write.
    """
    import re as _re

    conanfile = package.directory / "conanfile.py"
    if dependency.name in conan_requirements(conanfile):
        return []
    text = conanfile.read_text("utf-8")
    reference = f'"{dependency.name}/[>={floor}]"'
    match = _re.search(r"^(\s*)requires = \((.*?)\)$", text, flags=_re.M | _re.S)
    if match is None:
        fail(
            f"{conanfile} declares no `requires = (...)` tuple to add"
            f" {reference} to; add the requirement by hand"
        )
    indent, inner = match.group(1), match.group(2).strip()
    items = [item.strip() for item in inner.split(",") if item.strip()]
    items.append(reference)
    body = ", ".join(items) + ("," if len(items) == 1 else "")
    text = text[: match.start()] + f"{indent}requires = ({body})" + text[match.end() :]
    conanfile.write_text(text, encoding="utf-8")
    return ["conanfile.py"]


def declared_requirements(package: Package) -> dict[str, str]:
    """The conan references the package's recipe declares.

    The layering lint compares these against the contract's
    ``[[depends]]`` edges; a package without a conanfile declares
    nothing.
    """
    conanfile = package.directory / "conanfile.py"
    if not conanfile.is_file():
        return {}
    return conan_requirements(conanfile)


def current_version(package: Package) -> str:
    """The version the recipe's ``version`` attribute declares."""
    text = (package.directory / "conanfile.py").read_text("utf-8")
    match = re.search(r'^\s*version = "([^"]+)"$', text, re.M)
    return match.group(1) if match else "0.0.0"


def stamp_version(package: Package) -> _Stamper:
    """Where a conan package's version lives, ready to stamp."""
    return _Stamper(package)


class _Stamper:
    """Stamp a version into the recipe's ``version``, idempotently."""

    def __init__(self, package: Package) -> None:
        self._package = package

    def homes(self) -> list[Path]:
        """The recipe, the one file the version lives in."""
        return [self._package.directory / "conanfile.py"]

    def stamp(self, version: str) -> list[str]:
        """Write *version* into ``conanfile.py``; what changed."""
        conanfile = self._package.directory / "conanfile.py"
        text = conanfile.read_text("utf-8")
        stamped, count = re.subn(
            r'^(\s*)version = "[^"]+"$',
            rf'\g<1>version = "{version}"',
            text,
            count=1,
            flags=re.M,
        )
        if count != 1:
            fail(f"{conanfile} has no version line to stamp")
        if stamped == text:
            return []
        conanfile.write_text(stamped, encoding="utf-8")
        return ["conanfile.py"]


def _conan(package_dir: Path, *args: str, env: dict[str, str] | None = None) -> Result:
    """One conan invocation through the store's handle.

    *env* adds to this process's environment rather than replacing
    it: a handle passes what it is given as the child's whole
    environment, never a merge over the parent's.

    Raises:
        Failed: when conan is not deployed on this machine, naming
            the sync that supplies it.
    """
    try:
        return tools.conan.opts(
            cwd=package_dir,
            env={**os.environ, **(env or {})},
            nofail=True,
            recorded=False,
        )(*args)
    except OSError:
        from livery.workshop._tools import undeployed

        undeployed("conan")


#: The remote name the workshop configures on the conan client. One
#: name, always re-pointed at the resolved target, so a stale remote
#: from an earlier workspace cannot swallow an upload.
CONAN_REMOTE = "workshop"


#: How a saved cache is named, per package version and host: the
#: asset name on the release, and the file name in ``dist/``. The
#: host key is the store's (``linux-x64``), so a consumer asks for
#: its own host's file by name alone.
CACHE_NAME = "{name}-{version}-{host}.tgz"


def cache_name(name: str, version: str, host: str = "") -> str:
    """The saved-cache file name for *name* at *version* on *host*.

    *host* defaults to the machine running this, as the store keys
    hosts.
    """
    from livery.toolroom.store import host_key

    return CACHE_NAME.format(
        name=name,
        version=version,
        host=host or host_key(platform.system(), platform.machine()),
    )


#: The conan workspace file at the workspace root.
WORKSPACE_FILE = "conanws.yml"


def workspace_members(packages: Iterable[Package]) -> tuple[Package, ...]:
    """The members of *packages* whose kind packages them with conan, by path."""
    from livery.workshop._kinds import kind_for

    return tuple(
        sorted(
            (p for p in packages if kind_for(p.kind).artifact == "conan"),
            key=lambda p: p.path,
        )
    )


def workspace_file(members: Iterable[Package], *, root: Path | None = None) -> str:
    """The `WORKSPACE_FILE` that resolves *members* from their sources.

    Conan finds the file by walking up from the directory a command
    runs in, so a command inside the workspace resolves each member
    from its source tree, and one outside resolves it from the cache
    or a remote. Nothing is registered in the conan home, so a
    workspace leaves nothing behind, and each checkout of a repository
    resolves to its own sources. An entry names its member by path
    alone: conan reads the reference from the recipe's ``name`` and
    ``version``, so the file stays right when a release stamps the
    version.

    Args:
        members: The conan members, in the order the file lists them.
        root: The workspace root, for a file placed outside it: each
            path is then the member's absolute POSIX path. A Linux
            build container mounts the root at its host path and reads
            such a file beside the package it builds. None writes the
            paths relative to the workspace root, for the file the
            root holds.
    """
    lines = [
        "# The conan workspace: a conan command run inside this folder",
        "# resolves each package below from its source tree. Rendered from",
        "# the members whose kind packages with conan; the gate keeps it",
        "# matching.",
        "packages:",
        *(
            f"  - path: {member.path}"
            if root is None
            else f"  - path: {(root / member.path).as_posix()}"
            for member in members
        ),
    ]
    return "\n".join(lines) + "\n"


def root_files(members: tuple[Package, ...]) -> dict[str, str]:
    """The files the kind writes at the root: the conan workspace over *members*."""
    return {WORKSPACE_FILE: workspace_file(members)}


@contextmanager
def workspace_aside(root: Path) -> Generator[None]:
    """Hide *root*'s conan workspace for the block, so members resolve from the cache.

    A release leg builds what the release ships: a member's
    ``conan create`` and an extension's wheel resolve a sibling to the
    package the leg created, never to its source tree. Conan finds the
    workspace from the directory a command runs in and has no switch
    to ignore it, so the file moves aside for the block and comes back
    after it, a failure included. A leg killed inside the block leaves
    the file aside, and the next ``sync`` writes it again.
    """
    path = root / WORKSPACE_FILE
    if not path.is_file():
        yield
        return
    aside = path.with_name(f"{WORKSPACE_FILE}.aside")
    os.replace(path, aside)
    try:
        yield
    finally:
        os.replace(aside, path)


def save_cache(package: Package, version: str, into: Path) -> Path:
    """Save this host's built package out of the conan cache; the file.

    The release matrix runs one leg per wheel platform: each leg
    creates the package for its own host and saves it here, the
    collection gathers every leg's file, and the wave attaches them
    all to the member's release. ``conan cache restore`` reads one
    back on any machine.

    Raises:
        Failed: when conan refuses the save; the message carries its
            output.
    """
    into.mkdir(parents=True, exist_ok=True)
    archive = into / cache_name(package.name, version)
    result = _conan(
        package.directory,
        "cache",
        "save",
        f"{package.name}/{version}:*",
        "--file",
        str(archive),
    )
    if result.code != 0:
        fail(
            f"conan cache save ({package.name}/{version}) exited"
            f" {result.code}:\n{result.stdout[-3000:]}{result.stderr[-2000:]}"
        )
    return archive


def publish_artifact(
    package: Package, root: Path, *, version: str, target: RegistryTarget
) -> bool:
    """The kind's publish seam: the package to the resolved target.

    Three routes, by what the ladder resolved: the forge's own
    releases, a folder, or a conan remote. On the remote route conan
    logs in with the target's token, the forge lane's token for a
    forge's own registry, unless conan's own variables are set
    (``publish``).
    """
    if target.releases:
        return publish_to_releases(package, root, version=version)
    return publish(
        package, target.url, version=version, local=target.local, token=target.token
    )


def changelog_entry(package: Package, version: str) -> str:
    """The changelog section for *version*; empty when there is none.

    The body of the release the caches ride on, so a person reading
    the release page sees what changed, not a bare tag.
    """
    changelog = package.directory / "CHANGELOG.md"
    if not changelog.is_file():
        return ""
    lines = changelog.read_text("utf-8").splitlines()
    heads = [
        index
        for index, line in enumerate(lines)
        if line.startswith("## ") and version in line
    ]
    if not heads:
        return ""
    start = heads[0] + 1
    end = next(
        (index for index in range(start, len(lines)) if lines[index].startswith("## ")),
        len(lines),
    )
    return "\n".join(lines[start:end]).strip()


def publish_to_releases(package: Package, root: Path, *, version: str) -> bool:
    """Attach every host's saved cache to the member's own release.

    The route for a forge that hosts no conan registry: the release
    of the receipt tag is the package's address, and its assets are
    the per-host caches the matrix legs saved. Idempotent in both
    halves: an existing release is used, and an asset already
    attached is walked past, so a re-run after a half-finished wave
    completes it.

    Returns:
        True when this call attached anything; False when the
        release already carried every cache.

    Raises:
        Failed: when ``dist/`` holds no saved cache for *version*,
            which means the matrix legs never fed this wave, and
            when the forge refuses. The forge's own words carry, and
            the message names the re-run, which resumes where this
            one stopped.
    """
    from livery.forge import ForgeError
    from livery.workshop._forge_lane import this_repository

    tag = f"{package.path}/v{version}"
    caches = sorted(
        (package.directory / "dist").glob(f"{package.name}-{version}-*.tgz")
    )
    if not caches:
        fail(
            f"{package.name}: no saved conan cache in dist/ for {version}."
            " Each wheel platform's leg saves one"
            f" ({cache_name(package.name, version)}) and the collection"
            " brings them here, so this wave ran without the matrix."
        )
    repository = this_repository(root)
    uploaded = False
    try:
        if repository.release.get(tag) is None:
            repository.release.create(
                tag,
                name=f"{package.name} {version}",
                body=changelog_entry(package, version),
            )
        attached = {asset.name for asset in repository.release.assets(tag)}
        for cache in caches:
            if cache.name in attached:
                print(f"  {package.name}: {cache.name} already attached; walking past")
                continue
            repository.release.upload_asset(
                tag, cache.name, cache.read_bytes(), content_type="application/gzip"
            )
            print(f"  {package.name}: {cache.name} attached to {tag}")
            uploaded = True
    except ForgeError as error:
        fail(
            f"{package.name}: the forge refused the release {tag}: {error}."
            " Re-run the publish: the tag is cut, the release is reused,"
            " and each cache already attached is walked past."
        )
    return uploaded


def publish(
    package: Package, target_url: str, *, version: str, local: bool, token: str = ""
) -> bool:
    """Upload the recipe to the resolved target; False when already there.

    A remote target gets ``conan upload`` through the ``workshop``
    remote (re-pointed at *target_url* first, so the name never
    drifts). A folder target gets ``conan cache save``: the saved
    tarball lands as ``<dir>/<name>-<version>.tgz``, and
    ``conan cache restore`` reads it back on any machine.

    The upload logs in with *token* when conan's own variables are
    unset: ``CONAN_LOGIN_USERNAME`` and ``CONAN_PASSWORD``, or the
    pair scoped to the ``workshop`` remote. Set, they win. With
    neither, the upload is anonymous, and the refusal teaches the
    variables when the remote wants a login.
    """
    ref = f"{package.name}/{version}"
    if local:
        directory = Path(target_url)
        directory.mkdir(parents=True, exist_ok=True)
        archive = directory / f"{package.name}-{version}.tgz"
        result = _conan(
            package.directory,
            "cache",
            "save",
            f"{ref}:*",
            "--file",
            str(archive),
        )
        if result.code != 0:
            fail(
                f"conan cache save ({ref}) exited {result.code}:\n"
                f"{result.stdout[-3000:]}{result.stderr[-2000:]}"
            )
        return True
    added = _conan(
        package.directory,
        "remote",
        "add",
        CONAN_REMOTE,
        target_url,
        "--force",
    )
    if added.code != 0:
        fail(
            f"conan remote add {CONAN_REMOTE} {target_url} exited"
            f" {added.code}:\n{added.stdout}{added.stderr}"
        )
    result = _conan(
        package.directory,
        "upload",
        ref,
        "-r",
        CONAN_REMOTE,
        "--confirm",
        env=_login_env(token),
    )
    if result.code != 0:
        output = f"{result.stdout}{result.stderr}"
        if "already" in output.lower() and "exist" in output.lower():
            print(f"  {package.name}: already uploaded; walking past")
            return False
        hint = ""
        if "401" in output or "auth" in output.lower():
            hint = (
                "\n  the remote wants credentials: set"
                " CONAN_LOGIN_USERNAME and CONAN_PASSWORD (conan's own"
                " variables) and re-run"
            )
        fail(f"conan upload ({ref}) exited {result.code}:\n{output[-3000:]}{hint}")
    return True


#: The user conan logs in as with a registry's token. A forge's own
#: registry authenticates the token and ignores the name.
TOKEN_USER = "workshop"


def _login_env(token: str) -> dict[str, str]:
    """Conan's login for the ``workshop`` remote from *token*; empty to add none.

    Empty when there is no token, and when conan's own variables are
    set, generic or scoped to the remote: those win. Otherwise the
    pair scoped to the remote, so no other remote sees the token.
    """
    scope = CONAN_REMOTE.upper()
    own = (
        "CONAN_LOGIN_USERNAME",
        "CONAN_PASSWORD",
        f"CONAN_LOGIN_USERNAME_{scope}",
        f"CONAN_PASSWORD_{scope}",
    )
    if not token or any(os.environ.get(name) for name in own):
        return {}
    return {
        f"CONAN_LOGIN_USERNAME_{scope}": TOKEN_USER,
        f"CONAN_PASSWORD_{scope}": token,
    }


class ConanRegistry:
    """The wave's probe against a conan target; fits the Registry seam.

    Three routes, matching the publisher's: the forge's releases
    answer from each member's release assets, a remote answers from
    ``conan list``, and a folder answers from the saved tarball
    names in the directory.
    """

    def __init__(self, target: RegistryTarget, *, root: Path) -> None:
        self._target = target
        self._url = target.url
        self._local = target.local
        self._cwd = root
        self._root = root
        self._repository: Repository | None = None

    def versions(self, name: str) -> tuple[str, ...]:
        """The published versions of *name* at the target."""
        if self._target.releases:
            return self._released_versions(name)
        if self._local:
            directory = Path(self._url)
            prefix = f"{name}-"
            return tuple(
                sorted(
                    path.name[len(prefix) : -len(".tgz")]
                    for path in directory.glob(f"{prefix}*.tgz")
                )
            )
        import json

        _conan(self._cwd, "remote", "add", CONAN_REMOTE, self._url, "--force")
        result = _conan(
            self._cwd,
            "list",
            f"{name}/*",
            "-r",
            CONAN_REMOTE,
            "--format=json",
        )
        if result.code != 0:
            return ()
        try:
            data = json.loads(result.stdout)
        except ValueError:
            return ()
        listed = data.get(CONAN_REMOTE, {})
        versions = []
        for ref in listed:
            _, _, version = str(ref).partition("/")
            if version:
                versions.append(version)
        return tuple(sorted(versions))

    def _released_versions(self, name: str) -> tuple[str, ...]:
        """The versions whose release carries a cache for *name*.

        A tag alone is not a served version here: the wave pushes the
        tag before it uploads, so the proof is an asset. A release
        with no cache attached reads as not served, and the wave's
        re-run finishes it.
        """
        path = member_path(self._root, name)
        if not path:
            return ()
        # One connection for the probe's whole life: the wave polls
        # this in a loop until the artifact is served.
        if self._repository is None:
            from livery.workshop._forge_lane import this_repository

            self._repository = this_repository(self._root)
        repository = self._repository
        prefix = f"{path}/v"
        versions = []
        for tag in repository.tags(prefix=prefix):
            version = tag[len(prefix) :]
            if repository.release.get(tag) is None:
                continue
            wanted = f"{name}-{version}-"
            if any(
                asset.name.startswith(wanted)
                for asset in repository.release.assets(tag)
            ):
                versions.append(version)
        return tuple(sorted(versions))


def member_path(root: Path, name: str) -> str:
    """The workspace path of the member named *name*; empty when none is.

    Names the member whose releases carry a conan package, so a
    consumer turns a requirement's name into the tag that holds it.
    """
    from livery.workshop._packages import discover_packages

    for package in discover_packages(root):
        if package.name == name:
            return package.path
    return ""


def restore_from_releases(root: Path, name: str, version: str, *, cwd: Path) -> None:
    """Restore *name* at *version* from its release into the conan cache.

    The consumer's half of the releases route: the member's release
    is read for this host's saved cache, the bytes are checked
    against the digest the forge reports, and ``conan cache
    restore`` puts the package where a build resolves it.

    Raises:
        Failed: when the member is unknown here, the release carries
            no cache for this host, or the bytes do not match the
            digest.
    """
    from livery.workshop._forge_lane import this_repository

    path = member_path(root, name)
    if not path:
        fail(
            f"{name} is not a member of this workspace, so its conan"
            " package has no release here; declare a conan remote that"
            " serves it"
        )
    tag = f"{path}/v{version}"
    wanted = cache_name(name, version)
    repository = this_repository(root)
    assets = repository.release.assets(tag)
    match = next((asset for asset in assets if asset.name == wanted), None)
    if match is None:
        carried = ", ".join(sorted(asset.name for asset in assets)) or "nothing"
        fail(
            f"release {tag} carries no conan cache for this host: wanted"
            f" {wanted}, it carries {carried}. Release {name} {version}"
            " from a wave that ran this host's wheel platform."
        )
    data = repository.release.download_asset(tag, wanted)
    verify_digest(wanted, data, match.digest)
    with tempfile.TemporaryDirectory() as scratch:
        archive = Path(scratch) / wanted
        archive.write_bytes(data)
        result = _conan(cwd, "cache", "restore", str(archive))
        if result.code != 0:
            fail(
                f"conan cache restore ({wanted}) exited {result.code}:\n"
                f"{result.stdout[-3000:]}{result.stderr[-2000:]}"
            )
    print(f"  conan: {name}/{version} restored from {tag}")


def verify_digest(name: str, data: bytes, digest: str) -> None:
    """Check *data* against the forge's *digest*; a mismatch refuses.

    An empty digest is a forge that reports none: the bytes are used
    and the line says they were not checked, which is the honest
    state rather than a silent pass.

    Raises:
        Failed: when the digest is a sha256 the bytes do not match,
            or names an algorithm this does not know.
    """
    import hashlib

    if not digest:
        print(f"  conan: {name} carries no digest on the release; not verified")
        return
    algorithm, _, expected = digest.partition(":")
    if algorithm != "sha256":
        fail(
            f"{name}: the release reports a {algorithm or 'nameless'} digest,"
            " and the restore verifies sha256; upload it again from a"
            " forge that reports one"
        )
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        fail(
            f"{name}: the downloaded cache does not match the release's"
            f" digest (wanted {expected}, read {actual}); the release asset"
            " changed under the tag"
        )


def ensure_profile(cwd: Path) -> list[str]:
    """Make conan's default profile name a compiler; a line when it was replaced.

    `conan create` refuses a profile that names no compiler. The
    cmake-conan provider writes the default profile itself when there
    is none, from inside CMake's configure, where conan's detection
    finds no compiler, and every later conan call keeps that profile.
    So a missing profile is detected here before anything configures,
    and one that names no compiler is detected again, which replaces
    it. A profile that names a compiler is kept as it is.

    Returns:
        The line saying the profile was detected again; empty when it
        named a compiler already, or was missing and is detected now.

    Raises:
        Failed: when conan cannot detect or show the profile, in its
            own words, or when detection finds no compiler here.
    """
    _detect_profile(cwd, "--exist-ok")
    if "compiler" in _host_settings(cwd):
        return []
    _detect_profile(cwd, "--force")
    settings = _host_settings(cwd)
    compiler = settings.get("compiler")
    if compiler is None:
        fail(
            "conan's default profile names no compiler, and `conan profile"
            " detect` found none on this machine: install a C and C++"
            " compiler, or name one with CC and CXX, then re-run"
        )
    named = f"{compiler} {settings.get('compiler.version', '')}".rstrip()
    return [f"  conan: the default profile named no compiler; detected again: {named}"]


def _detect_profile(cwd: Path, mode: str) -> None:
    """Run ``conan profile detect`` with *mode*, ``--exist-ok`` or ``--force``."""
    result = _conan(cwd, "profile", "detect", mode)
    if result.code != 0:
        fail(
            f"conan profile detect exited {result.code}:\n"
            f"{result.stdout}{result.stderr}"
        )


def _host_settings(cwd: Path) -> dict[str, str]:
    """The host settings of conan's default profile, read from its JSON."""
    result = _conan(cwd, "profile", "show", "--format=json")
    if result.code != 0:
        fail(
            f"conan profile show exited {result.code}:\n{result.stdout}{result.stderr}"
        )
    settings: dict[str, str] = json.loads(result.stdout)["host"]["settings"]
    return settings


def build(package: Package, root: Path, *, epoch: int = 0) -> Path:
    """Package *package* through ``conan create``; the conan cache dir.

    ``conan create`` exports the recipe, builds in the cache, and
    packages the result there; publishing uploads from the cache, so
    nothing lands in a ``dist/`` directory. Conan's default profile is
    made to name a compiler first (``ensure_profile``). Returns the
    package's ``build`` directory, for the caller to inspect. Every
    backend's build takes *epoch*; conan writes its own metadata, so
    this one does not read it.
    """
    del root, epoch
    for line in ensure_profile(package.directory):
        print(line)
    result = _conan(package.directory, "create", ".")
    if result.code != 0:
        fail(
            f"conan create ({package.name}) exited {result.code}:\n"
            f"{result.stdout[-4000:]}{result.stderr[-2000:]}"
        )
    return package.directory / "build"
