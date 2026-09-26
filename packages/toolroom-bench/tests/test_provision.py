"""The provisioning engine and task — `fm tools.provision`.

The tiers are driven with the real driver metadata but mocked at their one
outward edge (subprocess, HTTP), so the grouping, dedup, asset matching and
unpacking are exercised without installing anything or hitting the network.
"""

from __future__ import annotations

import io
import os
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from livery.toolroom.bench import _provision
from livery.toolroom.bench._drivers import Driver, Provision


def _tar_gz(path: Path, arcname: str, data: bytes) -> None:
    with tarfile.open(path, "w:gz") as tar:
        info = tarfile.TarInfo(arcname)
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))


def _zip(path: Path, arcname: str, data: bytes) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(arcname, data)


# --- tiers -------------------------------------------------------------------


def test_only_takes_a_set_of_tools(tmp_path):
    """A gather drives the tiers from `uv` and `bun` and installs each
    release itself, so those two are all a refresh needs in its prefix.
    Fetching the other 26 was work nothing read — and 26 more chances for a
    dropped connection to cost a platform its observations.
    """
    drivers = (Driver("ruff"), Driver("uv"), Driver("bun"))
    outcomes = _provision.provision(drivers, tmp_path / "p", only="uv,bun")
    assert sorted(o.key for o in outcomes) == ["bun", "uv"]
    # and one name still means one tool
    assert [o.key for o in _provision.provision(drivers, tmp_path / "p", only="ruff")]


def test_a_spent_fetch_is_a_provision_error_naming_the_url(tmp_path, monkeypatch):
    """The retry lives in the store; what the bench adds is its own refusal."""
    from livery.toolroom.store import FetchError

    def spent(url, *_a, **_kw):
        raise FetchError(f"{url}: reset", status=None)

    monkeypatch.setattr(_provision, "fetch_file", spent)
    with pytest.raises(_provision.ProvisionError, match=r"http://x/gh\.zip: reset"):
        _provision._download("http://x/gh.zip", tmp_path)
    monkeypatch.setattr(_provision, "fetch_json", spent)
    with pytest.raises(_provision.ProvisionError, match="http://x/api: reset"):
        _provision._get_json("http://x/api")


def test_strict_turns_a_failed_tier_into_a_failed_run(tmp_path, monkeypatch):
    """`ok` for a prefix that is missing tools is right for a person and
    wrong for a job. A refresh run where bun hit a rate limit still said
    `ok`, and the half-provisioned prefix went into the gather unremarked
    — cspell and markdownlint were skipped for want of the tool that had
    failed two steps earlier.
    """
    from livery.footman import Failed
    from livery.toolroom.bench import _tasks as tools

    outcomes = [
        _provision.Outcome("ruff", "uv", "ok", "ruff"),
        _provision.Outcome("bun", "bun", "fail", "HTTP Error 403: rate limit"),
    ]
    monkeypatch.setattr(_provision, "provision", lambda *a, **k: outcomes)

    # Without it: the table names the failure and the run succeeds.
    tools.provision(prefix=tmp_path / "p")

    with pytest.raises(Failed) as refused:
        tools.provision(prefix=tmp_path / "p", strict=True)
    assert "bun" in str(refused.value)
    assert "rate limit" in str(refused.value)
    assert refused.value.code == 70


def test_deferred_is_reported_not_fetched(tmp_path):
    # `system` stood beside `deferred` here until the tier was deleted: it
    # named tools taken off the host because fetching them per release was
    # not yet possible, and nothing is in that position any more.
    drivers = (
        Driver(
            "tea", provision=Provision(kind="deferred", note="hangs until > 0.14.2")
        ),
    )
    by = {o.key: o for o in _provision.provision(drivers, tmp_path)}
    assert by["tea"].status == "deferred" and "hangs" in by["tea"].detail


def test_uv_tier_installs_each_package_once(tmp_path, monkeypatch):
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(argv, env):
        calls.append((argv, env))
        return True

    monkeypatch.setattr(_provision, "_run", fake_run)
    drivers = (
        Driver("ruff", provision=Provision()),
        Driver("ruff", attr="ruff_format", base=("format",), provision=Provision()),
        Driver("mypy", provision=Provision()),
    )
    outcomes = _provision.provision(drivers, tmp_path)
    assert [argv[-1] for argv, _ in calls] == ["ruff", "mypy"]  # deduped
    assert all(o.status == "ok" for o in outcomes)
    argv, env = calls[0]
    assert argv[:4] == ["uv", "tool", "install", "--upgrade"]
    assert env["UV_TOOL_BIN_DIR"] == str(_provision.bin_dir(tmp_path))
    assert env["UV_TOOL_DIR"] == str(tmp_path / "uv-tools")


