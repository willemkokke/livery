"""The replay's python half: a released version installed alone and tested.

The released-wheels replay checks the pairing a consumer gets: the
distribution the index serves, installed into a plain virtual
environment beside the tests recorded for that same version, the
package imported from site-packages and never from the workspace.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from livery.footman import fail

if TYPE_CHECKING:
    from pathlib import Path

    from livery.workshop._packages import Package


def replay(
    package: Package, *, tree: Path, version: str, python: str, index: str, extras: str
) -> int:
    """Install *package*'s released *version* alone, then run its tests at *tree*.

    The plain environment sits beside *tree*, which is checked out at the
    release's receipt tag. A red install runs no test.

    Returns:
        The exit code: the install's when it is red, the tests' otherwise.
    """
    from dataclasses import replace

    venv = tree.parent / ".replay"
    requirement = f"{package.name}{f'[{extras}]' if extras else ''}=={version}"
    module = import_name(replace(package, directory=tree / package.path))
    code = install(venv, python, requirement, index)
    if code != 0:
        return code
    return run_tests(venv, tree, package.member, module)


def import_name(package: Package) -> str:
    """The module *package*'s wheel ships, which the replay imports from site-packages.

    The first of the python kind's module roots
    ([livery.extensions.python._backend.module_roots][]): the build
    backend's ``module-name`` where the manifest declares one, which
    names a module under a namespace too.

    Raises:
        Failed: when *package* ships no module, naming its ``src``.
    """
    from livery.extensions.python._backend import module_roots

    roots = module_roots(package)
    if not roots:
        fail(
            f"{package.directory / 'src'} holds no package directory: nothing to import"
        )
    return roots[0]


def install(venv: Path, python: str, requirement: str, index: str) -> int:
    """Create the plain environment and install *requirement* into it; the exit code.

    ``uv`` makes the environment and installs, but the environment is
    a plain one outside the workspace: no lock, no editable members,
    so the package can only come from the index.
    """
    import livery.toolroom.tools as toolroom

    made = toolroom.uv.opts(nofail=True)("venv", str(venv), "--python", python)
    if made.code != 0:
        return made.code
    args = ["pip", "install", "--python", str(venv / "bin" / "python")]
    if index:
        args += ["--index", index]
    args += [requirement, "pytest", "pytest-xdist"]
    return toolroom.uv.opts(nofail=True)(*args).code


def run_tests(venv: Path, tree: Path, member: str, module: str) -> int:
    """Prove the import comes from site-packages, then run the member's tests."""
    from livery.footman import run

    python = str(venv / "bin" / "python")
    probe = run(
        [
            python,
            "-c",
            # The first portion of the package's path: a namespace root
            # has no __file__, a regular package's path is its directory.
            f"import {module} as m, pathlib; p = pathlib.Path(list(m.__path__)[0]);"
            " assert 'site-packages' in str(p), p; print('testing', p)",
        ],
        cwd=tree,
        nofail=True,
        capture=False,
    )
    if probe.code != 0:
        print(
            f"  {module} does not import from site-packages; the replay proves nothing"
        )
        return probe.code
    return run(
        [python, "-m", "pytest", f"packages/{member}/tests", "-p", "no:cacheprovider"],
        cwd=tree,
        nofail=True,
        capture=False,
    ).code
