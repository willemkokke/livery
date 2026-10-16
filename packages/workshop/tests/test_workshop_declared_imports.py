"""The python extension's declared-imports step: refusals first, then what passes."""

from __future__ import annotations

from importlib import metadata
from pathlib import Path

import pytest

from livery.extensions.python._declared import manifest, problems
from livery.extensions.python._installed import (
    _modules_under,
    read_installed,
    requirement_name,
)
from livery.extensions.python._parse import RuleContext
from livery.footman import prog
from livery.workshop._packages import Package, discover_packages

ROOT = Path(__file__).resolve().parents[3]
SOURCE = "packages/core/src/acme/core/__init__.py"
MANIFEST = "packages/core/pyproject.toml"


def _member(root: Path, name: str, files: dict[str, str], *, project: str = "") -> None:
    """A python member ``acme-<name>``: *project* joins its ``pyproject.toml``."""
    directory = root / "packages" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "workshop.toml").write_text(
        f'extensions = ["python"]\nname = "acme-{name}"\n', encoding="utf-8"
    )
    (directory / "pyproject.toml").write_text(
        f'[project]\nname = "acme-{name}"\n{project}', encoding="utf-8"
    )
    for relative, text in files.items():
        _source(root, name, relative, text)


def _source(root: Path, name: str, relative: str, text: str) -> None:
    path = root / "packages" / name / "src" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _judged(root: Path, files: frozenset[str] | None = None) -> list[str]:
    return problems(RuleContext(root, discover_packages(root), files))


def _in_this_checkout() -> None:
    import livery.workshop

    if not Path(livery.workshop.__file__).resolve().is_relative_to(ROOT):
        # The release train's isolated leg installs the workshop's wheel
        # alone, beside no checkout's packages.
        pytest.skip("the judgement reads this checkout's packages")


# The refusals first.


def test_an_undeclared_distribution_at_import_time_refuses_naming_it(
    tmp_path: Path,
) -> None:
    # pygments is a dependency of the workshop's own, so every
    # environment this suite runs in installs it.
    _member(tmp_path, "core", {"acme/core/__init__.py": "import pygments\n"})
    assert _judged(tmp_path) == [
        f"{SOURCE} imports pygments at import time, and pygments provides it,"
        f" which {MANIFEST} does not declare in [project.dependencies]: declare"
        " it there"
    ]


def test_an_extras_distribution_refuses_at_import_time_and_passes_inside_a_function(
    tmp_path: Path,
) -> None:
    extra = '\n[project.optional-dependencies]\nhighlight = ["Pygments>=2.0"]\n'
    _member(
        tmp_path,
        "core",
        {"acme/core/__init__.py": "from pygments import lexers\n"},
        project=extra,
    )
    assert _judged(tmp_path) == [
        f"{SOURCE} imports pygments at import time, and pygments provides it,"
        f" which {MANIFEST} declares in its optional extra 'highlight' alone:"
        " import it inside the function that uses it, or declare it in"
        " [project.dependencies]"
    ]
    lazy = (
        "def lexers() -> object:\n"
        "    from pygments import lexers\n\n"
        "    return lexers\n"
    )
    _source(tmp_path, "core", "acme/core/__init__.py", lazy)
    assert _judged(tmp_path) == []


def test_a_lazy_import_no_declaration_names_refuses(tmp_path: Path) -> None:
    lazy = "def lexers() -> object:\n    import pygments\n\n    return pygments\n"
    _member(tmp_path, "core", {"acme/core/__init__.py": lazy})
    assert _judged(tmp_path) == [
        f"{SOURCE} imports pygments inside a function, and pygments provides it,"
        f" which {MANIFEST} declares neither in [project.dependencies] nor in an"
        " optional extra: declare it in one of them"
    ]


def test_a_module_no_installed_distribution_provides_refuses_naming_it(
    tmp_path: Path,
) -> None:
    body = (
        "import acme_absent\n"
        "from acme_absent.part import thing\n\n\n"
        "def later() -> None:\n"
        "    import acme_gone\n"
    )
    _member(tmp_path, "core", {"acme/core/__init__.py": body})
    advice = (
        f"declare the distribution that does in {MANIFEST}, then `{prog()} sync`"
        " installs it; one an optional extra declares installs once the package's"
        " dev-extras lists that extra"
    )
    # Each module once, however many names the source imports from it.
    assert _judged(tmp_path) == [
        f"{SOURCE} imports acme_absent, and no distribution installed in the"
        f" workspace's environment provides it: {advice}",
        f"{SOURCE} imports acme_gone, and no distribution installed in the"
        f" workspace's environment provides it: {advice}",
    ]


