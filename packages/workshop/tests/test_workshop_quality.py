"""The scoped gate: every verb it schedules actually runs.

The property pinned here is execution, not exit: a built step is not
a run step under footman's block contract, and a gate whose verbs are
built and dropped exits green having checked nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import workshop_python_steps as fake_steps
from livery.workshop import _phases, _quality
from livery.workshop._ci_context import LEG_VARIABLE
from livery.workshop._packages import Package
from livery.workshop._phases import PhaseContext
from livery.workshop._shard import ONE, Shard
from workshop_python_steps import python_steps_fixture  # noqa: F401

# The python steps that judge and never rewrite, sorted, as the fakes
# below record them: one type check per type checker.
PYTHON_JUDGES = (
    "test.fake",
    "typecheck.fake",
)

# The base's layering steps over the sources: the one that rewrites,
# and the ones that only judge, sorted.
LAYERING_FIXER = "layering.sibling-references"
LAYERING_JUDGES = ("layering.base-imports-no-extension",)
LAYERING_STEPS = (LAYERING_FIXER, *LAYERING_JUDGES)


def _package(tmp_path: Path) -> Package:
    member = tmp_path / "packages" / "one"
    (member / "tests").mkdir(parents=True)
    # A file to judge: a step never names a directory with none.
    (member / "tests" / "test_one.py").write_text("")
    (member / "pyproject.toml").write_text(
        '[project]\nname = "livery-one"\nversion = "0.1.0"\n'
    )
    (member / "workshop.toml").write_text(
        'extensions = ["python"]\nname = "livery-one"\n'
    )
    return Package(
        directory=member,
        path="packages/one",
        name="livery-one",
        depends=(),
        extensions=("python",),
    )


def _record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[list[str], list[dict[str, object]]]:
    ran: list[str] = []
    calls: list[dict[str, object]] = []

    def named(name: str):
        def body(*args: object, **kwargs: object) -> None:
            ran.append(name)
            calls.append({"verb": name, "args": args, **kwargs})

        return body

    def rule(step: str, *args: object, **kwargs: object) -> list[str]:
        ran.append(f"layering.{step}")
        calls.append({"verb": f"layering.{step}", "args": args, **kwargs})
        return []

    monkeypatch.setattr(_quality, "workspace_root", lambda: tmp_path)
    monkeypatch.setattr(
        "livery.workshop._packages.verify_graph", named("layering.graph")
    )
    monkeypatch.setattr("livery.workshop._packages.rule_problems", rule)
    monkeypatch.setattr(_quality, "drift_check", named("drift.check"))
    monkeypatch.setattr(
        "livery.workshop._provenance.check_content", named("provenance.check")
    )
    monkeypatch.setattr(fake_steps, "run_format", named("format.fake"))
    monkeypatch.setattr(fake_steps, "run_lint", named("lint.fake"))
    monkeypatch.setattr(fake_steps, "run_typecheck", named("typecheck.fake"))
    monkeypatch.setattr(fake_steps, "run_test", named("test.fake"))
    return ran, calls


def _in_ci(monkeypatch: pytest.MonkeyPatch, leg: str) -> None:
    """A fake Gitea run, this process on the check leg labelled *leg*."""
    for name in ("GITHUB_EVENT_PATH", "GITHUB_SHA", "GITHUB_REF", "GITHUB_JOB"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITEA_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_RUN_ID", "1013")
    monkeypatch.setenv(LEG_VARIABLE, leg)


def _seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[PhaseContext]:
    """A bare workspace at *tmp_path*, the walk replaced; the contexts it is handed."""
    seen: list[PhaseContext] = []

    def walk(ctx: PhaseContext, **kwargs: object) -> None:
        del kwargs
        seen.append(ctx)

    (tmp_path / "workshop.toml").write_text("[workspace]\n")
    (tmp_path / "tasks.py").write_text("x = 1\n")
    monkeypatch.setattr(_quality, "workspace_root", lambda: tmp_path)
    monkeypatch.setattr(_quality, "walk", walk)
    monkeypatch.chdir(tmp_path)
    return seen


def test_a_shard_outside_the_form_refuses_before_anything_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _seeded(tmp_path, monkeypatch)
    monkeypatch.delenv("CI", raising=False)
    with pytest.raises(
        (SystemExit, Exception),
        match=re.escape("'0of2' is not a shard: the index counts from 1"),
    ):
        _quality.check("tasks.py", shard="0of2")
    with pytest.raises(
        (SystemExit, Exception),
        match=re.escape(
            "'two' is not a shard; a shard is spelled <i>of<n>, such as 2of4"
        ),
    ):
        _quality.run_steps(("drift.check",), shard="two")
    assert seen == []


def test_a_leg_label_outside_the_form_refuses_naming_the_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _seeded(tmp_path, monkeypatch)
    _in_ci(monkeypatch, "check-ubuntu-latest-3.14-0of2")
    with pytest.raises(
        (SystemExit, Exception),
        match=re.escape(
            "WORKSHOP_LEG=check-ubuntu-latest-3.14-0of2: '0of2' is not a shard:"
            " the index counts from 1"
        ),
    ):
        _quality.run_phase_over("drift", ())
    assert seen == []


def test_inside_ci_the_legs_label_names_the_shard_and_the_flag_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _seeded(tmp_path, monkeypatch)
    _in_ci(monkeypatch, "check-ubuntu-latest-3.14-2of3")
    # No flag: the gate over named files, a verb with and without
    # paths, and a phase run over packages all read the label.
    _quality.check("tasks.py")
    _quality.run_steps(("drift.check",))
    _quality.run_steps(("drift.check",), ("tasks.py",))
    _quality.run_phase_over("drift", ())
    assert [ctx.shard for ctx in seen] == [Shard(2, 3)] * 4
    # The flag, when given, is the shard.
    seen.clear()
    _quality.check("tasks.py", shard="3of3")
    _quality.run_steps(("drift.check",), shard="1of3")
    assert [ctx.shard for ctx in seen] == [Shard(3, 3), Shard(1, 3)]
    # A label without a shard is one shard of one, as a desk is.
    seen.clear()
    _in_ci(monkeypatch, "check-ubuntu-latest-3.14")
    _quality.run_phase_over("drift", ())
    monkeypatch.delenv("GITHUB_ACTIONS")
    _quality.run_steps(("drift.check",))
    assert [ctx.shard for ctx in seen] == [ONE, ONE]


def test_a_fixing_gate_refuses_inside_ci(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # CI judges, it never rewrites: a runner's CI variable turns
    # --fix into a taught refusal instead of a silent mutation.
    monkeypatch.setenv("CI", "true")
    with pytest.raises(BaseException, match="never rewritten"):
        _quality.check(fix=True)


def test_the_scoped_gate_runs_every_verb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, python_steps: object
) -> None:
    from livery.workshop._influence import Changes

    ran, _ = _record(monkeypatch, tmp_path)
    package = _package(tmp_path)
    # A scoped gate that knows nothing of what changed reads every
    # input of every workspace step.
    _quality._scoped_check((package,))
    assert sorted(ran) == sorted(
        (
            "format.fake",
            "layering.graph",
            *LAYERING_STEPS,
            "lint.fake",
            "drift.check",
            "provenance.check",
            *PYTHON_JUDGES,
        )
    )
    # A source change reaches the layering steps over the sources and
    # none of the workspace steps that read no source.
    ran.clear()
    source = "packages/one/src/one/a.py"
    _quality._scoped_check((package,), changes=Changes(tmp_path, (source,)))
    assert sorted(ran) == sorted(
        ("format.fake", *LAYERING_STEPS, "lint.fake", *PYTHON_JUDGES)
    )


def test_the_workspace_tests_are_a_unit_of_the_scoped_gate_with_no_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, python_steps: object
) -> None:
    from livery.workshop._packages import workspace_suite

    ran, calls = _record(monkeypatch, tmp_path)
    # The root lists python, and its own tests hold the root's set.
    (tmp_path / "workshop.toml").write_text('[workspace]\nextensions = ["python"]\n')
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_root.py").write_text("")
    unit = workspace_suite(tmp_path)
    assert unit is not None
    _quality._scoped_check((unit,))
    # The unit is every unit this workspace has, so style and types run
    # over the configured whole; the tests run as the one suite, and no
    # kind step for it.
    by_verb = {c["verb"]: c for c in calls}
    assert by_verb["format.fake"]["paths"] == (".",)
    assert by_verb["lint.fake"]["paths"] == (".",)
    assert by_verb["typecheck.fake"]["paths"] == (".",)
    assert by_verb["test.fake"]["packages"] == (unit,)
    assert by_verb["test.fake"]["scoped"] is True
    # Beside a package, the unit rides along and the package keeps its
    # own paths.
    ran.clear()
    calls.clear()
    package = _package(tmp_path)
    # A second member outside the scope, so the run narrows.
    other = tmp_path / "packages" / "two"
    (other / "tests").mkdir(parents=True)
    (other / "pyproject.toml").write_text('[project]\nname = "livery-two"\n')
    (other / "workshop.toml").write_text(
        'extensions = ["python"]\nname = "livery-two"\n'
    )
    _quality._scoped_check((package, unit))
    by_verb = {c["verb"]: c for c in calls}
    assert by_verb["format.fake"]["paths"] == ("packages/one/tests", "tests")
    assert by_verb["test.fake"]["packages"] == (package, unit)


def test_the_scoped_fix_mode_rewrites_first_and_still_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, python_steps: object
) -> None:
    # The serial rewrites were the silent half of the drift: built
    # outside the block, dropped without even a refusal.
    from livery.workshop._influence import Changes

    ran, calls = _record(monkeypatch, tmp_path)
    package = _package(tmp_path)
    source = Changes(tmp_path, ("packages/one/src/one/a.py",))
    _quality._scoped_check((package,), fix=True, changes=source)
    # The base's fixers first, then a listed extension's in its order:
    # the formatter before the linter.
    assert ran[:3] == [LAYERING_FIXER, "format.fake", "lint.fake"]
    assert sorted(ran) == sorted(
        ("format.fake", *LAYERING_STEPS, "lint.fake", *PYTHON_JUDGES)
    )
    rewrites = {c["verb"]: c for c in calls[:3]}
    assert rewrites["format.fake"]["check"] is False
    assert rewrites["lint.fake"]["fix"] is True
    # Every fixer that applies rewrites before any judge, the layering
    # fix and an extension's own included, and none of them is judged after:
    # the order lives in the walk alone, whichever gate calls it.
    from livery.workshop import _steps
    from livery.workshop._steps import StepRecord, register_step

    state = _steps.snapshot()

    def acme_fix(ctx: PhaseContext) -> None:
        del ctx
        ran.append("acme-fix")

    def acme_judge(ctx: PhaseContext) -> None:
        del ctx
        ran.append("acme-judged")

    try:
        register_step(
            StepRecord(
                "acme", "format", acme_judge, fix=acme_fix, extension="acme.extension"
            )
        )
        ran.clear()
        between: list[list[str]] = []
        _quality._scoped_check(
            (package,),
            fix=True,
            between=lambda: between.append(list(ran)),
            changes=source,
        )
    finally:
        _steps.restore(state)
    fixed = between[0]
    assert fixed[:3] == [LAYERING_FIXER, "format.fake", "lint.fake"]
    assert LAYERING_FIXER in fixed and "acme-fix" in fixed
    assert sorted(ran[len(fixed) :]) == sorted((*LAYERING_JUDGES, *PYTHON_JUDGES))
    assert "acme-judged" not in ran


def test_check_refuses_both_fix_flags_and_no_phase_verb_is_written_by_hand() -> None:
    with pytest.raises((SystemExit, Exception)) as caught:
        _quality.check("tasks.py", fix=True, safe_fix=True)
    assert "Pass one" in str(caught.value)
    # The phase verbs are generated from the registry: none is written
    # here beside the steps it would duplicate.
    for verb in ("format", "lint", "typecheck", "typecomplete", "test"):
        assert not hasattr(_quality, verb), verb


# --- what the gate is, pinned before the step registry replaces it -----------
#
# Characterisation, not specification: these say what the gate does
# today so that a reimplementation which still passes them preserved
# the behaviour. A failure after the swap is either a regression or a
# deliberate change that updates the test with its reason. Nothing
# here asserts that the current shape is the right one.
#
# Two properties are pinned elsewhere and are not repeated:
# `test_workshop_cpp_kind.py` holds both skip prints, `judged_by()`'s
# per-step line and the kind gate's run-beside-skip announcement.


def _whole_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, python: bool = True
) -> tuple[list[str], list[str]]:
    """Drive the whole gate against a seeded repository; what ran, and trees.

    Returns the recorded run, with `<parallel` and `>parallel` around
    each block footman schedules, and the tree ids the rewrite pass
    recomputed. With *python* the repository holds a package's source,
    a ``tasks.py`` and a root test, so every python step has files to
    read; without, it holds its contract alone.
    """
    import contextlib

    import livery.toolroom.tools as tools

    # The block from its owner, not through the walk's re-export: the
    # name patched below is the walk's, the behaviour wrapped is
    # footman's own.
    from livery.footman import parallel as real_parallel

    ran: list[str] = []
    trees: list[str] = []

    def named(name: str):
        def body(*args: object, **kwargs: object) -> None:
            ran.append(name)

        return body

    def typecheck(*args: object, **kwargs: object) -> None:
        del args, kwargs
        ran.append("typecheck.fake")

    @contextlib.contextmanager
    def watched():
        ran.append("<parallel")
        with real_parallel() as block:
            yield block
        # The members run as the real block exits, so they land
        # between the markers rather than after them.
        ran.append(">parallel")

    # A local run: no run context, and no CI variable, which the gate
    # reads to refuse --fix on a checkout nobody keeps.
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(_quality, "workspace_root", lambda: tmp_path)
    monkeypatch.setattr("livery.workshop._ci_context.run_context", lambda: None)
    monkeypatch.setattr(_phases, "parallel", watched)
    monkeypatch.setattr(_quality, "drift_check", named("drift.check"))

    def rule(step: str, *args: object, **kwargs: object) -> list[str]:
        del args, kwargs
        ran.append(f"layering.{step}")
        return []

    monkeypatch.setattr(
        "livery.workshop._packages.verify_graph", named("layering.graph")
    )
    monkeypatch.setattr("livery.workshop._packages.rule_problems", rule)
    monkeypatch.setattr(
        "livery.workshop._provenance.check_content", named("provenance.check")
    )
    monkeypatch.setattr(fake_steps, "run_format", named("format.fake"))
    monkeypatch.setattr(fake_steps, "run_lint", named("lint.fake"))
    monkeypatch.setattr(fake_steps, "run_typecheck", typecheck)
    monkeypatch.setattr(fake_steps, "run_test", named("test.fake"))

    def rewritten(root: object, run: object, tree: str) -> str:
        trees.append(tree)
        return tree + "-after"

    monkeypatch.setattr(_quality, "_rewritten_tree", rewritten)
    (tmp_path / "workshop.toml").write_text("[workspace]\nextensions = []\n")
    if python:
        member = tmp_path / "packages" / "one"
        (member / "src" / "one").mkdir(parents=True)
        (member / "src" / "one" / "__init__.py").write_text("x = 1\n")
        (member / "workshop.toml").write_text(
            'extensions = ["python"]\nname = "acme-one"\n'
        )
        (member / "pyproject.toml").write_text('[project]\nname = "acme-one"\n')
        (tmp_path / "tasks.py").write_text("x = 1\n")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_seed.py").write_text(
            "def test_it() -> None:\n    pass\n"
        )
    git = tools.git.opts(cwd=tmp_path, nofail=True, recorded=False)
    git("init", "-q", "--initial-branch=main")
    git("add", "-A")
    git(
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-q",
        "-m",
        "seed",
    )
    return ran, trees


def test_the_whole_gate_runs_every_member_in_one_parallel_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, python_steps: object
) -> None:
    ran, _ = _whole_gate(monkeypatch, tmp_path)
    _quality._run_check(full=True, fix=False, base="")
    assert ran[0] == "<parallel"
    assert ran[-1] == ">parallel"
    # The cpp-conan steps judge no package here, and the examples
    # step finds no example to read.
    assert sorted(ran[1:-1]) == sorted(
        (
            "format.fake",
            "layering.graph",
            *LAYERING_STEPS,
            "lint.fake",
            "provenance.check",
            "drift.check",
            *PYTHON_JUDGES,
        )
    )


def _remembered(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """What the local gate record is asked to remember; the record itself untouched."""
    rows: list[dict[str, object]] = []

    def remember(root: Path, **row: object) -> str:
        del root
        rows.append(row)
        return "  remembered"

    monkeypatch.setattr("livery.workshop._gate_record.remember", remember)
    return rows


def test_a_later_shard_runs_the_steps_that_read_it_alone_and_names_the_rest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    python_steps: object,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ran, _ = _whole_gate(monkeypatch, tmp_path)
    rows = _remembered(monkeypatch)
    shards: list[object] = []

    def test_body(*args: object, **kwargs: object) -> None:
        del args
        ran.append("test.fake")
        shards.append(kwargs["shard"])

    monkeypatch.setattr(fake_steps, "run_test", test_body)
    _quality._run_check(full=True, fix=False, base="", shard=Shard(2, 3))
    # The one step that reads the shard runs, and sees which it is.
    assert ran == ["<parallel", "test.fake", ">parallel"]
    assert shards == [Shard(2, 3)]
    out = capsys.readouterr().out
    left = sorted(
        line.strip().partition(":")[0]
        for line in out.splitlines()
        if "left to the first shard (this leg is 2of3)" in line
    )
    assert left == sorted(
        (
            "format.fake",
            "layering.graph",
            *LAYERING_STEPS,
            "lint.fake",
            "provenance.check",
            "drift.check",
            "typecheck.fake",
        )
    )
    # A share of the gate proves no tree.
    assert rows == []
    assert (
        "shard 2of3: a share of the gate ran; the tree is not recorded as proved" in out
    )


def test_the_first_shard_runs_every_step_and_names_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    python_steps: object,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ran, _ = _whole_gate(monkeypatch, tmp_path)
    rows = _remembered(monkeypatch)
    _quality._run_check(full=True, fix=False, base="", shard=Shard(1, 3))
    assert sorted(ran[1:-1]) == sorted(
        (
            "format.fake",
            "layering.graph",
            *LAYERING_STEPS,
            "lint.fake",
            "provenance.check",
            "drift.check",
            *PYTHON_JUDGES,
        )
    )
    out = capsys.readouterr().out
    assert "left to the first shard" not in out
    # The first shard still ran a share of the tests, so it proves no tree
    # either; one shard of one does.
    assert rows == []
    assert "shard 1of3: a share of the gate ran" in out
    ran.clear()
    _quality._run_check(full=True, fix=False, base="")
    assert len(rows) == 1
    assert "a share of the gate ran" not in capsys.readouterr().out


def test_a_workspace_without_python_starts_no_python_step(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    python_steps: object,
) -> None:
    ran, _ = _whole_gate(monkeypatch, tmp_path, python=False)
    _quality._run_check(full=True, fix=False, base="")
    # The steps without claims read what their own body decides; the
    # python ones claim .py files, and none is there to read.
    assert sorted(ran[1:-1]) == sorted(
        ("drift.check", "layering.graph", *LAYERING_STEPS, "provenance.check")
    )
    out = capsys.readouterr().out
    for name in ("format.fake", "lint.fake", *PYTHON_JUDGES):
        assert f"  {name}: no file it reads in the workspace; not run" in out


def _blocks(ran: list[str]) -> list[list[str]]:
    """What ran inside each parallel block, block by block."""
    blocks: list[list[str]] = []
    for item in ran:
        if item == "<parallel":
            blocks.append([])
        elif item != ">parallel":
            blocks[-1].append(item)
    return blocks


def test_the_fixing_gate_rewrites_serially_then_judges_in_parallel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, python_steps: object
) -> None:
    ran, _ = _whole_gate(monkeypatch, tmp_path)
    _quality._run_check(full=True, fix=True, base="")
    blocks = _blocks(ran)
    # The rewriters one at a time, each the one member of its own
    # block, in order, before the judges' block: they write the files
    # the judges then read, and none of them is a judge under --fix.
    # The base's run first, then a listed extension's in its list
    # order, the formatter before the linter.
    assert blocks[:-1] == [
        ["drift.check"],
        ["provenance.check"],
        ["layering.graph"],
        [LAYERING_FIXER],
        ["format.fake"],
        ["lint.fake"],
    ]
    assert sorted(blocks[-1]) == sorted((*LAYERING_JUDGES, *PYTHON_JUDGES))
    assert ran[-1] == ">parallel"


def test_the_judges_read_the_tree_the_rewriters_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, python_steps: object
) -> None:
    """The tree is recomputed between the two, and only under --fix."""
    ran, trees = _whole_gate(monkeypatch, tmp_path)
    _quality._run_check(full=True, fix=True, base="")
    assert len(trees) == 1
    ran.clear()
    trees.clear()
    _quality._run_check(full=True, fix=False, base="")
    assert trees == []


def test_one_refusing_member_is_the_gate_s_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, python_steps: object
) -> None:
    """The gate is the conjunction: one member's refusal is the answer."""
    _whole_gate(monkeypatch, tmp_path)

    def refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("typecheck says no")

    monkeypatch.setattr(fake_steps, "run_typecheck", refuse)
    with pytest.raises(BaseException, match="typecheck says no"):
        _quality._run_check(full=True, fix=False, base="")
