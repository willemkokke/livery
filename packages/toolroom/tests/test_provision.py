"""The provisioning engine and task — `fm tools.provision`.

The tiers are driven with the real driver metadata but mocked at their one
outward edge (subprocess, HTTP), so the grouping, dedup, asset matching and
unpacking are exercised without installing anything or hitting the network.
"""

from __future__ import annotations

import io
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from livery.toolroom.tools._machinery import _provision
from livery.toolroom.tools._machinery._drivers import Driver, Provision


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


def test_a_dropped_download_is_retried(tmp_path, monkeypatch):
    """A refresh leg died on `Remote end closed connection without response`
    part-way through gh's zip. The release was there; the download was not
    finished. A 404 is an answer and is not retried — a dropped connection
    says nothing about the asset.
    """
    import email.message
    import urllib.error

    calls = []

    class Fake:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, *a):
            return b""

    def flaky(request, timeout=0):
        calls.append(1)
        if len(calls) < 3:
            raise urllib.error.URLError("Remote end closed connection")
        return Fake()

    monkeypatch.setattr(_provision.urllib.request, "urlopen", flaky)
    monkeypatch.setattr(_provision.time, "sleep", lambda _s: None)
    placed = _provision._download("http://x/gh.zip", tmp_path)
    assert placed.exists() and len(calls) == 3

    calls.clear()

    def gone(request, timeout=0):
        calls.append(1)
        raise urllib.error.HTTPError(
            "http://x/gh.zip", 404, "Not Found", email.message.Message(), None
        )

    monkeypatch.setattr(_provision.urllib.request, "urlopen", gone)
    with pytest.raises(_provision.ProvisionError, match="404"):
        _provision._download("http://x/missing.zip", tmp_path)
    assert len(calls) == 1  # an answer, not a hiccup


def test_strict_turns_a_failed_tier_into_a_failed_run(tmp_path, monkeypatch):
    """`ok` for a prefix that is missing tools is right for a person and
    wrong for a job. A refresh run where bun hit a rate limit still said
    `ok`, and the half-provisioned prefix went into the gather unremarked
    — cspell and markdownlint were skipped for want of the tool that had
    failed two steps earlier.
    """
    from livery.footman import Failed
    from livery.toolroom.tools._machinery import _tasks as tools

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


def test_node_tier_fails_without_bun(tmp_path):
    drivers = (Driver("cspell", provision=Provision(kind="node")),)
    (out,) = _provision.provision(drivers, tmp_path)
    assert out.status == "fail" and "bun" in out.detail


def test_node_tier_installs_through_bun(tmp_path, monkeypatch):
    _provision.bin_dir(tmp_path).mkdir(parents=True)
    bun_name = "bun.exe" if sys.platform == "win32" else "bun"
    (_provision.bin_dir(tmp_path) / bun_name).write_text("#!/bin/sh\n")
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(argv, env):
        calls.append((argv, env))
        return True

    monkeypatch.setattr(_provision, "_run", fake_run)
    drivers = (
        Driver("cspell", provision=Provision(kind="node")),
        Driver(
            "markdownlint-cli2", attr="markdownlint", provision=Provision(kind="node")
        ),
    )
    outcomes = _provision.provision(drivers, tmp_path)
    argv, env = calls[0]
    assert argv[1:3] == ["add", "--global"]
    assert argv[3:] == ["cspell", "markdownlint-cli2"]  # sorted, deduped
    assert env["BUN_INSTALL"] == str(tmp_path)
    assert all(o.status == "ok" for o in outcomes)


