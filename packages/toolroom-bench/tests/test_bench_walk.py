"""The walks and the tiers: listing releases, installing one, and what a run reports."""

from __future__ import annotations

import collections
from typing import Any

import pytest

from livery.toolroom.store import Option, ToolSpec, Verb
from toolroom_bench_readings import (
    chain_of,
    gathered,
    isolate,
    spec_of,
    with_options,
)


def test_releases_break_a_same_day_tie_by_version(monkeypatch):
    """Two releases on one day are common — prek shipped 0.4.7 and 0.4.8
    together. Resolved by dict order the walk skips one and a later prime
    appends it *below* its own successor, which corrupts the chain. Read
    through a PyPI-tier driver, since the index shape is PyPI's.
    """
    import json as _json

    from livery.toolroom.bench import _drivers, _toolfetch

    index = {
        "releases": {
            "0.4.7": [{"upload_time": "2026-07-04T10:00:00"}],
            "0.4.8": [{"upload_time": "2026-07-04T18:00:00"}],
            "0.4.9": [{"upload_time": "2026-07-11T09:00:00"}],
            "0.4.6": [],  # no files: not installable, so not a release to read
        }
    }
    monkeypatch.setattr(
        _toolfetch, "fetch_bytes", lambda *a, **k: _json.dumps(index).encode()
    )
    driver = _drivers.find("mypy")  # a PyPI-tier driver
    assert driver is not None
    assert [r.version for r in _toolfetch.releases(driver)] == [
        "0.4.9",
        "0.4.8",
        "0.4.7",
    ]


def test_only_listable_tiers_are_primed():
    """A tool footman cannot enumerate is named and skipped, never treated as
    a tool with no history.
    """
    from livery.toolroom.bench import _drivers, _toolfetch

    uv_tier = _drivers.find("mypy")
    manual = _drivers.find("bash")
    assert uv_tier and manual
    assert _toolfetch.can_list(uv_tier)
    assert not _toolfetch.can_list(manual)  # hand-written stub, nothing to read
    parked = _drivers.Driver("nope", provision=_drivers.Provision(kind="deferred"))
    assert not _toolfetch.can_list(parked)


def test_release_is_read_in_the_era_it_shipped_in():
    """Each release gets the newest CPython that existed when it shipped.

    Not one fixed interpreter: reading everything on the oldest supported
    Python would drop argparse aliases from releases that really do show
    them, because 3.13 taught argparse to print every alias. Reading a
    release in its own era records what it actually printed, and the
    surface corrects itself as the walk crosses October 2024.
    """
    from livery.toolroom.bench._toolfetch import (
        PYTHON_RELEASES,
        READ_PYTHON,
        read_python,
    )

    assert read_python(date="2026-06-19") == "3.14"  # pytest 9.1.1, aliases shown
    assert read_python(date="2025-01-01") == "3.13"  # after 3.13, before 3.14
    assert read_python(date="2024-05-16") == "3.12"  # twine 5.1.0
    assert read_python(date="2022-12-01") == "3.11"  # twine 4.0.2
    assert read_python(date="2022-06-01") == "3.10"  # twine 4.0.1
    # A boundary belongs to the version that shipped that day, not before it.
    for version, since in PYTHON_RELEASES:
        assert read_python(date=since) == version
    # No date is no era: the newest, which is what an unstamped release meant.
    assert read_python() == PYTHON_RELEASES[0][0]
    # Older than every era — filtered by the horizon, but never below the floor.
    assert read_python(date="2001-01-01") == READ_PYTHON
    # A tool asking for more than its era offered is read on what it will run on.
    assert read_python(">=3.13", "2022-06-01") == "3.13"
    assert read_python(">=3.8", "2022-06-01") == "3.10"  # below the era: the era


def test_cutoff_takes_the_far_edge_of_the_publishing_window(monkeypatch):
    """Publishing is a window, and the cutoff belongs at the end of it.

    ninja 1.11.1 uploaded its seventeen files over 76 days; cmake 4.3.1 took
    three. A cutoff at the *first* upload filters out the release's own later
    files, and uv then reports "no version of cmake==4.3.1" while being asked
    for exactly that — or assembles a partial set whose `--help` segfaults.
    Both holes in a 709-release walk were this, on every platform at once,
    which is what makes it look like a platform difference.
    """
    import json as _json

    from livery.toolroom.bench import _drivers, _toolfetch

    index = {
        "releases": {
            # ninja 1.11.1's real shape: first file 2022-11-05, last 2023-01-20
            "1.11.1": [
                {"upload_time": "2022-11-06T12:00:00", "requires_python": ""},
                {"upload_time": "2023-01-20T09:00:00", "requires_python": ""},
                {"upload_time": "2022-11-05T18:00:00", "requires_python": ""},
            ],
        }
    }
    monkeypatch.setattr(
        _toolfetch, "fetch_bytes", lambda *a, **k: _json.dumps(index).encode()
    )
    driver = _drivers.find("mypy")  # a PyPI-tier driver: the index shape is PyPI's
    assert driver is not None
    (release,) = _toolfetch.releases(driver)
    # Both edges taken across the files, not off whichever the index listed
    # first — here that is neither the earliest nor the latest.
    assert release.date == "2022-11-05"  # when it started publishing
    assert release.published == "2023-01-20"  # when it finished


def test_release_date_cutoff_is_spelled_in_utc(monkeypatch, tmp_path):
    """The cutoff has to say UTC, or it excludes the release it is pinning.

    The index reports `upload_time` in UTC; a bare date reaches uv as local
    midnight. East of UTC that lands before the UTC day ends, so a release
    published in its last hour is filtered out by its own release date — uv
    0.11.32 went up at 23:05Z against a 23:00Z cutoff in BST, and resolved
    to "no version of uv==0.11.32". On a UTC CI runner it would have passed.
    """
    from livery.toolroom.bench import _drivers, _toolfetch

    calls: list[list[str]] = []

    def fake_run(argv, env=None):
        calls.append(argv)
        return True

    monkeypatch.setattr(_toolfetch, "_run", fake_run)
    driver = _drivers.find("mypy")  # a PyPI-tier driver
    assert driver is not None
    release = _toolfetch.Release(version="0.4.11", date="2026-07-23")
    _toolfetch._install_pypi(driver, release, tmp_path / "mypy")
    install = next(c for c in calls if "pip" in c)
    assert "--exclude-newer" in install
    assert install[install.index("--exclude-newer") + 1] == "2026-07-23T23:59:59Z"


def test_releases_older_than_the_interpreter_are_not_offered(monkeypatch):
    """A release older than `READ_PYTHON` is out of scope, not a hole.

    Nothing was built for an interpreter before it existed, so such a
    release has no period wheels to resolve against and cannot be read on
    any interpreter this walk uses. A hole says "this could not be read",
    which would be a shrug recorded about a release nobody needs.
    """
    import json as _json

    from livery.toolroom.bench import _drivers, _toolfetch

    index = {
        "releases": {
            # the day READ_PYTHON shipped, and one day either side of it
            "2.0.0": [{"upload_time": "2021-10-05T00:00:00", "requires_python": ""}],
            "1.9.0": [{"upload_time": "2021-10-04T00:00:00", "requires_python": ""}],
            "1.8.0": [{"upload_time": "2021-10-03T00:00:00", "requires_python": ""}],
        }
    }
    monkeypatch.setattr(
        _toolfetch, "fetch_bytes", lambda *a, **k: _json.dumps(index).encode()
    )
    driver = _drivers.find("mypy")  # a PyPI-tier driver
    assert driver is not None
    assert [r.version for r in _toolfetch.releases(driver)] == ["2.0.0", "1.9.0"]


