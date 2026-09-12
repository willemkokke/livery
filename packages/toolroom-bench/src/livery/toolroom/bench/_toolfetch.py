"""Past releases of a curated tool, for priming its option history.

Provisioning fetches the *latest* of everything into one prefix. Priming is
the other direction: one specific release at a time, into a throwaway
environment, read once and thrown away — walking backwards from the newest
because the current version is the one that matters most, and because a
backward walk appends to the history rather than rewriting it.

The listable tiers: PyPI (`uv`), npm (`node`), release assets from GitHub,
GitLab and Gitea — which covers bun's own releases too — CPython, whose
index is the provisioned uv's own, and docker's static-build directory.
What remains unlistable is the `system` tier (git, read from the host). A
tool footman cannot list is named and skipped rather than silently treated
as having no history — the same doctrine `audit` follows.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from livery.toolroom.bench._drivers import Driver, Plugin, Provision

PYPI = "https://pypi.org/pypi/{package}/json"
TIMEOUT = 30

PYTHON_RELEASES = (
    ("3.14", "2025-10-07"),
    ("3.13", "2024-10-07"),
    ("3.12", "2023-10-02"),
    ("3.11", "2022-10-24"),
    ("3.10", "2021-10-04"),
)
"""When each CPython arrived, newest first — the eras a release is read in.

A release has to be read in roughly the world it shipped into or it cannot
be read at all: twine 5.1.0 indexes `metadata["home-page"]` at import, a key
`importlib_metadata` 8 stopped returning and started raising `KeyError` on,
so under today's resolution it dies before argparse runs. `--exclude-newer`
at the release's own date fixes that, and only resolves on an interpreter
old enough to have wheels from that date — a 2022 `cryptography` has no
cp314 build and will not compile against one.

So the interpreter is the newest CPython that existed when the release
shipped, not one fixed version. That is what makes the reading *of its
era* rather than merely old: a 2026 release is read on 3.14 exactly as it
is today, and a 2022 release on 3.11, because that is what anyone running
it then would have seen.

It also settles what a fixed floor could not. Python 3.13 taught argparse
to print every alias, so pytest reports `--junitxml` alongside
`--junit-xml` there and only `--junit-xml` below it. Reading everything on
one old interpreter would have quietly dropped those aliases from releases
that really do show them; reading each release in its own era records the
aliases exactly where they appeared, and the surface corrects itself as the
walk crosses October 2024.

Upstream release days, not the build dates in uv's index — python-build-
standalone shipped 3.11 in January 2023, three months late, which would
have read that autumn's releases on 3.10.
"""

READ_PYTHON, READ_PYTHON_SINCE = PYTHON_RELEASES[-1]
"""The oldest era, and so the walk's horizon: the oldest CPython still
receiving fixes, and the day it arrived.

Nothing was ever built for an interpreter before it existed, so a release
older than this has no period wheels to resolve against and cannot be read
on anything. Those are not offered rather than walked and recorded as
holes — a hole says "this could not be read", and the store should not
carry a shrug about a release that is simply out of scope.

