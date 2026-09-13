"""The engine: refusals and fallbacks first, then installs, links, deltas, mirrors."""

from __future__ import annotations

import hashlib
import io
import os
import stat
import sys
import tarfile
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from livery.strongroom import Digest, FolderSource
from livery.toolroom.store import (
    Definition,
    Event,
    Home,
    Spec,
    SpecError,
    Store,
    StoreError,
    Version,
    _engine,
)

HOST = "linux-x64"
OTHER = "macos-arm"


def _zip(files: dict[str, bytes], *, executable: tuple[str, ...] = ()) -> bytes:
    """A zip whose members carry Unix modes, an executable bit where named."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            info = zipfile.ZipInfo(name)
            mode = 0o755 if name in executable else 0o644
            info.external_attr = mode << 16
            archive.writestr(info, data)
    return buffer.getvalue()


def _tar(files: dict[str, bytes], *, executable: tuple[str, ...] = ()) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755 if name in executable else 0o644
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _spec(
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
) -> Spec:
    """A spec pinning *version* with one definition per host in *artifacts*."""
    definitions = {}
    for host, data in artifacts.items():
        platform, arch = host.split("-")
        definitions[host] = Definition(
            platform,
            arch,
            url=f"https://origin.test/{name}/{version}/{host}.zip",
            sha256=_sha(data),
            root=root,
            exe=exe,
            paths=paths,
            env=env or {},
            shims=shims or {},
        )
    return Spec(
        name,
        kind=kind,
        pinned=version,
        versions={version: Version(version, definitions)},
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


def _serve(origin: dict[str, bytes], spec: Spec, artifacts: dict[str, bytes]) -> None:
    for host, data in artifacts.items():
        origin[spec.versions[spec.pinned].definitions[host].url] = data


@pytest.fixture
def home(tmp_path: Path) -> Home:
    return Home(tmp_path / "home")


def _tool(name: str = "tool") -> tuple[dict[str, bytes], bytes]:
    data = _zip(
        {
            f"{name}-1.0.0/bin/{name}": b"#!/bin/sh\necho hi\n",
            f"{name}-1.0.0/README": b"r",
        },
        executable=(f"{name}-1.0.0/bin/{name}",),
    )
    return {HOST: data, OTHER: data + b"\n"}, data


# --- refusals and fallbacks first ------------------------------------------


def test_a_host_no_spec_names_and_a_delegated_kind_are_refused(home: Home) -> None:
    with pytest.raises(StoreError, match="host 'plan9-mips' is not one of"):
        Store(home, host="plan9-mips")
    store = Store(home, host=HOST)
    artifacts, _ = _tool()
    delegated = _spec("uvx", artifacts, kind="uv-tool")
    with pytest.raises(StoreError, match="kind 'uv-tool' is delegated to its tool"):
        store.ensure(delegated)
    with pytest.raises(SpecError, match="no definition for windows-arm"):
        Store(home, host="windows-arm").ensure(_spec("tool", artifacts))


def test_an_origin_serving_the_wrong_bytes_is_refused_naming_it(
    home: Home, origin: dict[str, bytes]
) -> None:
    artifacts, data = _tool()
    spec = _spec("tool", artifacts)
    _serve(origin, spec, {HOST: data + b"tampered"})
    store = Store(home, host=HOST)
    with pytest.raises(StoreError, match="served bytes that are not sha256:"):
        store.ensure(spec)
    assert store.probe(spec) is None
    assert store.objects.state(Digest("sha256", _sha(data))) == "absent"


def test_an_offline_miss_fails_closed_naming_the_origin(
    home: Home, origin: dict[str, bytes]
) -> None:
    artifacts, _data = _tool()
    spec = _spec("tool", artifacts)
    _serve(origin, spec, artifacts)
    store = Store(home, host=HOST, offline=True)
    with pytest.raises(
        StoreError,
        match=r"tool@1\.0\.0: sha256:.* is in no source and the store is offline; https://origin.test/tool/1.0.0/linux-x64.zip would have satisfied it",
    ):
        store.ensure(spec)


def test_a_corrupt_mirror_entry_is_passed_over_for_the_next_tier(
    home: Home, origin: dict[str, bytes], tmp_path: Path
) -> None:
    artifacts, data = _tool()
    spec = _spec("tool", artifacts)
    _serve(origin, spec, artifacts)
    mirror = Home(tmp_path / "mirror").open_store()
    digest = Digest("sha256", _sha(data))
    # The mirror holds wrong bytes under the right name.
    wrong = mirror.object_path(digest)
    wrong.parent.mkdir(parents=True, exist_ok=True)
    wrong.write_bytes(b"not the tool")
    events: list[Event] = []
    store = Store(
        home, host=HOST, sources=[FolderSource(mirror.root)], progress=events.append
    )
    ensured = store.ensure(spec)
    assert ensured.installed
    assert [e.action for e in events] == ["probe", "fetch", "install", "link"][:3]
    assert events[1].detail == spec.definition_for(HOST).url


def test_an_archive_without_the_declared_root_is_refused(
    home: Home, origin: dict[str, bytes]
) -> None:
    artifacts, _data = _tool()
    spec = _spec("tool", artifacts, root="elsewhere")
    _serve(origin, spec, artifacts)
    with pytest.raises(
        StoreError, match=r"has no root 'elsewhere'; it holds tool-1\.0\.0"
    ):
        Store(home, host=HOST).ensure(spec)
    # Nothing half-made stays: no ref, no scratch, no tool directory.
    assert Store(home, host=HOST).objects.refs("tools") == []
    assert not any(home.tools.glob(".build-*"))


def test_a_binary_without_an_exe_and_a_non_archive_are_refused(
    home: Home, origin: dict[str, bytes]
) -> None:
    payload = b"#!/bin/sh\necho bin\n"
    spec = _spec("bin", {HOST: payload}, kind="binary", paths=(".",))
    _serve(origin, spec, {HOST: payload})
    with pytest.raises(
        StoreError, match="the binary definition linux-x64 names no exe"
    ):
        Store(home, host=HOST).ensure(spec)
    not_an_archive = Spec(
        "raw",
        pinned="1",
        versions={
            "1": Version(
                "1",
                {
                    HOST: Definition(
                        "linux",
                        "x64",
                        url="https://origin.test/raw.bin",
                        sha256=_sha(payload),
                    )
                },
            )
        },
    )
    origin["https://origin.test/raw.bin"] = payload
    with pytest.raises(
        StoreError, match=r"raw\.bin is not an archive the store extracts"
    ):
        Store(home, host=HOST).ensure(not_an_archive)


def test_a_directory_the_store_did_not_make_is_never_removed(
    home: Home, origin: dict[str, bytes]
) -> None:
    artifacts, _data = _tool()
    spec = _spec("tool", artifacts)
    _serve(origin, spec, artifacts)
    mine = home.tool_dir("tool", "1.0.0")
    mine.mkdir(parents=True)
    (mine / "precious").write_text("mine")
    with pytest.raises(
        StoreError, match="exists and is not the store's view; the store never removes"
    ):
        Store(home, host=HOST).ensure(spec)
    assert (mine / "precious").read_text() == "mine"


def test_a_ref_that_already_names_another_tree_is_refused(
    home: Home, origin: dict[str, bytes]
) -> None:
    artifacts, _data = _tool()
    spec = _spec("tool", artifacts)
    _serve(origin, spec, artifacts)
    store = Store(home, host=HOST)
    other = store.objects.put(b"{}")
    store.objects.set_ref("tools", "tool@1.0.0", other, previous=None, by=_engine.BY)
    with pytest.raises(
        StoreError,
        match=r"already names another tree \(sha256:.*\); the artifact changed",
    ):
        store.ensure(spec)


def test_a_launcher_stands_in_where_a_link_is_refused(
    home: Home,
    origin: dict[str, bytes],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts, _data = _tool()
    spec = _spec("tool", artifacts, root="tool-1.0.0")
    _serve(origin, spec, artifacts)
    store = Store(home, host=HOST)
    ensured = store.ensure(spec)

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
    spec = _spec(
        "tool",
        artifacts,
        root="tool-1.0.0",
        env={"TOOL_HOME": "$package/lib", "TOOL_MODE": "fast"},
        shims={"alias": "bin/tool"},
    )
    _serve(origin, spec, artifacts)
    events: list[Event] = []
    store = Store(home, host=HOST, progress=events.append)
    ensured = store.ensure(spec)
    assert ensured.installed and ensured.version == "1.0.0"
    tool = ensured.tool_dir / "bin" / "tool"
    assert tool.read_bytes() == b"#!/bin/sh\necho hi\n"
    if sys.platform != "win32":
        assert tool.stat().st_mode & stat.S_IXUSR
        assert (ensured.tool_dir / "alias").is_symlink()
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
    again = store.ensure(spec)
    assert not again.installed and again.tree == ensured.tree
    assert [e.action for e in events] == ["probe"]
    # A damaged view is made whole again through its own record.
    tool.unlink()
    assert store.probe(spec) is None
    repaired = store.ensure(spec)
    assert repaired.installed and tool.exists()


def test_a_tar_archive_and_a_binary_install_too(
    home: Home, origin: dict[str, bytes]
) -> None:
    tar = _tar({"tool": b"#!/bin/sh\necho tar\n"}, executable=("tool",))
    spec = _spec("tarred", {HOST: tar}, paths=(".",))
    url = spec.definition_for(HOST).url.replace(".zip", ".tar.gz")
    spec = Spec(
        "tarred",
        pinned="1.0.0",
        versions={
            "1.0.0": Version(
                "1.0.0",
                {
                    HOST: Definition(
                        "linux", "x64", url=url, sha256=_sha(tar), paths=(".",)
                    )
                },
            )
        },
    )
    origin[url] = tar
    store = Store(home, host=HOST)
    ensured = store.ensure(spec)
    assert (ensured.tool_dir / "tool").read_bytes().endswith(b"echo tar\n")
    payload = b"#!/bin/sh\necho bin\n"
    binary = _spec("bin", {HOST: payload}, kind="binary", exe="bin", paths=(".",))
    _serve(origin, binary, {HOST: payload})
    placed = store.ensure(binary)
    assert (placed.tool_dir / "bin").read_bytes() == payload
    if sys.platform != "win32":
        assert (placed.tool_dir / "bin").stat().st_mode & stat.S_IXUSR


def test_link_fills_the_bin_directory_and_removes_only_what_it_made(
    home: Home, origin: dict[str, bytes], tmp_path: Path
) -> None:
    artifacts, _data = _tool()
    spec = _spec("tool", artifacts, root="tool-1.0.0")
    _serve(origin, spec, artifacts)
    other_artifacts, _other_data = _tool("other")
    other = _spec("other", other_artifacts, root="other-1.0.0")
    _serve(origin, other, other_artifacts)
    store = Store(home, host=HOST)
    installs = [store.ensure(spec), store.ensure(other)]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "mine").write_text("a person's file")
    made = store.link(installs, bin_dir)
    assert [p.name for p in made] == ["tool", "other"]
    if sys.platform != "win32":
        assert (bin_dir / "tool").is_symlink()
        assert os.access(bin_dir / "tool", os.X_OK)
    # The pins change: only the store's links move; the person's file stays.
    made = store.link(installs[1:], bin_dir)
    assert [p.name for p in made] == ["other"]
    assert not (bin_dir / "tool").exists()
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
    spec = _spec("tool", artifacts, root="tool-1.0.0")
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
    ensured = offline.ensure(spec)
    assert ensured.installed
    # One blob gone from the mirror: the next offline install fails closed, naming it.
    gone = Home(mirror).open_store()
    gone.evict(Digest("sha256", _sha(data + b"\n")))
    other_home = Home(tmp_path / "other-home")
    with pytest.raises(StoreError, match="is in no source and the store is offline"):
        Store(
            other_home,
            host=OTHER,
            offline=True,
            sources=[FolderSource(Home(mirror).store)],
        ).ensure(spec)


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
    spec = _spec("broken", {HOST: broken})
    _serve(origin, spec, {HOST: broken})
    with pytest.raises(StoreError, match=r"the archive at .* will not extract"):
        Store(home, host=HOST).ensure(spec)


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
    spec = _spec("zipped", {HOST: data}, root="t")
    _serve(origin, spec, {HOST: data})
    ensured = Store(home, host=HOST).ensure(spec)
    assert (ensured.tool_dir / "bin" / "plain").read_bytes() == b"x"


def test_link_skips_what_is_not_an_executable_file_or_a_name_taken(
    home: Home, origin: dict[str, bytes], tmp_path: Path
) -> None:
    data = _zip(
        {
            "t/bin/tool": b"#!/bin/sh\n",
            "t/bin/README": b"prose",
            "t/bin/sub/inner": b"#!/bin/sh\n",
            "t/lib/only": b"",
        },
        executable=("t/bin/tool", "t/bin/sub/inner"),
    )
    spec = _spec("first", {HOST: data}, root="t", paths=("bin", "missing"))
    _serve(origin, spec, {HOST: data})
    twin = _spec("second", {HOST: data + b"\n"}, root="t", paths=("bin",))
    _serve(origin, twin, {HOST: data + b"\n"})
    store = Store(home, host=HOST)
    installs = [store.ensure(spec), store.ensure(twin)]
    bin_dir = tmp_path / "bin"
    made = store.link(installs, bin_dir)
    # One link: README is not executable, sub is a directory, the twin's
    # tool is a name already taken, and "missing" is no directory.
    assert [p.name for p in made] == ["tool"]
    # A link the manifest names but that is already gone is no error.
    (bin_dir / "tool").unlink()
    assert [p.name for p in store.link(installs, bin_dir)] == ["tool"]
    # The same install twice contributes its directory once.
    assert store.delta([installs[0], installs[0]]).paths == installs[0].paths


def test_fetch_skips_a_delegated_kind_and_a_shim_never_overwrites(
    home: Home, origin: dict[str, bytes], tmp_path: Path
) -> None:
    data = _zip(
        {"t/bin/bun": b"#!/bin/sh\n", "t/node": b"already here"},
        executable=("t/bin/bun",),
    )
    spec = _spec("bun", {HOST: data}, root="t", shims={"node": "bin/bun"})
    _serve(origin, spec, {HOST: data})
    delegated = _spec("uvx", {HOST: b"unused"}, kind="uv-tool")
    store = Store(home, host=HOST)
    ensured = store.ensure(spec)
    # The archive carried a `node` of its own: the shim leaves it alone.
    assert (ensured.tool_dir / "node").read_bytes() == b"already here"
    fetched = store.fetch([delegated, spec], into=tmp_path / "mirror")
    assert [f.name for f in fetched] == ["bun"]
