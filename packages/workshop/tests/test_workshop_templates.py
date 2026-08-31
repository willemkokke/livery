"""The render gate: byte-honest, drift-naming, namespace-clean."""

from __future__ import annotations

import shutil
from pathlib import Path

from livery.workshop._templates import (
    apply_project,
    project_drift,
    read_answers,
    render,
)

ROOT = Path(__file__).resolve().parents[3]


def _template_instance(tmp_path: Path) -> Path:
    """A scratch workspace carrying the real template source and answers."""
    shutil.copytree(ROOT / "templates", tmp_path / "templates")
    shutil.copy(ROOT / ".copier-answers.yml", tmp_path / ".copier-answers.yml")
    return tmp_path


def test_the_monorepo_matches_its_own_render() -> None:
    # The dogfood pin: contract 8 says the monorepo is the workshop's
    # first instance, so a fresh render of the project kind agrees
    # with the committed tree byte for byte.
    assert project_drift(ROOT) == []


def test_apply_settles_and_drift_names_the_file(tmp_path: Path) -> None:
    root = _template_instance(tmp_path)
    changed = apply_project(root)
    assert "livery.toml" in changed and "pyproject.toml" in changed
    assert project_drift(root) == []
    assert apply_project(root) == []  # idempotent: a clean tree changes nothing
    (root / "livery.toml").write_text("drifted\n")
    assert project_drift(root) == ["livery.toml: differs from its render"]


def test_a_package_renders_namespace_clean(tmp_path: Path) -> None:
    destination = tmp_path / "scratch"
    answers = read_answers(ROOT / ".copier-answers.yml")
    render(
        ROOT / "templates",
        destination,
        {
            "kind": "package-python",
            "package_name": "livery-scratch",
            "package_description": "livery-scratch: a livery workspace package.",
            "namespace_package": "livery",
            "author_name": answers["author_name"],
            "author_email": answers["author_email"],
            "copyright_year": answers["copyright_year"],
            "forge_owner": answers["forge_owner"],
            "project_name": answers["project_name"],
            "python_versions": answers["python_versions"],
        },
    )
    module = destination / "src" / "livery" / "scratch"
    assert (module / "__init__.py").is_file()
    assert (module / "py.typed").is_file()
    assert (destination / "tests" / "test_scratch_package.py").is_file()
    # The namespace stays PEP 420: no livery/__init__.py, ever.
    assert not (destination / "src" / "livery" / "__init__.py").exists()
    assert "livery-scratch" in (destination / "pyproject.toml").read_text()