def test_walk_caches_nothing_it_will_not_reread(tmp_path):
    """A walk must not leave behind what it unpacked from.

    `_discard` deletes each release once its surface is read, but uv had
    already unpacked that release into its cache, where nothing collects it
    until the run ends: a full gather put 5 GB into `archive-v0` while the
    interpreter store sat at 110 MB. Peak disk has to scale with how many
    installs run at once, not with how many the walk performs — the walk is
    parallel on purpose, so throttling it to save disk pays for the space
    with wall-clock instead. The cache is inside the scratch directory the
    walk deletes at the end, so nothing ever reads it twice.
    """
    import os

    from livery.toolroom.bench._tasks import _sandboxed

    with _sandboxed(tmp_path):
        assert os.environ["UV_NO_CACHE"] == "1"
        assert os.environ["UV_CACHE_DIR"] == str(tmp_path / "cache")
        assert os.environ["UV_PYTHON_INSTALL_DIR"] == str(tmp_path / "pythons")
    assert "UV_NO_CACHE" not in os.environ  # restored, like the other two


def _index(monkeypatch, payload):
    """Serve *payload* as the registry's JSON, whatever URL is asked for."""
    import json as _json

    from livery.toolroom.bench import _toolfetch

    monkeypatch.setattr(
        _toolfetch, "fetch_bytes", lambda *a, **k: _json.dumps(payload).encode()
    )


def test_npm_releases_come_from_the_time_map(monkeypatch):
    """Npm keeps publication dates in `time`, alongside two entries that are
    not versions at all.
    """
    from livery.toolroom.bench import _drivers, _toolfetch

    _index(
        monkeypatch,
        {
            "versions": {"9.0.0": {}, "10.0.0": {}, "10.0.1": {}},
            "time": {
                "created": "2020-01-01T00:00:00Z",
                "modified": "2026-05-31T00:00:00Z",
                "9.0.0": "2026-01-05T00:00:00Z",
                "10.0.0": "2026-05-30T00:00:00Z",
                "10.0.1": "2026-05-31T00:00:00Z",
                "10.0.2": "2026-06-01T00:00:00Z",  # in `time`, not in `versions`
            },
        },
    )
    driver = _drivers.find("cspell")
    assert driver is not None
    got = _toolfetch.releases(driver)
    assert [r.version for r in got] == ["10.0.1", "10.0.0", "9.0.0"]
    assert got[0].date == "2026-05-31"


def test_github_releases_normalise_the_tag_and_drop_the_unreleased(monkeypatch):
    """A tag is `v2.96.0` on one project and `2.96.0` on the next, while the
    binary reports the bare number — and the history keys on what the binary
    says, or a primed release never matches the base it belongs under.
    """
    from livery.toolroom.bench import _drivers, _toolfetch

    _index(
        monkeypatch,
        [
            {"tag_name": "v2.96.0", "published_at": "2026-07-02T00:00:00Z"},
            {"tag_name": "v2.95.0", "published_at": "2026-06-01T00:00:00Z"},
            {
                "tag_name": "v3.0.0-rc1",
                "published_at": "2026-07-20T00:00:00Z",
                "prerelease": True,
            },
            {
                "tag_name": "v2.97.0",
                "published_at": "2026-07-10T00:00:00Z",
                "draft": True,
            },
        ],
    )
    driver = _drivers.find("gh")
    assert driver is not None
    assert [r.version for r in _toolfetch.releases(driver)] == ["2.96.0", "2.95.0"]


def _dirlisting(monkeypatch, html):
    """Serve *html* as a directory index, whatever URL is asked for.

    The engine listing that dates those files is stubbed empty, so a test
    about the directory is about the directory; a test about dates says so.
    """
    from livery.toolroom.bench import _toolfetch

    monkeypatch.setattr(_toolfetch, "fetch_bytes", lambda *a, **k: html.encode())
    monkeypatch.setattr(_toolfetch, "_docker_dates", dict)


DOCKER_LISTING = """<html><body><pre>
<a href="../">../</a>
<a href="sbx/">sbx/</a>
<a href="docker-17.03.0-ce.tgz">ce</a>            2025-08-06 10:05  44MB
<a href="docker-rootless-extras-27.5.1.tgz">rl</a>  2025-01-22 09:00  20MB
<a href="docker-27.5.1.tgz">docker-27.5.1</a>      2025-01-22 09:00  44MB
<a href="docker-29.4.2.tgz">docker-29.4.2</a>      2026-06-01 10:05  46MB
<a href="docker-29.4.2-2.tgz">rebuild</a>         2026-06-03 10:05  46MB
<a href="docker-29.6.2.tgz">docker-29.6.2</a>      2026-07-16 12:00  46MB
</pre></body></html>"""


def test_docker_reads_its_versions_from_a_directory_listing(monkeypatch):
    """Docker publishes a static build of every release per platform, so the
    index is a folder rather than an asset list. Three neighbours sit in the
    same folder and none of them is a docker release: the rootless extras,
    the 2017 `-ce` spelling, and a `-2` rebuild of a version already there.
    """
    from livery.toolroom.bench import _drivers, _toolfetch

    _dirlisting(monkeypatch, DOCKER_LISTING)
    driver = _drivers.find("docker")
    assert driver is not None
    got = _toolfetch.releases(driver)
    assert [r.version for r in got] == ["29.6.2", "29.4.2", "27.5.1"]
    assert got[0].date == "2026-07-16"


def test_docker_index_is_chosen_by_platform_and_architecture(monkeypatch):
    """One index per (os, arch) pair, and Windows publishes no arm64 build —
    an arm Windows box takes the x86_64 zip and runs it in emulation.
    """
    import platform as _platform_mod

    from livery.toolroom.bench import _toolfetch

    def channel(platform, machine, windows):
        monkeypatch.setattr(_toolfetch.sys, "platform", platform)
        monkeypatch.setattr(_toolfetch, "_windows", lambda: windows)
        monkeypatch.setattr(_platform_mod, "machine", lambda: machine)
        return _toolfetch._docker_channel()

    assert channel("darwin", "arm64", False) == ("mac", "aarch64", "tgz")
    assert channel("linux", "x86_64", False) == ("linux", "x86_64", "tgz")
    assert channel("win32", "ARM64", True) == ("win", "x86_64", "zip")


def test_docker_is_fetched_rather_than_read_from_the_host():
    """It used to be a `system` tool, read from whatever the laptop had
    installed — so its history could only ever hold one version.
    """
    from livery.toolroom.bench import _drivers, _toolfetch

    driver = _drivers.find("docker")
    assert driver is not None
    assert driver.provision.kind == "docker"
    assert _toolfetch.can_list(driver)


COMPOSE_RELEASES = [
    {"tag_name": "v5.3.1", "published_at": "2026-07-07T00:00:00Z"},
    {"tag_name": "v2.32.4", "published_at": "2025-01-15T00:00:00Z"},
    {"tag_name": "v2.0.0", "published_at": "2021-09-28T00:00:00Z"},
    {"tag_name": "1.29.2", "published_at": "2021-05-10T00:00:00Z"},
]


