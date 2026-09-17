"""The engine: refusals and fallbacks first, then installs, links, deltas, mirrors."""

from __future__ import annotations

import io
import os
import stat
import sys
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from livery.strongroom import Digest, FolderSource
from livery.toolroom.store import (
    Artifact,
    Event,
    Home,
    Layout,
    Record,
    RecordDelta,
    RecordError,
    Store,
    StoreError,
    _engine,
    resolve,
)
from toolroom_store_archives import make_tar, make_zip, sha

HOST = "linux-x64"
OTHER = "macos-arm"
# The fixtures are Linux-shaped; on Windows the engine judges an
# executable by its suffix, so the fixtures carry one there.
EXE = ".exe" if sys.platform == "win32" else ""


def _record(
    name: str,
    artifacts: dict[str, bytes],
    *,
    kind: str = "archive",
    root: str = "",
    exe: str = "",
    paths: tuple[str, ...] = ("bin",),
    env: dict[str, str] | None = None,
    shims: dict[str, str] | None = None,
    version: str = "1.0.0",
    entry_points: tuple[str, ...] | None = None,
) -> Record:
    """A record tracking one *version* with one artifact per host in *artifacts*.

    The entry points default to the one executable the test archives
    carry: `bin/<name>` when `bin` is on PATH, else `<name>` at the top,
    the binary's `exe` for a binary.
    """
    if entry_points is None:
        if exe:
            entry_points = (exe,)
        elif "bin" in paths and root:
            entry_points = (f"bin/{name}{EXE}",)
        elif "bin" in paths:
            entry_points = (f"{name}-{version}/bin/{name}{EXE}",)
        else:
            entry_points = (f"{name}{EXE}",)
    found = {
        host: Artifact(f"https://origin.test/{name}/{version}/{host}.zip", sha(data))
        for host, data in artifacts.items()
    }
    layout = Layout(
        root=root or None,
        exe=exe or None,
        entry_points=entry_points,
        paths=paths or None,
        env=env or None,
        shims=shims or None,
    )
    return Record(
        name,
        kind=kind,
        hosts=tuple(artifacts),
        layout=layout,
        deltas=(RecordDelta(1, version, "", found),),
    )


