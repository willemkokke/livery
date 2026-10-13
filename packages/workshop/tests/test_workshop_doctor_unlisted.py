"""The host doctor's line for an installed extension nothing lists."""

from __future__ import annotations

from livery.workshop._doctor import unlisted


def test_a_package_level_extension_is_listed_by_a_package() -> None:
    assert unlisted("nanobind", "livery-workshop", ("package",)) == (
        "extensions: nanobind (livery-workshop) is installed and not listed;"
        " it does nothing until a package's extensions names it"
    )


def test_an_extension_of_both_levels_names_both_lists() -> None:
    line = unlisted("acme", "", ("workspace", "package"))
    assert line.endswith(
        "nothing until [workspace] extensions or a package's extensions names it"
    )
    assert "acme is installed" in line


def test_a_workspace_extension_and_one_declaring_no_level_name_the_workspace() -> None:
    for levels in (("workspace",), ()):
        assert unlisted("docs", "", levels).endswith(
            "nothing until [workspace] extensions names it"
        )
