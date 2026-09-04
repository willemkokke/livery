"""Publish across kinds: the identity guard, conan seam, matrix emit."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from footman import Failed

from livery.workshop._backends import _cpp_conan
from livery.workshop._git_ops import GitOps
from livery.workshop._packages import Package
from livery.workshop._publish import assert_wheel_identity, publish_release

_FAILURES = (SystemExit, Failed)


def _package(directory: Path, name: str, type_name: str) -> Package:
    directory.mkdir(parents=True, exist_ok=True)
    return Package(
        directory=directory,
        path=f"packages/{directory.name}",
        name=name,
        type=type_name,
        depends=(),
    )


# The identity guard, both ways, first.


def test_a_pure_kind_with_a_platform_wheel_refuses(tmp_path: Path) -> None:
    package = _package(tmp_path / "packages" / "plain", "acme-plain", "python")
    dist = package.directory / "dist"
    dist.mkdir()
    (dist / "acme_plain-0.1.0-cp314-cp314-macosx_11_0_arm64.whl").touch()
    with pytest.raises(_FAILURES) as caught:
        assert_wheel_identity(package)
    message = str(caught.value)
    assert "python" in message and "platform tag" in message
    assert "cp314-cp314-macosx_11_0_arm64" in message


def test_a_native_kind_with_a_pure_wheel_refuses(tmp_path: Path) -> None:
    package = _package(tmp_path / "packages" / "ext", "acme-ext", "python-nanobind")
    dist = package.directory / "dist"
    dist.mkdir()
    (dist / "acme_ext-0.1.0-py3-none-any.whl").touch()
    with pytest.raises(_FAILURES) as caught:
        assert_wheel_identity(package)
    message = str(caught.value)
    assert "python-nanobind" in message and "pure-tagged" in message


def test_the_guard_skips_a_kind_without_wheels(tmp_path: Path) -> None:
    package = _package(tmp_path / "packages" / "lib", "acme-lib", "cpp-conan")
    assert_wheel_identity(package)  # no dist at all, and no complaint


def test_matching_identities_pass(tmp_path: Path) -> None:
    pure = _package(tmp_path / "packages" / "plain", "acme-plain", "python")
    (pure.directory / "dist").mkdir()
    (pure.directory / "dist" / "acme_plain-0.1.0-py3-none-any.whl").touch()
    assert_wheel_identity(pure)
    native = _package(tmp_path / "packages" / "ext", "acme-ext", "python-nanobind")
    (native.directory / "dist").mkdir()
    (
        native.directory / "dist" / "acme_ext-0.1.0-cp314-cp314-manylinux_x86_64.whl"
    ).touch()
    assert_wheel_identity(native)


# The conan version homes.


def test_the_conan_stamp_writes_and_refuses(tmp_path: Path) -> None:
    package = _package(tmp_path / "packages" / "lib", "acme-lib", "cpp-conan")
    conanfile = package.directory / "conanfile.py"
    conanfile.write_text('class L:\n    name = "acme-lib"\n    version = "0.1.0"\n')
    assert _cpp_conan.current_version(package) == "0.1.0"
    assert _cpp_conan.stamp_version(package).stamp("0.2.0") == ["conanfile.py"]
    assert _cpp_conan.current_version(package) == "0.2.0"
    assert _cpp_conan.stamp_version(package).stamp("0.2.0") == []  # idempotent
    conanfile.write_text("class L:\n    pass\n")
    with pytest.raises(_FAILURES, match="no version line"):
        _cpp_conan.stamp_version(package).stamp("0.3.0")


def test_the_folder_registry_answers_from_saved_archives(tmp_path: Path) -> None:
    target = tmp_path / "share"
    target.mkdir()
    (target / "acme-lib-0.1.0.tgz").touch()
    (target / "acme-lib-0.2.0.tgz").touch()
    (target / "acme-other-9.9.9.tgz").touch()
    registry = _cpp_conan.ConanRegistry(str(target), local=True, cwd=tmp_path)
    assert registry.versions("acme-lib") == ("0.1.0", "0.2.0")
    assert registry.versions("acme-ghost") == ()


def test_conan_publish_refuses_without_conan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    package = _package(tmp_path / "packages" / "lib", "acme-lib", "cpp-conan")
    with pytest.raises(_FAILURES, match="conan is not on"):
        _cpp_conan.publish(
            package, str(tmp_path / "share"), version="0.1.0", local=True
        )


# The wave across kinds, seams stubbed.


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True)


class _Ledger:
    def __init__(self) -> None:
        self.served: dict[str, set[str]] = {}

    def serve(self, name: str, version: str) -> None:
        self.served.setdefault(name, set()).add(version)

    def versions(self, name: str) -> tuple[str, ...]:
        return tuple(sorted(self.served.get(name, set())))


@pytest.fixture
def cross_train(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A squashed release of the library and its extension."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "--initial-branch=main")
    root = tmp_path / "ws"
    _git(tmp_path, "clone", str(origin), "ws")
    _git(root, "config", "user.email", "t@livery.local")
    _git(root, "config", "user.name", "T")
    (root / "workshop.toml").write_text("[workspace]\n")
    lib = root / "packages" / "geometry"
    lib.mkdir(parents=True)
    (lib / "workshop.toml").write_text('type = "cpp-conan"\nname = "acme-geometry"\n')
    (lib / "conanfile.py").write_text(
        'class G:\n    name = "acme-geometry"\n    version = "0.3.0"\n'
    )
    (lib / "CHANGELOG.md").write_text("# Changelog\n")
    ext = root / "packages" / "ext"
    (ext / "src" / "acme" / "ext").mkdir(parents=True)
    (ext / "workshop.toml").write_text(
        'type = "python-nanobind"\nname = "acme-ext"\n'
        "[[depends]]\n"
        'path = "packages/geometry"\nkind = "build"\nfloor = "0.3.0"\n'
    )
    (ext / "pyproject.toml").write_text(
        '[project]\nname = "acme-ext"\nversion = "0.3.0"\ndependencies = []\n'
    )
    (ext / "conanfile.py").write_text('requires = "acme-geometry/[>=0.3.0]"\n')
    (ext / "CHANGELOG.md").write_text("# Changelog\n")
    (ext / "src" / "acme" / "ext" / "__init__.py").write_text('__version__ = "0.3.0"\n')
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "chore: seed")
    _git(root, "push", "-u", "origin", "main")
    for member in ("geometry", "ext"):
        (root / "packages" / member / "CHANGELOG.md").write_text(
            "# Changelog\n\n## [0.3.0]\n\n- x\n"
        )
    _git(root, "add", "-A")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True
    ).stdout.strip()
    _git(
        root,
        "commit",
        "-m",
        "chore(release): released acme-geometry v0.3.0, acme-ext v0.3.0",
        "-m",
        f"Mined-At: {sha}",
    )
    monkeypatch.setattr("livery.workshop._publish.PROBE_POLL", 0.01, raising=False)
    monkeypatch.setenv("CONAN_REMOTE_URL", str(tmp_path / "share"))
    return root, GitOps(root)


def test_the_cross_kind_wave_orders_and_dispatches(
    cross_train, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root, git = cross_train
    registry = _Ledger()
    conan_registry = _Ledger()
    order: list[str] = []

    def _fake_build(package: Package, _root: Path, *, epoch: int = 0) -> Path:
        dist = package.directory / "dist"
        dist.mkdir(exist_ok=True)
        if package.type == "python-nanobind":
            (dist / "acme_ext-0.3.0-cp314-cp314-manylinux_x86_64.whl").touch()
        return dist

    def _fake_conan_publish(
        package: Package, target_url: str, *, version: str, local: bool
    ) -> bool:
        order.append(package.name)
        assert local  # CONAN_REMOTE_URL points at a bare path
        conan_registry.serve(package.name, version)
        return True

    def _fake_wheels(package: Package, **kwargs: object) -> bool:
        order.append(package.name)
        registry.serve(package.name, "0.3.0")
        return True

    from livery.workshop._backends import _cpp_conan as cpp
    from livery.workshop._backends import _python_nanobind as nb

    monkeypatch.setattr(cpp, "build", _fake_build)
    monkeypatch.setattr(nb, "build", _fake_build)
    monkeypatch.setattr(cpp, "publish", _fake_conan_publish)
    monkeypatch.setattr("livery.workshop._publish.publish_wheels", _fake_wheels)

    def registry_for(package: Package) -> _Ledger:
        return conan_registry if package.type == "cpp-conan" else registry

    receipts = publish_release(
        root,
        git,
        registry_for,
        ref=git.head_sha(),
        probe_timeout=5,
        probe_poll=0.01,
    )
    assert order == ["acme-geometry", "acme-ext"]
    assert [r.tag for r in receipts] == [
        "packages/geometry/v0.3.0",
        "packages/ext/v0.3.0",
    ]


def test_prebuilt_refuses_an_empty_collection(
    cross_train, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, git = cross_train
    registry = _Ledger()
    conan_registry = _Ledger()

    def _fake_conan_publish(
        package: Package, target_url: str, *, version: str, local: bool
    ) -> bool:
        conan_registry.serve(package.name, version)
        return True

    from livery.workshop._backends import _cpp_conan as cpp

    monkeypatch.setattr(cpp, "build", lambda p, r, epoch=0: p.directory / "dist")
    monkeypatch.setattr(cpp, "publish", _fake_conan_publish)

    def registry_for(package: Package) -> _Ledger:
        return conan_registry if package.type == "cpp-conan" else registry

    with pytest.raises(_FAILURES) as caught:
        publish_release(
            root,
            git,
            registry_for,
            ref=git.head_sha(),
            probe_timeout=5,
            probe_poll=0.01,
            prebuilt=True,
        )
    assert "no collected wheels" in str(caught.value)
    assert "wheels matrix" in str(caught.value)


# The emitted matrix.


def _release_answers(*, with_native: bool) -> dict[str, object]:
    packages: list[dict[str, str]] = [
        {"dir": "plain", "name": "acme-plain", "dev": "acme-plain"}
    ]
    if with_native:
        packages.append(
            {
                "dir": "ext",
                "name": "acme-ext",
                "dev": "acme-ext",
                "kind": "python-nanobind",
            }
        )
    return {"packages": packages, "runners": ["ubuntu-latest", "windows"]}


def test_the_github_release_gains_the_matrix_only_with_a_native_member() -> None:
    from livery.workshop._ci_generate import _github_release

    pure = _github_release(_release_answers(with_native=False), "fm")
    assert "wheels:" not in pure
    assert "--prebuilt" not in pure
    native = _github_release(_release_answers(with_native=True), "fm")
    assert "wheels:" in native
    assert "matrix:" in native and "macos-latest" in native
    assert "release.wheels" in native
    assert "needs: [wheels]" in native
    assert "--prebuilt" in native
    assert "pattern: wheels-*" in native


def test_the_gitea_release_matrix_rides_the_declared_runners() -> None:
    from livery.workshop._ci_generate import _gitea_release

    pure = _gitea_release(_release_answers(with_native=False), "fm")
    assert "wheels:" not in pure
    native = _gitea_release(_release_answers(with_native=True), "fm")
    assert "wheels:" in native
    assert "runner: [ubuntu-latest, windows]" in native
    assert "--prebuilt" in native


def test_validate_member_skips_a_wheelless_kind(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop._release_driver import MemberPlan, validate_member

    package = _package(tmp_path / "packages" / "lib", "acme-lib", "cpp-conan")
    validate_member(tmp_path, MemberPlan(package=package, version="0.1.0"), ())
    out = capsys.readouterr().out
    assert "isolated legs skip" in out
    assert "cpp-conan" in out


# The armed proofs: a real conan against the folder target and the
# rig's gitea registry.

_CONAN_GAP = (
    ""
    if all(shutil.which(t) for t in ("conan", "cmake", "ninja", "cc"))
    else "conan, cmake, ninja, and cc must resolve"
)
needs_conan = pytest.mark.skipif(bool(_CONAN_GAP), reason=_CONAN_GAP)

ROOT = Path(__file__).resolve().parents[3]
TEMPLATES = ROOT / "packages/workshop/src/livery/workshop/templates"


def _render_library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Package:
    """A rendered cpp-conan package, built into an isolated CONAN_HOME."""
    from livery.workshop._templates import read_answers, render

    home = tmp_path / "conan-home"
    monkeypatch.setenv("CONAN_HOME", str(home))
    destination = tmp_path / "packages" / "geometry"
    answers = read_answers(ROOT / ".copier-answers.yml")
    render(
        str(TEMPLATES),
        destination,
        {
            "kind": "package-cpp-conan",
            "package_name": "acme-geometry",
            "package_description": "acme-geometry: a native library.",
            "namespace_package": "acme",
            "author_name": answers["author_name"],
            "author_email": answers["author_email"],
            "copyright_year": answers["copyright_year"],
            "project_name": "acme",
        },
    )
    package = _package(destination, "acme-geometry", "cpp-conan")
    from livery.workshop._backends import _cpp_conan as cpp

    cpp.build(package, tmp_path)
    return package


@needs_conan
def test_the_folder_target_round_trips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _render_library(tmp_path, monkeypatch)
    share = tmp_path / "share"
    assert _cpp_conan.publish(package, str(share), version="0.0.1", local=True)
    registry = _cpp_conan.ConanRegistry(str(share), local=True, cwd=tmp_path)
    assert registry.versions("acme-geometry") == ("0.0.1",)
    # A clean home restores the package from the saved archive alone.
    monkeypatch.setenv("CONAN_HOME", str(tmp_path / "clean-home"))
    restored = subprocess.run(
        [
            "conan",
            "cache",
            "restore",
            str(share / "acme-geometry-0.0.1.tgz"),
        ],
        capture_output=True,
        text=True,
    )
    assert restored.returncode == 0, restored.stderr
    listed = subprocess.run(
        ["conan", "list", "acme-geometry/*"], capture_output=True, text=True
    )
    assert "0.0.1" in listed.stdout


@needs_conan
def test_the_rig_conan_registry_round_trips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import urllib.request

    url = os.environ.get("GITEA_URL", "http://localhost:3000")
    token = os.environ.get("GITEA_TOKEN", "")
    if not token:
        pytest.skip("GITEA_TOKEN unset: the rig's conan registry needs it")
    try:
        urllib.request.urlopen(url, timeout=3)
    except OSError:
        pytest.skip(f"the rig at {url} is not answering")
    package = _render_library(tmp_path, monkeypatch)
    remote = f"{url.rstrip('/')}/api/packages/livery-admin/conan"
    monkeypatch.setenv("CONAN_LOGIN_USERNAME", "livery-admin")
    monkeypatch.setenv("CONAN_PASSWORD", token)
    login = subprocess.run(
        ["conan", "remote", "add", _cpp_conan.CONAN_REMOTE, remote, "--force"],
        capture_output=True,
        text=True,
    )
    assert login.returncode == 0, login.stderr
    auth = subprocess.run(
        ["conan", "remote", "login", _cpp_conan.CONAN_REMOTE, "livery-admin"],
        capture_output=True,
        text=True,
    )
    assert auth.returncode == 0, auth.stderr
    assert _cpp_conan.publish(package, remote, version="0.0.1", local=False) in (
        True,
        False,  # a re-run walks past the earlier upload
    )
    registry = _cpp_conan.ConanRegistry(remote, local=False, cwd=tmp_path)
    assert "0.0.1" in registry.versions("acme-geometry")
    # The consumer proof: a clean home installs it back from the rig.
    monkeypatch.setenv("CONAN_HOME", str(tmp_path / "clean-home"))
    subprocess.run(["conan", "profile", "detect", "--exist-ok"], capture_output=True)
    subprocess.run(
        ["conan", "remote", "add", _cpp_conan.CONAN_REMOTE, remote, "--force"],
        capture_output=True,
    )
    subprocess.run(
        ["conan", "remote", "login", _cpp_conan.CONAN_REMOTE, "livery-admin"],
        capture_output=True,
        text=True,
    )
    installed = subprocess.run(
        [
            "conan",
            "install",
            "--requires=acme-geometry/0.0.1",
            "-r",
            _cpp_conan.CONAN_REMOTE,
            "--build=missing",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert installed.returncode == 0, f"{installed.stdout}\n{installed.stderr}"