def test_node_tier_leaves_a_node_beside_the_launchers(tmp_path, monkeypatch):
    """The prefix has to be runnable by whoever puts it on PATH.

    `bun add --global` writes launchers beginning `#!/usr/bin/env node`, and a
    launcher spawned as a subprocess has its shebang resolved by the operating
    system, with bun nowhere in the chain. Everything that reads the prefix
    pays for that: `sync` on a node-less machine recorded cspell and
    markdownlint as version `unknown`, and the reading sat at the floor of the
    chain where `prime` could not walk past it.
    """
    monkeypatch.setattr(_provision.shutil, "which", lambda _: None)  # no real node
    _provision.bin_dir(tmp_path).mkdir(parents=True)
    bun_name = "bun.exe" if sys.platform == "win32" else "bun"
    bun = _provision.bin_dir(tmp_path) / bun_name
    bun.write_text("#!/bin/sh\n")
    monkeypatch.setattr(_provision, "_run", lambda argv, env: True)
    _provision.provision(
        (Driver("cspell", provision=Provision(kind="node")),), tmp_path
    )

    shim_name = "node.cmd" if sys.platform == "win32" else "node"
    shim = _provision.bin_dir(tmp_path) / shim_name
    assert shim.exists(), "a prefix with node tools must carry a node"
    assert str(bun) in shim.read_text()
    assert "--bun" in shim.read_text()


def test_no_node_shim_where_a_real_node_exists(tmp_path, monkeypatch):
    """A machine with node keeps using it — the shim is a stand-in, not a
    preference, and shadowing the real thing would change what is read.
    """
    monkeypatch.setattr(_provision.shutil, "which", lambda _: "/usr/bin/node")
    assert _provision.write_node_shim(tmp_path, Path("/somewhere/bun")) is None
    assert not (tmp_path / "node").exists()


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


def test_extract_binary_from_tar_gz(tmp_path):
    archive = tmp_path / "eclint_Darwin_arm64.tar.gz"
    _tar_gz(archive, "eclint-0.6/eclint", b"ELF-ish")
    placed = _provision._extract_binary(archive, "eclint", tmp_path / "bin")
    assert placed.read_bytes() == b"ELF-ish"
    if sys.platform != "win32":
        assert placed.stat().st_mode & 0o111  # +x — Windows has no exec bit


def test_extract_binary_from_zip(tmp_path):
    archive = tmp_path / "gh_macOS_arm64.zip"
    _zip(archive, "gh_2.0_macOS_arm64/bin/gh", b"go-binary")
    placed = _provision._extract_binary(archive, "gh", tmp_path / "bin")
    want = "gh.exe" if sys.platform == "win32" else "gh"
    assert placed.read_bytes() == b"go-binary" and placed.name == want


def test_exe_spells_a_binary_for_its_platform():
    """One spelling for every tier. Each tier that grew its own copy of the
    conditional was a separate Windows bug — the placed file gained `.exe`
    while the tier still reached for the bare name (the docker tier did
    exactly that, and provisioning died on a `docker` that was `docker.exe`).
    """
    assert _provision.exe("docker", windows=True) == "docker.exe"
    assert _provision.exe("docker", windows=False) == "docker"
    assert _provision.exe("docker") == (
        "docker.exe" if sys.platform == "win32" else "docker"
    )


def test_extract_binary_names_the_exe_on_windows(tmp_path):
    """PATHEXT makes an extensionless PE invisible to `shutil.which`, so the
    placed name carries `.exe` even when the archive member did not. The
    platform arrives as a parameter (the `_bash_path` idiom) — patching the
    global `os.name` takes down the whole xdist worker on POSIX 3.11.
    """
    archive = tmp_path / "eclint_Windows_x86_64.tar.gz"
    _tar_gz(archive, "eclint-0.6/eclint", b"PE-ish")
    placed = _provision._extract_binary(
        archive, "eclint", tmp_path / "bin", windows=True
    )
    assert placed.name == "eclint.exe" and placed.read_bytes() == b"PE-ish"