def test_uv_tier_failure_is_a_fail_outcome(tmp_path, monkeypatch):
    monkeypatch.setattr(_provision, "_run", lambda argv, env: False)
    (out,) = _provision.provision((Driver("ruff"),), tmp_path)
    assert out.status == "fail"


def _fake_node(prefix: Path) -> Path:
    """A node the nodejs tier would have unpacked, with npm's script beside it."""
    root = prefix / ".nodejs" / "node" / "node-v24.0.0-x"
    node = root / ("node.exe" if sys.platform == "win32" else "bin/node")
    cli = (
        root / "node_modules" / "npm" / "bin" / "npm-cli.js"
        if sys.platform == "win32"
        else root / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js"
    )
    for path in (node, cli):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    return node


def test_node_tier_fails_without_node_and_names_bun_when_a_driver_needs_it(tmp_path):
    drivers = (
        Driver("basedpyright", provision=Provision(kind="node")),
        Driver("cspell", provision=Provision(kind="node", runtime="bun")),
    )
    outcomes = _provision.provision(drivers, tmp_path)
    assert all(o.status == "fail" and "node" in o.detail for o in outcomes)
    _fake_node(tmp_path)
    by_key = {o.key: o for o in _provision.provision(drivers, tmp_path)}
    assert by_key["cspell"].status == "fail" and "bun" in by_key["cspell"].detail


def test_node_tier_installs_through_each_runtime(tmp_path, monkeypatch):
    node = _fake_node(tmp_path)
    _provision.bin_dir(tmp_path).mkdir(parents=True, exist_ok=True)
    bun_name = "bun.exe" if sys.platform == "win32" else "bun"
    (_provision.bin_dir(tmp_path) / bun_name).write_text("#!/bin/sh\n")
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(argv, env):
        calls.append((argv, env))
        return True

    monkeypatch.setattr(_provision, "_run", fake_run)
    drivers = (
        Driver("cspell", provision=Provision(kind="node", runtime="bun")),
        Driver(
            "markdownlint-cli2",
            attr="markdownlint",
            provision=Provision(kind="node", runtime="bun"),
        ),
        Driver("basedpyright", provision=Provision(kind="node")),
    )
    outcomes = _provision.provision(drivers, tmp_path)
    assert all(o.status == "ok" for o in outcomes)
    on_node, on_bun = calls
    assert on_node[0][:2] == [str(node), str(_provision.npm_cli(node))]
    assert on_node[0][2:4] == ["install", "--global"]
    assert on_node[0][-1] == "basedpyright"
    assert on_bun[0][1:3] == ["add", "--global"]
    assert on_bun[0][3:] == ["cspell", "markdownlint-cli2"]  # sorted, deduped
    assert on_bun[1]["BUN_INSTALL"] == str(tmp_path)
    assert (tmp_path / "install" / "global" / "package.json").read_text() == "{}\n"
    assert on_bun[1]["PATH"].split(os.pathsep)[0] == str(_provision.bin_dir(tmp_path))


