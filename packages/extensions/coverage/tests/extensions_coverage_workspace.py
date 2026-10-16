"""What the coverage suite builds its workspaces with.

Fake extensions, the dependency groups the engine composes for a
workspace, a count of the processes a block starts, and the skip for a
test that mounts an extension this distribution does not depend on.
"""

from __future__ import annotations

import functools
import importlib
import shutil
import subprocess
import sys
import textwrap
import tomllib
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.metadata import EntryPoint
from os.path import basename
from pathlib import Path
from typing import Any

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


def composed_file(tmp_path: Path, name: str, extensions: tuple[str, ...] = ()) -> str:
    """*name* as the engine composes it into a workspace listing *extensions*."""
    from livery.workshop._shipped_files import outputs

    workspace = tmp_path / "composed"
    workspace.mkdir(exist_ok=True)
    listed = ", ".join(f'"{extension}"' for extension in extensions)
    # A listed extension's templates read the project's identity, which
    # a named workspace has.
    named = 'name = "composed"\n' if extensions else ""
    (workspace / "workshop.toml").write_text(
        f"[workspace]\n{named}extensions = [{listed}]\n"
    )
    (output,) = [o for o in outputs(workspace) if o.path == name]
    return output.body.decode()


@contextmanager
def counting_spawns() -> Iterator[Counter[str]]:
    """Count every process started inside the block, by program and git subcommand.

    ``counts["git"]`` is every git; ``counts["git fetch"]`` the fetches,
    past git's own ``-c`` pairs and flags, for a pin on round trips.
    """
    counts: Counter[str] = Counter()
    original = subprocess.Popen.__init__

    def counting(
        self: subprocess.Popen[Any], args: Any, *rest: Any, **kwargs: Any
    ) -> None:
        argv = (
            [str(item) for item in args]
            if isinstance(args, (list, tuple))
            else [str(args)]
        )
        program = basename(argv[0]).removesuffix(".exe") if argv else ""
        counts[program] += 1
        words = argv[1:]
        while words and words[0].startswith("-"):
            words = words[2:] if words[0] == "-c" else words[1:]
        if words:
            counts[f"{program} {words[0]}"] += 1
        original(self, args, *rest, **kwargs)

    subprocess.Popen.__init__ = counting  # type: ignore[method-assign]
    try:
        yield counts
    finally:
        subprocess.Popen.__init__ = original  # type: ignore[method-assign]


def skip_unless_installed(*extensions: str) -> None:
    """Skip the running test while an extension of *extensions* is not installed.

    The coverage suite mounts extensions this distribution does not
    depend on: docs, whose site its pages join, and pytest and cmake,
    which measure for it. A run of this wheel alone, as a release leg
    makes, installs none of them. The reason names each missing
    extension and the distribution that ships it.
    """
    missing = [
        name
        for name in dict.fromkeys(extensions)
        if _extensions.installed_declaration(name) is None
    ]
    if missing:
        named = ", ".join(
            f"{name} ({_extensions.distribution_of(name)})" for name in missing
        )
        pytest.skip(f"not installed here: {named}")
