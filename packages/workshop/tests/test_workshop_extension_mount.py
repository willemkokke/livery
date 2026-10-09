"""Extensions at mount: the refusals first, then what an extension declares."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from livery.workshop import _checks, _extensions
from livery.workshop._checks import (
    CheckRecord,
    GateContext,
    register_check,
    unregister_check,
)
from workshop_extension_fakes import fake_extensions, root_contract

_FAILURES = (BaseException,)


@pytest.fixture
def restored_checks():
    state = _checks.snapshot()
    yield
    _checks.restore(state)


def _noop(ctx: GateContext) -> None:
    del ctx


# The refusals first.


def test_an_incompatible_api_version_refuses_naming_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(tmp_path, monkeypatch, old="[extension]\napi-version = 99\n")
    found = _extensions.declaration("acme.old")
    assert found is not None
    with pytest.raises(
        RuntimeError,
        match="declares workshop API version 99; this workshop is version 1",
    ):
        _extensions.check_api_version("acme.old", found)
    # Mount refuses it before anything registers.
    root_contract(tmp_path, '["acme.old"]')
    from livery.footman import _registry as registry

    with registry.capture(), pytest.raises(RuntimeError, match="API version 99"):
        _extensions.mount_extensions(tmp_path)


def test_a_missing_dependency_and_a_misordered_one_are_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(
        tmp_path,
        monkeypatch,
        brand='[extension]\nrequires = ["acme.base"]\n',
        base="",
    )
    root_contract(tmp_path, '["acme.brand"]')
    (problem,) = _extensions.closure_problems(tmp_path)
    assert problem == (
        "[workspace] extensions lists acme.brand, which depends on acme.base, and"
        " does not list it; the gate's --fix adds it before acme.brand"
    )
    root_contract(tmp_path, '["acme.brand", "acme.base"]')
    (problem,) = _extensions.closure_problems(tmp_path)
    assert problem == (
        "[workspace] extensions lists acme.base after acme.brand, which depends on"
        " it; move acme.base before acme.brand"
    )
    # The layering lint carries the closure: a workspace with the
    # contract at its root refuses through it.
    from livery.workshop._packages import verify_workspace

    with pytest.raises(ValueError, match=re.escape("move acme.base before acme.brand")):
        verify_workspace(tmp_path)


def test_a_root_without_a_contract_writes_nothing(tmp_path: Path) -> None:
    # The fix runs for a scoped gate whose root is not a workspace yet.
    assert _extensions.write_extensions(tmp_path) == []
    assert _extensions.closure_problems(tmp_path) == []
    assert not (tmp_path / "workshop.toml").exists()


# Then what an extension declares, written and named.


def test_the_fix_adds_the_missing_extension_before_its_dependent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(
        tmp_path,
        monkeypatch,
        brand='[extension]\nrequires = ["acme.base"]\n',
        base="",
    )
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nextensions = [\n    "acme.brand",\n]\n'
    )
    assert _extensions.write_extensions(tmp_path) == [
        "  layering: [workspace] extensions gains acme.base before acme.brand, which"
        " requires it"
    ]
    text = (tmp_path / "workshop.toml").read_text()
    assert '    "acme.base",  # required by acme.brand\n    "acme.brand",\n' in text
    assert _extensions.closure_problems(tmp_path) == []
    assert _extensions.write_extensions(tmp_path) == []  # idempotent
    assert _extensions.requirers(tmp_path)["acme.base"] == ("acme.brand",)
    # The inline list form takes the entry the same way.
    root_contract(tmp_path, '["acme.brand"]')
    _extensions.write_extensions(tmp_path)
    assert (
        'extensions = ["acme.base", "acme.brand"]'
        in (tmp_path / "workshop.toml").read_text()
    )


def test_the_fix_places_a_requirement_before_its_dependents_entry_of_any_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(
        tmp_path, monkeypatch, brand='[extension]\nrequires = ["acme.base"]\n', base=""
    )
    contract = tmp_path / "workshop.toml"
    added = [
        "  layering: [workspace] extensions gains acme.base before acme.brand,"
        " which requires it"
    ]
    contract.write_text(
        '[workspace]\nextensions = [\n    { name = "acme.brand", for = [] },\n]\n'
    )
    assert _extensions.write_extensions(tmp_path) == added
    assert contract.read_text() == (
        '[workspace]\nextensions = [\n    "acme.base",  # required by acme.brand\n'
        '    { name = "acme.brand", for = [] },\n]\n'
    )
    contract.write_text('[workspace]\nextensions = [{ name = "acme.brand" }]\n')
    assert _extensions.write_extensions(tmp_path) == added
    assert contract.read_text() == (
        '[workspace]\nextensions = ["acme.base", { name = "acme.brand" }]\n'
    )
    # A spelling the writer cannot read is a person's edit; the judge
    # names it until then.
    contract.write_text('[workspace]\nextensions=[{ name = "acme.brand" }]\n')
    assert _extensions.write_extensions(tmp_path) == []
    assert "acme.base" not in contract.read_text()


def test_a_declaration_without_an_api_version_is_taken_at_the_current_version() -> None:
    from livery.workshop._declaration import Declaration

    quiet = Declaration("acme.quiet", "acme.quiet", Path("extension.toml"))
    _extensions.check_api_version("acme.quiet", quiet)


def test_the_gate_names_what_a_extension_registered_and_withdrew(
    restored_checks,
) -> None:
    # An extension mounted earlier in this process (the docs extension's
    # checks) is already named; the lines below are this test's own.
    before = set(_checks.narrowings())

    def added() -> set[str]:
        return set(_checks.narrowings()) - before

    register_check(CheckRecord("brand", "lint", _noop, extension="acme.brand"))
    unregister_check("test.ctest", by="acme.brand")
    assert added() == {
        "  lint.brand: registered by acme.brand",
        "  test.ctest: withdrawn by acme.brand",
    }
    # An extension withdrawing its own check narrows nothing the base owned.
    unregister_check("lint.brand", by="acme.brand")
    assert added() == {"  test.ctest: withdrawn by acme.brand"}
    # Registering the name again clears the withdrawal.
    register_check(CheckRecord("ctest", "test", _noop))
    assert added() == set()


def test_the_doctor_lists_installed_extensions_the_contract_does_not_mount() -> None:
    advertised = [
        ("docs", "livery-workshop"),
        ("livery.forge", "livery-forge"),
        ("acme.brand", "acme-brand"),
    ]
    assert _extensions.available_extensions(("livery.forge",), advertised) == [
        ("docs", "livery-workshop"),
        ("acme.brand", "acme-brand"),
    ]
    assert (
        _extensions.available_extensions(
            ("docs", "livery.forge", "acme.brand"), advertised
        )
        == []
    )


def test_a_footman_plugin_is_not_offered_as_an_extension() -> None:
    # footman's own task providers declare no extension: never offered,
    # however many verbs they add.
    offered = {name for name, _ in _extensions.available_extensions(())}
    assert "footman.self" not in offered and "footman.janitor" not in offered
    assert "docs" in offered


def test_the_mount_remembers_what_it_found_undeclared_until_an_install_declares_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored_checks: None
) -> None:
    # Listed and installed by nothing: the mount names it, skips it, and
    # remembers it; nothing is declared since, so nothing is new.
    root_contract(tmp_path, '["acme.later"]')
    from livery.footman import _registry as registry

    with registry.capture():
        assert _extensions.mount_extensions(tmp_path) == ()
    assert _extensions.UNDECLARED == ("acme.later",)
    assert _extensions.unmounted(tmp_path) == ("acme.later",)
    assert _extensions.declared_now(("acme.later",)) == ()
    # An install declares it: the next look finds it by its entry point,
    # and imports nothing, since a distribution installed after the
    # interpreter started may not be importable in it.
    fake_extensions(tmp_path, monkeypatch, later="", unloadable="")
    unloadable = tmp_path / "site" / "acme" / "unloadable" / "__init__.py"
    unloadable.write_text("raise ImportError('later')\n")
    assert _extensions.declared_now(("acme.later", "acme.unloadable")) == (
        "acme.later",
        "acme.unloadable",
    )
    assert _extensions.declared_now(()) == ()
    # A contract that no longer lists it counts it no more.
    root_contract(tmp_path, "[]")
    assert _extensions.unmounted(tmp_path) == ()
    # A mount that finds every listed extension remembers none.
    root_contract(tmp_path, '["acme.later"]')
    with registry.capture():
        _extensions.mount_extensions(tmp_path)
    assert list(_extensions.UNDECLARED) == []
    assert _extensions.unmounted(tmp_path) == ()


def test_an_extension_listed_at_the_wrong_level_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_extensions(
        tmp_path,
        monkeypatch,
        native='[extension]\nlevels = ["package"]\n',
        site="",
    )
    # A package-level extension in the workspace list: the gate refuses,
    # the mount names it and skips it.
    root_contract(tmp_path, '["acme.native"]')
    from livery.footman import _registry as registry

    with registry.capture():
        assert _extensions.mount_extensions(tmp_path) == ()
    assert "declares the levels package" in capsys.readouterr().err
    assert (
        "[workspace] extensions lists acme.native, which is listed in a"
        " package's `extensions` alone; move it there"
    ) in _extensions.closure_problems(tmp_path)
    # A workspace-level extension in a package's list: named the same way.
    root_contract(tmp_path, "[]")
    member = tmp_path / "packages" / "core"
    member.mkdir(parents=True)
    (member / "workshop.toml").write_text(
        'kind = "python"\nextensions = ["acme.site"]\n'
    )
    assert _extensions.closure_problems(tmp_path) == [
        "packages/core: `extensions` lists acme.site, which is listed in"
        " [workspace] extensions alone; move it there"
    ]


# The options an entry turns on: the refusals first, then what they register.

_OPTIONED = """\
[options]
deep = "judges deeper"

