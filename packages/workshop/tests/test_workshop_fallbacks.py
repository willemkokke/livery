"""The fallbacks, forced: the paths that only fire when something fails.

A broken happy path announces itself in daily use; a broken fallback
hides until it is the only path left. These tests make the primary
paths fail on purpose: symlinks refused so the copy fallback carries
the content channel, arming schedules lost until the retries exhaust,
the self-heal running its full clean continuation, and the release
verbs refusing every malformed shape they guard against.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from footman import Failed

from livery.forge.testing import FakeForge
from livery.workshop._backends import _python
from livery.workshop._git_ops import GitOps
from livery.workshop._materialise import materialise
from livery.workshop._packages import Package
from livery.workshop._release import prepare_release, verify_release
from livery.workshop._submit import _arm_verified

_FAILURES = (SystemExit, Failed)

OWNER, NAME = "willemkokke", "livery"


def _deny_symlinks(monkeypatch: pytest.MonkeyPatch) -> None:
    def refused(*_args: object, **_kwargs: object) -> None:
        raise OSError("symlinks refused for this test")

    monkeypatch.setattr(os, "symlink", refused)


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "content" / "skills"
    (source / "thing").mkdir(parents=True)
    (source / "thing" / "SKILL.md").write_text("shipped\n")
    (source / "single.md").parent.mkdir(exist_ok=True)
    (source / "single.md").write_text("one file\n")
    return source


def test_the_copy_fallback_carries_the_content_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _deny_symlinks(monkeypatch)
    source = _source(tmp_path)
    repo = tmp_path / "repo"
    lines = materialise(repo, source, "skills")
    copied = repo / ".claude" / "skills" / "thing" / "SKILL.md"
    assert copied.is_file() and not copied.is_symlink()
    assert copied.read_text() == "shipped\n"
    assert any("cop" in line for line in lines)  # copied, announced
    # Idempotent through the fallback too.
    assert materialise(repo, source, "skills") == []
    # And the shipped copy updates when the source moves.
    (source / "thing" / "SKILL.md").write_text("newer\n")
    materialise(repo, source, "skills")
    assert copied.read_text() == "newer\n"


def test_the_fallback_prunes_what_is_no_longer_shipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _deny_symlinks(monkeypatch)
    source = _source(tmp_path)
    repo = tmp_path / "repo"
    materialise(repo, source, "skills")
    (source / "thing" / "SKILL.md").unlink()
    (source / "thing").rmdir()
    lines = materialise(repo, source, "skills")
    assert any("no longer shipped" in line for line in lines)
    assert not (repo / ".claude" / "skills" / "thing").exists()


def test_a_local_override_survives_the_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _deny_symlinks(monkeypatch)
    source = _source(tmp_path)
    repo = tmp_path / "repo"
    materialise(repo, source, "skills")
    override = repo / ".claude" / "skills" / "thing" / "SKILL.md"
    override.write_text("mine now\n")
    lines = materialise(repo, source, "skills")
    assert any("override" in line for line in lines)
    assert override.read_text() == "mine now\n"


def test_arm_retries_exhaust_with_the_forges_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeForge()
    fake.create_repo(OWNER, NAME, private=True, description="t")
    repo = fake.repository(OWNER, NAME)
    fake.push(OWNER, NAME, "feat/x")
    pr = repo.pr.open("feat/x", "main", "feat: x")
    fake.faults.lose_arm_schedule = 99  # every retry loses
    with pytest.raises(_FAILURES) as caught:
        _arm_verified(repo, pr.number, title="feat: x", message="")
    assert "lost the auto-merge schedule" in str(caught.value)


def test_the_clean_heal_reships_and_lands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The heal's full continuation: behind the base with no conflict,
    # integrate succeeds, the re-gate is skipped, the re-push re-arms,
    # and the merge lands on the healed head.
    from livery.workshop._submit import submit_flow
    from livery.workshop._verdict import EXIT_BEHIND

    calls = {"follows": 0}

    class HealGit(GitOps):
        def push(self, branch: str) -> None:  # the fake owns the remote
            pass

        def fetch(self) -> None:
            pass

        def integrate(self, base: str) -> None:
            calls["integrated"] = True

        def current_branch(self) -> str:
            return "feat/1-heal"

        def head_subject(self) -> str:
            return "feat: heal"

        def head_body(self) -> str:
            return ""

        def subjects_ahead(self, base: str) -> list[str]:
            return ["feat: heal"]

    fake = FakeForge()
    fake.create_repo(OWNER, NAME, private=True, description="t")
    repo = fake.repository(OWNER, NAME)
    sha = fake.push(OWNER, NAME, "feat/1-heal")

    from livery.workshop import _verdict

    real_follow = _verdict.follow

    def follow_behind_once(*args: Any, **kwargs: Any) -> Any:
        calls["follows"] += 1
        if calls["follows"] == 1:
            raise SystemExit(EXIT_BEHIND)
        # The healed head goes green only now, so the first arm could
        # not merge on the spot and the re-arm settles it.
        fake.settle(OWNER, NAME, sha)
        return real_follow(*args, **kwargs)

    monkeypatch.setattr("livery.workshop._submit.follow", follow_behind_once)
    number = submit_flow(
        repo,
        HealGit(tmp_path),
        armed=True,
        gate=False,
        interval=0,
        timeout=5,
    )
    assert calls.get("integrated") is True
    merged = repo.pr.get(number)
    assert merged is not None and merged.merged


def test_release_verify_refuses_each_malformed_shape(tmp_path: Path) -> None:
    root = tmp_path
    (root / "workshop.toml").write_text("[workspace]\n")
    (root / "packages").mkdir()
    subprocess.run(["git", "init"], cwd=root, capture_output=True, check=True)
    with pytest.raises(_FAILURES) as caught:
        verify_release(root, "not-a-tag")
    assert "does not match" in str(caught.value)
    with pytest.raises(_FAILURES) as caught:
        verify_release(root, "packages/ghost/v1.0.0")
    assert "not a workspace package" in str(caught.value)
    with pytest.raises(_FAILURES) as caught:
        prepare_release(root, "packages/ghost", "1.0.0")
    assert "not a workspace package" in str(caught.value)
    thing = root / "packages" / "thing"
    thing.mkdir()
    (thing / "workshop.toml").write_text('type = "python"\nname = "livery-thing"\n')
    (thing / "pyproject.toml").write_text(
        '[project]\nname = "livery-thing"\nversion = "0.1.0"\ndependencies = []\n'
    )
    with pytest.raises(_FAILURES) as caught:
        prepare_release(root, "packages/thing", "not.a.version")
    assert "is not <major>" in str(caught.value)


def test_the_enforcement_reads_real_coverage_data(tmp_path: Path) -> None:
    # measured_coverage and enforce_coverage run for real, against a
    # coverage data file recorded here, not a monkeypatch.
    package_dir = tmp_path / "packages" / "thing"
    (package_dir / "src").mkdir(parents=True)
    (package_dir / "workshop.toml").write_text(
        'type = "python"\nname = "livery-thing"\n[qa]\ncoverage_floor = 50\n'
    )
    module = package_dir / "src" / "mod.py"
    module.write_text("def run():\n    return 1\n\nrun()\n")
    subprocess.run(
        ["python3", "-m", "coverage", "run", "--source", str(package_dir), str(module)],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    package = Package(
        directory=package_dir,
        path="packages/thing",
        name="livery-thing",
        type="python",
        depends=(),
    )
    measured = _python.measured_coverage(tmp_path, (package,))
    assert measured["packages/thing"] > 0
    _python.enforce_coverage(tmp_path, (package,))  # above its floor
    (package_dir / "workshop.toml").write_text(
        'type = "python"\nname = "livery-thing"\n[qa]\ncoverage_floor = 101\n'
    )
    with pytest.raises(_FAILURES):
        _python.enforce_coverage(tmp_path, (package,))


def _cliff_workspace(tmp_path: Path, kind: str) -> tuple[Path, Package]:
    """A workspace whose contract names *kind*, with one package."""
    (tmp_path / "workshop.toml").write_text(
        f'[workspace]\n\n[forge]\nkind = "{kind}"\n'
    )
    directory = tmp_path / "packages" / "thing"
    directory.mkdir(parents=True)
    (directory / "cliff.toml").write_text("[changelog]\n")
    package = Package(
        directory=directory,
        path="packages/thing",
        name="livery-thing",
        type="python",
        depends=(),
    )
    return tmp_path, package


def test_the_changelog_runs_offline_when_no_credential_is_in_reach(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A private repository answers the author lookup only for a caller
    # it can authenticate, and git-cliff stops rather than degrading.
    # Without the token the run must go offline instead, or every
    # release on a private forge dies at the changelog.
    import footman

    from livery.workshop import _cliff

    root, package = _cliff_workspace(tmp_path, "gitea")
    for name in ("GITEA_TOKEN", "FORGE_TOKEN", "FORGE_TOKEN__GITEA_COM"):
        monkeypatch.delenv(name, raising=False)
    seen: list[list[str]] = []
    environments: list[dict[str, str]] = []

    def _fake_run(command: list[str], **kwargs: Any) -> Any:
        from types import SimpleNamespace

        seen.append(list(command))
        environments.append(dict(kwargs.get("env") or {}))
        return SimpleNamespace(code=0, stdout="## [Unreleased]\n", stderr="")

    monkeypatch.setattr(footman, "run", _fake_run)
    _cliff.unreleased_entry(root, package)
    assert "--offline" in seen[0]
    out = capsys.readouterr().out
    assert "without its authors" in out and "FORGE_TOKEN" in out
    # With the credential in reach the lookup is asked for, and the
    # one mapping site hands FORGE_TOKEN to git-cliff under the name
    # its own contract reads.
    seen.clear()
    monkeypatch.setenv("FORGE_TOKEN", "a-token")
    _cliff.unreleased_entry(root, package)
    assert "--offline" not in seen[0]
    assert environments[-1].get("GITEA_TOKEN") == "a-token"


def test_a_refused_author_lookup_says_what_to_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import footman

    from livery.workshop import _cliff

    root, package = _cliff_workspace(tmp_path, "gitea")
    monkeypatch.setenv("FORGE_TOKEN", "a-token")

    def _refuse(command: list[str], **kwargs: Any) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(
            code=101, stdout="", stderr="Could not get gitea metadata: Status(404)"
        )

    monkeypatch.setattr(footman, "run", _refuse)
    with pytest.raises(_FAILURES) as caught:
        _cliff.bumped_version(root, package)
    message = str(caught.value)
    assert "FORGE_TOKEN" in message and "api_url" in message


def test_a_missing_git_cliff_names_the_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import footman

    from livery.workshop import _cliff

    root, package = _cliff_workspace(tmp_path, "github")

    def _absent(command: list[str], **kwargs: Any) -> Any:
        raise FileNotFoundError(command[0])

    monkeypatch.setattr(footman, "run", _absent)
    with pytest.raises(_FAILURES) as caught:
        _cliff.bumped_version(root, package)
    assert "git-cliff" in str(caught.value) and "fm sync" in str(caught.value)


def test_a_package_without_the_contract_is_named(tmp_path: Path) -> None:
    from livery.workshop import _cliff

    root, package = _cliff_workspace(tmp_path, "github")
    (package.directory / "cliff.toml").unlink()
    with pytest.raises(_FAILURES) as caught:
        _cliff.bumped_version(root, package)
    assert "cliff.toml" in str(caught.value)


def test_a_newborn_workspace_has_zero_packages(tmp_path: Path) -> None:
    from livery.workshop._packages import discover_packages

    # No packages/ directory at all: the state every instance is born
    # in, and a legal one, not an error.
    assert discover_packages(tmp_path) == ()
