"""Python's requirement writers, and the one parse its layering steps share.

The writers put a sibling's floor into a package's ``pyproject.toml``:
a runtime requirement into its dependencies, a test requirement into
its test extra. Each refuses a manifest with no ``[project]`` and
leaves a sibling of another ecosystem to that ecosystem's writer. The
layering phase parses each source once, whatever reads it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from extensions_python_workspace import skip_unless_installed
from livery.extensions.python import _backend as _python
from livery.workshop import Package, verify_workspace

_FAILURES = (BaseException,)


def _package(root: Path, name: str) -> None:
    directory = root / "packages" / name
    directory.mkdir(parents=True)
    (directory / "workshop.toml").write_text(
        f'extensions = ["python"]\nname = "livery-{name}"\n'
    )
    (directory / "pyproject.toml").write_text(
        f'[project]\nname = "livery-{name}"\ndependencies = []\n'
    )


def _forge_stub(root: Path) -> None:
    # A member the graph's edges point at, with one clean source.
    src = root / "packages" / "forge" / "src"
    src.mkdir(parents=True)
    (src / "ok.py").write_text("import json\n")
    (root / "packages" / "forge" / "workshop.toml").write_text(
        'extensions = ["python"]\nname = "livery-forge"\n'
    )
    (root / "packages" / "forge" / "pyproject.toml").write_text(
        '[project]\nname = "livery-forge"\ndependencies = []\n'
    )


def _module(root: Path, package: str, name: str, body: str) -> Path:
    src = root / "packages" / package / "src"
    src.mkdir(parents=True, exist_ok=True)
    path = src / name
    path.write_text(body)
    return path


def _recipe(name: str) -> str:
    """A conan package's recipe, which names its reference, for ``acme-<name>``."""
    return (
        "from conan import ConanFile\n\n\n"
        f"class {name.title()}(ConanFile):\n"
        f'    """The {name} library."""\n\n'
        f'    name = "acme-{name}"\n'
        '    version = "0.1.0"\n\n'
        "    def build(self):\n"
        "        pass\n"
    )


#: A consumer's recipe: requirements alone, and no reference of its own.
CONSUMER_RECIPE = (
    "from conan import ConanFile\n\n\n"
    "class Consumer(ConanFile):\n"
    '    generators = "CMakeDeps"\n'
    '    requires = ("fmt/[>=11.0]",)\n'
)


def _member(
    root: Path,
    name: str,
    files: dict[str, str],
    extensions: tuple[str, ...] = (),
) -> Package:
    """The member ``acme-<name>`` at ``packages/<name>``, holding *files*."""
    directory = root / "packages" / name
    directory.mkdir(parents=True)
    listed = ", ".join(f'"{extension}"' for extension in extensions)
    held = f"extensions = [{listed}]\n" if extensions else ""
    (directory / "workshop.toml").write_text(f'name = "acme-{name}"\n{held}')
    for relative, text in files.items():
        (directory / relative).write_text(text)
    return Package(
        directory=directory,
        path=f"packages/{name}",
        name=f"acme-{name}",
        depends=(),
        extensions=extensions,
    )


def _three_ecosystems(root: Path) -> tuple[Package, Package, Package]:
    """A python package, a C++ library with conan, and a nanobind module.

    Each lists its extensions; the nanobind module's set holds python
    and conan beside nanobind, conan through a consumer's recipe.
    """
    echo = _member(
        root,
        "echo",
        {"pyproject.toml": '[project]\nname = "acme-echo"\n'},
        ("python",),
    )
    lib = _member(root, "lib", {"conanfile.py": _recipe("lib")}, ("cmake", "conan"))
    native = _member(
        root,
        "native",
        {
            "pyproject.toml": '[project]\nname = "acme-native"\n',
            "conanfile.py": CONSUMER_RECIPE,
        },
        ("nanobind",),
    )
    return echo, lib, native


def _tree(root: Path) -> dict[str, str]:
    """Every file under *root*'s packages, by its path there, to its text."""
    return {
        path.relative_to(root).as_posix(): path.read_text()
        for path in sorted((root / "packages").rglob("*"))
        if path.is_file()
    }


def test_python_s_writer_leaves_a_sibling_outside_its_ecosystem_alone(
    tmp_path: Path,
) -> None:
    # conan's own suite judges conan's writer the same way.
    skip_unless_installed("conan", "nanobind")
    echo, lib, native = _three_ecosystems(tmp_path)
    before = _tree(tmp_path)
    # Python's writer takes a sibling that holds python alone.
    assert _python.declare_requirement(echo, lib, "0.1.0") is None
    assert _tree(tmp_path) == before
    # It takes a sibling of its own ecosystem.
    assert _python.declare_requirement(native, echo, "0.1.0") == ["pyproject.toml"]