Drop the last row when 3.10 goes end-of-life and the horizon slides with
it, taking the releases it could read out of scope by the same rule.
"""

_MIN_PYTHON = re.compile(r">=\s*3\.(\d+)")


@dataclass(frozen=True)
class Release:
    """One published release: what to install, and when it was published."""

    version: str
    tag: str = ""
    """What the *forge* calls this release, when that differs from the version
    it reports — bun tags `bun-v1.3.13` for a binary answering `1.3.13`.
    Carried rather than guessed: an install that pattern-matched the tag could
    only ever cover the spellings someone had already met."""
    date: str = ""
    """`YYYY-MM-DD`, from the index. Not the ordering key — the version is —
    but what breaks a tie between two builds of one base (`0.6.0-wk.5`),
    which is the one comparison a version cannot make."""
    requires_python: str = ""
    """The release's own `Requires-Python`, verbatim from the index. What
    keeps the floor from becoming a ceiling: zensical asks for `>=3.10` today
    and something will ask for more tomorrow, so the interpreter to read on is
    the higher of this and `READ_PYTHON`, never `READ_PYTHON` flat."""
    published: str = ""
    """The day the release *finished* publishing, where `date` is the day it
    started. Usually the same day and usually uninteresting, but publishing is
    a window rather than an instant: ninja 1.11.1 took 76 days to upload its
    seventeen files, cmake 4.3.1 three. A date cutoff at the near edge of that
    window filters out the release's own later files, which is how a release
    ends up unable to install itself."""


def read_python(requires_python: str = "", date: str = "") -> str:
    """The interpreter to read a release on: its era, raised to its minimum.

    The era is the newest CPython that had shipped by *date*. A release
    that will not run on it — one asking for more than its own era offered,
    which happens while a Python is still in prerelease — is read on the
    oldest it will run on instead. Without a date there is no era to pick,
    so the newest is used, which is what reading an unstamped release
    already meant.
    """
    era = (
        next((v for v, since in PYTHON_RELEASES if date >= since), READ_PYTHON)
        if date
        else PYTHON_RELEASES[0][0]
    )
    found = _MIN_PYTHON.search(requires_python or "")
    minimum = int(found.group(1)) if found else 0
    return f"3.{max(int(era.split('.')[1]), minimum)}"


LISTABLE = (
    "uv",
    "node",
    "github",
    "gitlab",
    "gitea",
    "bun",
    "python",
    "docker",
    "man",
)
"""The tiers with a release index footman can read. `system` is absent
because git and docker are read from the host and have no fetch source."""


def can_list(driver: Driver) -> bool:
    """Whether this tool's past releases can be enumerated."""
    return driver.provision.kind in LISTABLE and driver.source != "manual"


def releases(driver: Driver) -> list[Release]:
    """Every published release, **newest first**, whatever the tier.

    A tier with no index at all returns nothing: the caller names it as
    skipped, and a tool nobody can list is not a tool with no history.

    An index that *exists* and could not be read raises `Unreachable`
    instead. The two used to share the empty list, which is the wrong shape
    for the question a release job asks — "is there anything new" answered
    "no" by a throttled registry ends the job with "nothing to release".
    """
    if not can_list(driver):
        # Not just the unlistable tiers: a hand-written stub carries the
        # *default* provision kind, so `bash` would otherwise be looked up on
        # PyPI — where a package by that name exists and is a different thing
        # entirely.
        return []
    kind = driver.provision.kind
    if kind == "uv":
        found = _pypi(driver)
    elif kind == "node":
        found = _npm(driver)
    elif kind in ("github", "gitlab", "gitea", "bun"):
        found = _forge(driver, kind if kind in ("gitlab", "gitea") else "github")
    elif kind == "python":
        found = _uv_python()
    elif kind == "docker":
        found = _docker_index()
    elif kind == "man":
        found = _man_index(driver)
    else:
        return []
    found = _stable(found)
    if driver.provision.floor:
        from livery.toolroom.tools import version_tuple

        cut = version_tuple(driver.provision.floor)
        found = [r for r in found if version_tuple(r.version) >= cut]
    return found


_PRERELEASE = re.compile(
    r"(?:a|b|rc|alpha|beta|dev|pre)\.?\d*$|-(?:alpha|beta|rc|dev|pre)", re.I
)


def _stable(found: list[Release]) -> list[Release]:
    """Releases only — an alpha is not something to say a flag arrived in.

    Also what made two tools *look* like they ship series in parallel:
    coverage's 4.5.4 landing after 5.0a6, cspell's 6.31.3 after
    7.0.1-alpha.8. Neither is concurrent maintenance; both are a pre-release
    sorting as though it were the final one.
    """
    return [release for release in found if not _PRERELEASE.search(release.version)]


def _order(found: list[Release]) -> list[Release]:
    """Newest first, **by version** — with the date breaking a tie.

    Not by date, which was the first answer and the wrong one. This history
    answers a version question — does *my* build carry this flag — and three
    tools here keep more than one series alive at once: cmake 3.31.x beside
    4.x, pytest's 4.6 LTS beside 5.x, CPython's five. For those, publication
    order and version order genuinely differ, and a date-ordered walk steps
    from 3.14.6 to 3.13.14 and reads every 3.14 option as dropped and then
    re-added a few entries later.

    Version order was avoided because `version_tuple` could not separate
    `0.6.0-wk.5` from `0.6.0` — a fact about the comparator rather than about
    versions, and fixed there. Measured across all 24 listable tools and
    3,214 stable releases, this ordering is total with no collisions.
    """
    from livery.toolroom.tools import version_tuple

    return sorted(
        found,
        key=lambda r: (version_tuple(r.version), _patchlevel(r.version), r.date),
        reverse=True,
    )


