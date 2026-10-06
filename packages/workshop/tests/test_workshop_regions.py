"""Regions: the unmatched markers first, then the content that survives a sync."""

from __future__ import annotations

from pathlib import Path

from livery.workshop import _regions

_OPEN = "# -- workshop: region own, yours to edit; the render keeps it --\n"
_CLOSE = "# -- workshop: end own --\n"


def test_a_marker_without_its_partner_is_no_region() -> None:
    text = f"a\n{_OPEN}x\n"
    assert _regions.regions_in(text) == ()
    assert _regions.unmatched(text) == ("own",)
    lone_close = f"a\n{_CLOSE}"
    assert _regions.regions_in(lone_close) == ()
    assert _regions.unmatched(lone_close) == ("own",)


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


def test_explain_names_a_managed_files_regions(tmp_path: Path) -> None:
    from livery.workshop._provenance import owned_lines

    (tmp_path / "f.toml").write_text(f"head\n{_OPEN}x\n{_CLOSE}")
    assert owned_lines(tmp_path, Path("f.toml")) == [
        "region own: lines 2 to 4, yours to edit inside the markers; the"
        " render keeps it"
    ]
    assert owned_lines(tmp_path, Path("absent.toml")) == []
