"""The affected engine: seeds, closures, and the everything fallback."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from livery.workshop._git_ops import GitOps
from livery.workshop._graph import (
    affected_from_paths,
    affected_packages,
    dependents_closure,
)
from livery.workshop._packages import Package, discover_packages
from workshop_seeds import Seeds, _seed_home, pushed, seed_copier  # noqa: F401


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True)


def _packages(root: Path) -> None:
    """Four packages: a chain of three, and one beside it."""
    (root / "workshop.toml").write_text("[workspace]\n")
    for name, extra in (
        ("core", ""),
        ("mid", '[[depends]]\npath = "packages/core"\nkind = "build"\n'),
        ("top", '[[depends]]\npath = "packages/mid"\nkind = "build"\n'),
        ("aside", ""),
    ):
        directory = root / "packages" / name
        directory.mkdir(parents=True)
        (directory / "workshop.toml").write_text(
            f'type = "python"\nname = "livery-{name}"\n{extra}'
        )
        (directory / "pyproject.toml").write_text(
            f'[project]\nname = "livery-{name}"\ndependencies = []\n'
        )
        (directory / "thing.py").write_text("x = 1\n")


def _build(base: Path) -> None:
    root = pushed(base, clone="ws", fill=_packages)
    _git(root, "checkout", "-b", "feat/change")


def _workspace(seeds: Seeds) -> Path:
    """The four-package workspace on a feature branch, copied for this test."""
    return seeds("graph", _build) / "ws"


def test_the_closure_follows_reversed_edges(seeds: Seeds) -> None:
    root = _workspace(seeds)
    packages = discover_packages(root)
    closure = dependents_closure(packages, {"packages/core"})
    assert [p.path for p in closure] == [
        "packages/core",
        "packages/mid",
        "packages/top",
    ]
    assert [p.path for p in dependents_closure(packages, {"packages/top"})] == [
        "packages/top"
    ]


def test_a_leaf_change_affects_only_its_closure(seeds: Seeds) -> None:
    root = _workspace(seeds)
    (root / "packages" / "mid" / "thing.py").write_text("x = 2\n")
    affected = affected_packages(root, GitOps(root))
    assert affected is not None
    assert [p.path for p in affected] == ["packages/mid", "packages/top"]


def test_a_workspace_tests_change_affects_that_unit_alone(
    seeds: Seeds, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _workspace(seeds)
    # Refusals first: the directory changed and gone is a root change,
    # since the unit cannot run.
    (root / "tests").mkdir()
    (root / "tests" / "test_all.py").write_text("def test_it():\n    pass\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "test: the workspace tests")
    _git(root, "push", "origin", "feat/change:main")
    _git(root, "fetch", "origin")
    (root / "tests" / "test_all.py").unlink()
    (root / "tests").rmdir()
    assert affected_packages(root, GitOps(root)) is None
    assert "tests/: changed and gone; everything runs" in capsys.readouterr().out
    # A change under the directory affects the workspace tests alone,
    # answered as a package of their own.
    (root / "tests").mkdir()
    (root / "tests" / "test_all.py").write_text("def test_it():\n    assert True\n")
    affected = affected_packages(root, GitOps(root))
    assert affected is not None
    assert [p.path for p in affected] == ["tests"]
    assert affected[0].name == "workspace-tests"
    # With a package change the closure comes first and the unit last.
    (root / "packages" / "mid" / "thing.py").write_text("x = 2\n")
    affected = affected_packages(root, GitOps(root))
    assert affected is not None
    assert [p.path for p in affected] == ["packages/mid", "packages/top", "tests"]
    # A root file beside it still widens to everything.
    (root / "tasks.py").write_text("# touched\n")
    assert affected_packages(root, GitOps(root)) is None


def test_a_root_change_affects_everything(seeds: Seeds) -> None:
    root = _workspace(seeds)
    (root / "workshop.toml").write_text("[workspace]\n# touched\n")
    assert affected_packages(root, GitOps(root)) is None


def test_no_change_affects_nothing(seeds: Seeds) -> None:
    root = _workspace(seeds)
    affected = affected_packages(root, GitOps(root))
    assert affected == ()


def test_committed_and_uncommitted_changes_both_count(seeds: Seeds) -> None:
    root = _workspace(seeds)
    (root / "packages" / "aside" / "thing.py").write_text("x = 3\n")
    _git(root, "commit", "-am", "feat: aside moves")
    (root / "packages" / "core" / "thing.py").write_text("x = 4\n")
    affected = affected_packages(root, GitOps(root))
    assert affected is not None
    assert {p.path for p in affected} == {
        "packages/aside",
        "packages/core",
        "packages/mid",
        "packages/top",
    }


# --- prose affects no package ----------------------------------------------------


def test_a_prose_only_change_affects_nothing(seeds: Seeds) -> None:
    from livery.workshop._graph import is_prose

    root = _workspace(seeds)
    (root / "notes").mkdir()
    (root / "notes" / "plan.md").write_text("# a plan\n")
    (root / "README.md").write_text("# the readme\n")
    (root / "packages" / "core" / "docs").mkdir(parents=True)
    (root / "packages" / "core" / "docs" / "index.md").write_text("# core\n")
    assert affected_packages(root, GitOps(root)) == ()
    assert is_prose("notes/anything.txt") and is_prose("packages/core/README.md")
    assert not is_prose("packages/core/thing.py") and not is_prose("tests/notes.py")


def test_a_mixed_change_narrows_to_its_packages_and_a_root_file_still_widens(
    seeds: Seeds,
) -> None:
    root = _workspace(seeds)
    (root / "notes").mkdir()
    (root / "notes" / "plan.md").write_text("# a plan\n")
    (root / "packages" / "mid" / "thing.py").write_text("x = 2\n")
    affected = affected_packages(root, GitOps(root))
    assert affected is not None
    assert [p.path for p in affected] == ["packages/mid", "packages/top"]
    (root / "tasks.py").write_text("# touched\n")
    assert affected_packages(root, GitOps(root)) is None


# --- the site's own files affect no package ------------------------------------


def test_a_site_only_change_affects_nothing(
    seeds: Seeds, capsys: pytest.CaptureFixture[str]
) -> None:
    # Refusals first: another root file still widens to everything,
    # and the reason names the file, so a widened gate is never silent.
    from livery.workshop._graph import is_site

    root = _workspace(seeds)
    (root / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    assert affected_packages(root, GitOps(root)) is None
    assert "pyproject.toml: outside the packages" in capsys.readouterr().out
    (root / "pyproject.toml").unlink()
    # The site's configuration and the root docs tree reach no gate:
    # the site build judges them, on every run.
    (root / "zensical.toml").write_text("site_name = 'x'\n")
    (root / "docs" / "assets").mkdir(parents=True)
    (root / "docs" / "assets" / "logo.svg").write_text("<svg/>\n")
    assert affected_packages(root, GitOps(root)) == ()
    assert is_site("zensical.toml") and is_site("docs/assets/logo.svg")
    # A package's own docs file is the package's; the workspace tests
    # and a nested zensical.toml are not the site's.
    assert not is_site("packages/core/docs/nav.toml")
    assert not is_site("tests/docs/thing.py") and not is_site("packages/zensical.toml")
    # Beside a package change the site files change nothing: the
    # legs narrow to the package.
    (root / "packages" / "mid" / "thing.py").write_text("x = 2\n")
    affected = affected_packages(root, GitOps(root))
    assert affected is not None
    assert [p.path for p in affected] == ["packages/mid", "packages/top"]


# --- what a change reaches, by the kind's classification -------------------------


def _paths(root: Path, *paths: str) -> None:
    for path in paths:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x = 1\n")


def test_the_python_kind_classifies_tests_support_source_and_configuration() -> None:
    from livery.workshop._backends import _python
    from livery.workshop._kinds import CONFIGURATION, SOURCE, TEST, TEST_SUPPORT

    package = Package(Path("packages/x"), "packages/x", "livery-x", "python", ())
    assert _python.classify(package, "tests/test_a.py") == TEST
    assert _python.classify(package, "tests/deep/a_test.py") == TEST
    assert _python.classify(package, "tests/conftest.py") == TEST_SUPPORT
    assert _python.classify(package, "tests/x_seeds.py") == TEST_SUPPORT
    assert _python.classify(package, "tests/data/test_a.json") == TEST_SUPPORT
    assert _python.classify(package, "src/livery/x/mod.py") == SOURCE
    assert _python.classify(package, "pyproject.toml") == CONFIGURATION
    assert _python.classify(package, "workshop.toml") == CONFIGURATION


def test_a_test_file_reaches_its_package_alone_and_runs_alone(seeds: Seeds) -> None:
    root = _workspace(seeds)
    _paths(root, "packages/mid/tests/test_a.py")
    packages = discover_packages(root)
    scope = affected_from_paths(root, packages, ["packages/mid/tests/test_a.py"])
    assert scope is not None
    assert [p.path for p in scope.packages] == ["packages/mid"]
    assert scope.tests == {"packages/mid": ("packages/mid/tests/test_a.py",)}
    # Test support and configuration widen to the suite and the dependents.
    for path in ("packages/mid/tests/conftest.py", "packages/mid/pyproject.toml"):
        scope = affected_from_paths(root, packages, [path])
        assert scope is not None and scope.tests == {}
        assert [p.path for p in scope.packages] == ["packages/mid", "packages/top"]
    # A test beside a source change of its package runs the suite.
    scope = affected_from_paths(
        root, packages, ["packages/mid/tests/test_a.py", "packages/mid/thing.py"]
    )
    assert scope is not None and scope.tests == {}
    assert [p.path for p in scope.packages] == ["packages/mid", "packages/top"]
    # A dependency's source change reaches the dependent's suite over its
    # own test selection.
    scope = affected_from_paths(
        root, packages, ["packages/core/thing.py", "packages/top/tests/test_a.py"]
    )
    assert scope is not None and scope.tests == {}
    assert [p.path for p in scope.packages] == [
        "packages/core",
        "packages/mid",
        "packages/top",
    ]
    # Two packages' tests alone: each runs its own files.
    scope = affected_from_paths(
        root,
        packages,
        ["packages/aside/tests/test_z.py", "packages/mid/tests/test_a.py"],
    )
    assert scope is not None
    assert [p.path for p in scope.packages] == ["packages/aside", "packages/mid"]
    assert scope.tests == {
        "packages/aside": ("packages/aside/tests/test_z.py",),
        "packages/mid": ("packages/mid/tests/test_a.py",),
    }


def test_the_workspace_tests_unit_runs_its_changed_files_alone_or_its_suite(
    seeds: Seeds,
) -> None:
    root = _workspace(seeds)
    _paths(root, "tests/test_all.py")
    packages = discover_packages(root)
    scope = affected_from_paths(root, packages, ["tests/test_all.py"])
    assert scope is not None
    assert [p.path for p in scope.packages] == ["tests"]
    assert scope.tests == {"tests": ("tests/test_all.py",)}
    scope = affected_from_paths(
        root, packages, ["tests/conftest.py", "tests/test_all.py"]
    )
    assert scope is not None and scope.tests == {}
    assert [p.path for p in scope.packages] == ["tests"]


def test_a_docs_page_reaches_its_examples_harness_narrowed(seeds: Seeds) -> None:
    """Contract: a page edit runs the page's own examples; other prose runs nothing."""
    from livery.workshop._graph import EXAMPLES_HARNESS, docs_page

    root = _workspace(seeds)
    _paths(root, f"packages/mid/{EXAMPLES_HARNESS}")
    (root / "packages/mid/docs").mkdir(parents=True, exist_ok=True)
    (root / "packages/mid/docs/guide.md").write_text("# guide\n")
    packages = discover_packages(root)
    mid = next(p for p in packages if p.path == "packages/mid")
    assert docs_page(packages, "packages/mid/docs/guide.md") is mid
    assert docs_page(packages, "packages/mid/docs/_generated/api.md") is None
    assert docs_page(packages, "docs/index.md") is None
    scope = affected_from_paths(root, packages, ["packages/mid/docs/guide.md"])
    assert scope is not None
    assert [p.path for p in scope.packages] == ["packages/mid"]
    assert scope.tests == {"packages/mid": (f"packages/mid/{EXAMPLES_HARNESS}",)}
    assert scope.pages == {"packages/mid": ("packages/mid/docs/guide.md",)}
    # Two pages of one package: one harness run, both pages named.
    scope = affected_from_paths(
        root, packages, ["packages/mid/docs/guide.md", "packages/mid/docs/index.md"]
    )
    assert scope is not None
    assert scope.tests == {"packages/mid": (f"packages/mid/{EXAMPLES_HARNESS}",)}
    assert scope.pages["packages/mid"] == (
        "packages/mid/docs/guide.md",
        "packages/mid/docs/index.md",
    )
    # A page beside a source change of its package runs the suite whole.
    scope = affected_from_paths(
        root, packages, ["packages/mid/docs/guide.md", "packages/mid/thing.py"]
    )
    assert scope is not None and scope.tests == {} and scope.pages == {}
    # A package without a harness, the root docs, a note and a README
    # reach nothing.
    for path in (
        "packages/core/docs/guide.md",
        "docs/index.md",
        "notes/20260101-plan.md",
        "README.md",
    ):
        scope = affected_from_paths(root, packages, [path])
        assert scope is not None and scope.packages == () and scope.pages == {}, path