[checks.acme.lint]
run = "{package}._checks:noop"

[checks.acme.typecomplete]
run = "{package}._checks:noop"
listed-with = "deep"
"""


def test_an_entry_with_a_version_or_a_scope_refuses_naming_the_spelling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(tmp_path, monkeypatch, tool=_OPTIONED)
    for spelled in ("acme.tool>=1.0", "acme.tool?", "acme.tool[deep]@linux"):
        root_contract(tmp_path, f'["{spelled}"]')
        assert _extensions.closure_problems(tmp_path) == [
            f"[workspace] extensions lists {spelled!r}; an entry is a name with"
            " the options it turns on, `name` or `name[option,option]`, and no"
            " version or scope: the lock pins an extension's version from its"
            " wheel"
        ]
    # A spelling the grammar does not read is named the same way, and
    # read whole as a name no distribution declares.
    root_contract(tmp_path, '["acme.tool[deep"]')
    problems = _extensions.closure_problems(tmp_path)
    assert any(
        problem.startswith("[workspace] extensions lists 'acme.tool[deep';")
        for problem in problems
    )
    assert _extensions.extension_names(tmp_path) == ("acme.tool[deep",)


def test_an_option_the_extension_does_not_declare_refuses_and_mounts_off(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    restored_checks: None,
) -> None:
    fake_extensions(tmp_path, monkeypatch, tool=_OPTIONED)
    root_contract(tmp_path, '["acme.tool[deeper]"]')
    why = (
        "[workspace] extensions lists acme.tool with 'deeper', which it does not"
        " declare; its options are deep"
    )
    assert _extensions.closure_problems(tmp_path) == [why]
    from livery.footman import _registry as registry

    with registry.capture():
        assert _extensions.mount_extensions(tmp_path) == ("acme.tool",)
    assert f"{why}; the mount leaves it off" in capsys.readouterr().err
    names = _checks.checks_by_name()
    assert "lint.acme" in names
    assert "typecomplete.acme" not in names


def test_a_check_for_an_option_its_extension_does_not_declare_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored_checks: None
) -> None:
    from livery.workshop._declaration import DeclarationError

    fake_extensions(
        tmp_path,
        monkeypatch,
        tool=_OPTIONED.replace('[options]\ndeep = "judges deeper"\n', ""),
        odd='options = ["deep"]\n',
    )
    with pytest.raises(DeclarationError, match="its options are none"):
        _extensions.declaration("acme.tool")
    with pytest.raises(DeclarationError, match=r"options is a list"):
        _extensions.declared_options("acme.odd")


def test_an_option_registers_its_checks_and_fm_extensions_says_which_are_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored_checks: None
) -> None:
    fake_extensions(tmp_path, monkeypatch, tool=_OPTIONED)
    root_contract(tmp_path, '["acme.tool"]')
    assert _extensions.extension_options(tmp_path) == {"acme.tool": ()}
    described = _extensions.describe_extensions(tmp_path)
    assert "    option deep (off): judges deeper" in described
    # The table form spells its name the same way.
    root_contract(tmp_path, '[{ name = "acme.tool[deep]" }]')
    assert _extensions.extension_names(tmp_path) == ("acme.tool",)
    assert _extensions.extension_options(tmp_path) == {"acme.tool": ("deep",)}
    assert _extensions.closure_problems(tmp_path) == []
    described = _extensions.describe_extensions(tmp_path)
    assert "    option deep (on): judges deeper" in described
    from livery.footman import _registry as registry

    with registry.capture():
        _extensions.mount_extensions(tmp_path)
    assert _checks.checks_by_name()["typecomplete.acme"].extension == "acme.tool"


_BRANDED = """\
[extension]
plugin = "{package}"

