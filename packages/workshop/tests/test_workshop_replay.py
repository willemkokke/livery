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


@pytest.fixture
def released(tmp_path: Path) -> tuple[Path, GitOps, Package]:
    """A workspace whose member thing is released at v1.2.0 and tagged."""
    root = tmp_path / "ws"
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
    package = Package(
        directory=root / "packages" / "thing",
        path="packages/thing",
        name="livery-thing",
        type="python",
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


def test_a_tree_without_a_package_refuses(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    with pytest.raises(_FAILURES, match="holds no package directory"):
        _replay.import_name(tmp_path / "src")


def test_a_red_install_is_red_before_any_test_runs(
    released: tuple[Path, GitOps, Package], capsys: pytest.CaptureFixture[str]
) -> None:
    root, git, package = released
    tested: list[str] = []

    def refuse(venv: Path, python: str, requirement: str, index: str) -> int:
        return 1

    def never(venv: Path, tree: Path, member: str, module: str) -> int:
        tested.append(member)
        return 0

    with pytest.raises(
        _FAILURES, match=r"replay of livery-thing==1.2.0 is red \(exit 1\)"
    ):
        _replay.replay_flow(
            root,
            git,
            package,
            python="3.14",
            registry=_Index("1.2.0"),
            index="",
            installer=refuse,
            tester=never,
        )
    assert tested == []
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

    def installed(venv: Path, python: str, requirement: str, index: str) -> int:
        return 0

    def red(venv: Path, tree: Path, member: str, module: str) -> int:
        return 2

    with pytest.raises(_FAILURES, match="is red"):
        _replay.replay_flow(
            root,
            git,
            package,
            python="3.14",
            registry=_Index("1.2.0"),
            index="",
            repo=repo,
            installer=installed,
            tester=red,
        )
    assert (
        "the forge refused the issue: issues are read-only here"
        in capsys.readouterr().out
    )


# --- the orchestration --------------------------------------------------------


def test_a_green_replay_runs_the_tests_from_the_tagged_tree(
    released: tuple[Path, GitOps, Package], capsys: pytest.CaptureFixture[str]
) -> None:
    root, git, package = released
    seen: dict[str, object] = {}

    def installed(venv: Path, python: str, requirement: str, index: str) -> int:
        seen["install"] = (python, requirement, index)
        return 0

    def tested(venv: Path, tree: Path, member: str, module: str) -> int:
        seen["test"] = (
            member,
            module,
            (tree / "packages" / "thing" / "tests").is_dir(),
        )
        return 0

    _replay.replay_flow(
        root,
        git,
        package,
        python="3.14",
        registry=_Index("1.2.0", "1.1.0"),
        index="https://index.example/simple",
        extras="github-secrets",
        installer=installed,
        tester=tested,
    )
    assert seen["install"] == (
        "3.14",
        "livery-thing[github-secrets]==1.2.0",
        "https://index.example/simple",
    )
    assert seen["test"] == ("thing", "livery.thing", True)
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

    def red(venv: Path, tree: Path, member: str, module: str) -> int:
        return 1

    def green(venv: Path, python: str, requirement: str, index: str) -> int:
        return 0

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
                installer=green,
                tester=red,
            )
    out = capsys.readouterr().out
    assert f"filed #1: {_replay.MARKER}" in out
    assert (
        "commented on #1: livery-thing==1.2.0 failed its replay: http://gitea:3000/o/r/actions/runs/77"
        in out
    )
    (issue,) = repo.issue.search(_replay.MARKER)
    assert issue.number == 1
