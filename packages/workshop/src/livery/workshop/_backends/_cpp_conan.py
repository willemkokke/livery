"""The C/C++ backend: the build callables for ``type = "cpp-conan"``.

The gate verb configures and builds with cmake and ninja and runs
the ctest suite through the generated ``test`` target. Packaging,
the saved caches, and the editable registrations go through conan.

Every tool this module runs is a tool of the store, reached through
its toolroom handle, so the version is the one this checkout's lock
pins. A handle spawns its tool by name, so a machine that never
deployed one raises ``OSError``, and ``_undeployed`` turns that into
the sync command. The one tool reached without a handle is the host
compiler in ``_toolchain_arguments``: a ``host_tool`` is by contract
the machine's own, and the store never installs it.
"""

from __future__ import annotations

import functools
import os
import platform
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn
from xml.etree import ElementTree

import livery.footman as footman
from livery.footman import fail
from livery.toolroom import tools

if TYPE_CHECKING:
    from livery.forge import Repository
    from livery.toolroom.tools import Result
    from livery.workshop._packages import Neighbours, Package
    from livery.workshop._registries import RegistryTarget

#: Where the gate's cmake configure lands, under the package.
#: conan's own cmake_layout also builds under build/, so one
#: gitignore entry covers both.
GATE_BUILD_DIR = "build/gate"

#: The include the gate's configure hands CMake, shipped beside this
#: module: the instrumentation each compiler family takes, so the
#: measurer beside the compiler can read the run.
COVERAGE_CMAKE = Path(__file__).with_name("coverage.cmake")

#: Where an instrumented test run leaves its profiles and reports,
#: under the gate build.
PROFILE_DIR = "coverage"

#: The shell that reads the batch file MSVC's environment comes from:
#: a Windows component, never a tool of the store.
SHELL = "cmd.exe"

#: Where the Visual Studio installer keeps vswhere, under the 32-bit
#: program files directory: the one path Microsoft documents as stable
#: across versions and editions.
VSWHERE = ("Microsoft Visual Studio", "Installer", "vswhere.exe")

