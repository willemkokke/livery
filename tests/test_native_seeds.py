"""The native seeds pass the native extensions' checks from their first commit.

The seeds are the workshop's, and the style and the lint checks are
the extensions', so each property spans both.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

import livery.toolroom.tools as tools
from livery.extensions.clang.tidy import _checks as clang_tidy
from livery.workshop import GateContext, Package
from livery.workshop import _checks as registry
from livery.workshop._kinds import kind_stands_for

# The workshop's test helper: pytest's pythonpath carries every tests
# directory, which basedpyright's search path does not.
from workshop_composed import seed_into  # pyright: ignore[reportMissingImports]


@pytest.fixture
def registered() -> Iterator[None]:
    """The extensions' checks registered, as the mount registers them when listed."""
    from livery.workshop._extensions import declaration, register_declared

    state = registry.snapshot()
    for name in ("clang-format", "clang-tidy"):
        found = declaration(name)
        assert found is not None
        register_declared(name, found.additions)
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
    settle_package(destination, kind_stands_for(kind))
    assert (destination / ".clang-format").is_file()
    package = Package(
        directory=destination,
        path="packages/native",
        name=name,
        kind=kind,
        depends=(),
    )
    suffixes = {
        suffix
        for claim in registry.check_for("format.clang-format").claims
        for suffix in claim.suffixes
    }
    sources = tuple(
        path
        for suffix in sorted(suffixes)
        for path in sorted(destination.rglob(f"*{suffix}"))
    )
    assert sources  # the seeds carry C or C++ to judge
    # The gate's own run over the files a run names, as `fm check <paths>`
    # hands them: clang-format's words from the package's directory, so
    # its own .clang-format sets the style.
    named = tuple(path.relative_to(destination).as_posix() for path in sources)
    registry.check_for("format.clang-format").run(
        GateContext(
            root=tmp_path,
            packages=(package,),
            package=package,
            files=tuple(f"{package.path}/{path}" for path in named),
            catalogue={package.path: tuple((path, "source") for path in named)},
        )
    )


@pytest.mark.skipif(
    any(shutil.which(tool) is None for tool in ("cmake", "ninja", "cc", "c++"))
    or sys.platform == "win32",
    reason="the build needs the host's C++ toolchain; Windows builds with MSVC",
)
def test_a_tidy_finding_in_a_seeded_member_turns_the_gate_red(
    tmp_path: Path, registered: None
) -> None:
    from livery.workshop import compile_commands
    from livery.workshop._backends import _cpp_conan
    from livery.workshop._shipped_files import settle_package

    destination = tmp_path / "packages" / "native"
    seed_into(
        destination,
        "package-cpp-conan",
        {
            "package_name": "acme-native",
            "package_description": "A native member.",
            "namespace_package": "acme",
            "project_name": "acme",
        },
    )
    settle_package(destination, kind_stands_for("cpp-conan"))
    package = Package(
        directory=destination,
        path="packages/native",
        name="acme-native",
        kind="cpp-conan",
        depends=(),
    )
    source = destination / "src" / "native.cpp"
    source.write_text(
        source.read_text().replace(
            "} // namespace native",
            "int branch(int a) {\n"
            "    if (a > 0) {\n"
            "        return 1;\n"
            "    } else {\n"
            "        return 1;\n"
            "    }\n"
            "}\n"
            "\n"
            "} // namespace native",
        )
    )
    _cpp_conan.gate_build(package, tmp_path)
    database = compile_commands(package)
    assert database is not None and database.is_file()
    with pytest.raises(BaseException, match="clang-tidy found something") as caught:
        clang_tidy.run_lint(package, (source,), database)
    assert "branch" in str(caught.value) or "bugprone" in str(caught.value)