_PATCHLEVEL = re.compile(r"p(\d+)$")


def _patchlevel(version: str) -> int:
    """OpenSSH's portable patchlevel: `9.9p2` follows `9.9p1`.

    `version_tuple` deliberately reads two builds of one base as equal and
    leaves the caller to say what that means; ordering a release chain is a
    caller with an answer. OpenSSH's listing shows no dates, so without
    this the p-levels of one base would keep their listing order rather
    than their release order. Inert everywhere else: no other curated
    tool's versions end in `p<digits>`.
    """
    match = _PATCHLEVEL.search(version)
    return int(match[1]) if match else 0


class Unreachable(Exception):
    """An index that could not be read at all.

    Raised rather than returned as an empty listing, because the two are
    opposite answers that used to share a value. A release job asks "is
    there anything new" and stops when the answer is no; a throttled
    registry answering `{}` would end that job with "nothing new, nothing to
    release" when the truth is that nobody looked. An exception is the shape
    that cannot be read past by accident.
    """

    def __init__(self, source: str, cause: object) -> None:
        super().__init__(f"cannot read {source}: {cause}")
        self.source = source


def _read_index(request: urllib.request.Request, url: str) -> bytes:
    """Fetch an index, retrying the failures that are about the connection.

    The same rule `_download` follows, one layer up. A refresh leg died on
    `HTTP Error 504: Gateway Timeout` reading docker/buildx's release list —
    a momentary hiccup, and the whole platform's observations went with it,
    which is exactly what retrying a download was written to prevent.

    `Unreachable` still ends the run when the tries are spent: an index
    that will not answer must never read as "nothing new".
    """
    from livery.toolroom.bench._provision import _worth_retrying

    for attempt in range(_INDEX_TRIES):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                data: bytes = response.read()
                return data
        except (urllib.error.URLError, TimeoutError, OSError) as cause:
            if attempt + 1 == _INDEX_TRIES or not _worth_retrying(cause):
                raise Unreachable(url, cause) from cause
            time.sleep(_INDEX_BACKOFF * (attempt + 1))
    raise Unreachable(url, "unreachable")  # pragma: no cover - the loop returns


_INDEX_TRIES = 3
_INDEX_BACKOFF = 1.0


def _index(url: str) -> Any:
    """A registry's JSON — shape varies by registry (PyPI a dict, a paged
    forge API a list), so the honest static type is the JSON it is. Raises
    `Unreachable` when it cannot be read.
    """
    from livery.toolroom.bench._provision import api_headers

    request = urllib.request.Request(url, headers=api_headers(url))
    try:
        return json.loads(_read_index(request, url))
    except ValueError as cause:  # answered, but not with JSON
        raise Unreachable(url, cause) from cause


def _npm(driver: Driver) -> list[Release]:
    """Npm's registry keeps a `time` map of version to publication date."""
    package = driver.provision.target(driver.name)
    index = _index(f"https://registry.npmjs.org/{package}")
    times = index.get("time", {})
    return _order(
        [
            Release(version=version, date=str(published)[:10])
            for version, published in times.items()
            if version not in ("created", "modified")
            and version in index.get("versions", {})
        ]
    )


def _forge(driver: Driver, host: str, pages: int = 1) -> list[Release]:
    """GitHub, GitLab and Gitea releases, with the tag normalised to a version.

    A tag is `v2.96.0` on one project and `2.96.0` on the next, while the
    binary reports the bare number — and the history keys on what the binary
    says, or a primed release would never match the base it belongs under.

    One page — the hundred newest — is the horizon for a tool, which is
    read newest-first and walked back only as far as anyone asks. A plugin
    is different: it is looked up *by date*, to pair with a release of the
    host tool, so the page that matters is the one covering that date. The
    compose listing runs out in late 2022, which is well inside the range
    docker itself goes back to.
    """
    from livery.toolroom.tools import read_version

    repo = driver.provision.repo
    if not repo:
        return []
    if host in ("github", "gitea"):
        # Gitea's release API is GitHub-shaped — same field names, different
        # base URL and page size — so the two share one reading.
        base, size = (
            (f"https://api.github.com/repos/{repo}/releases?per_page=100", 100)
            if host == "github"
            else (f"https://gitea.com/api/v1/repos/{repo}/releases?limit=50", 50)
        )
        entries = []
        for page in range(1, pages + 1):
            index = _index(f"{base}&page={page}")
            got = index if isinstance(index, list) else []
            entries += got
            if len(got) < size:
                break
        found = [
            Release(
                version=read_version(e.get("tag_name", "")),
                tag=str(e.get("tag_name", "")),
                date=str(e.get("published_at"))[:10],
            )
            for e in entries
            if not e.get("draft") and not e.get("prerelease")
        ]
    else:
        quoted = urllib.parse.quote(repo, safe="")
        index = _index(f"https://gitlab.com/api/v4/projects/{quoted}/releases")
        entries = index if isinstance(index, list) else []
        found = [
            Release(
                version=read_version(e.get("tag_name", "")),
                tag=str(e.get("tag_name", "")),
                date=str(e.get("released_at"))[:10],
            )
            for e in entries
        ]
    return _order([r for r in found if r.version and r.date[:1].isdigit()])


