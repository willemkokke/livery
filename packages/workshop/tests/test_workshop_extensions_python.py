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


def _replayed_package(directory: Path) -> object:
    from livery.workshop._packages import Package

    return Package(
        directory=directory,
        path="packages/core",
        name="acme-core",
        kind="python",
        depends=(),
    )


def test_a_tree_without_a_package_refuses_to_replay(tmp_path: Path) -> None:
    from livery.extensions.python import _replay
    from livery.workshop._packages import Package

    (tmp_path / "src" / "empty").mkdir(parents=True)
    package = _replayed_package(tmp_path)
    assert isinstance(package, Package)
    with pytest.raises(BaseException, match="holds no package directory"):
        _replay.import_name(package)


def test_a_namespace_rooted_package_replays_the_module_it_declares(
    tmp_path: Path,
) -> None:
    # Three levels of namespace and no __init__.py: what an extension's
    # wheel ships, named by its build backend's module-name.
    from livery.extensions.python import _replay
    from livery.workshop._packages import Package

    (tmp_path / "src" / "acme" / "extensions" / "lint").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "acme-extensions-lint"\n\n'
        '[tool.uv.build-backend]\nmodule-name = "acme.extensions.lint"\n'
        "namespace = true\n"
    )
    package = _replayed_package(tmp_path)
    assert isinstance(package, Package)
    assert _replay.import_name(package) == "acme.extensions.lint"


def test_a_red_replay_install_runs_no_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.extensions.python import _replay
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

    monkeypatch.setattr(_replay, "install", _install)
    monkeypatch.setattr(_replay, "run_tests", _test)
    package = Package(
        directory=tree / "packages" / "core",
        path="packages/core",
        name="acme-core",
        kind="python",
        depends=(),
    )
    code = _replay.replay(
        package, tree=tree, version="1.2.0", python="3.14", index="idx", extras="x"
    )
    assert code == 1
    assert installs == [
        (tmp_path / "scratch" / ".replay", "3.14", "acme-core[x]==1.2.0", "idx")
    ]
    assert tested == []


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
