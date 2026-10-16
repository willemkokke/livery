"""The python extension: its distribution, its declaration, its answers and its steps.

Its own distribution first: what it declares and requires, the kit,
and the loop's dev wheels. Then the build's refusal of a platform
wheel, the answers a python package gives, the stamp, the publish and
the verification, and the package docs its wheel carries beside the
module it ships.
"""

from __future__ import annotations

import importlib.metadata
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from extensions_python_workspace import (
    package_contract,
    root_contract,
    skip_unless_installed,
)
from livery.extensions.python._backend import (
    materialise_module_docs,
    module_docs,
    module_docs_dir,
)
from livery.footman import Failed
from livery.workshop import discover_packages
from livery.workshop.testing import CLAUSES, Clause

ROOT = Path(__file__).resolve().parents[4]

_FAILURES = (SystemExit, Failed)


def _mount(root: Path) -> tuple[str, ...]:
    """Mount what *root*'s lists hold, as a verb there would; the names mounted."""
    from livery.footman import _registry as registry
    from livery.workshop._extensions import mount_extensions

    with registry.capture():
        return mount_extensions(root)


def _in_this_checkout() -> None:
    """Skip the running test unless the extension runs from this checkout.

    The release train's isolated leg installs this distribution's wheel
    alone, beside no checkout's packages.
    """
    import livery.extensions.python

    here = Path(livery.extensions.python.__file__).resolve()
    if not here.is_relative_to(ROOT):
        pytest.skip("the test reads this checkout's packages")


# --- its own distribution -----------------------------------------------------


def test_its_own_distribution_ships_python_and_the_workshop_wheel_none() -> None:
    from livery.workshop._extensions import declaration, shipping_distribution

    ours = {
        (entry.group, entry.name, entry.value)
        for entry in importlib.metadata.distribution(
            "livery-extensions-python"
        ).entry_points
    }
    assert ours == {("workshop.extensions", "python", "livery.extensions.python")}
    theirs = importlib.metadata.distribution("livery-workshop").entry_points
    assert [entry for entry in theirs if entry.group == "workshop.extensions"] == []
    shipped = shipping_distribution("livery.extensions.python")
    assert shipped is not None and shipped.name == "livery-extensions-python"
    # At runtime it requires the base, footman, the forge's index probe
    # and the toolroom's tools, and nothing else.
    required = importlib.metadata.requires("livery-extensions-python") or []
    assert sorted(required) == [
        "livery-footman>=0.60.0",
        "livery-forge>=0.7.0",
        "livery-toolroom>=0.11.0",
        "livery-workshop>=0.10.0",
    ]
    declared = declaration("python")
    assert declared is not None
    assert declared.requires == ()
    assert declared.plugin == ""


def test_a_package_listing_python_mounts_it(tmp_path: Path) -> None:
    (tmp_path / "workshop.toml").write_text('[workspace]\nextensions = ["python"]\n')
    assert _mount(tmp_path) == ("python",)


@pytest.mark.parametrize("clause", CLAUSES, ids=lambda clause: clause.name)
def test_python_conforms(clause: Clause, tmp_path: Path) -> None:
    from livery.workshop._steps import StepRecord, steps_by_name
    from livery.workshop.testing import Subject

    (tmp_path / "workshop.toml").write_text('[workspace]\nextensions = ["python"]\n')
    _mount(tmp_path)
    # python registers the layering phase's three steps in the gate; its
    # stamp, build, publish, test, verify and run steps run in a release
    # or a verb, and none of them reaches the gate.
    steps: tuple[StepRecord, ...] = tuple(
        record for record in steps_by_name().values() if record.extension == "python"
    )
    assert sorted(record.name for record in steps) == [
        "layering.declared-imports",
        "layering.private-reaches",
        "layering.runner-terminal",
    ]
    subject = Subject("livery.extensions.python", sets=(("python",),), steps=steps)
    assert clause.judge(subject) == []