#: Per host architecture, the Visual Studio component that is the C++
#: build tools for it and the batch file that enters their
#: environment; the x64 pair serves every architecture not named.
MSVC_TOOLS = {
    "ARM64": ("Microsoft.VisualStudio.Component.VC.Tools.ARM64", "vcvarsarm64.bat"),
}
MSVC_TOOLS_X64 = ("Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "vcvars64.bat")


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


def module_roots(package: Package) -> tuple[str, ...]:
    """Nothing: a conan package is referenced by recipe name, not import.

    The name a consumer writes is the recipe's, which the conanfile
    already declares and ``declared_requirements`` already reads.
    """
    del package
    return ()


def referenced_siblings(package: Package, around: Neighbours) -> dict[str, str]:
    """Nothing: reading a recipe's own sources for references is unwritten.

    The answer this kind owes is an ``#include`` of a header belonging
    to another workspace package with no matching ``requires`` in the
    conanfile. Until it is written the lint finds nothing here, which
    is silence rather than a pass.
    """
    del package, around
    return {}


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
    import re

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

    def stamp(self, version: str) -> list[str]:
        """Write *version* into ``conanfile.py``; what changed."""
        import re

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


def _undeployed(name: str) -> NoReturn:
    """Refuse: *name* is a tool of the store this machine has not deployed.

    Every handle in this module spawns its tool by name, which the
    entered environment puts on PATH. A machine that never deployed
    the tool raises ``OSError`` from the spawn instead, and a
    traceback names nothing a person can do, so each call turns one
    into this sentence.
    """
    fail(
        f"{name} is not on PATH: it is a tool of the store, so enter the"
        f" environment (`{footman.prog()} sync`, then the printed"
        " env.emit line) and re-run"
    )


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
        _undeployed("conan")


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


def forget_editable(package: Package) -> None:
    """Unregister *package* as editable; silent when it was not one.

    The workspace registers every conan member editable so a sibling
    compiles against its source at HEAD. A leg that builds what the
    release ships must resolve the created package instead, and an
    editable registration outranks the cache.
    """
    result = _conan(package.directory, "editable", "remove", str(package.directory))
    if result.code == 0:
        print(f"  {package.name}: editable registration removed for this leg")


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
    releases, a folder, or a conan remote. Conan credentials stay
    conan-native on the remote route (``CONAN_LOGIN_USERNAME`` and
    ``CONAN_PASSWORD``), resolved by the tool itself, so the
    target's token is unused there.
    """
    if target.releases:
        return publish_to_releases(package, root, version=version)
    return publish(package, target.url, version=version, local=target.local)


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


def publish(package: Package, target_url: str, *, version: str, local: bool) -> bool:
    """Upload the recipe to the resolved target; False when already there.

    A remote target gets ``conan upload`` through the ``workshop``
    remote (re-pointed at *target_url* first, so the name never
    drifts). A folder target gets ``conan cache save``: the saved
    tarball lands as ``<dir>/<name>-<version>.tgz``, and
    ``conan cache restore`` reads it back on any machine.
    Credentials stay conan-native: ``CONAN_LOGIN_USERNAME`` and
    ``CONAN_PASSWORD``, taught by the refusal when the remote wants
    them.
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
    result = _conan(package.directory, "upload", ref, "-r", CONAN_REMOTE, "--confirm")
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
        for tag in repository.tags():
            if not tag.startswith(prefix):
                continue
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
    import tempfile

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


#: The extensions of a C or C++ test source under ``tests/``.
TEST_SOURCES = (".cpp", ".cc", ".cxx", ".c")

#: The extensions of a C or C++ source or header: what clang-format
#: walks, and what a native check's claims admit.
SOURCE_SUFFIXES = (*TEST_SOURCES, ".hpp", ".h", ".hxx")


def toolchain_env() -> dict[str, str]:
    """The environment the gate's build tools run in.

    This process's environment, entered into MSVC's on Windows: with
    no ``CXX`` set and no ``cl`` on PATH, vswhere names the newest
    Visual Studio that has the C++ build tools for this architecture,
    and the environment its vcvars batch file leaves is read back
    through ``set``. ``CC`` and ``CXX`` then name ``cl``, so CMake
    takes MSVC over a MinGW gcc that is also on PATH: on Windows the
    gate builds with MSVC unless ``CXX`` says otherwise, and a person
    who sets it chooses. Read once per process; a copy each call.

    Raises:
        Failed: on Windows with no compiler chosen and no Visual
            Studio installation with the C++ build tools to enter.
    """
    return dict(_entered())


@functools.cache
def _entered() -> dict[str, str]:
    """The environment `toolchain_env` copies, read once per process."""
    env = dict(os.environ)
    if not _on_windows() or env.get("CXX"):
        return env
    if not shutil.which("cl", path=env.get("PATH", "")):
        env = _msvc_environment(env)
    return {**env, "CC": env.get("CC") or "cl", "CXX": "cl"}


def _on_windows() -> bool:
    """Whether this is Windows, where the gate enters MSVC's environment itself."""
    return sys.platform == "win32"


def vswhere_path(env: dict[str, str] | None = None) -> Path:
    """Where this machine keeps vswhere, present or not.

    Under the 32-bit program files directory *env* names, this
    process's when *env* is None; the key is read in both spellings,
    since Windows upper-cases the ones a process inherits.
    """
    variables = dict(os.environ) if env is None else env
    program_files = (
        variables.get("PROGRAMFILES(X86)")
        or variables.get("ProgramFiles(x86)")
        or r"C:\Program Files (x86)"
    )
    return Path(program_files).joinpath(*VSWHERE)


def _msvc_environment(env: dict[str, str]) -> dict[str, str]:
    """*env* entered into the newest Visual Studio's C++ build tools.

    Raises:
        Failed: when vswhere is not installed, names no installation
            with the C++ build tools for this architecture, or the
            tools' batch file leaves no toolset in the environment.
    """
    vswhere = vswhere_path(env)
    if not vswhere.is_file():
        fail(
            "no C++ compiler is on PATH and Visual Studio's installer is not at"
            f" {vswhere}; install the Build Tools with the C++ workload, or set"
            " CXX to the compiler to build with"
        )
    component, batch = MSVC_TOOLS.get(platform.machine().upper(), MSVC_TOOLS_X64)
    installation = _asked(
        [
            str(vswhere),
            "-latest",
            "-products",
            "*",
            "-requires",
            component,
            "-property",
            "installationPath",
        ]
    )
    if not installation:
        fail(
            "no C++ compiler is on PATH and no Visual Studio installation has the"
            f" C++ build tools ({component}); install the workload, or set CXX to"
            " the compiler to build with"
        )
    script = Path(installation) / "VC" / "Auxiliary" / "Build" / batch
    entered = _from_set_output(_shell_set(f'@call "{script}" >nul\n@set\n'))
    if "VCTOOLSINSTALLDIR" not in entered:
        fail(
            f"{script} left no VCToolsInstallDir in the environment, so the C++"
            f" build tools of {installation} cannot be entered; repair the"
            " installation, or set CXX to the compiler to build with"
        )
    return entered


def _shell_set(script: str) -> str:
    """What ``set`` prints after *script* ran in cmd.exe; empty when it will not run."""
    with tempfile.TemporaryDirectory(prefix="workshop-msvc-") as home:
        batch = Path(home) / "enter.cmd"
        batch.write_text(script, encoding="utf-8")
        try:
            answer = footman.run(
                [SHELL, "/d", "/c", str(batch)],
                nofail=True,
                recorded=False,
                timeout=120,
            )
        except (OSError, footman.TimedOut):
            return ""
    return (answer.stdout or "") if answer.code == 0 else ""


def _from_set_output(text: str) -> dict[str, str]:
    """The environment ``set`` printed, keys upper-cased as Windows compares them.

    cmd's hidden variables print with an empty key and are dropped,
    as is any line without ``=``.
    """
    parsed: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key:
            parsed[key.upper()] = value
    return parsed


def configure(package: Package) -> None:
    """Configure *package* into the gate's build directory.

    The dependency-free library needs no conan at gate time: cmake
    configures against the host toolchain with the Ninja generator
    and exports the compile commands clang-tidy reads, in the
    environment [livery.workshop._backends._cpp_conan.toolchain_env][]
    enters. A package that declares conan requirements gains a conan
    install step when the cross-kind dependency lands; today a
    missing generator file fails the configure with cmake's own
    message.
    """
    build_dir = package.directory / GATE_BUILD_DIR
    tools.cmake.opts(cwd=package.directory, env=toolchain_env())(
        "-S",
        ".",
        "-B",
        str(build_dir),
        "-G",
        "Ninja",
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
        f"-DCMAKE_PROJECT_INCLUDE={COVERAGE_CMAKE}",
    )


def compile(package: Package) -> None:
    """Build the configured *package*; incremental, so one edit costs that edit."""
    build_dir = package.directory / GATE_BUILD_DIR
    tools.cmake.opts(cwd=package.directory, env=toolchain_env())(
        "--build", str(build_dir)
    )


def gate_build(package: Package, root: Path) -> None:
    """Configure and build *package* into the gate's build directory.

    What the kind's tests run on, in one call, for the affected
    gate's test-only step; the two halves are the ``configure`` and
    ``build`` checks the kind registers.
    """
    del root
    configure(package)
    compile(package)


def test(
    package: Package,
    root: Path,
    *,
    selection: tuple[str, ...] = (),
    pages: tuple[str, ...] = (),
) -> None:
    """Run ctest over the gate build, measured: every test, or *selection*'s alone.

    A selected test file maps to the ctest named after its stem
    (``tests/test_acme.cpp`` runs ``test_acme``), which is how the
    template registers tests; a selection no ctest answers to is a
    refusal naming the rule. The run is measured by the family of
    the compiler CMake configured the gate build with: gcov and
    llvm-cov read the counters a green run left, and Microsoft's
    engine collects around the run itself. The lines reached land as
    the package's part at the workspace *root*; a red run leaves
    none, and a family without a measurer refuses by name, since a
    suite that ran unmeasured never passes as measured.
    """
    del pages  # no docs examples harness answers to a C++ kind
    from livery.workshop import _coverage_lines as lines_

    build_dir = package.directory / GATE_BUILD_DIR
    env = {
        **toolchain_env(),
        "CTEST_OUTPUT_ON_FAILURE": "1",
        **_fresh_profiles(package),
    }
    names = [Path(path).stem for path in selection]
    arguments = ["--test-dir", str(build_dir), "--output-on-failure"]
    if names:
        pattern = "^(" + "|".join(re.escape(name) for name in names) + ")$"
        arguments += ["-R", pattern]
    compiler_id, compiler = compiler_of(package)
    family = lines_.measurer_for(compiler_id)
    if family == "msvc":
        measured = _measure_msvc(package, arguments, env, names)
    else:
        _run_ctest(package, arguments, env, names)
        if not family:
            fail(
                f"{package.name}: the gate build's compiler"
                f" {compiler_id or 'is unknown'} has no coverage measurer; the"
                f" families are {', '.join(sorted(lines_.FAMILIES))}"
            )
        measured = (
            _measure_gcov(package, compiler)
            if family == "gcov"
            else _measure_llvm(package, compiler)
        )
    kept = lines_.within(lines_.relativise(measured, root), package)
    lines_.write_part(root, package.path, kept)
    print(f"  {package.name}: coverage: {len(kept)} file(s) measured by {family}")


def _run_ctest(
    package: Package, arguments: list[str], env: dict[str, str], names: list[str]
) -> None:
    """Run ctest with *arguments*; a red run or an unanswered selection refuses.

    ctest is an entry point of the cmake record, so the store deploys
    the two together and one handle each reaches them.
    """
    try:
        ran = tools.ctest.opts(
            cwd=package.directory, env=env, nofail=True, recorded=False
        )(*arguments)
    except OSError:
        _undeployed("ctest")
    if names and "No tests were found" in ran.stdout + ran.stderr:
        _no_ctest_named(package, names)
    if ran.code != 0:
        fail(
            f"{package.name}: ctest failed (exit {ran.code}):\n"
            f"{ran.stdout[-4000:]}{ran.stderr[-2000:]}"
        )


def _no_ctest_named(package: Package, names: list[str]) -> NoReturn:
    """Refuse a selection no ctest answers to, naming the rule that maps them."""
    fail(
        f"{package.name}: no ctest is named {', '.join(names)}; the cpp-conan"
        " kind maps a test file to the ctest of its stem"
        " (add_test(NAME <stem> ...)), so register it or run the suite"
    )


def _fresh_profiles(package: Package) -> dict[str, str]:
    """Clear the last run's counters and name where the next run's profiles land.

    gcov adds a run's counts to the ``.gcda`` files the build left,
    and llvm writes one ``.profraw`` per process wherever
    ``LLVM_PROFILE_FILE`` points, so both are cleared first and the
    run measures itself alone.
    """
    build_dir = package.directory / GATE_BUILD_DIR
    for stale in build_dir.rglob("*.gcda"):
        stale.unlink()
    profiles = build_dir / PROFILE_DIR
    profiles.mkdir(parents=True, exist_ok=True)
    for stale in profiles.glob("*.profraw"):
        stale.unlink()
    return {"LLVM_PROFILE_FILE": str(profiles / "%p-%m.profraw")}


def compiler_of(package: Package) -> tuple[str, str]:
    """The gate build's C++ compiler id and path, as CMake detected them.

    Read from the compiler file CMake writes on configure; both empty
    for a package whose gate build is not configured.
    """
    build_dir = package.directory / GATE_BUILD_DIR
    found = sorted(build_dir.glob("CMakeFiles/*/CMakeCXXCompiler.cmake"))
    if not found:
        return "", ""
    text = found[-1].read_text(encoding="utf-8", errors="replace")
    identity = re.search(r'set\(CMAKE_CXX_COMPILER_ID "([^"]*)"\)', text)
    compiler = re.search(r'set\(CMAKE_CXX_COMPILER "([^"]*)"\)', text)
    return (
        identity.group(1) if identity else "",
        compiler.group(1) if compiler else "",
    )


def _beside(compiler: str, *names: str) -> str:
    """The first of *names* beside *compiler*, then on PATH; empty when none is."""
    home = Path(compiler).parent if compiler else None
    for name in names:
        if home is not None:
            for candidate in (home / name, (home / name).with_suffix(".exe")):
                if candidate.is_file():
                    return str(candidate)
        found = shutil.which(name)
        if found:
            return found
    return ""


def _xcrun(name: str) -> str:
    """Where the SDK keeps *name*, on macOS; empty elsewhere or when it has none."""
    if sys.platform != "darwin":
        return ""
    return _asked(["xcrun", "--find", name])


def _measure_gcov(package: Package, compiler: str) -> dict[str, dict[int, int]]:
    """The lines gcov reads from the build's counters.

    A file is named as the compiler saw it; one relative to the build
    directory is made absolute there.
    """
    from livery.workshop import _coverage_lines as lines_

    build_dir = package.directory / GATE_BUILD_DIR
    data = sorted(build_dir.rglob("*.gcda"))
    if not data:
        fail(
            f"{package.name}: the test run left no .gcda counter under"
            f" {GATE_BUILD_DIR}; the build was configured without"
            f" {COVERAGE_CMAKE.name}, so configure it again"
        )
    suffix = re.search(r"-(\d+)$", Path(compiler).name)
    names = [f"gcov-{suffix.group(1)}"] if suffix else []
    gcov = _beside(compiler, *names, "gcov")
    if not gcov:
        fail(
            f"{package.name}: built with {compiler}, and no gcov is beside it or"
            " on PATH; install the compiler's gcov"
        )
    merged: dict[str, dict[int, int]] = {}
    for path in data:
        result = footman.run(
            [gcov, "--json-format", "--stdout", str(path)],
            cwd=build_dir,
            nofail=True,
            recorded=False,
        )
        if result.code != 0:
            fail(
                f"{package.name}: gcov exited {result.code} on {path.name}:\n"
                f"{result.stdout[-2000:]}{result.stderr[-2000:]}"
            )
        read = lines_.from_gcov_json(result.stdout)
        # gcov names a file as the compiler saw it, relative to the
        # build directory when the compile line was.
        absolute = {
            name if Path(name).is_absolute() else str(build_dir / name): counts
            for name, counts in read.items()
        }
        merged = lines_.merge(merged, absolute)
    return merged


def _measure_llvm(package: Package, compiler: str) -> dict[str, dict[int, int]]:
    """The lines llvm-cov reads from the run's profiles over the test executables."""
    from livery.workshop import _coverage_lines as lines_

    build_dir = package.directory / GATE_BUILD_DIR
    raws = sorted((build_dir / PROFILE_DIR).glob("*.profraw"))
    if not raws:
        fail(
            f"{package.name}: the test run left no .profraw under"
            f" {GATE_BUILD_DIR}/{PROFILE_DIR}; the build was configured without"
            f" {COVERAGE_CMAKE.name}, so configure it again"
        )
    profdata = _beside(compiler, "llvm-profdata") or _xcrun("llvm-profdata")
    cov = _beside(compiler, "llvm-cov") or _xcrun("llvm-cov")
    if not profdata or not cov:
        fail(
            f"{package.name}: built with {compiler}, and llvm-profdata or"
            " llvm-cov is not beside it, on PATH, or where xcrun looks; install"
            " the compiler's llvm tools"
        )
    merged = build_dir / PROFILE_DIR / "merged.profdata"
    result = footman.run(
        [profdata, "merge", "-sparse", *(str(raw) for raw in raws), "-o", str(merged)],
        nofail=True,
        recorded=False,
    )
    if result.code != 0:
        fail(
            f"{package.name}: llvm-profdata exited {result.code}:\n"
            f"{result.stdout[-2000:]}{result.stderr[-2000:]}"
        )
    objects = _test_objects(package)
    if not objects:
        fail(f"{package.name}: ctest names no test executable to read coverage from")
    argv = [cov, "export", "-format=lcov", f"-instr-profile={merged}", objects[0]]
    for extra in objects[1:]:
        argv += ["-object", extra]
    result = footman.run(argv, nofail=True, recorded=False)
    if result.code != 0:
        fail(
            f"{package.name}: llvm-cov exited {result.code}:\n"
            f"{result.stdout[-2000:]}{result.stderr[-2000:]}"
        )
    return lines_.from_lcov(result.stdout)


def _measure_msvc(
    package: Package, arguments: list[str], env: dict[str, str], names: list[str]
) -> dict[str, dict[int, int]]:
    """The lines Microsoft's engine reads from the ctest run it collects around.

    Each executable ctest names, and every shared library the build
    made, is instrumented statically into a copy that takes the
    original's place for the run and gives it back after, its symbols
    removed with it, so the build's own output is never instrumented
    twice. ctest runs under
    ``dotnet-coverage collect`` with child processes included, and
    its verdict is read from the JUnit report it writes, never from
    the collector's exit code, which is the collector's own.

    Raises:
        Failed: when ctest names no executable, an instrumentation
            or the collection fails, a test fails, or no report
            comes out; each names what happened.
    """
    from livery.workshop import _coverage_lines as lines_

    profiles = package.directory / GATE_BUILD_DIR / PROFILE_DIR
    objects = _test_objects(package)
    if not objects:
        fail(f"{package.name}: ctest names no test executable to instrument")
    ctest = _ctest_program()
    settings = profiles / "coverage.config"
    settings.write_text(_msvc_settings(objects), encoding="utf-8")
    report = profiles / "coverage.cobertura.xml"
    verdict = profiles / "ctest.xml"
    for stale in (report, verdict):
        stale.unlink(missing_ok=True)
    run_env = {
        **env,
        "DOTNET_COVERAGE_TELEMETRY_OPTOUT": "1",
        "DOTNET_COVERAGE_NOLOGO": "1",
    }
    swapped: list[tuple[Path, Path]] = []
    try:
        for name in objects:
            original = Path(name)
            instrumented = original.with_name(
                f"{original.stem}.instrumented{original.suffix}"
            )
            result = _dotnet_coverage(
                package,
                run_env,
                "instrument",
                "--nologo",
                "-s",
                str(settings),
                "-o",
                str(instrumented),
                str(original),
            )
            if result.code != 0 or not instrumented.is_file():
                fail(
                    f"{package.name}: dotnet-coverage could not instrument"
                    f" {original.name} (exit {result.code}):\n"
                    f"{result.stdout[-2000:]}{result.stderr[-2000:]}"
                )
            kept = original.with_name(f"{original.name}.uninstrumented")
            os.replace(original, kept)
            os.replace(instrumented, original)
            swapped.append((original, kept))
        result = _dotnet_coverage(
            package,
            run_env,
            "collect",
            "--nologo",
            "-s",
            str(settings),
            "-f",
            "cobertura",
            "-o",
            str(report),
            "--",
            ctest,
            *arguments,
            "--output-junit",
            str(verdict),
        )
    finally:
        for original, kept in swapped:
            os.replace(kept, original)
            # The engine writes the instrumented binary's own symbols
            # beside it (`<stem>.instrumented.pdb`), read during the
            # run; nothing of the instrumented copy outlives it.
            for extra in original.parent.glob(f"{original.stem}.instrumented.*"):
                extra.unlink()
    output = result.stdout + result.stderr
    _ctest_verdict(package, names, verdict, output)
    if result.code != 0:
        fail(
            f"{package.name}: dotnet-coverage exited {result.code} collecting the"
            f" run:\n{output[-4000:]}"
        )
    if not report.is_file():
        fail(
            f"{package.name}: dotnet-coverage wrote no report at {report}:\n"
            f"{output[-4000:]}"
        )
    return lines_.from_cobertura(report.read_text(encoding="utf-8"))


def _ctest_program() -> str:
    """Where ctest is, for a collector that spawns it by path.

    Raises:
        Failed: when ctest is not deployed, naming the sync that
            supplies it.
    """
    found = shutil.which("ctest")
    if not found:
        _undeployed("ctest")
    return found


def _dotnet_coverage(package: Package, env: dict[str, str], *args: str) -> Result:
    """One dotnet-coverage invocation through the store's handle.

    Raises:
        Failed: when the tool is not deployed: a workspace measured
            with MSVC requires it, and the refusal names the line.
    """
    try:
        return tools.dotnet_coverage.opts(
            cwd=package.directory, env=env, nofail=True, recorded=False
        )(*args)
    except OSError:
        fail(
            "dotnet-coverage is not on PATH: a cpp-conan package built with MSVC"
            ' is measured by it, so add "dotnet_coverage" to [tools] requires'
            f" in workshop.toml, run `{footman.prog()} tools.lock`, then"
            f" `{footman.prog()} sync` and the printed env.emit line"
        )


def _msvc_settings(objects: list[str]) -> str:
    """The engine's settings: native instrumentation on, the build's own modules alone.

    A module path is matched by its file name in any letter case,
    since the loader reports a path in the case it has, and the
    pattern stays inside the regular expression syntax every engine
    version reads: no inline flags.
    """
    names = "|".join(_any_case(Path(name).name) for name in objects)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<Configuration>\n"
        "  <CodeCoverage>\n"
        "    <EnableStaticNativeInstrumentation>True"
        "</EnableStaticNativeInstrumentation>\n"
        "    <EnableDynamicNativeInstrumentation>True"
        "</EnableDynamicNativeInstrumentation>\n"
        "    <EnableStaticManagedInstrumentation>False"
        "</EnableStaticManagedInstrumentation>\n"
        "    <EnableDynamicManagedInstrumentation>False"
        "</EnableDynamicManagedInstrumentation>\n"
        "    <UseVerifiableInstrumentation>False</UseVerifiableInstrumentation>\n"
        "    <AllowLowIntegrityProcesses>True</AllowLowIntegrityProcesses>\n"
        "    <CollectFromChildProcesses>True</CollectFromChildProcesses>\n"
        "    <ModulePaths>\n"
        "      <Include>\n"
        f"        <ModulePath>.*[\\\\/]({names})$</ModulePath>\n"
        "      </Include>\n"
        "    </ModulePaths>\n"
        "  </CodeCoverage>\n"
        "</Configuration>\n"
    )


