"""Extensions at mount: the refusals first, then what an extension declares."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from livery.workshop import _extensions, _steps
from livery.workshop._phases import PhaseContext
from livery.workshop._steps import (
    StepRecord,
    register_step,
    unregister_step,
)
from workshop_extension_fakes import fake_extensions, package_contract, root_contract

_FAILURES = (BaseException,)

#: This repository's root, where the workshop under test lives in its checkout.
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def restored_steps():
    state = _steps.snapshot()
    yield
    _steps.restore(state)


def _noop(ctx: PhaseContext) -> None:
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


def test_a_root_list_out_of_its_canonical_form_is_named_and_the_fix_writes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(
        tmp_path,
        monkeypatch,
        brand='[extension]\nrequires = ["acme.base"]\n',
        base="",
        zeta="",
    )
    contract = tmp_path / "workshop.toml"
    suffix = "; the gate's --fix rewrites it"
    # The refusals first: an implied entry, an order that is not
    # composition order, an extension listed twice, and a table entry
    # whose `for` is its default.
    for listed, canonical, why in (
        (
            '["acme.brand", "acme.base"]',
            '["acme.brand"]',
            "acme.base is required by another listed extension",
        ),
        (
            '["acme.zeta", "acme.brand"]',
            '["acme.brand", "acme.zeta"]',
            "its order is composition order: each extension after what it"
            " requires, ties alphabetical",
        ),
        (
            '["acme.brand", "acme.brand"]',
            '["acme.brand"]',
            "it lists an extension twice",
        ),
        (
            '[{ name = "acme.brand" }]',
            '["acme.brand"]',
            "the entry for acme.brand names its default targets, which the name"
            " alone says",
        ),
    ):
        root_contract(tmp_path, listed)
        (problem,) = _extensions.closure_problems(tmp_path)
        assert problem == (
            f"[workspace] extensions is {listed}, and its canonical list is"
            f" {canonical}; {why}{suffix}"
        )
    # The layering lint carries the judge: a workspace with the contract
    # at its root refuses through it.
    from livery.workshop._packages import verify_workspace

    with pytest.raises(ValueError, match=re.escape("which the name alone says")):
        verify_workspace(tmp_path)
    # The fix writes the canonical list, each entry with its comments;
    # an entry left out takes its comments with it.
    contract.write_text(
        "[workspace]\n"
        "# The list.\n"
        "extensions = [  # the opening comment stays\n"
        "    # The last one.\n"
        '    "acme.zeta",  # its own line\n'
        "    # The requirement, implied.\n"
        '    "acme.base",\n'
        "\n"
        "    # The brand.\n"
        '    { name = "acme.brand" },\n'
        "    # The closing comment stays.\n"
        "]\n"
        "\n[forge]\nkind = 'github'\n"
    )
    assert _extensions.write_extensions(tmp_path) == [
        '  layering: [workspace] extensions is ["acme.brand", "acme.zeta"], its'
        " canonical list"
    ]
    assert contract.read_text() == (
        "[workspace]\n"
        "# The list.\n"
        "extensions = [  # the opening comment stays\n"
        "\n"
        "    # The brand.\n"
        '    "acme.brand",\n'
        "    # The last one.\n"
        '    "acme.zeta",  # its own line\n'
        "    # The closing comment stays.\n"
        "]\n"
        "\n[forge]\nkind = 'github'\n"
    )
    assert _extensions.closure_problems(tmp_path) == []
    assert _extensions.write_extensions(tmp_path) == []  # idempotent
    # One line stays one line.
    root_contract(tmp_path, '["acme.zeta", "acme.base", "acme.brand"]')
    _extensions.write_extensions(tmp_path)
    assert contract.read_text() == (
        '[workspace]\nextensions = ["acme.brand", "acme.zeta"]\n'
    )


def test_an_array_the_writer_does_not_read_is_named_until_a_person_writes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(tmp_path, monkeypatch, zeta="", alpha="")
    contract = tmp_path / "workshop.toml"
    # A multi-line string is TOML the writer leaves alone, and so is a
    # list written as a dotted key outside its table, and one whose key
    # a string before it spells as well, which the writer reads first.
    for text in (
        '[workspace]\nextensions = [\n    """acme.zeta""",\n    "acme.alpha",\n]\n',
        'workspace.extensions = ["acme.zeta", "acme.alpha"]\n',
        '[workspace]\ndescription = """\nextensions = ["acme.alpha"]\n"""\n'
        'extensions = ["acme.zeta", "acme.alpha"]\n',
    ):
        contract.write_text(text)
        (problem,) = _extensions.closure_problems(tmp_path)
        assert "its canonical list is" in problem
        assert _extensions.write_extensions(tmp_path) == []
        assert contract.read_text() == text
    # A table entry that is more than a name and a `for` of names is the
    # contract judge's to name: the list is not judged canonical until
    # it goes.
    for listed in (
        '[{ name = "acme.zeta", version = "1" }, "acme.alpha"]',
        '[{ name = "acme.zeta", for = "acme.alpha" }, "acme.alpha"]',
    ):
        root_contract(tmp_path, listed)
        assert _extensions.root_list_problems(tmp_path) == []
        assert _extensions.write_extensions(tmp_path) == []
    # A literal string is an entry like any other.
    contract.write_text("[workspace]\nextensions = ['acme.zeta', 'acme.alpha']\n")
    assert _extensions.write_extensions(tmp_path) == [
        '  layering: [workspace] extensions is ["acme.alpha", "acme.zeta"], its'
        " canonical list"
    ]
    assert contract.read_text() == (
        "[workspace]\nextensions = ['acme.alpha', 'acme.zeta']\n"
    )


def test_a_root_without_a_contract_writes_nothing(tmp_path: Path) -> None:
    # The fix runs for a scoped gate whose root is not a workspace yet.
    assert _extensions.write_extensions(tmp_path) == []
    assert _extensions.closure_problems(tmp_path) == []
    assert not (tmp_path / "workshop.toml").exists()


# Then what an extension declares, written and named.


def test_a_requirement_the_root_list_leaves_out_joins_the_root_s_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(
        tmp_path,
        monkeypatch,
        brand='[extension]\nrequires = ["acme.base"]\n',
        base="",
    )
    root_contract(tmp_path, '["acme.brand"]')
    assert _extensions.closure_problems(tmp_path) == []
    assert _extensions.write_extensions(tmp_path) == []
    assert _extensions.root_set(tmp_path) == ("acme.base", "acme.brand")
    assert _extensions.extension_names(tmp_path) == ("acme.base", "acme.brand")
    assert _extensions.requirers(tmp_path)["acme.base"] == ("acme.brand",)


def test_a_declaration_without_an_api_version_is_taken_at_the_current_version() -> None:
    from livery.workshop._declaration import Declaration

    quiet = Declaration("acme.quiet", "acme.quiet", Path("extension.toml"))
    _extensions.check_api_version("acme.quiet", quiet)


def test_the_gate_names_what_a_extension_registered_and_withdrew(
    restored_steps,
) -> None:
    # An extension mounted earlier in this process (the docs extension's
    # steps) is already named; the lines below are this test's own.
    before = set(_steps.narrowings())

    def added() -> set[str]:
        return set(_steps.narrowings()) - before

    register_step(StepRecord("brand", "lint", _noop, extension="acme.brand"))
    unregister_step("drift.check", by="acme.brand")
    assert added() == {
        "  lint.brand: registered by acme.brand",
        "  drift.check: withdrawn by acme.brand",
    }
    # An extension withdrawing its own step narrows nothing the base owned.
    unregister_step("lint.brand", by="acme.brand")
    assert added() == {"  drift.check: withdrawn by acme.brand"}
    # Registering the name again clears the withdrawal.
    register_step(StepRecord("check", "drift", _noop))
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored_steps: None
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


def test_a_listed_extension_no_distribution_declares_is_named_with_its_list(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    restored_steps: None,
) -> None:
    fake_extensions(
        tmp_path, monkeypatch, native='[extension]\nrequires = ["acme.gone"]\n'
    )
    root_contract(tmp_path, '["acme.later"]')
    package_contract(tmp_path, "core", '["acme.ghost"]')
    package_contract(tmp_path, "app", '["acme.ghost", "acme.native"]')
    from livery.footman import _registry as registry

    with registry.capture():
        assert _extensions.mount_extensions(tmp_path) == ()
    notes = [
        line.removeprefix("  note: ")
        for line in capsys.readouterr().err.splitlines()
        if "no installed distribution declares it" in line
    ]
    # Each is named with the list that brings it: a package's, the root's,
    # or the listed extension that requires it.
    assert [note.partition(", and no installed")[0] for note in notes] == [
        "extension 'acme.ghost' is listed in the `extensions` of packages/app and"
        " packages/core",
        "extension 'acme.gone' is required by acme.native",
        "extension 'acme.later' is listed in [workspace] extensions",
    ]
    assert notes[0].endswith(
        "install the distribution that ships it (acme-ghost by its name), or"
        " remove the entry; `fm sync` installs one this workspace builds"
    )
    # The sync installs each of them, a package's own among them.
    assert _extensions.unmounted(tmp_path) == ("acme.ghost", "acme.gone", "acme.later")


def test_any_extension_mounts_from_either_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored_steps: None
) -> None:
    fake_extensions(
        tmp_path,
        monkeypatch,
        native='[phases.build.native]\nmain = "{package}._steps:noop"\n',
        site='[phases.lint.site]\nmain = "{package}._steps:noop"\n',
    )
    # One that composes a package in the root's list, and one that
    # composes nothing in a package in a package's list.
    assert _extensions.composes_per_package("acme.native")
    assert not _extensions.composes_per_package("acme.site")
    root_contract(tmp_path, '["acme.native"]')
    package_contract(tmp_path, "core", '["acme.site"]')
    from livery.footman import _registry as registry

    with registry.capture():
        assert _extensions.mount_extensions(tmp_path) == ("acme.native", "acme.site")
    steps = _steps.steps_by_name()
    assert steps["lint.site"].extension == "acme.site"
    assert steps["build.native"].extension == "acme.native"
    assert _extensions.closure_problems(tmp_path) == []


# The entries of the list: the refusals first, then what a step declared
# off registers.

_DECLARED_OFF = """\
[phases.lint.acme]
main = "{package}._steps:noop"

