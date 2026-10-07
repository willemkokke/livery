"""The bench's package contract: its surface and its verbs."""

from __future__ import annotations

from importlib.metadata import version

import livery.toolroom.bench as package


def test_the_version_is_the_installed_distributions() -> None:
    assert package.__version__ == version("livery-toolroom-bench")


def test_importing_the_bench_mounts_its_verbs() -> None:
    assert set(package.__all__) == {
        "Refreshed",
        "__version__",
        "submit_refresh",
        "tasks",
    }
    assert package.tasks.name == "tools"
    assert "docs" in package.tasks.tasks


def test_mounting_the_bench_loads_no_store_engine() -> None:
    import subprocess
    import sys

    # A fresh interpreter, so this suite's own imports do not count.
    script = (
        "import sys, livery.toolroom.bench;"
        " print(sorted(m for m in sys.modules if m in"
        " ('livery.toolroom.store._engine', 'livery.strongroom._store')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[]"
