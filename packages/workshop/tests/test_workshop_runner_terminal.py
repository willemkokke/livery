"""The python extension's runner-terminal step: refusals first, then the mount."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.extensions.python._parse import parsed_modules
from livery.extensions.python._terminal import problems
from livery.workshop._packages import discover_packages


def _package(root: Path, name: str) -> None:
    directory = root / "packages" / name
    directory.mkdir(parents=True)
    (directory / "workshop.toml").write_text(
        f'kind = "python"\nname = "livery-{name}"\n'
    )
    (directory / "pyproject.toml").write_text(
        f'[project]\nname = "livery-{name}"\ndependencies = []\n'
    )


def _module(root: Path, package: str, name: str, body: str) -> Path:
    src = root / "packages" / package / "src"
    src.mkdir(parents=True, exist_ok=True)
    path = src / name
    path.write_text(body)
    return path


def _judged(root: Path) -> list[str]:
    return problems(parsed_modules(root, discover_packages(root)))


# The refusals first.


def test_a_module_importing_the_runner_may_not_ask_the_terminal(
    tmp_path: Path,
) -> None:
    _package(tmp_path, "tool")
    _module(
        tmp_path,
        "tool",
        "verbs.py",
        "import sys\nimport livery.footman as footman\n\n"
        "def go() -> bool:\n    return sys.stdin.isatty()\n",
    )
    (found,) = _judged(tmp_path)
    assert "attended()" in found
    assert "sys.stdin.isatty()" in found
    # The stdout spelling is the same question, and so is a name
    # imported from sys directly.
    _module(
        tmp_path,
        "tool",
        "verbs.py",
        "from sys import stdout\nfrom livery import footman\n\n"
        "def go() -> bool:\n    return stdout.isatty()\n",
    )
    (found,) = _judged(tmp_path)
    assert "attended()" in found
    # stderr and os.isatty ask the same terminal.
    _module(
        tmp_path,
        "tool",
        "verbs.py",
        "import os\nimport sys\nimport livery.footman as footman\n\n"
        "def go() -> bool:\n    return sys.stderr.isatty() or os.isatty(1)\n",
    )
    both = _judged(tmp_path)
    assert len(both) == 2
    assert all("attended()" in line for line in both)
    assert any("os.isatty()" in line for line in both)


def test_the_root_tasks_file_is_judged_by_its_path(tmp_path: Path) -> None:
    (tmp_path / "tasks.py").write_text(
        "import sys\nfrom livery.footman import task\n\n"
        "def go() -> bool:\n    return sys.stdout.isatty()\n"
    )
    assert _judged(tmp_path) == [
        "tasks.py asks the terminal with sys.stdout.isatty(): a module that"
        " imports the runner asks livery.footman.attended() instead, which knows"
        " --no-input and --dry-run as well"
    ]


# Then what may ask.


def test_the_runner_and_plain_application_code_may_ask(tmp_path: Path) -> None:
    # The runner implements the answer, so its own sources ask freely.
    _package(tmp_path, "footman")
    _module(
        tmp_path,
        "footman",
        "context.py",
        "import sys\nfrom livery.footman import task\n\n"
        "def attended() -> bool:\n    return sys.stdin.isatty()\n",
    )
    # A module that never imports the runner is the workspace's own
    # business, whatever it asks.
    _package(tmp_path, "app")
    _module(
        tmp_path,
        "app",
        "ui.py",
        "import sys\n\ndef colour() -> bool:\n    return sys.stdout.isatty()\n",
    )
    assert _judged(tmp_path) == []
    # And the runner's own answer is what a task module should call.
    _package(tmp_path, "tool")
    _module(
        tmp_path,
        "tool",
        "verbs.py",
        "import livery.footman as footman\n\n"
        "def go() -> bool:\n    return footman.attended()\n",
    )
    assert _judged(tmp_path) == []


def test_a_test_may_ask_the_terminal(tmp_path: Path) -> None:
    _package(tmp_path, "tool")
    tests = tmp_path / "packages" / "tool" / "tests"
    tests.mkdir()
    (tests / "test_tty.py").write_text(
        "import sys\nimport livery.footman as footman\n\n"
        "def test_tty() -> None:\n    assert not sys.stdin.isatty()\n"
    )
    assert _judged(tmp_path) == []


def test_runner_terminal_judges_the_root_tasks_file_while_python_is_mounted(
    tmp_path: Path,
) -> None:
    """The base registers no terminal step; the python extension's mount does.

    A workspace whose packages hold the python extension mounts it, and
    the step it registers judges the root's ``tasks.py`` with the
    packages' sources, over the files a run reaches.
    """
    from dataclasses import replace

    from livery.footman import Failed
    from livery.workshop import Changes, PhaseContext, _steps
    from livery.workshop._extensions import installed_declaration, register_declared
    from livery.workshop._steps import step_for, steps_by_name

    (tmp_path / "tasks.py").write_text(
        "import sys\nimport livery.footman as footman\n\n"
        "def go() -> bool:\n    return sys.stdin.isatty()\n"
    )
    _package(tmp_path, "tool")
    _module(tmp_path, "tool", "ok.py", "import json\n")
    declared = installed_declaration("python")
    assert declared is not None
    state = _steps.snapshot()
    try:
        _steps.restore(_steps.snapshot(initial=True))
        assert "layering.runner-terminal" not in steps_by_name()
        assert register_declared("python", declared.additions)
        step = step_for("layering.runner-terminal")
        assert step.extension == "python"
        assert step.inputs is not None
        assert step.inputs.reads == ("tasks.py", "packages/**/*.py")
        whole = PhaseContext(
            root=tmp_path, packages=discover_packages(tmp_path), step=step.name
        )
        with pytest.raises(Failed) as raised:
            step.run(whole)
        assert str(raised.value).startswith(
            "the sources break the runner-terminal rule:\n  tasks.py asks the"
            " terminal with sys.stdin.isatty()"
        )
        # A run reaching another file alone sees nothing of it.
        elsewhere = Changes(tmp_path, ("packages/tool/src/ok.py",))
        step.run(replace(whole, changes=elsewhere))
    finally:
        _steps.restore(state)