def _plugin_fetch(monkeypatch, placed):
    """Serve the compose listing, and record what was asked for."""
    from livery.toolroom.bench import _provision, _toolfetch

    _index(monkeypatch, COMPOSE_RELEASES)
    monkeypatch.setattr(_toolfetch, "_LISTINGS", {})
    asked = []

    def assets_for(_host, _repo, tag=""):
        asked.append(tag)
        if not placed:
            raise _provision.ProvisionError("rate limited")
        return [("docker-compose-linux-x86_64", "http://x/bin")]

    monkeypatch.setattr(_provision, "assets_for", assets_for)
    monkeypatch.setattr(_provision, "_pick_asset", lambda a: a[0])
    monkeypatch.setattr(
        _provision, "_download", lambda url, into: _written(into / "docker-compose")
    )
    monkeypatch.setattr(
        _provision, "place_binary", lambda archive, tool, into, **_kw: into / tool
    )
    return asked


def _written(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"plugin")
    return path


def test_a_plugin_is_paired_with_the_release_that_shipped_alongside(
    monkeypatch, tmp_path
):
    """Compose has its own release line, so there is no version to match on
    — but "what a user of that docker would have had" is a fact the two
    dates settle between them, the same answer every time.
    """
    from livery.toolroom.bench import _drivers, _toolfetch

    asked = _plugin_fetch(monkeypatch, placed=True)
    docker = _drivers.find("docker")
    assert docker is not None
    compose = docker.plugins[0]
    assert _toolfetch.install_plugin(compose, "2025-06-01", tmp_path) is True
    assert asked == ["v2.32.4"]  # not v5.3.1, which had not shipped yet


def test_an_era_before_the_plugin_existed_pairs_with_nothing(monkeypatch, tmp_path):
    """Compose 1.x was a program you ran as `docker-compose`; `docker
    compose` did not exist until 2.0, and dropping a 1.x binary into the
    plugin directory would not make it one.
    """
    from livery.toolroom.bench import _drivers, _toolfetch

    asked = _plugin_fetch(monkeypatch, placed=True)
    docker = _drivers.find("docker")
    assert docker is not None
    assert _toolfetch.install_plugin(docker.plugins[0], "2021-06-01", tmp_path) is False
    assert asked == []


def test_a_plugin_that_cannot_be_fetched_is_unreachable_not_absent(
    monkeypatch, tmp_path
):
    """A rate limit read past becomes "this docker had no compose" — a
    different claim, and one the history would write down as a removal.
    """
    from livery.toolroom.bench import _drivers, _toolfetch

    _plugin_fetch(monkeypatch, placed=False)
    docker = _drivers.find("docker")
    assert docker is not None
    with pytest.raises(_toolfetch.Unreachable, match=r"docker/compose 2\.32\.4"):
        _toolfetch.install_plugin(docker.plugins[0], "2025-06-01", tmp_path)


def test_an_index_the_store_cannot_read_is_unreachable_naming_it(monkeypatch):
    """The retry lives in the store's read. What the walk adds is the
    shape: an index that will not answer ends the run as `Unreachable`,
    never as an empty listing.
    """
    from livery.toolroom.bench import _toolfetch
    from livery.toolroom.store import FetchError

    def spent(url, **_kw):
        raise FetchError(f"{url}: HTTP 504", status=504)

    monkeypatch.setattr(_toolfetch, "fetch_bytes", spent)
    with pytest.raises(_toolfetch.Unreachable, match=r"cannot read http://x: .*504"):
        _toolfetch._read_index("http://x")


def test_a_listing_is_read_once_per_process(monkeypatch):
    """Every release of a walk asks the same question of the same
    repository, and the answer cannot change while the walk runs.
    """
    from livery.toolroom.bench import _toolfetch

    calls: list[tuple[object, ...]] = []
    _index(monkeypatch, COMPOSE_RELEASES)
    monkeypatch.setattr(_toolfetch, "_LISTINGS", {})
    real = _toolfetch._forge

    def fake_forge(*a, **k):
        calls.append(a)
        return real(*a, **k)

    monkeypatch.setattr(_toolfetch, "_forge", fake_forge)
    first = _toolfetch._listing("docker/compose", 3)
    again = _toolfetch._listing("docker/compose", 3)
    assert first == again and len(calls) == 1


def test_docker_dates_come_from_the_engine_not_the_upload(monkeypatch):
    """The static index dates its files by upload time, and docker
    re-uploads in bulk: a third of the archives are stamped one day in 2025,
    including 20.10.6, which shipped in April 2021. Those dates decide which
    compose a release is paired with.
    """
    from livery.toolroom.bench import _drivers, _toolfetch

    _dirlisting(monkeypatch, DOCKER_LISTING)
    monkeypatch.setattr(
        _toolfetch,
        "_docker_dates",
        lambda: {"27.5.1": "2025-01-22", "29.6.2": "2026-07-16"},
    )
    driver = _drivers.find("docker")
    assert driver is not None
    dated = {r.version: r.date for r in _toolfetch.releases(driver)}
    assert dated["27.5.1"] == "2025-01-22"
    assert dated["29.4.2"] == "2026-06-01"  # not in the engine listing: the mtime


def test_man_index_reads_a_dated_and_an_undated_listing(monkeypatch):
    """One reader serves both manual publishers, and both date their rows —
    kernel.org on the row itself, OpenSSH in the next cell a line below, which
    is why its pattern crosses newlines where kernel.org's need not.
    """
    from livery.toolroom.bench import _toolfetch
    from livery.toolroom.bench._drivers import Driver, Manual, Provision

    kernel = (
        '<a href="git-manpages-2.50.0.tar.gz">x</a> 16-Jun-2025 16:31\n'
        '<a href="git-manpages-2.49.0.tar.gz">x</a> 14-Mar-2025 18:34\n'
    )
    monkeypatch.setattr(_toolfetch, "_read_index", lambda *_a: kernel.encode())
    git = Driver(
        "git",
        provision=Provision(
            kind="man",
            manual=Manual(
                index="https://k.org/",
                archive="git-manpages-{version}.tar.gz",
                listing=(
                    r'href="git-manpages-(?P<version>\d+(?:\.\d+)+)\.tar\.gz"'
                    r".*?(?P<day>\d{2})-(?P<month>[A-Z][a-z]{2})-(?P<year>\d{4})"
                ),
            ),
        ),
    )
    got = _toolfetch.releases(git)
    assert [(r.version, r.date) for r in got] == [
        ("2.50.0", "2025-06-16"),
        ("2.49.0", "2025-03-14"),
    ]

    # The real shape: the date lives in the row's *next* cell, on its own
    # line, and the `.asc` beside each tarball must not read as a release.
    openssh = (
        '<tr><td><a href="openssh-9.9p1.tar.gz">openssh-9.9p1.tar.gz</a></td>\n'
        '    <td data-o="1">19-Sep-2024 01:17</td><td>1.9M</td></tr>\n'
        '<tr><td><a href="openssh-9.9p1.tar.gz.asc">x</a></td>\n'
        '    <td data-o="1">19-Sep-2024 01:17</td><td>833B</td></tr>\n'
        '<tr><td><a href="openssh-10.0p1.tar.gz">openssh-10.0p1.tar.gz</a></td>\n'
        '    <td data-o="1">09-Apr-2025 07:00</td><td>1.9M</td></tr>\n'
        '<tr><td><a href="openssh-9.9p2.tar.gz">openssh-9.9p2.tar.gz</a></td>\n'
        '    <td data-o="1">18-Feb-2025 01:17</td><td>1.9M</td></tr>\n'
    )
    monkeypatch.setattr(_toolfetch, "_read_index", lambda *_a: openssh.encode())
    ssh = Driver(
        "ssh",
        provision=Provision(
            kind="man",
            manual=Manual(
                index="https://o.org/",
                archive="openssh-{version}.tar.gz",
                listing=(
                    r'href="openssh-(?P<version>\d+\.\d+p\d+)\.tar\.gz"'
                    r"[\s\S]*?(?P<day>\d{2})-(?P<month>[A-Z][a-z]{2})-(?P<year>\d{4})"
                ),
            ),
        ),
    )
    got = _toolfetch.releases(ssh)
    # `version_tuple` reads 9.9p1 and 9.9p2 as the same base; the portable
    # patchlevel breaks the tie, and each release carries its own date.
    assert [(r.version, r.date) for r in got] == [
        ("10.0p1", "2025-04-09"),
        ("9.9p2", "2025-02-18"),
        ("9.9p1", "2024-09-19"),
    ]


