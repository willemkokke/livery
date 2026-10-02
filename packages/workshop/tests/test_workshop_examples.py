"""Documentation examples as files: the collector, the runner, the check."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from livery.footman.api import Failed
from livery.workshop._packages import Package
from livery.workshop._pytest_examples import MARKER, is_example


def _examples(tmp_path: Path) -> Path:
    base = tmp_path / "packages" / "x" / "docs" / "examples"
    base.mkdir(parents=True)
    (base / "ok.py").write_text(
        "from dataclasses import dataclass\n\n\n@dataclass\nclass Pair:\n"
        "    a: int\n\n\nassert Pair(1).a == 1\n"
    )
    (base / "conftest.py").write_text(
        "import pytest\n\n\n@pytest.hookimpl(wrapper=True)\n"
        "def pytest_runtest_call(item):\n"
        f"    assert item.get_closest_marker({MARKER!r}) is not None\n"
        "    item.config.stash.setdefault('seen', []).append(item.name)\n"
        "    return (yield)\n"
    )
    return base


def _pytest(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-n", "0", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def test_an_example_that_raises_reports_its_own_file_and_line(tmp_path: Path) -> None:
    base = _examples(tmp_path)
    (base / "bad.py").write_text("x = 1\ny = 2\nz = x / 0\n")
    run = _pytest(str(base), cwd=tmp_path)
    assert run.returncode == 1, run.stdout + run.stderr
    assert "bad.py:3" in run.stdout and "ZeroDivisionError" in run.stdout
    assert "1 failed, 1 passed" in run.stdout, run.stdout


def test_a_named_example_runs_whole_once_and_its_test_functions_never(
    tmp_path: Path,
) -> None:
    # Pytest collects a file named on its command line as a test module,
    # and one matching python_files by pattern in a directory it walks;
    # either would import the example outside its setup and run its
    # test functions as the workspace's tests.
    base = _examples(tmp_path)
    ran = tmp_path / "ran.txt"
    body = (
        "from pathlib import Path\n\n"
        f"with Path({str(ran)!r}).open('a') as out:\n"
        "    out.write('ran\\n')\n\n\n"
        "def test_boom() -> None:\n"
        "    raise SystemExit('an example function ran as a test')\n"
    )
    (base / "guide.py").write_text(body)
    (base / "test_guide.py").write_text(body)
    run = _pytest(
        str(base / "guide.py"), str(base / "test_guide.py"), "-v", cwd=tmp_path
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "guide.py::guide PASSED" in run.stdout
    assert "test_guide.py::test_guide PASSED" in run.stdout
    assert "test_boom" not in run.stdout
    assert ran.read_text() == "ran\nran\n"
    ran.unlink()
    run = _pytest(str(base), "-v", cwd=tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "test_boom" not in run.stdout
    assert ran.read_text() == "ran\nran\n"


def test_only_example_files_are_collected_and_a_conftest_never_is(
    tmp_path: Path,
) -> None:
    assert not is_example(Path("packages/x/docs/examples/conftest.py"))
    assert not is_example(Path("packages/x/docs/examples/notes.md"))
    assert not is_example(Path("packages/x/tests/test_examples.py"))
    assert is_example(Path("packages/x/docs/examples/guide.py"))
    assert is_example(Path("docs/examples/deep/er/guide.py"))
    base = _examples(tmp_path)
    run = _pytest(str(base), "-v", cwd=tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "ok.py::ok PASSED" in run.stdout
    assert "conftest" not in run.stdout.split("PASSED")[0]


def _package(tmp_path: Path) -> Package:
    directory = tmp_path / "packages" / "x"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "workshop.toml").write_text('kind = "python"\nname = "acme-x"\n')
    (directory / "pyproject.toml").write_text('[project]\nname = "acme-x"\n')
    from livery.workshop._packages import discover_packages

    (tmp_path / "workshop.toml").write_text("[workspace]\n")
    return next(p for p in discover_packages(tmp_path) if p.path == "packages/x")


class _Result:
    def __init__(self, code: int) -> None:
        self.code = code
        self.stdout = "out\n"
        self.stderr = "err\n"


class _Pytest:
    def __init__(self, code: int) -> None:
        self.code = code
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def opts(self, **kwargs: object) -> _Pytest:
        self.calls.append((("opts",), kwargs))
        return self

    def __call__(self, *args: str) -> _Result:
        self.calls.append((args, {}))
        return _Result(self.code)


def test_the_runner_says_so_without_examples_and_names_a_red_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop._backends import _python

    package = _package(tmp_path)
    fake = _Pytest(code=1)
    monkeypatch.setattr(_python, "pytest", fake)
    _python.run_examples(package, tmp_path)
    assert "examples: packages/x has none" in capsys.readouterr().out
    assert fake.calls == []
    _examples(tmp_path)
    with pytest.raises(Failed, match="examples of packages/x: pytest exited 1"):
        _python.run_examples(package, tmp_path)
    out = capsys.readouterr().out
    assert "out" in out and "err" in out
    assert fake.calls[-1][0] == ("packages/x/docs/examples",)
    assert fake.calls[-2][1] == {"in_process": False, "cwd": tmp_path, "nofail": True}
    fake.code = 0
    _python.run_examples(package, tmp_path)
    # Named examples run alone; a named conftest is every example's
    # setup, so the directory runs.
    ok = str(tmp_path / "packages/x/docs/examples/ok.py")
    conftest = str(tmp_path / "packages/x/docs/examples/conftest.py")
    _python.run_examples(package, tmp_path, (ok,))
    assert fake.calls[-1][0] == (ok,)
    _python.run_examples(package, tmp_path, (ok, conftest))
    assert fake.calls[-1][0] == ("packages/x/docs/examples",)


def test_the_kind_names_its_runner_and_a_child_inherits_it() -> None:
    from livery.workshop._backends import _python
    from livery.workshop._kinds import kind_examples

    assert kind_examples("python") is _python.run_examples
    assert kind_examples("python-nanobind") is _python.run_examples
    assert kind_examples("cpp-conan") is None


def test_the_check_runs_the_kinds_runner_and_skips_a_tests_only_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop._backends import _python
    from livery.workshop._checks import GateContext, check_for
    from livery.workshop._packages import discover_packages

    record = check_for("examples.pytest")
    assert record.role == "examples" and record.kinds == ("python",)
    assert [claim.category for claim in record.claims] == ["example"]
    assert record.tools == ("pytest",)
    package = _package(tmp_path)
    ran: list[tuple[str, tuple[str, ...]]] = []
    # The record holds the runner itself, so the lookup is the seam.
    from livery.workshop import _kinds

    monkeypatch.setattr(
        _kinds,
        "kind_examples",
        lambda kind: lambda package, root, files=(): ran.append((package.path, files)),
    )
    del _python
    monkeypatch.setattr("livery.workshop._quality.workspace_root", lambda: tmp_path)
    packages = discover_packages(tmp_path)
    # A whole walk runs the package's directory, whatever its catalogue
    # lists; a run over named files hands the runner the named examples.
    listed = {"packages/x": (("docs/examples/ok.py", "example"),)}
    record.run(GateContext(root=tmp_path, packages=packages, catalogue=listed))
    assert ran == [("packages/x", ())]
    ran.clear()
    record.run(
        GateContext(
            root=tmp_path,
            packages=packages,
            subset=(package,),
            files=("packages/x/docs/examples/ok.py",),
            catalogue=listed,
        )
    )
    example = str(tmp_path / "packages/x/docs/examples/ok.py")
    assert ran == [("packages/x", (example,))]
    ran.clear()
    record.run(GateContext(root=tmp_path, packages=packages))
    assert ran == [("packages/x", ())]
    # Scoped, with its tests alone changed: the examples did not move.
    record.run(
        GateContext(
            root=tmp_path,
            packages=packages,
            subset=(package,),
            tests={"packages/x": ("packages/x/tests/test_a.py",)},
        )
    )
    assert ran == [("packages/x", ())]
    # Scoped, with its examples alone changed: they run.
    record.run(
        GateContext(
            root=tmp_path,
            packages=packages,
            subset=(package,),
            examples=("packages/x",),
        )
    )
    assert ran == [("packages/x", ()), ("packages/x", ())]
    del capsys
