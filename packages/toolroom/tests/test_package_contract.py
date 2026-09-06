"""The imported package's standing contracts, pinned before anything else."""

from __future__ import annotations

import tomllib
from pathlib import Path


def test_the_distribution_stays_dependency_free() -> None:
    # Contract 5 of the migration plan: dependencies = [] today, and
    # the first runtime dependency stays reserved for strongroom.
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    parsed = tomllib.loads(pyproject.read_text("utf-8"))
    assert parsed["project"]["dependencies"] == []


def test_the_namespace_carries_no_init() -> None:
    # PEP 420: livery/ is a namespace directory, never a package.
    src = Path(__file__).resolve().parents[1] / "src"
    assert not (src / "livery" / "__init__.py").exists()