def test_install_man_pulls_named_pages_from_a_source_tarball(tmp_path, monkeypatch):
    """OpenSSH's release tarball carries its pages beside the sources: only
    the named pages land, by basename, and nothing else escapes.
    """
    import io
    import tarfile

    from livery.toolroom.bench import _provision, _toolfetch
    from livery.toolroom.bench._drivers import Driver, Manual, Provision

    archive = tmp_path / "openssh-9.9p2.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for member, payload in [
            ("openssh-9.9p2/ssh.1", b".Dd ssh page"),
            ("openssh-9.9p2/ssh-keygen.1", b".Dd keygen page"),
            ("openssh-9.9p2/configure", b"#!/bin/sh"),
        ]:
            info = tarfile.TarInfo(member)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    monkeypatch.setattr(_provision, "_download", lambda _url, _into, **_kw: archive)
    driver = Driver(
        "ssh",
        provision=Provision(
            kind="man",
            manual=Manual(
                index="https://o.org/",
                archive="openssh-{version}.tar.gz",
                listing=r'href="openssh-(?P<version>\d+\.\d+p\d+)\.tar\.gz"',
                pages=("ssh.1", "ssh-keygen.1"),
            ),
        ),
    )
    release = _toolfetch.Release(version="9.9p2", date="")
    placed = _toolfetch.install(driver, release, tmp_path / "into")
    assert placed is not None
    assert sorted(p.name for p in placed.glob("man1/*")) == ["ssh-keygen.1", "ssh.1"]
    assert (placed / "man1" / "ssh.1").read_bytes() == b".Dd ssh page"


def test_man_tier_merges_every_tools_pages(tmp_path, monkeypatch):
    """The man tier holds more than one tool's pages: a second driver merges
    into the shared tree rather than replacing the first's.
    """
    from livery.toolroom.bench import _provision, _toolfetch
    from livery.toolroom.bench._drivers import Driver, Manual, Provision

    manual = Manual(index="https://x/", archive="{version}.tar.gz", listing="x")
    drivers = [
        Driver("git", provision=Provision(kind="man", manual=manual)),
        Driver("ssh", provision=Provision(kind="man", manual=manual)),
    ]
    release = _toolfetch.Release(version="1.0", date="")
    monkeypatch.setattr(_toolfetch, "releases", lambda _d: [release])

    def fake_install(driver, _release, into):
        tree = into / "man"
        (tree / "man1").mkdir(parents=True)
        (tree / "man1" / f"{driver.name}.1").write_text("page")
        return tree

    monkeypatch.setattr(_toolfetch, "install", fake_install)
    prefix = tmp_path / "prefix"
    outcomes = _provision._man_tier(prefix, drivers)
    assert [(o.status, o.detail) for o in outcomes] == [("ok", "1.0"), ("ok", "1.0")]
    assert sorted(p.name for p in (prefix / "man" / "man1").glob("*.1")) == [
        "git.1",
        "ssh.1",
    ]


def test_gitlab_releases_read_their_own_field_names(monkeypatch):
    from livery.toolroom.bench import _drivers, _toolfetch

    _index(
        monkeypatch,
        [
            {"tag_name": "v0.6.0-wk.5", "released_at": "2026-07-07T00:00:00Z"},
            {"tag_name": "v0.6.0-wk.4", "released_at": "2026-06-07T00:00:00Z"},
        ],
    )
    driver = _drivers.find("eclint")
    assert driver is not None
    got = _toolfetch.releases(driver)
    assert [r.version for r in got] == ["0.6.0-wk.5", "0.6.0-wk.4"]


def test_gitea_releases_read_the_github_shape(monkeypatch):
    """Gitea's API answers with GitHub's field names — one reading serves
    both hosts, and the prerelease/draft filter applies the same way.
    """
    from livery.toolroom.bench import _toolfetch
    from livery.toolroom.bench._drivers import Driver, Provision

    _index(
        monkeypatch,
        [
            {
                "tag_name": "v0.16.0-rc1",
                "published_at": "2026-07-28T00:00:00Z",
                "prerelease": True,
            },
            {"tag_name": "v0.15.0", "published_at": "2026-07-27T00:00:00Z"},
            {"tag_name": "v0.14.2", "published_at": "2026-06-26T00:00:00Z"},
        ],
    )
    driver = Driver("x", provision=Provision(kind="gitea", repo="o/r"))
    got = _toolfetch.releases(driver)
    assert [r.version for r in got] == ["0.15.0", "0.14.2"]
    assert got[0].tag == "v0.15.0" and got[0].date == "2026-07-27"


def test_observe_carries_every_release_field_through(tmp_path, monkeypatch):
    """The walk funnels a release through the observe task's parameters, and
    a field the funnel drops silently reverts its fix inside the walk: the
    publishing-window cutoff never reached _install_pypi through here, so
    cmake 4.3.1 kept resolving at the near edge and holing — while the
    identical install ran clean by hand.
    """
    from livery.toolroom.bench import _tasks as tools_tasks

    seen: list[Any] = []

    def fake_install(driver, release, into):
        seen.append(release)
        return None  # stop before extraction — the release is the assertion

    monkeypatch.setattr("livery.toolroom.bench._toolfetch.install", fake_install)
    monkeypatch.setattr(tools_tasks, "_refuse_a_broken_environment", lambda p: None)
    tools_tasks.observe(
        "cmake",
        "4.3.1",
        date="2026-03-28",
        published="2026-03-31",
        requires_python=">=3.8",
        scratch=str(tmp_path),
    )
    assert seen[0].published == "2026-03-31"
    assert seen[0].requires_python == ">=3.8"
    assert seen[0].date == "2026-03-28"


def test_a_provision_floor_takes_releases_out_of_scope(monkeypatch):
    """Below the floor is not *offered* — not walked, never holes. Unlike
    `deferred` the tool stays curated; its history just starts at the
    floor. tea's sits at 0.15.0, above the console-hang band.
    """
    from livery.toolroom.bench import _drivers, _toolfetch

    _index(
        monkeypatch,
        [
            {"tag_name": "v0.15.0", "published_at": "2026-07-27T00:00:00Z"},
            {"tag_name": "v0.14.2", "published_at": "2026-06-26T00:00:00Z"},
            {"tag_name": "v0.9.0", "published_at": "2024-01-01T00:00:00Z"},
        ],
    )
    driver = _drivers.find("tea")
    assert driver is not None
    assert driver.provision.floor == "0.15.0"
    got = _toolfetch.releases(driver)
    assert [r.version for r in got] == ["0.15.0"]