def _any_case(text: str) -> str:
    """A regular expression matching *text* in any letter case, every flavour."""
    return "".join(
        f"[{char.lower()}{char.upper()}]" if char.isalpha() else re.escape(char)
        for char in text
    )


def _ctest_verdict(
    package: Package, names: list[str], report: Path, output: str
) -> None:
    """Refuse unless the JUnit report ctest wrote says every test ran and passed.

    A missing report means ctest never ran to its end, a count of
    zero means no test ran (a selection no ctest answers to, or a
    suite with none registered), and a failure fails the run with
    ctest's *output*.
    """
    if not report.is_file():
        fail(
            f"{package.name}: ctest left no report at {report.name}; its"
            f" output:\n{output[-4000:]}"
        )
    try:
        suite = ElementTree.parse(report).getroot()
    except ElementTree.ParseError as error:
        fail(f"{package.name}: ctest's report is not XML ({error})")
    tests = int(suite.get("tests", "0"))
    failures = int(suite.get("failures", "0"))
    if tests == 0:
        if names:
            _no_ctest_named(package, names)
        fail(f"{package.name}: ctest ran no test; register one with add_test")
    if failures:
        fail(
            f"{package.name}: ctest failed ({failures} of {tests} test(s)):\n"
            f"{output[-4000:]}"
        )