def test_extract_binary_prefers_the_file_over_a_directory_of_the_same_name(tmp_path):
    """Docker's tarball is `docker/docker` — a directory whose name matches
    the tool, listed before the binary it holds. Matching on name alone took
    the directory, and the extraction failed one line later with "not a
    file"; a zip took it too, and wrote a zero-byte binary instead.
    """
    archive = tmp_path / "docker-27.5.1.tgz"
    with tarfile.open(archive, "w:gz") as tar:
        folder = tarfile.TarInfo("docker/")
        folder.type = tarfile.DIRTYPE
        tar.addfile(folder)
        info = tarfile.TarInfo("docker/docker")
        info.size = len(b"the-real-binary")
        tar.addfile(info, io.BytesIO(b"the-real-binary"))
    placed = _provision._extract_binary(archive, "docker", tmp_path / "bin")
    assert placed.read_bytes() == b"the-real-binary"


def test_extract_binary_skips_a_zip_directory_entry(tmp_path):
    archive = tmp_path / "docker.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("docker/", b"")
        zf.writestr("docker/docker.exe", b"the-real-binary")
    placed = _provision._extract_binary(archive, "docker", tmp_path / "bin")
    assert placed.read_bytes() == b"the-real-binary"


def test_extract_binary_missing_is_an_error(tmp_path):
    archive = tmp_path / "x.tar.gz"
    _tar_gz(archive, "something-else", b"nope")
    with pytest.raises(_provision.ProvisionError, match="not found inside"):
        _provision._extract_binary(archive, "gh", tmp_path / "bin")


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
    want = "gh.exe" if sys.platform == "win32" else "gh"
    assert (_provision.bin_dir(tmp_path) / want).read_bytes() == b"gh!"


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


# --- the low-level HTTP edges (mocked urlopen) -------------------------------


def test_get_json_reads_response(monkeypatch):
    monkeypatch.setattr(
        _provision.urllib.request,
        "urlopen",
        lambda req, timeout=0: io.BytesIO(b'{"tag_name": "v1"}'),
    )
    assert _provision._get_json("http://x")["tag_name"] == "v1"


def test_get_json_error_is_provision_error(monkeypatch):
    def boom(req, timeout=0):
        raise OSError("no net")

    monkeypatch.setattr(_provision.urllib.request, "urlopen", boom)
    with pytest.raises(_provision.ProvisionError):
        _provision._get_json("http://x")


def test_download_caches_by_name(tmp_path, monkeypatch):
    hits: list[int] = []

    def fake_urlopen(req, timeout=0):
        hits.append(1)
        return io.BytesIO(b"payload")

    monkeypatch.setattr(_provision.urllib.request, "urlopen", fake_urlopen)
    first = _provision._download("http://x/thing.tar.gz", tmp_path)
    second = _provision._download("http://x/thing.tar.gz", tmp_path)
    assert first == second and first.read_bytes() == b"payload"
    assert len(hits) == 1  # second call served from cache


# --- the task ----------------------------------------------------------------


def test_task_prints_table_and_export(tmp_path, monkeypatch, capsys):
    from livery.toolroom.tools._machinery import _tasks as tools

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

    from livery.toolroom.tools._machinery import _tasks as tools

    monkeypatch.setattr(_provision, "provision", lambda *a, **k: [])
    seen: dict[str, str] = {}

    def fake_sync(only="", prefix=""):
        with tools._on_path(prefix):
            seen.update(only=only, path=os.environ.get("PATH", ""))

    monkeypatch.setattr(tools, "sync", fake_sync)
    tools.provision(prefix=tmp_path, sync_=True)
    assert str(_provision.bin_dir(tmp_path)) in seen["path"]


def test_pytest_provisions_with_its_cov_plugin():
    from livery.toolroom.tools._machinery import _drivers

    pytest_driver = next(d for d in _drivers.DRIVERS if d.key == "pytest")
    # The prefix install carries pytest-cov, so provision reads a pytest whose
    # --cov* flags are present — no dev-env special case, no skip.
    assert pytest_driver.provision.plugins == ("pytest-cov",)


