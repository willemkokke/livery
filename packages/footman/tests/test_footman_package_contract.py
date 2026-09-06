"""The distribution contract the workspace pins on this package.

The suite it joined already pins the public surface; this file pins
what the packaging must keep true.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any


def _pyproject() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    return tomllib.loads(path.read_text("utf-8"))


def test_zero_runtime_dependencies() -> None:
    # The completion hot path is stdlib-only, and the first runtime
    # dependency this ecosystem takes is reserved for strongroom.
    assert _pyproject()["project"]["dependencies"] == []


def test_the_console_scripts_are_this_packages() -> None:
    scripts = _pyproject()["project"]["scripts"]
    assert scripts == {
        "footman": "livery.footman:main",
        "fm": "livery.footman:main",
    }


def test_the_entry_names_keep_their_ecosystem_spelling() -> None:
    # Entry-point names are identities tasks files mount by; only the
    # targets moved under the livery namespace. The stock `new` builtin
    # is deliberately gone.
    points = _pyproject()["project"]["entry-points"]
    tasks = points["footman.tasks"]
    assert set(tasks) == {
        "footman.docs",
        "footman.env_files",
        "footman.self",
        "footman.profile",
        "livery.footman",
    }
    assert all(target.startswith("livery.footman") for target in tasks.values())
    assert "footman.new" not in tasks


def test_the_stock_builtin_is_self_only() -> None:
    from livery import footman

    assert footman.BUILTIN == ("footman.self",)
