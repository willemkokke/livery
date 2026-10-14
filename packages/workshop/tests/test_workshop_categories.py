"""The category tables and the channel rules: refusals first, then their answers."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery import git
from livery.workshop import _categories
from livery.workshop._categories import (
    BASE_TABLE,
    WORKSPACE,
    CategoryError,
    CategoryRule,
    ChannelRule,
    Provenance,
    category_of,
    channel_of,
    known_categories,
    register_channels,
    unregister_channels,
)
from livery.workshop._packages import Package, discover_packages

# The site's jobs are the docs extension's, added as the mount adds them.
from workshop_docs_declared import docs_jobs  # noqa: F401
from workshop_python_steps import python_steps_fixture  # noqa: F401


def _package(
    extensions: tuple[str, ...] = ("python",),
    categories: tuple[tuple[str, tuple[str, ...]], ...] = (),
) -> Package:
    return Package(
        Path("packages/x"),
        "packages/x",
        "livery-x",
        (),
        categories=categories,
        extensions=extensions,
    )


def _member(root: Path, contract: str) -> Path:
    """A python member with *contract*'s lines after its list and name."""
    member = root / "packages" / "x"
    member.mkdir(parents=True, exist_ok=True)
    member.joinpath("workshop.toml").write_text(
        f'extensions = ["python"]\nname = "livery-x"\n{contract}'
    )
    member.joinpath("pyproject.toml").write_text('[project]\nname = "livery-x"\n')
    return member


#: A package-level extension's declaration, its categories to follow.
PACKAGE_LEVEL = '[extension]\nlevels = ["package"]\n'


@pytest.fixture
def brand_extension():
    yield
    unregister_channels(extension="acme.brand")


# The refusals first.


def test_two_rules_of_one_specificity_claiming_one_path_refuse_naming_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from workshop_extension_fakes import fake_package_extensions

    fake_package_extensions(
        tmp_path,
        monkeypatch,
        brand=PACKAGE_LEVEL
        + '\n[categories]\nvendored = ["src/**/*_vendor.py"]\n'
        + 'generated = ["src/**/*_vendor.py"]\n',
    )
    package = replace(_package(), extensions=("acme.brand",))
    with pytest.raises(CategoryError, match="two rules of one specificity") as caught:
        category_of(package, "src/livery/x/lib_vendor.py")
    assert "'src/**/*_vendor.py' as vendored (acme.brand)" in str(caught.value)
    assert "'src/**/*_vendor.py' as generated (acme.brand)" in str(caught.value)


def test_a_categories_value_off_the_shape_refuses_naming_it(tmp_path: Path) -> None:
    _member(tmp_path, '[categories]\nvendored = "docs/vendor/**"\n')
    with pytest.raises(
        BaseException,
        match=r"categories.vendored is a string .*; it takes a list of strings",
    ):
        discover_packages(tmp_path)
    _member(tmp_path, "categories = 3\n")
    with pytest.raises(BaseException, match=r"categories is an integer \(3\)"):
        discover_packages(tmp_path)


def test_a_declared_categories_value_off_the_shape_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._declaration import DeclarationError
    from livery.workshop._extensions import installed_declaration
    from workshop_extension_fakes import fake_package_extensions

    fake_package_extensions(
        tmp_path,
        monkeypatch,
        lua='[extension]\nlevels = ["package"]\n\n[categories]\nsource = "lua/**"\n',
    )
    with pytest.raises(DeclarationError, match="categories"):
        installed_declaration("acme.lua")


def test_a_channels_table_in_a_package_refuses(tmp_path: Path) -> None:
    _member(tmp_path, '[channels]\nrendered = ["x"]\n')
    with pytest.raises(BaseException, match="the top level has no key 'channels'"):
        discover_packages(tmp_path)


