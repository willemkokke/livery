"""Run a package's documentation examples, the python files under ``docs/examples/``.

An example is a file a page shows whole or by named section through
the snippets extension, so the code a reader sees and the code that
runs are one file. This plugin, loaded through the ``pytest11`` entry
point, collects every ``docs/examples/**/*.py`` pytest is pointed at
as one test item per file, executed whole in a fresh module namespace
under its own path, so a failure's traceback names the example's file
and line. The python kind's runner,
[livery.extensions.python._backend.run_examples][], points pytest at
a package's examples directory. A package's own setup around an
example, a registry capture or a recording mode, is its
``docs/examples/conftest.py``'s business through the ``example``
marker every item carries; the conftest itself is never an example.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

#: The marker every example item carries, for a conftest's hooks.
MARKER = "example"


def is_example(path: Path) -> bool:
    """Whether *path* is an example: a python file under ``docs/examples/``.

    A ``conftest.py`` there is the package's setup, never an example.
    """
    if path.suffix != ".py" or path.name == "conftest.py":
        return False
    parts = path.parts
    return any(parts[i : i + 2] == ("docs", "examples") for i in range(len(parts) - 1))


def pytest_configure(config: pytest.Config) -> None:
    """Register the marker, so a strict-markers run accepts it."""
    config.addinivalue_line(
        "markers", f"{MARKER}: a documentation example, a file under docs/examples/"
    )


def pytest_collect_file(
    file_path: Path, parent: pytest.Collector
) -> pytest.Collector | None:
    """Collect an example file; every other file is another collector's."""
    if not is_example(file_path):
        return None
    return ExampleFile.from_parent(parent, path=file_path)


@pytest.hookimpl(tryfirst=True)
def pytest_pycollect_makemodule(
    module_path: Path, parent: pytest.Collector
) -> pytest.Collector | None:
    """Keep pytest's python collector off an example.

    Pytest collects a file named on its command line as a test module
    whatever its name, and one whose name matches ``python_files`` in
    a directory it walks. Either way it would import the example
    outside the setup its conftest gives it, and run the example's
    ``test_*`` functions as tests of the workspace: a function showing
    a reader how to test a tasks file then runs the workspace's own
    gate from inside it. The example stays this plugin's, run whole.
    """
    if not is_example(module_path):
        return None
    return Withheld.from_parent(parent, path=module_path)


class ExampleFile(pytest.File):
    """One example file: one item, the file run whole."""

    def collect(self) -> Iterator[pytest.Item]:
        item = ExampleItem.from_parent(self, name=self.path.stem)
        item.add_marker(MARKER)
        yield item


class Withheld(pytest.File):
    """An example as pytest's python collector sees it: nothing to collect."""

    def collect(self) -> Iterator[pytest.Item]:
        return iter(())


class ExampleItem(pytest.Item):
    """The run of one example file in a fresh module namespace."""

    def runtest(self) -> None:
        """Execute the file under its own path; a raise is the failure, at its line."""
        source = self.path.read_text(encoding="utf-8")
        code = compile(source, str(self.path), "exec")
        # A real module in sys.modules, not a bare dict: dataclasses
        # resolving string annotations and typing's evaluation look
        # the defining module up by name.
        module = types.ModuleType(f"docs_example_{self.name.replace('-', '_')}")
        module.__file__ = str(self.path)
        sys.modules[module.__name__] = module
        try:
            exec(code, module.__dict__)
        finally:
            del sys.modules[module.__name__]

    def reportinfo(self) -> tuple[Path, int, str]:
        """The file, its first line, and the name the report prints."""
        return self.path, 0, f"example {self.path.name}"
