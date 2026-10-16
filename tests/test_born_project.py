"""A project born by `fm new.project` passes its own gate, on this checkout's code.

So does a package created from each template with each answer its
options take, each in a project of its own.

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

from separate_workspace import environment

ROOT = Path(__file__).resolve().parents[1]

_END_TABLES = "# -- workshop: end tables --"

#: What conan's cpp-package template needs, which the root lists before
#: the C++ member is created. cmake ships as a distribution of its own,
#: which the newborn's region sources from this checkout, so the sync
#: that installs it takes it from there.
NATIVE = ("cmake", "conan")

#: What nanobind's nanobind-module template needs: nanobind, which
#: requires python, cpp and conan. It ships as a distribution of its
#: own, which the newborn's region sources from this checkout too.
NANOBIND = ("nanobind",)

#: Each creation the templates' options make, each in a workspace of its
#: own: the member's name, the template, and the answers on the line.
CREATIONS = (
    ("plugin", "workshop-extension", ()),
    ("brand", "workshop-extension", ("--app=brand",)),
    ("thing", "python-library", ()),
    ("tool", "python-library", ("--command=tool",)),
    ("geometry", "cpp-package", ("--package-type=library",)),
    ("viewer", "cpp-package", ("--package-type=application",)),
    ("shapes", "cpp-package", ("--package-type=header-library",)),
    ("solids", "cpp-package", ("--package-type=static-library",)),
    ("meshes", "cpp-package", ("--package-type=shared-library",)),
)


def _run(cmd: list[str], cwd: Path, env: dict[str, str]) -> str:
    done = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    assert done.returncode == 0, (
        f"{' '.join(cmd)} exited {done.returncode}:\n{done.stdout}\n{done.stderr}"
    )
    # The runner prints its verdict lines on stderr.
    return done.stdout + done.stderr


def _point_at_this_checkout(project: Path, listed: tuple[str, ...]) -> None:
    """Add path sources for our distributions to the newborn's own region.

    The same members the checkout index builds for the extensions
    *listed* ([livery.workshop._e2e.dev_members][]), as editable paths.
    """
    from livery.workshop._e2e import dev_members
    from livery.workshop._packages import discover_packages

    ours = set(dev_members(ROOT, listed))
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


def _birth(tmp_path: Path) -> tuple[Path, str, dict[str, str]]:
    """A project born by `fm new.project` under *tmp_path*, locked and synced.

    Its region points at this checkout's packages. The project, the
    runner its environment holds, and the environment to run it in.
    Skips unless WORKSHOP_CONFORMANCE_DRIVE is set.
    """
    if not os.environ.get("WORKSHOP_CONFORMANCE_DRIVE"):
        pytest.skip(
            "set WORKSHOP_CONFORMANCE_DRIVE=1 to run the birth: it locks and"
            " syncs a scratch workspace over the network"
        )
    from livery.workshop._e2e import checkout_index
    from livery.workshop._extensions import SELF
    from livery.workshop._new_project import birth_extensions

    env = {key: value for key, value in environment().items() if key != "VIRTUAL_ENV"}
    # Its own conan home: a sync registers the native member as an
    # editable, which in the machine's home would outlive this test.
    env["CONAN_HOME"] = str(tmp_path / "conan-home")
    # Its own root for the temporary directories of the newborn's suite:
    # pytest's session cleanup in a root another session shares unlinks a
    # symlink the other removes at the same moment, and fails the gate.
    temproot = tmp_path / "pytest-temp"
    temproot.mkdir()
    env["PYTEST_DEBUG_TEMPROOT"] = str(temproot)
    # Above any project the runner mounts its own built-ins alone, so the
    # birth reaches the workshop through a user tasks file in a scratch
    # config directory. The config-dir variable names it, since the
    # suite's isolation already points XDG_CONFIG_HOME elsewhere.
    bridge = tmp_path / "bridge-config"
    bridge.mkdir()
    (bridge / "tasks.py").write_text(
        'from livery.footman import plugin\n\nplugin("livery.workshop")\n'
    )
    # The index serves what the birth lists; the newborn's own sources
    # name this checkout's members for that and for what the root lists
    # later.
    births = birth_extensions([SELF])
    index = checkout_index(ROOT, tmp_path / "checkout-index", births)
    born = {**env, "FOOTMAN_CONFIG_DIR": str(bridge), "UV_INDEX": index}
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
    _point_at_this_checkout(project, (*births, *NATIVE, *NANOBIND))
    _run(["uv", "lock"], project, env)
    _run(["uv", "sync"], project, env)
    return project, _script(project, "fm"), env


def _script(project: Path, name: str) -> str:
    """The script *name* in *project*'s environment."""
    scripts = "Scripts" if sys.platform == "win32" else "bin"
    suffix = ".exe" if sys.platform == "win32" else ""
    return str(project / ".venv" / scripts / f"{name}{suffix}")