def _test_objects(package: Package) -> list[str]:
    """The executables ctest runs, then every shared library the build made."""
    import json

    build_dir = package.directory / GATE_BUILD_DIR
    try:
        listed = tools.ctest.opts(cwd=package.directory, nofail=True, recorded=False)(
            "--show-only=json-v1", "--test-dir", str(build_dir)
        )
    except OSError:
        _undeployed("ctest")
    objects: list[str] = []
    if listed.code == 0:
        try:
            tests = json.loads(listed.stdout).get("tests", [])
        except ValueError:
            tests = []
        for entry in tests:
            command = entry.get("command") if isinstance(entry, dict) else None
            if not isinstance(command, list) or not command:
                continue
            if Path(command[0]).is_file() and command[0] not in objects:
                objects.append(command[0])
    for suffix in (".so", ".dylib", ".dll"):
        objects += [
            str(path)
            for path in sorted(build_dir.rglob(f"*{suffix}"))
            if str(path) not in objects
        ]
    return objects


#: Where a package keeps the sources the two clang tools read.
SOURCE_DIRS = ("src", "include", "tests")


def sources(package: Package) -> list[Path]:
    """Every C and C++ file the package owns, sorted.

    The two clang tools read the same set: what the package wrote,
    never what a build wrote under `build/`.
    """
    found: list[Path] = []
    for name in SOURCE_DIRS:
        directory = package.directory / name
        if not directory.is_dir():
            continue
        for suffix in SOURCE_SUFFIXES:
            found.extend(directory.rglob(f"*{suffix}"))
    return sorted(found)


