"""The scale fixture: refusals first, then the members, the timings and the row."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from livery.footman import Failed
from livery.workshop import _scale
from livery.workshop._metrics import _metrics  # pyright: ignore[reportPrivateUsage]

_FAILURES = (SystemExit, Failed)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _member(root: Path, name: str) -> Path:
    member = root / "packages" / name
    (member / "src" / "livery" / name.replace("-", "_")).mkdir(parents=True)
    (member / "pyproject.toml").write_text(
        f'[project]\nname = "livery-{name}"\nversion = "0.0.0"\n'
        'requires-python = ">=3.11"\n'
    )
    (member / "workshop.toml").write_text(f'kind = "python"\nname = "livery-{name}"\n')
    return member


# --- refusals -----------------------------------------------------------------


def test_a_kind_with_no_tag_refuses_naming_the_kinds() -> None:
    with pytest.raises(_FAILURES, match="no scale tag for kind 'package-rust'"):
        _scale.member_names({"package-rust": 1})


def test_an_edit_of_a_member_with_no_module_refuses(tmp_path: Path) -> None:
    _member(tmp_path, "scale-p000")
    with pytest.raises(_FAILURES, match="packages/scale-p000: no package module"):
        _scale.edit_member(tmp_path, "scale-p000")


def test_a_fixture_with_no_python_member_has_no_leaf() -> None:
    with pytest.raises(_FAILURES, match="no python member to edit"):
        _scale.leaf([("scale-c000", "package-cpp-conan")])


def test_outside_ci_nothing_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("GITHUB_ACTIONS", "GITEA_ACTIONS", "GITLAB_CI", "GITHUB_RUN_ID"):
        monkeypatch.delenv(name, raising=False)
    line = _scale.record(tmp_path, {"job": "scale"}, sha="0" * 40)
    assert line == "  not a CI run: the timings are recorded by CI only"


def test_a_verb_that_fails_is_timed_and_its_output_printed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _Result:
        code = 3
        stdout = "the child's words\n"
        stderr = ""

    monkeypatch.setattr("livery.footman.run", lambda *a, **k: _Result())
    timing = _scale.run_verb(tmp_path, ("sync",), name="sync cold")
    assert timing.code == 3 and timing.name == "sync cold"
    out = capsys.readouterr().out
    assert "sync cold:" in out and "exit 3" in out and "the child's words" in out


def test_the_fixture_fails_naming_every_verb_that_exited_non_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _templates

    seen: list[tuple[Path, dict[str, int]]] = []

    def run(source: Path, root: Path, counts: dict[str, int]) -> list[_scale.Timing]:
        seen.append((root, counts))
        return [
            _scale.Timing(name="sync cold", ms=1.0, code=0),
            _scale.Timing(name="check cold", ms=1.0, code=1),
            _scale.Timing(name="check warm", ms=1.0, code=2),
        ]

    monkeypatch.setattr(_scale, "run_fixture", run)
    monkeypatch.setattr(_templates, "_root", lambda: tmp_path)
    with pytest.raises(_FAILURES, match="exited non-zero: check cold, check warm"):
        _scale.ci_scale(python=2, cpp=0, nanobind=0, keep=str(tmp_path / "kept"))
    # A kind counted zero is no kind of the fixture.
    assert seen == [(tmp_path / "kept", {"package-python": 2})]


# --- members ------------------------------------------------------------------


def test_members_are_named_by_kind_tag_and_index() -> None:
    assert _scale.member_names({"package-python": 2, "package-cpp-conan": 1}) == [
        ("scale-p000", "package-python"),
        ("scale-p001", "package-python"),
        ("scale-c000", "package-cpp-conan"),
    ]


def test_the_python_members_form_a_binary_tree() -> None:
    assert [_scale.parent_of(i) for i in range(7)] == [None, 0, 0, 1, 1, 2, 2]
    members = _scale.member_names({"package-python": 7})
    # The last member has no child, so the edit to it changes one member.
    assert _scale.leaf(members) == "scale-p006"
    assert all(_scale.parent_of(i) != 6 for i in range(7))


def test_generate_renders_each_member_then_the_project_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _templates

    rendered: list[tuple[str, str]] = []
    applied: list[Path] = []

    def render(root: Path, name: str, *, kind: str) -> str:
        rendered.append((name, kind))
        if kind == "package-python":
            _member(root, name)
        return name

    monkeypatch.setattr(_templates, "render_member", render)
    monkeypatch.setattr(_templates, "apply_project", applied.append)
    committed: list[str] = []
    monkeypatch.setattr(
        _scale, "_commit", lambda root, subject: committed.append(subject)
    )
    members = _scale.generate(tmp_path, {"package-python": 3, "package-cpp-conan": 1})
    assert rendered == members and len(members) == 4
    assert applied == [tmp_path]
    assert committed == ["chore: 4 generated members"]
    # Each edge is in the contract and the manifest, at one floor.
    leaf = tmp_path / "packages" / "scale-p002"
    assert (
        '[[depends]]\npath = "packages/scale-p000"\nkind = "runtime"\nfloor = "0.0.0"'
        in ((leaf / "workshop.toml").read_text())
    )
    assert "livery-scale-p000>=0.0.0" in (leaf / "pyproject.toml").read_text()
    root = tmp_path / "packages" / "scale-p000"
    assert "depends" not in (root / "workshop.toml").read_text()


# --- the copy, the timings and the row ----------------------------------------


def test_the_copy_is_the_committed_tree_alone_with_no_remote(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "--quiet", "--initial-branch=main")
    (source / "kept.txt").write_text("kept\n")
    _git(source, "add", "kept.txt")
    _git(source, "-c", "user.email=a@b.c", "-c", "user.name=A", "commit", "-qm", "x")
    _git(source, "remote", "add", "origin", "https://example.invalid/r.git")
    (source / "uncommitted.txt").write_text("no\n")
    copy = tmp_path / "copy"
    _scale.copy_tree(source, copy)
    assert (copy / "kept.txt").read_text() == "kept\n"
    assert not (copy / "uncommitted.txt").exists()
    assert _git(copy, "remote").strip() == ""
    assert _git(copy, "rev-list", "--count", "HEAD").strip() == "1"
    assert not (tmp_path / "copy.tar").exists()


def test_the_copy_loses_its_own_test_modules_and_floors(tmp_path: Path) -> None:
    _git(tmp_path, "init", "--quiet", "--initial-branch=main")
    _git(tmp_path, "config", "user.email", "a@b.c")
    _git(tmp_path, "config", "user.name", "A")
    (tmp_path / "workshop.toml").write_text("[workspace]\n")
    helped = _member(tmp_path, "helped")
    (helped / "tests").mkdir()
    (helped / "tests" / "test_helped.py").write_text("def test_x() -> None: ...\n")
    (helped / "tests" / "helped_seeds.py").write_text('"""Seeds."""\n')
    with (helped / "workshop.toml").open("a") as handle:
        handle.write("\n[qa]\ncoverage-floor = 87\n")
    bare = _member(tmp_path, "bare-one")
    (bare / "tests").mkdir()
    (bare / "tests" / "test_bare.py").write_text("def test_x() -> None: ...\n")
    # The root suite is the project render's: it stays.
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_workspace.py").write_text("")
    stripped = _scale.strip_own_suites(tmp_path)
    assert stripped == ["packages/bare-one", "packages/helped"]
    assert [p.name for p in (helped / "tests").iterdir()] == ["helped_seeds.py"]
    assert "coverage-floor = 0\n" in (helped / "workshop.toml").read_text()
    # A directory left with no module keeps one for the checkers to read.
    assert [p.name for p in (bare / "tests").iterdir()] == ["bare_one_copy.py"]
    assert (tmp_path / "tests" / "test_workspace.py").exists()
    assert _git(tmp_path, "status", "--porcelain") == ""


def test_every_verb_is_timed_in_every_state_the_cold_gate_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _member(tmp_path, "scale-p000")
    module = tmp_path / "packages/scale-p000/src/livery/scale_p000/__init__.py"
    module.write_text('"""A member."""\n\n\ndef f() -> None:\n    """F."""\n')
    (tmp_path / ".venv").mkdir()
    calls: list[tuple[str, ...]] = []

    def run(root: Path, verb: tuple[str, ...], *, name: str) -> _scale.Timing:
        calls.append(verb)
        return _scale.Timing(name=name, ms=1.0, code=0)

    monkeypatch.setattr(_scale, "run_verb", run)
    timings = _scale.measure(tmp_path, [("scale-p000", "package-python")])
    assert not (tmp_path / ".venv").exists()
    assert [t.name for t in timings] == [
        f"{verb} {state}"
        for state in ("cold", "warm", "one changed")
        for verb in ("sync", "drift.check", "check")
    ]
    assert calls[2] == ("check", "--full") and calls[5] == ("check",)
    assert module.read_text().endswith('"""F."""\n\n\nSCALE_EDIT = 1\n')


def test_the_row_reads_back_as_ci_timings_metrics() -> None:
    timings = [
        _scale.Timing(name="sync cold", ms=1500.0, code=0),
        _scale.Timing(name="check warm", ms=500.0, code=1),
    ]
    members = [("scale-p000", "package-python"), ("scale-c000", "package-cpp-conan")]
    row = _scale.row(timings, members)
    assert row["codes"] == {"sync cold": 0, "check warm": 1}
    assert row["members"] == {"package-cpp-conan": 1, "package-python": 1}
    entry: dict[str, Any] = {"jobs": {"scale": row}}
    assert _metrics(entry)["scale"] == {
        "total_ms": 2000.0,
        "task sync cold": 1500.0,
        "task check warm": 500.0,
    }