def test_an_unreadable_index_is_not_an_empty_one(monkeypatch):
    """The distinction the release gate rests on.

    "Is there anything new" is answered "no" by a throttled registry exactly
    as it is by a tool that has genuinely not moved — and one of those means
    stop, while the other means nobody looked. Sharing the empty list would
    let a rate limit read as "nothing to release".

    A prime still skips such a tool rather than failing the run, but it has
    to *choose* to, which is the point of raising.
    """
    from livery.toolroom.bench import _drivers, _toolfetch
    from livery.toolroom.store import FetchError

    def boom(*a, **k):
        raise FetchError("no network")

    monkeypatch.setattr(_toolfetch, "fetch_bytes", boom)
    driver = _drivers.find("mypy")  # a PyPI-tier driver
    assert driver is not None
    with pytest.raises(_toolfetch.Unreachable):
        _toolfetch.releases(driver)


def test_which_tiers_can_be_listed():
    """Every tier that can name its past releases. A hand-written stub has
    nothing to read at all, and a deferred tool is parked on purpose.
    """
    from livery.toolroom.bench import _drivers, _toolfetch

    expected = {
        "mypy": True,  # uv
        "prek": True,  # github
        "cspell": True,  # node
        "gh": True,  # github
        "eclint": True,  # gitlab
        "tea": True,  # gitea
        "bun": True,  # bun's own releases
        "node": True,  # nodejs.org's release index
        "python": True,  # uv carries CPython's own download index
        "docker": True,  # its own static-build index
        "git": True,  # kernel.org's per-release manuals
        "bash": False,  # manual stub
    }
    for key, listable in expected.items():
        driver = _drivers.find(key)
        assert driver is not None, key
        assert _toolfetch.can_list(driver) is listable, key
        if not listable:
            assert _toolfetch.releases(driver) == [], key


def test_installing_an_unlistable_tier_declines(tmp_path):
    from livery.toolroom.bench import _drivers, _toolfetch

    # No curated tool sits in a tier that cannot be fetched any more — git
    # was the last, and it is read from kernel.org's manuals now (the
    # `system` tier it sat in is deleted). The rule still holds, so it is
    # stated against a driver rather than a tool.
    driver = _drivers.Driver("nope", provision=_drivers.Provision(kind="deferred"))
    release = _toolfetch.Release("2.50.0")
    assert _toolfetch.install(driver, release, tmp_path / "nope") is None


def test_the_npm_tier_needs_its_runtime_and_says_so(tmp_path, monkeypatch):
    """A node-tier package installs through its runtime and its launcher runs
    on node, so priming needs the runtime on PATH. Without it the walk stops
    and reports why, because a scheduled job reading '+0' cannot tell that
    from 'nothing left to read'.
    """
    import shutil

    from livery.toolroom.bench import _drivers, _toolfetch

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    driver = _drivers.find("cspell")
    assert driver is not None
    release = _toolfetch.Release("10.0.0")
    assert _toolfetch.install(driver, release, tmp_path / "cspell") is None
    basedpyright = _drivers.find("basedpyright")
    assert basedpyright is not None
    assert _toolfetch.install(basedpyright, release, tmp_path / "bp") is None

    # ...and the walk says so rather than walking into holes. Without this
    # the docstring above was a claim the test never checked: a macOS
    # gather with no runtime reported 23 releases across cspell and
    # markdownlint as holes, "these could not be had", when every one of
    # them reads fine the moment the runtime is on PATH. Node is named
    # first, since every launcher runs on it; bun only once node is there.
    from livery.toolroom.bench import _toolfetch as fetch
    from livery.toolroom.bench._tasks import _curated

    chosen, skipped = _curated("", fetch)
    assert "cspell (no node to install with)" in skipped
    assert "basedpyright (no node to install with)" in skipped
    assert not any(d.provision.kind == "node" for d in chosen)
    monkeypatch.setattr(
        shutil, "which", lambda name: "/usr/bin/node" if name == "node" else None
    )
    chosen, skipped = _curated("", fetch)
    assert "cspell (no bun to install with)" in skipped
    assert "basedpyright" in {d.key for d in chosen}


def _uv_listing(monkeypatch, entries):
    """Serve *entries* as `uv python list --output-format json` would."""
    import json as _json

    from livery.toolroom.bench import _toolfetch

    monkeypatch.setattr(_toolfetch, "_capture", lambda _argv: _json.dumps(entries))


def _cpython(version, day, **over):
    """One entry of uv's listing, defaulting to a downloadable stable build."""
    return {
        "version": version,
        "implementation": "cpython",
        "variant": "default",
        "path": None,
        "url": f"https://example.invalid/releases/download/{day}/cpython-{version}.tar.gz",
        **over,
    }


def test_the_python_listing_keeps_only_what_is_a_release(monkeypatch):
    """A pre-release is not something to claim an option arrived in, a
    free-threaded build is a build of a release rather than one of its own,
    and pypy is a different tool.
    """
    from livery.toolroom.bench import _drivers, _toolfetch

    _uv_listing(
        monkeypatch,
        [
            _cpython("3.14.6", "20260718"),
            _cpython("3.15.0a7", "20260801"),
            _cpython("3.13.14", "20260718", variant="freethreaded"),
            _cpython("3.12.0", "20231002", implementation="pypy"),
        ],
    )
    driver = _drivers.find("python")
    assert driver is not None
    assert [r.version for r in _toolfetch.releases(driver)] == ["3.14.6"]


def test_the_python_listing_asks_only_for_downloads(monkeypatch):
    """Installing a version replaces its download entry with the local path
    and drops the URL the date is read from. Asking for anything but downloads
    would therefore make the index answer differently on every machine — and a
    prime would erase releases from the listing it is walking.
    """
    from livery.toolroom.bench import _drivers, _toolfetch

    seen: list[list[str]] = []

    def fake_capture(argv):
        seen.append(argv)
        return "[]"

    monkeypatch.setattr(_toolfetch, "_capture", fake_capture)
    driver = _drivers.find("python")
    assert driver is not None
    _toolfetch.releases(driver)
    assert "--only-downloads" in seen[0]


def test_a_uv_that_will_not_answer_is_unreachable_not_empty(monkeypatch):
    """Uv carries the index inside itself, so "no uv" is "nothing seen" — and
    emphatically not "CPython has no releases".
    """
    from livery.toolroom.bench import _drivers, _toolfetch

    monkeypatch.setattr(_toolfetch, "_capture", lambda _argv: "")
    driver = _drivers.find("python")
    assert driver is not None
    with pytest.raises(_toolfetch.Unreachable):
        _toolfetch.releases(driver)


def test_a_chain_is_ordered_by_version_not_by_publication_date(monkeypatch):
    """Three curated tools keep more than one series alive at once — cmake
    3.31.x beside 4.x, pytest's 4.6 LTS beside 5.x, CPython's five — so the
    newest release is not the most recently published one.

    Ordered by date, a walk back from 3.14.6 steps to 3.13.14 and records
    every 3.14 option as dropped, then re-adds them lower down; every
    interval derived from that chain is then wrong. The history answers a
    version question, so version is what orders it.
    """
    from livery.toolroom.bench import _drivers, _toolfetch

    _uv_listing(
        monkeypatch,
        [
            _cpython("3.13.14", "20260718"),  # same build date as 3.14.6...
            _cpython("3.14.6", "20260718"),
            _cpython("3.12.13", "20260718"),
            _cpython("3.14.5", "20260611"),  # ...and published before 3.13.14
        ],
    )
    driver = _drivers.find("python")
    assert driver is not None
    found = [r.version for r in _toolfetch.releases(driver)]
    assert found == ["3.14.6", "3.14.5", "3.13.14", "3.12.13"]