#: A clang-format violation line: the file, then its line and column,
#: then the complaint. The path is read up to the line number rather
#: than to the first colon, which on Windows is the drive letter.
_VIOLATION = re.compile(
    r"^(?P<path>.+?):\d+:\d+: (?:error|warning): code should be clang-formatted"
)


def unformatted(output: str) -> list[str]:
    """The files clang-format would rewrite, named once each, sorted."""
    found = {
        match["path"]
        for line in output.splitlines()
        if (match := _VIOLATION.match(line))
    }
    return sorted(found)


def format_check(package: Package, *, fix: bool = False) -> None:
    """Refuse a source clang-format would rewrite; *fix* rewrites it.

    The style is the package's own `.clang-format`, seeded at birth
    and edited there. The refusal names each file, because a person
    fixes files, not a diff.

    Raises:
        Failed: when a file is not formatted, or clang-format exits
            non-zero for a reason of its own.
    """
    files = sources(package)
    if not files:
        return
    arguments = ["-i"] if fix else ["--dry-run", "--Werror"]
    result = tools.clang_format.opts(
        cwd=package.directory, nofail=True, recorded=False
    )(*arguments, *(str(path) for path in files))
    if result.code == 0:
        return
    named = ", ".join(unformatted(result.stderr + result.stdout)) or "no file named"
    fail(
        f"{package.name}: clang-format would rewrite {named}."
        f" Run `{footman.prog()} check --fix` to apply the package's own"
        " .clang-format."
    )