def test_two_channel_rules_of_one_rank_refuse_naming_both(
    tmp_path: Path, brand_extension
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


# The fallbacks.


def test_a_package_whose_extensions_declare_no_categories_takes_the_base_s_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from workshop_extension_fakes import fake_extensions

    fake_extensions(tmp_path, monkeypatch, lua=PACKAGE_LEVEL)
    # The package lists an extension that declares no categories: no
    # table but the base's answers.
    package = replace(_package(), extensions=("acme.lua",))

    def found(package: Package, path: str) -> tuple[str, str]:
        category = category_of(package, path)
        return category.name, category.supplier

    assert found(package, "src/acme/x.py") == ("configuration", "livery.workshop")
    assert found(package, "tests/test_x.py") == ("configuration", "livery.workshop")
    assert found(package, "docs/index.md") == ("prose", "livery.workshop")
    # Listing nothing, it holds no table but the base's either.
    assert found(_package(()), "src/acme/x.py") == ("configuration", "livery.workshop")
    # Listing python, python's table answers.
    assert found(_package(), "src/acme/x.py") == ("source", "python")


def test_a_set_whose_declared_order_has_no_start_reads_its_tables_alphabetically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from workshop_extension_fakes import fake_package_extensions

    # Each requires the other, which layering.graph names; the categories
    # still answer, so the sync that lets a person remove the cycle runs.
    fake_package_extensions(
        tmp_path,
        monkeypatch,
        a=PACKAGE_LEVEL
        + 'requires = ["acme.b"]\n\n[categories]\ngenerated = ["gen/**"]\n',
        b=PACKAGE_LEVEL
        + 'requires = ["acme.a"]\n\n[categories]\nsource = ["gen/**"]\n',
    )
    found = category_of(replace(_package(), extensions=("acme.a",)), "gen/a")
    assert (found.name, found.supplier) == ("source", "acme.b")


# Then what the tables answer, and who supplied it.


def test_the_known_categories_are_the_four_builtin_and_the_base_s() -> None:
    assert known_categories() == {
        "source",
        "test",
        "test-support",
        "configuration",
        "prose",
        "nav",
        "asset",
        "example",
        "generated",
        "notes",
        "site",
        "readme",
    }


def test_a_extensions_rule_answers_with_its_name_and_the_more_specific_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from workshop_extension_fakes import fake_extensions

    fake_extensions(
        tmp_path,
        monkeypatch,
        brand=PACKAGE_LEVEL
        + '\n[categories]\nvendored = ["src/livery/x/_vendor/**"]\n',
    )
    # Its pattern is more specific than python's src/**, whatever the
    # order of the two tables.
    package = replace(_package(), extensions=("python", "acme.brand"))
    found = category_of(package, "src/livery/x/_vendor/lib.py")
    assert (found.name, found.supplier) == ("vendored", "acme.brand")
    assert category_of(package, "src/livery/x/mod.py").name == "source"


def test_the_package_exception_wins_over_its_extensions_rules() -> None:
    package = _package(categories=(("vendored", ("docs/assets/vendor/**",)),))
    found = category_of(package, "docs/assets/vendor/codemirror.js")
    assert (found.name, found.supplier) == ("vendored", "the package")
    assert category_of(package, "docs/assets/site.css").name == "asset"


def test_an_extensions_declared_categories_answer_for_a_package_listing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from workshop_extension_fakes import fake_package_extensions

    fake_package_extensions(
        tmp_path,
        monkeypatch,
        lua=(
            '[extension]\nlevels = ["package"]\n\n[categories]\n'
            'source = ["lua/**"]\ntest = ["spec/**"]\n'
        ),
    )
    package = replace(_package(), extensions=("acme.lua",))
    found = category_of(package, "lua/init.lua")
    assert (found.name, found.supplier) == ("source", "acme.lua")
    assert category_of(package, "spec/init_spec.lua").name == "test"
    # The base's table still answers; python's, which the package does
    # not hold, does not.
    assert category_of(package, "docs/index.md").name == "prose"
    assert category_of(package, "src/acme/x.py").name == "configuration"
    # A package's own table wins over its extensions'.
    own = replace(package, categories=(("vendored", ("lua/vendor/**",)),))
    assert category_of(own, "lua/vendor/json.lua").name == "vendored"


def test_a_package_takes_the_table_of_each_extension_it_holds() -> None:
    from dataclasses import replace

    # A nanobind package lists nanobind, which requires python, cpp and
    # conan.
    package = replace(_package(), extensions=("nanobind",))
    assert category_of(package, "src/acme/ext.cpp").name == "source"
    assert category_of(package, "include/acme/ext.hpp").supplier == "cpp"
    assert category_of(package, "tests/test_ext.cpp").supplier == "cpp"
    assert category_of(package, "tests/test_ext.py").name == "test"
    assert category_of(package, "docs/examples/first.py").name == "example"
    assert category_of(package, "docs/index.md").name == "prose"
    assert category_of(package, "docs/nav.toml").name == "nav"
    assert category_of(package, "docs/_generated/api.md").name == "generated"


def test_between_two_tables_the_extension_later_in_composition_order_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from workshop_extension_fakes import fake_package_extensions

    def declares(category: str, requires: str = "") -> str:
        return (
            PACKAGE_LEVEL
            + (f'requires = ["{requires}"]\n' if requires else "")
            + f'\n[categories]\n{category} = ["gen/**"]\n'
        )

    fake_package_extensions(
        tmp_path,
        monkeypatch,
        early=declares("generated"),
        late=declares("source"),
        app=declares("source", requires="acme.lib"),
        lib=declares("generated"),
    )
    # The base's table comes last, whatever it claims at one specificity.
    asset = CategoryRule(BASE_TABLE, "gen/**", "asset", "livery.workshop")
    monkeypatch.setattr(_categories, "BASE_RULES", (asset, *_categories.BASE_RULES))

    def found(*extensions: str) -> tuple[str, str]:
        category = category_of(replace(_package(), extensions=extensions), "gen/a")
        return category.name, category.supplier

    # Of two that do not require one another, the later alphabetically wins.
    assert found("acme.early", "acme.late") == ("source", "acme.late")
    assert found("acme.late", "acme.early") == ("source", "acme.late")
    # An extension comes after the one it requires, so app wins over lib.
    assert found("acme.app") == ("source", "acme.app")
    assert found("acme.lib") == ("generated", "acme.lib")


def test_the_workspace_unit_answers_for_the_roots_own_files(tmp_path: Path) -> None:
    from livery.workshop._packages import is_workspace_unit, workspace_suite

    expected = {
        "tests/test_a.py": "test",
        "tests/helpers.py": "test-support",
        "notes/musings.md": "notes",
        "docs/index.md": "site",
        "zensical.toml": "site",
        "README.md": "readme",
        "pyproject.toml": "configuration",
    }
    # The root of a workspace without tests is its unit, and so are the
    # tests once they exist: each takes the workspace's table.
    root = Package(tmp_path, ".", "workspace", ())
    (tmp_path / "tests").mkdir()
    suite = workspace_suite(tmp_path)
    assert suite is not None
    for unit in (root, suite):
        assert is_workspace_unit(unit)
        assert {path: category_of(unit, path).name for path in expected} == expected
    # A member is no unit of the workspace, and its extensions' tables answer.
    assert not is_workspace_unit(_package())
    assert category_of(_package(), "notes/musings.md").name == "configuration"


def test_a_extensions_channel_rule_answers_at_its_rank(
    tmp_path: Path, brand_extension
) -> None:
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
    # No listed extension writes CLAUDE.md here: a file there is yours.
    assert classify(tmp_path, Path("CLAUDE.md"), emitted=nothing).channel == "yours"


def test_explain_prints_category_channel_supplier_and_claims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, python_steps: object
) -> None:
    from livery.workshop import _provenance

    member = _member(tmp_path, "")
    (tmp_path / "workshop.toml").write_text('[workspace]\nextensions = ["docs"]\n')
    (member / "docs" / "examples").mkdir(parents=True)
    (member / "docs" / "examples" / "first.py").write_text("x = 1\n")
    monkeypatch.setattr(_provenance, "emitted_paths", lambda root: frozenset())
    lines = _provenance.describe(tmp_path, Path("packages/x/docs/examples/first.py"))
    assert lines[0] == "  packages/x/docs/examples/first.py"
    assert lines[1] == "    category: example (livery.workshop)"
    assert lines[2] == "    channel: yours (livery.workshop)"
    assert "    claimed by: lint.fake, gate/docs" in lines
    note = _provenance.describe(tmp_path, Path("notes/musings.md"))
    assert note[1] == "    category: notes (livery.workshop)"
    assert not any(line.startswith("    claimed by") for line in note)