[phases.typecomplete.acme]
main = "{package}._steps:noop"
enabled = false
"""


def test_an_entry_with_a_version_or_a_scope_refuses_naming_the_spelling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(tmp_path, monkeypatch, tool=_DECLARED_OFF)
    for spelled in ("acme.tool>=1.0", "acme.tool?", "acme.tool@linux"):
        root_contract(tmp_path, f'["{spelled}"]')
        assert _extensions.closure_problems(tmp_path) == [
            f"[workspace] extensions lists {spelled!r}; an entry is a name alone,"
            " with no version or scope: the lock pins an extension's version from"
            " its wheel"
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


def test_an_extension_entry_with_brackets_refuses_naming_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    restored_steps: None,
) -> None:
    fake_extensions(tmp_path, monkeypatch, tool=_DECLARED_OFF)
    root_contract(tmp_path, '["acme.tool[typecomplete.acme]"]')
    why = (
        "[workspace] extensions lists 'acme.tool[typecomplete.acme]'; an entry is"
        " a name alone, and a step acme.tool declares off is turned on with"
        " [phases.typecomplete.acme] enabled = true in this contract"
    )
    assert _extensions.closure_problems(tmp_path) == [why]
    # An option that is no step address names the address's shape.
    root_contract(tmp_path, '["acme.tool[deep]"]')
    assert _extensions.closure_problems(tmp_path) == [
        "[workspace] extensions lists 'acme.tool[deep]'; an entry is a name alone,"
        " and a step acme.tool declares off is turned on with"
        " [phases.<phase>.<step>] enabled = true in this contract"
    ]
    # The mount names it and reads the name alone: the step declared
    # off stays off.
    from livery.footman import _registry as registry

    with registry.capture():
        assert _extensions.mount_extensions(tmp_path) == ("acme.tool",)
    assert (
        "[workspace] extensions lists 'acme.tool[deep]'; an entry is a name alone"
        in capsys.readouterr().err
    )
    names = _steps.steps_by_name()
    assert "lint.acme" in names
    assert "typecomplete.acme" not in names


def test_a_step_declared_off_is_on_where_the_root_contract_enables_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored_steps: None
) -> None:
    from livery.footman import _registry as registry

    fake_extensions(tmp_path, monkeypatch, tool=_DECLARED_OFF)
    root_contract(tmp_path, '["acme.tool"]')
    assert _extensions.enabled_steps(tmp_path) == frozenset()
    described = _extensions.describe_extensions(tmp_path)
    assert (
        "    step typecomplete.acme (off): declared off; the root contract turns it"
        " on with [phases.typecomplete.acme] enabled = true"
    ) in described
    with registry.capture():
        _extensions.mount_extensions(tmp_path)
    names = _steps.steps_by_name()
    assert "lint.acme" in names
    assert "typecomplete.acme" not in names
    # Turned on at the step's address, it registers under the listed name.
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nextensions = ["acme.tool"]\n\n'
        "[phases.typecomplete.acme]\nenabled = true\n"
    )
    assert _extensions.enabled_steps(tmp_path) == frozenset({"typecomplete.acme"})
    assert _extensions.closure_problems(tmp_path) == []
    described = _extensions.describe_extensions(tmp_path)
    assert (
        "    step typecomplete.acme (on): declared off; the root contract turns it"
        " on with [phases.typecomplete.acme] enabled = true"
    ) in described
    with registry.capture():
        _extensions.mount_extensions(tmp_path)
    assert _steps.steps_by_name()["typecomplete.acme"].extension == "acme.tool"
    # A table turning on a step no listed extension declares off, or one
    # that is on already, refuses naming the steps declared off; a
    # table that turns nothing on is no refusal.
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nextensions = ["acme.tool"]\n\n'
        "[phases.lint.acme]\nenabled = true\n\n"
        "[phases.lint.other]\nenabled = true\n\n"
        "[phases.typecomplete.acme]\nenabled = false\n"
    )
    assert _extensions.enabled_steps(tmp_path) == frozenset({"lint.acme", "lint.other"})
    assert _extensions.closure_problems(tmp_path) == [
        "[phases.lint.acme] turns on lint.acme, which no listed extension declares"
        " off; the steps declared off are typecomplete.acme (acme.tool)",
        "[phases.lint.other] turns on lint.other, which no listed extension"
        " declares off; the steps declared off are typecomplete.acme (acme.tool)",
    ]


_BRANDED = """\
[extension]
plugin = "{package}"

