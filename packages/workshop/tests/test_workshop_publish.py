"""The publish wave, forced: refusals, receipts, overlap, the diamond.

Build and upload are stubbed at their seams; the wave's own edges,
eligibility by receipt, failure isolation, walk-past, the probes and
backstops, are what these force.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from livery.footman import Failed
from livery.workshop._git_ops import GitOps
from livery.workshop._packages import Package, discover_packages
from livery.workshop._publish import (
    Receipt,
    discover_release,
    floor_probe,
    movement_check,
    probe_until_served,
    publish_release,
)
from workshop_seeds import Seeds, _seed_home, seed_copier  # noqa: F401

_FAILURES = (SystemExit, Failed)


class LedgerRegistry:
    """A registry whose answers and probe timing the test scripts."""

    def __init__(self) -> None:
        self.served: dict[str, set[str]] = {}
        self.probes: list[tuple[float, str]] = []
        self.delay = 0.0

    def serve(self, name: str, version: str) -> None:
        self.served.setdefault(name, set()).add(version)

    def versions(self, name: str) -> tuple[str, ...]:
        self.probes.append((time.monotonic(), name))
        if self.delay:
            time.sleep(self.delay)
        return tuple(sorted(self.served.get(name, set())))


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True)


def _member(root: Path, name: str, *, floors_on: tuple[str, ...] = ()) -> None:
    directory = root / "packages" / name
    (directory / "src" / "livery" / name).mkdir(parents=True)
    depends = "".join(
        f'[[depends]]\npath = "packages/{dep}"\nkind = "build"\nfloor = "0.3.0"\n'
        for dep in floors_on
    )
    requirements = ", ".join(f'"livery-{dep}>=0.3.0"' for dep in floors_on)
    (directory / "workshop.toml").write_text(
        f'type = "python"\nname = "livery-{name}"\n{depends}'
    )
    (directory / "pyproject.toml").write_text(
        f'[project]\nname = "livery-{name}"\nversion = "0.3.0"\n'
        f"dependencies = [{requirements}]\n"
    )
    (directory / "CHANGELOG.md").write_text("# Changelog\n")
    (directory / "src" / "livery" / name / "__init__.py").write_text(
        '__version__ = "0.3.0"\n'
    )


def _squash(root: Path, members: tuple[str, ...], *, mined_at: str = "") -> str:
    listed = ", ".join(f"livery-{m} v0.3.0" for m in members)
    for member in members:
        (root / "packages" / member / "CHANGELOG.md").write_text(
            "# Changelog\n\n## [0.3.0]\n\n- x\n"
        )
    _git(root, "add", "-A")
    point = mined_at or (
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )
    _git(
        root,
        "commit",
        "-m",
        f"chore(release): released {listed}",
        "-m",
        f"Mined-At: {point}",
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _seed(base: Path) -> None:
    """The diamond workspace, base, two legs, an apex, built once per session."""
    origin = base / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "--initial-branch=main")
    root = base / "ws"
    _git(base, "clone", str(origin), "ws")
    _git(root, "config", "user.email", "t@livery.local")
    _git(root, "config", "user.name", "T")
    (root / "workshop.toml").write_text("[workspace]\n")
    _member(root, "base")
    _member(root, "left", floors_on=("base",))
    _member(root, "right", floors_on=("base",))
    _member(root, "apex", floors_on=("left", "right"))
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "chore: seed")
    _git(root, "push", "-u", "origin", "main")


@pytest.fixture
def train(seeds: Seeds, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A squashed release of a diamond: base, two legs, an apex."""
    root = seeds("publish", _seed) / "ws"
    git = GitOps(root)
    registry = LedgerRegistry()
    starts: dict[str, float] = {}
    spans: dict[str, tuple[float, float]] = {}

    def _fake_build(package: Package, _root: Path, *, epoch: int = 0) -> Path:
        starts[package.directory.name] = time.monotonic()
        time.sleep(0.05)
        dist = package.directory / "dist"
        dist.mkdir(exist_ok=True)
        # The identity guard reads real filenames, so the stub
        # leaves a pure-tagged wheel where a python build would.
        wheel = package.name.replace("-", "_")
        (dist / f"{wheel}-0.3.0-py3-none-any.whl").touch()
        return dist

    def _fake_publish(package: Package, **kwargs: object) -> bool:
        name = package.directory.name
        registry.serve(package.name, "0.3.0")
        spans[name] = (starts[name], time.monotonic())
        return True

    monkeypatch.setattr("livery.workshop._backends._python.build", _fake_build)
    monkeypatch.setattr("livery.workshop._publish.publish_wheels", _fake_publish)
    monkeypatch.setattr("livery.workshop._publish.PROBE_POLL", 0.01, raising=False)
    return root, git, registry, spans