_PBS_DATE = re.compile(r"/download/(\d{8})/")
_STABLE = re.compile(r"\d+\.\d+\.\d+")


def _uv_python() -> list[Release]:
    """CPython's releases, from uv's own download index.

    uv ships that index *inside the binary*, so the reading is only as
    current as the uv doing it — which is why the prime puts a provisioned
    prefix on `PATH` rather than trusting whatever uv a machine happens to
    have. A stale uv silently reports a stale newest python.

    The date is python-build-standalone's build date, read out of the
    download URL. It is not CPython's own release date, but it is when the
    artifact we install was published, and it is the only date the index
    carries. Several series share one build date, which `_order` breaks on
    the version.
    """
    listing = _capture(
        [
            "uv",
            "python",
            "list",
            "--all-versions",
            # Downloads only, or the index answers differently on every
            # machine: installing a version *replaces* its download entry
            # with the local path and drops the URL, so a prime would erase
            # releases from the very listing it walks.
            "--only-downloads",
            "--output-format",
            "json",
        ]
    )
    try:
        entries = json.loads(listing)
    except ValueError as cause:
        # No uv, or a uv that would not answer. Not "CPython has no
        # releases" — see `Unreachable`.
        raise Unreachable("uv python list", cause) from cause
    found: dict[str, Release] = {}
    for entry in entries if isinstance(entries, list) else ():
        version = str(entry.get("version", ""))
        if entry.get("implementation") != "cpython":
            continue  # pypy and graalpy are not this stub's tool
        if entry.get("variant") != "default":
            continue  # free-threaded is a build of a release, not a release
        if not _STABLE.fullmatch(version):
            continue  # 3.15.0a7 is not something to claim an option arrived in
        stamp = _PBS_DATE.search(str(entry.get("url") or ""))
        if stamp and version not in found:
            day = stamp.group(1)
            found[version] = Release(
                version=version, date=f"{day[:4]}-{day[4:6]}-{day[6:]}"
            )
    return _order(list(found.values()))


_DOCKER_INDEX = "https://download.docker.com/{os}/static/stable/{arch}/"
_DOCKER_FILE = re.compile(
    r'href="docker-(?P<version>\d+\.\d+\.\d+)\.(?:tgz|zip)"'
    r".*?(?P<date>\d{4}-\d{2}-\d{2})"
)


def _docker_channel() -> tuple[str, str, str]:
    """Where this machine's docker archives live, and what they are called.

    Docker publishes static builds per platform and architecture rather than
    one asset list, so the *index* is chosen here and the version list falls
    out of it — the inverse of a forge, where one release names many assets.
    """
    import platform as _platform_mod

    machine = _platform_mod.machine().lower()
    arch = "aarch64" if machine in ("arm64", "aarch64") else "x86_64"
    if _windows():
        return "win", "x86_64", "zip"  # no arm64 index published
    if sys.platform == "darwin":
        return "mac", arch, "tgz"
    return "linux", arch, "tgz"


_MOBY = "moby/moby"
_PLAIN_TAG = re.compile(r"v(\d+\.\d+\.\d+)$")

_LISTINGS: dict[tuple[str, int], list[Release]] = {}
_LISTINGS_LOCK = threading.Lock()


