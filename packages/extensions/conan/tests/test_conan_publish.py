"""Publish a conan package: its version homes, its seam, the caches a run collected."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from extensions_conan_workspace import Said, seed_into, skip_unless_installed
from livery.extensions.conan import _package as _conan
from livery.footman import Failed
from livery.workshop._packages import Package
from livery.workshop._registries import RegistryTarget

_FAILURES = (SystemExit, Failed)


def _package(directory: Path, name: str, *extensions: str) -> Package:
    directory.mkdir(parents=True, exist_ok=True)
    return Package(
        directory=directory,
        path=f"packages/{directory.name}",
        name=name,
        depends=(),
        extensions=extensions,
    )


# The conan version homes.


def test_the_conan_stamp_writes_and_refuses(tmp_path: Path) -> None:
    package = _package(tmp_path / "packages" / "lib", "acme-lib", "cmake", "conan")
    conanfile = package.directory / "conanfile.py"
    conanfile.write_text('class L:\n    name = "acme-lib"\n    version = "0.1.0"\n')
    assert _conan.current_version(package) == "0.1.0"
    assert _conan.stamp_version(package).stamp("0.2.0") == ["conanfile.py"]
    assert _conan.current_version(package) == "0.2.0"
    assert _conan.stamp_version(package).stamp("0.2.0") == []  # idempotent
    conanfile.write_text("class L:\n    pass\n")
    with pytest.raises(_FAILURES, match="no version line"):
        _conan.stamp_version(package).stamp("0.3.0")


def test_the_folder_registry_answers_from_saved_archives(tmp_path: Path) -> None:
    target = tmp_path / "share"
    target.mkdir()
    (target / "acme-lib-0.1.0.tgz").touch()
    (target / "acme-lib-0.2.0.tgz").touch()
    (target / "acme-other-9.9.9.tgz").touch()
    registry = _conan.ConanRegistry(
        RegistryTarget(kind="conan", url=str(target), local=True), root=tmp_path
    )
    assert registry.versions("acme-lib") == ("0.1.0", "0.2.0")
    assert registry.versions("acme-ghost") == ()


def test_conan_publish_refuses_without_conan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    package = _package(tmp_path / "packages" / "lib", "acme-lib", "cmake", "conan")
    with pytest.raises(_FAILURES, match="conan is not on"):
        _conan.publish(package, str(tmp_path / "share"), version="0.1.0", local=True)


_REMOTE = "https://forge.example/api/packages/acme/conan"


def _recorded_conan(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[tuple[str, ...], dict[str, str]]]:
    """Fake conan on the remote route; each call's arguments and added variables.

    Conan's own login variables are cleared first, whatever the host has.
    """
    calls: list[tuple[tuple[str, ...], dict[str, str]]] = []

    class _Done:
        code = 0
        stdout = ""
        stderr = ""

    def _fake(cwd: Path, *args: str, env: dict[str, str] | None = None) -> _Done:
        del cwd
        calls.append((args, dict(env or {})))
        return _Done()

    monkeypatch.setattr(_conan, "_conan", _fake)
    for name in (
        "CONAN_LOGIN_USERNAME",
        "CONAN_PASSWORD",
        "CONAN_LOGIN_USERNAME_WORKSHOP",
        "CONAN_PASSWORD_WORKSHOP",
    ):
        monkeypatch.delenv(name, raising=False)
    return calls


def _upload_logins(
    calls: list[tuple[tuple[str, ...], dict[str, str]]],
) -> list[dict[str, str]]:
    return [env for args, env in calls if args[0] == "upload"]


def _answering(
    monkeypatch: pytest.MonkeyPatch, **answers: Said
) -> list[tuple[str, ...]]:
    """Fake conan, each command answered by its first word; every call's arguments.

    A command *answers* does not name succeeds with no output.
    """
    calls: list[tuple[str, ...]] = []

    def _fake(cwd: Path, *args: str, env: dict[str, str] | None = None) -> Said:
        del cwd, env
        calls.append(args)
        return answers.get(args[0], Said())

    monkeypatch.setattr(_conan, "_conan", _fake)
    return calls


# The refusals conan's answers make, before the routes that succeed.


def test_a_save_conan_refuses_is_named_with_its_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _answering(monkeypatch, cache=Said(1, stderr="no such package"))
    package = _package(tmp_path / "packages" / "lib", "acme-lib", "cmake", "conan")
    with pytest.raises(_FAILURES) as refused:
        _conan.save_cache(package, "0.1.0", tmp_path / "dist")
    assert str(refused.value) == (
        "conan cache save (acme-lib/0.1.0) exited 1:\nno such package"
    )


def test_a_folder_publish_conan_refuses_is_named_with_its_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _answering(monkeypatch, cache=Said(1, stdout="saving\n", stderr="disk full"))
    package = _package(tmp_path / "packages" / "lib", "acme-lib", "cmake", "conan")
    with pytest.raises(_FAILURES) as refused:
        _conan.publish(package, str(tmp_path / "share"), version="0.1.0", local=True)
    assert str(refused.value) == (
        "conan cache save (acme-lib/0.1.0) exited 1:\nsaving\ndisk full"
    )


def test_a_remote_conan_will_not_add_refuses_before_the_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _answering(monkeypatch, remote=Said(2, stderr="bad url"))
    package = _package(tmp_path / "packages" / "lib", "acme-lib", "cmake", "conan")
    with pytest.raises(_FAILURES) as refused:
        _conan.publish(package, _REMOTE, version="0.1.0", local=False)
    assert (
        str(refused.value) == f"conan remote add workshop {_REMOTE} exited 2:\nbad url"
    )
    assert [args[0] for args in calls] == ["remote"]


def test_an_upload_the_remote_holds_already_is_walked_past(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _answering(monkeypatch, upload=Said(1, stderr="Recipe already exists"))
    package = _package(tmp_path / "packages" / "lib", "acme-lib", "cmake", "conan")
    assert not _conan.publish(package, _REMOTE, version="0.1.0", local=False)
    assert "  acme-lib: already uploaded; walking past" in capsys.readouterr().out


def test_an_upload_the_remote_refuses_for_a_login_names_conan_s_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _answering(monkeypatch, upload=Said(1, stderr="ERROR: 401 Unauthorized"))
    package = _package(tmp_path / "packages" / "lib", "acme-lib", "cmake", "conan")
    with pytest.raises(_FAILURES) as refused:
        _conan.publish(package, _REMOTE, version="0.1.0", local=False)
    assert str(refused.value) == (
        "conan upload (acme-lib/0.1.0) exited 1:\nERROR: 401 Unauthorized\n"
        "  the remote wants credentials: set CONAN_LOGIN_USERNAME and"
        " CONAN_PASSWORD (conan's own variables) and re-run"
    )


def test_any_other_upload_refusal_is_named_with_its_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _answering(monkeypatch, upload=Said(1, stderr="connection reset"))
    package = _package(tmp_path / "packages" / "lib", "acme-lib", "cmake", "conan")
    with pytest.raises(_FAILURES) as refused:
        _conan.publish(package, _REMOTE, version="0.1.0", local=False)
    assert str(refused.value) == (
        "conan upload (acme-lib/0.1.0) exited 1:\nconnection reset"
    )


@pytest.mark.parametrize(
    "answer",
    [Said(1, stderr="remote down"), Said(stdout="not json")],
    ids=["failed", "unparsed"],
)
def test_a_remote_whose_listing_fails_or_does_not_parse_answers_no_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: Said
) -> None:
    _answering(monkeypatch, list=answer)
    registry = _conan.ConanRegistry(
        RegistryTarget(kind="conan", url=_REMOTE), root=tmp_path
    )
    assert registry.versions("acme-lib") == ()


def test_a_remote_answers_each_listed_reference_s_version_and_skips_a_bare_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    listed: dict[str, dict[str, object]] = {
        "workshop": {"acme-lib": {}, "acme-lib/0.2.0": {}, "acme-lib/0.1.0": {}}
    }
    _answering(monkeypatch, list=Said(stdout=json.dumps(listed)))
    registry = _conan.ConanRegistry(
        RegistryTarget(kind="conan", url=_REMOTE), root=tmp_path
    )
    assert registry.versions("acme-lib") == ("0.1.0", "0.2.0")


def test_a_member_without_a_changelog_releases_with_an_empty_body(
    tmp_path: Path,
) -> None:
    package = _package(tmp_path / "packages" / "lib", "acme-lib", "cmake", "conan")
    assert _conan.changelog_entry(package, "0.1.0") == ""


@pytest.mark.parametrize(
    "own", ["CONAN_PASSWORD", "CONAN_LOGIN_USERNAME", "CONAN_PASSWORD_WORKSHOP"]
)
def test_conans_own_login_wins_over_the_registry_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, own: str
) -> None:
    calls = _recorded_conan(monkeypatch)
    monkeypatch.setenv(own, "chosen")
    package = _package(tmp_path / "packages" / "lib", "acme-lib", "cmake", "conan")
    assert _conan.publish(package, _REMOTE, version="0.1.0", local=False, token="lane")
    assert _upload_logins(calls) == [{}]


def test_an_upload_without_a_token_adds_no_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _recorded_conan(monkeypatch)
    package = _package(tmp_path / "packages" / "lib", "acme-lib", "cmake", "conan")
    assert _conan.publish(package, _REMOTE, version="0.1.0", local=False)
    assert _upload_logins(calls) == [{}]


def test_the_registry_token_logs_in_to_the_workshop_remote_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _recorded_conan(monkeypatch)
    package = _package(tmp_path / "packages" / "lib", "acme-lib", "cmake", "conan")
    assert _conan.publish(package, _REMOTE, version="0.1.0", local=False, token="lane")
    assert _upload_logins(calls) == [
        {"CONAN_LOGIN_USERNAME_WORKSHOP": "workshop", "CONAN_PASSWORD_WORKSHOP": "lane"}
    ]
    # Only the upload logs in: the remote's address is set anonymously.
    assert [env for args, env in calls if args[0] == "remote"] == [{}]


def test_the_publish_seam_hands_the_targets_token_to_the_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    def _publish(
        package: Package, url: str, *, version: str, local: bool, token: str = ""
    ) -> bool:
        seen.append(token)
        return True

    monkeypatch.setattr(_conan, "publish", _publish)
    package = _package(tmp_path / "packages" / "lib", "acme-lib", "cmake", "conan")
    target = RegistryTarget(kind="conan", url=_REMOTE, token="lane")
    assert _conan.publish_artifact(package, tmp_path, version="0.1.0", target=target)
    assert seen == ["lane"]


# A member a job of the run built: the publish job restores every
# host's saved cache the run collected before the upload reads conan's
# cache. The refusals and the cases that restore nothing come first.

#: Three hosts, in the order their saved caches' names sort.
_HOSTS = ("linux-x64", "macos-arm", "windows-x64")


def _collected(package: Package, *hosts: str, version: str = "0.1.0") -> list[Path]:
    """One saved cache per host in *package*'s dist/, as the run collects them.

    Returns the files, in *hosts* order.
    """
    dist = package.directory / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    files = []
    for host in hosts:
        archive = dist / _conan.cache_name(package.name, version, host)
        archive.write_bytes(f"{version} saved on {host}".encode())
        files.append(archive)
    return files


def _restored(calls: list[tuple[tuple[str, ...], dict[str, str]]]) -> list[str]:
    """The files the recorded conan calls restored, in order."""
    return [args[2] for args, _env in calls if args[:2] == ("cache", "restore")]


@pytest.mark.parametrize(
    "held", [("0.2.0",), ()], ids=["another-version-collected", "nothing-collected"]
)
def test_a_prebuilt_member_without_a_cache_for_the_version_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, held: tuple[str, ...]
) -> None:
    calls = _recorded_conan(monkeypatch)
    package = _package(tmp_path / "packages" / "lib", "acme-lib", "cmake", "conan")
    others = [
        archive
        for version in held
        for archive in _collected(package, "linux-x64", version=version)
    ]
    target = RegistryTarget(kind="conan", url=_REMOTE, token="lane")
    with pytest.raises(_FAILURES) as refused:
        _conan.publish_artifact(
            package, tmp_path, version="0.1.0", target=target, prebuilt=True
        )
    # The member, the version, and every file dist/ holds.
    message = str(refused.value)
    assert message.startswith("acme-lib: ")
    assert "0.1.0" in message
    assert all(archive.name in message for archive in others)
    # Nothing restored, and nothing uploaded.
    assert calls == []


def test_a_restore_conan_refuses_stops_the_publish_before_the_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, ...]] = []

    class _Result:
        def __init__(self, code: int, stderr: str = "") -> None:
            self.code = code
            self.stdout = ""
            self.stderr = stderr

    def _fake(cwd: Path, *args: str, env: dict[str, str] | None = None) -> _Result:
        del cwd, env
        calls.append(args)
        # The first restore lands, the second is refused.
        if len(calls) == 1:
            return _Result(0)
        return _Result(1, "ERROR: the archive is damaged")

    monkeypatch.setattr(_conan, "_conan", _fake)
    package = _package(tmp_path / "packages" / "lib", "acme-lib", "cmake", "conan")
    first, second = _collected(package, "linux-x64", "macos-arm")
    target = RegistryTarget(kind="conan", url=_REMOTE)
    with pytest.raises(_FAILURES) as refused:
        _conan.publish_artifact(
            package, tmp_path, version="0.1.0", target=target, prebuilt=True
        )
    # Conan's own words carry, and nothing uploads after the refusal.
    assert "the archive is damaged" in str(refused.value)
    assert calls == [
        ("cache", "restore", str(first)),
        ("cache", "restore", str(second)),
    ]


def test_a_saved_cache_for_another_version_is_not_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _recorded_conan(monkeypatch)
    package = _package(tmp_path / "packages" / "lib", "acme-lib", "cmake", "conan")
    (wanted,) = _collected(package, "linux-x64")
    _collected(package, "linux-x64", version="0.2.0")
    # A version whose own text goes on after the one released, as a
    # pre-release's does.
    _collected(package, "linux-x64", version="0.1.0-rc1")
    target = RegistryTarget(kind="conan", url=_REMOTE)
    assert _conan.publish_artifact(
        package, tmp_path, version="0.1.0", target=target, prebuilt=True
    )
    assert _restored(calls) == [str(wanted)]


def test_every_host_s_saved_cache_is_restored_before_the_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _recorded_conan(monkeypatch)
    package = _package(tmp_path / "packages" / "lib", "acme-lib", "cmake", "conan")
    collected = _collected(package, *reversed(_HOSTS))
    target = RegistryTarget(kind="conan", url=_REMOTE)
    assert _conan.publish_artifact(
        package, tmp_path, version="0.1.0", target=target, prebuilt=True
    )
    # Every host's file, in name order, and each of them before the upload.
    assert _restored(calls) == sorted(str(archive) for archive in collected)
    assert [args[:2] for args, _env in calls] == [
        *[("cache", "restore")] * len(_HOSTS),
        ("remote", "add"),
        ("upload", "acme-lib/0.1.0"),
    ]


def test_a_member_this_job_built_restores_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The build put the package in this job's cache; a file in dist/ stays there."""
    calls = _recorded_conan(monkeypatch)
    package = _package(tmp_path / "packages" / "lib", "acme-lib", "cmake", "conan")
    _collected(package, *_HOSTS)
    target = RegistryTarget(kind="conan", url=_REMOTE)
    assert _conan.publish_artifact(package, tmp_path, version="0.1.0", target=target)
    assert _restored(calls) == []
    assert [args[0] for args, _env in calls] == ["remote", "upload"]