def test_publish_refuses_without_an_upload_address(tmp_path: Path) -> None:
    # The refusal first: uv's silent default is PyPI, so an empty
    # publish address must die loudly before any upload, teaching
    # the declaration that fixes it.
    from livery.workshop._publish import publish_wheels

    package = Package(
        directory=tmp_path,
        path="packages/thing",
        name="thing",
        type="python",
        depends=(),
    )
    with pytest.raises(_FAILURES) as caught:
        publish_wheels(package, index_url="", token="t")
    message = str(caught.value)
    assert "never defaulted" in message
    assert "[registries.python] publish" in message


def test_a_garbled_manifest_falls_back_to_the_diff() -> None:
    # The fallback first: unreadable content answers None and the
    # caller keeps the diff-derived discovery for legacy squashes.
    from livery.workshop._publish import read_manifest

    assert read_manifest("not json") is None
    assert read_manifest("{}") is None
    assert read_manifest('{"members": []}') is None
    assert read_manifest('{"members": [{"dir": "core"}]}') is None


def test_the_manifest_names_the_set_the_diff_cannot() -> None:
    from livery.workshop._publish import read_manifest

    pairs = read_manifest(
        '{"schema": 1, "members": ['
        '{"dir": "core", "name": "livery-core", "version": "0.3.0"},'
        '{"dir": "tool", "name": "livery-tool", "version": "0.3.0"}]}'
    )
    assert pairs == (("core", "0.3.0"), ("tool", "0.3.0"))


