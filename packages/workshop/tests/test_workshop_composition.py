"""Package composition: the refusals first, then a set's order, list and name."""

from __future__ import annotations

from collections.abc import Callable
from importlib.metadata import EntryPoint, distributions
from pathlib import Path
from typing import Any

import pytest

from livery.footman import (
    Failed,
    _entries,  # pyright: ignore[reportPrivateUsage]
)
from livery.workshop import _checks, _extensions, _tasks, _templates
from livery.workshop._composition import (
    OrderCycle,
    canonical,
    combination,
    combinations,
    connected,
    order,
    package_set,
)
from livery.workshop._contract import load_contract
from livery.workshop._declaration import Declaration, DeclarationError
from livery.workshop.testing import CLAUSES, Subject
from workshop_extension_fakes import (
    fake_extensions,
    fake_package_extensions,
    package_contract,
    root_contract,
)

#: A package-level extension's identity, the start of most fakes here.
PACKAGE = '[extension]\nlevels = ["package"]\n'


def _declared(
    name: str,
    *,
    levels: tuple[str, ...] = ("package",),
    requires: tuple[str, ...] = (),
    compatible: tuple[str, ...] = (),
    before: tuple[str, ...] = (),
    after: tuple[str, ...] = (),
    contributes: tuple[str, ...] = (),
) -> Declaration:
    tables: dict[str, dict[str, Any]] = {target: {} for target in contributes}
    return Declaration(
        name,
        name,
        Path(name) / "extension.toml",
        levels=levels,
        requires=requires,
        compatible=compatible,
        before=before,
        after=after,
        target_tables=tables,
    )


def _lookup(*declared: Declaration) -> Callable[[str], Declaration | None]:
    return {item.extension: item for item in declared}.get


@pytest.fixture
def restored_checks():
    state = _checks.snapshot()
    yield
    _checks.restore(state)


# The refusals first.


def test_an_unconnected_pair_refuses_naming_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(tmp_path, monkeypatch, cpp=PACKAGE, python=PACKAGE)
    root_contract(tmp_path, "[]")
    package_contract(tmp_path, "core", '["acme.cpp", "acme.python"]')
    assert _extensions.closure_problems(tmp_path) == [
        "packages/core: `extensions` composes acme.cpp and acme.python, and"
        " neither requires the other, contributes to it, nor declares it"
        " compatible; list only one of them, or ask either extension to declare"
        " the other compatible"
    ]
    # A claim from either side connects the two, and so does a contribution.
    fake_extensions(
        tmp_path,
        monkeypatch,
        cpp=PACKAGE,
        python=PACKAGE + 'compatible = ["acme.cpp"]\n',
    )
    assert _extensions.closure_problems(tmp_path) == []
    fake_extensions(
        tmp_path, monkeypatch, cpp=PACKAGE + '[for."acme.python"]\n', python=PACKAGE
    )
    assert _extensions.closure_problems(tmp_path) == []


def test_a_requirement_no_list_holds_at_its_level_refuses_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(
        tmp_path,
        monkeypatch,
        native=PACKAGE + 'requires = ["acme.site", "acme.absent"]\n',
        site="",
        runner='[extension]\nrequires = ["acme.python"]\n',
        python=PACKAGE,
    )
    root_contract(tmp_path, '["acme.runner"]')
    package_contract(tmp_path, "core", '["acme.native"]')
    assert _extensions.closure_problems(tmp_path) == [
        "[workspace] extensions lists acme.runner, which requires acme.python, a"
        " package-level extension no package lists; list acme.python in the"
        " `extensions` of each package acme.runner serves",
        "packages/core: acme.native requires acme.site, which [workspace]"
        " extensions does not list; the gate's --fix adds it",
        "packages/core: acme.native requires acme.absent, which no installed"
        " distribution declares in workshop.extensions; install it",
    ]
    # The fix lists the workspace's own requirement; a package's set is a
    # person's choice, and an absent distribution an install.
    assert _extensions.write_extensions(tmp_path) == [
        "  layering: [workspace] extensions gains acme.site, which acme.native in"
        " packages/core requires"
    ]
    package_contract(tmp_path, "app", '["acme.python"]')
    assert _extensions.closure_problems(tmp_path) == [
        "packages/core: acme.native requires acme.absent, which no installed"
        " distribution declares in workshop.extensions; install it",
    ]


