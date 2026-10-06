"""A project born by `fm new.project` passes its own gate, on this checkout's code.

The birth runs here, from this checkout, and finishes with the runner
the newborn's environment holds, so the newborn's first lock reads this
checkout's distributions from a local index of their wheels: the
published ones are older, and an extension a birth lists may not be
published at all. The newborn's uv sources then point at this
checkout's packages through the region of its project file, so its own
verbs run the code under test. It locks over the network, which the
merge path never waits on, so it arms with WORKSHOP_CONFORMANCE_DRIVE=1
like the stranger drive.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_END_TABLES = "# -- workshop: end tables --"


def _run(cmd: list[str], cwd: Path, env: dict[str, str]) -> str:
    done = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    assert done.returncode == 0, (
        f"{' '.join(cmd)} exited {done.returncode}:\n{done.stdout}\n{done.stderr}"
    )
    # The runner prints its verdict lines on stderr.
    return done.stdout + done.stderr


def _point_at_this_checkout(project: Path) -> None:
    """Add path sources for our distributions to the newborn's own region.

    The same members the checkout index builds
    ([livery.workshop._e2e.dev_members][]), as editable paths.
    """
    from livery.workshop._e2e import dev_members
    from livery.workshop._packages import discover_packages

    ours = set(dev_members(ROOT))
    pyproject = project / "pyproject.toml"
    text = pyproject.read_text("utf-8")
    assert _END_TABLES in text, "the composed project file carries no tables region"
    sources = "".join(
        f"[tool.uv.sources.{package.name}]\n"
        f'path = "{package.directory.as_posix()}"\n'
        "editable = true\n"
        for package in discover_packages(ROOT)
        if package.member in ours
    )
    pyproject.write_text(text.replace(_END_TABLES, sources + _END_TABLES), "utf-8")


def test_a_born_project_is_green(tmp_path: Path) -> None:
    if not os.environ.get("WORKSHOP_CONFORMANCE_DRIVE"):
        pytest.skip(
            "set WORKSHOP_CONFORMANCE_DRIVE=1 to run the birth: it locks and"
            " syncs a scratch workspace over the network"
        )
    from livery.workshop._e2e import checkout_index

    env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
    # Its own conan home: a sync registers the native member as an
    # editable, which in the machine's home would outlive this test.
    env["CONAN_HOME"] = str(tmp_path / "conan-home")
    # Above any project the runner mounts its own built-ins alone, so the
    # birth reaches the workshop through a user tasks file in a scratch
    # config directory. The config-dir variable names it, since the
    # suite's isolation already points XDG_CONFIG_HOME elsewhere.
    bridge = tmp_path / "bridge-config"
    bridge.mkdir()
    (bridge / "tasks.py").write_text(
        'from livery.footman.api import plugin\n\nplugin("livery.workshop")\n'
    )
    born = {
        **env,
        "FOOTMAN_CONFIG_DIR": str(bridge),
        "UV_INDEX": checkout_index(ROOT, tmp_path / "checkout-index"),
    }
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
    # A package in a group directory: the distribution names the group,
    # and so does the import path.
    _run([fm, "new.package", "extensions/demo"], project, env)
    grouped = project / "packages" / "extensions" / "demo"
    assert 'name = "acme-extensions-demo"' in (grouped / "workshop.toml").read_text()
    assert (grouped / "src" / "acme" / "extensions" / "demo").is_dir()
    assert '"packages/extensions/demo"' in (project / "pyproject.toml").read_text()
    gate = _run([fm, "check"], project, env)
    for check in ("lint-doclinks", "lint-docstrings", "test-ctest", "drift-check"):
        assert f"ok   {check}" in gate, gate
    # Removing a member is deleting its directory and syncing: the
    # handoff enters the environment as it is, the sync composes the
    # project file without the member, and the gate is green again.
    import shutil

    shutil.rmtree(project / "packages" / "thing")
    # From outside the project's environment, as a person's own `fm`
    # arrives: the runner hands off to the project, which is the step a
    # stale project file used to fail.
    outside = {
        key: value
        for key, value in env.items()
        if key not in ("FOOTMAN_UV_REEXEC", "FOOTMAN_NO_UV")
    }
    _run([sys.executable, "-m", "livery.footman", "sync"], project, outside)
    assert "packages/thing" not in (project / "pyproject.toml").read_text()
    # What removal owns: every composed and generated file follows. The
    # whole gate waits on issue #1111: with the last python member gone,
    # the python checks still run over the root without their tools.
    _run([fm, "drift.check"], project, env)