def test_discovery_refuses_what_is_not_a_release(train) -> None:
    # The fallback first: a commit touching no member changelog is
    # not a release squash, whatever its title says.
    root, git, _registry, _spans = train
    (root / "notes.txt").write_text("just notes\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "chore(release): released livery-base v0.3.0")
    with pytest.raises(_FAILURES) as caught:
        discover_release(root, git, git.head_sha())
    assert "not a release squash" in str(caught.value)


def test_discovery_reads_members_from_the_squash_content(train) -> None:
    root, git, _registry, _spans = train
    sha = _squash(root, ("base", "left"))
    discovered = discover_release(root, git, sha)
    assert [(p.directory.name, v) for p, v in discovered] == [
        ("base", "0.3.0"),
        ("left", "0.3.0"),
    ]


def test_a_version_the_index_already_serves_is_walked_past_before_upload(
    train, capsys: pytest.CaptureFixture[str]
) -> None:
    # The recovery first: a died receipt re-run must reach the tag
    # without repeating an upload it may not be able to make.
    root, git, registry, spans = train
    registry.serve("livery-base", "0.3.0")
    sha = _squash(root, ("base", "left"))
    receipts = publish_release(
        root, git, lambda _p: registry, ref=sha, index_url="https://idx.example/"
    )
    by_name = {r.package.directory.name: r for r in receipts}
    assert by_name["base"].published is False
    assert by_name["left"].published is True
    assert "base" not in spans  # no upload ran for it
    assert "livery-base v0.3.0: already served; walking past the upload" in (
        capsys.readouterr().out
    )
    tags = subprocess.run(
        ["git", "tag", "--list", "packages/*"], cwd=root, capture_output=True, text=True
    ).stdout.split()
    assert "packages/base/v0.3.0" in tags and "packages/left/v0.3.0" in tags


def test_pending_release_wave_sees_only_an_unwaved_squash(train) -> None:
    # The fallbacks first: plain history answers None, and a squash
    # whose receipts are all cut answers None; only missing receipts
    # name the recovery.
    from livery.workshop._release_driver import pending_release_wave

    root, git, _registry, _spans = train
    assert pending_release_wave(root, git) is None
    sha = _squash(root, ("base", "left"))
    pending = pending_release_wave(root, git)
    assert pending is not None
    found, missing = pending
    assert found == sha
    assert missing == ("packages/base/v0.3.0", "packages/left/v0.3.0")
    # A later release that is fully cut does not strand the older
    # died wave: the oldest squash with an uncut receipt answers.
    later = _squash(root, ("right",))
    _git(root, "tag", "-a", "packages/right/v0.3.0", "-m", "right", later)
    _git(root, "push", "origin", "--tags")
    pending = pending_release_wave(root, git)
    assert pending is not None and pending[0] == sha
    for tag in missing:
        _git(root, "tag", "-a", tag, "-m", tag, sha)
    _git(root, "push", "origin", "--tags")
    assert pending_release_wave(root, git) is None


def test_uncut_receipts_are_matched_to_the_requested_set(train) -> None:
    # The fallback first: an uncut receipt outside the set names no
    # recovery for it, so the requested release goes ahead.
    from livery.workshop._packages import discover_packages
    from livery.workshop._release_driver import uncut_in_set

    root = train[0]
    by_name = {p.directory.name: p for p in discover_packages(root)}
    missing = ("packages/base/v0.3.0", "packages/left/v0.3.0")
    assert uncut_in_set(missing, (by_name["right"],)) == ()
    assert uncut_in_set(missing, (by_name["left"], by_name["right"])) == (
        "packages/left/v0.3.0",
    )
    assert uncut_in_set((), (by_name["left"],)) == ()


def test_the_recovery_finds_the_requested_sets_own_uncut_squash(train) -> None:
    # Two uncut squashes at once, the older one another set's: the
    # set-blind selection answers the older, the set's own selection
    # answers the squash whose receipts touch the set, and a set with
    # no uncut receipt anywhere gets None while the others still
    # stand.
    from livery.workshop._packages import discover_packages
    from livery.workshop._release_driver import (
        pending_release_wave,
        pending_release_wave_for,
        pending_release_waves,
    )

    root, git, _registry, _spans = train
    by_name = {p.directory.name: p for p in discover_packages(root)}
    assert pending_release_wave_for(root, git, (by_name["base"],)) is None
    older = _squash(root, ("base",))
    newer = _squash(root, ("left", "right"))
    assert [sha for sha, _ in pending_release_waves(root, git)] == [older, newer]
    assert pending_release_wave(root, git) == (older, ("packages/base/v0.3.0",))
    assert pending_release_wave_for(root, git, (by_name["left"],)) == (
        newer,
        ("packages/left/v0.3.0", "packages/right/v0.3.0"),
    )
    own = pending_release_wave_for(root, git, (by_name["base"],))
    assert own is not None and own[0] == older
    assert pending_release_wave_for(root, git, (by_name["apex"],)) is None


def test_discovery_ignores_rider_files_and_survives_a_wrong_title(train) -> None:
    # hse's shape: the title is presentation. A rider file in the
    # squash and a hand-mangled title change nothing about what the
    # changed changelogs state.
    root, git, _registry, _spans = train
    (root / "packages" / "base" / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [0.3.0]\n\n- hand-edited entry\n"
    )
    (root / "rider.txt").write_text("a rider\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "chore(release): the lock records something")
    discovered = discover_release(root, git, git.head_sha())
    assert [(p.directory.name, v) for p, v in discovered] == [("base", "0.3.0")]


def test_discovery_refuses_an_unreadable_heading(train) -> None:
    root, git, _registry, _spans = train
    (root / "packages" / "base" / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n- pending\n"
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "chore: mangle")
    with pytest.raises(_FAILURES) as caught:
        discover_release(root, git, git.head_sha())
    assert "no" in str(caught.value) and "heading" in str(caught.value)


def test_the_wave_runs_independent_legs_abreast_and_the_apex_waits(
    train,
) -> None:
    root, git, registry, spans = train
    sha = _squash(root, ("base", "left", "right", "apex"))
    receipts = publish_release(
        root,
        git,
        lambda _p: registry,
        ref=sha,
        probe_timeout=5,
        probe_poll=0.01,
    )
    assert [r.package.directory.name for r in receipts] == [
        "base",
        "left",
        "right",
        "apex",
    ]
    tags = git.tags()
    for member in ("base", "left", "right", "apex"):
        assert f"packages/{member}/v0.3.0" in tags
    # The two legs overlapped: each started before the other finished.
    left, right = spans["left"], spans["right"]
    assert left[0] < right[1] and right[0] < left[1]
    # The apex waited for both legs' receipts.
    assert spans["apex"][0] >= max(left[1], right[1]) - 0.01


def test_a_failed_member_stops_only_its_dependents(
    train, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, git, registry, spans = train
    sha = _squash(root, ("base", "left", "right", "apex"))

    real_serve = registry.serve

    def _left_dies(package: Package, **kwargs: object) -> bool:
        name = package.directory.name
        if name == "left":
            raise SystemExit("left's upload was rejected")
        real_serve(package.name, "0.3.0")
        spans[name] = (0.0, time.monotonic())
        return True

    monkeypatch.setattr("livery.workshop._publish.publish_wheels", _left_dies)
    with pytest.raises(_FAILURES) as caught:
        publish_release(
            root,
            git,
            lambda _p: registry,
            ref=sha,
            probe_timeout=5,
            probe_poll=0.01,
        )
    message = str(caught.value)
    assert "left" in message and "walked past" in message
    tags = git.tags()
    # Siblings kept their receipts; the apex never started.
    assert "packages/base/v0.3.0" in tags
    assert "packages/right/v0.3.0" in tags
    assert "packages/left/v0.3.0" not in tags
    assert "packages/apex/v0.3.0" not in tags


def test_a_rerun_walks_past_the_receipts_already_cut(
    train, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, git, registry, _spans = train
    sha = _squash(root, ("base", "left"))
    publish_release(
        root, git, lambda _p: registry, ref=sha, probe_timeout=5, probe_poll=0.01
    )
    republished: list[str] = []
    monkeypatch.setattr(
        "livery.workshop._publish.publish_wheels",
        lambda package, **kwargs: republished.append(package.name),
    )
    receipts = publish_release(
        root, git, lambda _p: registry, ref=sha, probe_timeout=5, probe_poll=0.01
    )
    assert republished == []  # everything tagged was walked past
    assert all(not receipt.published for receipt in receipts)


def test_movement_after_prepare_refuses_naming_the_commits(train) -> None:
    root, git, _registry, _spans = train
    packages = {p.directory.name: p for p in discover_packages(root)}
    # A commit touching base lands after the entry was stamped, before
    # the squash: the squash carries code the changelog never saw.
    mined_at = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    sneak = root / "packages" / "base" / "src" / "livery" / "base" / "sneak.py"
    sneak.write_text("x = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "feat: the sneaked change")
    sha = _squash(root, ("base",), mined_at=mined_at)
    with pytest.raises(_FAILURES) as caught:
        movement_check(root, git, packages["base"], sha)
    message = str(caught.value)
    assert "sneaked" in message and "workflow.release" in message


def test_the_floor_probe_refuses_an_unservable_floor(train) -> None:
    root, _git_seam, registry, _spans = train
    packages = {p.path: p for p in discover_packages(root)}
    registry.serve("livery-base", "0.1.0")  # below the 0.3.0 floor
    with pytest.raises(_FAILURES) as caught:
        floor_probe(packages["packages/left"], packages, lambda _p: registry)
    assert "strands every consumer" in str(caught.value)
    registry.serve("livery-base", "0.3.0")
    floor_probe(packages["packages/left"], packages, lambda _p: registry)


def test_the_probe_times_out_teaching_the_rerun() -> None:
    registry = LedgerRegistry()
    with pytest.raises(_FAILURES) as caught:
        probe_until_served(registry, "livery-ghost", "1.0.0", timeout=0.05, poll=0.01)
    assert "never served" in str(caught.value)
    assert "walked past" in str(caught.value)


def test_receipts_expose_the_ledger(train) -> None:
    root, git, registry, _spans = train
    sha = _squash(root, ("base",))
    receipts = publish_release(
        root, git, lambda _p: registry, ref=sha, probe_timeout=5, probe_poll=0.01
    )
    assert receipts == (
        Receipt(
            package=receipts[0].package,
            version="0.3.0",
            tag="packages/base/v0.3.0",
            published=True,
        ),
    )