[phases.lint.brand]
main = "{package}._steps:noop"

[ci.jobs.gate.brand]
entries = ["lint.brand"]
gates = true
"""


def test_an_extension_its_app_mounts_as_builtin_registers_its_declaration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    restored_steps: None,
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
        assert _steps.steps_by_name()["lint.brand"].extension == "acme.brand"
        assert "brand" in verdict_needs("gate")
    finally:
        withdraw_job("gate", "brand")
    assert "did not mount its plugin" not in capsys.readouterr().err


def test_a_root_list_is_judged_canonical_once_each_entry_is_a_name_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(
        tmp_path,
        monkeypatch,
        tool='[extension]\nrequires = ["acme.base"]\n' + _DECLARED_OFF,
        base="",
    )
    # An entry spelled beyond its name is named first, and the list is
    # neither judged canonical nor rewritten until a person fixes it.
    root_contract(tmp_path, '["acme.tool[deep]", "acme.base"]')
    (problem,) = _extensions.closure_problems(tmp_path)
    assert problem.startswith("[workspace] extensions lists 'acme.tool[deep]';")
    assert _extensions.write_extensions(tmp_path) == []
    root_contract(tmp_path, '["acme.tool", "acme.base"]')
    (problem,) = _extensions.closure_problems(tmp_path)
    assert problem.endswith(
        "acme.base is required by another listed extension; the gate's --fix"
        " rewrites it"
    )


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
    # Listed, the extension is a site, named as a step is.
    root_contract(tmp_path, '["acme.tooled"]')
    found = [r for r in requirements(tmp_path) if r.site == "extension acme.tooled"]
    assert [(r.name, r.floor) for r in found] == [("docker", "27")]
    assert "docker" in tool_names(tmp_path)
    assert "    tools: docker>=27" in _extensions.describe_extensions(tmp_path)


#: A house whose [for] table requires a tool: required while it applies.
_TOOLED_HOUSE = """\
[for."acme.python".toolroom]
requires = ["docker>=27"]
"""


def test_a_for_table_s_tools_are_required_only_while_the_table_applies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._tools import requirements, tool_names

    fake_extensions(tmp_path, monkeypatch, house=_TOOLED_HOUSE, python="", cpp="")
    # The absences first: the target unlisted, then listed while the
    # house's own entry points it at another target.
    root_contract(tmp_path, '["acme.house"]')
    assert _extensions.extension_tools(tmp_path) == {"acme.house": ()}
    assert "docker" not in tool_names(tmp_path)
    root_contract(
        tmp_path,
        '["acme.python", "acme.cpp", { name = "acme.house", for = ["acme.cpp"] }]',
    )
    assert _extensions.extension_tools(tmp_path)["acme.house"] == ()
    assert "docker" not in tool_names(tmp_path)
    # Both listed, the table applies: its tools are the house's, at the
    # house's own site, and fm extensions names them.
    root_contract(tmp_path, '["acme.python", "acme.house"]')
    found = [r for r in requirements(tmp_path) if r.site == "extension acme.house"]
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


#: A house's step for a python extension, registered while both are listed.
_HOUSE = """\
[for."acme.python".phases.lint.house]
main = "{package}._steps:noop"
"""


def _house(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A house contributing to a python extension, and that extension."""
    fake_extensions(tmp_path, monkeypatch, house=_HOUSE, python="")


