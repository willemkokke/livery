"""The release point's verbs decide for themselves what the shell once decided.

The templates verb reads the wave at the ref and skips a wave that did
not release the publisher; the wave decides prebuilt from a collected
dist inside CI; the driver pin installs nothing on an empty input.
The refusals and skips first, then the acting cases.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from livery.workshop._git_ops import GitOps
from livery.workshop._publish import MANIFEST


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _workspace(tmp_path: Path) -> Path:
    """A workspace with a pure member and a platform-wheel member, on git."""
    root = tmp_path / "ws"
    (root / "packages").mkdir(parents=True)
    (root / "workshop.toml").write_text(
        '[workspace]\nextensions = []\n\n[forge]\nkind = "gitea"\nowner = "acme"\n'
    )
    for name, kind in (("pure", "python"), ("native", "python-nanobind")):
        member = root / "packages" / name
        (member / "src").mkdir(parents=True)
        (member / "workshop.toml").write_text(
            f'kind = "{kind}"\nname = "acme-{name}"\n'
        )
        (member / "pyproject.toml").write_text(f'[project]\nname = "acme-{name}"\n')
        (member / "CHANGELOG.md").write_text("## 1.0.0\n")
    _git(root, "init", "-q", "--initial-branch=main")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "chore: birth")
    return root


def _stamp(root: Path, *members: str) -> str:
    (root / MANIFEST).write_text(
        json.dumps({"members": [{"dir": m, "version": "1.0.0"} for m in members]})
    )
    _git(root, "add", MANIFEST)
    _git(root, "commit", "-qm", f"chore(release): released {', '.join(members)}")
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture(autouse=True)
def _outside_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GITHUB_ACTIONS", "GITEA_ACTIONS", "GITLAB_CI", "GITHUB_EVENT_PATH"):
        monkeypatch.delenv(name, raising=False)


# --- the wave's prebuilt decision -------------------------------------------------


def test_the_wave_builds_unless_ci_collected_wheels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop._release_driver import collected_wheels

    root = _workspace(tmp_path)
    sha = _stamp(root, "native", "pure")
    git = GitOps(root)
    dist = root / "packages" / "native" / "dist"
    dist.mkdir()
    (dist / "acme_native-1.0.0-cp314-cp314-linux_x86_64.whl").write_bytes(b"")
    # Outside CI a dist/ may be a stale local build: never trusted.
    assert collected_wheels(root, git, sha) is False
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_RUN_ID", "1")
    # Inside CI, an empty dist/ means no matrix fed the wave.
    (dist / "acme_native-1.0.0-cp314-cp314-linux_x86_64.whl").unlink()
    assert collected_wheels(root, git, sha) is False
    # Inside CI, wheels in a platform member's dist/ were collected.
    (dist / "acme_native-1.0.0-cp314-cp314-linux_x86_64.whl").write_bytes(b"")
    assert collected_wheels(root, git, sha) is True
    assert "prebuilt: the wheels matrix collected wheels for acme-native" in (
        capsys.readouterr().out
    )
    # A wave without a platform member has nothing to collect.
    pure_only = _stamp(root, "pure")
    assert collected_wheels(root, git, pure_only) is False


# --- the driver pin ----------------------------------------------------------------


def test_the_driver_pin_installs_nothing_on_an_empty_input(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import livery.toolroom.tools as tools
    from livery.workshop._release import release_driver

    seen: list[tuple[str, ...]] = []
    monkeypatch.setattr(tools, "uv", lambda *args: seen.append(args))
    release_driver()
    assert "the checkout's own workshop drives the wave" in capsys.readouterr().out
    assert seen == []
    release_driver(workshop="0.4.0")
    assert seen == [("pip", "install", "livery-workshop==0.4.0")]
    assert "livery-workshop 0.4.0 installed" in capsys.readouterr().out