@pytest.mark.parametrize(
    ("declared", "refusal"),
    [
        (
            '[extension]\ncompatible = ["acme.other"]\n',
            "extension.compatible composes package-level extensions on one package,"
            " and this extension's levels are workspace; add 'package' to levels,"
            " or drop compatible",
        ),
        (
            PACKAGE + 'before = ["acme.other"]\n',
            "extension.before names acme.other, which this extension neither"
            " requires, declares compatible, nor contributes to; it orders itself"
            " only against extensions it knows",
        ),
        (
            PACKAGE + 'compatible = ["acme.other"]\nbefore = ["acme.other"]\n'
            'after = ["acme.other"]\n',
            "extension.after names acme.other, which before names too; an extension"
            " runs either before another or after it",
        ),
        (
            PACKAGE + 'compatible = ["acme.odd"]\n',
            "extension.compatible names acme.odd, this extension itself; it names"
            " the others",
        ),
        (
            PACKAGE + '\n[options]\ndeep = "judges deeper"\n',
            "options is turned on by a [workspace] extensions entry, and this"
            " extension's levels are package; a package's entry is the"
            " extension's name alone",
        ),
    ],
    ids=["workspace-level", "unknown", "both", "itself", "options"],
)
def test_a_composition_the_file_alone_shows_wrong_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, declared: str, refusal: str
) -> None:
    fake_extensions(tmp_path, monkeypatch, odd=declared)
    with pytest.raises(DeclarationError) as raised:
        _extensions.declaration("acme.odd")
    path = tmp_path / "site" / "acme" / "odd" / "extension.toml"
    assert str(raised.value) == f"{path}: {refusal}"


def test_an_order_with_no_start_refuses_naming_its_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookup = _lookup(
        _declared("a", compatible=("b",), after=("b",)),
        _declared("b", compatible=("a",), after=("a",)),
        _declared("c", requires=("a",)),
    )
    with pytest.raises(OrderCycle) as raised:
        order(("c", "b", "a"), lookup)
    assert raised.value.members == ("a", "b")
    assert str(raised.value) == (
        "the extensions' declared order has no start: a before b before a;"
        " remove one requires, before or after among them"
    )
    # The layering check names it per package, and never twice.
    fake_extensions(
        tmp_path,
        monkeypatch,
        a=PACKAGE + 'compatible = ["acme.b"]\nafter = ["acme.b"]\n',
        b=PACKAGE + 'compatible = ["acme.a"]\nafter = ["acme.a"]\n',
    )
    root_contract(tmp_path, "[]")
    package_contract(tmp_path, "core", '["acme.a", "acme.b"]')
    assert _extensions.closure_problems(tmp_path) == [
        "packages/core: the extensions' declared order has no start: acme.a"
        " before acme.b before acme.a; remove one requires, before or after"
        " among them"
    ]
    # The mount still finds an order, so the sync that repairs it runs, and
    # the fix leaves the list to the person who removes the cycle.
    assert _extensions.package_extensions(tmp_path) == ("acme.a", "acme.b")
    assert _extensions.write_extensions(tmp_path) == []


def test_an_order_only_the_packages_together_cycle_is_named_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(
        tmp_path,
        monkeypatch,
        a=PACKAGE + 'compatible = ["acme.b", "acme.c"]\nafter = ["acme.c"]\n',
        b=PACKAGE,
        c=PACKAGE + 'compatible = ["acme.a"]\nafter = ["acme.a"]\n',
    )
    root_contract(tmp_path, "[]")
    package_contract(tmp_path, "core", '["acme.a", "acme.b"]')
    package_contract(tmp_path, "tool", '["acme.c"]')
    assert _extensions.closure_problems(tmp_path) == [
        "the package-level extensions the packages list: the extensions' declared"
        " order has no start: acme.a before acme.c before acme.a; remove one"
        " requires, before or after among them"
    ]