[checks.brand.lint]
run = "{package}._checks:noop"

[ci.jobs.gate.brand]
entries = ["lint.brand"]
gates = true
"""


def test_an_extension_its_app_mounts_as_builtin_registers_its_declaration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    restored_checks: None,
) -> None:
    # A branded App mounts its own extension's plugin before the mount
    # runs: the mount leaves the plugin alone, and registers what the
    # declaration adds as it does for any other extension.
    from livery.footman import _paths
    from livery.workshop._points import verdict_needs, withdraw_job

    fake_extensions(tmp_path, monkeypatch, brand=_BRANDED)
    root_contract(tmp_path, '["acme.brand"]')
    monkeypatch.setattr(_paths, "_builtin", ("acme.brand",))
    try:
        _mount(tmp_path)
        assert _checks.checks_by_name()["lint.brand"].extension == "acme.brand"
        assert "brand" in verdict_needs("gate")
    finally:
        withdraw_job("gate", "brand")
    assert "did not mount its plugin" not in capsys.readouterr().err


def test_the_list_writers_find_an_entry_by_its_name_whatever_its_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(
        tmp_path,
        monkeypatch,
        tool='[extension]\nrequires = ["acme.base"]\n' + _OPTIONED,
        base="",
    )
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nextensions = [\n    "acme.tool[deep]",\n]\n'
    )
    assert _extensions.write_extensions(tmp_path) == [
        "  layering: [workspace] extensions gains acme.base before acme.tool,"
        " which requires it"
    ]
    written = (tmp_path / "workshop.toml").read_text()
    assert '    "acme.base",  # required by acme.tool\n    "acme.tool[deep]",\n' in (
        written
    )
    root_contract(tmp_path, '["acme.tool[deep]"]')
    _extensions.write_extensions(tmp_path)
    written = (tmp_path / "workshop.toml").read_text()
    assert 'extensions = ["acme.base", "acme.tool[deep]"]' in written


# The tool declaration and the contributions by target: refusals and
# the unlisted arms first.


def test_a_extension_declaring_tools_off_the_shape_refuses_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._declaration import DeclarationError

    fake_extensions(tmp_path, monkeypatch, odd='[toolroom]\nrequires = "docker"\n')
    root_contract(tmp_path, '["acme.odd"]')
    with pytest.raises(DeclarationError, match=r"toolroom\.requires is a string"):
        _extensions.extension_tools(tmp_path)
    from livery.workshop._tools import requirements

    with pytest.raises(_FAILURES, match=r"toolroom\.requires"):
        requirements(tmp_path)


def test_an_unlisted_extensions_tools_never_enter_the_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._tools import requirements, tool_names

    fake_extensions(
        tmp_path, monkeypatch, tooled='[toolroom]\nrequires = ["docker>=27"]\n'
    )
    root_contract(tmp_path, "[]")
    assert "extension acme.tooled" not in {r.site for r in requirements(tmp_path)}
    assert "docker" not in tool_names(tmp_path)
    # Listed, the extension is the fourth site, named as the kinds are.
    root_contract(tmp_path, '["acme.tooled"]')
    found = [r for r in requirements(tmp_path) if r.site == "extension acme.tooled"]
    assert [(r.name, r.floor) for r in found] == [("docker", "27")]
    assert "docker" in tool_names(tmp_path)
    assert "    tools: docker>=27" in _extensions.describe_extensions(tmp_path)


def test_a_extension_declaring_contributions_off_the_shape_refuses_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._declaration import DeclarationError

    fake_extensions(tmp_path, monkeypatch, odd='for = ["acme.python"]\n')
    root_contract(tmp_path, '["acme.odd"]')
    with pytest.raises(DeclarationError, match=r"for is a list"):
        _extensions.declared_targets(tmp_path)


#: A house's check for a python extension, registered while both are listed.
_HOUSE = """\
[for."acme.python".checks.house.lint]
run = "{package}._checks:noop"
"""


def _house(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A house contributing to a python extension, and that extension."""
    fake_extensions(tmp_path, monkeypatch, house=_HOUSE, python="")


