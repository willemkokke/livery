"""Regions and tails: the drift arms first, then the content that survives a render."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.workshop import _regions
from livery.workshop._templates import _drift_line

_OPEN = "# -- workshop: region own, yours to edit; the render keeps it --\n"
_CLOSE = "# -- workshop: end own --\n"


def test_a_marker_without_its_partner_is_no_region() -> None:
    text = f"a\n{_OPEN}x\n"
    assert _regions.regions_in(text) == ()
    assert _regions.unmatched(text) == ("own",)
    lone_close = f"a\n{_CLOSE}"
    assert _regions.regions_in(lone_close) == ()
    assert _regions.unmatched(lone_close) == ("own",)


def test_a_missing_marker_is_drift_naming_the_marker() -> None:
    rendered = f"a\n{_OPEN}{_CLOSE}".encode()
    committed = f"a\n{_OPEN}x\n".encode()
    (line,) = _drift_line("f.toml", committed, rendered)
    assert line.startswith(
        "f.toml: the `own` region's markers are rendered; restore them"
    )


def test_an_edit_outside_a_region_is_drift_naming_the_region() -> None:
    rendered = f"a\n{_OPEN}x\n{_CLOSE}".encode()
    committed = f"b\n{_OPEN}x\n{_CLOSE}".encode()
    (line,) = _drift_line("f.toml", committed, rendered)
    assert line == (
        "f.toml: differs from its render outside the `own` region; lines of"
        " your own go inside the region"
    )


def test_a_file_without_regions_keeps_the_plain_line() -> None:
    assert _drift_line("f.toml", b"b\n", b"a\n", " (the x extension owns it)") == [
        "f.toml: differs from its render (the x extension owns it)"
    ]
    assert _drift_line("f.toml", b"a\n", b"a\n") == []


def test_a_tail_file_is_judged_on_the_lines_the_render_owns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from livery.workshop import _templates

    monkeypatch.setattr(_templates, "TAIL_FILES", ("plain.txt",))
    rendered = b"one\ntwo\n"
    assert _drift_line("plain.txt", b"one\ntwo\nmine\n", rendered) == []
    assert _drift_line("plain.txt", b"one\ntwo\n", rendered) == []
    (line,) = _drift_line("plain.txt", b"one\nchanged\nmine\n", rendered)
    assert line == (
        "plain.txt: differs from its render in the lines it owns (1 to 2);"
        " your own lines go after them"
    )
    assert _regions.tail_split(b"one\ntwo\nmine\n", rendered) == (
        b"one\ntwo\n",
        b"mine\n",
    )
    assert _regions.tail_split(b"one\ntwo\n", rendered) == (b"one\ntwo\n", b"")


# Then the content that survives.


def test_regions_are_read_with_their_lines_and_content(tmp_path: Path) -> None:
    text = f"head\n{_OPEN}mine = 1\nyours = 2\n{_CLOSE}tail\n"
    (region,) = _regions.regions_in(text)
    assert region == _regions.Region("own", "mine = 1\nyours = 2\n", 2, 5)
    path = tmp_path / "f.toml"
    path.write_text(text)
    assert _regions.contents(path) == {"own": "mine = 1\nyours = 2\n"}
    assert _regions.contents(tmp_path / "absent.toml") == {}
    empty = f"{_OPEN}{_CLOSE}"
    assert _regions.regions_in(empty)[0].content == ""
    assert _regions.marker_lines("//", "settings") == (
        "// -- workshop: region settings, yours to edit; the render keeps it --",
        "// -- workshop: end settings --",
    )


def test_this_workspace_renders_its_own_regions_back(tmp_path: Path) -> None:
    """The committed regions are the render's input, so the render keeps them."""
    from livery.workshop._identity import project_facts
    from livery.workshop._templates import render_injections

    root = Path(__file__).resolve().parents[3]
    answers = project_facts(root)
    regions = render_injections(root, answers)["regions"]
    # No file copier renders carries a region any more: the composed
    # files keep theirs through the fragment engine.
    assert regions == {}
    # The three execution environments this repository declares live in
    # its own region of the composed project file, not in the base's
    # template.
    from livery.workshop._regions import contents

    tables = contents(root / "pyproject.toml")["tables"]
    assert "packages/footman/tests" in tables
    template = (
        root / "packages/workshop/src/livery/workshop/content/root/pyproject.toml.jinja"
    ).read_text()
    assert "packages/footman" not in template


def test_explain_names_a_managed_files_regions(tmp_path: Path) -> None:
    from livery.workshop._provenance import owned_lines

    (tmp_path / "f.toml").write_text(f"head\n{_OPEN}x\n{_CLOSE}")
    assert owned_lines(tmp_path, Path("f.toml")) == [
        "region own: lines 2 to 4, yours to edit inside the markers; the"
        " render keeps it"
    ]
    assert owned_lines(tmp_path, Path("absent.toml")) == []