def test_a_package_entry_with_options_or_nothing_installed_refuses_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(tmp_path, monkeypatch, python=PACKAGE)
    root_contract(tmp_path, "[]")
    package_contract(tmp_path, "core", '["acme.python[typed]", "acme.ghost"]')
    assert _extensions.closure_problems(tmp_path) == [
        "packages/core: `extensions` lists 'acme.python[typed]'; a package's entry"
        " is the extension's name alone, and a package-level extension takes no"
        " options",
        "packages/core: `extensions` lists acme.ghost, which no installed"
        " distribution declares in workshop.extensions",
    ]
    # A list the judge refuses for its entries is a person's to fix first.
    assert _extensions.write_extensions(tmp_path) == []


@pytest.mark.parametrize(
    ("contract", "refusal"),
    [
        ('kind = "python\nextensions = [', " not valid TOML: "),
        (
            'kind = "python"\nextensions = "acme.cpp"\n',
            "\n  extensions is a string ('acme.cpp'); it takes a list of strings",
        ),
    ],
    ids=["not-toml", "not-a-list"],
)
def test_a_package_contract_the_mount_cannot_read_lists_nothing_and_the_mount_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, contract: str, refusal: str
) -> None:
    fake_extensions(tmp_path, monkeypatch, cpp=PACKAGE)
    root_contract(tmp_path, "[]")
    package_contract(tmp_path, "app", '["acme.cpp"]')
    broken = package_contract(tmp_path, "core", "[]")
    broken.write_text(contract)
    assert _extensions.package_extensions(tmp_path) == ("acme.cpp",)
    from livery.footman import _registry as registry

    with registry.capture():
        assert _extensions.mount_extensions(tmp_path) == ()
    # The judge names the contract when the package is read; past our own
    # words, a TOML error is the parser's.
    with pytest.raises(Failed) as raised:
        load_contract(broken)
    assert str(raised.value).startswith(f"{broken}:{refusal}")


def test_a_claim_missing_from_the_wheels_metadata_fails_the_kit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = tmp_path / "site"
    package = site / "acme_kit_meta"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "extension.toml").write_text(
        PACKAGE + 'requires = ["acme.base"]\ncompatible = ["acme.python"]\n'
    )
    info = site / "acme_kit_meta-0.1.dist-info"
    info.mkdir()
    (info / "entry_points.txt").write_text(
        "[workshop.extensions]\nmeta = acme_kit_meta\n"
    )
    metadata = info / "METADATA"
    metadata.write_text("Metadata-Version: 2.4\nName: acme-kit-meta\nVersion: 0.1\n")
    # A scan while the site is on the path would find the fake for good:
    # the scan is the one before this test once it ends.
    monkeypatch.setattr(_entries, "_SCAN", _entries._SCAN)  # pyright: ignore[reportPrivateUsage]
    monkeypatch.syspath_prepend(str(site))
    (found,) = distributions(path=[str(site)])
    entry: EntryPoint = found.entry_points["meta"]
    previous = {
        name: item
        for name, item in _extensions._declared().items()  # pyright: ignore[reportPrivateUsage]
        if name != "meta"
    }
    (clause,) = [item for item in CLAUSES if item.name == "requirements-in-metadata"]
    # With no distribution declaring it, there is no metadata to read.
    monkeypatch.setattr(_extensions, "_declared", lambda: previous)
    assert [str(item) for item in clause.judge(Subject("acme_kit_meta"))] == [
        "requirements-in-metadata: extension acme_kit_meta: declares requires,"
        " compatible or [for] targets, and no installed distribution declares it in"
        " workshop.extensions, so its wheel's metadata cannot be read"
    ]
    monkeypatch.setattr(_extensions, "_declared", lambda: {**previous, "meta": entry})
    assert [str(item) for item in clause.judge(Subject("acme_kit_meta"))] == [
        "requirements-in-metadata: extension acme_kit_meta: requires acme.base, and"
        " its wheel does not depend on acme-base; a requires range is a dependency"
        " of the wheel, so the resolver holds the two to it",
        "requirements-in-metadata: extension acme_kit_meta: composes with"
        " acme.python or contributes to it, and its wheel has no extra"
        " 'acme-python' that requires acme-python; a compatibility claim is an"
        " extra of the wheel, which a workspace listing both installs",
    ]
    metadata.write_text(
        "Metadata-Version: 2.4\nName: acme-kit-meta\nVersion: 0.1\n"
        "Requires-Dist: acme-base>=0.1\n"
        'Requires-Dist: acme-python>=1.0; extra == "acme-python"\n'
        "Provides-Extra: acme-python\n"
    )
    assert clause.judge(Subject("acme_kit_meta")) == []


