"""The wave over a nanobind member and its conan library, and the wheels job's emit.

A workspace holds a conan library and a nanobind package that consumes
it. The wave orders and dispatches them, each to the registry its
extensions answer, from what the wheels job built or by building, and
the release workflow gains nanobind's wheels job only while a package
holds nanobind.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

# The wheels job is nanobind's, added as the mount adds it.
from extensions_nanobind_workspace import (
    nanobind_jobs,  # noqa: F401
    skip_unless_installed,
)
from livery import git
from livery.extensions.conan import _package as _conan
from livery.footman import Failed
from livery.workshop import _publish
from livery.workshop._packages import Package
from livery.workshop._publish import publish_release
from livery.workshop._registries import RegistryTarget

_FAILURES = (SystemExit, Failed)


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
    (root / "workshop.toml").write_text("[workspace]\n")
    lib = root / "packages" / "geometry"
    lib.mkdir(parents=True)
    (lib / "workshop.toml").write_text(
        'extensions = ["cmake", "conan"]\nname = "acme-geometry"\n'
    )
    (lib / "conanfile.py").write_text(
        'class G:\n    name = "acme-geometry"\n    version = "0.3.0"\n'
    )
    (lib / "CHANGELOG.md").write_text("# Changelog\n")
    ext = root / "packages" / "ext"
    (ext / "src" / "acme" / "ext").mkdir(parents=True)
    (ext / "workshop.toml").write_text(
        'name = "acme-ext"\nextensions = ["nanobind"]\n'
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
    (root / ".release-manifest.json").write_text(
        '{"schema": 1, "members": ['
        '{"dir": "geometry", "name": "acme-geometry", "version": "0.3.0"},'
        ' {"dir": "ext", "name": "acme-ext", "version": "0.3.0"}]}\n'
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
    return root


def _lists(root: Path, member: str, extensions: str) -> None:
    """Make ``packages/<member>``'s contract list *extensions*, a TOML list."""
    contract = root / "packages" / member / "workshop.toml"
    kept = [
        line
        for line in contract.read_text().splitlines()
        if not line.startswith("extensions = ")
    ]
    contract.write_text("\n".join([f"extensions = {extensions}", *kept]) + "\n")


def _handed(
    monkeypatch: pytest.MonkeyPatch, ledger: _Ledger
) -> list[tuple[str, str, str]]:
    """Stand in for each member's build and upload; each upload's member and target.

    Each upload is served by *ledger* at once.
    """
    from livery.workshop import _lifecycle

    handed: list[tuple[str, str, str]] = []

    def _build(package: Package, _root: Path, *, epoch: int = 0) -> Path:
        return package.directory / "dist"

    def _upload(
        package: Package,
        _root: Path,
        *,
        version: str,
        target: RegistryTarget,
        prebuilt: bool = False,
    ) -> bool:
        del prebuilt
        handed.append((package.name, target.kind, target.url))
        ledger.serve(package.name, version)
        return True

    monkeypatch.setattr(_lifecycle, "build_release", _build)
    monkeypatch.setattr(_lifecycle, "publish", _upload)
    return handed


