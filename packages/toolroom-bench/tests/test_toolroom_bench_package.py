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