def _asked(argv: list[str]) -> str:
    """The first line *argv* prints, or empty when it will not run."""
    try:
        answer = footman.run(argv, nofail=True, recorded=False)
    except OSError:
        return ""
    lines = (answer.stdout or "").strip().splitlines()
    return lines[0] if answer.code == 0 and lines else ""


def _toolchain_arguments() -> tuple[list[str], str]:
    """What the standalone clang-tidy needs, and why it cannot run.

    The static build is one binary. It carries no resource directory
    of its own, so the compiler's builtin headers (`stddef.h` and its
    kin) come from the host's compiler, and on macOS the standard
    library comes from the SDK xcrun names. Returns the arguments and
    an empty reason, or no arguments and the reason the lint cannot
    run, which the caller prints as a skip.
    """
    resources = _asked(["clang", "-print-resource-dir"]) or _asked(
        ["cc", "-print-file-name=include"]
    )
    if not resources:
        return [], "no compiler here answers where its builtin headers are"
    arguments = [f"--extra-arg=-resource-dir={resources.removesuffix('/include')}"]
    if sys.platform == "darwin":
        sdk = _asked(["xcrun", "--show-sdk-path"])
        if not sdk:
            return [], "xcrun names no SDK, where this platform keeps its headers"
        arguments.append(f"--extra-arg=-isysroot{sdk}")
    return arguments, ""


