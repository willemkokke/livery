"""Overlay composition: add or declared replace, never an edit."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.workshop._compose import compose_source
from livery.workshop._templates import apply_project, project_drift, render_source

ROOT = Path(__file__).resolve().parents[3]
TEMPLATES = ROOT / "packages/workshop/src/livery/workshop/templates"

_FAILURES = (BaseException,)


def _home(tmp_path: Path) -> Path:
    """A layer home: the base stack plus a member overlay layer."""
    root = tmp_path / "home"
    (root / "packages" / "brand").mkdir(parents=True)
    (root / "workshop.toml").write_text(
        "[workspace]\n"
        'layers = ["livery.workshop", "acme.brand"]\n'
        "\n"
        '[forge]\nkind = "github"\nowner = "acme"\n'
        "\n"
        '[ci]\nrunners = ["ubuntu-latest"]\nrequired_context = "gate"\n'
    )
    (root / "pyproject.toml").write_text(
        '[project]\nname = "home"\nrequires-python = ">=3.11"\n'
    )
    member = root / "packages" / "brand"
    (member / "workshop.toml").write_text('type = "python"\nname = "acme-brand"\n')
    (member / "pyproject.toml").write_text('[project]\nname = "acme-brand"\n')
    overlay = member / "src" / "acme" / "brand" / "templates"
    (overlay / "project").mkdir(parents=True)
    return root


def _overlay(root: Path) -> Path:
    return root / "packages/brand/src/acme/brand/templates"


def test_add_and_declared_replace_compose(tmp_path: Path) -> None:
    root = _home(tmp_path)
    overlay = _overlay(root)
    (overlay / "project" / "BRAND.md.jinja").write_text("# {{ project_name }}\n")
    (overlay / "project" / "README.md.jinja").write_text("# the brand's own\n")
    (overlay / "overlay.toml").write_text(
        '[[replace]]\npath = "project/README.md.jinja"\n'
        'reason = "the brand\'s own README seed"\n'
        "\n[questions.brand_motto]\n"
        'type = "str"\ndefault = "make good things"\n'
    )
    composed = compose_source(root, tmp_path / "out")
    assert (composed.path / "project" / "BRAND.md.jinja").is_file()
    text = (composed.path / "project" / "README.md.jinja").read_text()
    assert text == "# the brand's own\n"
    assert composed.owners["project/README.md.jinja"] == "acme.brand"
    assert composed.owners["project/pyproject.toml.jinja"] == "livery.workshop"
    config = (composed.path / "copier.yml").read_text()
    assert "Contributed by the acme.brand layer" in config
    assert "brand_motto" in config


def test_an_undeclared_same_path_file_is_refused(tmp_path: Path) -> None:
    root = _home(tmp_path)
    overlay = _overlay(root)
    (overlay / "project" / "pyproject.toml.jinja").write_text("# patched\n")
    with pytest.raises(_FAILURES) as caught:
        compose_source(root, tmp_path / "out")
    text = str(caught.value)
    assert "project/pyproject.toml.jinja" in text
    assert "acme.brand" in text and "never edits" in text


def test_an_unknown_kind_names_the_kinds_the_stack_has(tmp_path: Path) -> None:
    root = _home(tmp_path)
    overlay = _overlay(root)
    (overlay / "maya-plugin").mkdir()
    (overlay / "maya-plugin" / "x.jinja").write_text("x\n")
    with pytest.raises(_FAILURES) as caught:
        compose_source(root, tmp_path / "out")
    text = str(caught.value)
    assert "maya-plugin" in text and "package-python-layer" in text


def test_a_stale_replace_declaration_is_refused(tmp_path: Path) -> None:
    root = _home(tmp_path)
    overlay = _overlay(root)
    (overlay / "project" / "GONE.md.jinja").write_text("x\n")
    (overlay / "overlay.toml").write_text(
        '[[replace]]\npath = "project/GONE.md.jinja"\nreason = "stale"\n'
    )
    with pytest.raises(_FAILURES) as caught:
        compose_source(root, tmp_path / "out")
    assert "no lower layer ships" in str(caught.value)


def test_an_undefaulted_overlay_question_is_refused(tmp_path: Path) -> None:
    root = _home(tmp_path)
    overlay = _overlay(root)
    (overlay / "overlay.toml").write_text('[questions.motto]\ntype = "str"\n')
    with pytest.raises(_FAILURES) as caught:
        compose_source(root, tmp_path / "out")
    assert "without a prompt" in str(caught.value)


def test_a_replace_without_a_reason_is_refused(tmp_path: Path) -> None:
    root = _home(tmp_path)
    overlay = _overlay(root)
    (overlay / "project" / "README.md.jinja").write_text("x\n")
    (overlay / "overlay.toml").write_text(
        '[[replace]]\npath = "project/README.md.jinja"\n'
    )
    with pytest.raises(_FAILURES) as caught:
        compose_source(root, tmp_path / "out")
    assert "reason" in str(caught.value)


def test_the_home_gate_composes_the_local_overlay_and_names_the_owner(
    tmp_path: Path,
) -> None:
    import shutil

    root = _home(tmp_path)
    # The home carries the whole template source locally too, the
    # monorepo shape, so the drift gate runs.
    shutil.copytree(TEMPLATES, root / "templates")
    contract = (root / "workshop.toml").read_text()
    (root / "workshop.toml").write_text(
        contract.replace(
            'layers = ["livery.workshop", "acme.brand"]',
            'layers = ["livery.workshop", "acme.brand"]\ntemplates = "templates"',
        )
    )
    overlay = _overlay(root)
    (overlay / "project" / ".gitignore.jinja").write_text(
        (root / "templates/project/.gitignore.jinja").read_text() + "brand-extra/\n"
    )
    (overlay / "overlay.toml").write_text(
        '[[replace]]\npath = "project/.gitignore.jinja"\n'
        'reason = "the brand ignores its own build tree"\n'
    )
    (root / ".copier-answers.yml").write_text(
        "_src_path: templates\n"
        "kind: project\n"
        "project_name: home\n"
        "author_name: A\n"
        "author_email: a@example.com\n"
        "copyright_year: '2026'\n"
        "namespace_package: acme\n"
        "packages: []\n"
    )
    source, _ref, owners = render_source(root)
    assert source.endswith("composed-templates")
    assert owners["project/.gitignore.jinja"] == "acme.brand"
    changed = apply_project(root)
    assert ".gitignore" in changed
    assert "brand-extra/" in (root / ".gitignore").read_text()
    assert project_drift(root) == []
    # A doctored composed file names the layer that owns it.
    (root / ".gitignore").write_text("# doctored\n")
    drift = project_drift(root)
    assert any(
        ".gitignore: differs from its render (the acme.brand layer owns it)" in line
        for line in drift
    )
