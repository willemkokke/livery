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
    return Package(
        directory=member,
        path="packages/one",
        name="livery-one",
        type="python",
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
    monkeypatch.setattr(_quality, "run_kind_checks", named("kindcheck"))
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
    assert sorted(ran) == [
        "format",
        "kindcheck",
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
    assert by_verb["kindcheck"]["args"] == ((), tmp_path)
    # Beside a package, the unit rides along and the package keeps its
    # own paths.
    ran.clear()
    calls.clear()
    package = _package(tmp_path)
    _quality._scoped_check((package, unit))
    by_verb = {c["verb"]: c for c in calls}
    assert by_verb["format"]["paths"] == ("packages/one/tests", "tests")
    assert by_verb["test"]["packages"] == (package, unit)
    assert by_verb["kindcheck"]["args"] == ((package,), tmp_path)


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
        "kindcheck",
        "lint",
        "test",
        "typecheck",
        "typecomplete",
    ]
    rewrites = {c["verb"]: c for c in calls[:2]}
    assert rewrites["format"]["check"] is False
    assert rewrites["lint"]["fix"] is True
    # A caller that ran the rewriters itself, to measure the tree they
    # left, says so: the checks run and the rewriters do not run twice.
    ran.clear()
    _quality._scoped_check((package,), fix=True, rewritten=True)
    assert sorted(ran) == ["kindcheck", "test", "typecheck", "typecomplete"]


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
        type="python",
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
        type="python",
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


def test_lint_and_format_refuse_both_fix_flags() -> None:
    with pytest.raises((SystemExit, Exception)) as caught:
        _quality.lint(fix=True, safe_fix=True)
    assert "Pass one" in str(caught.value)
    with pytest.raises((SystemExit, Exception)) as caught:
        _quality.format(fix=True, safe_fix=True)
    assert "Pass one" in str(caught.value)


def test_the_pages_reach_pytest_as_docs_page_arguments(
    monkeypatch, tmp_path: Path
) -> None:
    from livery.workshop._backends import _python

    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        _python, "run_test", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    package = _package(tmp_path)
    _python.test(
        package,
        tmp_path,
        selection=("tests/test_docs_examples.py",),
        pages=(f"{package.path}/docs/a.md", f"{package.path}/docs/b.md"),
    )
    args, kwargs = calls[0]
    assert args == (
        f"--docs-page={package.path}/docs/a.md",
        f"--docs-page={package.path}/docs/b.md",
    )
    assert kwargs["selection"] == {
        package.path: (f"{package.path}/tests/test_docs_examples.py",)
    }
    assert _python.page_arguments(()) == []


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
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[list[str], list[str]]:
    """Drive the whole gate against a seeded repository; what ran, and trees.

    Returns the recorded run, with `<parallel` and `>parallel` around
    each block footman schedules, and the tree ids the rewrite pass
    recomputed.
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
    monkeypatch.setattr(_quality, "run_kind_checks", named("kindcheck"))
    monkeypatch.setattr(_quality, "template_check", named("template_check"))
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
    assert sorted(ran[1:-1]) == [
        "format",
        "kindcheck",
        "lint",
        "provenance_check",
        "template_check",
        "test",
        "typecheck",
        "typecomplete",
    ]


def test_the_fixing_gate_rewrites_serially_then_judges_in_parallel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran, _ = _whole_gate(monkeypatch, tmp_path)
    _quality._run_check(full=True, fix=True, base="")
    opened = ran.index("<parallel")
    # The three rewriters, in order, before any block is opened: they
    # write the files the judges then read.
    assert ran[:opened] == ["format", "lint", "provenance_check"]
    assert sorted(ran[opened + 1 : -1]) == [
        "kindcheck",
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