def test_nodejs_tier_unpacks_node_whole_and_links_it_into_bin(tmp_path, monkeypatch):
    """The whole tree, since npm is a script node ships beside itself, and a
    `node` beside the launchers for the ones the node tier writes.
    """
    from livery.toolroom.bench import _toolfetch

    # The build in this platform's own layout: node.exe with npm's script
    # under node_modules on Windows, bin/node with it under lib elsewhere.
    payload = io.BytesIO()
    windows = sys.platform == "win32"
    root = "node-v24.0.0-win-x64" if windows else "node-v24.0.0-darwin-arm64"
    members = (
        (
            (f"{root}/node.exe", b"MZ"),
            (f"{root}/node_modules/npm/bin/npm-cli.js", b"// npm\n"),
        )
        if windows
        else (
            (f"{root}/bin/node", b"#!/bin/sh\necho node\n"),
            (f"{root}/lib/node_modules/npm/bin/npm-cli.js", b"// npm\n"),
        )
    )
    with tarfile.open(fileobj=payload, mode="w:gz") as tar:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(data))
    archive = tmp_path / f"{root}.tar.gz"
    archive.write_bytes(payload.getvalue())
    monkeypatch.setattr(
        _toolfetch, "releases", lambda driver: [_toolfetch.Release("24.0.0", "v24.0.0")]
    )
    monkeypatch.setattr(_provision, "_pick_asset", lambda assets, host="": assets[0])
    monkeypatch.setattr(_provision, "_download", lambda url, prefix: archive)
    (out,) = _provision.provision(
        (Driver("node", provision=Provision(kind="nodejs")),), tmp_path
    )
    assert out.status == "ok" and out.detail == "24.0.0"
    node = _provision.provisioned_node(tmp_path)
    assert node is not None
    assert (node.parent if windows else node.parent.parent).name == root
    assert _provision.npm_cli(node).is_file()
    link = _provision.bin_dir(tmp_path) / (
        "node.cmd" if sys.platform == "win32" else "node"
    )
    assert link.exists()
    # The node tier now runs on it.
    assert _provision.provisioned_node(tmp_path) == node


# --- asset selection ---------------------------------------------------------