def _grafted() -> bool:
    return "lint.house" in _checks.checks_by_name()


def test_contributions_for_a_target_follow_the_graft_and_what_requires_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import contributions_for

    _house(tmp_path, monkeypatch)
    fake_extensions(
        tmp_path,
        monkeypatch,
        cpp="",
        needy=(
            '[extension]\nrequires = ["acme.python"]\n\n'
            '[checks.needy.lint]\nrun = "{package}._checks:noop"\n'
        ),
    )
    # The empty answers first: a target the workspace does not list, and
    # a contribution whose listing points it at another target.
    root_contract(tmp_path, '["acme.house"]')
    assert contributions_for("acme.python", tmp_path) == {}
    root_contract(
        tmp_path,
        '["acme.python", "acme.cpp", { name = "acme.house", for = ["acme.cpp"] }]',
    )
    assert contributions_for("acme.python", tmp_path) == {}
    # Then the table the mount grafts, as the file holds it, and the
    # top-level tables of an extension that requires the target.
    root_contract(tmp_path, '["acme.python", "acme.house", "acme.needy"]')
    assert contributions_for("acme.python", tmp_path) == {
        "acme.house": {
            "checks": {"house": {"lint": {"run": "acme.house._checks:noop"}}}
        },
        "acme.needy": {
            "checks": {"needy": {"lint": {"run": "acme.needy._checks:noop"}}}
        },
    }