def lint(package: Package, root: Path) -> None:
    """Run clang-tidy over the package's sources; a finding is a refusal.

    The checks are the package's own `.clang-tidy`. clang-tidy reads
    the compilation database the gate build exports, so the build
    runs first and a package that has not configured is configured
    here.

    Raises:
        Failed: when clang-tidy finds anything, with its own output.
    """
    files = sources(package)
    database = package.directory / GATE_BUILD_DIR / "compile_commands.json"
    if not files or not database.is_file():
        return
    arguments, reason = _toolchain_arguments()
    if reason:
        print(f"  {package.name}: clang-tidy skips, {reason}")
        return
    result = tools.clang_tidy.opts(cwd=package.directory, nofail=True, recorded=False)(
        "-p",
        GATE_BUILD_DIR,
        *arguments,
        *(str(path) for path in files),
    )
    if result.code != 0:
        fail(
            f"{package.name}: clang-tidy found something:\n"
            f"{result.stdout[-4000:]}{result.stderr[-2000:]}"
        )


def build(package: Package, root: Path, *, epoch: int = 0) -> Path:
    """Package *package* through ``conan create``; the conan cache dir.

    ``conan create`` exports the recipe, builds in the cache, and
    packages the result there; publishing uploads from the cache, so
    nothing lands in a ``dist/`` directory. Returns the package's
    ``build`` directory as the artifact location the caller can
    inspect. *epoch* is accepted for the backend contract; conan
    stamps its own metadata and the reproducibility guard for this
    kind is a later phase's work.
    """
    del root, epoch
    conan = _conan(package.directory, "profile", "detect", "--exist-ok")
    if conan.code != 0:
        fail(f"conan profile detect exited {conan.code}:\n{conan.stdout}{conan.stderr}")
    result = _conan(package.directory, "create", ".")
    if result.code != 0:
        fail(
            f"conan create ({package.name}) exited {result.code}:\n"
            f"{result.stdout[-4000:]}{result.stderr[-2000:]}"
        )
    return package.directory / "build"