def test_the_loop_s_dev_wheels_hold_this_distribution() -> None:
    # The loop's members loop-echo and loop-native are python
    # distributions. The dev wheels the pass builds and publishes before
    # the birth hold this distribution, so the newborn installs it, and
    # each python member builds and publishes through python's steps
    # from it.
    from livery.workshop._composition import closure
    from livery.workshop._e2e import LOOP_MEMBERS, dev_members, loop_stack
    from livery.workshop._extensions import installed_declaration

    _in_this_checkout()
    # The loop's members need nanobind too, so the stack is read where it
    # is installed.
    skip_unless_installed("nanobind")
    templates = {member.name: member.template for member in LOOP_MEMBERS}
    assert templates["loop-echo"] == "python-library"
    # The canonical list leaves python out, since nanobind requires it,
    # and the stack's set holds it.
    assert "python" in closure(loop_stack(), installed_declaration)
    assert "extensions/python" in dev_members(ROOT, loop_stack())


# --- the build ------------------------------------------------------------------


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


def test_the_extension_answers_each_python_package_here_as_its_code_does() -> None:
    """Every package here holding python is answered as python's code reads it."""
    from livery.extensions.python import _backend
    from livery.workshop._composition import extension_set
    from livery.workshop._queries import (
        CURRENT_VERSION,
        MODULE_ROOTS,
        PUBLIC_MODULES,
        REFERENCES,
        REQUIREMENTS,
        VERSION_FILES,
        answer,
    )

    _in_this_checkout()
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

    skip_unless_installed("pytest")

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
    from livery.workshop import ReferencedName, SourceReferences
    from livery.workshop._packages import Package

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
    assert isinstance(record, SourceReferences)
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

    monkeypatch.setattr("livery.extensions.python._backend.publish_wheels", _upload)
    target = RegistryTarget(
        kind="python",
        url="https://idx.example/simple",
        publish_url="https://idx.example/",
        token="t",
    )
    assert _lifecycle.publish(listing, tmp_path, version="2.0.0", target=target)
    assert uploads == [("acme-core", "https://idx.example/", "t")]


def test_publish_refuses_without_an_upload_address(tmp_path: Path) -> None:
    # The refusal first: uv's silent default is PyPI, so an empty
    # publish address must die loudly before any upload, teaching
    # the declaration that fixes it.
    from livery.extensions.python._backend import publish_wheels
    from livery.workshop._packages import Package

    package = Package(
        directory=tmp_path,
        path="packages/thing",
        name="thing",
        depends=(),
        extensions=("python",),
    )
    with pytest.raises(_FAILURES) as caught:
        publish_wheels(package, index_url="", token="t")
    message = str(caught.value)
    assert "never defaulted" in message
    assert "[registries.python] publish" in message


def test_an_empty_publish_token_variable_never_reaches_uv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The fallback first: CI renders UV_PUBLISH_TOKEN from a secret that
    # may be absent, which arrives as an empty string, and uv treats a
    # set but empty token as a credential, skipping trusted publishing.
    # The empty variable is dropped from the child; a carried value and
    # an explicit token both stay.
    from types import SimpleNamespace
    from typing import cast

    import livery.toolroom.tools as tools
    from livery.extensions.python._backend import publish_wheels
    from livery.workshop._packages import Package

    package = Package(
        directory=tmp_path,
        path="packages/thing",
        name="thing",
        depends=(),
        extensions=("python",),
    )
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "thing-0.1.0-py3-none-any.whl").write_bytes(b"")
    seen: list[tuple[object, tuple[str, ...]]] = []

    def _opts(**kwargs: object) -> object:
        def _run(*args: str) -> object:
            seen.append((kwargs.get("env"), args))
            return SimpleNamespace(code=0, stdout="", stderr="")

        return _run

    monkeypatch.setattr(tools, "uv", SimpleNamespace(opts=_opts))
    monkeypatch.setenv("UV_PUBLISH_TOKEN", "")
    assert publish_wheels(package, index_url="https://index.test/upload") is True
    env = cast("dict[str, str]", seen[-1][0])
    assert "UV_PUBLISH_TOKEN" not in env
    assert "--token" not in seen[-1][1]
    monkeypatch.setenv("UV_PUBLISH_TOKEN", "carried")
    publish_wheels(package, index_url="https://index.test/upload")
    env = cast("dict[str, str]", seen[-1][0])
    assert env["UV_PUBLISH_TOKEN"] == "carried"
    publish_wheels(package, index_url="https://index.test/upload", token="given")
    args = seen[-1][1]
    assert args[args.index("--token") + 1] == "given"