def _mount(root: Path) -> None:
    from livery.footman import _registry as registry

    with registry.capture():
        _extensions.mount_extensions(root)


def test_a_for_naming_a_target_the_extension_declares_nothing_for_mounts_and_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored_checks: None
) -> None:
    # The mount goes on without the target, and the layering check,
    # which runs inside a working fm, names the entry.
    _house(tmp_path, monkeypatch)
    fake_extensions(tmp_path, monkeypatch, cpp="")
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nextensions = [\n    "acme.python",\n'
        '    "acme.cpp",\n    { name = "acme.house", for = ["acme.cpp"] },\n]\n'
    )
    _mount(tmp_path)
    assert not _grafted()
    (problem,) = _extensions.closure_problems(tmp_path)
    assert problem == (
        "[workspace] extensions: the entry for acme.house names acme.cpp in `for`,"
        " and acme.house declares no contribution for it; remove it from `for`"
    )


def test_a_contribution_for_an_unlisted_target_never_mounts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored_checks: None
) -> None:
    _house(tmp_path, monkeypatch)
    root_contract(tmp_path, '["acme.house"]')
    _mount(tmp_path)
    assert not _grafted()
    assert _extensions.resolved_targets(tmp_path)["acme.house"] == ()
    assert "    for: acme.python" not in _extensions.describe_extensions(tmp_path)


def test_a_contribution_mounts_once_both_are_listed_whichever_is_later(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored_checks: None
) -> None:
    _house(tmp_path, monkeypatch)
    for listed in (
        '["acme.python", "acme.house"]',
        '["acme.house", "acme.python"]',
    ):
        if _grafted():
            _checks.unregister_check("lint.house")
        root_contract(tmp_path, listed)
        _mount(tmp_path)
        assert _checks.checks_by_name()["lint.house"].extension == "acme.house"
        assert _extensions.resolved_targets(tmp_path)["acme.house"] == ("acme.python",)
    assert "    for: acme.python" in _extensions.describe_extensions(tmp_path)