def test_an_entry_names_what_changed_and_counts_what_it_will_not_list():
    """Added and dropped options are what a reader acts on, so they are named
    — by their command-line spelling, which is what they recognise, and not
    by the Python-side key the surface happens to store them under.

    Rewordings are counted. A release can restate half a dozen descriptions
    without changing what the tool accepts, and listing those turns a release
    note into a diff dump.
    """
    from livery.toolroom.bench import _tasks as tools

    doc, versions = chain_of(
        with_options("quiet", "install_hooks"),
        with_options("quiet", "prepare_hooks"),  # one added, one dropped
    )
    entry = tools._entry_for("prek", doc, versions[1:])

    assert entry.startswith("- **prek 1.0.1**")
    assert "`--prepare-hooks`" in entry  # the spelling, not `prepare_hooks`
    assert "drops `--install-hooks`" in entry


def test_an_entry_spans_from_the_release_before_the_first_change():
    """A release compared against itself is empty by construction, so the
    span has to start one earlier or the first change is invisible — which
    is exactly what a bullet saying "changes its option surface" looked
    like.
    """
    from livery.toolroom.bench import _tasks as tools

    doc, versions = chain_of(with_options("quiet"), with_options("quiet", "fix"))
    assert "adds `--fix`" in tools._entry_for("demo", doc, [versions[1]])


def test_the_entry_lands_under_unreleased_changed(tmp_path):
    """`### Changed`, because a tool gaining a flag changes footman's *stub*
    — footman itself added nothing. And under `[Unreleased]`, never the
    released section above it, which is where a careless insert lands.
    """
    from livery.toolroom.bench import _tasks as tools

    path = tmp_path / "CHANGELOG.md"
    path.write_text(
        "# Changelog\n\n## [Unreleased]\n\n### Added\n\n- Something.\n\n"
        "### Fixed\n\n- Something else.\n\n## [0.23.0] — 2026-07-27\n\n"
        "### Changed\n\n- Already released.\n",
        encoding="utf-8",
    )
    assert tools._write_changelog(["- **prek 0.4.11** adds `--glob`."], path) is True

    written = path.read_text(encoding="utf-8")
    unreleased, released = written.split("## [0.23.0]")
    assert "- **prek 0.4.11** adds `--glob`." in unreleased
    assert "- **prek 0.4.11**" not in released  # not swept into a shipped version
    # Keep a Changelog's order: Changed sits above Fixed, not appended anywhere.
    assert unreleased.index("### Changed") < unreleased.index("### Fixed")


def test_an_entry_joins_a_changed_section_that_already_exists(tmp_path):
    from livery.toolroom.bench import _tasks as tools

    path = tmp_path / "CHANGELOG.md"
    path.write_text(
        "# Changelog\n\n## [Unreleased]\n\n### Changed\n\n- An earlier note.\n",
        encoding="utf-8",
    )
    assert tools._write_changelog(["- **ruff 0.16.1** adds `--fix`."], path) is True
    written = path.read_text(encoding="utf-8")
    assert written.count("### Changed") == 1
    assert "- **ruff 0.16.1** adds `--fix`." in written
    assert "- An earlier note." in written


def test_a_changelog_with_nowhere_to_write_says_so(tmp_path):
    """Reported rather than guessed at: a caller must not read "no entry
    written" as "there was nothing to write".
    """
    from livery.toolroom.bench import _tasks as tools

    path = tmp_path / "CHANGELOG.md"
    path.write_text("# Changelog\n\n## [0.23.0]\n\n- Released.\n", encoding="utf-8")
    assert tools._write_changelog(["- **prek 0.4.11** adds `--glob`."], path) is False
    assert tools._write_changelog(["- x"], tmp_path / "absent.md") is False


def test_a_bullet_joins_several_names_and_clauses_readably():
    from livery.toolroom.bench import _tasks as tools

    assert tools._names(["--a"]) == "`--a`"
    assert tools._names(["--a", "--b"]) == "`--a` and `--b`"
    assert tools._names(["--a", "--b", "--c"]) == "`--a`, `--b` and `--c`"
    assert tools._and(["one"]) == "one"
    assert tools._and(["one", "two", "three"]) == "one, two and three"
    assert tools._plural("release", 1) == "release"
    assert tools._plural("release", 2) == "releases"


def test_a_prime_keeps_uv_downloads_inside_its_own_scratch(tmp_path):
    """Uv writes to two places of its own accord — a wheel cache, and the
    store holding the interpreters this machine actually runs. Neither is a
    prime's to fill, and a walk of CPython's releases put 90 interpreters in
    that store and left them there.

    Pointed inside the scratch directory, the cleanup is structural: one
    rmtree removes every byte the walk caused, and the python you develop
    against is never a candidate for deletion.
    """
    import os

    from livery.toolroom.bench import _tasks as tools

    was = {k: os.environ.get(k) for k in ("UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR")}
    with tools._sandboxed(tmp_path):
        assert os.environ["UV_CACHE_DIR"].startswith(str(tmp_path))
        assert os.environ["UV_PYTHON_INSTALL_DIR"].startswith(str(tmp_path))
    assert {k: os.environ.get(k) for k in was} == was  # and put back


def test_an_overlay_restores_a_variable_that_was_not_set(tmp_path):
    """Restoring must remove what it added, not write an empty string —
    an empty `UV_CACHE_DIR` is a cache directory, not the absence of one.
    """
    import os

    from livery.toolroom.bench import _tasks as tools

    os.environ.pop("FOOTMAN_TEST_ABSENT", None)
    with tools._overlay(FOOTMAN_TEST_ABSENT="x"):
        assert os.environ["FOOTMAN_TEST_ABSENT"] == "x"
    assert "FOOTMAN_TEST_ABSENT" not in os.environ


def test_a_release_is_discarded_once_its_surface_is_read(tmp_path):
    """Peak disk is one release rather than all of them. Without this a prime
    holds everything it ever fetched until the run ends — ruff alone would
    stand up 416 environments at once.
    """
    from livery.toolroom.bench import _tasks as tools

    release = tmp_path / "1.2.3"
    (release / "bin").mkdir(parents=True)
    (release / "bin" / "tool").write_text("x")
    tools._discard(release / "bin")
    assert not release.exists()
    tools._discard(release / "bin")  # gone already: not an error


def test_a_bare_call_is_refused_with_directions(tmp_path, monkeypatch):
    """No sequential twin: outside a run the isolation the gather leans on
    does not exist, so the walk refuses and says how to run it instead of
    degrading into the exact race it was built to remove.
    """
    from livery.footman.context import Failed
    from livery.toolroom.bench import _tasks as tools

    isolate(tools, monkeypatch, tmp_path)
    with pytest.raises(Failed, match=r"footman\.testing\.Runner"):
        tools.refresh(only="ruff")
    with pytest.raises(Failed, match=r"needs a run"):
        tools.prime(only="ruff")


def test_the_sandbox_puts_nothing_of_its_own_on_path(tmp_path, monkeypatch):
    """The walk runs the packages on the node the machine or the prefix has:
    the sandbox scopes uv's directories and writes no runtime of its own.
    """
    import os
    import shutil

    from livery.toolroom.bench import _tasks as tools

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    before = os.environ["PATH"]
    with tools._sandboxed(tmp_path / "scratch"):
        assert os.environ["PATH"] == before
        assert os.environ["UV_NO_CACHE"] == "1"
        assert not (tmp_path / "scratch" / "shims").exists()


