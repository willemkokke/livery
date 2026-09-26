"""The artifact step: the refusals first, then a version's hosts recorded and staged."""

from __future__ import annotations

import io
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from livery.footman.context import Failed
from livery.strongroom import digest_of
from livery.toolroom.bench import (
    _artifacts,
    _drivers,
    _provision,
    _surfaces,
    _toolfetch,
)
from livery.toolroom.bench import _tasks as tools
from livery.toolroom.store import Home, Layout, Store, _engine
from toolroom_bench_readings import history, isolate, save, with_flags

LINUX, MAC, ARM, WIN = "linux-x64", "macos-arm", "linux-arm", "windows-x64"
_ASSET_NAMES = {
    LINUX: "tool_{v}_Linux_x86_64.zip",
    MAC: "tool_{v}_macOS_arm64.zip",
    WIN: "tool_{v}_Windows_x86_64.zip",
}


def _zip(members: dict[str, bytes]) -> bytes:
    """A zip whose `tool` binaries carry the executable bit and nothing else does."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            info = zipfile.ZipInfo(name)
            binary = name.rsplit("/", 1)[-1] in ("tool", "tool.exe")
            info.external_attr = (0o755 if binary else 0o644) << 16
            archive.writestr(info, data)
    return buffer.getvalue()


def _payloads(
    version: str, *, hosts: tuple[str, ...] = (LINUX, MAC, WIN)
) -> dict[str, bytes]:
    """One archive per host, named as a forge names them, rooted at `tool-<v>/`."""
    out: dict[str, bytes] = {}
    for host in hosts:
        exe = "tool.exe" if host == WIN else "tool"
        out[_ASSET_NAMES[host].format(v=version)] = _zip(
            {
                f"tool-{version}/bin/{exe}": b"#!/bin/sh\necho hi\n",
                f"tool-{version}/LICENSE": b"l",
            }
        )
    return out


def _driver(kind: str = "github") -> _drivers.Driver:
    return _drivers.Driver(
        "tool", provision=_drivers.Provision(kind=kind, repo="o/tool")
    )


def _record(*versions: str, layout: Layout | None = None) -> Any:
    reads = [
        (v, f"2026-0{n}-01", with_flags(f"--{n}")) for n, v in enumerate(versions, 1)
    ]
    record = history("tool", *reads, kind="download")
    return replace(
        record,
        hosts=(WIN,),
        layout=layout
        or Layout(root="tool-{version}", entry_points=("bin/tool",), paths=("bin",)),
        host_layouts={WIN: Layout(entry_points=("bin/tool.exe",))},
    )


def _serve(
    monkeypatch: pytest.MonkeyPatch,
    payloads: dict[str, bytes],
    *,
    tags: tuple[str, ...] = ("v1.0.0",),
) -> list[tuple[str, str, str]]:
    """The forge: an asset list per known tag, the bytes behind each URL, no origin."""
    calls: list[tuple[str, str, str]] = []

    def assets_for(forge: str, repo: str, tag: str = "") -> list[tuple[str, str]]:
        calls.append((forge, repo, tag))
        if tag not in tags:
            raise _provision.ProvisionError(f"no release {tag}")
        listed = [(name, f"https://forge.test/{tag}/{name}") for name in payloads]
        return [*listed, ("checksums.sha256", f"https://forge.test/{tag}/x.sha256")]

    monkeypatch.setattr(_provision, "assets_for", assets_for)
    monkeypatch.setattr(
        _artifacts, "_fetch", lambda url: payloads[url.rsplit("/", 1)[1]]
    )

    def no_origin(url: str) -> bytes:
        raise AssertionError(f"the store reached the network for {url}")

    monkeypatch.setattr(_engine, "download", no_origin)
    return calls


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(Home(tmp_path / "home"), host=LINUX)


# --- refusals first -----------------------------------------------------------


def test_a_package_tier_tool_has_no_artifacts_to_record(store: Store) -> None:
    with pytest.raises(
        _artifacts.ArtifactError, match="'uv' tier, which has no release"
    ):
        _artifacts.record_version(
            _record("1.0.0"), _driver("uv"), "1.0.0", "", store=store
        )


def test_a_version_the_record_does_not_track_is_refused(
    store: Store, monkeypatch
) -> None:
    _serve(monkeypatch, _payloads("1.0.0"))
    with pytest.raises(_artifacts.ArtifactError, match=r"tool does not track 9\.9\.9"):
        _artifacts.record_version(_record("1.0.0"), _driver(), "9.9.9", "", store=store)


def test_a_release_the_forge_lacks_names_every_spelling_tried(
    store: Store, monkeypatch
) -> None:
    calls = _serve(monkeypatch, _payloads("1.0.0"), tags=("release-1",))
    with pytest.raises(
        _artifacts.ArtifactError, match=r"no release on github at 1\.0\.0, v1\.0\.0"
    ):
        _artifacts.record_version(_record("1.0.0"), _driver(), "1.0.0", "", store=store)
    assert [c[2] for c in calls] == ["1.0.0", "v1.0.0"]


def test_a_release_with_no_asset_for_any_host_is_refused_naming_its_assets(
    store: Store, monkeypatch
) -> None:
    _serve(monkeypatch, {"tool-1.0.0-source.tar.gz": b"src"})
    with pytest.raises(
        _artifacts.ArtifactError,
        match=r"no asset for any of .*; it has tool-1\.0\.0-source\.tar\.gz, checksums",
    ):
        _artifacts.record_version(
            _record("1.0.0"), _driver(), "1.0.0", "v1.0.0", store=store
        )


def test_a_host_whose_deployment_does_not_resolve_whole_is_refused(
    store: Store, monkeypatch
) -> None:
    """A layout the record lacks is named here, never written to disk."""
    _serve(monkeypatch, _payloads("1.0.0"))
    bare = _record("1.0.0", layout=Layout(root="tool-{version}"))
    with pytest.raises(_artifacts.ArtifactError, match="resolves incomplete"):
        _artifacts.record_version(bare, _driver(), "1.0.0", "v1.0.0", store=store)


def test_the_verb_refuses_without_touching_the_record(tmp_path, monkeypatch) -> None:
    records = isolate(tools, monkeypatch, tmp_path)
    save(_record("1.0.0", layout=Layout(root="tool-{version}")), records)
    before = (records / "tool.jsonl").read_text()
    _serve(monkeypatch, _payloads("1.0.0"))
    monkeypatch.setattr(
        _drivers, "find", lambda key: _driver() if key == "tool" else None
    )
    monkeypatch.setattr(
        tools, "_bench_store", lambda: Store(Home(tmp_path / "h"), host=LINUX)
    )
    monkeypatch.setattr(_toolfetch, "releases", lambda driver: [])
    with pytest.raises(Failed, match="resolves incomplete"):
        tools.tools_artifacts("tool")
    assert (records / "tool.jsonl").read_text() == before


# --- the step -----------------------------------------------------------------


def test_hosts_without_an_asset_are_absent_and_the_rest_are_hashed_and_landed(
    store: Store, monkeypatch
) -> None:
    payloads = _payloads("1.0.0")
    _serve(monkeypatch, payloads)
    record, done = _artifacts.record_version(
        _record("1.0.0"),
        _driver(),
        "1.0.0",
        "v1.0.0",
        store=store,
        hosts=(LINUX, MAC, ARM, WIN),
    )
    assert done.absent == (ARM,)
    assert set(done.found) == {LINUX, MAC, WIN}
    # The record's hosts grow in the canonical order; the version's artifacts too.
    assert record.hosts == (MAC, LINUX, WIN)
    delta = record.delta_for("1.0.0")
    assert delta.hosts == (MAC, LINUX, WIN)
    for host, (asset, size) in done.found.items():
        assert delta.artifacts[host].sha256 == digest_of(payloads[asset]).encoded
        assert size == len(payloads[asset])
        assert delta.artifacts[host].url.endswith(asset)
    lines = done.lines("tool")
    assert lines[0] == "  tool 1.0.0: 3 artifact(s) recorded"
    assert lines[-1] == "    no asset for linux-arm"
    # The bytes were landed: the ingest verification stages them with no origin.
    from livery.toolroom.bench import _ingest

    report = _ingest.verify(record, "1.0.0", store=store)
    assert report.passed, report.findings
    assert record.layout.root == "tool-{version}"  # the token stays in the record


def test_a_host_already_recorded_is_left_as_it_is(store: Store, monkeypatch) -> None:
    payloads = _payloads("1.0.0")
    _serve(monkeypatch, payloads)
    once, _ = _artifacts.record_version(
        _record("1.0.0"), _driver(), "1.0.0", "v1.0.0", store=store, hosts=(LINUX,)
    )
    monkeypatch.setattr(_artifacts, "_fetch", lambda url: b"changed bytes")
    twice, done = _artifacts.record_version(
        once, _driver(), "1.0.0", "v1.0.0", store=store, hosts=(LINUX, MAC)
    )
    assert set(done.found) == {MAC}
    assert (
        twice.delta_for("1.0.0").artifacts[LINUX]
        == once.delta_for("1.0.0").artifacts[LINUX]
    )


def test_the_verb_records_saves_and_runs_the_nine_checks(
    tmp_path, monkeypatch, capsys
) -> None:
    from livery.toolroom.bench._toolfetch import Release

    records = isolate(tools, monkeypatch, tmp_path)
    save(_record("1.0.0"), records)
    _serve(monkeypatch, _payloads("1.0.0"))
    monkeypatch.setattr(
        _drivers, "find", lambda key: _driver() if key == "tool" else None
    )
    monkeypatch.setattr(
        tools, "_bench_store", lambda: Store(Home(tmp_path / "h"), host=LINUX)
    )
    monkeypatch.setattr(
        _toolfetch, "releases", lambda driver: [Release("1.0.0", tag="v1.0.0")]
    )
    tools.tools_artifacts("tool")
    out = capsys.readouterr().out
    assert "  tool 1.0.0: 3 artifact(s) recorded" in out
    assert "every check passed on 3 host(s)" in out
    saved = _surfaces.load(records / "tool.jsonl")
    assert saved is not None and saved.hosts_of("1.0.0") == (MAC, LINUX, WIN)


def test_the_verb_is_red_on_a_finding_with_the_artifacts_saved(
    tmp_path, monkeypatch, capsys
) -> None:
    """A missing entry point is a finding; the artifacts stay for the fix.

    A missing file is seen on every machine, where a stray executable's
    mode bit is not: a Windows filesystem carries none for a POSIX tree.
    """
    from livery.toolroom.bench._toolfetch import Release

    records = isolate(tools, monkeypatch, tmp_path)
    save(_record("1.0.0"), records)
    payloads = _payloads("1.0.0")
    payloads[_ASSET_NAMES[LINUX].format(v="1.0.0")] = _zip(
        {"tool-1.0.0/bin/other": b"#!/bin/sh\n", "tool-1.0.0/LICENSE": b"l"}
    )
    _serve(monkeypatch, payloads)
    monkeypatch.setattr(
        _drivers, "find", lambda key: _driver() if key == "tool" else None
    )
    monkeypatch.setattr(
        tools, "_bench_store", lambda: Store(Home(tmp_path / "h"), host=LINUX)
    )
    monkeypatch.setattr(
        _toolfetch, "releases", lambda driver: [Release("1.0.0", tag="v1.0.0")]
    )
    with pytest.raises(Failed, match=r"tool 1\.0\.0: 1 finding"):
        tools.tools_artifacts("tool")
    assert "entry-points" in capsys.readouterr().out
    saved = _surfaces.load(records / "tool.jsonl")
    assert saved is not None and LINUX in saved.hosts_of("1.0.0")


def test_the_assembler_records_artifacts_for_a_fresh_forge_version_only(
    tmp_path, monkeypatch
) -> None:
    """A tool on a package tier is folded as before; a forge tool gains its hosts."""
    records = isolate(tools, monkeypatch, tmp_path)
    save(_record("1.0.0"), records)
    pytool = history("pytool", ("1.0.0", "2026-01-01", with_flags("--p")))
    save(pytool, records)
    _serve(monkeypatch, _payloads("1.1.0"), tags=("v1.1.0",))
    drivers = {"tool": _driver(), "pytool": _drivers.Driver("pytool")}
    monkeypatch.setattr(_drivers, "find", drivers.get)
    monkeypatch.setattr(
        tools, "_bench_store", lambda: Store(Home(tmp_path / "h"), host=LINUX)
    )
    document = {
        "platform": "Linux",
        "observations": {
            name: {
                "1.1.0": {
                    "date": "2026-02-01",
                    "tag": "v1.1.0",
                    "surface": with_flags("--q"),
                }
            }
            for name in ("tool", "pytool")
        },
    }
    found = tools._assemble_documents([document])
    assert found.read == {"tool": ["1.1.0"], "pytool": ["1.1.0"]}
    assert list(found.artifacts) == ["tool"]
    assert found.artifacts["tool"][0] == "  tool 1.1.0: 3 artifact(s) recorded"
    moved = _surfaces.load(records / "tool.jsonl")
    assert moved is not None and moved.hosts_of("1.1.0") == (MAC, LINUX, WIN)
    assert moved.hosts_of("1.0.0") == ()  # history is not backfilled here
    still = _surfaces.load(records / "pytool.jsonl")
    assert still is not None and still.hosts == () and still.hosts_of("1.1.0") == ()


def test_a_verb_bound_view_of_another_binary_records_nothing(
    tmp_path, monkeypatch, capsys
) -> None:
    """`ruff_format` reads ruff's binary; ruff's record carries the artifacts."""
    records = isolate(tools, monkeypatch, tmp_path)
    save(_record("1.0.0"), records)
    view = _drivers.Driver(
        "tool",
        attr="tool_fmt",
        base=("fmt",),
        provision=_drivers.Provision(kind="github", repo="o/tool"),
    )
    _serve(monkeypatch, _payloads("1.1.0"), tags=("v1.1.0",))
    monkeypatch.setattr(_drivers, "find", lambda key: view if key == "tool" else None)
    monkeypatch.setattr(
        tools, "_bench_store", lambda: Store(Home(tmp_path / "h"), host=LINUX)
    )
    with pytest.raises(Failed, match=r"tool is a view of tool's binary bound to `fmt`"):
        tools.tools_artifacts("tool")
    document = {
        "platform": "Linux",
        "observations": {
            "tool": {
                "1.1.0": {
                    "date": "2026-02-01",
                    "tag": "v1.1.0",
                    "surface": with_flags("--q"),
                }
            }
        },
    }
    found = tools._assemble_documents([document])
    assert found.read == {"tool": ["1.1.0"]} and found.artifacts == {}
    moved = _surfaces.load(records / "tool.jsonl")
    assert moved is not None and moved.hosts_of("1.1.0") == ()


def test_a_universal_asset_is_recorded_for_every_host_without_a_listing(
    store: Store, monkeypatch
) -> None:
    """A release that lists no asset (cmake-conan's provider is one file at a
    versioned address) is recorded from the driver's URL template, the same
    bytes on every host, and no forge listing is asked for.
    """
    from livery.toolroom.bench import _provision
    from livery.toolroom.bench._drivers import Driver, Provision
    from livery.toolroom.store import HOSTS

    def no_listing(*_a, **_k):
        raise AssertionError("the forge was asked for a listing")

    monkeypatch.setattr(_provision, "assets_for", no_listing)
    monkeypatch.setattr(_artifacts, "_fetch", lambda url: f"bytes of {url}".encode())
    driver = Driver(
        "cmake-conan",
        source="manual",
        provision=Provision(
            kind="github",
            repo="conan-io/cmake-conan",
            asset="https://raw.example/{repo}/{tag}/conan_provider.cmake",
        ),
    )
    file_only = Layout(
        file="conan_provider.cmake", env={"P": "$package/conan_provider.cmake"}
    )
    bare = replace(_record("0.19.0", layout=file_only), hosts=(), host_layouts={})
    record, done = _artifacts.record_version(bare, driver, "0.19.0", "", store=store)
    url = "https://raw.example/conan-io/cmake-conan/0.19.0/conan_provider.cmake"
    assert set(record.delta_for("0.19.0").artifacts) == set(HOSTS)
    assert all(a.url == url for a in record.delta_for("0.19.0").artifacts.values())
    assert done.absent == ()
