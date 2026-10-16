"""What the mkdocstrings suite builds its workspaces with.

Fake extensions, packages that declare themselves beside the installed
ones, and the dependency groups the engine composes for a workspace.
"""

from __future__ import annotations

import functools
import importlib
import shutil
import sys
import textwrap
import tomllib
from importlib.metadata import EntryPoint
from pathlib import Path

import pytest

from livery.footman import (
    _entries,  # pyright: ignore[reportPrivateUsage]
    installed_entry_points,
)
from livery.workshop import _contract_keys, _extensions, _phases, _queries


def fake_extensions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **modules: str
) -> None:
    """Packages ``acme.<name>``, each declaring the given ``extension.toml``.

    ``{package}`` in a declaration stands for the package's import path,
    and each package's ``_steps`` defines ``noop`` for a step to run.
    Each fake declares itself as the extension named by its import path,
    beside every extension installed for real, in footman's scan of the
    installed entry points, which every reader of the extensions asks.
    What is read from the installed declarations is read again for the
    test alone, so a fake never reaches a later test.
    """
    site = tmp_path / "site"
    (site / "acme").mkdir(parents=True, exist_ok=True)
    for name, text in modules.items():
        package = site / "acme" / name
        package.mkdir(exist_ok=True)
        (package / "__init__.py").write_text("")
        (package / "_steps.py").write_text("def noop(ctx):\n    del ctx\n")
        (package / "extension.toml").write_text(
            textwrap.dedent(text).replace("{package}", f"acme.{name}")
        )
    monkeypatch.syspath_prepend(str(site))
    importlib.invalidate_caches()
    # An earlier test's fake of the same name left its modules behind.
    for loaded in [
        key for key in sys.modules if key == "acme" or key.startswith("acme.")
    ]:
        monkeypatch.delitem(sys.modules, loaded, raising=False)
    fakes = tuple(
        EntryPoint(f"acme.{name}", f"acme.{name}", _extensions.GROUP)
        for name in modules
    )
    named = {entry.name for entry in fakes}
    scanned = tuple(
        entry
        for entry in installed_entry_points()
        if entry.group != _extensions.GROUP or entry.name not in named
    )
    monkeypatch.setattr(_entries, "_scan", lambda: (*scanned, *fakes))
    monkeypatch.setattr(_entries, "_SCAN", None)
    for module, name in (
        (_contract_keys, "declarations"),
        (_phases, "declared_phases"),
        (_queries, "declared_queries"),
    ):
        monkeypatch.setattr(
            module, name, functools.cache(getattr(module, name).__wrapped__)
        )


def fake_steps(tmp_path: Path, name: str, source: str) -> None:
    """Write *source* as fake extension ``acme.<name>``'s ``_steps`` module.

    Written before anything reads the extension's declaration, whose
    references the read resolves against the module's source.
    """
    module = tmp_path / "site" / "acme" / name / "_steps.py"
    module.write_text(textwrap.dedent(source))
    # A step module rewritten within a test is imported afresh.
    sys.modules.pop(f"acme.{name}._steps", None)
    shutil.rmtree(module.parent / "__pycache__", ignore_errors=True)
    importlib.invalidate_caches()


def composed_groups(root: Path) -> dict[str, list[str]]:
    """The dependency groups of the project file the engine composes for *root*.

    Read from the engine's outputs, so nothing is written: the contract
    at *root* says what the workspace lists.
    """
    from livery.workshop._shipped_files import outputs

    (composed,) = [
        output for output in outputs(root) if output.path == "pyproject.toml"
    ]
    groups: dict[str, list[str]] = tomllib.loads(composed.body.decode())[
        "dependency-groups"
    ]
    return groups
