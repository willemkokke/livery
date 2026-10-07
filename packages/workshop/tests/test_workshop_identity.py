"""The workspace's identity and members, read from the contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.footman import Failed
from livery.workshop._identity import (
    is_born,
    package_facts,
    project_facts,
    roster,
    template_of,
)


def _member(
    root: Path, name: str, contract: str, manifest: str = "pyproject.toml"
) -> Path:
    directory = root / "packages" / name
    directory.mkdir(parents=True)
    (directory / "workshop.toml").write_text(contract)
    (directory / manifest).write_text("")
    return directory


def test_a_contract_off_the_shape_refuses_naming_the_key(tmp_path: Path) -> None:
    (tmp_path / "workshop.toml").write_text('[workspace]\nauthors = "me"\n')
    with pytest.raises(Failed, match=r"workspace\.authors"):
        project_facts(tmp_path)
    (tmp_path / "workshop.toml").write_text('[workspace]\nnmae = "x"\n')
    with pytest.raises(Failed, match=r"nmae"):
        project_facts(tmp_path)


def test_a_fact_the_contract_leaves_out_takes_the_birth_default(tmp_path: Path) -> None:
    root = tmp_path / "acme-tools"
    root.mkdir()
    (root / "workshop.toml").write_text("[workspace]\n")
    assert not is_born(root)
    facts = project_facts(root)
    assert facts["project_name"] == "acme-tools"
    assert facts["project_description"] == "The acme-tools monorepo (virtual root)."
    assert facts["author_name"] == "acme-tools authors"
    assert facts["author_email"] == ""
    assert facts["namespace_package"] == "acme_tools"
    assert facts["packages"] == []
    (root / "workshop.toml").write_text(
        '[workspace]\nname = "acme"\nnamespace = "acme.tools"\n'
        'authors = [{ name = "A", email = "a@e" }]\ncopyright-year = "2026"\n'
    )
    assert is_born(root)
    facts = project_facts(root)
    assert (facts["project_name"], facts["namespace_package"]) == ("acme", "acme.tools")
    assert (facts["author_name"], facts["author_email"]) == ("A", "a@e")
    assert facts["copyright_year"] == "2026"


def test_the_roster_is_discovery_with_each_python_members_extras(
    tmp_path: Path,
) -> None:
    (tmp_path / "workshop.toml").write_text('[workspace]\nname = "acme"\n')
    _member(tmp_path, "zeta", 'kind = "python"\nname = "acme-zeta"\n')
    _member(
        tmp_path,
        "alpha",
        'kind = "python"\nname = "acme-alpha"\ndev-extras = ["test", "docs"]\n',
    )
    _member(
        tmp_path, "native", 'kind = "cpp-conan"\nname = "acme-native"\n', "conanfile.py"
    )
    assert roster(tmp_path) == [
        {
            "dir": "alpha",
            "name": "acme-alpha",
            "kind": "python",
            "dev": "acme-alpha[test,docs]",
        },
        {"dir": "native", "name": "acme-native", "kind": "cpp-conan"},
        {"dir": "zeta", "name": "acme-zeta", "kind": "python", "dev": "acme-zeta"},
    ]


def test_a_members_facts_come_from_its_contract_and_the_projects(
    tmp_path: Path,
) -> None:
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nname = "acme"\nnamespace = "acme"\n'
        'authors = [{ name = "A", email = "a@e" }]\ncopyright-year = "2026"\n'
    )
    plain = _member(tmp_path, "plain", 'kind = "python"\nname = "acme-plain"\n')
    facts = package_facts(tmp_path, plain)
    assert facts["package_name"] == "acme-plain"
    assert facts["package_description"] == "acme-plain: a acme workspace package."
    assert (facts["package_dir"], facts["kind"]) == ("plain", "package-python")
    assert (facts["author_name"], facts["copyright_year"]) == ("A", "2026")
    # A template variant of the kind is the package's own declaration.
    variant = _member(
        tmp_path,
        "brand",
        'kind = "python"\nname = "acme-brand"\ndescription = "The brand."\n'
        'template = "package-extension"\n',
    )
    assert template_of(variant) == "package-extension"
    assert package_facts(tmp_path, variant)["package_description"] == "The brand."