@pytest.fixture
def origin(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    """The origin's bytes by URL; the download seam reads them, never the network."""
    served: dict[str, bytes] = {}

    def download(url: str) -> bytes:
        if url not in served:
            raise OSError(f"no such origin {url}")
        return served[url]

    monkeypatch.setattr(_engine, "download", download)
    return served


def _serve(origin: dict[str, bytes], spec: Record, artifacts: dict[str, bytes]) -> None:
    for host, data in artifacts.items():
        origin[spec.deltas[-1].artifacts[host].url] = data


@pytest.fixture
def home(tmp_path: Path) -> Home:
    return Home(tmp_path / "home")


def _tool(name: str = "tool") -> tuple[dict[str, bytes], bytes]:
    data = make_zip(
        {
            f"{name}-1.0.0/bin/{name}{EXE}": b"#!/bin/sh\necho hi\n",
            f"{name}-1.0.0/README": b"r",
        },
        executable=(f"{name}-1.0.0/bin/{name}{EXE}",),
    )
    return {HOST: data, OTHER: data + b"\n"}, data


# --- refusals and fallbacks first ------------------------------------------


def test_a_host_no_spec_names_and_a_delegated_kind_are_refused(home: Home) -> None:
    with pytest.raises(StoreError, match="host 'plan9-mips' is not one of"):
        Store(home, host="plan9-mips")
    store = Store(home, host=HOST)
    artifacts, _ = _tool()
    delegated = _record("bunx", artifacts, kind="bun-install")
    with pytest.raises(StoreError, match="kind 'bun-install' is delegated to its tool"):
        store.ensure(delegated, delegated.versions[-1])
    assert store.probe(delegated, delegated.versions[-1]) is None
    with pytest.raises(StoreError, match=r"tool: a archive needs its deployment"):
        store.supply("tool", "archive", "1.0.0")
    with pytest.raises(RecordError, match="no host windows-arm"):
        Store(home, host="windows-arm").ensure(_record("tool", artifacts), "1.0.0")


def test_an_origin_serving_the_wrong_bytes_is_refused_naming_it(
    home: Home, origin: dict[str, bytes]
) -> None:
    artifacts, data = _tool()
    spec = _record("tool", artifacts)
    _serve(origin, spec, {HOST: data + b"tampered"})
    store = Store(home, host=HOST)
    with pytest.raises(StoreError, match="served bytes that are not sha256:"):
        store.ensure(spec, spec.versions[-1])
    assert store.probe(spec, spec.versions[-1]) is None
    assert store.objects.state(Digest("sha256", sha(data))) == "absent"


def test_an_offline_miss_fails_closed_naming_the_origin(
    home: Home, origin: dict[str, bytes]
) -> None:
    artifacts, _data = _tool()
    spec = _record("tool", artifacts)
    _serve(origin, spec, artifacts)
    store = Store(home, host=HOST, offline=True)
    with pytest.raises(
        StoreError,
        match=r"tool@1\.0\.0: sha256:.* is in no source and the store is offline; https://origin.test/tool/1.0.0/linux-x64.zip would have satisfied it",
    ):
        store.ensure(spec, spec.versions[-1])


def test_a_corrupt_mirror_entry_is_passed_over_for_the_next_tier(
    home: Home, origin: dict[str, bytes], tmp_path: Path
) -> None:
    artifacts, data = _tool()
    spec = _record("tool", artifacts)
    _serve(origin, spec, artifacts)
    mirror = Home(tmp_path / "mirror").open_store()
    digest = Digest("sha256", sha(data))
    # The mirror holds wrong bytes under the right name.
    wrong = mirror.object_path(digest)
    wrong.parent.mkdir(parents=True, exist_ok=True)
    wrong.write_bytes(b"not the tool")
    events: list[Event] = []
    store = Store(
        home, host=HOST, sources=[FolderSource(mirror.root)], progress=events.append
    )
    ensured = store.ensure(spec, spec.versions[-1])
    assert ensured.installed
    assert [e.action for e in events] == ["probe", "fetch", "install", "link"][:3]
    assert events[1].detail == resolve(spec, spec.versions[-1], HOST).url


def test_an_archive_without_the_declared_root_is_refused(
    home: Home, origin: dict[str, bytes]
) -> None:
    artifacts, _data = _tool()
    spec = _record("tool", artifacts, root="elsewhere")
    _serve(origin, spec, artifacts)
    with pytest.raises(
        StoreError, match=r"has no root 'elsewhere'; it holds tool-1\.0\.0"
    ):
        Store(home, host=HOST).ensure(spec, spec.versions[-1])
    # Nothing half-made stays: no ref, no scratch, no tool directory.
    assert Store(home, host=HOST).objects.refs("tools") == []
    assert not any(home.tools.glob(".build-*"))


def test_a_binary_without_an_exe_and_a_non_archive_are_refused(
    home: Home, origin: dict[str, bytes]
) -> None:
    payload = b"#!/bin/sh\necho bin\n"
    # A binary that names no exe is refused by the record itself, at load.
    with pytest.raises(RecordError, match="a binary names no exe"):
        _record("bin", {HOST: payload}, kind="binary", paths=(".",))
    not_an_archive = Record(
        "raw",
        hosts=(HOST,),
        layout=Layout(entry_points=("raw",), paths=(".",)),
        deltas=(
            RecordDelta(
                1,
                "1",
                "",
                {HOST: Artifact("https://origin.test/raw.bin", sha(payload))},
            ),
        ),
    )
    origin["https://origin.test/raw.bin"] = payload
    with pytest.raises(
        StoreError, match=r"raw\.bin is not an archive the store extracts"
    ):
        Store(home, host=HOST).ensure(not_an_archive, not_an_archive.versions[-1])


def test_a_directory_the_store_did_not_make_is_never_removed(
    home: Home, origin: dict[str, bytes]
) -> None:
    artifacts, _data = _tool()
    spec = _record("tool", artifacts)
    _serve(origin, spec, artifacts)
    mine = home.tool_dir("tool", "1.0.0")
    mine.mkdir(parents=True)
    (mine / "precious").write_text("mine")
    with pytest.raises(
        StoreError, match="exists and is not the store's view; the store never removes"
    ):
        Store(home, host=HOST).ensure(spec, spec.versions[-1])
    assert (mine / "precious").read_text() == "mine"


def test_a_ref_that_already_names_another_tree_is_refused(
    home: Home, origin: dict[str, bytes]
) -> None:
    artifacts, _data = _tool()
    spec = _record("tool", artifacts)
    _serve(origin, spec, artifacts)
    store = Store(home, host=HOST)
    other = store.objects.put(b"{}")
    store.objects.set_ref("tools", "tool@1.0.0", other, previous=None, by=_engine.BY)
    with pytest.raises(
        StoreError,
        match=r"already names another tree \(sha256:.*\); the artifact changed",
    ):
        store.ensure(spec, spec.versions[-1])


def test_a_launcher_stands_in_where_a_link_is_refused(
    home: Home,
    origin: dict[str, bytes],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts, _data = _tool()
    spec = _record("tool", artifacts, root="tool-1.0.0")
    _serve(origin, spec, artifacts)
    store = Store(home, host=HOST)
    ensured = store.ensure(spec, spec.versions[-1])

    def refuse(target: Path, link: Path) -> None:
        raise OSError("symlinks refused here")

    monkeypatch.setattr(_engine, "symlink", refuse)
    (made,) = store.link([ensured], tmp_path / "bin")
    if sys.platform == "win32":
        assert made.with_suffix(".cmd").read_text().startswith("@echo off")
    else:
        assert not made.is_symlink()
        assert made.read_text().startswith("#!/bin/sh\nexec ")
        assert made.stat().st_mode & stat.S_IXUSR


# --- the shapes -----------------------------------------------------------------


def test_an_install_lands_extracts_hoists_collects_and_views(
    home: Home, origin: dict[str, bytes]
) -> None:
    artifacts, _data = _tool()
    spec = _record(
        "tool",
        artifacts,
        root="tool-1.0.0",
        env={"TOOL_HOME": "$package/lib", "TOOL_MODE": "fast"},
        shims={"alias": "bin/tool"},
    )
    _serve(origin, spec, artifacts)
    events: list[Event] = []
    store = Store(home, host=HOST, progress=events.append)
    ensured = store.ensure(spec, spec.versions[-1])
    assert ensured.installed and ensured.version == "1.0.0"
    tool = ensured.tool_dir / "bin" / f"tool{EXE}"
    assert tool.read_bytes() == b"#!/bin/sh\necho hi\n"
    if sys.platform != "win32":
        assert tool.stat().st_mode & stat.S_IXUSR
        assert (ensured.tool_dir / "alias").is_symlink()
    else:
        assert (ensured.tool_dir / "alias.exe").read_bytes() == tool.read_bytes()
    assert (ensured.tool_dir / "README").read_text() == "r"
    assert store.objects.ref("tools", "tool@1.0.0") == ensured.tree
    assert ensured.paths == (ensured.tool_dir / "bin",)
    assert ensured.env == {
        "TOOL_HOME": str(ensured.tool_dir / "lib"),
        "TOOL_MODE": "fast",
    }
    assert [e.action for e in events] == ["probe", "fetch", "install"]
    # The second call is a probe: nothing fetched, nothing installed.
    events.clear()
    again = store.ensure(spec, spec.versions[-1])
    assert not again.installed and again.tree == ensured.tree
    assert [e.action for e in events] == ["probe"]
    # A damaged view is made whole again through its own record. The
    # view's file is read-only, and Windows refuses to unlink one.
    tool.chmod(tool.stat().st_mode | stat.S_IWUSR)
    tool.unlink()
    assert store.probe(spec, spec.versions[-1]) is None
    repaired = store.ensure(spec, spec.versions[-1])
    assert repaired.installed and tool.exists()


def test_a_tar_archive_and_a_binary_install_too(
    home: Home, origin: dict[str, bytes]
) -> None:
    tar = make_tar({"tool": b"#!/bin/sh\necho tar\n"}, executable=("tool",))
    url = "https://origin.test/tarred/1.0.0/linux-x64.tar.gz"
    spec = Record(
        "tarred",
        hosts=(HOST,),
        layout=Layout(entry_points=("tool",), paths=(".",)),
        deltas=(RecordDelta(1, "1.0.0", "", {HOST: Artifact(url, sha(tar))}),),
    )
    origin[url] = tar
    store = Store(home, host=HOST)
    ensured = store.ensure(spec, spec.versions[-1])
    assert (ensured.tool_dir / "tool").read_bytes().endswith(b"echo tar\n")
    payload = b"#!/bin/sh\necho bin\n"
    binary = _record("bin", {HOST: payload}, kind="binary", exe="bin", paths=(".",))
    _serve(origin, binary, {HOST: payload})
    placed = store.ensure(binary, binary.versions[-1])
    assert (placed.tool_dir / "bin").read_bytes() == payload
    if sys.platform != "win32":
        assert (placed.tool_dir / "bin").stat().st_mode & stat.S_IXUSR


def test_link_fills_the_bin_directory_and_removes_only_what_it_made(
    home: Home, origin: dict[str, bytes], tmp_path: Path
) -> None:
    artifacts, _data = _tool()
    spec = _record("tool", artifacts, root="tool-1.0.0")
    _serve(origin, spec, artifacts)
    other_artifacts, _other_data = _tool("other")
    other = _record("other", other_artifacts, root="other-1.0.0")
    _serve(origin, other, other_artifacts)
    store = Store(home, host=HOST)
    installs = [
        store.ensure(spec, spec.versions[-1]),
        store.ensure(other, other.versions[-1]),
    ]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "mine").write_text("a person's file")
    made = store.link(installs, bin_dir)
    assert [p.name for p in made] == [f"tool{EXE}", f"other{EXE}"]
    if sys.platform != "win32":
        assert (bin_dir / "tool").is_symlink()
        assert os.access(bin_dir / "tool", os.X_OK)
    # The pins change: only the store's links move; the person's file stays.
    made = store.link(installs[1:], bin_dir)
    assert [p.name for p in made] == [f"other{EXE}"]
    assert not (bin_dir / f"tool{EXE}").exists()
    assert (bin_dir / "mine").read_text() == "a person's file"
    delta = store.delta(installs, bin_dir)
    assert delta.paths == (bin_dir,)
    assert store.delta(installs).paths == (
        installs[0].tool_dir / "bin",
        installs[1].tool_dir / "bin",
    )


def test_fetch_builds_a_mirror_an_offline_store_installs_from(
    home: Home, origin: dict[str, bytes], tmp_path: Path
) -> None:
    artifacts, data = _tool()
    spec = _record("tool", artifacts, root="tool-1.0.0")
    _serve(origin, spec, artifacts)
    store = Store(Home(tmp_path / "fetcher"), host=HOST)
    mirror = tmp_path / "mirror"
    fetched = store.fetch([spec], into=mirror)
    assert sorted((f.host, f.landed) for f in fetched) == [(HOST, True), (OTHER, True)]
    # A second fetch lands nothing: the mirror already holds every artifact.
    assert all(not f.landed for f in store.fetch([spec], into=mirror))
    # Only the hosts asked for.
    fresh = tmp_path / "one-host"
    assert [f.host for f in store.fetch([spec], hosts=[OTHER], into=fresh)] == [OTHER]
    # An offline store installs from the mirror alone.
    offline = Store(
        home, host=HOST, offline=True, sources=[FolderSource(Home(mirror).store)]
    )
    ensured = offline.ensure(spec, spec.versions[-1])
    assert ensured.installed
    # One blob gone from the mirror: the next offline install fails closed, naming it.
    gone = Home(mirror).open_store()
    gone.evict(Digest("sha256", sha(data + b"\n")))
    other_home = Home(tmp_path / "other-home")
    with pytest.raises(StoreError, match="is in no source and the store is offline"):
        Store(
            other_home,
            host=OTHER,
            offline=True,
            sources=[FolderSource(Home(mirror).store)],
        ).ensure(spec, spec.versions[-1])


# --- the seams and the edges ------------------------------------------------------


def test_the_running_machine_is_the_default_host(home: Home) -> None:
    import platform

    from livery.toolroom.store import host_key

    assert Store(home).host == host_key(platform.system(), platform.machine())


def test_the_download_reads_the_origin_through_the_fetch_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import contextmanager

    seen: list[str] = []

    @contextmanager
    def fake(
        url: str,
        *,
        connect_timeout: float,
        transfer_timeout: float,
        method: str = "GET",
    ) -> Iterator[io.BytesIO]:
        seen.append(url)
        yield io.BytesIO(b"the bytes")

    monkeypatch.setattr(_engine, "fetch_url", fake)
    assert _engine._download("https://origin.test/a.zip") == b"the bytes"
    assert seen == ["https://origin.test/a.zip"]


def test_an_archive_that_will_not_extract_is_refused(
    home: Home, origin: dict[str, bytes]
) -> None:
    broken = b"PK\x03\x04 not really a zip"
    spec = _record("broken", {HOST: broken})
    _serve(origin, spec, {HOST: broken})
    with pytest.raises(StoreError, match=r"the archive at .* will not extract"):
        Store(home, host=HOST).ensure(spec, spec.versions[-1])


def test_a_zip_with_directories_and_modeless_members_installs(
    home: Home, origin: dict[str, bytes]
) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(zipfile.ZipInfo("t/"), b"")  # a directory entry
        plain = zipfile.ZipInfo("t/bin/plain")  # no mode bits at all
        archive.writestr(plain, b"x")
        exe = zipfile.ZipInfo("t/bin/exe")
        exe.external_attr = 0o755 << 16
        archive.writestr(exe, b"#!/bin/sh\n")
        subdir = zipfile.ZipInfo("t/bin/")
        subdir.external_attr = 0o755 << 16
        archive.writestr(subdir, b"")
        # A member that names its way out of the archive: extracted
        # sanitised, and its mode never applied outside the root.
        escape = zipfile.ZipInfo("../escape")
        escape.external_attr = 0o755 << 16
        archive.writestr(escape, b"#!/bin/sh\n")
    data = buffer.getvalue()
    spec = _record("zipped", {HOST: data}, root="t", entry_points=("bin/exe",))
    _serve(origin, spec, {HOST: data})
    ensured = Store(home, host=HOST).ensure(spec, spec.versions[-1])
    assert (ensured.tool_dir / "bin" / "plain").read_bytes() == b"x"


def test_link_links_the_declared_entry_points_and_skips_a_name_taken(
    home: Home, origin: dict[str, bytes], tmp_path: Path
) -> None:
    data = make_zip(
        {
            f"t/bin/tool{EXE}": b"#!/bin/sh\n",
            "t/bin/README": b"prose",
            f"t/bin/sub/inner{EXE}": b"#!/bin/sh\n",
            "t/lib/only": b"",
        },
        executable=(f"t/bin/tool{EXE}", f"t/bin/sub/inner{EXE}"),
    )
    entry = (f"bin/tool{EXE}",)
    spec = _record(
        "first", {HOST: data}, root="t", paths=("bin", "missing"), entry_points=entry
    )
    _serve(origin, spec, {HOST: data})
    twin = _record(
        "second", {HOST: data + b"\n"}, root="t", paths=("bin",), entry_points=entry
    )
    _serve(origin, twin, {HOST: data + b"\n"})
    store = Store(home, host=HOST)
    installs = [
        store.ensure(spec, spec.versions[-1]),
        store.ensure(twin, twin.versions[-1]),
    ]
    bin_dir = tmp_path / "bin"
    made = store.link(installs, bin_dir)
    # One link: the annotation names tool alone, so the executable
    # under sub never reaches the bin directory, README is prose, and
    # the twin's tool is a name already taken.
    assert [p.name for p in made] == [f"tool{EXE}"]
    # A link the manifest names but that is already gone is no error.
    (bin_dir / f"tool{EXE}").unlink()
    assert [p.name for p in store.link(installs, bin_dir)] == [f"tool{EXE}"]
    # The same install twice contributes its directory once.
    assert store.delta([installs[0], installs[0]]).paths == installs[0].paths


def test_fetch_skips_a_delegated_kind_and_a_shim_never_overwrites(
    home: Home, origin: dict[str, bytes], tmp_path: Path
) -> None:
    data = make_zip(
        {f"t/bin/bun{EXE}": b"#!/bin/sh\n", f"t/node{EXE}": b"already here"},
        executable=(f"t/bin/bun{EXE}",),
    )
    spec = _record("bun", {HOST: data}, root="t", shims={"node": "bin/bun"})
    _serve(origin, spec, {HOST: data})
    delegated = _record("uvx", {HOST: b"unused"}, kind="uv-tool")
    store = Store(home, host=HOST)
    ensured = store.ensure(spec, spec.versions[-1])
    # The archive carried a `node` of its own: the shim leaves it alone.
    assert (ensured.tool_dir / f"node{EXE}").read_bytes() == b"already here"
    fetched = store.fetch([delegated, spec], into=tmp_path / "mirror")
    assert [f.name for f in fetched] == ["bun"]


# --- the delegated kinds ---------------------------------------------------------


def _uv_tool(name: str = "ruff", *versions: str) -> Record:
    from livery.toolroom.store import Surface

    return Record(
        name,
        kind="uv-tool",
        package="ruff-package" if name == "ruff" else "",
        deltas=tuple(
            RecordDelta(
                n, v, "", surface=Surface(("Linux",), 1, "A tool." if n == 1 else None)
            )
            for n, v in enumerate(versions or ("1.0.0",), start=1)
        ),
    )


def test_an_installer_that_fails_or_writes_no_launcher_leaves_nothing(
    home: Home, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = Store(home, host=HOST)
    calls: list[tuple[list[str], dict[str, str]]] = []

    def failing(argv: list[str], env: dict[str, str]) -> int:
        calls.append((argv, env))
        return 3

    monkeypatch.setattr(_engine, "run_installer", failing)
    record = _uv_tool("ruff", "1.0.0")
    with pytest.raises(
        StoreError,
        match=r"ruff 1\.0\.0: `uv tool install ruff-package==1\.0\.0` exited 3",
    ):
        store.ensure(record, "1.0.0")
    assert not (home.uv / "tools" / "ruff@1.0.0").exists()
    assert calls[0][0] == ["uv", "tool", "install", "ruff-package==1.0.0"]
    assert calls[0][1]["UV_TOOL_BIN_DIR"].endswith(os.path.join("ruff@1.0.0", "bin"))

    # An installer that exits 0 and writes no launcher is a failure too.
    monkeypatch.setattr(_engine, "run_installer", lambda argv, env: 0)
    with pytest.raises(StoreError, match=r"exited 0 and left no launcher"):
        store.ensure(record, "1.0.0")
    assert store.probe(record, "1.0.0") is None


def test_a_uv_tool_is_installed_once_into_its_own_directory(
    home: Home, monkeypatch: pytest.MonkeyPatch
) -> None:
    def installing(argv: list[str], env: dict[str, str]) -> int:
        bin_dir = Path(env["UV_TOOL_BIN_DIR"])
        bin_dir.mkdir(parents=True, exist_ok=True)
        (bin_dir / f"ruff{EXE}").write_text("launcher")
        (bin_dir / "helper").write_text("launcher")
        return 0

    calls: list[list[str]] = []

    def counting(argv: list[str], env: dict[str, str]) -> int:
        calls.append(argv)
        return installing(argv, env)

    monkeypatch.setattr(_engine, "run_installer", counting)
    events: list[Event] = []
    store = Store(home, host=HOST, progress=events.append)
    record = _uv_tool("ruff", "1.0.0")
    ensured = store.ensure(record, "1.0.0")
    assert ensured.installed and ensured.tree is None
    assert ensured.tool_dir == home.uv / "tools" / "ruff@1.0.0"
    assert ensured.deployment.entry_points == ("bin/helper", f"bin/ruff{EXE}")
    assert ensured.paths == (ensured.tool_dir / "bin",)
    assert [e.action for e in events] == ["probe", "install"]
    # A second ensure is a probe: nothing installs again.
    again = store.ensure(record, "1.0.0")
    assert not again.installed and len(calls) == 1
    assert store.probe(record, "1.0.0") is not None
    # Offline, the installer is told so.
    Store(home, host=HOST, offline=True).ensure(_uv_tool("ruff", "2.0.0"), "2.0.0")
    assert calls[-1] == ["uv", "tool", "install", "ruff-package==2.0.0", "--offline"]
    # The launchers link like any entry point.
    links = store.link([ensured], home.root / "bin")
    assert sorted(p.name for p in links) == ["helper", f"ruff{EXE}"]


def test_a_system_tool_is_found_on_path_and_held_to_its_floor(
    home: Home, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = Store(home, host=HOST)
    record = Record(
        "git",
        kind="system-check",
        min_version="2.40",
        deltas=_uv_tool("git", "2.55.0").deltas,
    )
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(StoreError, match=r"git: not on PATH; a system-check tool"):
        store.ensure(record, "2.55.0")
    assert store.probe(record, "2.55.0") is None
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/git")
    monkeypatch.setattr(_engine, "read_version", lambda argv: "git version 2.39.1")
    with pytest.raises(
        StoreError, match=r"git: /usr/bin/git reports 2\.39\.1, below the floor 2\.40"
    ):
        store.ensure(record, "2.55.0")
    monkeypatch.setattr(_engine, "read_version", lambda argv: "git version 2.55.0")
    ensured = store.ensure(record, "2.55.0")
    assert not ensured.installed and ensured.tool_dir == Path("/usr/bin")
    assert ensured.deployment.entry_points == () and ensured.paths == ()
    probed = store.probe(record, "2.55.0")
    assert probed is not None and probed.tool_dir == Path("/usr/bin")
    # No floor in the record: the locked version is the newest reading, not
    # a floor, so the machine's own tool passes at any version, or at none.
    bare = Record("git", kind="system-check", deltas=record.deltas)
    monkeypatch.setattr(_engine, "read_version", lambda argv: "git version 2.39.1")
    assert store.ensure(bare, "2.55.0").tool_dir == Path("/usr/bin")
    monkeypatch.setattr(_engine, "read_version", lambda argv: "")
    assert store.ensure(bare, "2.55.0").tool_dir == Path("/usr/bin")
    # A floored record still refuses a tool that prints no version.
    monkeypatch.setattr(_engine, "read_version", lambda argv: "")
    with pytest.raises(StoreError, match=r"reports no version, below the floor 2\.40"):
        store.ensure(record, "2.55.0")


def test_the_version_reader_reads_the_first_numeric_run_and_survives_no_tool(
    tmp_path: Path,
) -> None:
    printed = _engine._read_version([sys.executable, "--version"])
    assert _engine._version_in(printed).count(".") >= 1
    assert _engine._read_version([str(tmp_path / "nope")]) == ""
    assert _engine._version_in("none") == ""


def test_a_delegated_install_runs_its_installer_with_the_environment_handed_over(
    tmp_path: Path,
) -> None:
    script = tmp_path / "echo.py"
    script.write_text(
        "import os, sys; open(sys.argv[1], 'w').write(os.environ['UV_TOOL_DIR'])"
    )
    out = tmp_path / "out"
    code = _engine._run_installer(
        [sys.executable, str(script), str(out)], {"UV_TOOL_DIR": "here"}
    )
    assert code == 0 and out.read_text() == "here"


def test_an_origin_that_does_not_answer_is_a_store_refusal_naming_the_url(
    home: Home, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source's refusal is the store's to name.

    The workspace reports it and goes on, which a raw exception would
    not let it do.
    """
    from livery.strongroom import Unreachable

    artifacts, _data = _tool()
    record = _record("tool", artifacts)

    def unreachable(url: str) -> bytes:
        raise Unreachable(f"{url}: HTTP 302")

    monkeypatch.setattr(_engine, "download", unreachable)
    with pytest.raises(StoreError, match=r"tool@1\.0\.0: the origin .* did not answer"):
        Store(home, host=HOST).ensure(record, "1.0.0")

    def refused(url: str) -> bytes:
        raise OSError("connection reset")

    monkeypatch.setattr(_engine, "download", refused)
    with pytest.raises(StoreError, match=r"connection reset"):
        Store(home, host=HOST).ensure(record, "1.0.0")
