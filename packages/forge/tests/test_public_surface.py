"""The public surface is what __init__ declares; everything else is private."""

from __future__ import annotations

from pathlib import Path

import livery.forge


def test_the_surface_is_declared() -> None:
    assert livery.forge.__all__ == ["Unsupported", "__version__"]


def test_every_other_module_is_underscore_named() -> None:
    package = Path(livery.forge.__file__).parent
    for module in package.rglob("*.py"):
        if module.name != "__init__.py":
            assert module.name.startswith("_"), (
                f"{module.name} is public by name: modules are private, "
                "and public names are re-exported by __init__"
            )