def test_the_releases_route_attaches_a_prebuilt_member_s_files_and_restores_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route uploads the files in dist/, never conan's cache."""
    calls = _recorded_conan(monkeypatch)
    attached: list[str] = []

    def _attach(package: Package, root: Path, *, version: str) -> bool:
        del root
        attached.append(f"{package.name} {version}")
        return True

    monkeypatch.setattr(_conan, "publish_to_releases", _attach)
    package = _package(tmp_path / "packages" / "lib", "acme-lib", "cmake", "conan")
    _collected(package, *_HOSTS)
    target = RegistryTarget(kind="conan", url="fake://acme/ws", releases=True)
    assert _conan.publish_artifact(
        package, tmp_path, version="0.1.0", target=target, prebuilt=True
    )
    assert attached == ["acme-lib 0.1.0"]
    assert calls == []


@pytest.mark.parametrize("route", ["remote", "folder"])
def test_a_prebuilt_member_s_caches_are_restored_and_then_uploaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    calls = _recorded_conan(monkeypatch)
    package = _package(tmp_path / "packages" / "lib", "acme-lib", "cmake", "conan")
    collected = _collected(package, "linux-x64", "macos-arm")
    if route == "remote":
        target = RegistryTarget(kind="conan", url=_REMOTE)
        uploads = [("remote", "add"), ("upload", "acme-lib/0.1.0")]
    else:
        target = RegistryTarget(kind="conan", url=str(tmp_path / "share"), local=True)
        uploads = [("cache", "save")]
    assert _conan.publish_artifact(
        package, tmp_path, version="0.1.0", target=target, prebuilt=True
    )
    assert [args[:2] for args, _env in calls] == [
        ("cache", "restore"),
        ("cache", "restore"),
        *uploads,
    ]
    assert _restored(calls) == [str(archive) for archive in collected]