@pytest.fixture
def mac_arm(monkeypatch):
    monkeypatch.setattr(_provision.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(_provision.platform, "machine", lambda: "arm64")


def test_pick_asset_matches_aliases_and_prefers_archive(mac_arm):
    assets = [
        ("tool_Linux_x86_64.tar.gz", "linux"),
        ("tool-darwin-aarch64", "bare"),  # aarch64 == arm64; bare binary
        ("tool_macOS_arm64.tar.gz", "archive"),  # macOS == darwin
        ("tool_macOS_arm64.tar.gz.sha256", "sidecar"),
    ]
    _name, url = _provision._pick_asset(assets)
    assert url == "archive"  # archive beats the bare binary, sidecar excluded


def test_pick_asset_refuses_a_host_it_does_not_know(mac_arm):
    with pytest.raises(_provision.ProvisionError, match="unknown host 'freebsd-x64'"):
        _provision._pick_asset([("tool_Linux_x86_64.tar.gz", "x")], host="freebsd-x64")


def test_pick_asset_for_a_named_host_reads_the_host_not_the_machine(mac_arm):
    """The assembler picks every host's asset from one machine."""
    assets = [
        ("tool_Linux_x86_64.tar.gz", "linux-x64"),
        ("tool_Linux_arm64.tar.gz", "linux-arm"),
        ("tool_Windows_arm64.zip", "windows-arm"),
        ("tool_macOS_arm64.tar.gz", "macos-arm"),
    ]
    for host, url in (("windows-arm", "windows-arm"), ("linux-x64", "linux-x64")):
        assert _provision._pick_asset(assets, host=host)[1] == url
    assert _provision._pick_asset(assets)[1] == "macos-arm"  # the machine
    with pytest.raises(
        _provision.ProvisionError, match="no release asset for macos-x64"
    ):
        _provision._pick_asset(assets, host="macos-x64")


def test_pick_asset_universal_serves_both_macos_hosts_and_no_other(mac_arm):
    assets = [
        ("cmake-4.4.3-macos-universal.tar.gz", "mac"),
        ("cmake-4.4.3-macos-universal.dmg", "dmg"),  # an installer, ranked below
        ("cmake-4.4.3-linux-x86_64.tar.gz", "linux"),
    ]
    for host in ("macos-arm", "macos-x64"):
        assert _provision._pick_asset(assets, host=host)[1] == "mac"
    with pytest.raises(
        _provision.ProvisionError, match="no release asset for linux-arm"
    ):
        _provision._pick_asset(assets, host="linux-arm")


def test_pick_asset_arch_less_is_the_x64_build_or_the_universal_one(mac_arm):
    """Ninja names no architecture on the builds that predate its arm ones."""
    assets = [
        ("ninja-linux.zip", "linux"),
        ("ninja-linux-aarch64.zip", "linux-arm"),
        ("ninja-mac.zip", "mac"),
        ("ninja-win.zip", "win"),
        ("ninja-winarm64.zip", "win-arm"),
    ]
    picks = {
        host: _provision._pick_asset(assets, host=host)[1]
        for host in _provision.HOST_TOKENS
    }
    assert picks == {
        "linux-x64": "linux",
        "linux-arm": "linux-arm",
        "macos-x64": "mac",
        "macos-arm": "mac",
        "windows-x64": "win",
        "windows-arm": "win-arm",
    }
    # An asset naming another architecture never matches through the
    # arch-less rule: an arm host with no arm build stays without one.
    only_x64 = [("tool-linux-x86_64.tar.gz", "x"), ("tool-win.zip", "w")]
    with pytest.raises(
        _provision.ProvisionError, match="no release asset for linux-arm"
    ):
        _provision._pick_asset(only_x64, host="linux-arm")
    with pytest.raises(
        _provision.ProvisionError, match="no release asset for windows-arm"
    ):
        _provision._pick_asset(only_x64, host="windows-arm")


def test_pick_asset_prefers_the_msvc_build_over_the_mingw_one(win_amd64):
    """git-cliff ships both; the shorter MinGW name must not win by length."""
    assets = [
        ("tool-1.0-x86_64-pc-windows-gnu.zip", "gnu"),
        ("tool-1.0-x86_64-pc-windows-msvc.zip", "msvc"),
    ]
    assert _provision._pick_asset(assets)[1] == "msvc"
    assert _provision._pick_asset(assets, host="windows-x64")[1] == "msvc"


def test_pick_asset_no_match_raises(mac_arm):
    with pytest.raises(_provision.ProvisionError, match="no release asset"):
        _provision._pick_asset([("tool_Windows_x86_64.zip", "u")])


@pytest.fixture
def win_amd64(monkeypatch):
    monkeypatch.setattr(_provision.platform, "system", lambda: "Windows")
    monkeypatch.setattr(_provision.platform, "machine", lambda: "AMD64")


def test_pick_asset_win_never_matches_the_tail_of_darwin(win_amd64):
    """Bun's spelling. `bun-darwin-x64.zip` contains `win` and is one
    character shorter than the Windows asset, so substring matching plus the
    shortest-name tiebreak shipped a Mach-O binary to every Windows box.
    """
    assets = [
        ("bun-darwin-x64.zip", "mac"),
        ("bun-windows-x64.zip", "win"),
        ("bun-windows-x64-baseline.zip", "variant"),
    ]
    _name, url = _provision._pick_asset(assets)
    assert url == "win"


def test_pick_asset_goreleaser_spelling_on_windows(win_amd64):
    assets = [
        ("eclint_Darwin_x86_64.tar.gz", "mac"),
        ("eclint_Linux_x86_64.tar.gz", "linux"),
        ("eclint_Windows_x86_64.tar.gz", "win"),
    ]
    _name, url = _provision._pick_asset(assets)
    assert url == "win"


# --- extraction --------------------------------------------------------------


def _run(launcher: Path) -> str:
    """What the launcher prints, run as a reader would run it."""
    import subprocess

    return subprocess.run(
        [str(launcher)], capture_output=True, text=True, check=True
    ).stdout.strip()


def test_place_binary_refuses_a_missing_tool_and_a_broken_archive(tmp_path):
    archive = tmp_path / "x.tar.gz"
    _tar_gz(archive, "something-else", b"nope")
    with pytest.raises(_provision.ProvisionError, match="not found inside"):
        _provision.place_binary(archive, "gh", tmp_path / "bin")
    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"not a zip")
    with pytest.raises(_provision.ProvisionError, match="will not extract"):
        _provision.place_binary(broken, "gh", tmp_path / "bin")


def test_place_binary_places_a_bare_download_as_it_is(tmp_path):
    bare = tmp_path / "eclint"
    bare.write_bytes(b"ELF-ish")
    placed = _provision.place_binary(bare, "eclint", tmp_path / "bin")
    assert placed == tmp_path / "bin" / _provision.exe("eclint")
    assert placed.read_bytes() == b"ELF-ish"
    if sys.platform != "win32":
        assert placed.stat().st_mode & 0o111  # +x — Windows has no exec bit