def test_each_distribution_is_named_once_at_import_time_first(tmp_path: Path) -> None:
    body = (
        "def later() -> object:\n"
        "    from pygments import lexers\n\n"
        "    return lexers\n\n\n"
        "import pygments.util\n"
        "from pygments import token\n"
    )
    _member(tmp_path, "core", {"acme/core/__init__.py": body})
    assert _judged(tmp_path) == [
        f"{SOURCE} imports pygments.util at import time, and pygments provides it,"
        f" which {MANIFEST} does not declare in [project.dependencies]: declare"
        " it there"
    ]


def test_a_footman_plugin_may_import_what_the_runner_brings(tmp_path: Path) -> None:
    """The allowance follows the entry point, never a path spelled twice.

    A module the runner loads as a plugin has the runner present by
    construction. The package's own metadata is where that is declared,
    so a module no entry point names is refused the same import, and
    renaming the module in the metadata moves the allowance with it.
    The workspace here holds no runner, so the runner is a distribution
    the environment installed, as it is in a project the workshop made.
    """

    def entry(module: str) -> str:
        return f'\n[project.entry-points."footman.tasks"]\n"acme.core" = "{module}"\n'

    plugin = "import livery.footman as footman\nfrom livery import footman as runner\n"
    _member(
        tmp_path,
        "core",
        {"acme/core/__init__.py": "", "acme/core/_tasks.py": plugin},
        project=entry("acme.core._tasks"),
    )
    assert _judged(tmp_path) == []
    # The refusals: a module no entry point names imports the runner.
    _source(tmp_path, "core", "acme/core/_other.py", "import livery.footman\n")
    assert _judged(tmp_path) == [
        "packages/core/src/acme/core/_other.py imports livery.footman at import"
        " time, and livery-footman provides it, which packages/core/pyproject.toml"
        " does not declare in [project.dependencies]: declare it there"
    ]
    (tmp_path / "packages/core/src/acme/core/_other.py").unlink()
    # The allowance covers what the runner brings, not a third party.
    _source(tmp_path, "core", "acme/core/_tasks.py", plugin + "import pygments\n")
    (refused,) = _judged(tmp_path)
    assert refused.startswith(
        "packages/core/src/acme/core/_tasks.py imports pygments at import time"
    )
    _source(tmp_path, "core", "acme/core/_tasks.py", plugin)
    assert _judged(tmp_path) == []
    # The metadata decides: name another module and this one is refused.
    _member(tmp_path, "core", {}, project=entry("acme.core._elsewhere"))
    (refused,) = _judged(tmp_path)
    assert refused.startswith(
        "packages/core/src/acme/core/_tasks.py imports livery.footman at import"
        " time, and livery-footman provides it"
    )


def test_a_pytest_plugin_may_import_what_pytest_brings(tmp_path: Path) -> None:
    entry = '\n[project.entry-points.pytest11]\nacme = "acme.core._plugin"\n'
    plugin = "import pluggy\nimport pytest\nfrom _pytest import nodes\n"
    _member(
        tmp_path,
        "core",
        {"acme/core/__init__.py": "", "acme/core/_plugin.py": plugin},
        project=entry,
    )
    assert _judged(tmp_path) == []
    # Outside the plugin, pytest is a distribution like any other.
    _source(tmp_path, "core", "acme/core/_other.py", "import pytest\n")
    (refused,) = _judged(tmp_path)
    assert refused.startswith(
        "packages/core/src/acme/core/_other.py imports pytest at import time, and"
        " pytest provides it"
    )


# Then what passes, and what the rule leaves to others.


def test_the_standard_library_members_guards_and_declarations_pass(
    tmp_path: Path,
) -> None:
    _member(tmp_path, "base", {"acme/base/__init__.py": ""})
    body = (
        "from __future__ import annotations\n\n"
        "import json\n"
        "from collections import abc\n"
        "from typing import TYPE_CHECKING\n\n"
        "import pygments\n\n"
        "import acme.base\n"
        "from acme.core import _own\n\n"
        "if TYPE_CHECKING:\n"
        "    import acme_types\n"
        "try:\n"
        "    import acme_optional\n"
        "except ImportError:\n"
        "    acme_optional = None\n"
    )
    _member(
        tmp_path,
        "core",
        {"acme/core/__init__.py": body, "acme/core/_own.py": ""},
        project='dependencies = ["pygments>=2.0"]\n',
    )
    # A test imports what the workspace's environment holds for its
    # suites, which no package declares: the rule reads src alone.
    tests = tmp_path / "packages" / "core" / "tests"
    tests.mkdir()
    (tests / "test_core.py").write_text("import acme_absent\n", encoding="utf-8")
    assert _judged(tmp_path) == []