def _grafted() -> bool:
    return "lint.house" in _steps.steps_by_name()


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
            '[phases.lint.needy]\nmain = "{package}._steps:noop"\n'
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
            "phases": {"lint": {"house": {"main": "acme.house._steps:noop"}}}
        },
        "acme.needy": {
            "phases": {"lint": {"needy": {"main": "acme.needy._steps:noop"}}}
        },
    }


def _mount(root: Path) -> None:
    from livery.footman import _registry as registry

    with registry.capture():
        _extensions.mount_extensions(root)


def test_a_for_naming_a_target_the_extension_declares_nothing_for_mounts_and_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored_steps: None
) -> None:
    # The mount goes on without the target, and `layering.graph`,
    # which runs inside a working fm, names the entry.
    _house(tmp_path, monkeypatch)
    fake_extensions(tmp_path, monkeypatch, cpp="")
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nextensions = [\n    "acme.cpp",\n'
        '    { name = "acme.house", for = ["acme.cpp"] },\n    "acme.python",\n]\n'
    )
    _mount(tmp_path)
    assert not _grafted()
    (problem,) = _extensions.closure_problems(tmp_path)
    assert problem == (
        "[workspace] extensions: the entry for acme.house names acme.cpp in `for`,"
        " and acme.house declares no contribution for it; remove it from `for`"
    )