def _listing(repo: str, pages: int) -> list[Release]:
    """A forge listing, read once per process.

    A walk asks the same question of the same repository for every release
    it observes — which compose shipped by then — and the answer cannot
    change while the walk runs. Unmemoised, a hundred-release docker walk
    spends three hundred API calls on one listing and meets GitHub's
    unauthenticated hourly limit five times over.
    """
    key = (repo, pages)
    with _LISTINGS_LOCK:
        if key in _LISTINGS:
            return _LISTINGS[key]
    found = _forge(
        Driver(repo, provision=Provision(kind="github", repo=repo)), "github", pages
    )
    with _LISTINGS_LOCK:
        _LISTINGS[key] = found
    return found


def _docker_dates() -> dict[str, str]:
    """When each docker release actually shipped.

    The static index dates its files by *upload* time, and docker re-uploads
    in bulk: a third of the archives this machine can fetch are stamped one
    day in 2025, including 20.10.6, which shipped in April 2021. Those dates
    would go into the history as release dates and — worse — decide which
    compose each release is paired with, handing a 2021 docker the compose
    of four years later.

    The engine's own releases carry the real dates, under tags that are
    exactly the versions the static index publishes.
    """
    return {
        release.version: release.date
        for release in _listing(_MOBY, 3)
        if _PLAIN_TAG.fullmatch(release.tag)
    }


def _docker_index() -> list[Release]:
    """Every static docker build this platform can run.

    A directory listing rather than an API, so the version and its
    publication date are read from the row: `docker-29.6.2.tgz  2026-07-16`.

    The directory holds more than the builds we want, so the pattern spells
    out the exact `docker-<x.y.z>.<suffix>` shape rather than merely
    containing it. That deliberately passes over three neighbours:
    `docker-rootless-extras-*` (a different program), `docker-17.03.0-ce.tgz`
    (the 2017 spelling, older than any release this walk can read), and
    `docker-29.4.2-2.tgz` (a rebuild of a version already listed, which
    would key to the same entry and simply race the original).
    """
    os_name, arch, _suffix = _docker_channel()
    url = _DOCKER_INDEX.format(os=os_name, arch=arch)
    listing = _read_index(
        urllib.request.Request(url, headers={"User-Agent": "footman-provision"}), url
    ).decode("utf-8", "replace")
    shipped = _docker_dates()
    found = {
        match["version"]: Release(
            version=match["version"],
            date=shipped.get(match["version"], match["date"]),
        )
        for match in _DOCKER_FILE.finditer(listing)
    }
    return _order(list(found.values()))


def _install_docker(driver: Driver, release: Release, into: Path) -> Path | None:
    """Fetch one static build and place its binary where the walk reads it."""
    from livery.toolroom.bench import _provision

    os_name, arch, suffix = _docker_channel()
    index = _DOCKER_INDEX.format(os=os_name, arch=arch)
    url = f"{index}docker-{release.version}.{suffix}"
    bindir = into / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    try:
        archive = _provision._download(url, into)
        _provision._extract_binary(archive, "docker", bindir)
    except (_provision.ProvisionError, OSError, ValueError):
        return None
    home = home_beside(bindir)
    for plugin in driver.plugins:
        # The directory is made whether or not a plugin lands in it. An
        # absent one would send the reader to the machine's own plugins,
        # which is the contamination this exists to remove; an empty one
        # says what is true — this release had no compose, so those verbs
        # are absent.
        (home / plugin.path).mkdir(parents=True, exist_ok=True)
        install_plugin(plugin, release.date, home)
    return bindir


def home_beside(bindir: Path) -> Path:
    """The throwaway home that belongs to the release in *bindir*.

    A plugin is found by looking under the user's home, so reading one
    without reading the machine's own means giving the tool a different
    home for as long as it is being read. Keeping it beside the binaries
    is what lets the reader find it later from the binary alone, with no
    extra argument threaded through five call sites.
    """
    return bindir.parent / "home"


