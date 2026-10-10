"""The released-wheels replay: refusals first, then the orchestration."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from livery.forge import ForgeError
from livery.forge.testing import FakeForge
from livery.workshop import _replay
from livery.workshop._git_ops import GitOps
from livery.workshop._packages import Package
from workshop_seeds import Seeds, _seed_home, seed_copier  # noqa: F401

_FAILURES = (BaseException,)


class _Index:
    def __init__(self, *versions: str) -> None:
        self._versions = versions

    def versions(self, name: str) -> tuple[str, ...]:
        return self._versions


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def _build(base: Path) -> None:
    # No origin: replay reads tags and trees, never a remote.
    root = base / "ws"
    src = root / "packages" / "thing" / "src" / "livery" / "thing"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("x = 1\n")
    (root / "packages" / "thing" / "tests").mkdir()
    (root / "packages" / "thing" / "tests" / "test_thing.py").write_text(
        "def test_x():\n    pass\n"
    )
    _git(root, "init", "-q", "--initial-branch=main")
    _git(root, "config", "user.name", "tester")
    _git(root, "config", "user.email", "tester@example.invalid")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "chore(release): released livery-thing v1.2.0")
    _git(root, "tag", "-a", "packages/thing/v1.2.0", "-m", "receipt")


@pytest.fixture
def released(seeds: Seeds) -> tuple[Path, GitOps, Package]:
    """A workspace whose member thing is released at v1.2.0 and tagged."""
    root = seeds("released", _build) / "ws"
    package = Package(
        directory=root / "packages" / "thing",
        path="packages/thing",
        name="livery-thing",
        kind="python",
        depends=(),
    )
    return root, GitOps(root), package


# --- refusals -----------------------------------------------------------------


def test_no_released_version_refuses() -> None:
    with pytest.raises(_FAILURES, match="serves no released version of livery-thing"):
        _replay.latest_release(_Index("1.2.0.dev3", "2.0.0rc1"), "livery-thing")


def test_the_latest_release_is_the_newest_final() -> None:
    assert (
        _replay.latest_release(_Index("1.9.0", "1.10.0", "2.0.0.dev1"), "x") == "1.10.0"
    )


def test_a_missing_receipt_tag_refuses(released: tuple[Path, GitOps, Package]) -> None:
    root, git, package = released
    with pytest.raises(_FAILURES, match=r"carries no tag packages/thing/v2\.0\.0"):
        _replay.replay_flow(
            root, git, package, python="3.14", registry=_Index("2.0.0"), index=""
        )


def _replayed(
    monkeypatch: pytest.MonkeyPatch, code: int, seen: dict[str, object] | None = None
) -> None:
    """Stand in for the replay phase: record what it got, answer *code*."""

    def _replay(package: Package, root: Path, **given: object) -> int:
        tree = given["tree"]
        assert isinstance(tree, Path)
        if seen is not None:
            seen.update(given)
            seen["tests"] = (tree / "packages" / "thing" / "tests").is_dir()
        return code

    monkeypatch.setattr("livery.workshop._lifecycle.replay", _replay)


def test_a_red_replay_fails_and_leaves_no_worktree(
    released: tuple[Path, GitOps, Package], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, git, package = released
    _replayed(monkeypatch, 1)
    with pytest.raises(
        _FAILURES, match=r"replay of livery-thing==1.2.0 is red \(exit 1\)"
    ):
        _replay.replay_flow(
            root, git, package, python="3.14", registry=_Index("1.2.0"), index=""
        )
    # The temporary worktree is gone either way.
    assert "fm-replay-" not in _git(root, "worktree", "list")


def test_a_refused_issue_is_named_and_the_replay_still_fails(
    released: tuple[Path, GitOps, Package],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, git, package = released
    fake = FakeForge()
    fake.create_repo("o", "r")
    repo = fake.repository("o", "r")

    def refuse(*args: object, **kwargs: object) -> None:
        raise ForgeError("issues are read-only here", status=403)

    monkeypatch.setattr(repo.issue, "search", refuse)

    _replayed(monkeypatch, 2)
    with pytest.raises(_FAILURES, match="is red"):
        _replay.replay_flow(
            root,
            git,
            package,
            python="3.14",
            registry=_Index("1.2.0"),
            index="",
            repo=repo,
        )
    assert (
        "the forge refused the issue: issues are read-only here"
        in capsys.readouterr().out
    )


# --- the orchestration --------------------------------------------------------


def test_a_green_replay_runs_the_tests_from_the_tagged_tree(
    released: tuple[Path, GitOps, Package],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, git, package = released
    seen: dict[str, object] = {}
    _replayed(monkeypatch, 0, seen)
    _replay.replay_flow(
        root,
        git,
        package,
        python="3.14",
        registry=_Index("1.2.0", "1.1.0"),
        index="https://index.example/simple",
        extras="github-secrets",
    )
    assert {key: seen[key] for key in ("version", "python", "index", "extras")} == {
        "version": "1.2.0",
        "python": "3.14",
        "index": "https://index.example/simple",
        "extras": "github-secrets",
    }
    assert seen["tests"] is True
    out = capsys.readouterr().out
    assert (
        "replaying livery-thing[github-secrets]==1.2.0 at packages/thing/v1.2.0"
        " on python 3.14"
    ) in out
    assert (
        "green: livery-thing[github-secrets]==1.2.0 passes its own tests"
        " from site-packages"
    ) in out


def test_a_red_replay_files_then_extends_the_marker_issue(
    released: tuple[Path, GitOps, Package],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, git, package = released
    fake = FakeForge()
    fake.create_repo("o", "r")
    repo = fake.repository("o", "r")
    monkeypatch.setenv("GITHUB_SERVER_URL", "http://gitea:3000")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_RUN_ID", "77")

    _replayed(monkeypatch, 1)
    for _ in range(2):
        with pytest.raises(_FAILURES, match="is red"):
            _replay.replay_flow(
                root,
                git,
                package,
                python="3.14",
                registry=_Index("1.2.0"),
                index="",
                repo=repo,
            )
    out = capsys.readouterr().out
    assert f"filed #1: {_replay.MARKER}" in out
    assert (
        "commented on #1: livery-thing==1.2.0 failed its replay: http://gitea:3000/o/r/actions/runs/77"
        in out
    )
    (issue,) = repo.issue.search(_replay.MARKER)
    assert issue.number == 1
