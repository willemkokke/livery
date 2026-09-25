# Seeded from the template channel (package-python kind) at
# birth; this file is the workspace's own. Edit it directly:
# the template never rewrites it.
"""The package's standing contracts, pinned before anything else."""

from __future__ import annotations

import ast
import subprocess
import sys
import tomllib
from pathlib import Path

import livery.cbor as package

PACKAGE = Path(__file__).resolve().parents[1]
SOURCE = PACKAGE / "src" / "livery" / "cbor"


def test_the_distribution_has_no_dependency() -> None:
    # The codec is the package everything else may depend on, so it
    # depends on nothing: not a third-party library, not an extra.
    parsed = tomllib.loads((PACKAGE / "pyproject.toml").read_text("utf-8"))
    assert parsed["project"]["dependencies"] == []
    assert "optional-dependencies" not in parsed["project"]
    assert parsed["project"]["requires-python"] == ">=3.11"


def test_the_namespace_carries_no_init() -> None:
    # PEP 420: livery/ is a namespace directory, never a package.
    assert not (PACKAGE / "src" / "livery" / "__init__.py").exists()


def test_no_module_imports_beyond_the_stdlib_and_itself() -> None:
    allowed = set(sys.stdlib_module_names)
    offending: list[str] = []
    for source in sorted(SOURCE.rglob("*.py")):
        tree = ast.parse(source.read_text("utf-8"), filename=str(source))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.split(".")[0] in allowed or name.startswith("livery.cbor"):
                    continue
                offending.append(f"{source.relative_to(PACKAGE)}: {name}")
    assert offending == []


def test_importing_loads_no_other_first_party_module() -> None:
    # A fresh interpreter, so this suite's own imports do not count.
    script = (
        "import sys, livery.cbor;"
        " print(sorted(m for m in sys.modules if m.startswith('livery.')"
        " and not m.startswith('livery.cbor')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[]"


def test_imports_and_carries_a_version() -> None:
    from importlib.metadata import version

    # Against the installed metadata, never a literal: the release
    # train stamps the version, and a spelled copy here would need a
    # hand edit every release.
    assert package.__version__ == version("livery-cbor")


def test_the_public_surface_is_pinned() -> None:
    assert set(package.__all__) == {
        "MAX_DEPTH",
        "CodecError",
        "Value",
        "__version__",
        "decode",
        "encode",
    }
    modules = sorted(p.stem for p in SOURCE.glob("*.py") if p.stem != "__init__")
    assert all(name.startswith("_") for name in modules), modules


def test_the_spec_and_its_vectors_are_beside_the_package() -> None:
    assert (PACKAGE / "spec" / "codec.md").is_file()
    assert (PACKAGE / "spec" / "vectors" / "codec.json").is_file()
