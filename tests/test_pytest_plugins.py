"""A change to any module a member here names as a pytest plugin runs every suite.

pytest loads each of them into every session it starts, so the affected
engine widens a change to one to every member pytest runs over.
"""

from __future__ import annotations

from pathlib import Path

from livery.extensions.python._backend import plugin_hosts
from livery.workshop import Package, discover_packages, extension_set
from livery.workshop._graph import affected_from_paths

ROOT = Path(__file__).resolve().parents[1]


def _source(package: Package, module: str) -> str:
    """The root-relative path of *module*'s file under *package*'s ``src``."""
    base = package.directory.joinpath("src", *module.split("."))
    found = base.with_suffix(".py")
    if not found.is_file():
        found = base / "__init__.py"
    return found.relative_to(ROOT).as_posix()


def test_every_pytest_plugin_module_here_reaches_every_python_member() -> None:
    packages = discover_packages(ROOT)
    plugins = [
        _source(package, module)
        for package in packages
        for module, programs in plugin_hosts(package).items()
        if "pytest" in programs
    ]
    assert plugins, "a member here names a pytest plugin"
    python = [
        package.path for package in packages if "python" in extension_set(package)
    ]
    for path in plugins:
        scope = affected_from_paths(ROOT, packages, [path])
        assert scope is not None, path
        assert [package.path for package in scope.packages] == python, path