def install_plugin(plugin: Plugin, on_or_before: str, home: Path) -> bool:
    """Place the plugin release a user of that tool release would have had.

    Paired by date, newest that is not newer. A plugin has its own release
    line, so there is no version to match on — but "what shipped alongside"
    is a fact the two dates settle between them, the same answer every
    time. A plugin older than any release (or a listing that cannot be
    reached) leaves the home empty, and the verbs it owns simply read as
    absent, which is what a walk of an era before the plugin existed
    should say.
    """
    from livery.toolroom.bench import _provision
    from livery.toolroom.tools import version_tuple

    found = _listing(plugin.repo, 3)
    floor = version_tuple(plugin.since) if plugin.since else ()
    dated = [
        r
        for r in found
        if r.date
        and (not on_or_before or r.date <= on_or_before)
        and version_tuple(r.version) >= floor
    ]
    if not dated:
        return False
    release = dated[0]  # _forge orders newest first
    into = home / plugin.path
    into.mkdir(parents=True, exist_ok=True)
    for tag in (release.tag, release.version, f"v{release.version}"):
        if not tag:
            continue
        try:
            assets = _provision.assets_for("github", plugin.repo, tag)
            _name, url = _provision._pick_asset(assets)
            archive = _provision._download(url, home)
            _provision._extract_binary(archive, plugin.name, into)
        except (_provision.ProvisionError, OSError, ValueError):
            continue
        return True
    # We know which release belongs here and could not get it — a rate
    # limit, an outage, a withdrawn asset. Read past, that becomes "this
    # docker had no compose", which is a different claim entirely and one
    # that would be written into the history as a removal.
    raise Unreachable(f"{plugin.repo} {release.version}", "could not be placed")


_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)  # fmt: skip


def _man_index(driver: Driver) -> list[Release]:
    """Every release of a manual-read tool, by the pages that shipped with it.

    A tool is read from its *manual* where the binary cannot answer — git's
    `-h` omits about half its flags, ssh has no `--help` at all — and a
    manual is not a binary: the pages are the reading, so there is nothing
    to install and nothing to run. Where the manual lives is the driver's
    `Manual` descriptor; this walks its listing.

    The pages are also the same bytes everywhere. A manual has no platform,
    so two machines reading this tier cannot disagree — the cross-platform
    tagging simply has nothing to say about these tools, which is the
    honest answer rather than a gap.
    """
    man = driver.provision.manual
    if man is None:
        return []
    listing = _read_index(
        urllib.request.Request(man.index, headers={"User-Agent": "footman-provision"}),
        man.index,
    ).decode("utf-8", "replace")
    found = {}
    for match in re.finditer(man.listing, listing):
        date = ""
        if "month" in match.groupdict():
            if match["month"] not in _MONTHS:  # pragma: no cover - a mirror typo
                continue
            month = _MONTHS.index(match["month"]) + 1
            date = f"{match['year']}-{month:02d}-{match['day']}"
        found[match["version"]] = Release(version=match["version"], date=date)
    return _order(list(found.values()))


def _install_man(driver: Driver, release: Release, into: Path) -> Path | None:
    """Unpack one release's manuals where the reader will look for them."""
    import tarfile

    from livery.toolroom.bench import _provision

    man = driver.provision.manual
    if man is None:
        return None
    url = man.index + man.archive.format(version=release.version)
    tree = into / "man"
    tree.mkdir(parents=True, exist_ok=True)
    try:
        archive = _provision._download(url, into)
        with tarfile.open(archive) as tar:
            if not man.pages:
                # A bare `man1/…` tree with no top directory (git's manpages
                # tarball), landing where `man -M` expects a manpath root.
                tar.extractall(tree, filter="data")
            else:
                # A source archive that happens to carry its pages (OpenSSH's
                # release tarball): pull just the named pages, by basename so
                # the top directory's name never matters, written by hand so
                # a member path can't steer the extraction.
                section = tree / "man1"
                section.mkdir(parents=True, exist_ok=True)
                wanted = set(man.pages)
                for member in tar.getmembers():
                    name = member.name.rsplit("/", 1)[-1]
                    if member.isfile() and name in wanted:
                        payload = tar.extractfile(member)
                        if payload is not None:
                            (section / name).write_bytes(payload.read())
    except (_provision.ProvisionError, OSError, ValueError, tarfile.TarError):
        return None
    proof = man.pages[0] if man.pages else f"{driver.name}.1"
    if not any(tree.glob(f"man1/{proof}")):
        return None
    # The tree says which release it documents. git's pages carry it in
    # their .TH line, but mdoc pages (OpenSSH's) state no version anywhere —
    # the installer is the one who knows, so it stamps what it fetched.
    # Per tool, because the provision tier merges every manual into one
    # tree and git's release is not ssh's.
    (tree / f"VERSION-{driver.name}").write_text(release.version, encoding="utf-8")
    return tree