@pytest.mark.parametrize(
    ("help_text", "options", "verdict"),
    [
        ("Lint your spelling.", ("fix",), True),
        ("/usr/bin/env: 'node': No such file or directory", (), False),
        ("/usr/bin/env: 'node': No such file or directory", ("fix",), False),
        ("cspell: command not found", (), False),
        ("'foo' is not recognized as an internal or external command", (), False),
        ("Lint your spelling.", (), False),
    ],
)
def test_a_reading_must_describe_a_tool_to_count_as_one(help_text, options, verdict):
    """A launcher that cannot find its interpreter still prints prose and
    exits, and the extractor will faithfully turn that prose into a surface.

    The Linux box had no `node`; every npm-tier release read as one bare verb
    with no options and help text saying so. Stored, that claims the tool
    accepts nothing — which folds as 855 options "missing on Linux" for a
    tool that never ran. An observation has to be a description, not merely
    output.
    """
    from livery.toolroom.bench import _tasks as tools

    spec = ToolSpec(
        name="cspell",
        help=help_text,
        verbs=(
            Verb(
                name="",
                help=help_text,
                options=tuple(Option(o, (f"--{o}",)) for o in options),
            ),
        ),
    )
    assert tools._describes_itself(spec) is verdict


def test_a_run_whose_holes_outnumber_its_readings_fails(capsys):
    """`wrote obs-linux.json — 33 observations` was the line a *complete* run
    printed too, and the exit status was 0 with 330 of 363 releases unread.

    That is worse than failing: the document looks foldable, and folding it
    records a platform where the tools do not exist. Holes in the majority
    mean the machine, not the tools.
    """
    from livery.footman.context import Failed
    from livery.toolroom.bench import _tasks as tools

    with pytest.raises(Failed) as failed:
        tools._report_gather(gathered(observed=33, missed=330))
    assert failed.value.code == 75  # EX_TEMPFAIL: look again
    assert "picture of the machine" in failed.value.reason
    assert "33 observed, 330 holes" in capsys.readouterr().out


def test_an_ordinary_hole_is_not_a_failure(capsys):
    """One release whose asset has gone is ordinary. Failing on it would
    teach a weekly job's readers to ignore the exit code, which is the only
    way the majority case can go unnoticed.
    """
    from livery.toolroom.bench import _tasks as tools

    tools._report_gather(gathered(observed=300, missed=2))  # no raise
    out = capsys.readouterr().out
    assert "300 observed, 2 holes" in out
    assert "holes in ruff:" in out


def test_the_counts_are_stated_together(capsys):
    """Both numbers on one line: a truncated read of a long log still shows
    what was missed beside what was found.
    """
    from livery.toolroom.bench import _tasks as tools

    tools._report_gather(gathered(observed=5, missed=0))
    assert "Linux: 5 observed, 0 holes" in capsys.readouterr().out


def test_a_disk_with_no_room_stops_the_walk_instead_of_recording_holes(
    tmp_path, monkeypatch
):
    """A hole says *this release* could not be had. A full disk says nothing
    about any release, and every observation after it fails identically —
    recorded, that reads as a platform where the tools do not exist.

    The Linux box hit exactly this: a walk that exhausted the disk wrote 330
    holes, any one of which would have been folded as fact.
    """
    import shutil

    from livery.footman.context import Failed
    from livery.toolroom.bench import _tasks as tools

    Usage = collections.namedtuple("Usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: Usage(1, 1, 4 * 1024 * 1024))
    with pytest.raises(Failed) as failed:
        tools._refuse_a_broken_environment(tmp_path)
    assert failed.value.code == 75
    assert "4 MB free" in failed.value.reason

    # ...and room enough is simply a hole, as before.
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: Usage(1, 1, 40 * 1024**3))
    tools._refuse_a_broken_environment(tmp_path)


def test_a_skip_only_some_legs_reported_says_which():
    """Unattributed, one leg's skip reads as every leg's.

    Windows has no `man`, so it alone skips the tools read from their
    manuals — and `git (no man to read the pages with)` in a refresh PR
    then looks exactly like a tier nobody refreshes, when git's pages had
    been read on both Linux and macOS. A skip every leg reported needs no
    attribution: it is a fact about the tool, not about a box.
    """
    from livery.toolroom.bench._tasks import _attributed

    lines = _attributed(
        {
            "bash (hand-written)": ["Linux", "macOS", "Windows"],
            "git (no man to read the pages with)": ["Windows"],
            "cspell (no bun to install with)": ["Windows", "macOS"],
        },
        legs=3,
    )
    assert "bash (hand-written)" in lines
    assert "git (no man to read the pages with) — Windows only" in lines
    assert "cspell (no bun to install with) — Windows and macOS only" in lines

    # One document has nothing to contrast with, so nothing is claimed.
    assert _attributed({"git (no man)": ["Windows"]}, legs=1) == ["git (no man)"]


def test_a_hand_written_stub_is_not_a_uv_tier_tool():
    """A `source="manual"` driver carries the *default* provision kind, so
    asking it names the `uv` tier for a shell nobody fetches — six of them in
    every document's skipped list.
    """
    from livery.toolroom.bench import _toolfetch
    from livery.toolroom.bench._tasks import _curated

    _, skipped = _curated("", _toolfetch)
    assert "bash (hand-written)" in skipped
    assert not any("uv tier" in line for line in skipped)


def test_bin_on_path_overlays_the_directory_itself(tmp_path):
    """A Windows venv keeps binaries in `Scripts` and uv's interpreter store
    keeps `python.exe` at the store root — the observation must overlay the
    directory the install actually returned, never a rebuilt `<parent>/bin`.
    """
    import os

    from livery.toolroom.bench._tasks import _bin_on_path

    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    with _bin_on_path(scripts):
        assert os.environ["PATH"].split(os.pathsep)[0] == str(scripts)


def test_observe_rejects_a_reading_of_the_wrong_binary(tmp_path, monkeypatch):
    """The help-path twin of `_from_click`'s guard. With the release's own
    directory missing from `PATH`, the extractor resolves some ambient
    binary and faithfully describes it under this release's label — on
    Windows a whole platform's uv tier read as one tool, no holes to show
    for it. A reading that names a different version must be a hole.
    """
    from livery.toolroom.bench import _tasks as tools_tasks

    bindir = tmp_path / "release" / "bin"

    def fake_install(driver, release, into):
        bindir.mkdir(parents=True, exist_ok=True)
        return bindir

    monkeypatch.setattr("livery.toolroom.bench._toolfetch.install", fake_install)
    monkeypatch.setattr(
        tools_tasks._drivers, "extract", lambda driver: spec_of(version="0.99.9")
    )
    assert tools_tasks.observe("ruff", "0.15.0", scratch=str(tmp_path)) is None

    monkeypatch.setattr(
        tools_tasks._drivers, "extract", lambda driver: spec_of(version="0.15.0")
    )
    assert tools_tasks.observe("ruff", "0.15.0", scratch=str(tmp_path)) is not None


