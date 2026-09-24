"""The ingest checks: one forced failure per check, then the version that passes.

Every artifact is a zip served from a folder source beside the
records; the network is refused, so a check reads what the store
stages and nothing else.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from livery.strongroom import FolderSource, digest_of
from livery.strongroom import Store as ObjectStore
from livery.toolroom.bench import _ingest
from livery.toolroom.store import (
    Artifact,
    Home,
    Layout,
    Record,
    RecordDelta,
    Store,
    Surface,
    _engine,
)

LINUX, WINDOWS = "linux-x64", "windows-x64"


def _zip(members: dict[str, bytes], *, executable: tuple[str, ...] = ()) -> bytes:
    """A zip of *members*; the paths in *executable* carry the mode bits."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            info = zipfile.ZipInfo(name)
            mode = 0o755 if name in executable else 0o644
            info.external_attr = (0o100000 | mode) << 16
            archive.writestr(info, data)
    return buffer.getvalue()


def _linux_payload(
    *, entry: str = "bin/tool", extra: dict[str, bytes] | None = None
) -> bytes:
    members = {
        "tool-1/" + entry: b"#!/bin/sh\necho tool\n",
        "tool-1/lib/data": b"d",
        "tool-1/docs/readme": b"r",
    }
    members.update({"tool-1/" + name: data for name, data in (extra or {}).items()})
    return _zip(members, executable=tuple(m for m in members if m.endswith(entry)))


def _windows_payload() -> bytes:
    return _zip(
        {
            "tool-1/bin/tool.exe": b"MZ",
            "tool-1/lib/data": b"d",
            "tool-1/docs/readme": b"r",
        }
    )