def test_every_index_wording_of_a_duplicate_upload_is_walked_past(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The fallback first: a re-run must walk past what an earlier
    # attempt landed, and each index refuses the duplicate in its own
    # words. Gitea says the version already exists, GitLab's registry
    # says the file name has already been taken; any other refusal
    # surfaces verbatim.
    from types import SimpleNamespace

    import livery.toolroom.tools as tools
    from livery.extensions.python._backend import publish_wheels
    from livery.workshop._packages import Package

    package = Package(
        directory=tmp_path,
        path="packages/thing",
        name="thing",
        depends=(),
        extensions=("python",),
    )
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "thing-0.1.0-py3-none-any.whl").write_bytes(b"")
    refusal = {"text": ""}

    def _opts(**kwargs: object) -> object:
        def _run(*args: str) -> object:
            return SimpleNamespace(code=2, stdout="", stderr=refusal["text"])

        return _run

    monkeypatch.setattr(tools, "uv", SimpleNamespace(opts=_opts))
    refusal["text"] = (
        'Server returned status code 400 Bad Request. Server says: {"message":'
        '"400 Bad request - Validation failed: File name has already been taken"}'
    )
    assert publish_wheels(package, index_url="https://index.test/upload") is False
    refusal["text"] = "409 Conflict: package version already exists"
    assert publish_wheels(package, index_url="https://index.test/upload") is False
    refusal["text"] = "403 Forbidden: bad credentials"
    with pytest.raises(_FAILURES) as caught:
        publish_wheels(package, index_url="https://index.test/upload")
    assert "bad credentials" in str(caught.value)


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
    from livery.workshop._categories import category_of
    from livery.workshop._composition import extension_set

    _in_this_checkout()
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


# A package's docs/ rides in its wheel: the python extension copies it
# beside the module it ships. The refusals and fallbacks first.


def _docs_workspace(tmp_path: Path) -> Path:
    """Members core, with pages under docs/, and bare, with none.

    Each member is the package ``acme.<name>``.
    """
    root = tmp_path / "ws"
    for name, pages in (("core", ("index.md", "guide.md")), ("bare", ())):
        member = root / "packages" / name
        (member / "src" / "acme" / name).mkdir(parents=True)
        (member / "src" / "acme" / name / "__init__.py").write_text("")
        (member / "workshop.toml").write_text(
            f'extensions = ["python"]\nname = "acme-{name}"\n'
        )
        (member / "pyproject.toml").write_text(f'[project]\nname = "acme-{name}"\n')
        if pages:
            (member / "docs").mkdir()
            for page in pages:
                (member / "docs" / page).write_text(f"# {page}\n")
    (root / "workshop.toml").write_text('[workspace]\nname = "acme"\nextensions = []\n')
    return root


def test_a_docsless_package_gets_its_stale_wheel_copy_removed(
    tmp_path: Path,
) -> None:
    root = _docs_workspace(tmp_path)
    bare = next(p for p in discover_packages(root) if p.directory.name == "bare")
    target = module_docs_dir(bare)
    assert target is not None
    target.mkdir(parents=True)
    (target / "stale.md").write_text("stale\n")
    assert materialise_module_docs(bare) is None
    assert not target.exists()


def test_a_root_with_no_public_names_keeps_its_docs_where_the_wheel_ships(
    tmp_path: Path,
) -> None:
    # No __init__.py marks the root, so nothing is found until the build
    # names the module it ships.
    root = _docs_workspace(tmp_path)
    core = next(p for p in discover_packages(root) if p.directory.name == "core")
    module = core.directory / "src" / "acme" / "core"
    (module / "__init__.py").unlink()
    (module / "_extension.py").write_text("API_VERSION = 1\n")
    assert module_docs_dir(core) is None
    # With nowhere to go, no copy is made, and a build carries none.
    assert materialise_module_docs(core) is None
    with module_docs(core) as copied:
        assert copied is None
    assert not list(core.directory.rglob("_docs"))
    (core.directory / "pyproject.toml").write_text(
        '[project]\nname = "acme-core"\n\n'
        '[tool.uv.build-backend]\nmodule-name = "acme.core"\n'
    )
    assert module_docs_dir(core) == module / "_docs"


