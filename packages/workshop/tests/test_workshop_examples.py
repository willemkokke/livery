"""Documentation examples as files: the collector the workshop's pytest plugin adds."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from livery.workshop import is_example
from livery.workshop._pytest_examples import MARKER


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
    # The refusals first: the examples' setup, a page beside them, a
    # stub, a test, and an examples directory that is not under docs.
    assert not is_example(Path("packages/x/docs/examples/conftest.py"))
    assert not is_example(Path("packages/x/docs/examples/notes.md"))
    assert not is_example(Path("packages/x/docs/examples/guide.pyi"))
    assert not is_example(Path("packages/x/tests/test_examples.py"))
    assert not is_example(Path("packages/x/docs/pages/examples/guide.py"))
    assert is_example(Path("packages/x/docs/examples/guide.py"))
    assert is_example(Path("docs/examples/deep/er/guide.py"))
    assert is_example(tmp_path / "packages" / "x" / "docs" / "examples" / "guide.py")
    base = _examples(tmp_path)
    run = _pytest(str(base), "-v", cwd=tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "ok.py::ok PASSED" in run.stdout
    assert "conftest" not in run.stdout.split("PASSED")[0]
