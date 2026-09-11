"""CI check legs narrow to the affected packages when the contract says so."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from livery.workshop import _coverage_store, _quality, _verified
from livery.workshop._git_ops import GitError
from livery.workshop._packages import Package
from livery.workshop._state import RunContext
from livery.workshop._verified import read_marker


def _root(tmp_path: Path, ci: str) -> Path:
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nlayers = ["livery.workshop"]\n\n[forge]\nkind = "gitea"\n'
        f"\n[ci]\n{ci}"
    )
    return tmp_path


def _run(event: str, base: str, leg: str = "check-a") -> RunContext:
    return RunContext("gitea", "7", event, "refs/pull/3/merge", "a" * 40, base, leg=leg)


def _member(root: Path, name: str, *, suite: bool = True) -> Package:
    directory = root / "packages" / name
    directory.mkdir(parents=True)
    (directory / "workshop.toml").write_text(
        f'type = "python"\nname = "livery-{name}"\n'
    )
    if suite:
        (directory / "tests").mkdir()
    return Package(
        directory=directory,
        path=f"packages/{name}",
        name=f"livery-{name}",
        type="python",
        depends=(),
    )


def _refusal(action: Callable[[], object]) -> str:
    with pytest.raises(BaseException) as caught:
        action()
    return str(caught.value)


# --- refusals and fallbacks first ------------------------------------------


def test_a_non_boolean_key_refuses_naming_it(tmp_path: Path) -> None:
    root = _root(tmp_path, 'affected-legs = "yes"\n')
    message = _refusal(lambda: _quality.affected_legs(root))
    assert "[ci] affected-legs must be true or false" in message


def test_the_full_gate_outside_ci_without_the_key_or_off_a_pull_request(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    declared = _root(tmp_path / "on", "affected-legs = true\n")
    undeclared = _root(tmp_path / "off", 'runners = ["ubuntu-latest"]\n')
    assert _quality.ci_affected_base(declared, None) == ""
    assert _quality.ci_affected_base(undeclared, _run("pull_request", "main")) == ""
    assert _quality.ci_affected_base(declared, _run("push", "")) == ""
    assert "a push run pays the full gate" in capsys.readouterr().out
    assert _quality.ci_affected_base(declared, _run("pull_request", "")) == ""
    assert "names no base branch; full gate" in capsys.readouterr().out


def test_a_missing_merge_base_falls_open_to_everything_with_gits_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop._git_ops import GitError

    root = _root(tmp_path, "affected-legs = true\n")
    monkeypatch.setattr("livery.workshop._quality.workspace_root", lambda: root)
    monkeypatch.setattr("livery.workshop._git_ops.GitOps.fetch", lambda self: None)

    def _no_base(root: Path, git: object, *, base: str = "main") -> None:
        raise GitError("fatal: Not a valid commit name origin/develop")

    monkeypatch.setattr("livery.workshop._graph.affected_packages", _no_base)
    assert _quality._affected("develop") is None
    out = capsys.readouterr().out
    assert "no merge base with origin/develop; failing open to everything" in out
    assert "Not a valid commit name" in out


# --- the happy path ----------------------------------------------------------


def test_a_local_affected_gate_narrows_against_the_base_it_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Outside CI the caller names the base: the submit hands over the
    # branch its pull request merges into. Without one, main.
    root = _root(tmp_path, "affected-legs = true\n")
    monkeypatch.setattr("livery.workshop._quality.workspace_root", lambda: root)
    monkeypatch.setattr("livery.workshop._state.run_context", lambda: None)
    bases: list[str] = []

    def _subset(base: str = "main") -> tuple[()]:
        bases.append(base)
        return ()

    monkeypatch.setattr("livery.workshop._quality._affected", _subset)
    reasons: list[str] = []

    def _reason(base: str) -> str:
        reasons.append(base)
        return "the branch changes no files"

    monkeypatch.setattr("livery.workshop._quality._nothing_reason", _reason)
    _quality.check(affected=True, base="develop")
    assert bases == ["develop"]
    assert reasons == ["develop"]
    assert "nothing affected: the branch changes no files" in capsys.readouterr().out
    _quality.check(affected=True)
    assert bases == ["develop", "main"]


def test_a_pull_request_with_a_declared_key_narrows_against_its_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _root(tmp_path, "affected-legs = true\n")
    assert _quality.ci_affected_base(root, _run("pull_request", "develop")) == "develop"
    # The gate reads the run and scopes against that base.
    monkeypatch.setattr("livery.workshop._quality.workspace_root", lambda: root)
    # The verb imports the run context from the state module at call time.
    monkeypatch.setattr(
        "livery.workshop._state.run_context", lambda: _run("pull_request", "develop")
    )
    bases: list[str] = []

    def _subset(base: str = "main") -> tuple[()]:
        bases.append(base)
        return ()

    monkeypatch.setattr("livery.workshop._quality._affected", _subset)
    _quality.check()
    out = capsys.readouterr().out
    assert bases == ["develop"]
    assert "affected-legs: the scoped gate against origin/develop" in out
    assert "nothing affected" in out


# --- the coverage store decides which skipped suites run anyway ---------------


def test_a_leg_without_a_label_or_an_identity_runs_every_suite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _root(tmp_path, "affected-legs = true\n")
    x, y = _member(root, "x"), _member(root, "y")
    z = _member(root, "z", suite=False)
    monkeypatch.setattr(_coverage_store, "closure_id", lambda git, ps, p: "k" * 64)
    widened = _quality._with_unstored_suites(
        root, _run("pull_request", "main", ""), (x, y, z), (x,)
    )
    assert widened == (x, y)  # z has no suite to run
    assert (
        "packages/y runs, nothing to reuse (this leg has no label)"
        in capsys.readouterr().out
    )
    monkeypatch.setattr(
        _coverage_store,
        "recorded",
        lambda root, *, leg, base="main": _coverage_store.Record({}),
    )

    def _no_identity(git: object, ps: object, p: object) -> str:
        raise GitError("HEAD:packages/y is not in HEAD")

    monkeypatch.setattr(_coverage_store, "closure_id", _no_identity)
    widened = _quality._with_unstored_suites(
        root, _run("pull_request", "main"), (x, y, z), (x,)
    )
    assert widened == (x, y)
    assert "its closure has no identity" in capsys.readouterr().out


def test_a_suite_the_store_holds_stays_skipped_and_a_miss_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _root(tmp_path, "affected-legs = true\n")
    x, y, z = _member(root, "x"), _member(root, "y"), _member(root, "z")
    monkeypatch.setattr("livery.workshop._quality.workspace_root", lambda: root)
    monkeypatch.setattr("livery.workshop._quality._packages", lambda: (x, y, z))
    monkeypatch.setattr(
        "livery.workshop._state.run_context", lambda: _run("pull_request", "main")
    )
    monkeypatch.setattr("livery.workshop._quality._affected", lambda base="main": (x,))
    monkeypatch.setattr(_coverage_store, "closure_id", lambda git, ps, p: "k" * 64)
    # The record holds y at the current closure and z at another: one
    # read decides both, and only z runs.
    held = {
        "packages/y": _coverage_store.Unit("packages/y", "k" * 64, "5", "a" * 40, {}),
        "packages/z": _coverage_store.Unit("packages/z", "j" * 64, "4", "a" * 40, {}),
    }
    reads: list[tuple[str, str]] = []

    def _recorded(
        root: Path, *, leg: str, base: str = "main"
    ) -> _coverage_store.Record:
        reads.append((leg, base))
        return _coverage_store.Record(held)

    monkeypatch.setattr(_coverage_store, "recorded", _recorded)
    gated: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        "livery.workshop._quality._scoped_check",
        lambda subset, *, fix=False: gated.append(tuple(p.path for p in subset)),
    )
    _quality.check()
    out = capsys.readouterr().out
    assert gated == [("packages/x", "packages/z")]
    assert reads == [("check-a", "main")]  # no branch on this run: main's alone
    assert (
        "coverage store: packages/z runs, nothing to reuse (on this leg main's"
        " record holds it at another closure)" in out
    )
    assert "packages/y runs" not in out
    assert "affected: packages/x, packages/z" in out
    assert read_marker(root) == {
        "scope": "affected",
        "packages": ["packages/x", "packages/z"],
        "leg": "check-a",
    }
    # An unreadable record runs every skipped suite and names the reason;
    # every suite widened is the whole gate, so the widening is asked
    # directly here.
    monkeypatch.setattr(
        _coverage_store,
        "recorded",
        lambda root, *, leg, base="main": _coverage_store.Record(
            {}, failed=True, reason="down"
        ),
    )
    widened = _quality._with_unstored_suites(
        root, _run("pull_request", "main"), (x, y, z), (x,)
    )
    out = capsys.readouterr().out
    assert widened == (x, y, z)
    assert (
        "coverage store: packages/y runs, nothing to reuse (on this leg main's"
        " record could not be read (down))" in out
    )
    assert "packages/z runs, nothing to reuse (on this leg main's record" in out
    # A pull request's leg asks its own branch's record before main's, and
    # a unit either holds at its closure is skipped.
    reads.clear()
    own = _run("pull_request", "main")
    own = RunContext(
        own.forge,
        own.run_id,
        own.event,
        own.ref,
        own.head_sha,
        own.base_ref,
        leg=own.leg,
        head_ref="feat/x",
    )
    by_base = {
        "feat/x": _coverage_store.Record(
            {
                "packages/y": _coverage_store.Unit(
                    "packages/y", "k" * 64, "9", "a" * 40, {}
                )
            }
        ),
        "main": _coverage_store.Record(
            {
                "packages/z": _coverage_store.Unit(
                    "packages/z", "k" * 64, "5", "a" * 40, {}
                )
            }
        ),
    }

    def _by_base(root: Path, *, leg: str, base: str = "main") -> _coverage_store.Record:
        reads.append((leg, base))
        return by_base[base]

    monkeypatch.setattr(_coverage_store, "recorded", _by_base)
    assert _quality._with_unstored_suites(root, own, (x, y, z), (x,)) == (x,)
    assert reads == [("check-a", "feat/x"), ("check-a", "main")]
    assert "runs, nothing to reuse" not in capsys.readouterr().out


# --- a proved tree measures what main's record cannot supply ------------------


def test_a_proved_tree_measures_the_units_the_record_cannot_supply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop._backends import _python

    root = _root(tmp_path, "affected-legs = true\n")
    x, y = _member(root, "x"), _member(root, "y")
    (root / "tests").mkdir()
    monkeypatch.setattr("livery.workshop._quality.workspace_root", lambda: root)
    monkeypatch.setattr("livery.workshop._quality._packages", lambda: (x, y))
    monkeypatch.setattr(
        "livery.workshop._state.run_context", lambda: _run("push", "", "check-a")
    )
    proved = _verified.Verified(
        "t", "5", "a" * 40, "full", ("check-a",), branch="feat/x"
    )
    monkeypatch.setattr(
        "livery.workshop._quality.verified_already", lambda root: proved
    )
    monkeypatch.setattr(_coverage_store, "closure_id", lambda git, ps, p: "k" * 64)
    ran: list[tuple[str, ...]] = []

    def _run_test(*args: str, **kw: object) -> None:
        packages = kw["packages"]
        assert isinstance(packages, tuple)
        ran.append(tuple(p.path for p in packages))
        assert kw["scoped"] is True and kw["root"] == root

    monkeypatch.setattr(_python, "run_test", _run_test)
    # Every unit recorded at its closure: the leg runs nothing.
    every = {
        path: _coverage_store.Unit(path, "k" * 64, "5", "a" * 40, {})
        for path in ("packages/x", "packages/y", "tests")
    }
    reads: list[tuple[str, str]] = []

    def _every(root: Path, *, leg: str, base: str = "main") -> _coverage_store.Record:
        reads.append((leg, base))
        return _coverage_store.Record(every)

    monkeypatch.setattr(_coverage_store, "recorded", _every)
    _quality.check()
    assert ran == [] and read_marker(root)["scope"] == "verified"
    # The branch the verified row names is asked first, then main.
    assert reads == [("check-a", "feat/x"), ("check-a", "main")]
    # y and the workspace tests moved: the leg runs their tests alone,
    # measured, and leaves the measured scope naming them.
    moved = dict(every)
    moved["packages/y"] = _coverage_store.Unit(
        "packages/y", "j" * 64, "4", "a" * 40, {}
    )
    del moved["tests"]
    monkeypatch.setattr(
        _coverage_store,
        "recorded",
        lambda root, *, leg, base="main": _coverage_store.Record(moved),
    )
    _quality.check()
    out = capsys.readouterr().out
    assert ran == [("packages/y", "tests")]
    assert "measuring: packages/y, tests run for their lines alone" in out
    assert read_marker(root) == {
        "scope": "measured",
        "packages": ["packages/y", "tests"],
        "leg": "check-a",
    }


def test_a_prose_only_diff_says_so_and_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _root(tmp_path, "affected-legs = true\n")
    monkeypatch.setattr("livery.workshop._quality.workspace_root", lambda: root)
    monkeypatch.setattr(
        "livery.workshop._state.run_context", lambda: _run("pull_request", "main")
    )
    monkeypatch.setattr("livery.workshop._quality._affected", lambda base="main": ())
    monkeypatch.setattr(
        "livery.workshop._git_ops.GitOps.changed_paths",
        lambda self, base: ["notes/plan.md", "packages/x/README.md"],
    )
    _quality.check()
    out = capsys.readouterr().out
    assert (
        "nothing affected: only prose and site files changed (2 file(s) under"
        " notes/, markdown, the root docs/ tree, or zensical.toml); the gate"
        " skips, the site build judges them" in out
    )
    assert read_marker(root)["scope"] == "nothing"
    # The site's own files count the same way: a docs-only change
    # with the root zensical.toml among it runs no gate on the legs.
    monkeypatch.setattr(
        "livery.workshop._git_ops.GitOps.changed_paths",
        lambda self, base: ["zensical.toml", "docs/assets/logo.svg", "notes/a.md"],
    )
    _quality.check()
    assert "only prose and site files changed (3 file(s)" in capsys.readouterr().out
    monkeypatch.setattr(
        "livery.workshop._git_ops.GitOps.changed_paths", lambda self, base: []
    )
    _quality.check()
    assert "nothing affected: the branch changes no files" in capsys.readouterr().out