def test_uv_tier_installs_plugins_as_with_packages(tmp_path, monkeypatch):
    from livery.toolroom.tools._machinery._drivers import Driver, Provision

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
    from livery.toolroom.tools._machinery import _tasks as tools

    prefix = tmp_path / "prefix"
    prefix.mkdir()
    monkeypatch.setattr(_provision, "provision", lambda *a, **k: [])
    tools.provision(prefix=prefix, clean=True)
    assert not prefix.exists()


def test_a_token_reaches_the_api_and_nothing_else(monkeypatch):
    """GitHub allows 60 unauthenticated API calls an hour *per IP* and 5,000
    with a token. Sixty is ample for two forge-hosted tools until the IP is a
    shared CI runner, where strangers spend the budget.

    Scoped to the API host deliberately: urllib carries headers across
    redirects, and a release asset redirects to a CDN that has no business
    seeing a credential.
    """
    from livery.toolroom.tools._machinery._provision import api_headers

    monkeypatch.setenv("GH_TOKEN", "s3cret")
    assert api_headers("https://api.github.com/repos/cli/cli/releases") == {
        "User-Agent": "footman-provision",
        "Authorization": "Bearer s3cret",
    }
    for elsewhere in (
        "https://github.com/oven-sh/bun/releases/download/bun-v1.3.13/bun.zip",
        "https://objects.githubusercontent.com/whatever",
        "https://gitlab.com/api/v4/projects/x/releases",
        "https://pypi.org/pypi/ruff/json",
        "https://registry.npmjs.org/cspell",
    ):
        assert "Authorization" not in api_headers(elsewhere), elsewhere


def test_the_older_github_token_spelling_is_accepted(monkeypatch):
    """Actions exports `GITHUB_TOKEN`; `gh` exports `GH_TOKEN`. Both, so the
    workflow and a laptop need not disagree.
    """
    from livery.toolroom.tools._machinery._provision import api_headers

    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "from-actions")
    url = "https://api.github.com/rate_limit"
    assert api_headers(url)["Authorization"] == "Bearer from-actions"


def test_no_token_still_works_just_on_the_smaller_budget(monkeypatch):
    """A token is an offer, never a requirement — a fresh clone with no
    credentials still primes, against 60 calls an hour.
    """
    from livery.toolroom.tools._machinery._provision import api_headers

    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert api_headers("https://api.github.com/rate_limit") == {
        "User-Agent": "footman-provision"
    }


def test_the_interpreter_is_placed_however_the_platform_allows(tmp_path, monkeypatch):
    """Windows grants symlinks only with developer mode or elevation, and a
    copied `python.exe` is a broken interpreter — CPython finds its standard
    library relative to the real executable, so a lone copy finds nothing.
    A launcher is the one fallback that still runs.
    """
    import os

    from livery.toolroom.tools._machinery import _provision

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
    from livery.toolroom.tools._machinery import _tasks

    monkeypatch.setenv("FOOTMAN_DATA_DIR", str(tmp_path / "data"))
    assert _tasks.default_prefix() == tmp_path / "data" / "toolroom"


def test_empty_prefix_resolves_to_the_default_room_once_provisioned(
    tmp_path, monkeypatch
):
    from livery.toolroom.tools._machinery import _tasks

    monkeypatch.setenv("FOOTMAN_DATA_DIR", str(tmp_path / "data"))
    assert _tasks._resolve_prefix("") is None  # nothing provisioned: host PATH
    (tmp_path / "data" / "toolroom").mkdir(parents=True)
    assert _tasks._resolve_prefix("") == tmp_path / "data" / "toolroom"


def test_an_explicit_prefix_wins_over_the_default_room(tmp_path, monkeypatch):
    from livery.toolroom.tools._machinery import _tasks

    monkeypatch.setenv("FOOTMAN_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data" / "toolroom").mkdir(parents=True)
    mine = tmp_path / "mine"
    assert _tasks._resolve_prefix(str(mine)) == mine.resolve()
