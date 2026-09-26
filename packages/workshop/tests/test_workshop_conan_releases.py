"""The conan releases route: a member's caches ride its own release.

Refusals first: a wave with no saved cache, a restore with nothing to
restore, and a digest that does not match the bytes. Then the round
trip through the fake forge.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from livery.footman import Failed
from livery.forge import RegistryKind, Repository, Unsupported
from livery.forge.testing import FakeForge
from livery.workshop._backends import _cpp_conan
from livery.workshop._packages import Package
from livery.workshop._registries import RegistryTarget, resolve_registry

_FAILURES = (SystemExit, Failed)

OWNER = "acme"
REPO = "acme-workspace"


def _workspace(tmp_path: Path) -> Path:
    """A workspace with one conan member, acme-geometry under packages/."""
    root = tmp_path / "ws"
    (root / "packages" / "geometry").mkdir(parents=True)
    (root / "workshop.toml").write_text(
        '[workspace]\n[forge]\nkind = "gitea"\nowner = "acme"\n'
    )
    (root / "packages" / "geometry" / "workshop.toml").write_text(
        'type = "cpp-conan"\nname = "acme-geometry"\n'
    )
    (root / "packages" / "geometry" / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [0.3.0]\n\n- the area function\n\n## [0.2.0]\n\n- older\n"
    )
    return root


def _member(root: Path) -> Package:
    return Package(
        directory=root / "packages" / "geometry",
        path="packages/geometry",
        name="acme-geometry",
        type="cpp-conan",
        depends=(),
    )


class _NoConanRegistry(FakeForge):
    """A forge that hosts no conan registry, GitHub's shape.

    The route under test exists for exactly this forge: a conan
    registry of its own would win the ladder's rung above.
    """

    def registry_url(self, kind: RegistryKind, owner: str) -> str:
        """Every kind but conan, which this forge does not host."""
        if kind == "conan":
            raise Unsupported("this forge hosts no conan registry")
        return super().registry_url(kind, owner)


def _forge(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeForge, Repository]:
    """A fake forge with this workspace's repository, wired into the lane."""
    fake: FakeForge = _NoConanRegistry()
    fake.create_repo(OWNER, REPO)
    repository = fake.repository(OWNER, REPO)
    monkeypatch.setattr(
        "livery.workshop._forge_lane.this_repository", lambda _root: repository
    )
    monkeypatch.setattr("livery.workshop._forge_lane.this_forge", lambda _root: fake)
    return fake, repository


def _released(fake: FakeForge, repository: Repository, version: str) -> str:
    """A pushed tag with its release, as the wave leaves them; the tag."""
    tag = f"packages/geometry/v{version}"
    fake.create_tag(OWNER, REPO, tag)
    repository.release.create(tag, name=f"acme-geometry {version}")
    return tag


def _saved(root: Path, version: str, host: str = "") -> Path:
    """One leg's saved cache in the member's dist/; the file."""
    dist = root / "packages" / "geometry" / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    archive = dist / _cpp_conan.cache_name("acme-geometry", version, host)
    archive.write_bytes(f"cache of {version} for {host or 'this host'}".encode())
    return archive


def _another_host() -> str:
    """A host key that is not this machine's."""
    mine = _cpp_conan.cache_name("acme-geometry", "0.0.0")
    for host in ("linux-arm", "windows-x64", "macos-x64"):
        if _cpp_conan.cache_name("acme-geometry", "0.0.0", host) != mine:
            return host
    raise AssertionError("every host key matched this machine's")


# The refusals.


def test_the_publish_refuses_when_no_leg_saved_a_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    _forge(monkeypatch)
    with pytest.raises(_FAILURES, match="no saved conan cache"):
        _cpp_conan.publish_to_releases(_member(root), root, version="0.3.0")


def test_the_restore_refuses_a_name_no_member_carries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    _forge(monkeypatch)
    with pytest.raises(_FAILURES, match="not a member of this workspace"):
        _cpp_conan.restore_from_releases(root, "acme-ghost", "0.3.0", cwd=root)


def test_the_restore_refuses_when_the_release_carries_no_cache_for_this_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    fake, repository = _forge(monkeypatch)
    tag = _released(fake, repository, "0.3.0")
    other = _cpp_conan.cache_name("acme-geometry", "0.3.0", _another_host())
    repository.release.upload_asset(tag, other, b"another host's cache")
    with pytest.raises(_FAILURES, match="carries no conan cache for this host") as bad:
        _cpp_conan.restore_from_releases(root, "acme-geometry", "0.3.0", cwd=root)
    assert other in str(bad.value)


def test_a_digest_that_does_not_match_the_bytes_refuses() -> None:
    with pytest.raises(_FAILURES, match="does not match the release's digest"):
        _cpp_conan.verify_digest("c.tgz", b"bytes", "sha256:" + "0" * 64)


def test_a_digest_algorithm_the_restore_cannot_check_refuses() -> None:
    with pytest.raises(_FAILURES, match="verifies sha256"):
        _cpp_conan.verify_digest("c.tgz", b"bytes", "md5:abc")


def test_no_digest_is_used_and_said(capsys: pytest.CaptureFixture[str]) -> None:
    _cpp_conan.verify_digest("c.tgz", b"bytes", "")
    assert "not verified" in capsys.readouterr().out


def test_a_matching_digest_passes() -> None:
    data = b"the cache"
    _cpp_conan.verify_digest(
        "c.tgz", data, f"sha256:{hashlib.sha256(data).hexdigest()}"
    )