def _installed(project: Path, distribution: str) -> bool:
    """Whether *project*'s environment holds *distribution*."""
    probe = (
        "import importlib.metadata, sys\nimportlib.metadata.distribution(sys.argv[1])\n"
    )
    done = subprocess.run(
        [_script(project, "python"), "-c", probe, distribution], capture_output=True
    )
    return done.returncode == 0


def test_a_born_project_is_green(tmp_path: Path) -> None:
    project, fm, env = _birth(tmp_path)
    _run([fm, "new.package.python-library", "thing"], project, env)
    # The stock list holds python and not conan, which declares
    # cpp-package: a template has a verb while the installed set holds
    # the extension that declares it, so there is no such verb yet.
    refused = subprocess.run(
        [fm, "new.package.cpp-package", "geometry"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
    )
    said = refused.stdout + refused.stderr
    assert refused.returncode == 64, said
    assert "no task named 'new.package.cpp-package'" in said, said
    assert not (project / "packages" / "geometry").exists()
    # Listed, as the refusal says, and installed by the sync from the
    # source the newborn's project file names.
    from livery.workshop._extensions import list_in_root

    list_in_root(project, NATIVE)
    _run([fm, "sync"], project, env)
    _run([fm, "new.package.cpp-package", "geometry"], project, env)
    # A package in a group directory: the distribution names the group,
    # and so does the import path.
    _run([fm, "new.package.python-library", "extensions/demo"], project, env)
    grouped = project / "packages" / "extensions" / "demo"
    assert 'name = "acme-extensions-demo"' in (grouped / "workshop.toml").read_text()
    assert (grouped / "src" / "acme" / "extensions" / "demo").is_dir()
    assert '"packages/extensions/demo"' in (project / "pyproject.toml").read_text()
    gate = _run([fm, "check"], project, env)
    for step in ("lint.doclinks", "lint.docstrings", "test.ctest", "drift.check"):
        assert f"ok   {step}" in gate, gate
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
    # the python steps still run over the root without their tools.
    _run([fm, "drift.check"], project, env)


@pytest.mark.parametrize(
    ("name", "template", "answers"),
    CREATIONS,
    ids=[" ".join((template, *answers)) for _name, template, answers in CREATIONS],
)
def test_a_creation_with_each_answer_is_green_in_a_workspace_of_its_own(
    tmp_path: Path, name: str, template: str, answers: tuple[str, ...]
) -> None:
    project, fm, env = _birth(tmp_path)
    if template == "cpp-package":
        from livery.workshop._extensions import list_in_root

        list_in_root(project, NATIVE)
        _run([fm, "sync"], project, env)
    _run([fm, f"new.package.{template}", name, *answers], project, env)
    member = project / "packages" / name
    # The script an answer names is on PATH after the wiring's sync, and
    # a creation without one puts none there.
    if template == "python-library":
        assert Path(_script(project, "tool")).is_file() == bool(answers)
        assert (member / "src" / "acme" / name / "__main__.py").is_file() == bool(
            answers
        )
    if template == "workshop-extension":
        assert Path(_script(project, "brand")).is_file() == bool(answers)
        assert (member / "src" / "acme" / name / "_app.py").is_file() == bool(answers)
    if template == "cpp-package":
        kind = answers[0].partition("=")[2]
        assert f'package_type = "{kind}"' in (member / "conanfile.py").read_text()
    gate = _run([fm, "check"], project, env)
    assert "ok   drift.check" in gate, gate
    if template == "cpp-package":
        assert "ok   test.ctest" in gate, gate
    else:
        assert "ok   test.pytest" in gate, gate


def test_a_nanobind_module_is_created_once_its_extension_is_listed_and_synced(
    tmp_path: Path,
) -> None:
    project, fm, env = _birth(tmp_path)
    # The refusal first: the stock list holds no nanobind, so the
    # environment holds none of its distribution, and no verb creates
    # its template: footman refuses a task it does not know.
    refused = subprocess.run(
        [fm, "new.package.nanobind-module", "native"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
    )
    said = refused.stdout + refused.stderr
    assert refused.returncode == 64, said
    assert "no task named 'new.package.nanobind-module'" in said, said
    assert not (project / "packages" / "native").exists()
    assert not _installed(project, "livery-extensions-nanobind")
    # Listed, the sync installs its distribution from the source the
    # newborn's project file names, and the template's verb exists.
    from livery.workshop._extensions import list_in_root

    list_in_root(project, NANOBIND)
    _run([fm, "sync"], project, env)
    assert _installed(project, "livery-extensions-nanobind")
    _run([fm, "new.package.nanobind-module", "native"], project, env)
    member = project / "packages" / "native"
    assert 'extensions = ["nanobind"]' in (member / "workshop.toml").read_text()
    gate = _run([fm, "check"], project, env)
    for step in ("drift.check", "test.pytest"):
        assert f"ok   {step}" in gate, gate
