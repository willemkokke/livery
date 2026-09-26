"""Record a version's release artifacts, one per host, by downloading them.

A tool read from a forge tier has a release with one asset per host,
and node publishes one build per host under its own release index.
The record needs the asset's URL and its sha256 for every host before
the store can supply the version, and the only honest hash is the hash
of bytes the bench had: each asset is picked for its host from the
release's asset list, downloaded once, hashed, landed in the bench's
store so the ingest verification stages it without a second download,
and written on the version line as `artifacts[host]`.

A host the release publishes no asset for is absent from the version:
not a hole, not a refusal. A tool on a package tier (PyPI, npm) has no
artifacts to record, since its installer supplies it.

`fm tools.artifacts <tool>` records a version already read; the
assembler calls the same function for every fresh version of a
forge-tier tool.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from livery.toolroom.store import HOSTS, Artifact, RecordError

if TYPE_CHECKING:
    from livery.toolroom.bench._drivers import Driver
    from livery.toolroom.store import Record, Store

FORGE_TIERS = ("github", "gitlab", "gitea", "bun")
"""The provision tiers whose releases publish per-host assets on a forge."""

ASSET_TIERS = (*FORGE_TIERS, "nodejs")
"""Every tier a version's artifacts can be recorded from: the forges, and
node's own release index."""


class ArtifactError(Exception):
    """A version's artifacts could not be recorded; the message says why."""


@dataclass(frozen=True)
class Recorded:
    """What one version's artifact step found.

    Attributes:
        version: The version recorded.
        found: Per host, the asset's name and its size in bytes.
        absent: The hosts the release publishes no asset for.
    """

    version: str
    found: dict[str, tuple[str, int]]
    absent: tuple[str, ...]

    def lines(self, name: str) -> list[str]:
        """The step as report lines, one per host."""
        out = [f"  {name} {self.version}: {len(self.found)} artifact(s) recorded"]
        for host, (asset, size) in self.found.items():
            out.append(f"    {host}: {asset} ({size // 1024} KB)")
        if self.absent:
            out.append(f"    no asset for {', '.join(self.absent)}")
        return out


def forge_of(driver: Driver) -> str:
    """The forge to list assets from, or empty for a tier that is not a forge."""
    kind = driver.provision.kind
    if kind not in FORGE_TIERS:
        return ""
    return kind if kind in ("gitlab", "gitea") else "github"


def lists_assets(driver: Driver) -> bool:
    """Whether the driver's tier publishes per-host assets to record."""
    return driver.provision.kind in ASSET_TIERS


def _fetch(url: str) -> bytes:
    """The bytes at *url*; the seam a test replaces."""
    from livery.toolroom.store._engine import download

    return download(url)


def record_version(
    record: Record,
    driver: Driver,
    version: str,
    tag: str,
    *,
    store: Store,
    hosts: tuple[str, ...] = HOSTS,
) -> tuple[Record, Recorded]:
    """*record* with *version*'s artifacts for every host that has an asset.

    Lists the release at *tag* (the version when the tag is empty),
    picks one asset per host, downloads and hashes each, lands the
    bytes in *store*, and writes the artifacts on the version line.
    Hosts already recorded on the version are left as they are. The
    record's `hosts` gains every host recorded. The record validates
    itself as it is rebuilt, so a layout the record lacks refuses
    here, naming what is missing, and never on disk.

    Raises:
        ArtifactError: for a driver on a package tier, a version the
            record does not track, a release with no asset for any
            host, or a host whose deployment does not resolve whole.
    """
    from livery.toolroom.bench import _provision

    if not lists_assets(driver):
        raise ArtifactError(
            f"{record.name} is read from the {driver.provision.kind!r} tier, which"
            " has no release assets to record; its installer supplies it"
        )
    if version not in {delta.version for delta in record.deltas}:
        raise ArtifactError(f"{record.name} does not track {version}")
    universal = ""
    if driver.provision.asset:
        # One file for every host, at a versioned address the release
        # lists no asset for: no listing is asked for and no host is
        # absent.
        universal = driver.provision.asset.format(
            repo=driver.provision.repo, tag=tag or version
        )
        assets = [(universal.rsplit("/", 1)[-1], universal)]
    elif driver.provision.kind == "nodejs":
        from livery.toolroom.bench._toolfetch import nodejs_assets

        assets = nodejs_assets(version)
    else:
        forge = forge_of(driver)
        assets = _assets(record.name, forge, driver.provision.repo, version, tag)
    delta = record.delta_for(version)
    artifacts = dict(delta.artifacts)
    found: dict[str, tuple[str, int]] = {}
    absent: list[str] = []
    for host in hosts:
        if host in artifacts:
            continue
        try:
            asset, url = (
                assets[0] if universal else _provision._pick_asset(assets, host=host)
            )
        except _provision.ProvisionError:
            absent.append(host)
            continue
        try:
            data = _fetch(url)
        except OSError as error:
            raise ArtifactError(f"{record.name} {version}: {url}: {error}") from None
        digest = store.objects.put(data)
        artifacts[host] = Artifact(url, digest.encoded)
        found[host] = (asset, len(data))
    if not found and not delta.artifacts:
        raise ArtifactError(
            f"{record.name} {version}: the release at {tag or version} has no asset"
            f" for any of {', '.join(hosts)}; it has {_names(assets)}"
        )
    ordered = {host: artifacts[host] for host in HOSTS if host in artifacts}
    deltas = tuple(
        replace(d, artifacts=ordered) if d.version == version else d
        for d in record.deltas
    )
    known = tuple(host for host in HOSTS if host in record.hosts or host in ordered)
    try:
        # A record validates itself on construction: a host whose deployment
        # does not resolve whole is refused here, with the field named.
        updated = replace(record, hosts=known, deltas=deltas)
    except RecordError as error:
        raise ArtifactError(str(error)) from None
    return updated, Recorded(version, found, tuple(absent))


def _assets(
    name: str, forge: str, repo: str, version: str, tag: str
) -> list[tuple[str, str]]:
    """The release's asset list, addressed by *tag*, else the version's spellings.

    A forge tag is `v2.96.0` on one project and `2.96.0` on the next, so a
    version read from another index (PyPI, before a tool moved) is tried
    both ways. The tag a listing recorded wins when there is one.
    """
    from livery.toolroom.bench import _provision

    tried: list[str] = []
    for candidate in (tag, version, f"v{version}"):
        if not candidate or candidate in tried:
            continue
        tried.append(candidate)
        try:
            return _provision.assets_for(forge, repo, candidate)
        except _provision.ProvisionError:
            continue
    raise ArtifactError(
        f"{name} {version}: no release on {forge} at {', '.join(tried)}"
    )


def _names(assets: list[tuple[str, str]]) -> str:
    return ", ".join(name for name, _url in assets) or "no assets"
