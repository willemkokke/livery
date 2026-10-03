"""A project born by `fm new.project` passes its own gate, on this checkout's code.

The birth runs here, from this checkout; the newborn's uv sources then
point at this checkout's packages through the region of its project
file, so its own verbs run the code under test rather than the
published wheels. It locks over the network, which the merge path
never waits on, so it arms with WORKSHOP_CONFORMANCE_DRIVE=1 like the
stranger drive.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: The distributions the newborn takes from this checkout instead of
#: the index: the workshop and everything of ours it installs.
_LOCAL = ("workshop", "footman", "toolroom", "toolroom-store", "strongroom", "forge")

_END_TABLES = "# -- workshop: end tables --"


def _run(cmd: list[str], cwd: Path, env: dict[str, str]) -> str:
    done = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    assert done.returncode == 0, (
        f"{' '.join(cmd)} exited {done.returncode}:\n{done.stdout}\n{done.stderr}"
    )
    # The runner prints its verdict lines on stderr.
    return done.stdout + done.stderr


def _point_at_this_checkout(project: Path) -> None:
    """Add path sources for our distributions to the newborn's own region."""
    pyproject = project / "pyproject.toml"
    text = pyproject.read_text("utf-8")
    assert _END_TABLES in text, "the composed project file carries no tables region"
    sources = "".join(
        f"[tool.uv.sources.livery-{name}]\n"
        f'path = "{(ROOT / "packages" / name).as_posix()}"\n'
        "editable = true\n"
        for name in _LOCAL
    )
    pyproject.write_text(text.replace(_END_TABLES, sources + _END_TABLES), "utf-8")


def test_a_born_project_is_green(tmp_path: Path) -> None:
    if not os.environ.get("WORKSHOP_CONFORMANCE_DRIVE"):
        pytest.skip(
            "set WORKSHOP_CONFORMANCE_DRIVE=1 to run the birth: it locks and"
            " syncs a scratch workspace over the network"
        )
    env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
    # Above any project the runner mounts its own built-ins alone, so the
    # birth reaches the workshop through a user tasks file in a scratch
    # config directory. The config-dir variable names it, since the
    # suite's isolation already points XDG_CONFIG_HOME elsewhere.
    bridge = tmp_path / "bridge-config"
    bridge.mkdir()
    (bridge / "tasks.py").write_text(
        'from livery.footman.api import plugin\n\nplugin("livery.workshop")\n'
    )
    born = {**env, "FOOTMAN_CONFIG_DIR": str(bridge)}
    _run(
        [
            sys.executable,
            "-m",
            "livery.footman",
            "--yes",
            "new.project",
            "acme-born",
            "--local",
            "--namespace=acme",
        ],
        tmp_path,
        born,
    )
    project = tmp_path / "acme-born"
    for seed in ("README.md", "LICENSE", "docs/index.md"):
        assert (project / seed).is_file(), seed
    assert not (project / "tests").exists()
    _point_at_this_checkout(project)
    _run(["uv", "lock"], project, env)
    _run(["uv", "sync"], project, env)
    scripts = "Scripts" if sys.platform == "win32" else "bin"
    fm = str(project / ".venv" / scripts / "fm")
    _run([fm, "new.package", "thing"], project, env)
    _run([fm, "new.package", "geometry", "--kind=package-cpp-conan"], project, env)
    gate = _run([fm, "check"], project, env)
    for check in ("lint-doclinks", "lint-docstrings", "test-ctest", "template-check"):
        assert f"ok   {check}" in gate, gate
