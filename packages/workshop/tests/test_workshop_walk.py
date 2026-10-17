"""The gate's one walk: a step with no file to read in scope starts no process."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

import workshop_python_steps as fake_steps
from livery.workshop import _steps
from livery.workshop._packages import discover_packages
from livery.workshop._phases import PhaseContext
from livery.workshop._shard import ONE, Shard
from livery.workshop._steps import (
    PACKAGE,
    ROOT_UNIT,
    Claim,
    StepRecord,
    catalogue,
    on_this_shard,
    reads_files,
    register_step,
    run_step,
    with_files,
)
from workshop_installed import skip_unless_installed
from workshop_python_steps import python_steps_fixture  # noqa: F401


def _repository(tmp_path: Path) -> Path:
    """A workspace git knows: one python package with a source file, a tasks.py."""
    root = tmp_path / "ws"
    member = root / "packages" / "one"
    (member / "src" / "one").mkdir(parents=True)
    (member / "src" / "one" / "__init__.py").write_text("x = 1\n")
    (member / "workshop.toml").write_text(
        'extensions = ["python"]\nname = "acme-one"\n'
    )
    (member / "pyproject.toml").write_text('[project]\nname = "acme-one"\n')
    (root / "workshop.toml").write_text("[workspace]\n")
    (root / "tasks.py").write_text("x = 1\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


@pytest.fixture
def registry() -> Iterator[None]:
    state = _steps.snapshot()
    yield
    _steps.restore(state)


def _idle(ctx: PhaseContext) -> None:
    del ctx


def test_a_scope_holding_no_package_runs_no_step_that_narrows_by_packages(
    tmp_path: Path, registry: None
) -> None:
    # The refusal that matters most first: an empty scope read as the
    # whole would start every package's suite, and a test that drives
    # the gate would then run the gate inside itself.
    from livery.workshop._influence import Changes, Inputs

    root = _repository(tmp_path)
    register_step(StepRecord("whole", "lint", _idle))
    register_step(
        StepRecord("pages", "lint", _idle, inputs=Inputs(reads=("docs/**/*.md",)))
    )
    ctx = PhaseContext(
        root=root,
        packages=discover_packages(root),
        subset=(),
        changes=Changes(root, ("docs/a.md",)),
    )
    names = _steps.judges(ctx)
    assert "lint.whole" not in names
    assert "lint.pages" in names
    assert "lint.pages" in with_files(names, ctx)


def test_a_later_shard_leaves_a_step_without_the_flag_to_the_first(
    tmp_path: Path, registry: None, capsys: pytest.CaptureFixture[str]
) -> None:
    # A step that reads the shard runs its share on every shard; one
    # that does not runs whole, so the first shard runs it alone and
    # every other shard says it left it there.
    register_step(StepRecord("whole", "lint", _idle))
    register_step(StepRecord("share", "lint", _idle, flags=("shard",)))
    names = ("lint.whole", "lint.share")
    assert on_this_shard(names, PhaseContext(root=tmp_path, shard=Shard(2, 3))) == (
        "lint.share",
    )
    assert capsys.readouterr().out == (
        "  lint.whole: left to the first shard (this leg is 2of3)\n"
    )
    # The first shard, and a run that is one shard of one, keep every
    # step and say nothing.
    for shard in (Shard(1, 3), ONE):
        assert on_this_shard(names, PhaseContext(root=tmp_path, shard=shard)) == names
    assert capsys.readouterr().out == ""


# The fallback first: without a listing, every step that applies runs.


def test_without_a_git_listing_every_step_reads_files(tmp_path: Path) -> None:
    ctx = PhaseContext(root=tmp_path, packages=())
    assert catalogue(ctx) is None
    record = StepRecord(
        "acme", "lint", _idle, claims=(Claim("source", suffixes=(".xyz",)),)
    )
    assert reads_files(record, ctx)


# Then the filter: the claims decide, per scope.


def test_a_step_whose_claims_reach_nothing_in_scope_is_said_and_not_started(
    tmp_path: Path, registry: None, capsys: pytest.CaptureFixture[str]
) -> None:
    from dataclasses import replace

    skip_unless_installed("python")
    root = _repository(tmp_path)
    packages = discover_packages(root)
    ctx = PhaseContext(root=root, packages=packages)
    listed = catalogue(ctx)
    assert listed is not None
    assert ("src/one/__init__.py", "source") in listed["packages/one"]
    assert ("tasks.py", "configuration") in listed[ROOT_UNIT]
    ctx = replace(ctx, catalogue=listed)
    register_step(
        StepRecord(
            "acme-xyz", "lint", _idle, claims=(Claim("source", suffixes=(".xyz",)),)
        )
    )
    register_step(
        StepRecord(
            "acme-py", "lint", _idle, claims=(Claim("source", suffixes=(".py",)),)
        )
    )
    register_step(StepRecord("acme-tree", "lint", _idle))
    assert with_files(("lint.acme-xyz", "lint.acme-py", "lint.acme-tree"), ctx) == (
        "lint.acme-py",
        "lint.acme-tree",
    )
    assert (
        "lint.acme-xyz: no file it reads in the workspace; not run"
        in capsys.readouterr().out
    )
    # The root's own files count: tasks.py is configuration there.
    register_step(
        StepRecord(
            "acme-conf",
            "lint",
            _idle,
            claims=(Claim("configuration", suffixes=(".py",)),),
        )
    )
    assert reads_files(_steps.step_for("lint.acme-conf"), ctx)
    # A scoped run reads its subset and the root's files.
    scoped = replace(ctx, subset=packages)
    assert with_files(("lint.acme-xyz",), scoped) == ()
    assert "in the affected packages; not run" in capsys.readouterr().out


def test_a_package_step_skips_a_package_with_none_of_its_files(
    tmp_path: Path, registry: None, capsys: pytest.CaptureFixture[str]
) -> None:
    from dataclasses import replace

    skip_unless_installed("python")
    root = _repository(tmp_path)
    packages = discover_packages(root)
    ctx = PhaseContext(root=root, packages=packages)
    ctx = replace(ctx, catalogue=catalogue(ctx))
    ran: list[str] = []

    def body(ctx: PhaseContext) -> None:
        assert ctx.package is not None
        ran.append(ctx.package.path)

    register_step(
        StepRecord(
            "acme-cpp",
            "format",
            body,
            scope=PACKAGE,
            extensions=("python",),
            claims=(Claim("source", suffixes=(".cpp",)),),
        )
    )
    run_step("format.acme-cpp", ctx)
    assert ran == []
    assert "format.acme-cpp: packages/one has no file it reads; not run" in (
        capsys.readouterr().out
    )
    (root / "packages" / "one" / "src" / "one" / "native.cpp").write_text("int x;\n")
    ctx = replace(ctx, catalogue=catalogue(ctx))
    run_step("format.acme-cpp", ctx)
    assert ran == ["packages/one"]


# The gate over named files: `fm check <paths>`.


def test_named_paths_become_the_files_they_name(tmp_path: Path) -> None:
    from livery.workshop._quality import named_files

    root = _repository(tmp_path)
    (root / "packages" / "one" / "tests").mkdir()
    (root / "packages" / "one" / "tests" / "test_a.py").write_text("x = 1\n")
    outside = tmp_path / "elsewhere.py"
    outside.write_text("x = 1\n")
    found = named_files(
        root,
        (
            str(root / "tasks.py"),
            str(root / "packages" / "one" / "tests"),
            str(outside),
            str(root / "gone.py"),
        ),
    )
    assert found == ("packages/one/tests/test_a.py", "tasks.py")


def test_named_files_reach_only_the_steps_whose_claims_reach_them(
    tmp_path: Path,
    registry: None,
    python_steps: object,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from livery.workshop import _quality

    skip_unless_installed("python")
    root = _repository(tmp_path)
    (root / "packages" / "one" / "tests").mkdir()
    test_file = root / "packages" / "one" / "tests" / "test_a.py"
    test_file.write_text("def test_it() -> None:\n    pass\n")
    monkeypatch.chdir(root)
    calls: list[tuple[str, dict[str, object]]] = []

    def spy(name: str):
        def body(*args: object, **kwargs: object) -> None:
            calls.append((name, {"args": args, **kwargs}))

        return body

    for name in ("run_format", "run_lint", "run_typecheck", "run_test"):
        monkeypatch.setattr(fake_steps, name, spy(name))
    monkeypatch.setattr("livery.workshop._packages.verify_graph", spy("graph"))
    monkeypatch.setattr("livery.workshop._packages.rule_problems", spy("rules"))
    # A source file: the style and typecheck steps take it, the layering
    # steps over the sources judge it, and the package's whole suite
    # runs, since its tests measure that source. The graph is not made
    # of sources.
    _quality.check(str(root / "packages" / "one" / "src" / "one" / "__init__.py"))
    ran = {name for name, _ in calls}
    assert {"run_format", "run_lint", "run_typecheck", "run_test", "rules"} <= ran
    assert "graph" not in ran
    source = str(root / "packages" / "one" / "src" / "one" / "__init__.py")
    assert dict(calls)["run_format"]["paths"] == (source,)
    suite = dict(calls)["run_test"]
    assert [p.path for p in suite["packages"]] == ["packages/one"]  # type: ignore[attr-defined]
    out = capsys.readouterr().out
    assert "layering.graph: no file it reads in the named files; not run" in out
    # A test file: its package's whole suite, at the point asked.
    calls.clear()
    _quality.check(str(test_file), point="nightly")
    test_call = dict(calls)["run_test"]
    assert [p.path for p in test_call["packages"]] == ["packages/one"]  # type: ignore[attr-defined]
    assert test_call["point"] == "nightly"
    # A path no claim reaches runs nothing.
    calls.clear()
    (root / "notes.md").write_text("# notes\n")
    _quality.check(str(root / "notes.md"))
    assert calls == []


def test_the_fixers_only_walk_judges_nothing(
    tmp_path: Path,
    registry: None,
    python_steps: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from livery.workshop import _quality

    root = _repository(tmp_path)
    monkeypatch.chdir(root)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    ran: list[str] = []

    def spy(name: str):
        def body(*args: object, **kwargs: object) -> None:
            ran.append(f"{name}:{kwargs.get('safe_fix', '')}")

        return body

    for name in ("run_format", "run_lint", "run_typecheck", "run_test"):
        monkeypatch.setattr(fake_steps, name, spy(name))

    def fix(ctx: PhaseContext) -> None:
        assert ctx.safe and ctx.files == ("tasks.py",)
        ran.append("acme-fixed")

    def judge(ctx: PhaseContext) -> None:
        del ctx
        ran.append("acme-judged")

    register_step(
        StepRecord(
            "acme-fix",
            "format",
            judge,
            fix=fix,
            extension="acme.extension",
            claims=(Claim("configuration", suffixes=(".py",)),),
        )
    )
    _quality.fix_files((str(root / "tasks.py"),))
    # Every fixer the file's claims reach ran, in its in-flight mode, and
    # nothing judged: no typecheck step, no test, not the extension's judge.
    assert "run_format:True" in ran and "run_lint:True" in ran
    assert "acme-fixed" in ran
    assert "acme-judged" not in ran
    assert not any(line.startswith(("run_typecheck", "run_test")) for line in ran)