def test_a_contribution_for_an_unlisted_target_never_mounts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored_steps: None
) -> None:
    _house(tmp_path, monkeypatch)
    root_contract(tmp_path, '["acme.house"]')
    _mount(tmp_path)
    assert not _grafted()
    assert _extensions.resolved_targets(tmp_path)["acme.house"] == ()
    assert "    for: acme.python" not in _extensions.describe_extensions(tmp_path)


def test_a_contribution_mounts_once_both_are_listed_whichever_is_later(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored_steps: None
) -> None:
    _house(tmp_path, monkeypatch)
    for listed in (
        '["acme.python", "acme.house"]',
        '["acme.house", "acme.python"]',
    ):
        if _grafted():
            _steps.unregister_step("lint.house")
        root_contract(tmp_path, listed)
        _mount(tmp_path)
        assert _steps.steps_by_name()["lint.house"].extension == "acme.house"
        assert _extensions.resolved_targets(tmp_path)["acme.house"] == ("acme.python",)
    assert "    for: acme.python" in _extensions.describe_extensions(tmp_path)


def test_a_workspace_listing_coverage_without_docs_mounts(tmp_path: Path) -> None:
    """The plain pass's stack mounts: pytest measures for coverage, and no site.

    Coverage is awake there, and its tables for the docs extension apply
    to nothing while docs is unlisted, so the mount refuses none of them.
    """
    import livery.workshop
    from livery.workshop._e2e import loop_stack

    if not Path(livery.workshop.__file__).resolve().is_relative_to(ROOT):
        # The release train's isolated leg installs the workshop's wheel
        # alone, and the stack's other extensions ship in wheels of their own.
        pytest.skip("the plain pass's stack names extensions this checkout installs")
    stack = loop_stack()
    assert "coverage" in stack and "docs" not in stack
    root_contract(tmp_path, "[" + ", ".join(f'"{name}"' for name in stack) + "]")
    assert _extensions.dormancy(tmp_path) == {}
    assert _extensions.resolved_targets(tmp_path)["coverage"] == ()
    from livery.footman import _registry as registry

    with registry.capture():
        assert _extensions.mount_extensions(tmp_path) == stack


#: A phase outside the gate, declared by an extension that adds no step to it.
_BOOK = """\
[phases.book]
level = "workspace"
gated = false
verb = false
"""

#: A step into that phase from a table for its declarer.
_DRAWER = """\
[for."acme.book".phases.book.draw]
main = "{package}._steps:noop"
"""


def test_a_step_into_a_phase_neither_required_nor_contributed_to_refuses_naming_both_spellings(  # noqa: E501
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored_steps: None
) -> None:
    top = '[phases.book.index]\nmain = "{package}._steps:noop"\n'
    other = '[for."acme.shelf".phases.book.shelve]\nmain = "{package}._steps:noop"\n'
    fake_extensions(
        tmp_path, monkeypatch, book=_BOOK, index=top, shelver=other, shelf=""
    )
    # A step of the top-level tables, and a step of a table for another
    # target, refuse whatever the list holds and whatever its order.
    for name, listed in (
        ("index", '["acme.index"]'),
        ("index", '["acme.book", "acme.index"]'),
        ("index", '["acme.index", "acme.book"]'),
        ("shelver", '["acme.book", "acme.shelf", "acme.shelver"]'),
        ("shelver", '["acme.shelver"]'),
    ):
        root_contract(tmp_path, listed)
        with pytest.raises(
            _FAILURES,
            match=re.escape(
                f"extension 'acme.{name}' adds steps to the book phase, which"
                " acme.book declares, and does not require acme.book; add"
                " acme.book to its [extension] requires, or move the steps into"
                " [for.acme.book]"
            ),
        ):
            _mount(tmp_path)
    # Requiring the declarer, the list names the extension alone: its set
    # takes the declarer in, which mounts first.
    fake_extensions(
        tmp_path,
        monkeypatch,
        book=_BOOK,
        index='[extension]\nrequires = ["acme.book"]\n' + top,
    )
    root_contract(tmp_path, '["acme.index"]')
    _mount(tmp_path)
    assert _steps.steps_by_name()["book.index"].extension == "acme.index"


