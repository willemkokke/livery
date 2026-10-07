"""Columns are measured in terminal cells, not in `len()`."""

from __future__ import annotations

import textwrap

from livery.footman._describe import display_width, fit, pad_to
from livery.footman.testing import Runner


def test_width_counts_cells_not_characters():
    assert display_width("build") == 5
    assert display_width("构建") == 4  # two characters, four cells
    assert display_width("🚀") == 2
    assert display_width("café") == 4
    assert display_width("cafe\u0301") == 4  # combining acute rides the e
    assert display_width("\033[1mbuild\033[0m") == 5  # escapes are not cells


def test_padding_pads_to_cells():
    assert pad_to("构建", 6) == "构建  "
    assert pad_to("build", 6) == "build "
    assert pad_to("build", 2) == "build"  # never truncates, never goes negative


def test_fit_closes_a_style_the_cut_landed_inside():
    # The status line paints its failure count red. Cut mid-red by a naive
    # slice, the terminal keeps painting everything after it — and a slice
    # through the escape itself prints its tail as literal gibberish.
    line = "counting \033[31m2 failed and more words\033[0m"
    cut = fit(line, 14)
    assert display_width(cut) == 14
    assert cut.endswith("\033[0m")  # what was open at the cut is closed
    assert cut.count("\033[") == 2  # the opener and the added reset, whole


def test_fit_adds_no_reset_when_nothing_was_open():
    cut = fit("\033[31mred\033[0m then plain words", 8)
    assert display_width(cut) == 8
    assert cut == "\033[31mred\033[0m then"


def test_fit_never_splits_a_wide_character():
    # 5 cells asked of "构建构建" (8 cells) can only honestly give 4.
    cut = fit("构建构建", 5)
    assert cut == "构建"
    assert display_width(cut) == 4


def test_fit_leaves_a_line_that_already_fits_alone():
    assert fit("short", 40) == "short"


def test_a_wide_task_name_aligns_its_step_column(tmp_path):
    """A run's step lines pad the task-name column so siblings align. Padded
    by `len()`, a CJK name pushed its own line two cells right of every
    other — the column visibly bent around it."""
    # Explicit encoding: `write_text` defaults to the locale codepage, and
    # Windows' cp1252 cannot hold the very characters this test is about.
    (tmp_path / "tasks.py").write_text(
        textwrap.dedent(
            """
            from livery.footman import run, task

            @task(name="构建")
            def build():
                run(["python", "-c", "pass"])

            @task
            def lint():
                run(["python", "-c", "pass"])
            """
        ),
        encoding="utf-8",
    )
    result = Runner().invoke("-v -s 构建 lint", cwd=tmp_path)
    assert result.ok, result.stderr
    steps = [ln for ln in result.stdout.splitlines() if ln.startswith("ok ")]
    assert len(steps) == 2, result.stdout
    # In CELLS, not in characters: the whole point is that those two counts
    # disagree for 构建, and the terminal only ever sees the first.
    starts = {display_width(ln[: ln.index("python")]) for ln in steps}
    assert len(starts) == 1, steps  # one column, whatever the name is made of


def test_the_run_summary_column_aligns_too(tmp_path):
    """The step lines and the closing summary are two different columns in
    two different modules, and each learns its own width. The summary's
    learner was the last one still counting characters."""
    (tmp_path / "tasks.py").write_text(
        textwrap.dedent(
            """
            from livery.footman import task

            @task(name="构建")
            def build(): ...

            @task
            def lint(): ...

            @task(name="🚀deploy")
            def deploy(): ...
            """
        ),
        encoding="utf-8",
    )
    result = Runner().invoke("-s 构建 lint 🚀deploy", cwd=tmp_path)
    assert result.ok, result.stderr
    rows = [ln for ln in result.stderr.splitlines() if ln.startswith("ok ")]
    assert len(rows) == 3, result.stderr
    starts = {display_width(ln[: ln.index("(")]) for ln in rows}
    assert len(starts) == 1, rows