def test_a_narrowed_run_judges_the_sources_it_reaches(tmp_path: Path) -> None:
    _member(
        tmp_path,
        "core",
        {"acme/core/__init__.py": "", "acme/core/bad.py": "import pygments\n"},
    )
    # A cpp member holds no python extension, and a member that lists
    # nothing holds none: the rule passes both by.
    for name, listed in (("native", '["cmake", "conan"]'), ("odd", "[]")):
        member = tmp_path / "packages" / name
        (member / "src").mkdir(parents=True)
        (member / "workshop.toml").write_text(
            f'extensions = {listed}\nname = "acme-{name}"\n', encoding="utf-8"
        )
        (member / "src" / "stray.py").write_text(
            "import acme_absent\n", encoding="utf-8"
        )
    elsewhere = frozenset({"packages/core/src/acme/core/__init__.py"})
    assert _judged(tmp_path, elsewhere) == []
    reached = frozenset({"packages/core/src/acme/core/bad.py"})
    (refused,) = _judged(tmp_path, reached)
    assert refused.startswith("packages/core/src/acme/core/bad.py imports pygments")
    assert _judged(tmp_path) == [refused]


def test_a_member_without_a_manifest_declares_nothing(tmp_path: Path) -> None:
    package = Package(
        directory=tmp_path / "packages" / "bare",
        path="packages/bare",
        name="acme-bare",
        depends=(),
        extensions=("python",),
    )
    declared = manifest(package)
    assert declared.path == "packages/bare/pyproject.toml"
    assert declared.dependencies == frozenset()
    assert declared.extras == {}
    assert declared.loaded_by("acme.bare") == frozenset()


def test_forge_declares_no_runtime_dependency_and_its_sources_pass() -> None:
    """Forge's stdlib-only property, held by the general rule alone.

    It declares no runtime dependency, its one optional extra is
    PyNaCl, which it loads inside the function that seals a secret, and
    its dev plugin is a module the runner loads.
    """
    from livery.extensions.python._backend import RUNNER_DISTRIBUTION

    _in_this_checkout()
    packages = discover_packages(ROOT)
    (forge,) = [package for package in packages if package.name == "livery-forge"]
    declared = manifest(forge)
    assert declared.dependencies == frozenset()
    assert declared.extras["github-secrets"] == frozenset({"pynacl"})
    assert RUNNER_DISTRIBUTION in declared.loaded_by("livery.forge._dev")
    sources = frozenset(
        path.relative_to(ROOT).as_posix()
        for path in (forge.directory / "src").rglob("*.py")
    )
    assert problems(RuleContext(ROOT, packages, sources)) == []


def test_every_package_here_passes() -> None:
    _in_this_checkout()
    assert problems(RuleContext(ROOT, discover_packages(ROOT))) == []


