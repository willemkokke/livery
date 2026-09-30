"""The scoped gate: every verb it schedules actually runs.

The property pinned here is execution, not exit: a built step is not
a run step under footman's block contract, and a gate whose verbs are
built and dropped exits green having checked nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.workshop import _quality
from livery.workshop._backends import _python
from livery.workshop._packages import Package


def _package(tmp_path: Path) -> Package:
    member = tmp_path / "packages" / "one"
    (member / "tests").mkdir(parents=True)
    (member / "pyproject.toml").write_text(
        '[project]\nname = "livery-one"\nversion = "0.1.0"\n'
    )
    (member / "workshop.toml").write_text('kind = "python"\nname = "livery-one"\n')
    return Package(
        directory=member,
        path="packages/one",
        name="livery-one",
        kind="python",
        depends=(),
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

    monkeypatch.setattr(_quality, "workspace_root", lambda: tmp_path)
    monkeypatch.setattr("livery.workshop._packages.verify_workspace", named("layering"))
    monkeypatch.setattr(_python, "run_format", named("format"))
    monkeypatch.setattr(_python, "run_lint", named("lint"))
    monkeypatch.setattr(_python, "run_typecheck", named("typecheck"))
    monkeypatch.setattr(_python, "run_typecomplete", named("typecomplete"))
    monkeypatch.setattr(_python, "run_test", named("test"))
    return ran, calls


def test_a_fixing_gate_refuses_inside_ci(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # CI judges, it never rewrites: a runner's CI variable turns
    # --fix into a taught refusal instead of a silent mutation.
    monkeypatch.setenv("CI", "true")
    with pytest.raises(BaseException, match="never rewritten"):
        _quality.check(fix=True)


def test_the_scoped_gate_runs_every_verb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran, _ = _record(monkeypatch, tmp_path)
    _quality._scoped_check((_package(tmp_path),))
    # kindcheck retired with the registry: a python package has no
    # per-package check, and the layering check runs in a scoped gate.
    assert sorted(ran) == [
        "format",
        "layering",
        "lint",
        "test",
        "typecheck",
        "typecomplete",
    ]


def test_the_workspace_tests_are_a_unit_of_the_scoped_gate_with_no_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._coverage_store import workspace_suite

    ran, calls = _record(monkeypatch, tmp_path)
    (tmp_path / "tests").mkdir()
    unit = workspace_suite(tmp_path)
    assert unit is not None
    _quality._scoped_check((unit,))
    # Style and types over the directory itself, the tests run as the
    # one suite, no type-completeness, and no kind check for it.
    by_verb = {c["verb"]: c for c in calls}
    assert by_verb["format"]["paths"] == ("tests",)
    assert by_verb["lint"]["paths"] == ("tests",)
    assert by_verb["typecheck"]["paths"] == ("tests",)
    assert by_verb["typecomplete"]["args"] == ((),)
    assert by_verb["test"]["packages"] == (unit,)
    assert by_verb["test"]["scoped"] is True
    # Beside a package, the unit rides along and the package keeps its
    # own paths.
    ran.clear()
    calls.clear()
    package = _package(tmp_path)
    _quality._scoped_check((package, unit))
    by_verb = {c["verb"]: c for c in calls}
    assert by_verb["format"]["paths"] == ("packages/one/tests", "tests")
    assert by_verb["test"]["packages"] == (package, unit)


def test_the_scoped_fix_mode_rewrites_first_and_still_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The serial rewrites were the silent half of the drift: built
    # outside the block, dropped without even a refusal.
    ran, calls = _record(monkeypatch, tmp_path)
    package = _package(tmp_path)
    _quality._scoped_check((package,), fix=True)
    assert ran[:2] == ["format", "lint"]
    assert sorted(ran) == [
        "format",
        "layering",
        "lint",
        "test",
        "typecheck",
        "typecomplete",
    ]
    rewrites = {c["verb"]: c for c in calls[:2]}
    assert rewrites["format"]["check"] is False
    assert rewrites["lint"]["fix"] is True
    # Every fixer that applies rewrites before any judge, the layering
    # fix and a layer's own included, and none of them is judged after:
    # the order lives in the walk alone, whichever gate calls it.
    from livery.workshop import _checks
    from livery.workshop._checks import CheckRecord, GateContext, register_check

    state = _checks.snapshot()

    def acme_fix(ctx: GateContext) -> None:
        del ctx
        ran.append("acme-fix")

    def acme_judge(ctx: GateContext) -> None:
        del ctx
        ran.append("acme-judged")

    try:
        register_check(
            CheckRecord("acme", "format", acme_judge, fix=acme_fix, layer="acme.layer")
        )
        ran.clear()
        between: list[list[str]] = []
        _quality._scoped_check(
            (package,), fix=True, between=lambda: between.append(list(ran))
        )
    finally:
        _checks.restore(state)
    fixed = between[0]
    assert fixed[:2] == ["format", "lint"]
    assert "layering" in fixed and "acme-fix" in fixed
    assert sorted(ran[len(fixed) :]) == ["test", "typecheck", "typecomplete"]
    assert "acme-judged" not in ran


def test_the_module_derives_from_the_src_tree_not_the_dist_name(
    tmp_path: Path,
) -> None:
    # loop-echo under a workspace prefix once became "loop.echo", a
    # module that does not exist: the src tree is the truth.
    from livery.workshop._backends._python import module_for

    member = tmp_path / "packages" / "loop-echo"
    (member / "src" / "ci_e2e_loop" / "loop_echo").mkdir(parents=True)
    (member / "src" / "ci_e2e_loop" / "loop_echo" / "__init__.py").write_text("")
    package = Package(
        directory=member,
        path="packages/loop-echo",
        name="ci-e2e-loop-loop-echo",
        kind="python",
        depends=(),
    )
    assert module_for(package) == "ci_e2e_loop.loop_echo"


def test_a_srcless_package_falls_back_to_the_dist_spelling(
    tmp_path: Path,
) -> None:
    from livery.workshop._backends._python import module_for

    member = tmp_path / "packages" / "plain"
    member.mkdir(parents=True)
    package = Package(
        directory=member,
        path="packages/plain",
        name="livery-loop-echo",
        kind="python",
        depends=(),
    )
    assert module_for(package) == "livery.loop_echo"


def test_safe_fix_keeps_imports_and_foreign_files_pass_through(
    tmp_path: Path,
) -> None:
    # The edit-in-flight fix: the unused import survives, the sortable
    # one still heals, and a non-python path named alongside is a
    # no-op rather than an error.
    victim = tmp_path / "wip.py"
    victim.write_text("import sys\nimport os\n\nprint(sys.path)\n")
    foreign = tmp_path / "notes.md"
    foreign.write_text("#Heading\n")
    # The file heals in place; lint still reports the withheld import
    # by exiting non-zero (which the hook suppresses and a user
    # reads). The pin is the healed bytes, not the exit.
    import contextlib

    with contextlib.suppress(Exception):
        _python.run_lint(safe_fix=True, paths=(str(victim), str(foreign)))
    healed = victim.read_text()
    assert "import os" in healed  # F401 withheld
    assert healed.index("import os") < healed.index("import sys")  # I001 healed
    assert foreign.read_text() == "#Heading\n"  # foreign file untouched
    # A plain fix removes the unused import; safe-fix is the weaker one.
    with contextlib.suppress(Exception):
        _python.run_lint(fix=True, paths=(str(victim),))
    assert "import os" not in victim.read_text()


def test_check_refuses_both_fix_flags_and_the_role_verbs_are_gone() -> None:
    with pytest.raises((SystemExit, Exception)) as caught:
        _quality.check("tasks.py", fix=True, safe_fix=True)
    assert "Pass one" in str(caught.value)
    # The registry's checks are the gate's surface: no verb duplicates one.
    for verb in ("format", "lint", "typecheck", "typecomplete", "test"):
        assert not hasattr(_quality, verb), verb


def test_the_python_test_entry_maps_a_selection_to_its_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._backends import _python

    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        _python, "run_test", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    package = _package(tmp_path)
    _python.test(package, tmp_path, selection=("tests/test_a.py",))
    args, kwargs = calls[0]
    assert args == ()
    assert kwargs["selection"] == {package.path: (f"{package.path}/tests/test_a.py",)}
    _python.test(package, tmp_path)
    assert calls[1][1]["selection"] is None


# --- what the gate is, pinned before the check registry replaces it -----------
#
# Characterisation, not specification: these say what the gate does
# today so that a reimplementation which still passes them preserved
# the behaviour. A failure after the swap is either a regression or a
# deliberate change that updates the test with its reason. Nothing
# here asserts that the current shape is the right one.
#
# Two properties are pinned elsewhere and are not repeated:
# `test_workshop_cpp_kind.py` holds both skip prints, `gated()`'s
# per-verb line and the kind gate's run-beside-skip announcement.


def _whole_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, python: bool = True
) -> tuple[list[str], list[str]]:
    """Drive the whole gate against a seeded repository; what ran, and trees.

    Returns the recorded run, with `<parallel` and `>parallel` around
    each block footman schedules, and the tree ids the rewrite pass
    recomputed. With *python* the repository holds a package's source,
    a ``tasks.py`` and a root test, so every python check has files to
    read; without, it holds its contract alone.
    """
    import contextlib

    # The block from its owner, not through the gate's re-export: the
    # name patched below is the gate's, the behaviour wrapped is
    # footman's own.
    from livery.footman import parallel as real_parallel
    from livery.toolroom import tools

    ran: list[str] = []
    trees: list[str] = []

    def named(name: str):
        def body(*args: object, **kwargs: object) -> None:
            ran.append(name)

        return body

    @contextlib.contextmanager
    def watched():
        ran.append("<parallel")
        with real_parallel():
            yield
        # The members run as the real block exits, so they land
        # between the markers rather than after them.
        ran.append(">parallel")

    # A local run: no run context, and no CI variable, which the gate
    # reads to refuse --fix on a checkout nobody keeps.
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(_quality, "workspace_root", lambda: tmp_path)
    monkeypatch.setattr("livery.workshop._state.run_context", lambda: None)
    monkeypatch.setattr(_quality, "parallel", watched)
    monkeypatch.setattr(_quality, "template_check", named("template_check"))
    monkeypatch.setattr("livery.workshop._packages.verify_workspace", named("layering"))
    monkeypatch.setattr(
        "livery.workshop._provenance.provenance_check", named("provenance_check")
    )
    monkeypatch.setattr(_python, "run_format", named("format"))
    monkeypatch.setattr(_python, "run_lint", named("lint"))
    monkeypatch.setattr(_python, "run_typecheck", named("typecheck"))
    monkeypatch.setattr(_python, "run_typecomplete", named("typecomplete"))
    monkeypatch.setattr(_python, "run_test", named("test"))

    def rewritten(root: object, run: object, tree: str) -> str:
        trees.append(tree)
        return tree + "-after"

    monkeypatch.setattr(_quality, "_rewritten_tree", rewritten)
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nlayers = ["livery.workshop"]\n'
    )
    if python:
        member = tmp_path / "packages" / "one"
        (member / "src" / "one").mkdir(parents=True)
        (member / "src" / "one" / "__init__.py").write_text("x = 1\n")
        (member / "workshop.toml").write_text('kind = "python"\nname = "acme-one"\n')
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
        "user.email=t@t",
        "-c",
        "user.name=t",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-q",
        "-m",
        "seed",
    )
    return ran, trees


def test_the_whole_gate_is_eight_members_in_one_parallel_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran, _ = _whole_gate(monkeypatch, tmp_path)
    _quality._run_check(full=True, fix=False, base="")
    assert ran[0] == "<parallel"
    assert ran[-1] == ">parallel"
    # kindcheck retired with the registry (its cpp-conan records judge
    # no package here) and the layering check joined the base's set.
    assert sorted(ran[1:-1]) == [
        "format",
        "layering",
        "lint",
        "provenance_check",
        "template_check",
        "test",
        "typecheck",
        "typecomplete",
    ]


def test_a_workspace_without_python_starts_no_python_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran, _ = _whole_gate(monkeypatch, tmp_path, python=False)
    _quality._run_check(full=True, fix=False, base="")
    # The checks without claims read what their own body decides; the
    # python ones claim .py files, and none is there to read.
    assert sorted(ran[1:-1]) == ["layering", "provenance_check", "template_check"]
    out = capsys.readouterr().out
    for name in ("format", "lint", "test", "typecheck", "typecomplete"):
        assert f"  {name}: no file it reads in the workspace; not run" in out


def test_the_fixing_gate_rewrites_serially_then_judges_in_parallel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran, _ = _whole_gate(monkeypatch, tmp_path)
    _quality._run_check(full=True, fix=True, base="")
    opened = ran.index("<parallel")
    # The three rewriters, in order, before any block is opened: they
    # write the files the judges then read.
    # The layering check rewrites too since its fix mode landed, so it
    # is the fourth rewriter and no longer a judge under --fix.
    assert ran[:opened] == ["format", "lint", "provenance_check", "layering"]
    assert sorted(ran[opened + 1 : -1]) == [
        "template_check",
        "test",
        "typecheck",
        "typecomplete",
    ]
    assert ran[-1] == ">parallel"


def test_the_judges_read_the_tree_the_rewriters_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate is the conjunction: one member's refusal is the answer."""
    _whole_gate(monkeypatch, tmp_path)

    def refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("typecheck says no")

    monkeypatch.setattr(_python, "run_typecheck", refuse)
    with pytest.raises(BaseException, match="typecheck says no"):
        _quality._run_check(full=True, fix=False, base="")