def test_explain_names_the_schema_a_contract_is_judged_by(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _provenance

    _member(tmp_path, "")
    (tmp_path / "workshop.toml").write_text('[workspace]\nextensions = ["docs"]\n')
    monkeypatch.setattr(_provenance, "emitted_paths", lambda root: frozenset())

    def schema(path: str) -> list[str]:
        lines = _provenance.describe(tmp_path, Path(path))
        return [line for line in lines if line.startswith("    schema:")]

    # The fallback first: a file that is no contract names no schema.
    assert schema("notes/musings.md") == []
    assert schema("packages/x/pyproject.toml") == []
    # The member lists python, so python's keys are judged beside the
    # listed extension's.
    assert schema("workshop.toml") == [
        "    schema: .workshop/schema/workshop.json"
        " (livery.workshop, livery.toolroom.store, python, docs)"
    ]
    assert schema("packages/x/workshop.toml") == [
        "    schema: .workshop/schema/package.json"
        " (livery.workshop, livery.toolroom.store, python, docs)"
    ]
    # An extension's file holds the base's and the store's keys alone, whatever
    # the root lists.
    assert schema("packages/x/src/livery/x/extension.toml") == [
        "    schema: .workshop/schema/extension.json"
        " (livery.workshop, livery.toolroom.store)"
    ]
    (tmp_path / "workshop.toml").write_text("[workspace]\nextensions = []\n")
    assert schema("workshop.toml") == [
        "    schema: .workshop/schema/workshop.json"
        " (livery.workshop, livery.toolroom.store, python)"
    ]


def test_the_docs_job_reads_the_docs_and_the_readme_and_not_the_notes(
    tmp_path: Path,
) -> None:
    from livery.workshop._points import jobs_reading

    (tmp_path / "workshop.toml").write_text('[workspace]\nextensions = ["docs"]\n')
    read = [
        "packages/x/docs/index.md",
        "packages/x/docs/nav.toml",
        "packages/x/docs/examples/first.py",
        "packages/x/docs/assets/site.css",
        "packages/x/docs/_generated/api.md",
        "packages/group/y/docs/index.md",
        "docs/index.md",
        "zensical.toml",
        "README.md",
    ]
    unread = [
        "notes/musings.md",
        "packages/x/src/livery/x/a.py",
        "packages/x/workshop.toml",
        "packages/x/docs/examples/data.json",
    ]
    assert [jobs_reading(tmp_path, path) for path in read] == [("gate/docs",)] * len(
        read
    )
    assert [jobs_reading(tmp_path, path) for path in unread] == [()] * len(unread)


def test_ci_changes_is_the_runs_diff_against_its_base_or_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop import ci_changes

    broken: list[str] = []

    def fetch(root: Path) -> None:
        if broken:
            raise git.GitError(broken[0])

    def changed_paths(root: Path, base: str) -> list[str]:
        return [f"changed-against-{base}.md"]

    monkeypatch.setattr(git, "fetch", fetch)
    monkeypatch.setattr(git, "changed_paths", changed_paths)
    monkeypatch.setattr(git, "merge_base", lambda root, base: "b" * 40)
    # The fallbacks first: at a desk, and outside a pull request's
    # narrowing, the run judges everything.
    monkeypatch.setattr(
        "livery.workshop._ci_context.run_context", lambda environ=None: None
    )
    assert ci_changes(tmp_path) is None
    monkeypatch.setattr(
        "livery.workshop._ci_context.run_context", lambda environ=None: object()
    )
    monkeypatch.setattr(
        "livery.workshop._quality.ci_affected_base", lambda root, run: ""
    )
    assert ci_changes(tmp_path) is None
    monkeypatch.setattr(
        "livery.workshop._quality.ci_affected_base", lambda root, run: "main"
    )
    # A diff git cannot read judges everything, and says why.
    broken.append("no network")
    assert ci_changes(tmp_path) is None
    assert (
        "affected-legs: no diff against origin/main; everything is judged (no network)"
        in capsys.readouterr().out
    )
    broken.clear()
    # Then the run's changes, from the merge base a widen reference reads.
    changes = ci_changes(tmp_path)
    assert changes is not None
    assert changes.paths == ("changed-against-main.md",)
    assert changes.before == "b" * 40 and changes.root == tmp_path


def test_the_docs_job_skips_a_change_it_reads_nothing_of_and_runs_otherwise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop import _points

    (tmp_path / "workshop.toml").write_text('[workspace]\nextensions = ["docs"]\n')
    changed: list[str] = ["notes/musings.md"]
    broken: list[str] = []

    def fetch(root: Path) -> None:
        if broken:
            raise git.GitError(broken[0])

    def changed_paths(root: Path, base: str) -> list[str]:
        del base
        return list(changed)

    monkeypatch.setattr(git, "fetch", fetch)
    monkeypatch.setattr(git, "changed_paths", changed_paths)
    monkeypatch.setattr(git, "merge_base", lambda root, base: "b" * 40)
    monkeypatch.setattr(
        "livery.workshop._ci_context.run_context", lambda environ=None: object()
    )
    # The fallbacks first. Outside a pull request's narrowing every
    # entry runs.
    monkeypatch.setattr(
        "livery.workshop._quality.ci_affected_base", lambda root, run: ""
    )
    assert _points.unread_by_the_job(tmp_path, "gate", "docs") == ""
    monkeypatch.setattr(
        "livery.workshop._quality.ci_affected_base", lambda root, run: "main"
    )
    # A diff that cannot be read runs the job and says why.
    broken.append("no network")
    assert _points.unread_by_the_job(tmp_path, "gate", "docs") == ""
    assert (
        "affected-legs: no diff against origin/main; everything is judged"
        in capsys.readouterr().out
    )
    broken.clear()
    # A job that declares no inputs always runs.
    assert _points.unread_by_the_job(tmp_path, "merge", "govern") == ""
    # A change to the sources of the extension that contributed the job
    # runs it, though it reads none of them.
    monkeypatch.setattr(
        "livery.workshop._extensions.extension_provider",
        lambda extension, packages: "packages/site" if extension == "docs" else "",
    )
    changed[:] = ["packages/site/src/acme_site/pages.py"]
    assert _points.unread_by_the_job(tmp_path, "gate", "docs") == ""
    changed[:] = ["notes/musings.md"]
    assert _points.unread_by_the_job(tmp_path, "gate", "docs") == (
        "  gate/docs: nothing the job reads changed in this run (1 path(s)"
        " changed); its entries are skipped"
    )
    changed.append("docs/index.md")
    assert _points.unread_by_the_job(tmp_path, "gate", "docs") == ""