def test_a_for_table_s_step_goes_into_its_target_s_phase_whatever_the_list_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    restored_steps: None,
) -> None:
    fake_extensions(tmp_path, monkeypatch, book=_BOOK, drawer=_DRAWER)
    # Listed without its target, the table applies to nothing: the mount
    # says nothing of its step and registers none.
    root_contract(tmp_path, '["acme.drawer"]')
    _mount(tmp_path)
    assert "book.draw" not in _steps.steps_by_name()
    assert capsys.readouterr().err == ""
    # Listed with its target, its step registers whichever of the two the
    # list names first.
    for listed in ('["acme.book", "acme.drawer"]', '["acme.drawer", "acme.book"]'):
        if "book.draw" in _steps.steps_by_name():
            _steps.unregister_step("book.draw")
        root_contract(tmp_path, listed)
        _mount(tmp_path)
        assert _steps.steps_by_name()["book.draw"].extension == "acme.drawer"


#: An owner's step, and a second extension's step at the same address
#: replacing it; ``{requires}`` stands for the second's identity table.
_OWNER = '[phases.lint.acme]\nmain = "{package}._steps:noop"\n'
_TAKER = """\
{requires}
[phases.lint.acme]
main = "{package}._steps:noop"

[phases.lint.acme.replaces]
"acme.owner:lint.acme" = "it reads faster"
"""


def test_a_replacement_of_an_unrequired_extensions_step_or_file_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored_steps: None
) -> None:
    from livery.workshop._shipped_files import shipped

    # The refusals first. A step replacing a step of an extension it does
    # not require refuses at mount, whatever the list's order.
    fake_extensions(
        tmp_path, monkeypatch, owner=_OWNER, taker=_TAKER.replace("{requires}", "")
    )
    for listed in ('["acme.owner", "acme.taker"]', '["acme.taker", "acme.owner"]'):
        root_contract(tmp_path, listed)
        with pytest.raises(
            _FAILURES,
            match=re.escape(
                "extension 'acme.taker': lint.acme replaces acme.owner:lint.acme,"
                " and acme.taker does not require acme.owner; add acme.owner to"
                " its [extension] requires, or move the step into"
                " [for.acme.owner]"
            ),
        ):
            _mount(tmp_path)
    # So does a package step deleting one.
    deleter = (
        '[phases.build.taker]\nmain = "{package}._steps:noop"\n\n'
        '[phases.build.taker.deletes]\n"acme.owner:build.owner" = "it builds twice"\n'
    )
    fake_extensions(
        tmp_path,
        monkeypatch,
        owner='[phases.build.owner]\nmain = "{package}._steps:noop"\n',
        taker=deleter,
    )
    root_contract(tmp_path, '["acme.owner", "acme.taker"]')
    with pytest.raises(
        _FAILURES, match=re.escape("build.taker deletes acme.owner:build.owner, and")
    ):
        _mount(tmp_path)
    # And a shipped file replaced, or deleted, without its owner required.
    requires = '[extension]\nrequires = ["acme.owner"]\n'
    for table in (
        '[replaces]\n"acme.owner:x.txt" = "ours"\n',
        '[deletes]\n"acme.owner:x.txt" = "none"\n',
    ):
        fake_extensions(tmp_path, monkeypatch, owner="", taker=table)
        for name in ("owner", "taker"):
            shipped_file = (
                tmp_path / "site" / "acme" / name / "content" / "root" / "x.txt"
            )
            shipped_file.parent.mkdir(parents=True, exist_ok=True)
            shipped_file.write_text(f"{name}\n")
        root_contract(tmp_path, '["acme.owner", "acme.taker"]')
        verb = table.partition("]")[0].lstrip("[")
        with pytest.raises(
            _FAILURES,
            match=re.escape(
                f"acme.taker {verb} acme.owner:x.txt in its extension.toml, and does"
                " not require acme.owner; add acme.owner to its [extension] requires"
            ),
        ):
            shipped(tmp_path)
        # Requiring the owner, the taker's declaration stands.
        fake_extensions(tmp_path, monkeypatch, owner="", taker=requires + table)
        fragments, _order = shipped(tmp_path)
        assert any(
            fragment.owner == "acme.taker"
            and "acme.owner:x.txt" in (fragment.replaces, fragment.deletes)
            for fragment in fragments
        )
    # Requiring the owner, the step takes the address from a list naming
    # the taker alone; a table for the owner does without requires.
    fake_extensions(
        tmp_path,
        monkeypatch,
        owner=_OWNER,
        taker=_TAKER.replace("{requires}", requires),
    )
    root_contract(tmp_path, '["acme.taker"]')
    _mount(tmp_path)
    assert _steps.steps_by_name()["lint.acme"].extension == "acme.taker"
    _steps.unregister_step("lint.acme")
    grafted = (
        '[for."acme.owner".phases.lint.acme]\nmain = "{package}._steps:noop"\n\n'
        '[for."acme.owner".phases.lint.acme.replaces]\n'
        '"acme.owner:lint.acme" = "it reads faster"\n'
    )
    fake_extensions(tmp_path, monkeypatch, owner=_OWNER, taker=grafted)
    root_contract(tmp_path, '["acme.taker", "acme.owner"]')
    _mount(tmp_path)
    assert _steps.steps_by_name()["lint.acme"].extension == "acme.taker"