def test_a_member_whose_extensions_answer_no_registry_refuses_the_wave_naming_it(
    cross_train, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A library listing cmake alone releases nothing, so it has no registry.

    The wave refuses before anything uploads or any tag is cut, and the
    extension released beside it stays unpublished too.
    """
    skip_unless_installed("cmake")
    root = cross_train
    _lists(root, "geometry", '["cmake"]')
    ledger = _Ledger()
    handed = _handed(monkeypatch, ledger)
    with pytest.raises(Failed) as refused:
        publish_release(
            root,
            lambda _package: ledger,
            ref=git.head_sha(root),
            probe_timeout=5,
            probe_poll=0.01,
        )
    assert str(refused.value) == (
        "packages/geometry: its extensions (cmake) answer no registry: none of"
        " them releases a distribution of it, so the release train has nowhere"
        " to publish it. List the extension that releases it, or set [release]"
        " publish = false to version and tag it without an upload"
    )
    assert handed == []
    assert not [tag for tag in git.tags(root) if tag.startswith("packages/")]


def test_the_wave_resolves_each_member_s_target_from_its_distributions(
    cross_train, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Conan's target for the library that lists conan, python's for the extension.

    The extension lists nanobind: python releases its wheel, and its
    consumer's recipe releases nothing.
    """
    root = cross_train
    _lists(root, "geometry", '["cmake", "conan"]')
    ledger = _Ledger()
    handed = _handed(monkeypatch, ledger)
    receipts = publish_release(
        root,
        lambda _package: ledger,
        ref=git.head_sha(root),
        index_url="https://idx.example/",
        token="t",
        probe_timeout=5,
        probe_poll=0.01,
    )
    assert handed == [
        ("acme-geometry", "conan", str(tmp_path / "share")),
        ("acme-ext", "python", "https://idx.example/"),
    ]
    assert [receipt.tag for receipt in receipts] == [
        "packages/geometry/v0.3.0",
        "packages/ext/v0.3.0",
    ]


def test_the_cross_kind_wave_orders_and_dispatches(
    cross_train, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = cross_train
    registry = _Ledger()
    conan_registry = _Ledger()
    order: list[str] = []

    def _fake_build(package: Package, _root: Path, *, epoch: int = 0) -> Path:
        dist = package.directory / "dist"
        dist.mkdir(exist_ok=True)
        if "nanobind" in package.extensions:
            (dist / "acme_ext-0.3.0-cp314-cp314-manylinux_x86_64.whl").touch()
        return dist

    def _fake_conan_publish(
        package: Package, target_url: str, *, version: str, local: bool, token: str = ""
    ) -> bool:
        order.append(package.name)
        assert local  # CONAN_REMOTE_URL points at a bare path
        conan_registry.serve(package.name, version)
        return True

    def _fake_wheels(package: Package, **kwargs: object) -> bool:
        order.append(package.name)
        registry.serve(package.name, "0.3.0")
        return True

    from livery.extensions.nanobind import _build as nb

    monkeypatch.setattr(_conan, "build", _fake_build)
    monkeypatch.setattr(nb, "build_dist", _fake_build)
    monkeypatch.setattr(_conan, "publish", _fake_conan_publish)
    monkeypatch.setattr(
        "livery.extensions.python._backend.publish_wheels", _fake_wheels
    )

    def registry_for(package: Package) -> _Ledger:
        return conan_registry if "conan" in package.extensions else registry

    receipts = publish_release(
        root,
        registry_for,
        ref=git.head_sha(root),
        probe_timeout=5,
        probe_poll=0.01,
    )
    assert order == ["acme-geometry", "acme-ext"]
    assert [r.tag for r in receipts] == [
        "packages/geometry/v0.3.0",
        "packages/ext/v0.3.0",
    ]


def test_a_releases_target_tags_before_it_uploads_and_a_rerun_finishes_it(
    cross_train, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route for a forge with no conan registry, failure first.

    The release is addressed by the receipt tag, so the tag is pushed
    before the assets exist. That leaves a window: an upload that
    dies leaves a tag with nothing under it, and the re-run has to
    finish the job rather than read the tag as a receipt.
    """
    from livery.forge import ForgeError
    from livery.forge.testing import FakeForge

    root = cross_train
    registry = _Ledger()
    fake = FakeForge()
    fake.create_repo("acme", "ws")
    repository = fake.repository("acme", "ws")
    target = RegistryTarget(kind="conan", url="fake://acme/ws", releases=True)
    monkeypatch.setattr("livery.workshop.this_repository", lambda _root: repository)
    monkeypatch.setattr(
        "livery.workshop._registries.resolve_registry", lambda _root, _kind: target
    )
    real_cut = _publish.cut_tag

    def _cut_and_push_to_the_forge(root: Path, tag: str, ref: str) -> None:
        real_cut(root, tag, ref)
        if tag not in repository.tags():
            fake.create_tag("acme", "ws", tag)

    monkeypatch.setattr(_publish, "cut_tag", _cut_and_push_to_the_forge)

    def _fake_build(package: Package, _root: Path, *, epoch: int = 0) -> Path:
        dist = package.directory / "dist"
        dist.mkdir(exist_ok=True)
        if "nanobind" in package.extensions:
            (dist / "acme_ext-0.3.0-cp314-cp314-manylinux_x86_64.whl").touch()
        else:
            (dist / _conan.cache_name(package.name, "0.3.0")).write_bytes(b"cache")
        return dist

    def _fake_wheels(package: Package, **kwargs: object) -> bool:
        registry.serve(package.name, "0.3.0")
        return True

    from livery.extensions.nanobind import _build as nb

    monkeypatch.setattr(_conan, "build", _fake_build)
    monkeypatch.setattr(nb, "build_dist", _fake_build)
    monkeypatch.setattr(
        "livery.extensions.python._backend.publish_wheels", _fake_wheels
    )
    conan_registry = _conan.ConanRegistry(target, root=root)

    def registry_for(package: Package):
        return conan_registry if "conan" in package.extensions else registry

    # The upload dies after the tag is cut.
    def _refuse(*_args: object, **_kwargs: object) -> None:
        raise ForgeError("the upload died", status=500)

    monkeypatch.setattr(type(repository.release), "upload_asset", _refuse)
    with pytest.raises(_FAILURES, match="the upload died"):
        publish_release(
            root,
            registry_for,
            ref=git.head_sha(root),
            probe_timeout=2,
            probe_poll=0.01,
        )
    tag = "packages/geometry/v0.3.0"
    assert tag in git.tags(root)  # the tag went first, as the route needs
    assert repository.release.assets(tag) == ()

    # The re-run finishes it: the tag is not read as a receipt.
    monkeypatch.undo()
    monkeypatch.setattr("livery.workshop.this_repository", lambda _root: repository)
    monkeypatch.setattr(
        "livery.workshop._registries.resolve_registry", lambda _root, _kind: target
    )
    monkeypatch.setattr(_publish, "cut_tag", _cut_and_push_to_the_forge)
    monkeypatch.setattr(_conan, "build", _fake_build)
    monkeypatch.setattr(nb, "build_dist", _fake_build)
    monkeypatch.setattr(
        "livery.extensions.python._backend.publish_wheels", _fake_wheels
    )
    monkeypatch.setattr("livery.workshop._publish.PROBE_POLL", 0.01, raising=False)
    receipts = publish_release(
        root, registry_for, ref=git.head_sha(root), probe_timeout=5, probe_poll=0.01
    )
    assert [r.tag for r in receipts] == [
        "packages/geometry/v0.3.0",
        "packages/ext/v0.3.0",
    ]
    attached = [asset.name for asset in repository.release.assets(tag)]
    assert attached == [_conan.cache_name("acme-geometry", "0.3.0")]


def _serving_wave(
    monkeypatch: pytest.MonkeyPatch, order: list[str]
) -> Callable[[Package], _Ledger]:
    """Stand in for the wave's builds, which must not run, and its uploads.

    Each upload appends the member's name to *order* and serves it.
    Returns the registry each member is probed on.
    """
    from livery.extensions.nanobind import _build as nb

    registry = _Ledger()
    conan_registry = _Ledger()

    def _no_build(package: Package, _root: Path, *, epoch: int = 0) -> Path:
        raise AssertionError(f"the wave built {package.name}")

    def _fake_conan_publish(
        package: Package, target_url: str, *, version: str, local: bool, token: str = ""
    ) -> bool:
        order.append(package.name)
        conan_registry.serve(package.name, version)
        return True

    def _fake_wheels(package: Package, **kwargs: object) -> bool:
        order.append(package.name)
        registry.serve(package.name, "0.3.0")
        return True

    class _Done:
        code = 0
        stdout = ""
        stderr = ""

    def _fake_conan(cwd: Path, *args: str, env: dict[str, str] | None = None) -> _Done:
        del cwd, env
        order.append(f"conan {' '.join(args[:2])} {Path(args[2]).name}")
        return _Done()

    monkeypatch.setattr(_conan, "build", _no_build)
    monkeypatch.setattr(nb, "build_dist", _no_build)
    monkeypatch.setattr(_conan, "publish", _fake_conan_publish)
    monkeypatch.setattr(_conan, "_conan", _fake_conan)
    monkeypatch.setattr(
        "livery.extensions.python._backend.publish_wheels", _fake_wheels
    )

    def registry_for(package: Package) -> _Ledger:
        return conan_registry if "conan" in package.extensions else registry

    return registry_for


@pytest.mark.parametrize(
    ("collected", "refused"),
    [("ext", "acme-geometry"), ("geometry", "acme-ext")],
    ids=["the-library-uncollected", "the-extension-uncollected"],
)
def test_prebuilt_refuses_a_job_built_member_with_nothing_collected(
    cross_train, monkeypatch: pytest.MonkeyPatch, collected: str, refused: str
) -> None:
    """Building the member again on the wave's runner is not the answer.

    The wheels job builds both members before the wave, on each of its
    legs, and the run collects what every leg made. With --prebuilt and
    nothing collected for one of them, the wave stops instead of
    building what only this runner's host could use.
    """
    root = cross_train
    registry_for = _serving_wave(monkeypatch, [])
    dist = root / "packages" / collected / "dist"
    dist.mkdir(parents=True)
    # What the run collects for each: the library's saved cache for this
    # host, the extension's wheel.
    saved = {
        "geometry": _conan.cache_name("acme-geometry", "0.3.0"),
        "ext": "acme_ext-0.3.0-cp314-cp314-manylinux_x86_64.whl",
    }
    (dist / saved[collected]).write_bytes(b"")
    with pytest.raises(_FAILURES) as caught:
        publish_release(
            root,
            registry_for,
            ref=git.head_sha(root),
            probe_timeout=5,
            probe_poll=0.01,
            prebuilt=True,
        )
    assert (
        f"{refused}: --prebuilt, and dist/ holds nothing the wheels job collected;"
        " re-run the release so that job builds it before the wave"
    ) in str(caught.value)


def test_prebuilt_publishes_what_the_run_collected_without_building(
    cross_train, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = cross_train
    order: list[str] = []
    registry_for = _serving_wave(monkeypatch, order)
    saved = _conan.cache_name("acme-geometry", "0.3.0", "linux-x64")
    for member, collected in (
        ("geometry", saved),
        ("ext", "acme_ext-0.3.0-cp314-cp314-manylinux_x86_64.whl"),
    ):
        dist = root / "packages" / member / "dist"
        dist.mkdir(parents=True)
        (dist / collected).write_bytes(b"")
    receipts = publish_release(
        root,
        registry_for,
        ref=git.head_sha(root),
        probe_timeout=5,
        probe_poll=0.01,
        prebuilt=True,
    )
    # The library's saved cache goes into this job's conan cache before
    # its upload reads it.
    assert order == [f"conan cache restore {saved}", "acme-geometry", "acme-ext"]
    assert [r.tag for r in receipts] == [
        "packages/geometry/v0.3.0",
        "packages/ext/v0.3.0",
    ]


# The emitted matrix: nanobind's wheels job, added as the mount adds it
# for a workspace whose packages hold nanobind.


def _release_answers(*, with_native: bool) -> dict[str, object]:
    return {
        "runners": ["ubuntu-latest", "windows"],
        # The wheels job's labels are the union of the labels the
        # packages that hold nanobind declare, which the facts carry by
        # point and job; a workspace whose packages name none has none.
        "job_runners": {
            ("release", "wheels"): ("ubuntu-latest", "windows") if with_native else ()
        },
    }


@pytest.mark.usefixtures("nanobind_jobs")
def test_the_github_release_gains_the_matrix_only_with_a_native_member() -> None:
    from livery.workshop._ci_generate import _actions_workflow

    pure = _actions_workflow(
        _release_answers(with_native=False), "fm", "release.yml", forge="github"
    )
    assert "wheels:" not in pure
    native = _actions_workflow(
        _release_answers(with_native=True), "fm", "release.yml", forge="github"
    )
    assert "wheels:" in native
    # The matrix is the declared wheel platforms, never a fixed OS list.
    assert "matrix:" in native and "os: [ubuntu-latest, windows]" in native
    assert "ci.run --point=release --job=wheels" in native
    assert "needs: [wheels]" in native
    assert "pattern: wheels*" in native
    # The wave decides prebuilt from the collected dist; the shell
    # spells nothing.
    assert "--prebuilt" not in native
    # The legs that build native packages carry conan's home across
    # runs: a third-party package builds from source once per key.
    assert "path: ${{ runner.temp }}/conan" in native
    assert (
        "key: conan-${{ runner.os }}-${{ runner.arch }}"
        "-${{ hashFiles('packages/*/conanfile.py', 'packages/*/*/conanfile.py') }}"
        in native
    )
    assert "restore-keys: conan-${{ runner.os }}-${{ runner.arch }}-" in native
    # One job only: the publish job attaches what the legs built.
    assert native.count("path: ${{ runner.temp }}/conan") == 1
    assert "${{ runner.temp }}/conan" not in _actions_workflow(
        _release_answers(with_native=False), "fm", "release.yml", forge="github"
    )


@pytest.mark.usefixtures("nanobind_jobs")
def test_the_gitea_release_matrix_rides_the_declared_wheel_platforms() -> None:
    from livery.workshop._ci_generate import _actions_workflow

    pure = _actions_workflow(
        _release_answers(with_native=False), "fm", "release.yml", forge="gitea"
    )
    assert "wheels:" not in pure
    native = _actions_workflow(
        _release_answers(with_native=True), "fm", "release.yml", forge="gitea"
    )
    assert "wheels:" in native
    assert "os: [ubuntu-latest, windows]" in native
    assert "--prebuilt" not in native
