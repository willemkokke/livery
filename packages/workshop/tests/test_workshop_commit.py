"""The commit verb: refusals first, then the scope, the check, and the commit."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from livery.footman import Failed
from livery.workshop import _commit
from livery.workshop._commit import commit, scope_of

_FAILURES = (SystemExit, Failed)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def _rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A workspace with one package, seeded on main, on a feature branch."""
    root = tmp_path / "ws"
    root.mkdir()
    _git(root, "init", "-q", "--initial-branch=main")
    _git(root, "config", "user.name", "T")
    _git(root, "config", "user.email", "t@livery.local")
    (root / "workshop.toml").write_text(
        '[workspace]\nlayers = ["livery.workshop"]\n\n[forge]\nkind = "gitea"\n'
        'owner = "owner"\n'
    )
    package = root / "packages" / "x"
    package.mkdir(parents=True)
    (package / "workshop.toml").write_text('type = "python"\nname = "livery-x"\n')
    (package / "pyproject.toml").write_text(
        '[project]\nname = "livery-x"\nversion = "0.1.0"\n'
    )
    (package / "a.py").write_text("A = 1\n")
    (root / "README.md").write_text("the repository\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "chore: seed")
    _git(root, "checkout", "-qb", "feat/1-x")
    monkeypatch.chdir(root)
    return root


def _subject_and_body(root: Path) -> tuple[str, str]:
    subject = _git(root, "log", "-1", "--format=%s").strip()
    body = _git(root, "log", "-1", "--format=%b").strip()
    return subject, body


# --- refusals ------------------------------------------------------------------


def test_the_type_and_subject_are_refused_before_anything_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _rig(tmp_path, monkeypatch)
    (root / "packages" / "x" / "b.py").write_text("B = 2\n")
    with pytest.raises(_FAILURES, match="the type is one of feat, fix"):
        commit("wat", "does a thing", check=False)
    with pytest.raises(_FAILURES, match="name the subject"):
        commit("feat", "   ", check=False)
    assert _git(root, "status", "--porcelain").strip().startswith("??")  # unstaged


def test_main_and_a_reserved_branch_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _rig(tmp_path, monkeypatch)
    _git(root, "checkout", "-q", "main")
    (root / "packages" / "x" / "b.py").write_text("B = 2\n")
    with pytest.raises(_FAILURES, match="on main: start a branch first"):
        commit("feat", "adds b", check=False)
    _git(root, "checkout", "-qb", "workflow/release/x")
    with pytest.raises(_FAILURES, match="reserved branch"):
        commit("feat", "adds b", check=False)


def test_nothing_to_commit_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _rig(tmp_path, monkeypatch)
    with pytest.raises(_FAILURES, match="nothing to commit"):
        commit("feat", "adds nothing", check=False)


# --- the scope, the check, and the commit ---------------------------------------


def test_the_scope_is_the_packages_the_staged_change_touches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _rig(tmp_path, monkeypatch)
    (root / "packages" / "y").mkdir()
    (root / "packages" / "y" / "workshop.toml").write_text(
        'type = "python"\nname = "livery-y"\n'
    )
    (root / "packages" / "y" / "pyproject.toml").write_text(
        '[project]\nname = "livery-y"\nversion = "0.1.0"\n'
    )
    assert scope_of(root, ["packages/x/a.py"]) == "x"
    assert scope_of(root, ["packages/x/a.py", "packages/y/workshop.toml"]) == "x,y"
    assert scope_of(root, ["README.md", "notes/plan.md"]) == ""
    assert scope_of(root, ["packages/xylophone/a.py"]) == ""


def test_a_commit_runs_the_check_stages_everything_and_names_the_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _rig(tmp_path, monkeypatch)
    checks: list[str] = []
    monkeypatch.setattr(_commit, "_run_check", lambda: checks.append("ran"))
    (root / "packages" / "x" / "b.py").write_text("B = 2\n")
    commit("feat", "adds b", body="B is the second letter.")
    out = capsys.readouterr().out
    assert checks == ["ran"]
    assert "scope: x (from the changed paths)" in out and "committed " in out
    subject, body = _subject_and_body(root)
    assert subject == "feat(x): adds b" and body == "B is the second letter."
    assert _git(root, "status", "--porcelain").strip() == ""
    # A root file carries no scope; --no-check skips the reflex.
    (root / "README.md").write_text("the repository, again\n")
    commit("docs", "says it again", check=False)
    assert checks == ["ran"]
    assert _subject_and_body(root)[0] == "docs: says it again"


def test_a_given_scope_a_break_and_only_win(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _rig(tmp_path, monkeypatch)
    (root / "packages" / "x" / "b.py").write_text("B = 2\n")
    (root / "packages" / "x" / "c.py").write_text("C = 3\n")
    commit(
        "feat",
        "adds b alone",
        scope="ws",
        breaking=True,
        only="packages/x/b.py",
        check=False,
    )
    assert _subject_and_body(root)[0] == "feat(ws)!: adds b alone"
    # c.py stayed unstaged, for the next commit.
    assert "?? packages/x/c.py" in _git(root, "status", "--porcelain")
    commit("feat", "adds c", check=False)
    assert _subject_and_body(root)[0] == "feat(x): adds c"


def test_a_red_check_stops_before_anything_is_staged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _rig(tmp_path, monkeypatch)

    def _red() -> None:
        raise SystemExit("  check: red")

    monkeypatch.setattr(_commit, "_run_check", _red)
    (root / "packages" / "x" / "b.py").write_text("B = 2\n")
    with pytest.raises(SystemExit):
        commit("feat", "adds b")
    assert _git(root, "status", "--porcelain").strip().startswith("??")
    assert _subject_and_body(root)[0] == "chore: seed"
