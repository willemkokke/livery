"""The C/C++ backend: the build callables for ``type = "cpp-conan"``.

The gate verb configures and builds with cmake and ninja and runs
the ctest suite through the generated ``test`` target, all through
the toolroom handles. Packaging goes through conan, which has no
toolroom handle yet, so ``build`` starts it as a deliberate
``livery.footman.run`` after probing that the binary resolves; a machine
without conan gets the install command, never a stack trace.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import livery.footman as footman
from livery.footman import fail
from livery.toolroom import tools

if TYPE_CHECKING:
    from livery.forge import Repository
    from livery.workshop._packages import Package
    from livery.workshop._registries import RegistryTarget

#: Where the gate's cmake configure lands, under the package.
#: conan's own cmake_layout also builds under build/, so one
#: gitignore entry covers both.
GATE_BUILD_DIR = "build/gate"


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


def _conan(
    package_dir: Path, *args: str, env: dict[str, str] | None = None
) -> footman.Result:
    """One conan invocation; the refusal names the store that supplies conan."""
    if shutil.which("conan") is None:
        fail(
            "conan is not on PATH: it is a tool of the store, so enter the"
            f" environment (`{footman.prog()} sync`, then the printed"
            " env.emit line) and re-run"
        )
    run_env = dict(os.environ)
    if env:
        run_env.update(env)
    return footman.run(
        ["conan", *args],
        cwd=package_dir,
        env=run_env,
        nofail=True,
        recorded=False,
    )


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


def classify(package: Package, path: str) -> str:
    """What *path*, relative to *package*, is to the cpp-conan kind.

    A C or C++ source under ``tests/`` is a test, one ctest per file
    named after its stem, as the template registers them; any other
    file there is test support; ``src/`` and ``include/`` are source;
    everything else (``CMakeLists.txt``, the conanfile, the contract)
    is configuration.
    """
    from livery.workshop._kinds import CONFIGURATION, SOURCE, TEST, TEST_SUPPORT

    del package
    parts = path.split("/")
    if parts[0] == "tests" and len(parts) > 1:
        return TEST if parts[-1].endswith(TEST_SOURCES) else TEST_SUPPORT
    if parts[0] in ("src", "include"):
        return SOURCE
    return CONFIGURATION


def gate_build(package: Package, root: Path) -> None:
    """Configure and build *package* into the gate's build directory.

    The dependency-free library needs no conan at gate time: cmake
    configures against the host toolchain and ninja builds, so a
    rebuild after one edit costs that edit. A package that declares
    conan requirements gains a conan install step when the
    cross-kind dependency lands; today a missing generator file
    fails the configure with cmake's own message.
    """
    del root
    build_dir = package.directory / GATE_BUILD_DIR
    cmake = tools.cmake.opts(cwd=package.directory)
    cmake(
        "-S",
        ".",
        "-B",
        str(build_dir),
        "-G",
        "Ninja",
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
    )
    cmake("--build", str(build_dir))


def test(
    package: Package,
    root: Path,
    *,
    selection: tuple[str, ...] = (),
    pages: tuple[str, ...] = (),
) -> None:
    """Run ctest over the gate build: every test, or *selection*'s alone.

    A selected test file maps to the ctest named after its stem
    (``tests/test_acme.cpp`` runs ``test_acme``), which is how the
    template registers tests; a selection no ctest answers to is a
    refusal naming the rule.
    """
    del root, pages  # no docs examples harness answers to a C++ kind
    build_dir = package.directory / GATE_BUILD_DIR
    if not selection:
        # The Ninja generator's `test` target runs ctest with the
        # verdict in the exit code; CTEST_OUTPUT_ON_FAILURE makes a red
        # test print its output instead of a bare summary line. The env
        # rides whole: standalone toolroom passes `env=` as the child's
        # entire environment, never a merge over the parent's.
        result = tools.cmake.opts(
            cwd=package.directory,
            env={**os.environ, "CTEST_OUTPUT_ON_FAILURE": "1"},
            nofail=True,
        )("--build", str(build_dir), "--target", "test")
        if result.code != 0:
            fail(
                f"{package.name}: ctest failed (exit {result.code}):\n"
                f"{result.stdout[-4000:]}{result.stderr[-2000:]}"
            )
        return
    if shutil.which("ctest") is None:
        fail(
            f"{package.name}: ctest is not on PATH beside cmake; the selected"
            " tests need it, so install cmake's tools and re-run"
        )
    names = [Path(path).stem for path in selection]
    pattern = "^(" + "|".join(re.escape(name) for name in names) + ")$"
    ran = footman.run(
        ["ctest", "--test-dir", str(build_dir), "-R", pattern, "--output-on-failure"],
        cwd=package.directory,
        nofail=True,
        recorded=False,
    )
    if "No tests were found" in ran.stdout + ran.stderr:
        fail(
            f"{package.name}: no ctest is named {', '.join(names)}; the cpp-conan"
            " kind maps a test file to the ctest of its stem"
            " (add_test(NAME <stem> ...)), so register it or run the suite"
        )
    if ran.code != 0:
        fail(
            f"{package.name}: ctest failed (exit {ran.code}):\n"
            f"{ran.stdout[-4000:]}{ran.stderr[-2000:]}"
        )


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
        for suffix in (*TEST_SOURCES, ".hpp", ".h", ".hxx"):
            found.extend(directory.rglob(f"*{suffix}"))
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
    unformatted = sorted(
        {
            line.split(":", 1)[0].strip()
            for line in (result.stderr + result.stdout).splitlines()
            if "code should be clang-formatted" in line
        }
    )
    named = ", ".join(unformatted) or "no file named"
    fail(
        f"{package.name}: clang-format would rewrite {named}."
        f" Run `{footman.prog()} check --fix` to apply the package's own"
        " .clang-format."
    )


def _sdk_arguments() -> list[str]:
    """What clang-tidy needs to find this platform's own headers.

    The static build carries clang's own resource directory and
    nothing else. On macOS the standard library lives inside the SDK,
    whose path only xcrun knows. Linux keeps its headers where clang
    already looks, and a Windows shell that has run the compiler's
    environment carries them in INCLUDE.
    """
    if sys.platform != "darwin":
        return []
    found = footman.run(["xcrun", "--show-sdk-path"], nofail=True, recorded=False)
    sdk = (found.stdout or "").strip()
    return [f"--extra-arg=-isysroot{sdk}"] if found.code == 0 and sdk else []


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
    result = tools.clang_tidy.opts(cwd=package.directory, nofail=True, recorded=False)(
        "-p",
        GATE_BUILD_DIR,
        *_sdk_arguments(),
        *(str(path) for path in files),
    )
    if result.code != 0:
        fail(
            f"{package.name}: clang-tidy found something:\n"
            f"{result.stdout[-4000:]}{result.stderr[-2000:]}"
        )


def check(package: Package, root: Path) -> None:
    """Format, configure, build, ctest and lint; a refusal is the verdict."""
    format_check(package)
    gate_build(package, root)
    test(package, root)
    lint(package, root)


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
    if shutil.which("conan") is None:
        fail(
            f"{package.name} is a cpp-conan package and conan is not on"
            " PATH: it is a tool of the store, so enter the environment"
            f" (`{footman.prog()} sync`, then the printed env.emit line)"
            " and re-run"
        )
    conan = footman.run(
        ["conan", "profile", "detect", "--exist-ok"],
        cwd=package.directory,
        nofail=True,
        recorded=False,
    )
    if conan.code != 0:
        fail(f"conan profile detect exited {conan.code}:\n{conan.stdout}{conan.stderr}")
    result = footman.run(
        ["conan", "create", "."],
        cwd=package.directory,
        nofail=True,
        recorded=False,
    )
    if result.code != 0:
        fail(
            f"conan create ({package.name}) exited {result.code}:\n"
            f"{result.stdout[-4000:]}{result.stderr[-2000:]}"
        )
    return package.directory / "build"
