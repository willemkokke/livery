"""conan's requirement writer: a sibling's floor written into a recipe.

The writer takes a conan package alone, a sibling that holds conan and
whose recipe names a reference, and writes into the recipe's
`requires` tuple, opening one where the recipe declares none.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from livery.extensions.conan import _package as conan
from livery.workshop._packages import Package

_FAILURES = (BaseException,)


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
    """A python package, a C++ library with conan, and a consumer of conan's packages.

    Each lists its extensions; the consumer's recipe names requirements
    alone, and no reference of its own.
    """
    echo = _member(
        root,
        "echo",
        {"pyproject.toml": '[project]\nname = "acme-echo"\n'},
        ("python",),
    )
    lib = _member(root, "lib", {"conanfile.py": _recipe("lib")}, ("cmake", "conan"))
    consumer = _member(
        root, "consumer", {"conanfile.py": CONSUMER_RECIPE}, ("cmake", "conan")
    )
    return echo, lib, consumer


def _tree(root: Path) -> dict[str, str]:
    """Every file under *root*'s packages, by its path there, to its text."""
    return {
        path.relative_to(root).as_posix(): path.read_text()
        for path in sorted((root / "packages").rglob("*"))
        if path.is_file()
    }


def test_the_writer_leaves_a_sibling_outside_its_ecosystem_alone(
    tmp_path: Path,
) -> None:
    echo, lib, consumer = _three_ecosystems(tmp_path)
    before = _tree(tmp_path)
    # Conan's takes a conan package alone: neither a sibling that holds
    # no conan, nor one whose recipe, a consumer's, names no reference.
    assert conan.declare_requirement(lib, echo, "0.1.0") is None
    assert conan.declare_requirement(lib, consumer, "0.1.0") is None
    assert _tree(tmp_path) == before
    # It takes a sibling of its own ecosystem.
    assert conan.declare_requirement(consumer, lib, "0.1.0") == ["conanfile.py"]


def test_conan_s_answer_is_a_requirement_writer(tmp_path: Path) -> None:
    from livery.extensions.conan import _answers, _package
    from livery.workshop import RequirementWriter

    echo, lib, consumer = _three_ecosystems(tmp_path)
    # The type checkers hold the recipe's writer to the protocol here,
    # and conan answers the query with it.
    writer: RequirementWriter = _package.declare_requirement
    assert _answers.requirement_writers(consumer) == (writer,)
    # Called as the protocol, it leaves a sibling outside conan alone
    # and writes a conan sibling into the recipe.
    before = _tree(tmp_path)
    assert writer(consumer, echo, "0.1.0") is None
    assert _tree(tmp_path) == before
    assert writer(consumer, lib, "0.1.0") == ["conanfile.py"]
    assert '"acme-lib/[>=0.1.0]"' in (consumer.directory / "conanfile.py").read_text()


def _recipe_member(root: Path, name: str, body: str) -> Package:
    """The conan package ``acme-<name>``, its recipe *body*."""
    return _member(root, name, {"conanfile.py": body}, ("cmake", "conan"))


def test_the_conan_requirement_refuses_a_recipe_it_cannot_place_the_tuple_in(
    tmp_path: Path,
) -> None:
    low = _recipe_member(tmp_path, "low", _recipe("low"))
    called = _recipe_member(
        tmp_path,
        "called",
        "from conan import ConanFile\n\n\nclass Called(ConanFile):\n"
        '    name = "acme-called"\n\n    def requirements(self):\n'
        '        self.requires("fmt/[>=11.0]")\n',
    )
    with pytest.raises(
        _FAILURES,
        match=re.escape(
            "declares its requirements without a `requires = (...)` tuple to add"
            ' "acme-low/[>=0.3.0]" to; add the requirement by hand'
        ),
    ):
        conan.declare_requirement(called, low, "0.3.0")
    # No class derives from ConanFile, or the recipe class's body shares
    # its line: nothing marks where the tuple goes.
    for name, body in (
        ("plain", 'class Plain:\n    name = "acme-plain"\n'),
        (
            "inline",
            'from conan import ConanFile\n\n\nclass Inline(ConanFile): name = "x"\n',
        ),
    ):
        refused = _recipe_member(tmp_path, name, body)
        with pytest.raises(_FAILURES, match=re.escape("defines no recipe class")):
            conan.declare_requirement(refused, low, "0.3.0")
        assert (refused.directory / "conanfile.py").read_text() == body


def test_the_conan_requirement_joins_the_tuple_or_opens_one(tmp_path: Path) -> None:
    low = _recipe_member(tmp_path, "low", _recipe("low"))
    seeded = _recipe_member(
        tmp_path,
        "seeded",
        'class Seeded:\n    name = "acme-seeded"\n    requires = ("fmt/[>=11.0]",)\n',
    )
    assert conan.declare_requirement(seeded, low, "0.3.0") == ["conanfile.py"]
    assert (
        'requires = ("fmt/[>=11.0]", "acme-low/[>=0.3.0]")'
        in (seeded.directory / "conanfile.py").read_text()
    )
    assert conan.declare_requirement(seeded, low, "0.4.0") == []
    empty = _recipe_member(
        tmp_path, "empty", 'class Empty:\n    name = "acme-empty"\n    requires = ()\n'
    )
    conan.declare_requirement(empty, low, "0.3.0")
    assert (
        'requires = ("acme-low/[>=0.3.0]",)'
        in (empty.directory / "conanfile.py").read_text()
    )
    # A recipe that declares no requirement gets the tuple after the
    # attributes of its recipe class, or first in a class that starts
    # with a method.
    opened = '    requires = ("acme-low/[>=0.3.0]",)\n'
    head = "from conan import ConanFile\n\n\n"
    for name, body, written in (
        (
            "methods",
            f'{head}class Methods(ConanFile):\n    """Doc."""\n\n'
            '    name = "x"\n\n    def build(self):\n        pass\n',
            f'{head}class Methods(ConanFile):\n    """Doc."""\n\n'
            f'    name = "x"\n{opened}\n    def build(self):\n        pass\n',
        ),
        (
            "attributes",
            f'{head}class Attributes(ConanFile):\n    name = "x"\n    version = "1"',
            f"{head}class Attributes(ConanFile):\n"
            f'    name = "x"\n    version = "1"\n{opened}',
        ),
        (
            "decorated",
            f"{head}class Decorated(ConanFile):\n    @property\n"
            "    def build(self):\n        pass\n",
            f"{head}class Decorated(ConanFile):\n{opened}\n    @property\n"
            "    def build(self):\n        pass\n",
        ),
    ):
        bare = _recipe_member(tmp_path, name, body)
        assert conan.declare_requirement(bare, low, "0.3.0") == ["conanfile.py"]
        assert (bare.directory / "conanfile.py").read_text() == written