def _pypi(driver: Driver) -> list[Release]:
    """PyPI's index, minus the versions with no files and the ones older
    than the walk's horizon.

    It keeps yanked and file-less versions, and neither can be installed to
    be read. It also keeps releases that predate `READ_PYTHON` — see
    `READ_PYTHON_SINCE` for why those cannot be read on any interpreter this
    walk will use, and so are not offered rather than walked into holes.

    Both edges of the publishing window are read, and taken across the files
    rather than off the first of them: the index does not promise an order,
    and a release is not always uploaded in one sitting.
    """
    package = driver.provision.target(driver.name)
    index = _index(PYPI.format(package=package))
    found = [
        Release(
            version=version,
            date=min(f["upload_time"][:10] for f in files),
            published=max(f["upload_time"][:10] for f in files),
            requires_python=files[0].get("requires_python") or "",
        )
        for version, files in index.get("releases", {}).items()
        if files and min(f["upload_time"][:10] for f in files) >= READ_PYTHON_SINCE
    ]
    return _order(found)


def install(driver: Driver, release: Release, into: Path) -> Path | None:
    """Install one release into its own directory; return the `bin` to read.

    Per release rather than into a shared prefix, because the point is to read
    *this* version's `--help` and then forget it. `None` means this release
    could not be had — the caller ends that tool's walk rather than leaving a
    hole in the chain.
    """
    kind = driver.provision.kind
    into.mkdir(parents=True, exist_ok=True)
    if kind == "uv":
        return _install_pypi(driver, release, into)
    if kind == "node":
        return _install_npm(driver, release.version, into)
    if kind in ("github", "gitlab", "gitea", "bun"):
        return _install_asset(driver, release, into)
    if kind == "python":
        return _install_python(release.version, into)
    if kind == "docker":
        return _install_docker(driver, release, into)
    if kind == "man":
        return _install_man(driver, release, into)
    return None


def _install_python(version: str, into: Path) -> Path | None:
    """`uv python install` this exact patch, and read where it landed.

    Into a store of its own under the throwaway directory, like every other
    tier — not uv's shared one. A shared store is one lock, and the walk is
    ten concurrent installs: uv queues the waiters, the walk's subprocess
    timeouts kill the queue, and every run scattered a different third of
    the python chain into holes. `UV_NO_CACHE` (set for the whole walk by
    `_sandboxed`) already forces each version to download once per run, so
    a private store costs nothing the shared one was buying — and it is
    discarded with the release, which the shared store never could be.

    The directory uv reports already holds a plain `python` alongside the
    versioned name, which is what the extractor invokes.
    """
    env = {**os.environ, "UV_PYTHON_INSTALL_DIR": str(into / "store")}
    if not _run(["uv", "python", "install", version], env=env):
        return None
    # --no-project: `uv python find` consults the nearest pyproject, and the
    # walk runs inside footman's own checkout — whose `requires-python` has
    # opinions about which interpreter the question should resolve to.
    found = _capture(["uv", "python", "find", "--no-project", version], env=env)
    found = found.strip()
    if not found or not Path(found).exists():
        return None
    return Path(found).parent


def _install_pypi(driver: Driver, release: Release, into: Path) -> Path | None:
    """A venv per release, resolved as the release itself was.

    The plugins a driver declares (pytest's `pytest-cov`) ride along, or the
    reading loses flags the stub records.

    Two pins make an old release readable at all, and neither works without
    the other. `--exclude-newer` at the release's own date keeps its
    dependencies as they were the day it shipped, because a release read
    against today's transitive versions is not being read as itself — twine
    5.1.0 crashes at import under `importlib_metadata` 8. And the
    interpreter is `read_python` of that same date, because those period
    dependencies only have wheels for interpreters that existed back then:
    pinning the date alone leaves a 2022 `cryptography` trying to compile
    against a 2026 CPython.
    """
    package = driver.provision.target(driver.name)
    python = (
        into
        / ("Scripts" if _windows() else "bin")
        / ("python.exe" if _windows() else "python")
    )
    interpreter = read_python(release.requires_python, release.date)
    if not _run(["uv", "venv", "--quiet", "--python", interpreter, str(into)]):
        return None
    wanted = [f"{package}=={release.version}", *driver.provision.plugins]
    argv = ["uv", "pip", "install", "--quiet", "--python", str(python)]
    cutoff = release.published or release.date
    if cutoff:
        # The far edge of the publishing window, not the near one. A release
        # is not always uploaded in one sitting — ninja 1.11.1 took 76 days
        # over its seventeen files, cmake 4.3.1 three — and a cutoff at the
        # near edge filters out the release's own later files, leaving uv to
        # report "no version of cmake==4.3.1" while resolving cmake 4.3.1,
        # or to assemble a partial set that segfaults on --help.
        #
        # Spelled out to the second, and in UTC, because the two clocks
        # disagree: the index reports `upload_time` in UTC, while a bare
        # date reaches uv as local midnight. East of UTC that cutoff lands
        # *before* the end of the UTC day, so a release published in its
        # last hour is filtered out by its own release date — uv 0.11.32,
        # published 23:05Z, against a 23:00Z cutoff in BST. The bug would
        # sleep in CI on a UTC runner and wake on a European laptop.
        argv += ["--exclude-newer", f"{cutoff}T23:59:59Z"]
    if not _run([*argv, *wanted]):
        return None
    return python.parent