# What a package's set is.


def test_a_set_is_the_list_and_the_package_level_extensions_it_requires() -> None:
    lookup = _lookup(
        _declared("nanobind", requires=("python", "site")),
        _declared("python"),
        _declared("site", levels=("workspace",)),
    )
    assert package_set(("nanobind",), lookup) == ("nanobind", "python")
    # A requirement connects through whatever it requires in turn.
    lookup = _lookup(
        _declared("a", requires=("b",)), _declared("b", requires=("c",)), _declared("c")
    )
    assert connected("a", "c", lookup)
    assert connected("c", "a", lookup)


def test_the_order_puts_requirements_and_after_first_and_ties_alphabetically() -> None:
    lookup = _lookup(
        _declared("nanobind", requires=("python",), after=("cmake",)),
        _declared("python"),
        _declared("cmake", compatible=("nanobind",)),
        _declared("conan", compatible=("cmake",), before=("cmake",)),
    )
    assert order(("python", "nanobind", "cmake", "conan"), lookup) == (
        "conan",
        "cmake",
        "python",
        "nanobind",
    )
    # A phase orders by its keys, before and after, and not by requires.
    assert order(
        ("python", "nanobind"), lookup, requires=False, edges=[("nanobind", "python")]
    ) == ("nanobind", "python")


def test_a_name_nothing_declares_orders_nothing_and_connects_to_nothing() -> None:
    lookup = _lookup(_declared("a", compatible=("b",), before=("b",)), _declared("b"))
    assert not connected("a", "ghost", lookup)
    assert order(("ghost", "a"), lookup) == ("a", "ghost")
    # Before and the edges name extensions outside the set: no order.
    assert order(("a",), lookup, edges=[("a", "zed"), ("zed", "a")]) == ("a",)


def test_a_metadata_line_that_names_no_distribution_is_read_past() -> None:
    from livery.workshop.testing._conformance import (
        _required,  # pyright: ignore[reportPrivateUsage]
    )

    assert _required(['; extra == "x"', 'Acme_Base>=1; extra == "Py.Thon"']) == [
        ("acme-base", frozenset({"py-thon"}))
    ]


def test_the_canonical_list_drops_what_another_requires_and_names_the_set() -> None:
    lookup = _lookup(
        _declared("nanobind", requires=("python",), compatible=("cmake",)),
        _declared("python", compatible=("cmake",)),
        _declared("cmake"),
    )
    assert canonical(("python", "nanobind", "cmake", "python"), lookup) == (
        "cmake",
        "nanobind",
    )
    assert combination(("cmake", "nanobind"), lookup) == "cmake+nanobind"


