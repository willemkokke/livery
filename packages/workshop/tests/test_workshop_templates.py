"""The render gate: byte-honest, drift-naming, namespace-clean."""

from __future__ import annotations

import shutil
from pathlib import Path

from livery.workshop._templates import (
    apply_packages,
    apply_project,
    package_drift,
    project_drift,
    read_answers,
    render,
)

ROOT = Path(__file__).resolve().parents[3]


def _template_instance(tmp_path: Path) -> Path:
    """A scratch workspace carrying the real template source and answers."""
    shutil.copytree(ROOT / "templates", tmp_path / "templates")
    shutil.copy(ROOT / ".copier-answers.yml", tmp_path / ".copier-answers.yml")
    (tmp_path / "livery.toml").write_text('[workspace]\ntemplates = "templates"\n')
    return tmp_path


def test_the_template_source_is_the_contracts_call(tmp_path: Path) -> None:
    from livery.workshop._templates import (
        DEFAULT_TEMPLATE_SOURCE,
        local_template_dir,
        template_source,
    )

    (tmp_path / "livery.toml").write_text("[workspace]\n")
    assert template_source(tmp_path) == DEFAULT_TEMPLATE_SOURCE
    assert local_template_dir(tmp_path) is None  # remote: no local dir
    (tmp_path / "livery.toml").write_text(
        '[workspace]\ntemplates = "my-fork-checkout"\n'
    )
    assert local_template_dir(tmp_path) is None  # declared but absent
    (tmp_path / "my-fork-checkout").mkdir()
    assert local_template_dir(tmp_path) == tmp_path / "my-fork-checkout"
    (tmp_path / "livery.toml").write_text(
        '[workspace]\ntemplates = "git@example.com:me/fork.git"\n'
    )
    assert local_template_dir(tmp_path) is None


def test_the_monorepo_matches_its_own_render() -> None:
    # The dogfood pin: contract 8 says the monorepo is the workshop's
    # first instance, so a fresh render of the project kind agrees
    # with the committed tree byte for byte.
    assert project_drift(ROOT) == []
    # Each package's managed files answer to the template too.
    assert package_drift(ROOT) == []


def test_package_drift_judges_only_the_managed_files(tmp_path: Path) -> None:
    # The seeds must NOT be judged: a package writes its own README
    # and changelog the moment it is born, and reporting those as
    # drift would ask a living package to revert to its stub.
    root = _template_instance(tmp_path)
    apply_project(root)
    package = root / "packages" / "thing"
    package.mkdir(parents=True)
    answers = read_answers(ROOT / "packages" / "workshop" / ".copier-answers.yml")
    answers["package_name"] = "livery-thing"
    (package / ".copier-answers.yml").write_text(
        "\n".join(f"{key}: {value!r}" for key, value in answers.items()) + "\n"
    )
    # Nothing rendered yet: the managed file is named as missing.
    assert package_drift(root) == [
        "packages/thing/cliff.toml: rendered, but missing from the repository"
    ]
    assert "packages/thing/cliff.toml" in apply_packages(root)
    assert package_drift(root) == []
    assert apply_packages(root) == []  # idempotent
    # A README the package's authors wrote is not the template's to keep.
    (package / "README.md").write_text("# thing\n\nWritten by its authors.\n")
    assert package_drift(root) == []
    (package / "cliff.toml").write_text("# edited by hand\n")
    assert package_drift(root) == ["packages/thing/cliff.toml: differs from its render"]


def test_apply_settles_and_drift_names_the_file(tmp_path: Path) -> None:
    root = _template_instance(tmp_path)
    changed = apply_project(root)
    assert "livery.toml" in changed and "pyproject.toml" in changed
    assert project_drift(root) == []
    assert apply_project(root) == []  # idempotent: a clean tree changes nothing
    # The drift stays valid TOML: the drift walker itself reads the
    # contract for the template source.
    (root / "livery.toml").write_text('[workspace]\ntemplates = "templates"\n')
    assert project_drift(root) == ["livery.toml: differs from its render"]


def _render_kind(tmp_path: Path, forge_kind: str, **extra: object) -> Path:
    destination = tmp_path / forge_kind
    answers = read_answers(ROOT / ".copier-answers.yml")
    answers.update({"kind": "project", "forge_kind": forge_kind}, **extra)
    answers.update(extra)
    render(ROOT / "templates", destination, answers)
    return destination


def test_each_forge_kind_renders_a_ci_definition_that_lints(
    tmp_path: Path,
) -> None:
    import yaml

    github = _render_kind(tmp_path, "github")
    ci = yaml.safe_load((github / ".github" / "workflows" / "ci.yml").read_text())
    assert "gate" in ci["jobs"]
    assert not (github / ".gitea").exists()
    assert not (github / ".gitlab-ci.yml").exists()

    gitea = _render_kind(tmp_path, "gitea", forge_url="https://forge.example.com")
    ci = yaml.safe_load((gitea / ".gitea" / "workflows" / "ci.yml").read_text())
    assert "gate" in ci["jobs"]
    release = yaml.safe_load(
        (gitea / ".gitea" / "workflows" / "release.yml").read_text()
    )
    assert "publish" in release["jobs"]
    contract = (gitea / "livery.toml").read_text()
    assert 'url = "https://forge.example.com"' in contract
    assert not (gitea / ".github").exists()

    gitlab = _render_kind(tmp_path, "gitlab")
    pipeline = yaml.safe_load((gitlab / ".gitlab-ci.yml").read_text())
    assert "gate" in pipeline and "release" in pipeline
    assert pipeline["workflow"]["rules"]  # MRs, main, tags, LIVERY_WORKFLOW
    assert not (gitlab / ".github").exists()
    for rendered in (github, gitea, gitlab):
        assert 'required_context = "gate"' in (rendered / "livery.toml").read_text()


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
