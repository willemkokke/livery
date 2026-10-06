"""The native seeds are written in the style the clang-format extension renders.

The seeds are the workshop's and the style is the extension's, so the
property spans both: a member born from the seeds passes its own
format check from its first commit.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import livery.extensions.clang.format._extension as declaration
import livery.toolroom.tools.api as tools
from livery.extensions.clang.format import _checks as clang_format
from livery.workshop import _checks as registry
from livery.workshop.api import Package

# The workshop's test helper: pytest's pythonpath carries every tests
# directory, which basedpyright's search path does not.
from workshop_composed import seed_into  # pyright: ignore[reportMissingImports]


@pytest.fixture
def registered() -> Iterator[None]:
    """The extension's check registered, as the mount registers it when listed."""
    from livery.workshop._extensions import register_declared_checks

    state = registry.snapshot()
    register_declared_checks("clang-format", declaration)
    try:
        yield
    finally:
        registry.restore(state)


def _supplied() -> bool:
    try:
        return (
            tools.clang_format.opts(nofail=True, recorded=False)("--version").code == 0
        )
    except (OSError, tools.ToolError):
        return False


@pytest.mark.parametrize(
    ("tree", "kind", "name"),
    [
        ("package-cpp-conan", "cpp-conan", "acme-native"),
        ("package-python-nanobind", "python-nanobind", "ci-e2e-loop-loop-native"),
        ("package-python-nanobind", "python-nanobind", "x-y"),
    ],
)
def test_a_member_born_from_the_native_seeds_is_in_style(
    tmp_path: Path, registered: None, tree: str, kind: str, name: str
) -> None:
    from livery.workshop._shipped_files import settle_package

    if not _supplied():
        pytest.skip("clang-format is not supplied on this host")
    destination = tmp_path / "packages" / "native"
    seed_into(
        destination,
        tree,
        {
            "package_name": name,
            "package_description": "A native member.",
            "namespace_package": "acme",
            "project_name": "acme",
        },
    )
    settle_package(destination, kind)
    assert (destination / ".clang-format").is_file()
    package = Package(
        directory=destination,
        path="packages/native",
        name=name,
        kind=kind,
        depends=(),
    )
    sources = tuple(
        path
        for suffix in clang_format.SUFFIXES
        for path in sorted(destination.rglob(f"*{suffix}"))
    )
    assert sources  # the seeds carry C or C++ to judge
    clang_format.run_format(package, sources, fix=False)
