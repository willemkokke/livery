"""What the docs extension reads from the contracts and its queries' answers.

The fallbacks and the refusals first: a project without a name, a
workspace without packages, a value of the wrong shape that reaches a
reader without the contract's judge, answers whose requires close a
cycle. Then the members policy the listed extensions answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from extensions_docs_workspace import fake_extensions
from livery.extensions.docs import _contract
from livery.extensions.docs._config import ALL_MEMBERS, PUBLIC_MEMBERS, members_policy
from livery.extensions.docs._contract import (
    docs_requirements,
    package_generators,
    project_name,
)
from livery.extensions.docs._site import theme_values
from livery.extensions.docs._theme import THEME_DEFAULT
from livery.footman import Failed
from livery.workshop._packages import discover_packages

_FAILURES = (SystemExit, Failed)


def _workspace(tmp_path: Path, listed: str = '["docs"]') -> Path:
    """A workspace listing *listed*, with one member, core."""
    root = tmp_path / "ws"
    member = root / "packages" / "core"
    member.mkdir(parents=True)
    (root / "workshop.toml").write_text(f"[workspace]\nextensions = {listed}\n")
    (root / "pyproject.toml").write_text('[project]\nname = "acme-home"\n')
    (member / "workshop.toml").write_text('extensions = []\nname = "acme-core"\n')
    return root


def _relist(root: Path, listed: str) -> None:
    (root / "workshop.toml").write_text(f"[workspace]\nextensions = {listed}\n")


# The fallbacks first.


def test_a_root_project_without_a_name_names_the_site_after_its_directory(
    tmp_path: Path,
) -> None:
    root = tmp_path / "home"
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nversion = "1.0.0"\n')
    assert project_name(root) == "home"


def test_a_workspace_without_packages_installs_no_system_packages(
    tmp_path: Path,
) -> None:
    (tmp_path / "workshop.toml").write_text('[workspace]\nextensions = ["docs"]\n')
    assert docs_requirements(tmp_path) == ()


# The contract's judge refuses these before a reader sees them; the
# reader refuses them on its own as well, naming the file.


@pytest.mark.parametrize(
    ("table", "refusal"),
    [
        ({"generators": "docsgen.one"}, r"\[docs\] generators must be a list"),
        (
            {"generators": [{"verb": "docsgen.one", "requires": [3]}]},
            r"\[docs\] generators entry .* is not a verb name",
        ),
    ],
)
def test_generators_of_the_wrong_shape_refuse_without_the_judge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    table: dict[str, object],
    refusal: str,
) -> None:
    root = _workspace(tmp_path)
    (core,) = discover_packages(root)
    monkeypatch.setattr(_contract, "read_contract", lambda directory: {"docs": table})
    with pytest.raises(_FAILURES, match=rf"workshop\.toml: {refusal}"):
        package_generators(core)


def test_answers_whose_requires_close_a_cycle_leave_the_default_theme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Neither answer is the nearest, so neither decides the key; the
    # layering graph names the cycle.
    fake_extensions(
        tmp_path,
        monkeypatch,
        bold=(
            '[extension]\nrequires = ["acme.calm"]\n'
            '[queries."docs.theme"]\nvalue = { "language" = "nl" }\n'
        ),
        calm=(
            '[extension]\nrequires = ["acme.bold"]\n'
            '[queries."docs.theme"]\nvalue = { "language" = "de" }\n'
        ),
    )
    root = _workspace(tmp_path, '["docs", "acme.bold", "acme.calm"]')
    assert theme_values(root)["language"] == THEME_DEFAULT["language"]


# Then the members policy.


def test_the_members_policy_is_public_until_a_listed_extension_answers_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(
        tmp_path,
        monkeypatch,
        wide='[extension]\n[queries."docs.members"]\nvalue = "all"\n',
    )
    root = _workspace(tmp_path)
    assert members_policy(root) == PUBLIC_MEMBERS
    _relist(root, '["docs", "acme.wide"]')
    assert members_policy(root) == ALL_MEMBERS