def test_a_cache_whose_bytes_changed_under_the_tag_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    fake, repository = _forge(monkeypatch)
    tag = _released(fake, repository, "0.2.0")
    name = _cpp_conan.cache_name("acme-geometry", "0.2.0")
    repository.release.upload_asset(tag, name, b"the saved cache")
    monkeypatch.setattr(
        type(repository.release),
        "download_asset",
        lambda _self, _tag, _name: b"something else",
    )
    with pytest.raises(_FAILURES, match="does not match the release's digest"):
        _cpp_conan.restore_from_releases(root, "acme-geometry", "0.2.0", cwd=root)


# The round trip.


def test_the_publish_creates_the_release_and_attaches_every_host_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _workspace(tmp_path)
    fake, repository = _forge(monkeypatch)
    tag = "packages/geometry/v0.3.0"
    fake.create_tag(OWNER, REPO, tag)
    _saved(root, "0.3.0")
    _saved(root, "0.3.0", _another_host())
    _saved(root, "0.2.0")  # another version's cache stays where it is
    assert _cpp_conan.publish_to_releases(_member(root), root, version="0.3.0")
    release = repository.release.get(tag)
    assert release is not None
    assert release.body == "- the area function"
    assets = repository.release.assets(tag)
    assert sorted(asset.name for asset in assets) == sorted(
        [
            _cpp_conan.cache_name("acme-geometry", "0.3.0"),
            _cpp_conan.cache_name("acme-geometry", "0.3.0", _another_host()),
        ]
    )
    assert all(asset.digest.startswith("sha256:") for asset in assets)
    # Idempotent: a re-run attaches nothing and says so.
    capsys.readouterr()
    assert not _cpp_conan.publish_to_releases(_member(root), root, version="0.3.0")
    assert "already attached" in capsys.readouterr().out


def test_the_probe_answers_only_versions_whose_release_carries_a_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    fake, repository = _forge(monkeypatch)
    # A tag with no release at all.
    fake.create_tag(OWNER, REPO, "packages/geometry/v0.1.0")
    # A release the wave cut before the upload landed.
    _released(fake, repository, "0.2.0")
    # A release with this host's cache on it.
    tag = _released(fake, repository, "0.3.0")
    repository.release.upload_asset(
        tag, _cpp_conan.cache_name("acme-geometry", "0.3.0"), b"cache"
    )
    target = RegistryTarget(kind="conan", url="fake://acme", releases=True)
    registry = _cpp_conan.ConanRegistry(target, root=root)
    assert registry.versions("acme-geometry") == ("0.3.0",)
    assert registry.versions("acme-ghost") == ()


def test_the_restore_verifies_the_digest_and_hands_the_file_to_conan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _workspace(tmp_path)
    fake, repository = _forge(monkeypatch)
    tag = _released(fake, repository, "0.2.0")
    name = _cpp_conan.cache_name("acme-geometry", "0.2.0")
    repository.release.upload_asset(tag, name, b"the saved cache")
    seen: list[tuple[str, bytes]] = []

    class _Done:
        code = 0
        stdout = ""
        stderr = ""

    def _fake_conan(cwd: Path, *args: str, env: object = None) -> _Done:
        del cwd, env
        assert args[:2] == ("cache", "restore")
        seen.append((args[-1], Path(args[-1]).read_bytes()))
        return _Done()

    monkeypatch.setattr(_cpp_conan, "_conan", _fake_conan)
    _cpp_conan.restore_from_releases(root, "acme-geometry", "0.2.0", cwd=root)
    assert [payload for _path, payload in seen] == [b"the saved cache"]
    assert Path(seen[0][0]).name == name
    assert "restored from packages/geometry/v0.2.0" in capsys.readouterr().out


def test_the_ladder_answers_the_forge_releases_when_it_hosts_no_conan_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    _forge(monkeypatch)
    monkeypatch.delenv("CONAN_REMOTE_URL", raising=False)
    target = resolve_registry(root, "conan")
    assert target.releases and not target.local
    assert target.url == f"fake://{OWNER}/{REPO}"


def test_the_changelog_entry_is_the_release_body(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    package = _member(root)
    assert _cpp_conan.changelog_entry(package, "0.3.0") == "- the area function"
    assert _cpp_conan.changelog_entry(package, "0.2.0") == "- older"
    assert _cpp_conan.changelog_entry(package, "9.9.9") == ""


def test_the_waves_floor_check_reads_the_attached_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A floor no release carries stops the wave before anything uploads."""
    from livery.workshop._packages import Edge
    from livery.workshop._publish import floor_probe

    root = _workspace(tmp_path)
    fake, repository = _forge(monkeypatch)
    library = _member(root)
    extension = Package(
        directory=root / "packages" / "ext",
        path="packages/ext",
        name="acme-ext",
        type="python-nanobind",
        depends=(Edge(path="packages/geometry", kind="build", floor="0.1.0"),),
    )
    target = RegistryTarget(kind="conan", url="fake://acme", releases=True)
    by_path = {library.path: library}

    def registry_for(_package: Package) -> _cpp_conan.ConanRegistry:
        return _cpp_conan.ConanRegistry(target, root=root)

    with pytest.raises(_FAILURES, match=r"floors acme-geometry at 0\.1\.0"):
        floor_probe(extension, by_path, registry_for)
    tag = _released(fake, repository, "0.1.0")
    repository.release.upload_asset(
        tag, _cpp_conan.cache_name("acme-geometry", "0.1.0"), b"cache"
    )
    floor_probe(extension, by_path, registry_for)
