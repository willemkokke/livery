"""The codec's standing contracts inside strongroom, pinned first."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import livery.strongroom.cbor as codec

PACKAGE = Path(__file__).resolve().parents[1]
SOURCE = PACKAGE / "src" / "livery" / "strongroom" / "cbor"


def test_the_codec_imports_the_stdlib_and_itself_alone() -> None:
    # Every implementation of the standard needs the codec first, so it
    # leans on nothing else of strongroom's, at import time or lazily.
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
                if name.split(".")[0] in allowed or name.startswith(
                    "livery.strongroom.cbor"
                ):
                    continue
                offending.append(f"{source.relative_to(PACKAGE)}: {name}")
    assert offending == []


def test_importing_the_store_loads_no_part_of_the_codec() -> None:
    # A fresh interpreter, so this suite's own imports do not count: the
    # The root declares the codec and serves it on first use, so importing
    # the root loads none of it; its import path stays its own.
    script = (
        "import sys, livery.strongroom;"
        " print(sorted(m for m in sys.modules"
        " if m.startswith('livery.strongroom.cbor')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[]"


def test_the_public_surface_is_pinned() -> None:
    assert set(codec.__all__) == {
        "MAX_DEPTH",
        "CodecError",
        "Value",
        "decode",
        "encode",
    }
    for name in codec.__all__:
        assert hasattr(codec, name), name
    modules = sorted(p.stem for p in SOURCE.glob("*.py") if p.stem != "__init__")
    assert all(name.startswith("_") for name in modules), modules


def test_the_spec_and_its_vectors_are_beside_the_package() -> None:
    assert (PACKAGE / "spec" / "cbor.md").is_file()
    assert (PACKAGE / "spec" / "vectors" / "cbor.json").is_file()
