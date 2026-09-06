"""The shim's whole contract: one dependency, the surface forwarded.

The real package re-exports lazily through ``__getattr__``, so the shim
forwards attributes rather than re-exporting a list, and the contract is
identity per name. The console scripts and entry points ship with
livery-footman only; the shim declaring them too would register each
twice in one environment.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import footman
from livery import footman as real


def _pyproject() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    return tomllib.loads(path.read_text("utf-8"))


def test_the_shim_depends_on_exactly_the_real_distribution() -> None:
    parsed = _pyproject()
    dependencies = parsed["project"]["dependencies"]
    assert len(dependencies) == 1
    assert dependencies[0].startswith("livery-footman>=")


def test_the_shim_declares_no_scripts_and_no_entry_points() -> None:
    project = _pyproject()["project"]
    assert "scripts" not in project
    assert "entry-points" not in project


def test_every_public_name_forwards_identically() -> None:
    for name in real.__all__:
        if name == "__version__":
            # The one literal: the release train verifies the version
            # is declared in the shim itself, and the ruling keeps the
            # two distributions on one number, so equality is the
            # contract here rather than identity.
            assert footman.__version__ == real.__version__
            continue
        assert getattr(footman, name) is getattr(real, name), name


def test_the_version_is_the_real_packages() -> None:
    assert footman.__version__ == real.__version__


def test_the_submodule_spellings_forward() -> None:
    import importlib

    for spelling in (
        "app",
        "compose",
        "context",
        "docstrings",
        "env_files",
        "invocation",
        "markdown",
        "params",
        "profile",
        "pytest_plugin",
        "registry",
        "testing",
        "tasks",
        "tasks.docs",
        "tasks.self_",
    ):
        shim = importlib.import_module(f"footman.{spelling}")
        target = importlib.import_module(f"livery.footman.{spelling}")
        for name in dir(target):
            if name.startswith("_"):
                continue
            assert getattr(shim, name) is getattr(target, name), (spelling, name)


def test_python_dash_m_footman_is_the_console_script() -> None:
    # `python -m footman` must keep working: the uv script handoff
    # re-execs exactly that spelling when the declared dist is footman.
    import subprocess
    import sys

    done = subprocess.run(
        [sys.executable, "-m", "footman", "--version"],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    assert "footman" in done.stdout
