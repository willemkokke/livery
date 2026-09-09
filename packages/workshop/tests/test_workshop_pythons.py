"""The python matrix as contract config: refusals first, then the override."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.workshop import _pythons

_FAILURES = (BaseException,)


def _root(tmp_path: Path, ci: str = "", floor: str = "3.11") -> Path:
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nlayers = ["livery.workshop"]\n\n[forge]\nkind = "gitea"\n'
        'owner = "owner"\n\n[ci]\nrunners = ["ubuntu-latest"]\n' + ci
    )
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "scratch"\nrequires-python = ">={floor}"\n'
    )
    return tmp_path


def test_a_declaration_that_is_not_a_list_refuses(tmp_path: Path) -> None:
    with pytest.raises(
        _FAILURES, match=r"\[ci\] python-versions must be a non-empty list"
    ):
        _pythons.python_matrix(_root(tmp_path, 'python-versions = "3.14"\n'))


def test_an_empty_declaration_refuses(tmp_path: Path) -> None:
    with pytest.raises(_FAILURES, match="must be a non-empty list"):
        _pythons.python_matrix(_root(tmp_path, "python-versions = []\n"))


def test_a_value_that_is_not_a_version_refuses_naming_it(tmp_path: Path) -> None:
    with pytest.raises(_FAILURES, match="entry 'py3' is not a python version"):
        _pythons.python_matrix(_root(tmp_path, 'python-versions = ["py3"]\n'))
    with pytest.raises(_FAILURES, match="entry 3 is not a python version"):
        _pythons.python_matrix(_root(tmp_path, "python-versions = [3]\n"))


def test_the_derived_pair_stands_without_a_declaration(tmp_path: Path) -> None:
    assert _pythons.python_matrix(_root(tmp_path)) == [
        "3.11",
        _pythons.NEWEST_SUPPORTED,
    ]
    assert _pythons.declared_pythons(_root(tmp_path)) is None


def test_a_declaration_wins_and_may_name_a_free_threaded_build(tmp_path: Path) -> None:
    root = _root(tmp_path, 'python-versions = ["3.14", "3.14t"]\n')
    assert _pythons.python_matrix(root) == ["3.14", "3.14t"]


def test_the_emitted_matrices_follow_the_declaration(tmp_path: Path) -> None:
    import yaml

    from livery.workshop._ci_generate import generate

    root = _root(tmp_path, 'python-versions = ["3.14"]\n')
    (root / "workshop.toml").write_text(
        (root / "workshop.toml")
        .read_text()
        .replace('kind = "gitea"', 'kind = "gitea"\nurl = "https://forge.example.com"')
    )
    files = generate(root)
    ci = yaml.safe_load(files[".gitea/workflows/ci.yml"])
    assert ci["jobs"]["check"]["strategy"]["matrix"]["python"] == ["3.14"]
    nightly = yaml.safe_load(files[".gitea/workflows/nightly.yml"])
    assert nightly["jobs"]["nightly"]["strategy"]["matrix"]["python"] == ["3.14"]
