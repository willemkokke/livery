"""The step registry: refusals first, then a fake step runs, narrows and skips."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.workshop._packages import Package
from livery.workshop._phases import PhaseContext
from livery.workshop._steps import (
    NONE,
    PACKAGE,
    PACKAGES,
    PATHS,
    WORKSPACE,
    StepRecord,
    judges,
    phases,
    register_step,
    rewriters,
    run_step,
    step_for,
    step_names,
    unregister_step,
)
from workshop_python_steps import python_steps_fixture  # noqa: F401

_FAILURES = (BaseException,)


@pytest.fixture
def restored_registries():
    from livery.workshop import _kinds, _steps

    steps = _steps.snapshot()
    kinds = dict(_kinds._KINDS)
    yield
    _steps.restore(steps)
    _kinds._KINDS.clear()
    _kinds._KINDS.update(kinds)


def _package(tmp_path: Path, name: str, *extensions: str) -> Package:
    directory = tmp_path / "packages" / name
    directory.mkdir(parents=True, exist_ok=True)
    return Package(
        directory=directory,
        path=f"packages/{name}",
        name=f"acme-{name}",
        depends=(),
        extensions=extensions,
    )


def _noop(ctx: PhaseContext) -> None:
    del ctx


# The refusals first.


def test_a_record_with_an_unknown_scope_or_narrowing_refuses(restored_registries):
    with pytest.raises(_FAILURES, match="scope is 'workspace' or 'package'"):
        register_step(StepRecord("odd", "lint", _noop, scope="sometimes"))
    with pytest.raises(_FAILURES, match="narrowing is 'paths', 'packages' or 'none'"):
        register_step(StepRecord("odd", "lint", _noop, narrowing="somehow"))


def test_a_package_step_naming_no_extension_refuses(restored_registries):
    with pytest.raises(_FAILURES, match="names no extension"):
        register_step(StepRecord("odd", "lint", _noop, scope=PACKAGE))


def test_an_unknown_step_name_refuses_naming_the_registry(restored_registries):
    with pytest.raises(_FAILURES, match=r"not a registered step; steps: drift\.check"):
        step_for("nothing")
    with pytest.raises(_FAILURES, match="not a registered step"):
        unregister_step("nothing")


def test_selected_files_refuses_a_context_that_names_no_step(tmp_path: Path) -> None:
    # A context built by hand names no step, so the question has no
    # subject; a step's run is handed one that does.
    from livery.workshop import selected_files

    with pytest.raises(_FAILURES, match=r"names none: .* sets PhaseContext\.step"):
        selected_files(PhaseContext(root=tmp_path, packages=()))


def test_a_step_is_handed_a_context_naming_it_and_asks_what_it_judges(
    restored_registries, tmp_path: Path
) -> None:
    from livery.workshop import Changes, selected_files
    from livery.workshop._influence import Inputs

    seen: list[tuple[str, frozenset[str] | None]] = []

    def asks(ctx: PhaseContext) -> None:
        seen.append((ctx.step, selected_files(ctx)))

    register_step(
        StepRecord("asker", "lint", asks, narrowing=NONE, inputs=Inputs(("docs/**",)))
    )
    # A run that knows nothing of the changes: every file it reads.
    run_step("lint.asker", PhaseContext(root=tmp_path, packages=()))
    assert seen == [("lint.asker", None)]
    # A run that knows: the changed files among those it reads.
    seen.clear()
    changes = Changes(tmp_path, ("docs/a.md", "src/x.py"))
    run_step("lint.asker", PhaseContext(root=tmp_path, packages=(), changes=changes))
    assert seen == [("lint.asker", frozenset({"docs/a.md"}))]


# Then the fake step: registered, run, narrowed, skipped by name.


def test_a_registered_step_runs_narrows_and_is_replaced_by_name(
    restored_registries, tmp_path: Path
) -> None:
    seen: list[tuple[str, tuple[str, ...] | None]] = []

    def spy(ctx: PhaseContext) -> None:
        seen.append(
            ("spy", None if ctx.subset is None else tuple(p.path for p in ctx.subset))
        )

    def fixer(ctx: PhaseContext) -> None:
        seen.append(("fix", None))

    register_step(StepRecord("spy", "lint", spy, narrowing=PATHS, fix=fixer))
    assert "lint.spy" in step_names() and "lint" in phases()
    member = _package(tmp_path, "one", "python")
    whole = PhaseContext(root=tmp_path, packages=(member,))
    assert "lint.spy" in judges(whole)
    run_step("lint.spy", whole)
    assert seen == [("spy", None)]
    # Narrowed to the subset it is handed.
    seen.clear()
    scoped = PhaseContext(root=tmp_path, packages=(member,), subset=(member,))
    run_step("lint.spy", scoped)
    assert seen == [("spy", ("packages/one",))]
    # Under --fix the rewriter runs, and is not judged again.
    seen.clear()
    fixing = PhaseContext(root=tmp_path, packages=(member,), fix=True)
    assert rewriters(fixing)[-1] == "lint.spy"
    run_step("lint.spy", fixing, fix=True)
    assert seen == [("fix", None)]
    assert "lint.spy" not in judges(fixing)
    # Re-registering the name replaces the record: how an extension swaps a tool.
    register_step(StepRecord("spy", "lint", _noop))
    assert step_for("lint.spy").narrowing == NONE
    unregister_step("lint.spy")
    assert "lint.spy" not in step_names()


def test_a_package_step_runs_for_its_extensions_alone_and_skips_by_name(
    restored_registries,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    python_steps: object,
) -> None:
    from livery.workshop._steps import judged_by

    ran: list[str] = []

    def spy(ctx: PhaseContext) -> None:
        assert ctx.package is not None
        ran.append(ctx.package.name)

    register_step(StepRecord("probe", "lint", spy, scope=PACKAGE, extensions=("cpp",)))
    py = _package(tmp_path, "member", "python")
    native = _package(tmp_path, "native", "cmake", "conan")
    # The probe judges the native package alone, announcing it by name;
    # a workspace of python packages schedules no probe at all.
    assert "lint.probe" in judges(PhaseContext(root=tmp_path, packages=(py, native)))
    assert "lint.probe" not in judges(PhaseContext(root=tmp_path, packages=(py,)))
    run_step("lint.probe", PhaseContext(root=tmp_path, packages=(py, native)))
    assert ran == ["acme-native"]
    assert (
        "  lint.probe: packages/native runs (a cpp package)" in capsys.readouterr().out
    )
    # A step naming none of the extensions the native member holds skips
    # it by name, as the gate prints it.
    assert judged_by(step_for("test.fake"), (py, native)) == (py,)
    assert (
        "test.fake: packages/native skips (not a python package)"
        in capsys.readouterr().out
    )


def test_a_record_names_its_phase_and_step() -> None:
    record = StepRecord("fake", "lint", _noop)
    assert (record.name, record.phase, record.step) == ("lint.fake", "lint", "fake")
    # A record built in code: the step's keys at their defaults.
    assert (
        record.provides,
        record.reads,
        record.isolated,
        record.enabled,
        record.replaces,
        record.deletes,
    ) == ((), (), None, True, (), ())


def test_a_step_after_another_phases_step_runs_once_before_both_in_the_walk(
    restored_registries, tmp_path: Path
) -> None:
    from livery.workshop._phases import run_phase
    from livery.workshop._steps import with_earlier

    ran: list[str] = []

    def spy(ctx: PhaseContext) -> None:
        ran.append(ctx.step)

    # One earlier step, named in `after` by two later ones.
    register_step(StepRecord("probe", "build", spy))
    register_step(StepRecord("probe", "lint", spy, after=("build.probe",)))
    register_step(StepRecord("probe", "test", spy, after=("build.probe",)))
    ctx = PhaseContext(root=tmp_path, packages=())
    # A step run by hand runs its own body alone: the caller orders
    # the steps it runs.
    run_step("lint.probe", ctx)
    assert ran == ["lint.probe"]
    # The gate's walk brings the earlier step in, once, and each later
    # one starts after it.
    ran.clear()
    records = with_earlier(("lint.probe", "test.probe"))
    assert [record.name for record in records] == [
        "build.probe",
        "lint.probe",
        "test.probe",
    ]
    run_phase(ctx, records)
    assert sorted(ran) == ["build.probe", "lint.probe", "test.probe"]
    assert ran[0] == "build.probe"
    # An after naming no registered step refuses naming the registry.
    register_step(StepRecord("probe", "format", spy, after=("build.ghost",)))
    with pytest.raises(_FAILURES, match=r"'build\.ghost' is not a registered step"):
        with_earlier(("format.probe",))


def test_a_tests_only_step_runs_when_a_packages_change_is_its_tests_alone(
    restored_registries, tmp_path: Path
) -> None:
    ran: list[tuple[str, tuple[str, ...]]] = []

    def spy(ctx: PhaseContext) -> None:
        assert ctx.package is not None
        ran.append((ctx.step, ctx.selection))

    python = ("python",)
    register_step(StepRecord("probe", "lint", spy, scope=PACKAGE, extensions=python))
    register_step(
        StepRecord(
            "probe", "test", spy, scope=PACKAGE, extensions=python, tests_only=True
        )
    )
    member = _package(tmp_path, "one", "python")
    suite = {"packages/one": ("packages/one/tests/test_one.py",)}
    confined = PhaseContext(
        root=tmp_path, packages=(member,), subset=(member,), tests=suite
    )
    # The narrowing first: a change confined to the package's tests leaves
    # a step that is not tests-only nothing to judge, by name and when run.
    assert "lint.probe" not in judges(confined)
    run_step("lint.probe", confined)
    assert ran == []
    # The tests-only step judges the package, handed the selection
    # relative to it.
    assert "test.probe" in judges(confined)
    run_step("test.probe", confined)
    assert ran == [("test.probe", ("tests/test_one.py",))]
    # A scoped gate whose change reaches past the tests runs both.
    ran.clear()
    wider = PhaseContext(root=tmp_path, packages=(member,), subset=(member,))
    assert {"lint.probe", "test.probe"} <= set(judges(wider))
    run_step("lint.probe", wider)
    run_step("test.probe", wider)
    assert ran == [("lint.probe", ()), ("test.probe", ())]


def test_the_constants_name_the_vocabulary() -> None:
    assert (WORKSPACE, PACKAGE) == ("workspace", "package")
    assert (PATHS, PACKAGES, NONE) == ("paths", "packages", "none")


# A step belongs to a declared phase, and one extension declares it.


def test_a_step_of_an_undeclared_phase_refuses_naming_the_declared_ones(
    restored_registries,
) -> None:
    from livery.workshop._phases import declared_phases

    listed = ", ".join(declared_phases())
    with pytest.raises(_FAILURES) as raised:
        register_step(StepRecord("acme", "divination", _noop))
    assert str(raised.value) == (
        f"step 'divination.acme': divination is not a declared phase; the phases"
        f" are {listed}"
    )
    assert "divination.acme" not in step_names()


def test_two_extensions_declaring_one_step_refuse_unless_one_replaces_or_deletes_it(
    restored_registries,
) -> None:
    import re

    register_step(StepRecord("acme", "lint", _noop, extension="acme.one"))
    refusal = re.escape(
        "step lint.acme is declared by both acme.one and acme.two; two extensions"
        " declare one step unless the later names acme.one:lint.acme in replaces"
        " or deletes, with a reason"
    )
    with pytest.raises(_FAILURES, match=refusal):
        register_step(StepRecord("acme", "lint", _noop, extension="acme.two"))
    # Naming another owner's step excuses nothing.
    with pytest.raises(_FAILURES, match=refusal):
        register_step(
            StepRecord(
                "acme",
                "lint",
                _noop,
                extension="acme.two",
                replaces=(("acme.other:lint.acme", "it reads faster"),),
            )
        )
    assert step_for("lint.acme").extension == "acme.one"
    # Naming the registered step, in replaces or in deletes, takes the address.
    register_step(
        StepRecord(
            "acme",
            "lint",
            _noop,
            extension="acme.two",
            replaces=(("acme.one:lint.acme", "it reads faster"),),
        )
    )
    assert step_for("lint.acme").extension == "acme.two"
    register_step(
        StepRecord(
            "acme",
            "lint",
            _noop,
            extension="acme.three",
            deletes=(("acme.two:lint.acme", "it reads nothing"),),
        )
    )
    assert step_for("lint.acme").extension == "acme.three"
    # The same extension registers its step again, as a mount run again does.
    register_step(StepRecord("acme", "lint", _noop, extension="acme.three"))
    assert step_for("lint.acme").extension == "acme.three"


def test_a_step_of_an_ungated_phase_stays_out_of_the_gate(
    restored_registries, tmp_path: Path
) -> None:
    from livery.workshop._steps import verb_tree

    # The base declares configure outside the gate and without a verb;
    # lint is gated with one.
    register_step(StepRecord("acme", "configure", _noop, fix=_noop))
    register_step(StepRecord("acme", "lint", _noop, fix=_noop))
    judging = PhaseContext(root=tmp_path, packages=())
    fixing = PhaseContext(root=tmp_path, packages=(), fix=True)
    assert "lint.acme" in judges(judging) and "lint.acme" in rewriters(fixing)
    assert "configure.acme" not in judges(judging)
    assert "configure.acme" not in rewriters(fixing)
    assert "configure" not in verb_tree() and "lint" in verb_tree()
    # Run by name, the step still runs: its verb is its engine's to make.
    seen: list[str] = []
    register_step(StepRecord("acme", "configure", lambda ctx: seen.append(ctx.step)))
    run_step("configure.acme", judging)
    assert seen == ["configure.acme"]


# The options a package may set, refusals first.


def _member_with(tmp_path: Path, tables: str) -> Package:
    from livery.workshop._packages import discover_packages

    member = tmp_path / "packages" / "x"
    member.mkdir(parents=True, exist_ok=True)
    member.joinpath("workshop.toml").write_text(
        f'extensions = ["python"]\nname = "livery-x"\n{tables}'
    )
    member.joinpath("pyproject.toml").write_text('[project]\nname = "livery-x"\n')
    (package,) = discover_packages(tmp_path)
    return package


def test_an_option_on_an_unknown_address_or_an_undeclared_option_refuses(
    tmp_path: Path, python_steps: object
) -> None:
    from livery.workshop._steps import option_problems, option_value, step_for

    package = _member_with(tmp_path, "[phases.nothing]\nenabled = false\n")
    (problem,) = option_problems((package,))
    assert problem.startswith(
        "packages/x/workshop.toml: [phases.nothing] names no phase a registered"
        " step has; the phases are"
    )
    package = _member_with(tmp_path, "[phases.test.nothing]\nenabled = false\n")
    (problem,) = option_problems((package,))
    assert problem.startswith(
        "packages/x/workshop.toml: [phases.test.nothing] reaches no registered"
        " step; the steps are"
    )
    package = _member_with(tmp_path, "[phases.test.fake]\nworkers = 3\n")
    (problem,) = option_problems((package,))
    assert "sets 'workers', which no step it reaches declares" in problem
    package = _member_with(tmp_path, '[phases.test.fake]\nparallel = "no"\n')
    (problem,) = option_problems((package,))
    assert problem.endswith("[phases.test.fake] parallel is bool, not 'no'")
    with pytest.raises(_FAILURES, match="parallel is bool"):
        option_value(step_for("test.fake"), package, "parallel")
    with pytest.raises(_FAILURES, match="declares no option 'workers'"):
        option_value(step_for("test.fake"), package, "workers")


def test_a_package_option_table_in_the_check_spelling_refuses_naming_the_step_spelling(
    tmp_path: Path,
) -> None:
    said = {
        "[checks.fake.test]\nparallel = false\n": (
            "[checks.fake.test] is the check tables' spelling; the step's options"
            " are [phases.test.fake]"
        ),
        "[checks.fake]\nparallel = false\n": (
            "[checks.fake] is the check tables' spelling; a step's options are"
            " [phases.<phase>.fake], and [phases.<phase>] reaches every step of"
            " the phase"
        ),
        "checks = 3\n": (
            "[checks] is the check tables' spelling; a step's options are"
            " [phases.<phase>.<step>]"
        ),
        "[roles.test]\nparallel = false\n": (
            "[roles.test] is the check tables' spelling; every step of the phase"
            " takes [phases.test]"
        ),
        "roles = 3\n": (
            "[roles] is the check tables' spelling; every step of a phase takes"
            " [phases.<phase>]"
        ),
    }
    for text, line in said.items():
        with pytest.raises(_FAILURES) as caught:
            _member_with(tmp_path, text)
        assert line in str(caught.value), text
        # The refusal comes before the contract's, which cannot name the
        # spelling.
        assert "has no key" not in str(caught.value), text


def test_the_deeper_table_wins_key_by_key(tmp_path: Path, python_steps: object) -> None:
    from livery.workshop._steps import option_problems, option_value, step_for

    record = step_for("test.fake")
    # The fallback first: nothing set, the default.
    package = _member_with(tmp_path, "")
    assert option_value(record, package, "parallel") is True
    package = _member_with(tmp_path, "[phases.test]\nparallel = false\n")
    assert option_value(record, package, "parallel") is False
    # The step's table outranks the phase's.
    package = _member_with(
        tmp_path,
        "[phases.test]\nenabled = false\nparallel = true\n"
        "[phases.test.fake]\nparallel = false\n",
    )
    assert option_value(record, package, "parallel") is False
    # Key by key: the phase's `enabled` still reaches the step.
    assert option_value(record, package, "enabled") is False
    assert option_problems((package,)) == []


def test_a_phase_wide_option_table_reaches_every_step_of_the_phase(
    restored_registries, tmp_path: Path, python_steps: object
) -> None:
    from livery.workshop._steps import (
        option_addresses,
        option_problems,
        option_value,
        step_for,
    )

    # A second step of the test phase, beside the fake one.
    register_step(
        StepRecord("other", "test", _noop, scope=PACKAGE, extensions=("python",))
    )
    record = step_for("test.fake")
    assert option_addresses(record) == ("phases.test", "phases.test.fake")
    package = _member_with(tmp_path, "[phases.test]\nenabled = false\n")
    assert package.step_options == (("phases.test", (("enabled", False),)),)
    assert option_problems((package,)) == []
    # Every step of the phase, and no step of another.
    assert option_value(record, package, "enabled") is False
    assert option_value(step_for("test.other"), package, "enabled") is False
    assert option_value(step_for("lint.fake"), package, "enabled") is True
    # An option one step of the phase declares reaches it through the
    # phase's table, and refuses for a step that does not declare it.
    package = _member_with(tmp_path, "[phases.test]\nparallel = false\n")
    assert option_value(record, package, "parallel") is False
    with pytest.raises(_FAILURES, match="declares no option 'parallel'"):
        option_value(step_for("test.other"), package, "parallel")
    # The step's own table beside the phase's: both read, by address.
    package = _member_with(
        tmp_path,
        "[phases.test]\nparallel = false\n[phases.test.fake]\nenabled = false\n",
    )
    assert package.step_options == (
        ("phases.test", (("parallel", False),)),
        ("phases.test.fake", (("enabled", False),)),
    )
    assert option_problems((package,)) == []


def test_a_phases_table_off_the_shape_refuses(tmp_path: Path) -> None:
    from livery.workshop._packages import discover_packages

    member = tmp_path / "packages" / "x"
    member.mkdir(parents=True)
    member.joinpath("workshop.toml").write_text(
        'extensions = ["python"]\nname = "livery-x"\nphases = 3\n'
    )
    member.joinpath("pyproject.toml").write_text('[project]\nname = "livery-x"\n')
    with pytest.raises(
        _FAILURES, match=r"phases is an integer \(3\); it takes a table"
    ):
        discover_packages(tmp_path)
    member.joinpath("workshop.toml").write_text(
        'extensions = ["python"]\nname = "livery-x"\n[phases]\ntest = 3\n'
    )
    with pytest.raises(
        _FAILURES, match=r"phases.test is an integer \(3\); it takes a table"
    ):
        discover_packages(tmp_path)


def test_a_package_turns_a_step_off_and_is_skipped_by_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], python_steps: object
) -> None:
    from livery.workshop._steps import enabled, option_value, step_for

    package = _member_with(tmp_path, "[phases.test.fake]\nenabled = false\n")
    record = step_for("test.fake")
    assert option_value(record, package, "enabled") is False
    assert option_value(step_for("lint.fake"), package, "enabled") is True
    assert enabled("test.fake", (package,)) == ()
    assert "test.fake: packages/x skips (turned off in packages/x/workshop.toml)" in (
        capsys.readouterr().out
    )
    assert enabled("lint.fake", (package,)) == (package,)


def test_a_code_step_and_a_words_step_share_one_option_table(
    restored_registries, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop._steps import Option, enabled, option_addresses, option_value
    from livery.workshop._words import Command, Words

    level = Option("level", "int", 1, "how far the probe reads")
    words = Words(("acme", "judge"))
    coded = StepRecord(
        "probe", "lint", _noop, scope=PACKAGE, extensions=("python",), options=(level,)
    )
    spoken = StepRecord(
        "probe",
        "lint",
        Command(words, "lint.probe"),
        scope=PACKAGE,
        extensions=("python",),
        options=(level,),
        tools=("acme",),
        words=words,
    )
    # The refusals first: a value of the wrong type and a key the step does
    # not declare refuse by the same line whichever way the step is written.
    package = _member_with(tmp_path, '[phases.lint.probe]\nlevel = "deep"\n')
    for record in (coded, spoken):
        with pytest.raises(_FAILURES, match=r"\[phases\.lint\.probe\] level is int"):
            option_value(record, package, "level")
        with pytest.raises(_FAILURES, match="declares no option 'depth'"):
            option_value(record, package, "depth")
    # The table is addressed by the step's name, not by how the step runs:
    # the same tables reach both, and one setting answers both.
    assert (
        option_addresses(coded)
        == option_addresses(spoken)
        == ("phases.lint", "phases.lint.probe")
    )
    package = _member_with(
        tmp_path, "[phases.lint.probe]\nlevel = 2\nenabled = false\n"
    )
    assert option_value(coded, package, "level") == 2
    assert option_value(spoken, package, "level") == 2
    # And `enabled` turns either off by the same line; the words step's
    # tool is never called for a package that turned it off.
    for record in (coded, spoken):
        register_step(record)
        assert enabled("lint.probe", (package,)) == ()
        capsys.readouterr()
        run_step("lint.probe", PhaseContext(root=tmp_path, packages=(package,)))
        assert (
            "lint.probe: packages/x skips (turned off in packages/x/workshop.toml)"
            in capsys.readouterr().out
        )
