"""The golden render: a render change fails here until the goldens move with it.

The golden records are small and shaped to exercise the renderer: a
verb gained and one withdrawn across versions, an option added and one
dropped, a platform that read a version and lacked an option, a nested
verb, a choice, a negation, a default, and a tool that runs in process.
Each version's stub is checked in beside the records, and
`fm tools.goldens` is the one way the goldens move.
"""

from __future__ import annotations

import pytest

from livery.toolroom.bench import _index, _surfaces
from livery.toolroom.bench import _tasks as tools


def _cases() -> list[tuple[str, str]]:
    return [
        (record.name, version)
        for record in tools.golden_records()
        for version in _surfaces.versions(record)
    ]


def test_the_goldens_cover_what_the_renderer_must_keep_deliberate():
    records = {record.name: record for record in tools.golden_records()}
    assert set(records) == {"demo", "solo_inproc"}
    demo = records["demo"]
    assert _surfaces.versions(demo) == ["2.0.0", "1.1.0", "1.0.0"]
    spec = _surfaces.union(demo, name="demo")
    options = {(v.name, o.name): o for v in spec.verbs for o in v.options}
    assert options[("build", "fork")].since == "1.1.0"  # arrived, and the record saw it
    assert options[("build", "fork")].not_on == ("Windows",)
    assert (
        options[("build", "jobs")].until == "1.1.0"
    )  # the release it stopped appearing in
    assert options[("", "color")].choices == ("auto", "always", "never")
    assert options[("build", "clean")].negation == "--dirty"
    assert {v.name for v in spec.verbs} == {"", "build", "compose.up"}
    assert next(v for v in spec.verbs if v.name == "compose.up").not_on == ("Windows",)
    assert tools.golden_driver(records["solo_inproc"]).in_process


@pytest.mark.parametrize(("name", "version"), _cases())
def test_a_golden_record_renders_to_its_checked_in_stub(name: str, version: str):
    record = next(r for r in tools.golden_records() if r.name == name)
    path = tools.golden_path(record, version)
    assert path.is_file(), f"{path} is missing; run `fm tools.goldens` and commit it"
    rendered = _index.stub_for(record, version, driver=tools.golden_driver(record))
    assert rendered == path.read_text(encoding="utf-8"), (
        f"the renderer moved for {name} {version}: run `fm tools.goldens` and"
        " commit the goldens in the same change"
    )


def test_every_checked_in_golden_belongs_to_a_version():
    """A golden nobody renders is a stale claim; the set is exactly the versions read."""
    expected = {
        tools.golden_path(record, version)
        for record in tools.golden_records()
        for version in _surfaces.versions(record)
    }
    found = set((tools._GOLDENS / "stubs").rglob("*.pyi.golden"))
    assert found == expected


def test_the_goldens_verb_reports_and_writes(tmp_path, monkeypatch):
    from toolroom_bench_readings import tools_run

    result = tools_run("goldens --check")
    assert result.ok, result.stderr
    assert "would move 0: none" in result.stdout

    # Point the verb at a scratch copy with one golden gone, and it comes back.
    import shutil

    original = tools._GOLDENS
    scratch = tmp_path / "goldens"
    shutil.copytree(original, scratch)
    monkeypatch.setattr(tools, "_GOLDENS", scratch)
    gone = scratch / "stubs" / "solo_inproc" / "0.1.0.pyi.golden"
    gone.unlink()
    checked = tools_run("goldens --check")
    assert checked.ok, checked.stderr
    assert "would move 1: solo_inproc 0.1.0" in checked.stdout
    assert not gone.exists()
    written = tools_run("goldens")
    assert written.ok, written.stderr
    assert "wrote 1: solo_inproc 0.1.0" in written.stdout
    assert (
        gone.read_bytes()
        == (original / "stubs" / "solo_inproc" / "0.1.0.pyi.golden").read_bytes()
    )