def _python_member(root: Path, name: str, pyproject: str, *listed: str) -> Package:
    """The package ``livery-<name>`` holding *listed*, its manifest *pyproject*."""
    directory = root / "packages" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "pyproject.toml").write_text(pyproject)
    return Package(
        directory=directory,
        path=f"packages/{name}",
        name=f"livery-{name}",
        depends=(),
        extensions=listed,
    )


#: The comment a test extra the writer opens starts with.
OPENED = (
    "# The members this package's tests need beyond its dependencies, one\n"
    "# for each test edge of its workshop.toml, at the edge's floor.\n"
)


def test_the_python_test_requirement_refuses_without_project_and_skips_a_stranger(
    tmp_path: Path,
) -> None:
    low = _python_member(tmp_path, "low", '[project]\nname = "livery-low"\n', "python")
    native = _python_member(tmp_path, "native", "", "cmake")
    orphan = _python_member(tmp_path, "orphan", "[tool.uv]\npackage = false\n")
    with pytest.raises(_FAILURES, match="no \\[project\\] table"):
        _python.declare_test_requirement(orphan, low, "1.2.0")
    # A sibling that holds no python is another writer's.
    assert _python.declare_test_requirement(low, native, "1.2.0") is None
    assert (low.directory / "pyproject.toml").read_text() == (
        '[project]\nname = "livery-low"\n'
    )
    # An extra in a form the edit does not take refuses; nothing is written.
    odd = (
        '[project]\nname = "livery-odd"\n\n[project.optional-dependencies]\n'
        'test = ["pyyaml",\n    "livery-low"]\n'
    )
    taken = _python_member(tmp_path, "odd", odd, "python")
    with pytest.raises(
        _FAILURES,
        match=re.escape(
            "spells its test extra in a form this edit does not take; write"
            ' "livery-low>=1.2.0" into it by hand'
        ),
    ):
        _python.declare_test_requirement(taken, low, "1.2.0")
    assert (taken.directory / "pyproject.toml").read_text() == odd


def test_the_python_test_requirement_opens_the_extra_joins_it_and_writes_the_floor(
    tmp_path: Path,
) -> None:
    low = _python_member(tmp_path, "low", '[project]\nname = "livery-low"\n', "python")
    urls = '\n[project.urls]\nRepository = "https://example.test"\n'
    # No extra at all: the table opens after [project], before the next.
    bare = _python_member(
        tmp_path, "bare", f'[project]\nname = "livery-bare"\n{urls}', "python"
    )
    assert _python.declare_test_requirement(bare, low, "1.2.0") == ["pyproject.toml"]
    assert (bare.directory / "pyproject.toml").read_text() == (
        '[project]\nname = "livery-bare"\n\n[project.optional-dependencies]\n'
        f'{OPENED}test = ["livery-low>=1.2.0"]\n{urls}'
    )
    # Carried already: nothing to write.
    assert _python.declare_test_requirement(bare, low, "1.2.0") == []
    # A table without the extra: it gains the extra at its end.
    other = _python_member(
        tmp_path,
        "other",
        '[project]\nname = "livery-other"\n\n[project.optional-dependencies]\n'
        f'# Why.\ncli = ["click"]\n{urls}',
        "python",
    )
    _python.declare_test_requirement(other, low, "1.2.0")
    assert (other.directory / "pyproject.toml").read_text() == (
        '[project]\nname = "livery-other"\n\n[project.optional-dependencies]\n'
        f'# Why.\ncli = ["click"]\n{OPENED}test = ["livery-low>=1.2.0"]\n{urls}'
    )
    # A line at another floor is replaced where it stands, and a new one
    # joins the end while the extra fits on its line.
    inline = _python_member(
        tmp_path,
        "inline",
        '[project]\nname = "livery-inline"\n\n[project.optional-dependencies]\n'
        'test = ["livery-low>=1.0.0", "pyyaml"]  # kept\n',
        "python",
    )
    _python.declare_test_requirement(inline, low, "1.2.0")
    deep = _python_member(tmp_path, "deep", '[project]\nname = "x"\n', "python")
    _python.declare_test_requirement(inline, deep, "10.20.30")
    assert (
        (inline.directory / "pyproject.toml")
        .read_text()
        .endswith(
            'test = ["livery-low>=1.2.0", "pyyaml", "livery-deep>=10.20.30"]  # kept\n'
        )
    )
    # Past 88 columns the extra spreads to a line each, comments kept.
    longer = _python_member(
        tmp_path,
        "another-sibling-with-a-long-name",
        '[project]\nname = "y"\n',
        "python",
    )
    _python.declare_test_requirement(inline, longer, "1.0.0")
    spread = (
        "test = [  # kept\n"
        '    "livery-low>=1.2.0",\n'
        '    "pyyaml",\n'
        '    "livery-deep>=10.20.30",\n'
        '    "livery-another-sibling-with-a-long-name>=1.0.0",\n'
        "]\n"
    )
    assert (inline.directory / "pyproject.toml").read_text().endswith(spread)
    # A spread extra: a line at another floor is replaced, its comments kept.
    _python.declare_test_requirement(inline, low, "2.0.0")
    assert (
        (inline.directory / "pyproject.toml")
        .read_text()
        .endswith(spread.replace("livery-low>=1.2.0", "livery-low>=2.0.0"))
    )
    # No floor: the name alone.
    plain = _python_member(
        tmp_path, "plain", '[project]\nname = "livery-plain"\n', "python"
    )
    _python.declare_test_requirement(plain, low, "")
    assert 'test = ["livery-low"]' in (plain.directory / "pyproject.toml").read_text()
    assert _python.declare_test_requirement(plain, low, "") == []