def test_the_layering_step_refuses_over_the_files_a_run_reaches(
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    from livery.footman import Failed
    from livery.workshop import Changes, PhaseContext, _steps
    from livery.workshop._extensions import installed_declaration, register_declared
    from livery.workshop._steps import step_for

    _member(
        tmp_path,
        "core",
        {"acme/core/__init__.py": "", "acme/core/bad.py": "import pygments\n"},
    )
    declared = installed_declaration("python")
    assert declared is not None
    state = _steps.snapshot()
    try:
        register_declared("python", declared.additions)
        step = step_for("layering.declared-imports")
        assert step.extension == "python"
        assert step.inputs is not None
        assert step.inputs.reads == ("packages/**/*.py",)
        whole = PhaseContext(
            root=tmp_path, packages=discover_packages(tmp_path), step=step.name
        )
        with pytest.raises(Failed) as raised:
            step.run(whole)
        assert str(raised.value).startswith(
            "the sources break the declared-imports rule:\n"
            "  packages/core/src/acme/core/bad.py imports pygments at import time"
        )
        # A run reaching another source alone judges that one.
        elsewhere = Changes(tmp_path, ("packages/core/src/acme/core/__init__.py",))
        step.run(replace(whole, changes=elsewhere))
        # A changed manifest widens the run to every source.
        manifest_changed = Changes(tmp_path, ("packages/core/pyproject.toml",))
        with pytest.raises(Failed):
            step.run(replace(whole, changes=manifest_changed))
    finally:
        _steps.restore(state)


# What the environment's metadata says: which distribution ships a
# module, and what a distribution brings.


def _dist_info(
    site: Path,
    name: str,
    *,
    record: tuple[str, ...] = (),
    requires: tuple[str, ...] = (),
) -> None:
    info = site / f"{name.replace('-', '_')}-1.0.dist-info"
    info.mkdir(parents=True)
    lines = [f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n"]
    lines += [f"Requires-Dist: {requirement}\n" for requirement in requires]
    (info / "METADATA").write_text("".join(lines), encoding="utf-8")
    rows = [*record, f"{info.name}/METADATA", f"{info.name}/RECORD"]
    (info / "RECORD").write_text("".join(f"{row},,\n" for row in rows), "utf-8")


def test_an_editable_install_and_a_wheel_ship_their_modules_alike(
    tmp_path: Path,
) -> None:
    site = tmp_path / "site"
    _dist_info(
        site,
        "acme-wheel",
        record=(
            "acme/wheel/__init__.py",
            "acme/wheel/part.py",
            "acme/wheel/__pycache__/part.cpython-314.pyc",
            "acme/wheel/data.json",
            "acme_wheel-1.0.data/purelib/stray.py",
            "../../bin/acme",
            "",
        ),
    )
    # An editable install: a .pth file whose lines add its sources to
    # the import path, a comment, code and a missing directory adding
    # nothing, and a relative line read against the file's directory.
    src = tmp_path / "src"
    (src / "acme" / "edit").mkdir(parents=True)
    (src / "acme" / "edit" / "__init__.py").write_text("", encoding="utf-8")
    (src / "acme" / "__pycache__").mkdir()
    (src / "acme" / "not-a-module").mkdir()
    (src / "acme" / "loose.py").write_text("", encoding="utf-8")
    (src / "acme" / "__init__.py.jinja").write_text("", encoding="utf-8")
    (src / "__init__.py").write_text("", encoding="utf-8")
    (site / "relative" / "flat").mkdir(parents=True)
    (site / "relative" / "flat" / "__init__.py").write_text("", encoding="utf-8")
    _dist_info(site, "acme-edit", record=("acme_edit.pth",))
    (site / "acme_edit.pth").write_text(
        f"# the sources\nimport os\n{src}\n{tmp_path / 'missing'}\nrelative\n",
        encoding="utf-8",
    )
    # A .pth file it cannot read adds nothing.
    _dist_info(site, "acme-broken", record=("acme_broken.pth",))
    (site / "acme_broken.pth").mkdir()
    env = read_installed(metadata.distributions(path=[str(site)]))
    assert env.providers("acme.wheel.part.thing") == {"acme-wheel"}
    assert env.providers("acme.edit.thing") == {"acme-edit"}
    assert env.providers("acme.loose") == {"acme-edit"}
    assert env.providers("flat") == {"acme-edit"}
    # A namespace is every distribution that ships a portion of it.
    assert env.providers("acme") == {"acme-wheel", "acme-edit"}
    assert env.providers("acme.unknown") == {"acme-wheel", "acme-edit"}
    assert env.providers("bin") == frozenset()
    assert env.providers("stray") == frozenset()
    assert env.providers("absent") == frozenset()
    # A directory that cannot be listed holds nothing importable.
    assert list(_modules_under(tmp_path / "missing", ())) == []


def test_an_install_without_a_record_lists_its_files_another_way(
    tmp_path: Path,
) -> None:
    site = tmp_path / "site"
    info = site / "acme_old.egg-info"
    info.mkdir(parents=True)
    (info / "PKG-INFO").write_text(
        "Metadata-Version: 1.0\nName: acme-old\nVersion: 1.0\n", encoding="utf-8"
    )
    (info / "SOURCES.txt").write_text("acme_old/__init__.py\n", encoding="utf-8")
    # The metadata lists a file only while it is on disk.
    (site / "acme_old").mkdir()
    (site / "acme_old" / "__init__.py").write_text("", encoding="utf-8")
    env = read_installed(metadata.distributions(path=[str(site)]))
    assert env.providers("acme_old.part") == {"acme-old"}


def test_a_distribution_brings_what_it_requires_outside_its_extras(
    tmp_path: Path,
) -> None:
    site = tmp_path / "site"
    _dist_info(
        site,
        "acme-host",
        requires=(
            "Acme.Dep>=1",
            'acme-win; sys_platform == "win32"',
            'acme-test; extra == "test"',
        ),
    )
    # A cycle is walked once, and a requirement naming nothing is no
    # requirement.
    _dist_info(site, "acme-dep", requires=("acme-host", "@@@"))
    # Metadata that names no distribution is passed by.
    nameless = site / "nameless-1.0.dist-info"
    nameless.mkdir()
    (nameless / "METADATA").write_text("Metadata-Version: 2.1\n", encoding="utf-8")
    env = read_installed(metadata.distributions(path=[str(site)]))
    assert env.brings("Acme_Host") == {"acme-host", "acme-dep", "acme-win"}
    assert set(env.requires) == {"acme-host", "acme-dep"}
    assert env.brings("acme-unknown") == {"acme-unknown"}
    assert requirement_name("PyYAML[ext] >=6; python_version > '3'") == "pyyaml"
    assert requirement_name("; extra == 'x'") == ""