def test_npm_install_spawns_the_resolved_bun(tmp_path, monkeypatch):
    """`which` sees the task router's PATH overlay; Windows CreateProcess
    does not — its executable search reads the real process environment. So
    a bare `["bun", ...]` that `which` just found still failed to spawn, and
    every npm-tier release on the platform read as a hole. The spawn must
    use the path `which` resolved.
    """
    from livery.toolroom.bench import _drivers, _toolfetch

    fake = tmp_path / "bun.exe"
    calls: list[list[str]] = []
    monkeypatch.setattr("shutil.which", lambda name: str(fake))

    def fake_run(argv, env=None):
        calls.append(argv)
        return True

    monkeypatch.setattr(_toolfetch, "_run", fake_run)
    driver = _drivers.find("cspell")  # runs on bun by its driver
    assert driver is not None
    out = _toolfetch._install_npm(driver, "9.8.0", tmp_path / "into")
    assert out == tmp_path / "into" / "bin"
    assert calls[0][0] == str(fake)
    assert calls[0][1:] == ["add", "--global", "cspell@9.8.0"]
    # bun's global project is made under the prefix before it runs.
    assert (tmp_path / "into" / "install" / "global" / "package.json").is_file()


def test_the_nodejs_index_lists_support_lines_newest_first(monkeypatch):
    """Node's own index, not a forge: a row per release with its date and
    builds. Only the support lines are releases here, so a runtime the
    tools run on never lands on a current line, and the six builds a
    version's artifacts come from are named from the version alone.
    """
    from livery.toolroom.bench import _drivers, _toolfetch

    rows = [
        {"version": "v26.10.0", "date": "2026-09-21", "lts": False, "files": ["x"]},
        {"version": "v24.21.0", "date": "2026-09-07", "lts": "Krypton", "files": ["x"]},
        {"version": "v22.20.0", "date": "2026-08-01", "lts": "Jod", "files": ["x"]},
        {"version": "v24.20.0", "date": "2026-08-26", "lts": "Krypton", "files": []},
    ]
    monkeypatch.setattr(_toolfetch, "_index", lambda url: rows)
    driver = _drivers.find("node")
    assert driver is not None
    found = _toolfetch.releases(driver)
    assert [(r.version, r.tag, r.date) for r in found] == [
        ("24.21.0", "v24.21.0", "2026-09-07"),
        ("22.20.0", "v22.20.0", "2026-08-01"),
    ]
    assets = dict(_toolfetch.nodejs_assets("24.21.0"))
    assert set(assets) == {
        "node-v24.21.0-darwin-arm64.tar.gz",
        "node-v24.21.0-darwin-x64.tar.gz",
        "node-v24.21.0-linux-x64.tar.gz",
        "node-v24.21.0-linux-arm64.tar.gz",
        "node-v24.21.0-win-x64.zip",
        "node-v24.21.0-win-arm64.zip",
    }
    assert assets["node-v24.21.0-win-arm64.zip"] == (
        "https://nodejs.org/dist/v24.21.0/node-v24.21.0-win-arm64.zip"
    )
    # Each host picks its own build the way it picks a forge's asset.
    from livery.toolroom.bench import _provision

    picked = {
        host: _provision._pick_asset(list(assets.items()), host=host)[0]
        for host in _provision.HOST_TOKENS
    }
    assert picked == {
        "linux-x64": "node-v24.21.0-linux-x64.tar.gz",
        "linux-arm": "node-v24.21.0-linux-arm64.tar.gz",
        "macos-x64": "node-v24.21.0-darwin-x64.tar.gz",
        "macos-arm": "node-v24.21.0-darwin-arm64.tar.gz",
        "windows-x64": "node-v24.21.0-win-x64.zip",
        "windows-arm": "node-v24.21.0-win-arm64.zip",
    }


def test_npm_install_runs_npm_on_the_resolved_node(tmp_path, monkeypatch):
    """A package with no runtime named runs on node: npm's script beside the
    resolved node, the release's prefix to itself.
    """
    from livery.toolroom.bench import _drivers, _toolfetch

    node = tmp_path / "node" / "bin" / "node"
    cli = tmp_path / "node" / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js"
    for path in (node, cli):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    calls: list[list[str]] = []

    def fake_run(argv, env=None):
        calls.append(argv)
        return True

    monkeypatch.setattr(
        "shutil.which", lambda name: str(node) if name == "node" else None
    )
    monkeypatch.setattr(_toolfetch, "_run", fake_run)
    driver = _drivers.find("basedpyright")
    assert driver is not None
    out = _toolfetch._install_npm(driver, "1.39.0", tmp_path / "into")
    assert out == tmp_path / "into" / "bin"
    assert calls[0][:2] == [str(node), str(cli)]
    assert (
        calls[0][2:4] == ["install", "--global"]
        and calls[0][-1] == "basedpyright@1.39.0"
    )


def test_observe_accepts_a_repack_wheel_version(tmp_path, monkeypatch):
    """PyPI's ninja 1.11.1.4 wraps a binary that answers `1.11.1` — the
    repack's own trailing component must not read as a wrong binary. Only a
    dotted prefix though, and never the reverse: a binary reporting *more*
    components than its release is some other binary.
    """
    from livery.toolroom.bench import _tasks as tools_tasks

    bindir = tmp_path / "release" / "bin"

    def fake_install(driver, release, into):
        bindir.mkdir(parents=True, exist_ok=True)
        return bindir

    monkeypatch.setattr("livery.toolroom.bench._toolfetch.install", fake_install)
    monkeypatch.setattr(
        tools_tasks._drivers, "extract", lambda driver: spec_of(version="1.11.1")
    )
    assert tools_tasks.observe("ninja", "1.11.1.4", scratch=str(tmp_path)) is not None
    assert tools_tasks.observe("ninja", "1.11.14", scratch=str(tmp_path)) is None
    monkeypatch.setattr(
        tools_tasks._drivers, "extract", lambda driver: spec_of(version="1.11.1.4")
    )
    assert tools_tasks.observe("ninja", "1.11.1", scratch=str(tmp_path)) is None


def test_python_find_ignores_the_cwd_project(monkeypatch, tmp_path):
    """`uv python find` consults the nearest pyproject, and the walk runs
    inside footman's own checkout — whose `requires-python` has opinions.
    `--no-project` keeps the answer about the version that was asked.
    """
    from livery.toolroom.bench import _toolfetch

    calls: list[list[str]] = []

    def capture(argv, env=None):
        calls.append(argv)
        return ""

    monkeypatch.setattr(_toolfetch, "_run", lambda argv, env=None: True)
    monkeypatch.setattr(_toolfetch, "_capture", capture)
    assert _toolfetch._install_python("3.10.0", tmp_path) is None
    assert calls == [["uv", "python", "find", "--no-project", "3.10.0"]]


def test_python_installs_into_a_private_store(monkeypatch, tmp_path):
    """A shared uv store is one lock, and the walk is ten concurrent
    installs: uv queues the waiters, the walk's subprocess timeouts kill
    the queue, and every run scattered a different third of the python
    chain into holes. Each release installs into a store inside its own
    throwaway directory — discarded with the release, contended by nobody.
    """
    from livery.toolroom.bench import _toolfetch

    envs: list[dict[str, str] | None] = []

    def fake_run(argv, env=None):
        envs.append(env)
        return True

    monkeypatch.setattr(_toolfetch, "_run", fake_run)
    monkeypatch.setattr(_toolfetch, "_capture", lambda argv, env=None: "")
    _toolfetch._install_python("3.10.0", tmp_path)
    assert envs[0] is not None
    assert envs[0]["UV_PYTHON_INSTALL_DIR"] == str(tmp_path / "store")
