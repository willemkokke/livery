"""CI check legs narrow to the affected packages when the contract says so."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from livery.workshop import _quality
from livery.workshop._state import RunContext


def _root(tmp_path: Path, ci: str) -> Path:
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nlayers = ["livery.workshop"]\n\n[forge]\nkind = "gitea"\n'
        f"\n[ci]\n{ci}"
    )
    return tmp_path


def _run(event: str, base: str) -> RunContext:
    return RunContext("gitea", "7", event, "refs/pull/3/merge", "a" * 40, base)


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
