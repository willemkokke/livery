"""The local gate resolves docstring cross-references as the API site does."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from livery.extensions.docs._checks import names_removed
from livery.extensions.docs._refs import (
    defined_names,
    docstring_references,
    reference_problems,
)
from livery.workshop._influence import Changes

IMPL = '''"""The widget's home."""


class Widget:
    """A widget."""

    size: int = 1

    def turn(self) -> None:
        """Turn it."""


VALUE = 3
"""A module attribute."""
'''

API = '''"""The thing's public names."""

from acme.thing._impl import Widget as Widget
'''


def _sources(tmp_path: Path) -> tuple[Path, Path]:
    """A member whose ``api`` re-exports from a private module; its src and a user."""
    src = tmp_path / "packages" / "thing" / "src"
    package = src / "acme" / "thing"
    package.mkdir(parents=True)
    (package / "_impl.py").write_text(IMPL)
    (package / "api.py").write_text(API)
    return src, package / "_user.py"


def _problems(tmp_path: Path, docstring: str) -> list[str]:
    src, user = _sources(tmp_path)
    user.write_text(f'"""{docstring}"""\n')
    return reference_problems(tmp_path, [user], [src])


# The refusals first: each names nothing the site could link.


@pytest.mark.parametrize(
    "reference",
    [
        "acme.thing._impl.Gadget",  # a missing attribute
        "acme.thing._impl.turn",  # a method named without its class
        "acme.thnig._impl.Widget",  # a misspelt module
        "acme.thing.api.Gadget",  # a re-export that is not there
    ],
)
def test_a_reference_that_names_nothing_is_refused_with_its_place(
    tmp_path: Path, reference: str
) -> None:
    problems = _problems(tmp_path, f"See [{reference}][].")
    assert problems == [
        f"packages/thing/src/acme/thing/_user.py:1: [{reference}][] names nothing"
        " the API site can link"
    ]


@pytest.mark.parametrize(
    "reference",
    [
        "acme.thing._impl",  # a module
        "acme.thing._impl.Widget",  # a class
        "acme.thing._impl.Widget.turn",  # a method through its class
        "acme.thing._impl.Widget.size",  # a class attribute
        "acme.thing._impl.VALUE",  # a module attribute
        "acme.thing.api.Widget",  # a re-export, followed to its source
    ],
)
def test_a_reference_that_names_something_passes(
    tmp_path: Path, reference: str
) -> None:
    assert _problems(tmp_path, f"See [{reference}][].") == []


def test_a_reference_outside_every_members_namespace_is_left_to_the_site(
    tmp_path: Path,
) -> None:
    assert _problems(tmp_path, "See [pathlib.Nowhere][].") == []


def test_code_is_text_not_a_reference(tmp_path: Path) -> None:
    docstring = (
        "Inline `[acme.thing.Missing][]` and ``[acme.thing.Missing][]``.\n\n"
        "```text\n[acme.thing.Missing][]\n```\n"
    )
    assert _problems(tmp_path, docstring) == []


def test_a_reference_is_placed_on_its_own_line(tmp_path: Path) -> None:
    source = tmp_path / "m.py"
    source.write_text(
        '"""First line.\n\nThird: [acme.a.b][].\n"""\n\n\n'
        "X = 1\n"
        '"""After an assignment: [acme.c.d][]."""\n'
    )
    assert docstring_references(source) == [(3, "acme.a.b"), (8, "acme.c.d")]


def test_the_names_a_module_defines_include_its_classes_members() -> None:
    assert defined_names(IMPL) == {
        "Widget",
        "Widget.size",
        "Widget.turn",
        "VALUE",
    }


def test_a_change_that_removes_a_name_has_every_source_read(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    module = root / "packages" / "thing" / "src" / "m.py"
    module.parent.mkdir(parents=True)
    module.write_text(IMPL)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.email=t@x",
            "-c",
            "user.name=T",
            "commit",
            "-q",
            "-m",
            "seed",
        ],
        check=True,
    )
    changed = ("packages/thing/src/m.py",)
    # An added name leaves every other reference standing.
    module.write_text(IMPL + "\nEXTRA = 1\n")
    assert not names_removed(Changes(root, changed, "HEAD"))
    # A renamed method, and a deleted module, may each strand one.
    module.write_text(IMPL.replace("def turn", "def spin"))
    assert names_removed(Changes(root, changed, "HEAD"))
    module.unlink()
    assert names_removed(Changes(root, changed, "HEAD"))
