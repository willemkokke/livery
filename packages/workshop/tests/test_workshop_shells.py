"""The task shells, lit: resolution monkeypatched, the verbs exercised.

CI's gate never invokes status, doctor, the ci group, or the
affected mode, so their shells stayed dark in the union. These tests
run each shell against livery.forge.testing.FakeForge and a
temporary workspace, which is the same seam the flows already use.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from footman import Failed

from livery.forge.testing import FakeForge
from livery.workshop import _ci_tasks, _graph, _quality
from livery.workshop._backends import _python
from livery.workshop._packages import Package

_FAILURES = (SystemExit, Failed)

OWNER, NAME = "willemkokke", "livery"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True)


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[FakeForge, Path]:
    """A workspace clone on a feature branch, resolution faked."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "--initial-branch=main")
    root = tmp_path / "ws"
    _git(tmp_path, "clone", str(origin), "ws")
    _git(root, "config", "user.email", "t@livery.local")
    _git(root, "config", "user.name", "T")
    (root / "workshop.toml").write_text("[workspace]\n")
    package = root / "packages" / "thing"
    (package / "src" / "livery" / "thing").mkdir(parents=True)
    (package / "workshop.toml").write_text('type = "python"\nname = "livery-thing"\n')
    (package / "pyproject.toml").write_text(
        '[project]\nname = "livery-thing"\ndependencies = []\n'
    )
    (package / "src" / "livery" / "thing" / "mod.py").write_text("x = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "chore: seed")
    _git(root, "push", "-u", "origin", "main")
    _git(root, "checkout", "-b", "feat/1-thing")
    fake = FakeForge()
    fake.create_repo(OWNER, NAME, private=True, description="t")
    repo = fake.repository(OWNER, NAME)
    monkeypatch.chdir(root)
    monkeypatch.setattr(
        "livery.workshop._forge_lane.this_repository", lambda _root: repo
    )
    monkeypatch.setattr("livery.workshop._forge_lane.this_forge", lambda _root: fake)
    return fake, root


def test_status_and_doctor_answer(
    rig: tuple[FakeForge, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _, _root = rig
    _ci_tasks.status()  # no PR: exit 0, says so
    _ci_tasks.doctor()
    out = capsys.readouterr().out
    assert "no pull request for feat/1-thing" in out
    assert "fake-user on FakeForge" in out or "grants:" in out


def test_the_ci_group_reports_the_empty_head(
    rig: tuple[FakeForge, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _, _root = rig
    _ci_tasks.ci_rerun()
    _ci_tasks.ci_cancel()
    out = capsys.readouterr().out
    assert "no runs for" in out
    assert "nothing running for" in out


def test_ci_logs_prints_the_failing_tail(
    rig: tuple[FakeForge, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    fake, _root = rig
    sha = fake.push(OWNER, NAME, "feat/1-thing", outcome="failure")
    fake.settle(OWNER, NAME, sha)
    # The PR is what names the head commit the verbs inspect.
    fake.repository(OWNER, NAME).pr.open("feat/1-thing", "main", "feat: t")
    _ci_tasks.ci_logs()
    out = capsys.readouterr().out
    assert "concluded failure" in out
    _ci_tasks.ci_logs(failed_only=False)
    assert "ci.yml" in capsys.readouterr().out


def test_graph_affected_prints_the_reach(
    rig: tuple[FakeForge, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _, root = rig
    (root / "packages" / "thing" / "src" / "livery" / "thing" / "mod.py").write_text(
        "x = 2\n"
    )
    _graph.graph_affected()
    out = capsys.readouterr().out
    assert "packages/thing (livery-thing)" in out
    (root / "workshop.toml").write_text("[workspace]\n# root\n")
    _graph.graph_affected()
    out = capsys.readouterr().out
    assert "everything" in out


def test_check_affected_scopes_or_says_nothing(
    rig: tuple[FakeForge, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, root = rig
    ran: list[str] = []
    monkeypatch.setattr(_python, "run_format", lambda **kwargs: ran.append("format"))
    monkeypatch.setattr(_python, "run_lint", lambda **kwargs: ran.append("lint"))
    monkeypatch.setattr(_python, "run_typecheck", lambda **kwargs: ran.append("types"))
    monkeypatch.setattr(
        _python, "run_typecomplete", lambda subset: ran.append("complete")
    )
    monkeypatch.setattr(
        _python,
        "run_test",
        lambda **kwargs: ran.append("test"),
    )
    # footman's parallel block insists on a real run context; the
    # test wants the routing, so both collapse to run-in-place.
    import contextlib

    import footman

    monkeypatch.setattr(_quality, "parallel", contextlib.nullcontext)
    monkeypatch.setattr(footman, "step", lambda fn, title=None: lambda: fn())
    # Nothing changed: the affected gate says so and runs nothing.
    _quality.check(affected=True)
    out = capsys.readouterr().out
    assert "nothing affected" in out
    assert ran == []
    # A one-package change: the scoped verbs run.
    (root / "packages" / "thing" / "src" / "livery" / "thing" / "mod.py").write_text(
        "x = 3\n"
    )
    monkeypatch.setattr(
        _quality,
        "_packages",
        lambda: (
            Package(
                directory=root / "packages" / "thing",
                path="packages/thing",
                name="livery-thing",
                type="python",
                depends=(),
            ),
            Package(
                directory=root / "packages" / "ghost",
                path="packages/ghost",
                name="livery-ghost",
                type="python",
                depends=(),
            ),
        ),
    )
    _quality.check(affected=True)
    out = capsys.readouterr().out
    assert "affected: packages/thing" in out
    assert set(ran) == {"format", "lint", "types", "complete", "test"}


def test_check_fix_rewrites_serially_then_judges_the_rest(
    rig: tuple[FakeForge, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = rig
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        _python, "run_format", lambda **kwargs: calls.append(("format", kwargs))
    )
    monkeypatch.setattr(
        _python, "run_lint", lambda **kwargs: calls.append(("lint", kwargs))
    )
    monkeypatch.setattr(
        _python, "run_typecheck", lambda **kwargs: calls.append(("types", kwargs))
    )
    monkeypatch.setattr(
        _python, "run_typecomplete", lambda subset: calls.append(("complete", None))
    )
    monkeypatch.setattr(
        _python, "run_test", lambda **kwargs: calls.append(("test", kwargs))
    )
    monkeypatch.setattr(
        _quality, "template_check", lambda: calls.append(("render", None))
    )
    import contextlib

    monkeypatch.setattr(_quality, "parallel", contextlib.nullcontext)
    _quality.check(fix=True)
    # format and lint rewrite the same files, so they run first and in
    # order; the rest of the gate still judges after them.
    assert calls[0] == ("format", {"check": False})
    assert calls[1] == ("lint", {"fix": True})
    assert {name for name, _ in calls[2:]} == {"types", "complete", "test", "render"}
    calls.clear()
    # Without --fix nothing rewrites: format checks and lint reports.
    _quality.check()
    assert ("format", {"check": True}) in calls
    assert ("lint", {"fix": False}) in calls


def test_coverage_enforce_reads_the_workspace(
    rig: tuple[FakeForge, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, root = rig
    seen: list[Path] = []
    monkeypatch.setattr(
        _python,
        "enforce_coverage",
        lambda r, packages: seen.append(r),
    )
    _quality.coverage_enforce()
    assert seen == [root]


def test_the_layers_task_prints_the_walk(
    rig: tuple[FakeForge, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.workshop._tasks import layers

    _, _root = rig
    layers()
    out = capsys.readouterr().out
    assert "no workspace" in out or "instance's own files" in out


def test_forge_lane_reads_the_contract_and_the_remote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No rig here: the rig monkeypatches the very resolution this
    # test exercises.
    from livery.workshop import _forge_lane

    root = tmp_path / "lane"
    root.mkdir()
    _git(root, "init", "--initial-branch=main")
    _git(root, "remote", "add", "origin", "git@github.com:acme/widgets.git")
    assert _forge_lane.remote_repo_name(root) == "widgets"
    _git(root, "remote", "set-url", "origin", "https://github.com/acme/widgets")
    assert _forge_lane.remote_repo_name(root) == "widgets"

    class FakeConnectable:
        def __init__(self) -> None:
            self.repositories: list[tuple[str, str]] = []

        def repository(self, owner: str, name: str) -> tuple[str, str]:
            self.repositories.append((owner, name))
            return (owner, name)

    connected = FakeConnectable()
    for kind in ("github", "gitea", "gitlab"):
        (root / "workshop.toml").write_text(
            f'[workspace]\n[forge]\nkind = "{kind}"\nowner = "acme"\n'
        )
        import livery.forge as forge

        for cls_name in ("GithubForge", "GiteaForge", "GitlabForge"):
            monkeypatch.setattr(
                getattr(forge, cls_name),
                "connect",
                classmethod(lambda cls, **k: connected),
            )
        _forge_lane.this_repository(root)
        assert connected.repositories[-1] == ("acme", "widgets")
    (root / "workshop.toml").write_text('[workspace]\n[forge]\nkind = "svn"\n')
    with pytest.raises(_FAILURES):
        _forge_lane.this_forge(root)
    (root / "workshop.toml").write_text("[workspace]\n")
    with pytest.raises(_FAILURES):
        _forge_lane.this_repository(root)
