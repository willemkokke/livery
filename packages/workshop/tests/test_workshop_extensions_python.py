"""The python extension inside the wheel: its load order, its entry point, its level."""

from __future__ import annotations

import importlib.metadata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def test_python_s_build_refuses_a_platform_wheel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A platform tag is compiled code no leg built for any other platform."""
    from types import SimpleNamespace

    import livery.toolroom.tools as toolroom
    from livery.extensions.python import _backend
    from livery.footman import Failed
    from livery.workshop._packages import Package

    directory = tmp_path / "packages" / "core"
    directory.mkdir(parents=True)
    package = Package(
        directory=directory,
        path="packages/core",
        name="acme-core",
        depends=(),
        extensions=("python",),
    )
    # What uv build writes into the directory it is handed, by file name.
    wrote = ["acme_core-1.0.0-cp314-cp314-macosx_11_0_arm64.whl"]

    def _opts(**_kwargs: object) -> object:
        def _run(*args: str) -> object:
            out = Path(args[args.index("--out-dir") + 1])
            out.mkdir(parents=True, exist_ok=True)
            for name in wrote:
                (out / name).write_bytes(b"")
            return SimpleNamespace(code=0, stdout="", stderr="")

        return _run

    monkeypatch.setattr(toolroom, "uv", SimpleNamespace(opts=_opts))
    with pytest.raises(Failed) as refused:
        _backend.build(package, tmp_path)
    assert str(refused.value) == (
        "acme-core: the build produced a wheel tagged for a platform,"
        " acme_core-1.0.0-cp314-cp314-macosx_11_0_arm64.whl; python's build ships"
        " a pure wheel, tagged none-any, so a package with compiled code lists the"
        " extension that builds its platform wheels"
    )
    # A pure wheel beside the sdist is the build's answer.
    wrote[:] = ["acme_core-1.0.0-py3-none-any.whl", "acme_core-1.0.0.tar.gz"]
    assert _backend.build(package, tmp_path) == directory / "dist"


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


def test_the_extension_answers_each_python_package_here_as_its_code_does() -> None:
    """Every package here holding python is answered as python's code reads it."""
    import livery.workshop
    from livery.extensions.python import _backend
    from livery.workshop._composition import extension_set
    from livery.workshop._packages import discover_packages
    from livery.workshop._queries import (
        CURRENT_VERSION,
        MODULE_ROOTS,
        PUBLIC_MODULES,
        REFERENCES,
        REQUIREMENTS,
        VERSION_FILES,
        answer,
    )

    if not Path(livery.workshop.__file__).resolve().is_relative_to(ROOT):
        # The release train's isolated leg installs the workshop's wheel
        # alone, beside no checkout's packages.
        pytest.skip("the comparison reads this checkout's packages")
    listing = [
        package
        for package in discover_packages(ROOT)
        if "python" in extension_set(package)
    ]
    assert listing, "no package here holds python"
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
        assert answer(package, REFERENCES) == _backend.references(package)


def test_python_answers_its_runners_while_pytest_is_listed_and_a_package_holds_it(
    tmp_path: Path,
) -> None:
    """Pytest's steps ask for the functions that run the suites and the examples."""
    from livery.extensions.python import _backend
    from livery.workshop import Query, answer
    from workshop_extension_fakes import package_contract, root_contract

    suites: Query[object] = Query("python.test.runner")
    examples: Query[object] = Query("python.examples.runner")
    # The fallbacks first: no package holds python, then a package holds
    # it and pytest is not listed, and nothing answers either way.
    root_contract(tmp_path, '["pytest"]')
    package_contract(tmp_path, "lib", '["cmake"]')
    assert answer(tmp_path, suites) is None
    assert answer(tmp_path, examples) is None
    package_contract(tmp_path, "core", '["python"]')
    root_contract(tmp_path, "[]")
    assert answer(tmp_path, suites) is None
    assert answer(tmp_path, examples) is None
    # Listed beside a package that holds python, pytest is answered.
    root_contract(tmp_path, '["pytest"]')
    assert answer(tmp_path, suites) is _backend.run_test
    assert answer(tmp_path, examples) is _backend.run_examples