#: An extension that judges what another measures for it: dormant until
#: a listed extension contributes a table to it, with a step, a CI entry
#: and a judge of its own, and a step it adds to a python extension.
_SLEEPER = """\
[extension]
dormant-until = "measures"

[phases.lint.sleeper]
main = "{package}._steps:noop"

[ci.jobs.gate.check]
entries = ["acme.leg"]

[ci.jobs.gate.gate]
judges = ["acme.union"]

[for."acme.python".phases.lint.sleepy]
main = "{package}._steps:noop"
"""

#: What wakes it: a table contributed to it.
_METER = """\
[for."acme.sleeper"]
"""


def test_a_dormant_extension_registers_no_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored_steps: None
) -> None:
    from livery.workshop import _points

    fake_extensions(tmp_path, monkeypatch, sleeper=_SLEEPER, meter=_METER, python="")

    def joined() -> list[tuple[str, tuple[str, ...]]]:
        return [
            (item.job.name, tuple(entry.task for entry in item.entries))
            for item in _points.joined_jobs("gate")
            if item.extension == "acme.sleeper"
        ]

    # Listed with nothing that contributes to it, the extension is
    # dormant: no step, no entry, no judge, no contribution of its own,
    # and the list says what would wake it.
    root_contract(tmp_path, '["acme.python", "acme.sleeper"]')
    waits = "nothing listed measures; acme.meter would"
    assert _extensions.dormancy(tmp_path) == {"acme.sleeper": waits}
    _mount(tmp_path)
    assert joined() == []
    assert not {"lint.sleeper", "lint.sleepy"} & set(_steps.steps_by_name())
    assert f"    dormant: {waits}" in _extensions.describe_extensions(tmp_path)
    # A listing that contributes wakes it, wherever it stands in the list.
    root_contract(tmp_path, '["acme.python", "acme.sleeper", "acme.meter"]')
    assert _extensions.dormancy(tmp_path) == {}
    _mount(tmp_path)
    assert joined() == [("check", ("acme.leg",)), ("gate", ("acme.union",))]
    assert {"lint.sleeper", "lint.sleepy"} <= set(_steps.steps_by_name())


def test_a_dormant_extension_nothing_installed_would_wake_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An installed extension whose declaration cannot be read is not the
    # workspace's to judge while it is unlisted: it wakes nothing, and
    # the line is said all the same.
    fake_extensions(
        tmp_path, monkeypatch, sleeper=_SLEEPER, python="", odd="[unknown]\n"
    )
    root_contract(tmp_path, '["acme.python", "acme.sleeper"]')
    assert _extensions.dormancy(tmp_path) == {
        "acme.sleeper": "nothing listed measures; no installed extension would"
    }


_HOUSE_OFF = """\
[for."acme.python".phases.lint.house]
main = "{package}._steps:noop"
enabled = false
"""


def test_a_grafted_step_declared_off_registers_where_the_root_contract_enables_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored_steps: None
) -> None:
    fake_extensions(tmp_path, monkeypatch, house=_HOUSE_OFF, python="")
    # The fallback first: listed without the table, the step stays off.
    root_contract(tmp_path, '["acme.python", "acme.house"]')
    _mount(tmp_path)
    assert not _grafted()
    # Turned on at its address, it registers whichever of the two the
    # list names first.
    for listed in (
        '["acme.python", "acme.house"]',
        '["acme.house", "acme.python"]',
    ):
        if _grafted():
            _steps.unregister_step("lint.house")
        (tmp_path / "workshop.toml").write_text(
            f"[workspace]\nextensions = {listed}\n\n"
            "[phases.lint.house]\nenabled = true\n"
        )
        _mount(tmp_path)
        assert _steps.steps_by_name()["lint.house"].extension == "acme.house"


def test_a_contribution_whose_reference_does_not_resolve_refuses_naming_its_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(
        tmp_path, monkeypatch, house=_HOUSE.replace(":noop", ":absent"), python=""
    )
    root_contract(tmp_path, '["acme.python", "acme.house"]')
    with pytest.raises(
        RuntimeError,
        match=r'for\."acme\.python"\.phases\.lint\.house\.main names absent',
    ):
        _mount(tmp_path)