def test_place_binary_unpacks_whole_and_the_launcher_runs_the_tool_in_its_tree(
    tmp_path,
):
    """A PyInstaller directory app (conan's release) loads its runtime from
    beside the binary, so the tree stays whole and the launcher runs the
    binary where it lies.
    """
    archive = tmp_path / "app-1.0-linux.tgz"
    script = b'#!/bin/sh\nprintf \'%s\' "$(dirname "$0")"\n'
    with tarfile.open(archive, "w:gz") as tar:
        _add(tar, "app-1.0/bin/app", script)
        _add(tar, "app-1.0/bin/_internal/lib.so", b"so")
        _add(tar, "app-1.0/share/readme", b"doc")
    placed = _provision.place_binary(archive, "app", tmp_path / "bin", windows=False)
    tree = tmp_path / "trees" / "app" / "app-1.0"
    assert (tree / "bin" / "_internal" / "lib.so").read_bytes() == b"so"
    assert (tree / "share" / "readme").is_file()
    assert placed == tmp_path / "bin" / "app"
    assert f'exec "{tree / "bin" / "app"}"' in placed.read_text()
    if sys.platform != "win32":
        # The process is the binary in its tree: it sees its own directory.
        assert _run(placed) == str(tree / "bin")
    # A second placing of the same tool replaces the tree, never stacks it.
    _provision.place_binary(archive, "app", tmp_path / "bin", windows=False)
    assert sorted(p.name for p in (tmp_path / "trees").iterdir()) == ["app"]


def test_place_binary_names_a_cmd_launcher_on_windows(tmp_path):
    """`cmd` resolves a tool through PATHEXT, so the launcher is a `.cmd`
    that forwards `%*`. The platform arrives as a parameter (the
    `_bash_path` idiom): patching `os.name` takes down the xdist worker.
    """
    archive = tmp_path / "eclint_Windows_x86_64.zip"
    _zip(archive, "eclint-0.6/eclint.exe", b"PE-ish")
    placed = _provision.place_binary(archive, "eclint", tmp_path / "bin", windows=True)
    assert placed.name == "eclint.cmd"
    text = placed.read_text()
    assert "@echo off" in text and "eclint.exe" in text and "%*" in text
    assert (tmp_path / "trees" / "eclint" / "eclint-0.6" / "eclint.exe").is_file()


def test_place_binary_finds_the_file_not_a_directory_of_the_same_name(tmp_path):
    """Docker's tarball is `docker/docker`: a directory whose name matches
    the tool, listed before the binary it holds.
    """
    archive = tmp_path / "docker-27.5.1.tgz"
    with tarfile.open(archive, "w:gz") as tar:
        folder = tarfile.TarInfo("docker/")
        folder.type = tarfile.DIRTYPE
        tar.addfile(folder)
        _add(tar, "docker/docker", b"the-real-binary")
    placed = _provision.place_binary(archive, "docker", tmp_path / "bin", windows=False)
    assert (
        str(tmp_path / "trees" / "docker" / "docker" / "docker") in placed.read_text()
    )
    zipped = tmp_path / "docker.zip"
    with zipfile.ZipFile(zipped, "w") as zf:
        zf.writestr("docker/", b"")
        zf.writestr("docker/docker.exe", b"the-real-binary")
    placed = _provision.place_binary(zipped, "docker", tmp_path / "bin2", windows=True)
    assert "docker.exe" in placed.read_text()


def test_place_binary_copies_the_real_file_where_a_launcher_will_not_do(tmp_path):
    """A docker plugin directory must hold the binary itself: docker runs it."""
    archive = tmp_path / "compose.tgz"
    with tarfile.open(archive, "w:gz") as tar:
        _add(tar, "docker-compose", b"compose-binary")
    placed = _provision.place_binary(
        archive, "docker-compose", tmp_path / "plugins", windows=False, launcher=False
    )
    assert placed == tmp_path / "plugins" / "docker-compose"
    assert placed.read_bytes() == b"compose-binary"