def test_the_answer_marks_each_reference_lazy_guarded_or_made_at_import_time(
    tmp_path: Path,
) -> None:
    from livery.extensions.python._backend import references
    from livery.workshop._packages import Package, ReferencedName

    directory = tmp_path / "packages" / "core"
    source = directory / "src" / "acme" / "core" / "__init__.py"
    source.parent.mkdir(parents=True)
    (directory / "pyproject.toml").write_text('[project]\nname = "acme-core"\n')
    source.write_text(
        "import json\n"
        "from typing import TYPE_CHECKING\n\n"
        "if TYPE_CHECKING:\n"
        "    from acme.shapes import Shape\n\n"
        "try:\n"
        "    import yaml\n"
        "except ImportError:\n"
        "    yaml = None\n\n\n"
        "def load() -> object:\n"
        "    import tomllib\n\n"
        "    return tomllib\n\n\n"
        "class Loader:\n"
        "    def read(self) -> object:\n"
        "        from acme.lazy import helper\n\n"
        "        return helper\n"
    )
    package = Package(
        directory=directory,
        path="packages/core",
        name="acme-core",
        depends=(),
        extensions=("python",),
    )
    (record,) = references(package)
    assert record.path == "packages/core/src/acme/core/__init__.py"
    assert record.module == "acme.core"
    # Guarded and lazy first: a type checker's import, one the source
    # runs without, and one inside a function and a method.
    guarded = [ref for ref in record.references if ref.type_checking or ref.optional]
    assert guarded == [
        ReferencedName("acme.shapes", type_checking=True),
        ReferencedName("acme.shapes.Shape", type_checking=True),
        ReferencedName("yaml", optional=True),
    ]
    assert [ref for ref in record.references if ref.lazy] == [
        ReferencedName("tomllib", lazy=True),
        ReferencedName("acme.lazy", lazy=True),
        ReferencedName("acme.lazy.helper", lazy=True),
    ]
    # Then the module level, made at import time.
    assert [
        ref for ref in record.references if ref not in guarded and not ref.lazy
    ] == [
        ReferencedName("json"),
        ReferencedName("typing"),
        ReferencedName("typing.TYPE_CHECKING"),
    ]
    assert record.accounted == ()


def test_the_python_steps_stamp_and_publish_as_its_code_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One package through the extension's steps, its twin through its code."""
    from dataclasses import replace

    from livery.extensions.python import _backend
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
        depends=(),
        extensions=("python",),
    )
    plain = replace(
        listing, directory=_member("plain"), path="packages/plain", extensions=()
    )
    stamped = _lifecycle.stamp(listing, tmp_path, "2.0.0")
    assert stamped == _backend.stamp_version(plain).stamp("2.0.0")
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


def test_the_python_isolated_test_runs_the_release_leg(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One package through the test phase's isolated side: the extension's step."""
    from livery.workshop import _lifecycle, _steps
    from livery.workshop._packages import Package

    legs: list[tuple[str, str, tuple[Path, ...]]] = []

    def _leg(
        package: Package,
        root: Path,
        *,
        release_dirs: tuple[Path, ...],
        resolution: str,
    ) -> dict[str, str]:
        legs.append((package.path, resolution, release_dirs))
        return {"acme-core": "1.0.0"}

    monkeypatch.setattr("livery.extensions.python._backend.run_isolated_test", _leg)
    listing = Package(
        directory=tmp_path / "listing",
        path="packages/listing",
        name="acme-core",
        depends=(),
        extensions=("python",),
    )
    dirs = (tmp_path / "dist",)
    assert _lifecycle.has_isolated_test(listing)
    resolved = _lifecycle.isolated_test(
        listing, tmp_path, resolution="head", release_dirs=dirs
    )
    assert resolved == {"acme-core": "1.0.0"}
    assert legs == [("packages/listing", "head", dirs)]
    # The listing package's leg is the extension's step in the walk; the
    # gate, which runs the tree side, registers no step for it.
    assert "test.python: packages/listing runs (a python package)" in (
        capsys.readouterr().out
    )
    assert "test.python" not in _steps.steps_by_name()


def _verified_package(directory: Path) -> object:
    from livery.workshop._packages import Package

    return Package(
        directory=directory,
        path="packages/core",
        name="acme-core",
        depends=(),
        extensions=("python",),
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
        depends=(),
        extensions=("python",),
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


def test_each_python_package_here_is_categorised_by_python_s_table() -> None:
    """The extension's table answers for every package here that holds python."""
    import livery.workshop
    from livery.workshop._categories import category_of
    from livery.workshop._composition import extension_set
    from livery.workshop._packages import discover_packages

    if not Path(livery.workshop.__file__).resolve().is_relative_to(ROOT):
        pytest.skip("the categories are read from this checkout's packages")
    expected = {
        "src/livery/x/mod.py": "source",
        "tests/test_x.py": "test",
        "tests/x_test.py": "test",
        "tests/conftest.py": "test-support",
        "docs/index.md": "prose",
        "docs/examples/first.py": "example",
        "pyproject.toml": "configuration",
    }
    holding = [
        package
        for package in discover_packages(ROOT)
        if "python" in extension_set(package)
    ]
    assert holding
    for package in holding:
        found = {path: category_of(package, path).name for path in expected}
        assert found == expected, package.path
        assert category_of(package, "src/livery/x/mod.py").supplier == "python"
