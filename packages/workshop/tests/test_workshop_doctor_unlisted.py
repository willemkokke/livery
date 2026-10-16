"""The host doctor's line for an installed extension nothing lists."""

from __future__ import annotations

from livery.workshop._doctor import unlisted


def test_an_extension_that_composes_nothing_in_a_package_names_the_root_s_list() -> (
    None
):
    assert unlisted("docs", "", per_package=False) == (
        "extensions: docs is installed and not listed; it does nothing until"
        " [workspace] extensions names it"
    )


def test_an_extension_that_composes_a_package_names_both_lists() -> None:
    assert unlisted("nanobind", "livery-extensions-nanobind", per_package=True) == (
        "extensions: nanobind (livery-extensions-nanobind) is installed and not"
        " listed; it does nothing until [workspace] extensions or a package's"
        " extensions names it"
    )
