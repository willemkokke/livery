"""Fake extensions for the tests: packages that declare themselves, found by path."""

from __future__ import annotations

import functools
import importlib
import shutil
import sys
import textwrap
from importlib.metadata import EntryPoint
from pathlib import Path

import pytest

from livery.footman import (
    _entries,  # pyright: ignore[reportPrivateUsage]
    installed_entry_points,
)
from livery.workshop import _contract_keys, _extensions


def fake_extensions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **modules: str
) -> None:
    """Packages ``acme.<name>``, each declaring the given ``extension.toml``.

    ``{package}`` in a declaration stands for the package's import path,
    and each package's ``_checks`` defines ``noop`` for a check to run.
    Each fake declares itself as the extension named by its import path,
    beside every extension installed for real, in footman's scan of the
    installed entry points, which every reader of the extensions asks.
    The contract keys' owners are read again for the test alone, so a
    fake's keys never reach a later test.
    """
    site = tmp_path / "site"
    (site / "acme").mkdir(parents=True, exist_ok=True)
    for name, text in modules.items():
        package = site / "acme" / name
        package.mkdir(exist_ok=True)
        (package / "__init__.py").write_text("")
        (package / "_checks.py").write_text("def noop(ctx):\n    del ctx\n")
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
    # The scan itself, so a rescan, which an install asks for, finds them too.
    monkeypatch.setattr(_entries, "_scan", lambda: (*scanned, *fakes))
    monkeypatch.setattr(_entries, "_SCAN", None)
    owners = _contract_keys.declarations.__wrapped__
    monkeypatch.setattr(_contract_keys, "declarations", functools.cache(owners))


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


def root_contract(root: Path, extensions: str) -> None:
    """Write *root*'s contract listing *extensions*, a TOML list as written."""
    (root / "workshop.toml").write_text(f"[workspace]\nextensions = {extensions}\n")


def package_contract(root: Path, member: str, extensions: str) -> Path:
    """Write the contract of ``packages/<member>`` listing *extensions*; its path."""
    directory = root / "packages" / member
    directory.mkdir(parents=True, exist_ok=True)
    contract = directory / "workshop.toml"
    contract.write_text(f'kind = "python"\nextensions = {extensions}\n')
    return contract
