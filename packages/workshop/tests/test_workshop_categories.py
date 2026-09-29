"""The category and channel registries: the refusals first, then what they answer."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.workshop._categories import (
    WORKSPACE,
    CategoryError,
    ChannelRule,
    Provenance,
    category_of,
    channel_of,
    register_categories,
    register_channels,
    unregister_categories,
    unregister_channels,
)
from livery.workshop._packages import Package, discover_packages


def _package(
    kind: str = "python", categories: tuple[tuple[str, tuple[str, ...]], ...] = ()
) -> Package:
    return Package(
        Path("packages/x"), "packages/x", "livery-x", kind, (), categories=categories
    )


def _member(root: Path, contract: str) -> Path:
    """A python member with *contract*'s lines after its kind and name."""
    member = root / "packages" / "x"
    member.mkdir(parents=True, exist_ok=True)
    member.joinpath("workshop.toml").write_text(
        f'kind = "python"\nname = "livery-x"\n{contract}'
    )
    member.joinpath("pyproject.toml").write_text('[project]\nname = "livery-x"\n')
    return member


@pytest.fixture
def brand_layer():
    yield
    for kind in ("python", "base"):
        unregister_categories(kind, layer="acme.brand")
        unregister_categories(kind, layer="acme.other")
    unregister_channels(layer="acme.brand")


# The refusals first.


def test_two_rules_of_one_specificity_claiming_one_path_refuse_naming_both(
    brand_layer,
) -> None:
    register_categories(
        "python", [("src/**/*_vendor.py", "vendored")], layer="acme.brand"
    )
    register_categories(
        "python", [("src/**/*_vendor.py", "generated")], layer="acme.other"
    )
    with pytest.raises(CategoryError, match="two rules of one specificity") as caught:
        category_of(_package(), "src/livery/x/lib_vendor.py")
    assert "acme.brand" in str(caught.value) and "acme.other" in str(caught.value)


def test_a_categories_value_off_the_shape_refuses_naming_it(tmp_path: Path) -> None:
    _member(tmp_path, '[categories]\nvendored = "docs/vendor/**"\n')
    with pytest.raises(ValueError, match=r"\[categories\] vendored must be a list"):
        discover_packages(tmp_path)
    _member(tmp_path, "categories = 3\n")
    with pytest.raises(ValueError, match=r"\[categories\] is a table"):
        discover_packages(tmp_path)


def test_a_channels_table_in_a_package_refuses(tmp_path: Path) -> None:
    _member(tmp_path, '[channels]\nrendered = ["x"]\n')
    with pytest.raises(ValueError, match=r"\[channels\] is not a package's to say"):
        discover_packages(tmp_path)


def test_two_channel_rules_of_one_rank_refuse_naming_both(
    tmp_path: Path, brand_layer
) -> None:
    def claim(root: Path, relative: Path, emitted: frozenset[str] | None) -> Provenance:
        del root, relative, emitted
        return Provenance("brand", "acme", "edit")

    register_channels(
        [
            ChannelRule("one", claim, 200, "acme.brand"),
            ChannelRule("two", claim, 200, "acme.brand"),
        ]
    )
    with pytest.raises(CategoryError, match="two channel rules of rank 200"):
        channel_of(tmp_path, Path("anything.txt"), emitted=frozenset())


# Then what the registries answer, and who supplied it.


def test_a_layers_rule_answers_with_its_name_and_the_more_specific_wins(
    brand_layer,
) -> None:
    register_categories(
        "python", [("src/livery/x/_vendor/**", "vendored")], layer="acme.brand"
    )
    found = category_of(_package(), "src/livery/x/_vendor/lib.py")
    assert (found.name, found.supplier) == ("vendored", "acme.brand")
    assert category_of(_package(), "src/livery/x/mod.py").name == "source"


def test_the_package_exception_wins_over_the_kinds_rule() -> None:
    package = _package(categories=(("vendored", ("docs/assets/vendor/**",)),))
    found = category_of(package, "docs/assets/vendor/codemirror.js")
    assert (found.name, found.supplier) == ("vendored", "the package")
    assert category_of(package, "docs/assets/site.css").name == "asset"


def test_a_derived_kind_inherits_the_tables_up_its_chain() -> None:
    package = _package(kind="python-nanobind")
    assert category_of(package, "src/acme/ext.cpp").name == "source"
    assert category_of(package, "tests/test_ext.py").name == "test"
    assert category_of(package, "docs/examples/first.py").name == "example"
    assert category_of(package, "docs/index.md").name == "prose"
    assert category_of(package, "docs/nav.toml").name == "nav"
    assert category_of(package, "docs/_generated/api.md").name == "generated"


