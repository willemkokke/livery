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
from livery.workshop import _extensions, _steps
from livery.workshop._composition import (
    OrderCycle,
    canonical,
    closure,
    combination,
    order,
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
from workshop_installed import skip_unless_installed

#: An extension's identity, the start of most fakes here.
PACKAGE = "[extension]\n"


def _composing(name: str, identity: str = PACKAGE) -> str:
    """A fake that composes something in a package: it declares categories."""
    return identity + f'\n[categories]\nsource = ["{name}/**"]\n'


def _declared(name: str, *, requires: tuple[str, ...] = ()) -> Declaration:
    return Declaration(name, name, Path(name) / "extension.toml", requires=requires)


def _lookup(*declared: Declaration) -> Callable[[str], Declaration | None]:
    return {item.extension: item for item in declared}.get


@pytest.fixture
def restored_steps():
    state = _steps.snapshot()
    yield
    _steps.restore(state)


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


def test_a_requirement_joins_the_package_set_whatever_it_declares(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = '[phases.lint.site]\nmain = "{package}._steps:noop"\n'
    fake_extensions(
        tmp_path,
        monkeypatch,
        native=_composing(
            "native", PACKAGE + 'requires = ["acme.site", "acme.absent"]\n'
        ),
        site=site,
        runner='[extension]\nrequires = ["acme.python"]\n',
        python=_composing("python"),
    )
    root_contract(tmp_path, '["acme.runner"]')
    package_contract(tmp_path, "core", '["acme.native"]')
    # The refusal first: a requirement nothing installed declares.
    assert _extensions.closure_problems(tmp_path) == [
        "packages/core: acme.native requires acme.absent, which no installed"
        " distribution declares in workshop.extensions; install it",
    ]
    # A package's set holds acme.site, which composes nothing in a package,
    # whatever it declares, and the root's set holds acme.python, which
    # acme.runner requires: no list names either, and no fix adds one.
    assert _extensions.write_extensions(tmp_path) == []
    assert _extensions.installed_set(tmp_path) == (
        "acme.absent",
        "acme.python",
        "acme.runner",
        "acme.site",
        "acme.native",
    )
    # A root entry's requirement a package's set holds needs no entry.
    root_contract(tmp_path, '["acme.runner"]')
    package_contract(tmp_path, "app", '["acme.python"]')
    assert _extensions.closure_problems(tmp_path) == [
        "packages/core: acme.native requires acme.absent, which no installed"
        " distribution declares in workshop.extensions; install it",
    ]
    assert _extensions.write_extensions(tmp_path) == []
    # The set is the list and what it requires, whatever each declares.
    lookup = _lookup(
        _declared("nanobind", requires=("python", "site")),
        _declared("python"),
        _declared("site"),
    )
    assert closure(("nanobind",), lookup) == ("nanobind", "python", "site")
    # A requirement pulls in whatever it requires in turn.
    lookup = _lookup(
        _declared("a", requires=("b",)), _declared("b", requires=("c",)), _declared("c")
    )
    assert closure(("a",), lookup) == ("a", "b", "c")


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
    # `layering.graph` names it per package, and never twice: a set is
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
    assert _extensions.installed_set(tmp_path) == ("acme.a", "acme.b")
    assert _extensions.write_extensions(tmp_path) == []


def test_a_package_entry_with_options_or_nothing_installed_refuses_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(tmp_path, monkeypatch, python=PACKAGE)
    root_contract(tmp_path, "[]")
    package_contract(tmp_path, "core", '["acme.python[typed]", "acme.ghost"]')
    assert _extensions.closure_problems(tmp_path) == [
        "packages/core: `extensions` lists 'acme.python[typed]'; a package's entry"
        " is the extension's name alone, and takes no options",
        "packages/core: `extensions` lists acme.ghost, which no installed"
        " distribution declares in workshop.extensions",
    ]
    # A list the judge refuses for its entries is a person's to fix first.
    assert _extensions.write_extensions(tmp_path) == []


@pytest.mark.parametrize(
    ("contract", "refusal"),
    [
        ('name = "acme-core\nextensions = [', " not valid TOML: "),
        (
            'extensions = "acme.cpp"\n',
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
    assert _extensions.installed_set(tmp_path) == ("acme.cpp",)
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


def test_the_nearest_extensions_are_the_ones_no_other_requires() -> None:
    from livery.workshop import nearest_extensions

    # The fallbacks first: requires that close a cycle leave none nearest,
    # and a name nothing declares requires nothing.
    cycle = _lookup(_declared("a", requires=("b",)), _declared("b", requires=("a",)))
    assert nearest_extensions(("a", "b"), cycle) == ()
    assert nearest_extensions(("ghost",), cycle) == ("ghost",)
    lookup = _lookup(
        _declared("theme", requires=("brand",)),
        _declared("brand", requires=("base",)),
        _declared("base"),
        _declared("other"),
    )
    # A requirement met through another is not nearest; two that neither
    # requires are both nearest, in the order given, each once.
    assert nearest_extensions(("base", "theme", "brand"), lookup) == ("theme",)
    assert nearest_extensions(("other", "base", "theme", "other"), lookup) == (
        "other",
        "theme",
    )


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
        python=_composing("python"),
        nanobind=PACKAGE + 'requires = ["acme.python"]\n',
        cpp=_composing("cpp"),
        zig=_composing("zig"),
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
    assert core.read_text() == 'extensions = ["acme.nanobind"]\n'
    assert tool.read_text() == 'extensions = ["acme.cpp", "acme.zig"]\n'
    assert _extensions.closure_problems(tmp_path) == []
    assert _extensions.write_extensions(tmp_path) == []
    # A list twice over says so, and the fix writes each extension once;
    # an array the fix does not read stays named until a person writes it.
    twice = [
        'packages/tool: `extensions` is ["acme.cpp", "acme.cpp"], and its canonical'
        ' list is ["acme.cpp"]; it lists an extension twice; the gate\'s --fix'
        " rewrites it",
    ]
    tool.write_text('extensions=["acme.cpp", "acme.cpp"]\n')
    assert _extensions.closure_problems(tmp_path) == twice
    assert _extensions.write_extensions(tmp_path) == [
        '  layering: packages/tool `extensions` is ["acme.cpp"], its canonical list'
    ]
    assert tool.read_text() == 'extensions=["acme.cpp"]\n'
    tool.write_text('extensions = ["""acme.cpp""", "acme.cpp"]\n')
    assert _extensions.closure_problems(tmp_path) == twice
    assert _extensions.write_extensions(tmp_path) == []


def test_the_mount_and_answers_follow_composition_order_whatever_the_list_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored_steps: None
) -> None:
    from livery.footman import _registry as registry
    from livery.workshop import Query, answer

    def checked(tool: str, identity: str = "") -> str:
        return f'{identity}\n[phases.lint.{tool}]\nmain = "{{package}}._steps:noop"\n'

    def styled(name: str) -> str:
        return checked(name) + f'\n[queries."acme.style"]\nvalue = ["{name}"]\n'

    fake_extensions(
        tmp_path,
        monkeypatch,
        native=checked("native", PACKAGE + 'requires = ["acme.base"]'),
        base=checked("base", PACKAGE),
        site=PACKAGE + '[queries."acme.style"]\nlevel = "workspace"\n',
        alpha=styled("alpha"),
        zulu=styled("zulu"),
    )
    package_contract(tmp_path, "core", '["acme.native"]')
    package_contract(tmp_path, "app", '["acme.base"]')
    every = ("acme.alpha", "acme.base", "acme.native", "acme.site", "acme.zulu")
    for listed in (
        '["acme.zulu", "acme.site", "acme.alpha"]',
        '["acme.alpha", "acme.site", "acme.zulu"]',
    ):
        root_contract(tmp_path, listed)
        assert _extensions.extension_names(tmp_path) == every
        assert _extensions.stack_names(tmp_path) == (_extensions.SELF, *every)
        for name in ("lint.alpha", "lint.base", "lint.native", "lint.zulu"):
            if name in _steps.steps_by_name():
                _steps.unregister_step(name)
        with registry.capture():
            mounted = _extensions.mount_extensions(tmp_path)
        # The site declares the query alone: it registers and answers nothing.
        assert mounted == tuple(name for name in every if name != "acme.site")
        assert [name for name in _steps.steps_by_name() if name.startswith("lint.")][
            -4:
        ] == ["lint.alpha", "lint.base", "lint.native", "lint.zulu"]
        assert answer(tmp_path, Query[tuple[str, ...]]("acme.style")) == (
            "alpha",
            "zulu",
        )
    assert _extensions.describe_extensions(tmp_path)[1:6] == [
        "  acme.alpha",
        "  acme.base (required by acme.native)",
        "    packages: packages/app, packages/core",
        "  acme.native",
        "    packages: packages/core",
    ]


def test_a_package_s_set_is_in_composition_order_whatever_its_list_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import discover_packages, extension_set

    fake_extensions(
        tmp_path,
        monkeypatch,
        native=_composing("native", PACKAGE + 'requires = ["acme.zulu"]\n'),
        zulu=_composing("zulu"),
        alpha=_composing("alpha"),
    )
    root_contract(tmp_path, "[]")
    for listed in ('["acme.native", "acme.alpha"]', '["acme.alpha", "acme.native"]'):
        package_contract(tmp_path, "core", listed)
        (core,) = discover_packages(tmp_path)
        # Each after what it requires, ties alphabetical: alpha first, and
        # zulu before native, which requires it.
        assert extension_set(core) == ("acme.alpha", "acme.zulu", "acme.native")


def test_outside_a_workspace_the_stack_is_the_base_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_package_extensions(tmp_path, monkeypatch)
    assert _extensions.describe_extensions(tmp_path) == [
        f"  {_extensions.SELF} (the base)"
    ]


def test_a_package_contract_key_belongs_where_the_root_or_the_package_lists_its_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keys = "".join(
        f'\n[contract.{contract}.native.depth]\ntypes = ["str"]\ndoc = "how deep"\n'
        for contract in ("root", "package")
    )
    fake_extensions(
        tmp_path,
        monkeypatch,
        native=PACKAGE + keys,
        wrapper=PACKAGE + 'requires = ["acme.native"]\n',
    )
    root_contract(tmp_path, "[]")
    table = '\n[native]\ndepth = "deep"\n'
    # The refusals first: a package whose set does not hold the owner,
    # while the root's does not either, and the root while no list does.
    other = package_contract(tmp_path, "app", "[]")
    other.write_text(other.read_text() + table)
    with pytest.raises(Failed) as raised:
        load_contract(other)
    assert str(raised.value) == (
        f"{other}:\n  native.depth is a key of acme.native, which neither"
        " [workspace] extensions nor this package's `extensions` lists; list the"
        " extension, or remove the key"
    )
    root = tmp_path / "workshop.toml"
    root.write_text(root.read_text() + table)
    with pytest.raises(Failed) as raised:
        load_contract(root)
    assert str(raised.value) == (
        f"{root}:\n  native.depth is a key of acme.native, which neither"
        " [workspace] extensions nor a package's `extensions` lists; list the"
        " extension, or remove the key"
    )
    # A package whose set holds it, through what its list requires: its
    # table loads, and so does the root's.
    listing = package_contract(tmp_path, "core", '["acme.wrapper"]')
    listing.write_text(listing.read_text() + table)
    assert load_contract(listing)["native"] == {"depth": "deep"}
    assert load_contract(root)["native"] == {"depth": "deep"}
    # The root's list holds it: every package's contract takes the key.
    root.write_text(
        root.read_text().replace("extensions = []", 'extensions = ["acme.native"]')
    )
    assert load_contract(other)["native"] == {"depth": "deep"}


# What composes something in a package: the proof the declarations give.


def test_what_composes_a_package_is_read_from_the_declarations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    noop = '"{package}._steps:noop"'
    fake_package_extensions(
        tmp_path,
        monkeypatch,
        # The fallbacks first: what composes nothing in a package.
        quiet=PACKAGE,
        keyed='[contract.package.keyed.depth]\ntypes = ["str"]\ndoc = "how deep"\n',
        judge=(
            f'[phases.lint.judge]\nmain = {noop}\nextensions = ["acme.named"]\n'
            '\n[phases.lint.judge.fragments.".acme"]\ntext = "x"\n'
            'extensions = ["acme.framed"]\n'
        ),
        editor='[queries.editor-extensions]\nvalue = ["acme.editor"]\n',
        # A declaration that cannot be read names nothing, and the others
        # are judged all the same.
        broken="[extension]\nbogus = true\n",
        # Then each way one does: a step names it, itself or through a
        # per-package fragment; a step in a package's phase; an answer to
        # a question about a package, at the top level or for a target;
        # categories; and a requirement that composes.
        named=PACKAGE,
        framed=PACKAGE,
        built=f"[phases.build.built]\nmain = {noop}\n",
        asked="[queries.public-modules]\nvalue = []\n",
        targeted='[for."acme.quiet".queries.public-modules]\nvalue = []\n',
        categorised='[categories]\nsource = ["lua/**"]\n',
        wrapper=PACKAGE + 'requires = ["acme.built"]\n',
    )
    names = (
        "quiet",
        "keyed",
        "judge",
        "editor",
        "named",
        "framed",
        "built",
        "asked",
        "targeted",
        "categorised",
        "wrapper",
    )
    composing = [
        name for name in names if _extensions.composes_per_package(f"acme.{name}")
    ]
    assert composing == [
        "named",
        "framed",
        "built",
        "asked",
        "targeted",
        "categorised",
        "wrapper",
    ]
    # A name nothing installed declares composes nothing.
    assert not _extensions.composes_per_package("acme.ghost")


def test_the_proof_composes_exactly_cmake_conan_cpp_nanobind_python() -> None:
    skip_unless_installed("cmake")
    composing = [
        name
        for name in _extensions.installed_extensions()
        if _extensions.composes_per_package(name)
    ]
    assert composing == ["cmake", "conan", "cpp", "nanobind", "python"]


# A package's list composes: an entry that composes nothing refuses, and
# the fix moves it to the root's list.

#: A step the fakes below run.
NOOP = '"{package}._steps:noop"'

#: The end of the refusal of an entry that composes nothing in a package.
MOVE_IT = "; list it in [workspace] extensions, where the gate's --fix moves it"


def test_a_package_listing_an_extension_that_composes_nothing_refuses_saying_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_package_extensions(
        tmp_path,
        monkeypatch,
        # The checker's step names python and conan, so each of those
        # composes something in a package, and the checker does not.
        checker=(
            f"[phases.lint.checker]\nmain = {NOOP}\n"
            'extensions = ["acme.python", "acme.conan"]\n'
        ),
        python=PACKAGE,
        conan=PACKAGE,
        site=f"[phases.lint.site]\nmain = {NOOP}\n",
        quiet=PACKAGE,
    )
    root_contract(tmp_path, "[]")
    package_contract(
        tmp_path, "core", '["acme.checker", "acme.python", "acme.quiet", "acme.site"]'
    )
    # Each names what the extension does instead: what its steps judge,
    # or that it declares no step.
    assert _extensions.closure_problems(tmp_path) == [
        "packages/core: `extensions` lists acme.checker, which composes nothing in"
        " a package: its steps judge every package that holds acme.python or"
        " acme.conan" + MOVE_IT,
        "packages/core: `extensions` lists acme.quiet, which composes nothing in a"
        " package: it declares no step" + MOVE_IT,
        "packages/core: `extensions` lists acme.site, which composes nothing in a"
        " package: its steps judge the workspace as a whole" + MOVE_IT,
    ]
    # A name nothing installed declares has no step to name.
    assert _extensions.why_inert("acme.ghost") == "it declares no step"
    # The root's list takes any of them.
    root_contract(tmp_path, '["acme.checker", "acme.quiet", "acme.site"]')
    package_contract(tmp_path, "core", '["acme.python"]')
    assert _extensions.closure_problems(tmp_path) == []


def test_the_fix_moves_an_entry_that_composes_nothing_to_the_root_list_with_its_comments(  # noqa: E501
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_package_extensions(
        tmp_path,
        monkeypatch,
        native=f"[phases.build.native]\nmain = {NOOP}\n",
        site=f"[phases.lint.site]\nmain = {NOOP}\n",
        zulu=PACKAGE,
    )
    root = tmp_path / "workshop.toml"
    core = package_contract(tmp_path, "core", "[]")
    listed = (
        'extensions = [\n    "acme.native",\n    # the site\'s lint\n'
        '    "acme.site",  # moves\n]\n'
    )
    core.write_text(listed)
    app = package_contract(tmp_path, "app", '["acme.zulu"]')
    refusals = [
        "packages/app: `extensions` lists acme.zulu, which composes nothing in a"
        " package: it declares no step" + MOVE_IT,
        "packages/core: `extensions` lists acme.site, which composes nothing in a"
        " package: its steps judge the workspace as a whole" + MOVE_IT,
    ]
    # The fallback first: a root array the writer does not read takes no
    # entry, so nothing moves and the judge still names each.
    root.write_text('[workspace]\nextensions = ["""acme.zulu"""]\n')
    assert _extensions.closure_problems(tmp_path) == refusals
    assert _extensions.write_extensions(tmp_path) == []
    assert core.read_text() == listed
    assert app.read_text() == 'extensions = ["acme.zulu"]\n'
    # A root array it reads takes each entry it does not hold yet, with
    # the entry's comments, and is written as its canonical list.
    root.write_text(
        '[workspace]\nextensions = [\n    # the last\n    "acme.zulu",\n]\n'
    )
    assert _extensions.write_extensions(tmp_path) == [
        '  layering: [workspace] extensions is ["acme.site", "acme.zulu"], its'
        " canonical list",
        "  layering: packages/app `extensions` is [], and [workspace] extensions"
        " lists acme.zulu, which composes nothing in a package",
        '  layering: packages/core `extensions` is ["acme.native"], and [workspace]'
        " extensions lists acme.site, which composes nothing in a package",
    ]
    assert root.read_text() == (
        "[workspace]\nextensions = [\n    # the site's lint\n"
        '    "acme.site",  # moves\n    # the last\n    "acme.zulu",\n]\n'
    )
    assert core.read_text() == 'extensions = [\n    "acme.native",\n]\n'
    assert app.read_text() == "extensions = []\n"
    assert _extensions.closure_problems(tmp_path) == []
    assert _extensions.write_extensions(tmp_path) == []


def test_an_entry_whose_requirements_compose_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_package_extensions(
        tmp_path,
        monkeypatch,
        native=f"[phases.build.native]\nmain = {NOOP}\n",
        site=f"[phases.lint.site]\nmain = {NOOP}\n",
        # A binding's own step judges the workspace, and it requires an
        # extension that composes something in a package.
        binding=(
            PACKAGE + 'requires = ["acme.native", "acme.site"]\n'
            f"\n[phases.lint.binding]\nmain = {NOOP}\n"
        ),
    )
    root_contract(tmp_path, "[]")
    core = package_contract(tmp_path, "core", '["acme.binding"]')
    assert _extensions.closure_problems(tmp_path) == []
    assert _extensions.write_extensions(tmp_path) == []
    assert core.read_text() == 'extensions = ["acme.binding"]\n'
    # An entry another entry requires leaves the canonical list instead of
    # moving: the package's set holds it through the requirement.
    app = package_contract(tmp_path, "app", '["acme.binding", "acme.site"]')
    assert _extensions.closure_problems(tmp_path) == [
        'packages/app: `extensions` is ["acme.binding", "acme.site"], and its'
        ' canonical list is ["acme.binding"]; acme.site is required by another'
        " listed extension; the gate's --fix rewrites it"
    ]
    assert _extensions.write_extensions(tmp_path) == [
        '  layering: packages/app `extensions` is ["acme.binding"], its canonical list'
    ]
    assert app.read_text() == 'extensions = ["acme.binding"]\n'
    assert (tmp_path / "workshop.toml").read_text() == "[workspace]\nextensions = []\n"
