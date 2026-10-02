"""Importing livery.toolroom.store loads none of its modules until a name is asked for."""

from __future__ import annotations

import subprocess
import sys

import livery.toolroom.store.api as package


def _loaded_after(statement: str) -> list[str]:
    # A fresh interpreter, so this suite's own imports do not count.
    script = (
        f"import sys; {statement};"
        " print('\\n'.join(sorted(m for m in sys.modules"
        " if m.startswith('livery.toolroom.store.'))))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    return result.stdout.split()


def test_importing_the_root_loads_no_module_of_the_package() -> None:
    assert _loaded_after("import livery.toolroom.store.api") == [
        "livery.toolroom.store.api"
    ]


def test_one_name_loads_only_the_module_that_defines_it() -> None:
    assert _loaded_after("from livery.toolroom.store.api import HOSTS") == [
        "livery.toolroom.store._record",
        "livery.toolroom.store.api",
    ]


def test_every_public_name_resolves() -> None:
    for name in package.__all__:
        assert hasattr(package, name), name


def test_an_unknown_name_is_an_attribute_error() -> None:
    try:
        package.no_such_name  # noqa: B018
    except AttributeError as error:
        assert "no_such_name" in str(error)
    else:
        raise AssertionError("an unknown name resolved")
