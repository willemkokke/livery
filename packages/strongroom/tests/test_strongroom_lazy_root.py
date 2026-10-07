"""Importing livery.strongroom loads none of its modules until a name is asked for."""

from __future__ import annotations

import subprocess
import sys

import pytest

import livery.strongroom as package


def _loaded_after(statement: str) -> list[str]:
    # A fresh interpreter, so this suite's own imports do not count.
    script = (
        f"import sys; {statement};"
        " print('\\n'.join(sorted(m for m in sys.modules"
        " if m.startswith('livery.strongroom.'))))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    return result.stdout.split()


def test_importing_the_root_loads_no_module_of_the_package() -> None:
    assert _loaded_after("import livery.strongroom") == []


def test_one_name_loads_only_the_module_that_defines_it() -> None:
    assert _loaded_after("from livery.strongroom import FormatError") == [
        "livery.strongroom._canonical"
    ]


def test_a_public_package_is_served_on_first_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The import system binds a package on the root at its first import;
    # a program that imported the root alone reaches it through the root,
    # which imports it then.
    import livery.strongroom.cbor as cbor

    monkeypatch.delattr(package, "cbor")
    assert package.cbor is cbor


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
