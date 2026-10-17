"""The docs extension's lint step: the pages it was handed, and a removed heading."""

from __future__ import annotations

import subprocess
from pathlib import Path

from livery.workshop._influence import Changes


def test_a_removed_heading_has_every_page_judged(tmp_path: Path) -> None:
    from livery.extensions.docs._steps import headings_removed

    root = tmp_path / "ws"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    page = root / "docs" / "guide.md"
    page.parent.mkdir()
    page.write_text("# Guide\n\n## Install\n\n## Use\n")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "commit",
            "-q",
            "-m",
            "seed",
        ],
        check=True,
    )
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    page.write_text("# Guide\n\n## Install\n\n## Use\n\n## More\n")
    assert not headings_removed(Changes(root, ("docs/guide.md",), head))
    page.write_text("# Guide\n\n## Use\n")
    assert headings_removed(Changes(root, ("docs/guide.md",), head))


def test_a_page_without_earlier_text_loses_no_heading(tmp_path: Path) -> None:
    from livery.extensions.docs._steps import headings_removed

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "new.md").write_text("# New\n\n## Section\n")
    # Paths the caller names carry no earlier tree: no page had a
    # heading a link could name, and a file that is no page has none.
    assert not headings_removed(Changes(tmp_path, ("src/mod.py", "docs/new.md")))


def test_the_link_step_judges_the_pages_it_was_handed(tmp_path: Path) -> None:
    from livery.extensions.docs._steps import link_problems

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "good.md").write_text("[ok](bad.md)\n")
    (docs / "bad.md").write_text("[broken](nowhere.md)\n")
    assert link_problems(tmp_path, frozenset({"docs/good.md"})) == []
    assert link_problems(tmp_path, frozenset({"docs/bad.md"})) == [
        "docs/bad.md: nowhere.md does not exist"
    ]
