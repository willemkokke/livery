"""The python extension inside the wheel: its load order, its entry point, its level."""

from __future__ import annotations

import importlib.metadata
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def test_the_python_extension_imported_before_the_base_registers_the_kind() -> None:
    """The extension loads first and the kind still registers with its code.

    The kind registry imports the extension's code while it registers,
    so the extension's module imports the registry only when it is
    called. Imported first, in a fresh interpreter, it loads, and the
    python kind's backend is that module.
    """
    run = subprocess.run(
        [
            sys.executable,
            "-c",
            "from livery.extensions.python import _backend\n"
            "from livery.workshop._kinds import kind_for\n"
            "assert kind_for('python').backend is _backend\n"
            "extractor = kind_for('python').extractor\n"
            "print(extractor.name if extractor else '')",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "python"


def test_the_wheel_ships_python_as_a_package_level_extension() -> None:
    from livery.workshop._extensions import declaration

    found = {
        entry.name: entry.value
        for entry in importlib.metadata.entry_points(group="workshop.extensions")
    }
    assert found["python"] == "livery.extensions.python"
    declared = declaration("python")
    assert declared is not None
    assert declared.levels == ("package",)


def test_the_extension_answers_each_python_package_as_the_kind_backend_does() -> None:
    """Every python package here, listing it or not, is answered as the backend does."""
    from dataclasses import replace

    import livery.workshop
    from livery.extensions.python import _backend
    from livery.workshop._packages import discover_packages
    from livery.workshop._queries import (
        CURRENT_VERSION,
        MODULE_ROOTS,
        PUBLIC_MODULES,
        REQUIREMENTS,
        VERSION_FILES,
        answer,
    )

    if not Path(livery.workshop.__file__).resolve().is_relative_to(ROOT):
        # The release train's isolated leg installs the workshop's wheel
        # alone, beside no checkout's packages.
        pytest.skip("the comparison reads this checkout's packages")
    listing = [
        replace(package, extensions=("python",))
        for package in discover_packages(ROOT)
        if package.kind == "python"
    ]
    assert listing, "no package here is of the python kind"
    for package in listing:
        assert answer(package, CURRENT_VERSION) == _backend.current_version(package)
        assert answer(package, VERSION_FILES) == tuple(
            _backend.stamp_version(package).homes()
        )
        requirements = answer(package, REQUIREMENTS)
        assert dict(requirements) == _backend.declared_requirements(package)
        assert answer(package, MODULE_ROOTS) == tuple(_backend.module_roots(package))
        assert answer(package, PUBLIC_MODULES) == tuple(
            _backend.public_modules(package)
        )


def test_the_python_steps_stamp_and_publish_as_the_kind_backend_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One package through the extension's steps, its twin through the backend."""
    from dataclasses import replace

    from livery.workshop import _lifecycle
    from livery.workshop._packages import Package
    from livery.workshop._registries import RegistryTarget

    def _member(name: str) -> Path:
        directory = tmp_path / name
        module = directory / "src" / "acme" / "core"
        module.mkdir(parents=True)
        (directory / "pyproject.toml").write_text(
            '[project]\nname = "acme-core"\nversion = "1.0.0"\n'
        )
        (module / "__init__.py").write_text('__version__ = "1.0.0"\n')
        return directory

    listing = Package(
        directory=_member("listing"),
        path="packages/listing",
        name="acme-core",
        kind="python",
        depends=(),
        extensions=("python",),
    )
    plain = replace(
        listing, directory=_member("plain"), path="packages/plain", extensions=()
    )
    stamped = _lifecycle.stamp(listing, tmp_path, "2.0.0")
    assert stamped == _lifecycle.stamp(plain, tmp_path, "2.0.0")
    assert stamped
    for name in ("pyproject.toml", "src/acme/core/__init__.py"):
        assert (listing.directory / name).read_text() == (
            plain.directory / name
        ).read_text()
    uploads: list[tuple[str, str, str]] = []

    def _upload(package: Package, *, index_url: str, token: str) -> bool:
        uploads.append((package.name, index_url, token))
        return True

    monkeypatch.setattr("livery.workshop._publish.publish_wheels", _upload)
    target = RegistryTarget(
        kind="python",
        url="https://idx.example/simple",
        publish_url="https://idx.example/",
        token="t",
    )
    assert _lifecycle.publish(listing, tmp_path, version="2.0.0", target=target)
    assert uploads == [("acme-core", "https://idx.example/", "t")]


def _verified_package(directory: Path) -> object:
    from livery.workshop._packages import Package

    return Package(
        directory=directory,
        path="packages/core",
        name="acme-core",
        kind="python",
        depends=(),
    )


def test_a_tree_without_a_package_refuses_to_verify(tmp_path: Path) -> None:
    from livery.extensions.python import _verify
    from livery.workshop._packages import Package

    (tmp_path / "src" / "empty").mkdir(parents=True)
    package = _verified_package(tmp_path)
    assert isinstance(package, Package)
    with pytest.raises(BaseException, match="holds no package directory"):
        _verify.import_name(package)


def test_a_namespace_rooted_package_verifies_the_module_it_declares(
    tmp_path: Path,
) -> None:
    # Three levels of namespace and no __init__.py: what an extension's
    # wheel ships, named by its build backend's module-name.
    from livery.extensions.python import _verify
    from livery.workshop._packages import Package

    (tmp_path / "src" / "acme" / "extensions" / "lint").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "acme-extensions-lint"\n\n'
        '[tool.uv.build-backend]\nmodule-name = "acme.extensions.lint"\n'
        "namespace = true\n"
    )
    package = _verified_package(tmp_path)
    assert isinstance(package, Package)
    assert _verify.import_name(package) == "acme.extensions.lint"


def test_a_red_verification_install_runs_no_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.extensions.python import _verify
    from livery.workshop._packages import Package

    tree = tmp_path / "scratch" / "tree"
    (tree / "packages" / "core" / "src" / "acme").mkdir(parents=True)
    (tree / "packages" / "core" / "src" / "acme" / "__init__.py").write_text("")
    installs: list[tuple[Path, str, str, str]] = []
    tested: list[str] = []

    def _install(venv: Path, python: str, requirement: str, index: str) -> int:
        installs.append((venv, python, requirement, index))
        return 1

    def _test(venv: Path, tree: Path, member: str, module: str) -> int:
        tested.append(module)
        return 0

    monkeypatch.setattr(_verify, "install", _install)
    monkeypatch.setattr(_verify, "run_tests", _test)
    package = Package(
        directory=tree / "packages" / "core",
        path="packages/core",
        name="acme-core",
        kind="python",
        depends=(),
    )
    code = _verify.verify(
        package, tree=tree, version="1.2.0", python="3.14", index="idx", extras="x"
    )
    assert code == 1
    assert installs == [
        (tmp_path / "scratch" / ".verify", "3.14", "acme-core[x]==1.2.0", "idx")
    ]
    assert tested == []


def test_a_module_outside_site_packages_runs_no_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The fallback first: the probe finds the module somewhere other than
    # site-packages, so the tests would prove the workspace, and none runs.
    from types import SimpleNamespace

    import livery.footman
    from livery.extensions.python import _verify

    ran: list[list[str]] = []
    codes = [1]

    def _run(argv: list[str], **_kwargs: object) -> object:
        ran.append(argv)
        return SimpleNamespace(code=codes[0])

    monkeypatch.setattr(livery.footman, "run", _run)
    venv = tmp_path / ".verify"
    code = _verify.run_tests(venv, tmp_path / "tree", "core", "acme.core")
    assert code == 1
    assert len(ran) == 1
    assert ran[0][0] == str(venv / "bin" / "python")
    assert "site-packages" in ran[0][2]
    assert "acme.core does not import from site-packages" in capsys.readouterr().out
    # Imported from site-packages, the member's tests run at the tree.
    ran.clear()
    codes[0] = 0
    assert _verify.run_tests(venv, tmp_path / "tree", "core", "acme.core") == 0
    assert ran[1] == [
        str(venv / "bin" / "python"),
        "-m",
        "pytest",
        "packages/core/tests",
        "-p",
        "no:cacheprovider",
    ]


def test_the_released_version_installs_alone_into_a_plain_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A plain venv from uv, then the released requirement from the index
    # with the test runner: no lock, no workspace member, so the package
    # can come from the index alone.
    from types import SimpleNamespace

    import livery.toolroom.tools as toolroom
    from livery.extensions.python import _verify

    commands: list[tuple[str, ...]] = []

    def _opts(**_kwargs: object) -> object:
        def _run(*args: str) -> object:
            commands.append(args)
            return SimpleNamespace(code=0)

        return _run

    monkeypatch.setattr(toolroom, "uv", SimpleNamespace(opts=_opts))
    venv = tmp_path / ".verify"
    assert _verify.install(venv, "3.14", "acme-core[x]==1.2.0", "idx") == 0
    assert commands == [
        ("venv", str(venv), "--python", "3.14"),
        (
            "pip",
            "install",
            "--python",
            str(venv / "bin" / "python"),
            "--index",
            "idx",
            "acme-core[x]==1.2.0",
            "pytest",
            "pytest-xdist",
        ),
    ]


def test_a_python_package_is_categorised_alike_listing_the_extension_or_not() -> None:
    """One table, the extension's, answers both ways for every python package."""
    from dataclasses import replace

    import livery.workshop
    from livery.workshop._categories import category_of
    from livery.workshop._packages import discover_packages

    if not Path(livery.workshop.__file__).resolve().is_relative_to(ROOT):
        pytest.skip("the comparison reads this checkout's packages")
    paths = (
        "src/livery/x/mod.py",
        "tests/test_x.py",
        "tests/x_test.py",
        "tests/conftest.py",
        "docs/index.md",
        "docs/examples/first.py",
        "pyproject.toml",
    )
    for package in discover_packages(ROOT):
        if package.kind != "python":
            continue
        listing = replace(package, extensions=("python",))
        plain = replace(package, extensions=())
        for path in paths:
            assert category_of(listing, path) == category_of(plain, path), path
        assert category_of(plain, "src/livery/x/mod.py").supplier == "python"