def test_the_workspace_unit_answers_for_the_roots_own_files(tmp_path: Path) -> None:
    unit = Package(tmp_path, ".", "workspace", WORKSPACE, ())
    expected = {
        "tests/test_a.py": "test",
        "tests/helpers.py": "test-support",
        "notes/musings.md": "notes",
        "docs/index.md": "site",
        "zensical.toml": "site",
        "README.md": "readme",
        "pyproject.toml": "configuration",
    }
    assert {path: category_of(unit, path).name for path in expected} == expected


def test_a_layers_channel_rule_answers_at_its_rank(tmp_path: Path, brand_layer) -> None:
    def seed(
        root: Path, relative: Path, emitted: frozenset[str] | None
    ) -> Provenance | None:
        del root, emitted
        if relative.name != "og-card.png":
            return None
        return Provenance("brand seed", "acme", "edit in the brand")

    register_channels([ChannelRule("brand-seed", seed, 150, "acme.brand")])
    found = channel_of(tmp_path, Path("docs/assets/og-card.png"), emitted=frozenset())
    assert found is not None
    assert (found.provenance.channel, found.supplier) == ("brand seed", "acme.brand")
    assert channel_of(tmp_path, Path("some/yours.txt"), emitted=frozenset()) is None


def test_the_builtin_ladder_answers_as_before(tmp_path: Path) -> None:
    from livery.workshop._provenance import classify

    nothing: frozenset[str] = frozenset()
    assert classify(tmp_path, Path("uv.lock"), emitted=nothing).channel == "toolchain"
    assert (
        classify(tmp_path, Path("packages/x/workshop.toml"), emitted=nothing).channel
        == "contract"
    )
    assert (
        classify(
            tmp_path, Path("packages/x/src/livery/x/a.py"), emitted=nothing
        ).channel
        == "yours"
    )
    assert classify(tmp_path, Path("elsewhere.txt"), emitted=nothing).channel == "yours"
    assert classify(tmp_path, Path("CLAUDE.md"), emitted=nothing).channel == "sync stub"


def test_explain_prints_category_channel_supplier_and_claims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _provenance

    member = _member(tmp_path, "")
    (member / "docs" / "examples").mkdir(parents=True)
    (member / "docs" / "examples" / "first.py").write_text("x = 1\n")
    monkeypatch.setattr(_provenance, "emitted_paths", lambda root: frozenset())
    lines = _provenance.describe(tmp_path, Path("packages/x/docs/examples/first.py"))
    assert lines[0] == "  packages/x/docs/examples/first.py"
    assert lines[1] == "    category: example (livery.workshop)"
    assert lines[2] == "    channel: yours (livery.workshop)"
    assert "    claimed by: format, lint, site" in lines
    note = _provenance.describe(tmp_path, Path("notes/musings.md"))
    assert note[1] == "    category: notes (livery.workshop)"
    assert not any(line.startswith("    claimed by") for line in note)


def test_site_reads_the_docs_and_the_readme_and_not_the_notes(tmp_path: Path) -> None:
    from livery.workshop._docs import site_reads

    packages = (
        Package(tmp_path / "packages" / "x", "packages/x", "livery-x", "python", ()),
    )
    read = [
        "packages/x/docs/index.md",
        "packages/x/docs/examples/first.py",
        "packages/x/docs/assets/site.css",
        "docs/index.md",
        "zensical.toml",
        "README.md",
    ]
    unread = [
        "notes/musings.md",
        "packages/x/src/livery/x/a.py",
        "packages/x/workshop.toml",
    ]
    assert all(site_reads(tmp_path, packages, path) for path in read)
    assert not any(site_reads(tmp_path, packages, path) for path in unread)


def test_the_docs_job_skips_a_notes_only_change_and_builds_otherwise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._docs import unread_by_the_site

    changed: list[str] = ["notes/musings.md"]

    class FakeGit:
        def __init__(self, root: Path) -> None:
            del root

        def fetch(self) -> None:
            return None

        def changed_paths(self, base: str) -> list[str]:
            del base
            return list(changed)

    monkeypatch.setattr("livery.workshop._git_ops.GitOps", FakeGit)
    monkeypatch.setattr(
        "livery.workshop._state.run_context", lambda environ=None: object()
    )
    # Outside a pull request's narrowing the build always runs.
    monkeypatch.setattr(
        "livery.workshop._quality.ci_affected_base", lambda root, run: ""
    )
    assert unread_by_the_site(tmp_path) == ""
    monkeypatch.setattr(
        "livery.workshop._quality.ci_affected_base", lambda root, run: "main"
    )
    assert unread_by_the_site(tmp_path) == (
        "  docs: nothing the site reads changed against origin/main (1 path(s)"
        " changed); the build is skipped"
    )
    changed.append("docs/index.md")
    assert unread_by_the_site(tmp_path) == ""
