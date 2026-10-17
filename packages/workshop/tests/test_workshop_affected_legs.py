"""CI check legs narrow to the affected packages when the contract says so."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from livery import git
from livery.workshop import _quality, leg_scope
from livery.workshop._ci_context import RunContext
from livery.workshop._gate_record import Plan
from livery.workshop._packages import Package


def _root(tmp_path: Path, ci: str, listed: str = "[]") -> Path:
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "workshop.toml").write_text(
        f'[workspace]\nextensions = {listed}\n\n[forge]\nkind = "gitea"\n\n[ci]\n{ci}'
    )
    return tmp_path


def _run(event: str, base: str, leg: str = "check-a") -> RunContext:
    return RunContext("gitea", "7", event, "refs/pull/3/merge", "a" * 40, base, leg=leg)


def _push(branch: str, leg: str = "check-a") -> RunContext:
    """A push of *branch*: the ref names the branch, the payload no base."""
    return RunContext(
        "gitea", "7", "push", f"refs/heads/{branch}", "a" * 40, "", leg=leg
    )


def _member(root: Path, name: str, *, suite: bool = True) -> Package:
    directory = root / "packages" / name
    directory.mkdir(parents=True)
    (directory / "workshop.toml").write_text(
        f'extensions = ["python"]\nname = "livery-{name}"\n'
    )
    if suite:
        (directory / "tests").mkdir()
    return Package(
        directory=directory,
        path=f"packages/{name}",
        name=f"livery-{name}",
        depends=(),
        extensions=("python",),
    )


def _stepping(
    monkeypatch: pytest.MonkeyPatch, *paths: str, bases: list[str] | None = None
) -> list[str]:
    """Hand the leg a plan stepping from tree ``b...`` over *paths*; the bases asked."""
    asked = [] if bases is None else bases

    def _plan(root: Path, base: str, run: RunContext) -> Plan:
        asked.append(base)
        return Plan("step", "t" * 40, base_tree="b" * 40, paths=tuple(paths))

    monkeypatch.setattr("livery.workshop._quality._leg_plan", _plan)
    return asked


def _refusal(action: Callable[[], object]) -> str:
    with pytest.raises(BaseException) as caught:
        action()
    return str(caught.value)


# --- refusals and fallbacks first ------------------------------------------


def test_a_non_boolean_key_refuses_naming_it(tmp_path: Path) -> None:
    root = _root(tmp_path, 'affected-legs = "yes"\n')
    message = _refusal(lambda: _quality.affected_legs(root))
    assert "ci.affected-legs is a string ('yes'); it takes true or false" in message


def test_the_full_gate_outside_ci_without_the_key_or_off_a_pull_request(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    declared = _root(tmp_path / "on", "affected-legs = true\n")
    undeclared = _root(tmp_path / "off", 'runners = ["ubuntu-latest"]\n')
    assert _quality.ci_affected_base(declared, None) == ""
    assert _quality.ci_affected_base(undeclared, _run("pull_request", "main")) == ""
    # A push's legs take their plan from the record (`ci_push_base`),
    # on a branch alone; a dispatch pays the whole gate and says so.
    assert _quality.ci_affected_base(declared, _run("push", "")) == ""
    assert capsys.readouterr().out == ""
    assert _quality.ci_push_base(declared, _push("main")) == "main"
    assert _quality.ci_push_base(undeclared, _push("main")) == ""
    assert _quality.ci_push_base(declared, _run("push", "")) == ""
    assert _quality.ci_push_base(declared, _run("pull_request", "main")) == ""
    assert _quality.ci_affected_base(declared, _run("workflow_dispatch", "")) == ""
    assert "a workflow_dispatch run pays the full gate" in capsys.readouterr().out
    assert _quality.ci_affected_base(declared, _run("pull_request", "")) == ""
    assert "names no base branch; full gate" in capsys.readouterr().out


def test_a_push_with_no_proved_tree_in_reach_pays_the_whole_gate_never_the_merge_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop import _gate_record

    root = _root(tmp_path, "affected-legs = true\n")
    monkeypatch.setattr(git, "fetch", lambda root: None)
    plans = iter(
        [
            Plan(
                _gate_record.MERGE_BASE,
                "t" * 40,
                base_tree="t" * 40,
                paths=(),
                why="CI's record proves no tree in reach",
            ),
            Plan(
                "step",
                "t" * 40,
                base_tree="b" * 40,
                paths=("a.py",),
                why="proved by run 8",
            ),
        ]
    )
    monkeypatch.setattr(
        _gate_record, "leg_plan", lambda root, *, base, branch, leg: next(plans)
    )
    # The fallback first: on main the merge base is HEAD, so the step
    # would be empty; the push pays the whole gate instead.
    step = _quality._leg_plan(root, "main", _push("main"))
    assert step.mode == "full" and _quality._affected(root, step) is None
    assert (
        "affected: CI's record proves no tree in reach; a push pays the whole gate;"
        " everything runs" in capsys.readouterr().out
    )
    step = _quality._leg_plan(root, "main", _push("main"))
    assert step.mode == "step" and step.base_tree == "b" * 40
    assert "since tree bbbbbbbbbbbb (proved by run 8): 1 path(s) changed" in (
        capsys.readouterr().out
    )


def test_a_push_narrows_from_the_records_nearest_proved_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _root(tmp_path, "affected-legs = true\n")
    monkeypatch.setattr("livery.workshop._quality.workspace_root", lambda: root)
    monkeypatch.setattr(
        "livery.workshop._ci_context.run_context", lambda: _push("main")
    )
    bases = _stepping(monkeypatch)
    monkeypatch.setattr("livery.workshop._quality._affected", lambda root, step: ())
    _quality.check()
    out = capsys.readouterr().out
    assert bases == ["main"]
    assert "affected-legs: the scoped gate from the record's nearest proved tree" in out
    assert "nothing affected" in out
    assert leg_scope(root).base_tree == "b" * 40


def test_a_missing_merge_base_falls_open_to_everything_with_gits_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _root(tmp_path, "affected-legs = true\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "seed"],
        cwd=root,
        check=True,
    )
    monkeypatch.setattr(git, "fetch", lambda root: None)
    step = _quality._leg_plan(root, "develop", _run("pull_request", "develop"))
    assert step.mode == "full"
    assert _quality._affected(root, step) is None
    out = capsys.readouterr().out
    assert "affected: no merge base with origin/develop (" in out
    assert "failing open to everything" in out


def test_a_leg_says_where_it_measures_from_and_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop import _gate_record

    root = _root(tmp_path, "affected-legs = true\n")
    monkeypatch.setattr(git, "fetch", lambda root: None)
    plans = iter(
        [
            Plan(
                _gate_record.MERGE_BASE,
                "t" * 40,
                base_tree="m" * 40,
                paths=("a.py",),
                why="CI's record proves no tree in reach",
            ),
            Plan(
                "step",
                "t" * 40,
                base_tree="b" * 40,
                paths=("a.py", "b.py"),
                why="proved by run 8",
            ),
        ]
    )
    asked: list[tuple[str, str, str]] = []

    def _plan(root: Path, *, base: str, branch: str, leg: str) -> Plan:
        asked.append((base, branch, leg))
        return next(plans)

    monkeypatch.setattr(_gate_record, "leg_plan", _plan)
    run = RunContext(
        "github",
        "7",
        "pull_request",
        "refs/pull/3/merge",
        base_ref="main",
        head_ref="feat/x",
        leg="check-a",
    )
    _quality._leg_plan(root, "main", run)
    assert (
        "from the merge base, tree mmmmmmmmmmmm: CI's record proves no tree in"
        " reach; 1 path(s) changed" in capsys.readouterr().out
    )
    _quality._leg_plan(root, "main", run)
    assert (
        "since tree bbbbbbbbbbbb (proved by run 8): 2 path(s) changed"
        in capsys.readouterr().out
    )
    # The leg hands its own label on, so the record is read for its platform.
    assert asked == [("main", "feat/x", "check-a"), ("main", "feat/x", "check-a")]


# --- the happy path ----------------------------------------------------------


def test_a_local_gate_is_the_reflex_and_hands_its_base_to_the_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Outside CI the gate is the reflex: the plan names what changed
    # since the nearest proved tree, and the base the caller names is
    # where the chain's root is looked for (the submit hands over the
    # branch its pull request merges into; without one, main).
    from livery.workshop import _gate_record

    root = _root(tmp_path, "affected-legs = true\n")
    monkeypatch.setattr("livery.workshop._quality.workspace_root", lambda: root)
    monkeypatch.setattr("livery.workshop._ci_context.run_context", lambda: None)
    bases: list[str] = []

    def _plan(root: Path, *, base: str = "main") -> _gate_record.Plan:
        bases.append(base)
        return _gate_record.Plan(
            "step", "t" * 40, base_tree="b" * 40, paths=("notes/x.md",)
        )

    monkeypatch.setattr(_gate_record, "plan", _plan)
    recorded: list[dict[str, object]] = []

    def _remember(root: Path, **kw: object) -> str:
        recorded.append(kw)
        return "  gate record: recorded"

    monkeypatch.setattr(_gate_record, "remember", _remember)
    monkeypatch.setattr("livery.workshop._quality._packages", lambda: ())
    # The changed file is on disk: a path that is not reads as deleted,
    # which every link checker must answer by reading every page.
    (root / "notes").mkdir(exist_ok=True)
    (root / "notes" / "x.md").write_text("# x\n")
    _quality.check(base="develop")
    assert bases == ["develop"]
    out = capsys.readouterr().out
    assert "since tree bbbbbbbbbbbb: 1 path(s) changed" in out
    assert "nothing affected: no file a step reads changed" in out
    assert recorded == [{"tree": "t" * 40, "packages": (), "base_tree": "b" * 40}]
    _quality.check()
    assert bases == ["develop", "main"]


def test_a_fix_run_records_the_tree_the_rewriters_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The plan measures the tree before the rewriters run. The row
    # names the tree the judges read, so a fix that moves a file does
    # not leave the proved tree one rewrite behind the commit.
    from livery.workshop import _gate_record

    root = _root(tmp_path, "affected-legs = true\n")
    x, y = _member(root, "x"), _member(root, "y")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    monkeypatch.setattr("livery.workshop._quality.workspace_root", lambda: root)
    monkeypatch.setattr("livery.workshop._ci_context.run_context", lambda: None)
    monkeypatch.delenv("CI", raising=False)  # the fix guard is its own test

    def _plan(root: Path, *, base: str = "main") -> _gate_record.Plan:
        return _gate_record.Plan(
            "step", "t" * 40, base_tree="b" * 40, paths=("packages/x/a.py",)
        )

    monkeypatch.setattr(_gate_record, "plan", _plan)
    recorded: list[dict[str, object]] = []

    def _remember(root: Path, **kw: object) -> str:
        recorded.append(kw)
        return "  gate record: recorded"

    monkeypatch.setattr(_gate_record, "remember", _remember)
    monkeypatch.setattr("livery.workshop._quality._packages", lambda: (x, y))
    from livery.workshop._graph import Scope

    monkeypatch.setattr(
        "livery.workshop._graph.affected_from_paths",
        lambda root, packages, paths, **attribution: Scope((x,)),
    )

    judged: list[bool] = []

    def _scoped(
        subset: tuple[Package, ...],
        *,
        fix: bool = False,
        tests: object = None,
        examples: tuple[str, ...] = (),
        between: Callable[[], None] | None = None,
        point: str = "",
        safe: bool = False,
        changes: object = None,
    ) -> None:
        # The walk's fixers rewrite, then the hook between them and the
        # judges measures the tree the judges read.
        judged.append(fix)
        if fix:
            (root / "packages" / "x" / "a.py").write_text("x = 2\n")
            assert between is not None
            between()

    monkeypatch.setattr("livery.workshop._quality._scoped_check", _scoped)
    monkeypatch.setattr("livery.workshop._quality.drift_check", lambda: None)
    monkeypatch.setattr("livery.workshop._provenance.provenance_check", lambda: None)
    _quality.check(fix=True)
    # The walk ran the fixers, and the row names the tree they left.
    assert judged == [True]
    left = git.working_tree_id(root)
    assert left != "t" * 40
    assert recorded == [
        {"tree": left, "packages": ("packages/x",), "base_tree": "b" * 40}
    ]
    # Without --fix nothing rewrites, and the plan's tree is the row's.
    judged.clear()
    recorded.clear()
    _quality.check()
    assert judged == [False]
    assert recorded[0]["tree"] == "t" * 40


def test_a_test_only_delta_runs_its_packages_whole_suite_and_not_the_dependents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop import _gate_record

    root = _root(tmp_path, "affected-legs = true\n")
    x, y = _member(root, "x"), _member(root, "y")
    monkeypatch.setattr("livery.workshop._quality.workspace_root", lambda: root)
    monkeypatch.setattr("livery.workshop._ci_context.run_context", lambda: None)
    paths = ["packages/x/tests/test_a.py"]
    # The files are there: a changed test file that is gone runs its
    # package's suite instead of being handed to pytest.
    for named in ("packages/x/tests/test_a.py", "packages/y/tests/test_b.py"):
        (root / named).write_text("def test_it() -> None:\n    pass\n")

    def _plan(root: Path, *, base: str = "main") -> _gate_record.Plan:
        return _gate_record.Plan(
            "step", "t" * 40, base_tree="b" * 40, paths=tuple(paths)
        )

    monkeypatch.setattr(_gate_record, "plan", _plan)
    monkeypatch.setattr(
        _gate_record, "remember", lambda root, **kw: "  gate record: recorded"
    )
    monkeypatch.setattr("livery.workshop._quality._packages", lambda: (x, y))
    seen: list[tuple[tuple[str, ...], dict[str, tuple[str, ...]]]] = []

    def _scoped(
        subset: tuple[Package, ...],
        *,
        fix: bool = False,
        tests: dict[str, tuple[str, ...]] | None = None,
        examples: tuple[str, ...] = (),
        between: Callable[[], None] | None = None,
        point: str = "",
        safe: bool = False,
        changes: object = None,
    ) -> None:
        seen.append((tuple(p.path for p in subset), dict(tests or {})))

    monkeypatch.setattr("livery.workshop._quality._scoped_check", _scoped)
    monkeypatch.setattr("livery.workshop._quality.drift_check", lambda: None)
    monkeypatch.setattr("livery.workshop._provenance.provenance_check", lambda: None)
    _quality.check()
    out = capsys.readouterr().out
    assert "affected: packages/x" in out
    # The package's whole suite, named by no file, and no dependent.
    assert seen == [(("packages/x",), {})]
    # Test files in every package: every package's whole suite, which
    # is the whole walk; the scoped one does not run.
    seen.clear()
    walked: list[object] = []
    monkeypatch.setattr(
        "livery.workshop._quality.walk", lambda ctx, **kw: walked.append(ctx.subset)
    )
    paths.append("packages/y/tests/test_b.py")
    _quality.check()
    assert seen == []
    assert walked == [None]
    assert "every package is affected: everything runs" in capsys.readouterr().out


def test_a_pull_request_with_a_declared_key_narrows_against_its_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _root(tmp_path, "affected-legs = true\n")
    assert _quality.ci_affected_base(root, _run("pull_request", "develop")) == "develop"
    # The gate reads the run and scopes against that base.
    monkeypatch.setattr("livery.workshop._quality.workspace_root", lambda: root)
    # The verb imports the run context from the state module at call time.
    monkeypatch.setattr(
        "livery.workshop._ci_context.run_context",
        lambda: _run("pull_request", "develop"),
    )
    bases = _stepping(monkeypatch)
    monkeypatch.setattr("livery.workshop._quality._affected", lambda root, step: ())
    _quality.check()
    out = capsys.readouterr().out
    assert bases == ["develop"]
    assert "affected-legs: the scoped gate against origin/develop" in out
    assert "nothing affected" in out
    assert leg_scope(root).base_tree == "b" * 40


def test_a_prose_only_diff_says_so_and_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _root(tmp_path, "affected-legs = true\n")
    monkeypatch.setattr("livery.workshop._quality.workspace_root", lambda: root)
    monkeypatch.setattr(
        "livery.workshop._ci_context.run_context", lambda: _run("pull_request", "main")
    )
    monkeypatch.setattr("livery.workshop._quality._affected", lambda root, step: ())
    _stepping(monkeypatch, "notes/plan.md", "packages/x/README.md")
    for changed in ("notes/plan.md", "packages/x/README.md", "notes/a.md"):
        (root / changed).parent.mkdir(parents=True, exist_ok=True)
        (root / changed).write_text("# changed\n")
    (root / "packages" / "x" / "workshop.toml").write_text(
        'extensions = ["python"]\nname = "acme-x"\n'
    )
    (root / "packages" / "x" / "pyproject.toml").write_text(
        '[project]\nname = "acme-x"\n'
    )
    (root / "zensical.toml").write_text("")
    (root / "docs" / "assets").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "assets" / "logo.svg").write_text("<svg/>")
    _quality.check()
    out = capsys.readouterr().out
    assert (
        "nothing affected: only prose and site files changed (2 file(s) under"
        " notes/, markdown, the root docs/ tree, or zensical.toml); the gate"
        " skips, the site build judges them" in out
    )
    assert leg_scope(root).scope == "nothing"
    # The site's own files count the same way: a docs-only change
    # with the root zensical.toml among it runs no gate on the legs.
    _stepping(monkeypatch, "zensical.toml", "docs/assets/logo.svg", "notes/a.md")
    _quality.check()
    assert "only prose and site files changed (3 file(s)" in capsys.readouterr().out
    _stepping(monkeypatch)
    _quality.check()
    assert "nothing affected: the branch changes no files" in capsys.readouterr().out