def _install_npm(driver: Driver, version: str, into: Path) -> Path | None:
    """`bun add --global` at a pinned version, with the prefix to itself.

    bun is how the node tier is provisioned, so priming borrows it rather than
    adding a second package manager. Without bun on PATH there is nothing to
    install with, and the walk stops.
    """
    import shutil

    # Spawned by the path `which` resolved, never the bare name: the task
    # router's PATH overlay is visible to in-process lookups but not to
    # Windows CreateProcess, whose executable search reads the real process
    # environment — so `["bun", ...]` found by `which` still failed to
    # spawn, and every npm-tier release on the platform read as a hole.
    bun = shutil.which("bun")
    if bun is None:
        return None
    package = driver.provision.target(driver.name)
    env = {
        **os.environ,
        "BUN_INSTALL": str(into),
        "PATH": f"{into / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    if not _run([bun, "add", "--global", f"{package}@{version}"], env=env):
        return None
    return into / "bin"


def _install_asset(driver: Driver, release: Release, into: Path) -> Path | None:
    """Download this release's asset for this platform and unpack it.

    Addressed by the tag the listing recorded, not by one derived from the
    version. A forge tag is `v2.96.0` on one project, `2.96.0` on the next and
    `bun-v1.3.13` on a third, while the binary answers a bare number in every
    case — so guessing covered exactly the spellings someone had already met,
    and bun's whole history was unreachable without anyone noticing, because
    listing it worked and only installing failed. The version is still tried
    as a fallback, for a listing that recorded no tag.
    """
    from livery.toolroom.bench import _provision

    kind = driver.provision.kind
    host = kind if kind in ("gitlab", "gitea") else "github"
    bindir = into / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    for tag in (release.tag, release.version, f"v{release.version}"):
        if not tag:
            continue
        try:
            assets = _provision.assets_for(host, driver.provision.repo, tag)
            _name, url = _provision._pick_asset(assets)
            archive = _provision._download(url, into)
            _provision._extract_binary(archive, driver.name, bindir)
        except _provision.ProvisionError:
            continue
        except (OSError, ValueError):
            return None
        return bindir
    return None


def _capture(argv: list[str], env: dict[str, str] | None = None) -> str:
    """What a command printed, or empty when it could not be run.

    Empty is not "nothing to report": the callers treat a tool they cannot
    read as one they have not looked at, the same as an unreachable index.
    """
    from livery.footman.context import run as _fm_run

    try:
        done = _fm_run(
            argv,
            recorded=False,  # a probe, not part of the run's story
            timeout=TIMEOUT,
            nofail=True,
            # Passed rather than inherited, so footman reads the spawn as
            # deliberate — and so the prefix `prime` puts on `PATH` is what
            # picks the uv that carries the index. A captured spawn gets the
            # hidden console for free now.
            env=env if env is not None else dict(os.environ),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.code == 0 else ""


def _run(argv: list[str], env: dict[str, str] | None = None) -> bool:
    """Whether *argv* succeeded. A fetch step, not a step in the report."""
    from livery.footman.context import run as _fm_run

    try:
        done = _fm_run(argv, recorded=False, timeout=300, nofail=True, env=env)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.code == 0


def _windows() -> bool:
    import sys

    return sys.platform == "win32"