# The armed proofs: a real conan against the folder target and the
# rig's gitea registry.

_CONAN_GAP = (
    ""
    if all(shutil.which(t) for t in ("conan", "cmake", "ninja", "cc"))
    else "conan, cmake, ninja, and cc must resolve"
)
needs_conan = pytest.mark.skipif(bool(_CONAN_GAP), reason=_CONAN_GAP)

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def conan_homes() -> Iterator[Callable[[], Path]]:
    """Make conan homes under a short root, each removed afterwards.

    conan nests a package's folders several levels deep under its home,
    and pytest's per-test directory is already about 110 characters deep
    on a Windows runner, so a home there can pass Windows' 260-character
    path limit, depending on the worker and the test's name.
    """
    made: list[Path] = []

    def make() -> Path:
        home = Path(tempfile.mkdtemp(prefix="ch"))
        made.append(home)
        return home

    yield make
    for home in made:
        shutil.rmtree(home, ignore_errors=True)


def _render_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path
) -> Package:
    """A rendered cpp-conan package, built into an isolated CONAN_HOME."""
    from livery.workshop._identity import project_facts

    monkeypatch.setenv("CONAN_HOME", str(home))
    destination = tmp_path / "packages" / "geometry"
    answers = project_facts(ROOT)
    seed_into(
        destination,
        "cpp-package",
        {
            "package_name": "acme-geometry",
            "package_description": "acme-geometry: a native library.",
            "namespace_package": "acme",
            "author_name": answers["author_name"],
            "author_email": answers["author_email"],
            "copyright_year": answers["copyright_year"],
            "project_name": "acme",
        },
    )
    package = _package(destination, "acme-geometry", "cmake", "conan")

    _conan.build(package, tmp_path)
    return package


