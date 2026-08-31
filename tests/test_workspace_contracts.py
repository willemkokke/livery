"""The layering lint's seed (bootstrap plan, phase 0).

Every package carries its contract, and livery.forge imports only the
standard library at runtime. Grows into the real layering lint when a
second package arrives.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = sorted(p for p in (ROOT / "packages").iterdir() if p.is_dir())


def test_every_package_carries_its_contracts() -> None:
    for pkg in PACKAGES:
        assert (pkg / "livery.toml").is_file(), f"{pkg.name} lacks livery.toml"
        assert (pkg / "pyproject.toml").is_file(), f"{pkg.name} lacks pyproject.toml"


def test_forge_runtime_imports_only_stdlib() -> None:
    stdlib = sys.stdlib_module_names
    for source in (ROOT / "packages/forge/src").rglob("*.py"):
        tree = ast.parse(source.read_text("utf-8"), filename=str(source))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                top = name.split(".")[0]
                assert top in stdlib or top == "livery", (
                    f"{source.relative_to(ROOT)} imports {name!r}: "
                    "livery.forge is stdlib-only at runtime"
                )