def _add(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o755
    tar.addfile(info, io.BytesIO(data))


# --- release tier end to end -------------------------------------------------


def test_release_github_flow(tmp_path, monkeypatch, mac_arm):
    monkeypatch.setattr(
        _provision,
        "_get_json",
        lambda url: {
            "assets": [
                {
                    "name": "gh_macOS_arm64.zip",
                    "browser_download_url": "http://x/gh.zip",
                }
            ]
        },
    )

    def fake_download(url, prefix):
        archive = prefix / ".cache" / "gh.zip"
        archive.parent.mkdir(parents=True, exist_ok=True)
        _zip(archive, "gh/bin/gh", b"gh!")
        return archive

    monkeypatch.setattr(_provision, "_download", fake_download)
    driver = Driver("gh", provision=Provision(kind="github", repo="cli/cli"))
    (out,) = _provision.provision((driver,), tmp_path)
    assert out.status == "ok"
    want = "gh.cmd" if sys.platform == "win32" else "gh"
    launcher = _provision.bin_dir(tmp_path) / want
    binary = tmp_path / "trees" / "gh" / "gh" / "bin" / "gh"
    assert str(binary) in launcher.read_text()
    assert binary.read_bytes() == b"gh!"


def test_release_gitlab_parses_links(monkeypatch):
    monkeypatch.setattr(
        _provision,
        "_get_json",
        lambda url: {
            "assets": {
                "links": [{"name": "eclint_Darwin_arm64.tar.gz", "url": "http://u"}]
            }
        },
    )
    assets = _provision._latest_assets("gitlab", "willemkokke/eclint")
    assert assets == [("eclint_Darwin_arm64.tar.gz", "http://u")]


def test_release_gitea_reads_github_shaped_assets(monkeypatch):
    urls: list[str] = []

    def fake(url):
        urls.append(url)
        return {
            "assets": [
                {
                    "name": "tea-0.15.0-darwin-arm64",
                    "browser_download_url": "http://u/tea",
                }
            ]
        }

    monkeypatch.setattr(_provision, "_get_json", fake)
    assets = _provision._latest_assets("gitea", "gitea/tea")
    assert assets == [("tea-0.15.0-darwin-arm64", "http://u/tea")]
    assert urls == ["https://gitea.com/api/v1/repos/gitea/tea/releases/latest"]


def test_release_missing_repo_fails(tmp_path):
    driver = Driver("gh", provision=Provision(kind="github"))
    (out,) = _provision.provision((driver,), tmp_path)
    assert out.status == "fail" and "no repo" in out.detail


def test_latest_assets_unknown_host_raises():
    with pytest.raises(_provision.ProvisionError, match="unknown release host"):
        _provision._latest_assets("bitbucket", "a/b")


# --- the reads, over the store ------------------------------------------------


def test_get_json_and_download_go_through_the_stores_reads(tmp_path, monkeypatch):
    asked: list[object] = []

    def json_read(url, **_kw):
        asked.append(url)
        return {"tag_name": "v1"}

    monkeypatch.setattr(_provision, "fetch_json", json_read)
    assert _provision._get_json("http://x")["tag_name"] == "v1"

    def file_read(url, into, **_kw):
        asked.append((url, into))
        into.mkdir(parents=True, exist_ok=True)
        (into / "thing.tar.gz").write_bytes(b"payload")
        return into / "thing.tar.gz"

    monkeypatch.setattr(_provision, "fetch_file", file_read)
    got = _provision._download("http://x/thing.tar.gz", tmp_path)
    assert got.read_bytes() == b"payload"
    # The prefix's cache directory is where the store keeps the file.
    assert asked == ["http://x", ("http://x/thing.tar.gz", tmp_path / ".cache")]


# --- the task ----------------------------------------------------------------


def test_task_prints_table_and_export(tmp_path, monkeypatch, capsys):
    from livery.toolroom.bench import _tasks as tools

    monkeypatch.setattr(
        _provision,
        "provision",
        lambda drivers, prefix, only="": [
            _provision.Outcome("ruff", "uv", "ok", "ruff")
        ],
    )
    tools.provision(prefix=tmp_path)
    out = capsys.readouterr().out
    assert "ok" in out and "ruff" in out
    assert f'export PATH="{_provision.bin_dir(tmp_path)}:$PATH"' in out


def test_task_sync_runs_sync_against_the_prefix(tmp_path, monkeypatch):
    """`--sync` hands the prefix to `sync`, which puts its `bin/` on PATH for
    the read — the same `--prefix` any caller can pass by hand.
    """
    import os

    from livery.toolroom.bench import _tasks as tools

    monkeypatch.setattr(_provision, "provision", lambda *a, **k: [])
    seen: dict[str, str] = {}

    def fake_sync(only="", prefix=""):
        with tools._on_path(prefix):
            seen.update(only=only, path=os.environ.get("PATH", ""))

    monkeypatch.setattr(tools, "sync", fake_sync)
    tools.provision(prefix=tmp_path, sync_=True)
    assert str(_provision.bin_dir(tmp_path)) in seen["path"]


def test_pytest_provisions_with_its_cov_plugin():
    from livery.toolroom.bench import _drivers

    pytest_driver = next(d for d in _drivers.DRIVERS if d.key == "pytest")
    # The prefix install carries pytest-cov, so provision reads a pytest whose
    # --cov* flags are present — no dev-env special case, no skip.
    assert pytest_driver.provision.plugins == ("pytest-cov",)


def test_uv_tier_installs_plugins_as_with_packages(tmp_path, monkeypatch):
    from livery.toolroom.bench._drivers import Driver, Provision

    calls: list[list[str]] = []

    def fake_run(argv, env):
        calls.append(argv)
        return True

    monkeypatch.setattr(_provision, "_run", fake_run)
    drivers = (Driver("pytest", provision=Provision(plugins=("pytest-cov",))),)
    outcomes = _provision.provision(drivers, tmp_path)
    argv = calls[0]
    assert argv[:4] == ["uv", "tool", "install", "--upgrade"]
    assert "pytest" in argv and "--with=pytest-cov" in argv
    assert outcomes[0].status == "ok" and "pytest-cov" in outcomes[0].detail


def test_task_clean_removes_prefix(tmp_path, monkeypatch):
    from livery.toolroom.bench import _tasks as tools

    prefix = tmp_path / "prefix"
    prefix.mkdir()
    monkeypatch.setattr(_provision, "provision", lambda *a, **k: [])
    tools.provision(prefix=prefix, clean=True)
    assert not prefix.exists()


def test_the_interpreter_is_placed_however_the_platform_allows(tmp_path, monkeypatch):
    """Windows grants symlinks only with developer mode or elevation, and a
    copied `python.exe` is a broken interpreter — CPython finds its standard
    library relative to the real executable, so a lone copy finds nothing.
    A launcher is the one fallback that still runs.
    """
    import os

    from livery.toolroom.bench import _provision

    target = tmp_path / "real" / "python"
    target.parent.mkdir()
    target.write_text("#!/bin/sh\n")

    placed = _provision._place_interpreter(tmp_path / "bin", target)
    assert placed is not None and placed.exists()
    assert placed.resolve() == target.resolve()  # a symlink, where they work

    def refuse(*_a, **_k):
        raise OSError("a required privilege is not held by the client")

    monkeypatch.setattr(_provision.Path, "symlink_to", refuse)
    monkeypatch.setattr(os, "name", "nt")
    placed = _provision._place_interpreter(tmp_path / "win", target)
    assert placed is not None and placed.name == "python.cmd"
    assert str(target) in placed.read_text(encoding="utf-8")


# --- the default prefix -------------------------------------------------------

# With no --prefix, provisioned tools live in one durable machine-level room
# (the `toolroom` room in footman's data directory) and every empty-prefix
# reading looks there first — falling back to the host's PATH, exactly as an
# empty prefix always read, when nothing has been provisioned.


def test_default_prefix_rides_footman_data_dir(tmp_path, monkeypatch):
    from livery.toolroom.bench import _tasks

    monkeypatch.setenv("FOOTMAN_DATA_DIR", str(tmp_path / "data"))
    assert _tasks.default_prefix() == tmp_path / "data" / "toolroom-bench"


def test_empty_prefix_resolves_to_the_default_room_once_provisioned(
    tmp_path, monkeypatch
):
    from livery.toolroom.bench import _tasks

    monkeypatch.setenv("FOOTMAN_DATA_DIR", str(tmp_path / "data"))
    assert _tasks._resolve_prefix("") is None  # nothing provisioned: host PATH
    # The bench's store lives in the room too; a store is not a provisioned set.
    (tmp_path / "data" / "toolroom-bench" / "store").mkdir(parents=True)
    assert _tasks._resolve_prefix("") is None
    (tmp_path / "data" / "toolroom-bench" / "bin").mkdir()
    assert _tasks._resolve_prefix("") == tmp_path / "data" / "toolroom-bench"


def test_an_explicit_prefix_wins_over_the_default_room(tmp_path, monkeypatch):
    from livery.toolroom.bench import _tasks

    monkeypatch.setenv("FOOTMAN_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data" / "toolroom-bench" / "bin").mkdir(parents=True)
    mine = tmp_path / "mine"
    assert _tasks._resolve_prefix(str(mine)) == mine.resolve()
