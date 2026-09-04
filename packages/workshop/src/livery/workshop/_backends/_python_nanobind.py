"""The nanobind backend: build for ``type = "python-nanobind"``.

A child of the python backend: every quality verb is inherited
whole (the extension is a python distribution and every checker
applies), and only the wheel build differs. cibuildwheel builds
and repairs the wheel so even a single leg is
manylinux-compliant; the sdist still comes from ``uv build``.
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import TYPE_CHECKING

import toolroom
from footman import fail

from livery.workshop._backends import _python

if TYPE_CHECKING:
    from pathlib import Path

    from livery.workshop._packages import Package

check = _python.check

#: What ``uv tool run`` resolves for the build. A floor and a cap,
#: not an exact pin: the run has no lockfile to consult, and a major
#: bump changes cibuildwheel's defaults deliberately. The release
#: matrix (its own phase) pins its legs exactly.
CIBUILDWHEEL = "cibuildwheel>=3.0,<4"


def assert_platform_tagged(package: Package, dist: Path) -> None:
    """Refuse any pure-tagged wheel in *dist*; the identity guard.

    A wheel from a native kind that says ``none-any`` was built
    without its extension, and publishing it would hand every
    consumer an ImportError. The other half of the guard (a native
    tag from a pure kind) lives with the publish wave.
    """
    wheels = sorted(dist.glob("*.whl"))
    if not wheels:
        fail(f"{package.name}: the build produced no wheel in {dist}")
    pure = [wheel.name for wheel in wheels if "none-any" in wheel.name]
    if pure:
        fail(
            f"{package.name} is a python-nanobind package and built a"
            f" pure wheel: {', '.join(pure)}. The extension did not"
            " compile into the wheel; the platform tag is the proof it"
            " did."
        )


def build(package: Package, root: Path, *, epoch: int = 0) -> Path:
    """Build the platform wheel through cibuildwheel, and the sdist.

    Locally the run pins to the running interpreter
    (``CIBW_BUILD``, respected when the caller already set it), so
    the verb stays minutes; the release matrix widens the set in
    CI. Always from a clean ``dist/``, like the python backend.
    Refuses a pure-tagged result: see
    [livery.workshop._backends._python_nanobind.assert_platform_tagged][].
    """
    from livery.workshop._docs import materialise_module_docs

    materialise_module_docs(package)
    dist = package.directory / "dist"
    shutil.rmtree(dist, ignore_errors=True)
    env = dict(os.environ)
    if epoch:
        env["SOURCE_DATE_EPOCH"] = str(epoch)
    env.setdefault(
        "CIBW_BUILD",
        f"cp{sys.version_info.major}{sys.version_info.minor}-*",
    )
    result = toolroom.uv.opts(
        cwd=package.directory, env=env, nofail=True, recorded=False
    )("tool", "run", "--from", CIBUILDWHEEL, "cibuildwheel", "--output-dir", str(dist))
    if result.code != 0:
        fail(
            f"cibuildwheel ({package.name}) exited {result.code}:\n"
            f"{result.stdout[-4000:]}{result.stderr[-2000:]}"
        )
    sdist = toolroom.uv.opts(
        cwd=package.directory, env=env, nofail=True, recorded=False
    )("build", "--sdist", "--out-dir", str(dist))
    if sdist.code != 0:
        fail(
            f"uv build --sdist ({package.name}) exited {sdist.code}:\n"
            f"{sdist.stdout[-4000:]}{sdist.stderr[-2000:]}"
        )
    assert_platform_tagged(package, dist)
    return dist
