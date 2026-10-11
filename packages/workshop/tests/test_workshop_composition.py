"""Package composition: the refusals first, then a set's order, list and name."""

from __future__ import annotations

from collections.abc import Callable
from importlib.metadata import EntryPoint, distributions
from pathlib import Path

import pytest

from livery.footman import (
    Failed,
    _entries,  # pyright: ignore[reportPrivateUsage]
)
from livery.workshop import _checks, _extensions
from livery.workshop._composition import (
    OrderCycle,
    canonical,
    combination,
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
) -> Declaration:
    return Declaration(
        name, name, Path(name) / "extension.toml", levels=levels, requires=requires
    )


def _lookup(*declared: Declaration) -> Callable[[str], Declaration | None]:
    return {item.extension: item for item in declared}.get


@pytest.fixture
def restored_checks():
    state = _checks.snapshot()
    yield
    _checks.restore(state)


# The refusals first.


@pytest.mark.parametrize("key", ["compatible", "before", "after"])
def test_an_extension_declaring_compatible_before_or_after_refuses_naming_requires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    fake_extensions(tmp_path, monkeypatch, odd=PACKAGE + f'{key} = ["acme.other"]\n')
    with pytest.raises(DeclarationError) as raised:
        _extensions.declaration("acme.odd")
    path = tmp_path / "site" / "acme" / "odd" / "extension.toml"
    assert str(raised.value) == (
        f"{path}: extension.{key} is not a key: a package's set is its list and"
        " what the list requires, so composition is requires alone; drop it, and"
        " name in requires what this extension cannot work without"
    )


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


def test_an_order_with_no_start_refuses_naming_its_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookup = _lookup(
        _declared("a", requires=("b",)),
        _declared("b", requires=("a",)),
        _declared("c", requires=("a",)),
    )
    with pytest.raises(OrderCycle) as raised:
        order(("c", "b", "a"), lookup)
    assert raised.value.members == ("a", "b")
    assert str(raised.value) == (
        "the extensions' declared order has no start: a before b before a;"
        " remove one requires among them"
    )
    # The layering check names it per package, and never twice: a set is
    # closed under requires, so a package listing one member holds both.
    fake_extensions(
        tmp_path,
        monkeypatch,
        a=PACKAGE + 'requires = ["acme.b"]\n',
        b=PACKAGE + 'requires = ["acme.a"]\n',
    )
    root_contract(tmp_path, "[]")
    package_contract(tmp_path, "core", '["acme.a"]')
    assert _extensions.closure_problems(tmp_path) == [
        "packages/core: the extensions' declared order has no start: acme.a"
        " before acme.b before acme.a; remove one requires among them"
    ]
    # The mount still finds an order, so the sync that repairs it runs, and
    # the fix leaves the list to the person who removes the cycle.
    assert _extensions.package_extensions(tmp_path) == ("acme.a", "acme.b")
    assert _extensions.write_extensions(tmp_path) == []


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


def test_a_requirement_missing_from_the_wheels_metadata_fails_the_kit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = tmp_path / "site"
    package = site / "acme_kit_meta"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "extension.toml").write_text(PACKAGE + 'requires = ["acme.base"]\n')
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
        "requirements-in-metadata: extension acme_kit_meta: declares requires, and"
        " no installed distribution declares it in workshop.extensions, so its"
        " wheel's metadata cannot be read"
    ]
    monkeypatch.setattr(_extensions, "_declared", lambda: {**previous, "meta": entry})
    assert [str(item) for item in clause.judge(Subject("acme_kit_meta"))] == [
        "requirements-in-metadata: extension acme_kit_meta: requires acme.base, and"
        " its wheel does not depend on acme-base; a requires range is a dependency"
        " of the wheel, so the resolver holds the two to it",
    ]
    metadata.write_text(
        "Metadata-Version: 2.4\nName: acme-kit-meta\nVersion: 0.1\n"
        "Requires-Dist: acme-base>=0.1\n"
    )
    assert clause.judge(Subject("acme_kit_meta")) == []
    # A target the extension's own wheel ships needs no dependency on it.
    metadata.write_text("Metadata-Version: 2.4\nName: acme-base\nVersion: 0.1\n")
    assert clause.judge(Subject("acme_kit_meta")) == []


# What a package's set is.


def test_a_set_is_the_list_and_the_package_level_extensions_it_requires() -> None:
    lookup = _lookup(
        _declared("nanobind", requires=("python", "site")),
        _declared("python"),
        _declared("site", levels=("workspace",)),
    )
    assert package_set(("nanobind",), lookup) == ("nanobind", "python")
    # A requirement pulls in whatever it requires in turn.
    lookup = _lookup(
        _declared("a", requires=("b",)), _declared("b", requires=("c",)), _declared("c")
    )
    assert package_set(("a",), lookup) == ("a", "b", "c")


def test_the_order_puts_requirements_first_and_ties_alphabetically() -> None:
    lookup = _lookup(
        _declared("nanobind", requires=("python", "cmake")),
        _declared("python"),
        _declared("cmake"),
        _declared("conan", requires=("cmake",)),
    )
    assert order(("python", "nanobind", "cmake", "conan"), lookup) == (
        "cmake",
        "conan",
        "python",
        "nanobind",
    )
    # A phase orders by its keys, and not by requires.
    assert order(
        ("python", "nanobind"), lookup, requires=False, edges=[("nanobind", "python")]
    ) == ("nanobind", "python")


def test_a_name_nothing_declares_orders_nothing() -> None:
    lookup = _lookup(_declared("a", requires=("b",)), _declared("b"))
    assert order(("ghost", "a"), lookup) == ("a", "ghost")
    # A requirement and the edges name extensions outside the set: no order.
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
        _declared("nanobind", requires=("python",)),
        _declared("python"),
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
        cpp=PACKAGE,
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
        " extension after what it requires, ties alphabetical; the gate's --fix"
        " rewrites it",
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
        return f'{identity}\n[phases.lint.{tool}]\nmain = "{{package}}._checks:noop"\n'

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


def test_outside_a_workspace_the_stack_is_the_base_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_package_extensions(tmp_path, monkeypatch)
    assert _extensions.describe_extensions(tmp_path) == [
        f"  {_extensions.SELF} (the base)"
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