def test_a_list_out_of_its_canonical_form_is_named_and_the_fix_writes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(
        tmp_path,
        monkeypatch,
        python=PACKAGE,
        nanobind=PACKAGE + 'requires = ["acme.python"]\n',
        cpp=PACKAGE + 'compatible = ["acme.zig"]\nbefore = ["acme.zig"]\n',
        zig=PACKAGE,
    )
    root_contract(tmp_path, "[]")
    core = package_contract(tmp_path, "core", '["acme.python", "acme.nanobind"]')
    tool = package_contract(tmp_path, "tool", '["acme.zig", "acme.cpp"]')
    assert _extensions.closure_problems(tmp_path) == [
        'packages/core: `extensions` is ["acme.python", "acme.nanobind"], and its'
        ' canonical list is ["acme.nanobind"]; acme.python is required by another'
        " listed extension; the gate's --fix rewrites it",
        'packages/tool: `extensions` is ["acme.zig", "acme.cpp"], and its canonical'
        ' list is ["acme.cpp", "acme.zig"]; its order is composition order: each'
        " extension after what it requires and names in after, before what it"
        " names in before, ties alphabetical; the gate's --fix rewrites it",
    ]
    assert _extensions.write_extensions(tmp_path) == [
        '  layering: packages/core `extensions` is ["acme.nanobind"], its canonical'
        " list",
        '  layering: packages/tool `extensions` is ["acme.cpp", "acme.zig"], its'
        " canonical list",
    ]
    assert core.read_text() == 'kind = "python"\nextensions = ["acme.nanobind"]\n'
    assert (
        tool.read_text() == 'kind = "python"\nextensions = ["acme.cpp", "acme.zig"]\n'
    )
    assert _extensions.closure_problems(tmp_path) == []
    assert _extensions.write_extensions(tmp_path) == []
    # A list twice over says so; a list the fix cannot find inline stays
    # named until a person writes it.
    tool.write_text('kind = "python"\nextensions=["acme.cpp", "acme.cpp"]\n')
    assert _extensions.closure_problems(tmp_path) == [
        'packages/tool: `extensions` is ["acme.cpp", "acme.cpp"], and its canonical'
        ' list is ["acme.cpp"]; it lists an extension twice; the gate\'s --fix'
        " rewrites it",
    ]
    assert _extensions.write_extensions(tmp_path) == []


def test_the_package_level_extensions_mount_first_in_composition_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored_checks: None
) -> None:
    def checked(tool: str, identity: str = "") -> str:
        return f'{identity}\n[checks.{tool}.lint]\nrun = "{{package}}._checks:noop"\n'

    fake_extensions(
        tmp_path,
        monkeypatch,
        native=checked("native", PACKAGE + 'requires = ["acme.base"]'),
        base=checked("base", PACKAGE),
        site=checked("site"),
    )
    root_contract(tmp_path, '["acme.site"]')
    package_contract(tmp_path, "core", '["acme.native"]')
    package_contract(tmp_path, "app", '["acme.base"]')
    assert _extensions.workspace_names(tmp_path) == ("acme.site",)
    assert _extensions.package_extensions(tmp_path) == ("acme.base", "acme.native")
    assert _extensions.extension_names(tmp_path) == (
        "acme.base",
        "acme.native",
        "acme.site",
    )
    assert _extensions.stack_names(tmp_path)[0] == _extensions.SELF
    from livery.footman import _registry as registry

    with registry.capture():
        mounted = _extensions.mount_extensions(tmp_path)
    assert mounted == ("acme.base", "acme.native", "acme.site")
    assert _checks.checks_by_name()["lint.native"].extension == "acme.native"
    assert _extensions.describe_extensions(tmp_path)[1:5] == [
        "  acme.base (required by acme.native)",
        "    packages: packages/app, packages/core",
        "  acme.native",
        "    packages: packages/core",
    ]