def test_a_builds_docs_copy_is_gone_after_the_build_failed_or_not(
    tmp_path: Path,
) -> None:
    # Left in the source tree, the docs' example files read as the
    # package's own code to every checker that walks it.
    root = _docs_workspace(tmp_path)
    core = next(p for p in discover_packages(root) if p.directory.name == "core")
    target = module_docs_dir(core)
    assert target is not None
    with pytest.raises(RuntimeError, match="the build failed"), module_docs(core):
        assert (target / "guide.md").is_file()
        raise RuntimeError("the build failed")
    assert not target.exists()
    with module_docs(core) as copied:
        assert copied == target and (target / "guide.md").is_file()
    assert not target.exists()


def test_the_wheel_side_docs_refresh_whole(tmp_path: Path) -> None:
    root = _docs_workspace(tmp_path)
    core = next(p for p in discover_packages(root) if p.directory.name == "core")
    target = materialise_module_docs(core)
    assert target is not None and (target / "guide.md").is_file()
    (target / "stale.md").write_text("stale\n")
    (core.directory / "docs" / "guide.md").write_text("# edited\n")
    target = materialise_module_docs(core)
    assert target is not None
    assert not (target / "stale.md").exists()
    assert (target / "guide.md").read_text() == "# edited\n"


# The venv's meter: coverage.py's own startup hook, armed by the runner.


def _python_of_the_venv(env: dict[str, str], code: str, cwd: Path) -> str:
    """Run *code* in a fresh interpreter of this venv; what it printed."""
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def _unarmed() -> dict[str, str]:
    """This process's environment without the variables that arm a meter."""
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("COVERAGE_", "COV_CORE_"))
    }


def test_an_unarmed_python_of_the_venv_imports_no_coverage(tmp_path: Path) -> None:
    # Every fm start, test worker and subprocess pays for what the
    # venv's startup hooks import. Coverage's own hook imports coverage
    # only when the runner arms it; a hook that imported it anyway cost
    # most of a bare interpreter start.
    found = _python_of_the_venv(
        _unarmed(), "import sys; print('coverage' in sys.modules)", tmp_path
    )
    assert found == "False"


def test_an_armed_python_of_the_venv_meters_through_coverages_own_hook(
    tmp_path: Path,
) -> None:
    pytest.importorskip("coverage")
    config = tmp_path / "coverage.ini"
    config.write_text(
        f"[run]\nparallel = true\ndata_file = {(tmp_path / '.coverage').as_posix()}\n"
    )
    env = {**_unarmed(), "COVERAGE_PROCESS_START": str(config)}
    _python_of_the_venv(env, "x = 1", tmp_path)
    assert list(tmp_path.glob(".coverage.*"))


def test_the_python_entry_module_refuses_a_name_it_does_not_export() -> None:
    import livery.extensions.python as python_module

    # The refused name is a function of the module that defines the
    # exported ones: only the declared names are served.
    with pytest.raises(
        AttributeError,
        match=re.escape(
            "module 'livery.extensions.python' has no attribute"
            " 'materialise_module_docs'"
        ),
    ):
        python_module.materialise_module_docs  # noqa: B018  # pyright: ignore[reportAttributeAccessIssue]


def test_an_entry_module_serves_what_the_defining_module_holds_at_each_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import livery.extensions.python as python_module
    from livery.extensions.python import _backend

    # A test patches the defining module, and a reader of the entry
    # module sees the patch.
    tested = object()
    monkeypatch.setattr(_backend, "run_isolated_test", tested)
    assert python_module.run_isolated_test is tested
    monkeypatch.undo()
    assert python_module.run_isolated_test is _backend.run_isolated_test