_HOUSE_DEEP = """\
[options]
deep = "judges the python extension's packages deeper"

[for."acme.python".checks.house.lint]
run = "{package}._checks:noop"
listed-with = "deep"
"""


def test_a_grafted_check_registers_under_the_options_its_owners_listing_turns_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored_checks: None
) -> None:
    fake_extensions(tmp_path, monkeypatch, house=_HOUSE_DEEP, python="")
    # The fallback first: listed without the option, the check stays off.
    root_contract(tmp_path, '["acme.python", "acme.house"]')
    _mount(tmp_path)
    assert not _grafted()
    # Turned on, it registers whichever of the two the list names first.
    for listed in (
        '["acme.python", "acme.house[deep]"]',
        '["acme.house[deep]", "acme.python"]',
    ):
        if _grafted():
            _checks.unregister_check("lint.house")
        root_contract(tmp_path, listed)
        _mount(tmp_path)
        assert _checks.checks_by_name()["lint.house"].extension == "acme.house"


def test_a_contribution_whose_reference_does_not_resolve_refuses_naming_its_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(
        tmp_path, monkeypatch, house=_HOUSE.replace(":noop", ":absent"), python=""
    )
    root_contract(tmp_path, '["acme.python", "acme.house"]')
    with pytest.raises(
        RuntimeError,
        match=r'for\."acme\.python"\.checks\.house\.lint\.run names absent',
    ):
        _mount(tmp_path)


def test_the_fix_records_for_once_and_a_deleted_name_stays_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _house(tmp_path, monkeypatch)
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nextensions = [\n    "acme.python",\n'
        '    "acme.house",  # the opinions\n]\n'
    )
    assert _extensions.write_extensions(tmp_path) == [
        "  layering: [workspace] extensions records acme.house for acme.python"
    ]
    text = (tmp_path / "workshop.toml").read_text()
    assert (
        '    { name = "acme.house", for = ["acme.python"] },  # the opinions\n' in text
    )
    assert _extensions.write_extensions(tmp_path) == []  # written once
    # From then on the list is the truth: a deleted name stays deleted.
    (tmp_path / "workshop.toml").write_text(
        text.replace('for = ["acme.python"]', "for = []")
    )
    assert _extensions.write_extensions(tmp_path) == []
    assert _extensions.resolved_targets(tmp_path)["acme.house"] == ()
    assert _extensions.closure_problems(tmp_path) == []
    # A name the list does not carry, or the extension does not declare, refuses.
    (tmp_path / "workshop.toml").write_text(
        text.replace('for = ["acme.python"]', 'for = ["acme.cpp"]')
    )
    (problem,) = _extensions.closure_problems(tmp_path)
    assert problem == (
        "[workspace] extensions: the entry for acme.house names acme.cpp in `for`,"
        " and does not list it; list it, or remove it from `for`"
    )
    root_contract(tmp_path, '["acme.python", "acme.house"]')
    # The inline form takes the entry the same way, and a table entry
    # without `for` gains the key.
    _extensions.write_extensions(tmp_path)
    assert (
        'extensions = ["acme.python", { name = "acme.house",'
        ' for = ["acme.python"] }]' in (tmp_path / "workshop.toml").read_text()
    )
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nextensions = [\n    "acme.python",\n'
        '    { name = "acme.house" },\n]\n'
    )
    _extensions.write_extensions(tmp_path)
    assert (
        '    { name = "acme.house", for = ["acme.python"] },\n'
        in (tmp_path / "workshop.toml").read_text()
    )


def test_a_root_without_a_contract_lists_no_extensions_and_declares_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A verb handed a root with no workshop.toml, as the env check's
    # tests do, reads an empty list rather than a missing file.
    monkeypatch.setattr(_extensions, "workspace_root", lambda start=None: tmp_path)
    assert _extensions.extension_entries(tmp_path) == ()
    assert _extensions.extension_targets(tmp_path) == {}
    assert _extensions.extension_tools(tmp_path) == {}
    assert _extensions.resolved_targets(tmp_path) == {}