def test_the_python_requirement_joins_a_list_opens_one_or_refuses_without_project(
    tmp_path: Path,
) -> None:
    def member(name: str, pyproject: str) -> Package:
        directory = tmp_path / "packages" / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "pyproject.toml").write_text(pyproject)
        return Package(
            directory=directory,
            path=f"packages/{name}",
            name=f"livery-{name}",
            depends=(),
            extensions=("python",),
        )

    low = member("low", '[project]\nname = "livery-low"\n')
    listed = member(
        "listed",
        '[project]\nname = "livery-listed"\ndependencies = [\n    "pyyaml",\n]\n',
    )
    assert _python.declare_requirement(listed, low, "1.2.0") == ["pyproject.toml"]
    assert (listed.directory / "pyproject.toml").read_text() == (
        '[project]\nname = "livery-listed"\ndependencies = [\n'
        '    "pyyaml",\n    "livery-low>=1.2.0",\n]\n'
    )
    inline = member("inline", '[project]\nname = "livery-inline"\ndependencies = []\n')
    _python.declare_requirement(inline, low, "1.2.0")
    assert (inline.directory / "pyproject.toml").read_text() == (
        '[project]\nname = "livery-inline"\ndependencies = [\n'
        '    "livery-low>=1.2.0",\n]\n'
    )
    bare = member("bare", '[project]\nname = "livery-bare"\n')
    _python.declare_requirement(bare, low, "1.2.0")
    assert '"livery-low>=1.2.0"' in (bare.directory / "pyproject.toml").read_text()
    # Already declared, at any constraint: nothing to write.
    assert _python.declare_requirement(bare, low, "9.9.9") == []
    orphan = member("orphan", "[tool.uv]\npackage = false\n")
    with pytest.raises(_FAILURES, match="no \\[project\\] table"):
        _python.declare_requirement(orphan, low, "1.2.0")


# The one parse, and the rules an extension registers into it.


def test_the_layering_phase_parses_each_source_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.extensions.python import _declared, _parse, _terminal
    from livery.extensions.python._parse import RuleContext
    from livery.extensions.python._reaches import scan
    from livery.workshop import _lifecycle

    _package(tmp_path, "tool")
    _module(tmp_path, "tool", "a.py", "import json\n")
    _module(tmp_path, "tool", "b.py", "import os\n")
    (tmp_path / "tasks.py").write_text("x = 1\n")
    _forge_stub(tmp_path)
    counted: list[Path] = []
    real = _parse._parse

    def counting(path: Path) -> _parse.Parsed | None:
        counted.append(path)
        return real(path)

    def gate() -> None:
        # The base's rules, the python extension's three steps, and each
        # package's references answer.
        packages = verify_workspace(tmp_path)
        modules = _parse.parsed_modules(tmp_path, packages)
        assert scan(modules, packages) == {}
        assert _terminal.problems(modules) == []
        assert _declared.problems(RuleContext(tmp_path, packages)) == []
        for package in packages:
            assert _lifecycle.references(package)

    monkeypatch.setattr(_parse, "_parse", counting)
    _parse._PARSED.clear()
    gate()
    assert sorted(p.name for p in counted) == ["a.py", "b.py", "ok.py", "tasks.py"]
    # The memo: a second gate over the same bytes parses nothing.
    gate()
    assert len(counted) == 4
    # An edited file parses again, once.
    _module(tmp_path, "tool", "a.py", "import json\nimport re\n")
    gate()
    assert [p.name for p in counted[4:]] == ["a.py"]