def test_a_package_level_extensions_keys_belong_where_a_package_lists_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keys = "".join(
        f'\n[contract.{contract}.native.depth]\ntypes = ["str"]\ndoc = "how deep"\n'
        for contract in ("root", "package")
    )
    fake_extensions(tmp_path, monkeypatch, native=PACKAGE + keys)
    root_contract(tmp_path, "[]")
    table = '\n[native]\ndepth = "deep"\n'
    listing = package_contract(tmp_path, "core", '["acme.native"]')
    listing.write_text(listing.read_text() + table)
    assert load_contract(listing)["native"] == {"depth": "deep"}
    other = package_contract(tmp_path, "app", "[]")
    other.write_text(other.read_text() + table)
    with pytest.raises(Failed) as raised:
        load_contract(other)
    assert str(raised.value) == (
        f"{other}:\n  native.depth is a key of acme.native, which this package's"
        " `extensions` does not list; list the extension, or remove the key"
    )
    # In the root's contract, a package that lists it is enough.
    root = tmp_path / "workshop.toml"
    root.write_text(root.read_text() + table)
    assert load_contract(root)["native"] == {"depth": "deep"}
    listing.write_text(listing.read_text().replace('["acme.native"]', "[]"))
    with pytest.raises(Failed) as raised:
        load_contract(root)
    assert str(raised.value) == (
        f"{root}:\n  native.depth is a key of acme.native, which no package's"
        " `extensions` lists; list the extension, or remove the key"
    )


def test_fm_extensions_lists_every_valid_combination_of_what_is_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_package_extensions(
        tmp_path,
        monkeypatch,
        python=PACKAGE,
        nanobind=PACKAGE + 'requires = ["acme.python"]\n',
        cpp=PACKAGE,
        cmake=PACKAGE + 'compatible = ["acme.cpp"]\n',
        site="",
    )
    expected = (
        "acme.cmake",
        "acme.cmake+acme.cpp",
        "acme.cpp",
        "acme.nanobind",
        "acme.python",
    )
    assert _extensions.combination_names() == expected
    _tasks.extensions(combinations=True)
    assert capsys.readouterr().out == "".join(f"  {name}\n" for name in expected)
    # A declaration this workshop cannot take refuses the listing, named.
    fake_package_extensions(
        tmp_path, monkeypatch, odd=PACKAGE + 'before = ["acme.zed"]\n'
    )
    with pytest.raises(Failed) as raised:
        _tasks.extensions(combinations=True)
    assert str(raised.value).startswith(
        f"{tmp_path / 'site' / 'acme' / 'odd' / 'extension.toml'}: extension.before"
    )
    # A set missing what it requires, or with an order with no start, is
    # never a combination.
    lookup = _lookup(
        _declared("a", requires=("absent",)),
        _declared("b", compatible=("c",), after=("c",)),
        _declared("c", compatible=("b",), after=("b",)),
    )
    assert combinations(("a", "b", "c"), lookup) == ("b", "c")


def test_with_no_package_level_extension_installed_the_listing_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_package_extensions(tmp_path, monkeypatch)
    _tasks.extensions(combinations=True)
    assert capsys.readouterr().out == (
        "  no installed extension declares the package level\n"
    )
    # Outside a workspace the stack is the base alone.
    assert _extensions.describe_extensions(tmp_path) == [
        f"  {_extensions.SELF} (the base)"
    ]


def test_a_listed_claim_installs_with_its_extra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(
        tmp_path,
        monkeypatch,
        python=PACKAGE,
        nanobind=PACKAGE + '[for."acme.python"]\n',
    )
    entries = (
        (_extensions.SELF, "livery-workshop"),
        ("acme.python", "acme-python"),
        ("acme.nanobind", "acme-nanobind"),
    )
    lines = _templates._extension_requirements(  # pyright: ignore[reportPrivateUsage]
        entries, {"livery-workshop"}
    )
    assert lines == ["acme-python", "acme-nanobind[acme-python]"]
    alone = _templates._extension_requirements(  # pyright: ignore[reportPrivateUsage]
        entries[::2], {"livery-workshop"}
    )
    assert alone == ["acme-nanobind"]