def test_a_root_entry_s_for_opts_out_and_one_naming_its_default_is_written_as_its_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored_steps: None
) -> None:
    _house(tmp_path, monkeypatch)
    fake_extensions(tmp_path, monkeypatch, cpp="")
    # The refusals first: a target no list holds, and one the house
    # declares nothing for.
    root_contract(tmp_path, '[{ name = "acme.house", for = ["acme.cpp"] }]')
    (problem,) = _extensions.closure_problems(tmp_path)
    assert problem == (
        "[workspace] extensions: the entry for acme.house names acme.cpp in `for`,"
        " and does not list it; list it, or remove it from `for`"
    )
    # Cooperation is the default: listed by name, the house contributes
    # to every target it declares a table for.
    root_contract(tmp_path, '["acme.house", "acme.python"]')
    assert _extensions.closure_problems(tmp_path) == []
    assert _extensions.resolved_targets(tmp_path)["acme.house"] == ("acme.python",)
    _mount(tmp_path)
    assert _grafted()
    _steps.unregister_step("lint.house")
    # A `for` leaving the target out is the workspace's opt-out: the fix
    # keeps it, and nothing grafts.
    root_contract(tmp_path, '[{ name = "acme.house", for = [] }, "acme.python"]')
    assert _extensions.closure_problems(tmp_path) == []
    assert _extensions.write_extensions(tmp_path) == []
    assert _extensions.resolved_targets(tmp_path)["acme.house"] == ()
    _mount(tmp_path)
    assert not _grafted()
    # A `for` naming exactly the default says no more than the name, and
    # the fix writes the name in its place, its comment kept.
    contract = tmp_path / "workshop.toml"
    contract.write_text(
        "[workspace]\nextensions = [\n"
        '    { name = "acme.house", for = ["acme.python"] },  # the opinions\n'
        '    "acme.python",\n]\n'
    )
    (problem,) = _extensions.closure_problems(tmp_path)
    assert problem == (
        '[workspace] extensions is [{ name = "acme.house", for = ["acme.python"] },'
        ' "acme.python"], and its canonical list is ["acme.house", "acme.python"];'
        " the entry for acme.house names its default targets, which the name"
        " alone says; the gate's --fix rewrites it"
    )
    assert _extensions.write_extensions(tmp_path) == [
        '  layering: [workspace] extensions is ["acme.house", "acme.python"], its'
        " canonical list"
    ]
    assert contract.read_text() == (
        "[workspace]\nextensions = [\n"
        '    "acme.house",  # the opinions\n'
        '    "acme.python",\n]\n'
    )


def test_an_implied_root_entry_carrying_for_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored_steps: None
) -> None:
    _house(tmp_path, monkeypatch)
    fake_extensions(
        tmp_path, monkeypatch, brand='[extension]\nrequires = ["acme.house"]\n'
    )
    # Its `for` is the default: the entry says nothing its requirer's
    # does not, so the list leaves it out.
    root_contract(
        tmp_path,
        '[{ name = "acme.house", for = ["acme.python"] }, "acme.brand", "acme.python"]',
    )
    (problem,) = _extensions.closure_problems(tmp_path)
    assert problem.endswith(
        'its canonical list is ["acme.brand", "acme.python"]; acme.house is'
        " required by another listed extension; the gate's --fix rewrites it"
    )
    # An opt-out has no other place to be written: the implied entry
    # carrying it stays, and the house contributes nothing.
    listed = '[{ name = "acme.house", for = [] }, "acme.brand", "acme.python"]'
    root_contract(tmp_path, listed)
    assert _extensions.closure_problems(tmp_path) == []
    assert _extensions.write_extensions(tmp_path) == []
    _mount(tmp_path)
    assert not _grafted()


def test_rewriters_run_in_composition_order_and_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored_steps: None
) -> None:
    def fixer(phase: str, step: str, after: str = "") -> str:
        return (
            f"[phases.{phase}.{step}]\n"
            'main = "{package}._steps:noop"\n'
            'fix = "{package}._steps:noop"\n'
            + (f'after = ["{after}"]\n' if after else "")
        )

    fake_extensions(
        tmp_path,
        monkeypatch,
        alpha=fixer("format", "alpha"),
        beta=fixer("lint", "beta", after="format.gamma"),
        gamma=fixer("format", "gamma"),
    )
    fixing = PhaseContext(root=tmp_path, packages=(), fix=True)
    names = ("format.alpha", "lint.beta", "format.gamma")
    for listed in (
        '["acme.gamma", "acme.beta", "acme.alpha"]',
        '["acme.alpha", "acme.beta", "acme.gamma"]',
    ):
        for name in names:
            if name in _steps.steps_by_name():
                _steps.unregister_step(name)
        root_contract(tmp_path, listed)
        _mount(tmp_path)
        # Registered in composition order, whatever the list's.
        assert [name for name in _steps.steps_by_name() if name in names] == [
            "format.alpha",
            "lint.beta",
            "format.gamma",
        ]
        # Run under --fix in that order, each after the steps it names.
        assert [name for name in _steps.rewriters(fixing) if name in names] == [
            "format.alpha",
            "format.gamma",
            "lint.beta",
        ]


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