@needs_conan
def test_the_folder_target_round_trips(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    conan_homes: Callable[[], Path],
) -> None:
    skip_unless_installed("cmake")
    package = _render_library(tmp_path, monkeypatch, conan_homes())
    share = tmp_path / "share"
    assert _conan.publish(package, str(share), version="0.0.0", local=True)
    registry = _conan.ConanRegistry(
        RegistryTarget(kind="conan", url=str(share), local=True), root=tmp_path
    )
    assert registry.versions("acme-geometry") == ("0.0.0",)
    # A clean home restores the package from the saved archive alone.
    monkeypatch.setenv("CONAN_HOME", str(conan_homes()))
    restored = subprocess.run(
        [
            "conan",
            "cache",
            "restore",
            str(share / "acme-geometry-0.0.0.tgz"),
        ],
        capture_output=True,
        text=True,
    )
    assert restored.returncode == 0, restored.stderr
    listed = subprocess.run(
        ["conan", "list", "acme-geometry/*"], capture_output=True, text=True
    )
    assert "0.0.0" in listed.stdout


@needs_conan
def test_the_rig_conan_registry_round_trips(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    conan_homes: Callable[[], Path],
) -> None:
    skip_unless_installed("cmake")
    import os
    import urllib.request

    url = os.environ.get("GITEA_URL", "http://localhost:3000")
    token = os.environ.get("GITEA_TOKEN", "")
    if not token:
        pytest.skip("GITEA_TOKEN unset: the rig's conan registry needs it")
    try:
        urllib.request.urlopen(url, timeout=3)
    except OSError:
        pytest.skip(f"the rig at {url} is not answering")
    package = _render_library(tmp_path, monkeypatch, conan_homes())
    remote = f"{url.rstrip('/')}/api/packages/livery-admin/conan"
    monkeypatch.setenv("CONAN_LOGIN_USERNAME", "livery-admin")
    monkeypatch.setenv("CONAN_PASSWORD", token)
    login = subprocess.run(
        ["conan", "remote", "add", _conan.CONAN_REMOTE, remote, "--force"],
        capture_output=True,
        text=True,
    )
    assert login.returncode == 0, login.stderr
    auth = subprocess.run(
        ["conan", "remote", "login", _conan.CONAN_REMOTE, "livery-admin"],
        capture_output=True,
        text=True,
    )
    assert auth.returncode == 0, auth.stderr
    assert _conan.publish(package, remote, version="0.0.0", local=False) in (
        True,
        False,  # a re-run walks past the earlier upload
    )
    registry = _conan.ConanRegistry(
        RegistryTarget(kind="conan", url=remote), root=tmp_path
    )
    assert "0.0.0" in registry.versions("acme-geometry")
    # The consumer proof: a clean home installs it back from the rig.
    monkeypatch.setenv("CONAN_HOME", str(conan_homes()))
    subprocess.run(["conan", "profile", "detect", "--exist-ok"], capture_output=True)
    subprocess.run(
        ["conan", "remote", "add", _conan.CONAN_REMOTE, remote, "--force"],
        capture_output=True,
    )
    subprocess.run(
        ["conan", "remote", "login", _conan.CONAN_REMOTE, "livery-admin"],
        capture_output=True,
        text=True,
    )
    installed = subprocess.run(
        [
            "conan",
            "install",
            "--requires=acme-geometry/0.0.0",
            "-r",
            _conan.CONAN_REMOTE,
            "--build=missing",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert installed.returncode == 0, f"{installed.stdout}\n{installed.stderr}"


@needs_conan
def test_two_saved_caches_holding_one_package_restore_and_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    conan_homes: Callable[[], Path],
) -> None:
    """Two hosts' files can hold one package, and both restore into one cache.

    A header library's package id is the same on every host, so each
    leg saves the same package. The publish job restores every file
    into a fresh cache, so the second restore finds the package there
    already; the folder target then saves it from that cache.
    """
    skip_unless_installed("cmake")
    import json

    package = _render_library(tmp_path, monkeypatch, conan_homes())
    saved = _conan.save_cache(package, "0.0.0", package.directory / "dist")
    other = next(
        host
        for host in _HOSTS
        if _conan.cache_name("acme-geometry", "0.0.0", host) != saved.name
    )
    shutil.copyfile(
        saved, saved.with_name(_conan.cache_name("acme-geometry", "0.0.0", other))
    )
    assert len(_conan.collected_caches(package, "0.0.0")) == 2
    monkeypatch.setenv("CONAN_HOME", str(conan_homes()))
    share = tmp_path / "share"
    target = RegistryTarget(kind="conan", url=str(share), local=True)
    assert _conan.publish_artifact(
        package, tmp_path, version="0.0.0", target=target, prebuilt=True
    )
    # A clean home restores the one package from the folder target alone.
    monkeypatch.setenv("CONAN_HOME", str(conan_homes()))
    restored = subprocess.run(
        ["conan", "cache", "restore", str(share / "acme-geometry-0.0.0.tgz")],
        capture_output=True,
        text=True,
    )
    assert restored.returncode == 0, restored.stderr
    listed = subprocess.run(
        ["conan", "list", "acme-geometry/0.0.0:*", "--format=json"],
        capture_output=True,
        text=True,
    )
    assert listed.returncode == 0, listed.stderr
    revisions = json.loads(listed.stdout)["Local Cache"]["acme-geometry/0.0.0"][
        "revisions"
    ]
    packages = [pid for rev in revisions.values() for pid in rev.get("packages", {})]
    assert len(packages) == 1
