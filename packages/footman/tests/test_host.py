"""What a tool library asks of footman: the run it is called in, colour, and names."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import livery.footman as footman
from livery.footman import Context, use_context

# The fallbacks first: no run in flight answers None, in this process and
# in one that never loaded footman's run machinery.


def test_a_process_that_only_imports_footman_has_no_host() -> None:
    probe = (
        "import livery.footman as footman\n"
        "assert footman.host() is None\n"
        "print('none')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "none"


def test_outside_a_task_there_is_no_host() -> None:
    assert footman.host() is None


def test_an_unknown_style_refuses_naming_the_styles() -> None:
    with pytest.raises(ValueError, match=r"no style 'blink': the styles are bold, dim"):
        footman.styled("text", "blink")


# Inside a task: the host answers what a tool library asks.


def test_inside_a_task_the_host_answers_its_directory(tmp_path: Path) -> None:
    with use_context(Context(cwd=tmp_path)):
        found = footman.host()
        assert found is not None
        assert found.in_task
        assert found.real_cwd() == os.getcwd()
        assert found.target_cwd() == tmp_path
        assert found.target_cwd(relative="sub") == tmp_path / "sub"
        assert found.target_cwd(cwd="elsewhere") == Path("elsewhere")
        assert found.target_cwd(cwd="unmanaged") is None
        with pytest.raises(ValueError, match=r"needs a managed base"):
            found.target_cwd(cwd="unmanaged", relative="sub")
        with found.argv_override(["tool", "--flag"]):
            pass


def test_styled_paints_only_when_on() -> None:
    assert footman.styled("x", "cyan") == "\033[36mx\033[0m"
    assert footman.styled("x", "bold", on=False) == "x"
    assert footman.wants_color(sys.stdout, "always")
    assert not footman.wants_color(sys.stdout, "never")


def test_the_brand_s_names_answer_for_stock_footman() -> None:
    assert footman.directory_variable("DATA_DIR") == "FOOTMAN_DATA_DIR"
    assert footman.builtins() == ()
    assert footman.tasks_file_name() == "tasks.py"
