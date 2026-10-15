"""Fake extensions for the tests: packages that declare themselves, found by path.

A test of what happens before an extension is installed takes an
installed one away with `withheld`, and puts it back as an install
would.
"""

from __future__ import annotations

import functools
import importlib
import shutil
import sys
import textwrap
from collections.abc import Callable
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
    The contract keys' owners are read again for the test alone, so a
    fake's keys never reach a later test.
    """
    _fake(tmp_path, monkeypatch, modules, real_composing=True)


def fake_package_extensions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **modules: str
) -> None:
    """The fakes of [fake_extensions][], and no real extension composing a package.

    For a test that judges the set it built: the real ones the
    workshop installs that compose something in a package are left out
    of the scan.
    """
    _fake(tmp_path, monkeypatch, modules, real_composing=False)


def _fake(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    modules: dict[str, str],
    *,
    real_composing: bool,
) -> None:
    """Write the fakes and put them in footman's scan; see [fake_extensions][]."""
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
    if not real_composing:
        named |= _real_composing()
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
    # The declared phases and queries are read from the same scan, once
    # per test.
    phases = _phases.declared_phases.__wrapped__
    monkeypatch.setattr(_phases, "declared_phases", functools.cache(phases))
    queries = _queries.declared_queries.__wrapped__
    monkeypatch.setattr(_queries, "declared_queries", functools.cache(queries))


def _real_composing() -> set[str]:
    """The installed extensions that compose something in a package."""
    return {
        name
        for name in _extensions.installed_extensions()
        if _extensions.composes_per_package(name)
    }


def withheld(monkeypatch: pytest.MonkeyPatch, *extensions: str) -> Callable[[], None]:
    """Take installed *extensions* away for the test; the function that installs them.

    Each one's entry point leaves footman's scan of the installed
    distributions, so every reader of the extensions finds no
    distribution declaring it. The function returned puts them back as
    a sync that installs them would: the next read of the installed
    extensions finds them, and what is read from their declarations is
    read again.
    """
    scanned = installed_entry_points()
    shown = [
        tuple(
            entry
            for entry in scanned
            if entry.group != _extensions.GROUP or entry.name not in extensions
        )
    ]
    monkeypatch.setattr(_entries, "_scan", lambda: shown[0])
    monkeypatch.setattr(_entries, "_SCAN", None)
    read = (
        (_contract_keys, "declarations"),
        (_phases, "declared_phases"),
        (_queries, "declared_queries"),
    )
    for module, name in read:
        monkeypatch.setattr(
            module, name, functools.cache(getattr(module, name).__wrapped__)
        )

    def install() -> None:
        shown[0] = scanned
        monkeypatch.setattr(_entries, "_SCAN", None)
        for module, name in read:
            getattr(module, name).cache_clear()

    return install


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
    contract.write_text(f"extensions = {extensions}\n")
    return contract
