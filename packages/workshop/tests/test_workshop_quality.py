"""The scoped gate: every verb it schedules actually runs.

The property pinned here is execution, not exit: a built step is not
a run step under footman's block contract, and a gate whose verbs are
built and dropped exits green having checked nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import workshop_python_checks as fake_checks
from livery.workshop import _quality
from livery.workshop._backends import _python
from livery.workshop._packages import Package
from workshop_python_checks import python_checks_fixture  # noqa: F401

# The python checks that judge and never rewrite, sorted, as the fakes
# below record them: one type check per type checker.
PYTHON_JUDGES = (
    "test.fake",
    "typecheck.fake",
)


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
    monkeypatch.setattr(
        "livery.workshop._packages.verify_graph", named("layering.graph")
    )
    monkeypatch.setattr(
        "livery.workshop._packages.verify_imports", named("layering.imports")
    )
    monkeypatch.setattr(_quality, "drift_check", named("drift.check"))
    monkeypatch.setattr(
        "livery.workshop._provenance.check_content", named("provenance.check")
    )
    monkeypatch.setattr(fake_checks, "run_format", named("format.fake"))
    monkeypatch.setattr(fake_checks, "run_lint", named("lint.fake"))
    monkeypatch.setattr(fake_checks, "run_typecheck", named("typecheck.fake"))
    monkeypatch.setattr(fake_checks, "run_test", named("test.fake"))
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, python_checks: object
) -> None:
    from livery.workshop._influence import Changes

    ran, _ = _record(monkeypatch, tmp_path)
    package = _package(tmp_path)
    # A scoped gate that knows nothing of what changed reads every
    # input of every workspace check.
    _quality._scoped_check((package,))
    assert sorted(ran) == sorted(
        (
            "format.fake",
            "layering.graph",
            "layering.imports",
            "lint.fake",
            "drift.check",
            "provenance.check",
            *PYTHON_JUDGES,
        )
    )
    # A source change reaches the import rules and none of the
    # workspace checks that read no source.
    ran.clear()
    source = "packages/one/src/one/a.py"
    _quality._scoped_check((package,), changes=Changes(tmp_path, (source,)))
    assert sorted(ran) == sorted(
        ("format.fake", "layering.imports", "lint.fake", *PYTHON_JUDGES)
    )


def test_the_workspace_tests_are_a_unit_of_the_scoped_gate_with_no_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, python_checks: object
) -> None:
    from livery.workshop._coverage_store import workspace_suite

    ran, calls = _record(monkeypatch, tmp_path)
    (tmp_path / "tests").mkdir()
    unit = workspace_suite(tmp_path)
    assert unit is not None
    _quality._scoped_check((unit,))
    # The unit is every unit this workspace has, so style and types run
    # over the configured whole; the tests run as the one suite, and no
    # kind check for it.
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
    (other / "workshop.toml").write_text('kind = "python"\nname = "livery-two"\n')
    _quality._scoped_check((package, unit))
    by_verb = {c["verb"]: c for c in calls}
    assert by_verb["format.fake"]["paths"] == ("packages/one/tests", "tests")
    assert by_verb["test.fake"]["packages"] == (package, unit)


def test_the_scoped_fix_mode_rewrites_first_and_still_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, python_checks: object
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
    assert ran[:3] == ["layering.imports", "format.fake", "lint.fake"]
    assert sorted(ran) == sorted(
        ("format.fake", "layering.imports", "lint.fake", *PYTHON_JUDGES)
    )
    rewrites = {c["verb"]: c for c in calls[:3]}
    assert rewrites["format.fake"]["check"] is False
    assert rewrites["lint.fake"]["fix"] is True
    # Every fixer that applies rewrites before any judge, the layering
    # fix and an extension's own included, and none of them is judged after:
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
            CheckRecord(
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
        _checks.restore(state)
    fixed = between[0]
    assert fixed[:3] == ["layering.imports", "format.fake", "lint.fake"]
    assert "layering.imports" in fixed and "acme-fix" in fixed
    assert sorted(ran[len(fixed) :]) == list(PYTHON_JUDGES)
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


def _python_member(tmp_path: Path, name: str, manifest: str = "") -> Package:
    member = tmp_path / "packages" / name
    member.mkdir(parents=True)
    (member / "pyproject.toml").write_text(
        f'[project]\nname = "acme-{name}"\n{manifest}'
    )
    return Package(
        directory=member,
        path=f"packages/{name}",
        name=f"acme-{name}",
        kind="python",
        depends=(),
    )


def _root(member: Path, name: str) -> Path:
    """A root: its __init__, a public package it declares, a private module."""
    root = member / "src" / "acme" / name
    (root / "codec").mkdir(parents=True)
    (root / "__init__.py").write_text('__version__ = "0.0.0"\n')
    (root / "py.typed").write_text("")
    (root / "codec" / "__init__.py").write_text("")
    (root / "_private.py").write_text("")
    (root / "content").mkdir()
    (root / "content" / "seed.py").write_text("")
    return root


def test_the_public_modules_are_what_each_root_declares(tmp_path: Path) -> None:
    from livery.workshop import public_modules

    # A root with nothing public, an extension's, declares nothing.
    bare = _python_member(
        tmp_path, "ext", '[tool.uv.build-backend]\nmodule-name = "acme.ext"\n'
    )
    (bare.directory / "src" / "acme" / "ext").mkdir(parents=True)
    assert public_modules(bare) == ()
    # A root's __init__ declares its public packages too, so a verifier
    # reaches them through it: one module.
    package = _python_member(tmp_path, "one")
    _root(package.directory, "one")
    assert public_modules(package) == ("acme.one",)


def test_a_root_with_nothing_public_verifies_nothing(tmp_path: Path) -> None:
    # An extension: its build names the root, and nothing in it is public.
    package = _python_member(
        tmp_path,
        "ext",
        '[tool.uv.build-backend]\nmodule-name = "acme.extensions.ext"\n',
    )
    root = package.directory / "src" / "acme" / "extensions" / "ext"
    (root / "content").mkdir(parents=True)
    (root / "_checks.py").write_text("")
    assert _python.module_roots(package) == ("acme.extensions.ext",)
    assert _python.public_modules(package) == ()


def test_the_public_modules_follow_the_layout(tmp_path: Path) -> None:
    # A regular package is one module: the verifier walks the rest.
    regular = _python_member(tmp_path, "two")
    (regular.directory / "src" / "acme" / "two" / "sub").mkdir(parents=True)
    (regular.directory / "src" / "acme" / "two" / "__init__.py").write_text("")
    (regular.directory / "src" / "acme" / "two" / "sub" / "__init__.py").write_text("")
    assert _python.public_modules(regular) == ("acme.two",)
    # A namespace, with no __init__, declares nothing: an api module in
    # it is an ordinary module, and a regular package beneath it is a
    # root of its own.
    spaced = _python_member(tmp_path, "three")
    namespace = spaced.directory / "src" / "acme" / "three"
    (namespace / "codec").mkdir(parents=True)
    (namespace / "api.py").write_text('__version__ = "0.0.0"\n')
    (namespace / "codec" / "__init__.py").write_text("")
    assert _python.public_modules(spaced) == ("acme.three.codec",)
    # No src tree: the distribution name's module.
    srcless = _python_member(tmp_path, "four")
    assert _python.public_modules(srcless) == ("acme.four",)


def test_the_roots_are_what_the_build_ships(tmp_path: Path) -> None:
    # Undeclared: read from the src tree's marks.
    marked = _python_member(tmp_path, "five")
    _root(marked.directory, "five")
    assert _python.module_roots(marked) == ("acme.five",)
    # Declared: the build's own list, a second root with no marks included.
    declared = _python_member(
        tmp_path,
        "six",
        '[tool.uv.build-backend]\nmodule-name = ["acme.six", "acme.extensions.six"]\n',
    )
    _root(declared.directory, "six")
    assert _python.module_roots(declared) == ("acme.extensions.six", "acme.six")
    single = _python_member(
        tmp_path, "seven", '[tool.uv.build-backend]\nmodule-name = "acme.seven"\n'
    )
    (single.directory / "src").mkdir()
    assert _python.module_roots(single) == ("acme.seven",)


def test_check_refuses_both_fix_flags_and_no_role_verb_is_written_by_hand() -> None:
    with pytest.raises((SystemExit, Exception)) as caught:
        _quality.check("tasks.py", fix=True, safe_fix=True)
    assert "Pass one" in str(caught.value)
    # The role verbs are generated from the registry: none is written
    # here beside the checks it would duplicate.
    for verb in ("format", "lint", "typecheck", "typecomplete", "test"):
        assert not hasattr(_quality, verb), verb


def test_the_python_test_entry_maps_a_selection_to_its_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, python_checks: object
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
    # The words for the kind's test runner reach pytest unchanged.
    _python.test(package, tmp_path, arguments=("-k", "name"))
    assert calls[2][0] == ("-k", "name")


# --- what the gate is, pinned before the check registry replaces it -----------
#
# Characterisation, not specification: these say what the gate does
# today so that a reimplementation which still passes them preserved
# the behaviour. A failure after the swap is either a regression or a
# deliberate change that updates the test with its reason. Nothing
# here asserts that the current shape is the right one.
#
# Two properties are pinned elsewhere and are not repeated:
# `test_workshop_cpp_kind.py` holds both skip prints, `judged_by()`'s
# per-check line and the kind gate's run-beside-skip announcement.


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

    import livery.toolroom.tools as tools

    # The block from its owner, not through the gate's re-export: the
    # name patched below is the gate's, the behaviour wrapped is
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
    monkeypatch.setattr(_quality, "drift_check", named("drift.check"))
    monkeypatch.setattr(
        "livery.workshop._packages.verify_graph", named("layering.graph")
    )
    monkeypatch.setattr(
        "livery.workshop._packages.verify_imports", named("layering.imports")
    )
    monkeypatch.setattr(
        "livery.workshop._provenance.check_content", named("provenance.check")
    )
    monkeypatch.setattr(fake_checks, "run_format", named("format.fake"))
    monkeypatch.setattr(fake_checks, "run_lint", named("lint.fake"))
    monkeypatch.setattr(fake_checks, "run_typecheck", typecheck)
    monkeypatch.setattr(fake_checks, "run_test", named("test.fake"))

    def rewritten(root: object, run: object, tree: str) -> str:
        trees.append(tree)
        return tree + "-after"

    monkeypatch.setattr(_quality, "_rewritten_tree", rewritten)
    (tmp_path / "workshop.toml").write_text("[workspace]\nextensions = []\n")
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


def test_the_whole_gate_runs_every_member_in_one_parallel_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, python_checks: object
) -> None:
    ran, _ = _whole_gate(monkeypatch, tmp_path)
    _quality._run_check(full=True, fix=False, base="")
    assert ran[0] == "<parallel"
    assert ran[-1] == ">parallel"
    # The cpp-conan checks judge no package here, and the examples
    # check finds no example to read.
    assert sorted(ran[1:-1]) == sorted(
        (
            "format.fake",
            "layering.graph",
            "layering.imports",
            "lint.fake",
            "provenance.check",
            "drift.check",
            *PYTHON_JUDGES,
        )
    )


def test_a_workspace_without_python_starts_no_python_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    python_checks: object,
) -> None:
    ran, _ = _whole_gate(monkeypatch, tmp_path, python=False)
    _quality._run_check(full=True, fix=False, base="")
    # The checks without claims read what their own body decides; the
    # python ones claim .py files, and none is there to read.
    assert sorted(ran[1:-1]) == [
        "drift.check",
        "layering.graph",
        "layering.imports",
        "provenance.check",
    ]
    out = capsys.readouterr().out
    for name in ("format.fake", "lint.fake", *PYTHON_JUDGES):
        assert f"  {name}: no file it reads in the workspace; not run" in out


def test_the_fixing_gate_rewrites_serially_then_judges_in_parallel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, python_checks: object
) -> None:
    ran, _ = _whole_gate(monkeypatch, tmp_path)
    _quality._run_check(full=True, fix=True, base="")
    opened = ran.index("<parallel")
    # The rewriters, in order, before any block is opened: they write
    # the files the judges then read, and none of them is a judge under
    # --fix. The base's run first, then a listed extension's in its
    # list order, the formatter before the linter.
    assert ran[:opened] == [
        "drift.check",
        "provenance.check",
        "layering.graph",
        "layering.imports",
        "format.fake",
        "lint.fake",
    ]
    assert sorted(ran[opened + 1 : -1]) == sorted(PYTHON_JUDGES)
    assert ran[-1] == ">parallel"


def test_the_judges_read_the_tree_the_rewriters_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, python_checks: object
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, python_checks: object
) -> None:
    """The gate is the conjunction: one member's refusal is the answer."""
    _whole_gate(monkeypatch, tmp_path)

    def refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("typecheck says no")

    monkeypatch.setattr(fake_checks, "run_typecheck", refuse)
    with pytest.raises(BaseException, match="typecheck says no"):
        _quality._run_check(full=True, fix=False, base="")