class _Rig:
    """A store over a folder source of payloads, and a record builder."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.mirror = ObjectStore.create(tmp_path / "mirror")
        self.store = Store(
            Home(tmp_path / "home"),
            host=LINUX,
            sources=[FolderSource(tmp_path / "mirror")],
        )

        def no_network(url: str) -> bytes:
            raise AssertionError(f"the network was reached for {url}")

        monkeypatch.setattr(_engine, "download", no_network)

    def artifact(self, payload: bytes, host: str, version: str) -> Artifact:
        self.mirror.put(payload)
        return Artifact(
            f"https://origin.test/tool/{version}/{host}.zip", digest_of(payload).encoded
        )

    def record(
        self,
        *,
        layout: Layout | None = None,
        windows: Layout | None = None,
        first: dict[str, bytes] | None = None,
        second: dict[str, bytes] | None = None,
        second_layout: Layout | None = None,
        second_hosts: tuple[str, ...] = (LINUX, WINDOWS),
    ) -> Record:
        payloads = {
            (LINUX, "1.0.0"): (first or {}).get(LINUX, _linux_payload()),
            (WINDOWS, "1.0.0"): (first or {}).get(WINDOWS, _windows_payload()),
            (LINUX, "1.1.0"): (second or {}).get(LINUX, _linux_payload()),
            (WINDOWS, "1.1.0"): (second or {}).get(WINDOWS, _windows_payload()),
        }
        return Record(
            "tool",
            kind="archive",
            hosts=(LINUX, WINDOWS),
            layout=layout
            or Layout(root="tool-1", entry_points=("bin/tool",), paths=("bin",)),
            host_layouts={WINDOWS: windows or Layout(entry_points=("bin/tool.exe",))},
            deltas=(
                RecordDelta(
                    1,
                    "1.0.0",
                    "2026-01-01",
                    {
                        h: self.artifact(payloads[(h, "1.0.0")], h, "1.0.0")
                        for h in (LINUX, WINDOWS)
                    },
                ),
                RecordDelta(
                    2,
                    "1.1.0",
                    "2026-02-01",
                    {
                        h: self.artifact(payloads[(h, "1.1.0")], h, "1.1.0")
                        for h in second_hosts
                    },
                    second_layout or Layout(),
                ),
            ),
        )


def _checks(report: _ingest.Report) -> list[tuple[str, str]]:
    return [(f.check, f.host) for f in report.findings]


def test_a_root_the_archive_lacks_is_the_first_finding(tmp_path, monkeypatch):
    rig = _Rig(tmp_path, monkeypatch)
    record = rig.record(second_layout=Layout(root="elsewhere"))
    report = _ingest.verify(record, "1.1.0", store=rig.store)
    assert _checks(report) == [("root", LINUX), ("root", WINDOWS)]
    assert "has no root 'elsewhere'" in report.findings[0].detail
    assert report.diffs == ()  # nothing staged, nothing to diff


def test_an_entry_point_or_path_directory_missing_from_the_tree_is_named(
    tmp_path, monkeypatch
):
    rig = _Rig(tmp_path, monkeypatch)
    record = rig.record(second={LINUX: _linux_payload(entry="sbin/tool")})
    report = _ingest.verify(record, "1.1.0", store=rig.store, hosts=(LINUX,))
    assert _checks(report) == [("entry-points", LINUX), ("paths", LINUX)]
    assert "entry point 'bin/tool' is not a file" in report.findings[0].detail
    assert "path directory 'bin' is not in the tree" in report.findings[1].detail


def test_a_shim_target_and_an_env_path_that_are_not_in_the_tree_are_named(
    tmp_path, monkeypatch
):
    rig = _Rig(tmp_path, monkeypatch)
    record = rig.record(
        second_layout=Layout(
            shims={"t": "bin/other"}, env={"TOOL_HOME": "$package/share"}
        )
    )
    report = _ingest.verify(record, "1.1.0", store=rig.store, hosts=(LINUX,))
    assert _checks(report) == [("shims", LINUX), ("env", LINUX)]
    assert "shim 't' names 'bin/other'" in report.findings[0].detail
    assert "TOOL_HOME names 'share'" in report.findings[1].detail
    # A shim target that is there and an env path that is there pass.
    fine = rig.record(
        second={LINUX: _linux_payload(extra={"bin/other": b"o", "share/x": b"x"})},
        second_layout=Layout(
            shims={"t": "bin/other"}, env={"TOOL_HOME": "$package/share"}
        ),
    )
    assert _ingest.verify(fine, "1.1.0", store=rig.store, hosts=(LINUX,)).passed


def test_an_entry_point_or_a_host_the_previous_version_had_must_stay(
    tmp_path, monkeypatch
):
    rig = _Rig(tmp_path, monkeypatch)
    # The linux entry point moves to sbin and the windows build goes.
    record = rig.record(
        second={LINUX: _linux_payload(entry="sbin/tool")},
        second_layout=Layout(entry_points=("sbin/tool",), paths=("sbin",)),
        second_hosts=(LINUX,),
    )
    report = _ingest.verify(record, "1.1.0", store=rig.store)
    assert _checks(report) == [("hosts-kept", WINDOWS), ("entry-points-kept", LINUX)]
    assert "1.0.0 had a build and 1.1.0 has none" in report.findings[0].detail
    assert "gone: bin/tool" in report.findings[1].detail
    assert report.hosts == (LINUX,) and report.previous == "1.0.0"


def test_an_exclusion_that_matches_nothing_and_a_stray_executable_are_named(
    tmp_path, monkeypatch
):
    rig = _Rig(tmp_path, monkeypatch)
    record = rig.record(
        second={LINUX: _linux_payload(extra={"bin/helper": b"#!/bin/sh\n"})},
        second_layout=Layout(exclude=("docs/*", "nothing/*")),
    )
    # The helper carries no mode bit in the payload: add it through a payload that does.
    payload = _zip(
        {
            "tool-1/bin/tool": b"#!/bin/sh\n",
            "tool-1/bin/helper": b"#!/bin/sh\n",
            "tool-1/lib/data": b"d",
            "tool-1/docs/readme": b"r",
        },
        executable=("tool-1/bin/tool", "tool-1/bin/helper"),
    )
    record = rig.record(
        second={LINUX: payload}, second_layout=Layout(exclude=("docs/*", "nothing/*"))
    )
    report = _ingest.verify(record, "1.1.0", store=rig.store, hosts=(LINUX,))
    assert _checks(report) == [("exclusions", LINUX), ("stray-executables", LINUX)]
    assert "exclusion 'nothing/*' matches nothing" in report.findings[0].detail
    assert (
        "'bin/helper' is executable in path directory 'bin'"
        in report.findings[1].detail
    )
    # The Windows tree reads its executables by suffix.
    stray = _zip({"tool-1/bin/tool.exe": b"MZ", "tool-1/bin/extra.exe": b"MZ"})
    record = rig.record(second={WINDOWS: stray})
    report = _ingest.verify(record, "1.1.0", store=rig.store, hosts=(WINDOWS,))
    assert _checks(report) == [("stray-executables", WINDOWS)]


def test_a_version_that_passes_reports_every_host_and_its_diff(tmp_path, monkeypatch):
    rig = _Rig(tmp_path, monkeypatch)
    record = rig.record(
        second={LINUX: _linux_payload(extra={"lib/new": b"n"})},
        second_layout=Layout(exclude=("docs/*",)),
    )
    report = _ingest.verify(record, "1.1.0", store=rig.store)
    assert report.passed and report.hosts == (LINUX, WINDOWS)
    linux, windows = report.diffs
    # The exclusion empties docs/ and leaves the directory, so one path goes.
    assert (linux.host, linux.added, linux.removed) == (
        LINUX,
        ("lib/new",),
        ("docs/readme",),
    )
    assert (windows.host, windows.added, windows.removed) == (
        WINDOWS,
        (),
        ("docs/readme",),
    )
    lines = _ingest.summary(report)
    assert lines[0] == "  tool 1.1.0: every check passed on 2 host(s), against 1.0.0"
    assert "    linux-x64: +1 -1 path(s)" in lines and "      + lib/new" in lines
    # The first version with a host compares against nothing: every path is added.
    first = _ingest.verify(record, "1.0.0", store=rig.store, hosts=(LINUX,))
    assert first.passed and first.previous == ""
    assert first.diffs[0].removed == () and "bin/tool" in first.diffs[0].added
    assert _ingest.summary(first)[0].endswith("the first version with a host")
    assert _ingest.previous_with_hosts(record, "1.0.0") == ""
    assert _ingest.previous_with_hosts(record, "1.1.0") == "1.0.0"


def test_the_verify_verb_prints_the_report_and_is_red_on_a_finding(
    tmp_path, monkeypatch, capsys
):
    from livery.footman import Failed
    from livery.toolroom.bench import _tasks

    rig = _Rig(tmp_path, monkeypatch)
    records = tmp_path / "records"
    rig.record(second={LINUX: _linux_payload(entry="sbin/tool")}).save(records)
    monkeypatch.setattr(_tasks, "_RECORDS", records)
    monkeypatch.setattr(_tasks, "_bench_store", lambda: rig.store)
    with pytest.raises((Failed, SystemExit), match=r"tool 1.1.0: 2 finding\(s\)"):
        _tasks.tools_verify("tool")
    out = capsys.readouterr().out
    assert "  tool 1.1.0: 2 finding(s), against 1.0.0" in out
    assert "    entry-points on linux-x64: entry point 'bin/tool' is not a file" in out
    _tasks.tools_verify("tool", version="1.0.0")
    assert "  tool 1.0.0: every check passed on 2 host(s), the first version" in (
        capsys.readouterr().out
    )
    with pytest.raises(
        (Failed, SystemExit), match=r"no record of nope; the records are tool"
    ):
        _tasks.tools_verify("nope")
    # A tool never downloaded has nothing to stage.
    read = Surface(("Linux",), 1, "Bare.", {"": _root_verb()})
    Record(
        "bare", kind="uv-tool", deltas=(RecordDelta(1, "1.0.0", "", surface=read),)
    ).save(records)
    with pytest.raises((Failed, SystemExit), match=r"bare has no version with a host"):
        _tasks.tools_verify("bare")


def _root_verb() -> dict[str, object]:
    return {"help": "", "wraps": False, "positional": "any", "lead": "", "options": {}}
