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
from footman import Failed

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


@pytest.fixture
def train(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A squashed release of a diamond: base, two legs, an apex."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "--initial-branch=main")
    root = tmp_path / "ws"
    _git(tmp_path, "clone", str(origin), "ws")
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
