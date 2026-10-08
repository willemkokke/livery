"""The changelog extension: git-cliff writes each entry, crediting authors it can."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from livery.footman import Failed
from livery.workshop import Package

_FAILURES = (SystemExit, Failed)

CONTRACT = (
    "[workspace]\n"
    'name = "acme"\n'
    'namespace = "acme"\n'
    'authors = [{ name = "Acme", email = "dev@acme.test" }]\n'
    'copyright-year = "2026"\n'
)


def _workspace(root: Path, extensions: str, forge: str = "") -> Path:
    """A workspace listing *extensions*, on GitHub unless *forge* says otherwise."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "workshop.toml").write_text(
        CONTRACT
        + f"extensions = {extensions}\n\n"
        + (forge or '[forge]\nkind = "github"\nowner = "acme-org"\n')
    )
    return root


def _member(root: Path, name: str, kind: str = "python") -> Package:
    directory = root / "packages" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "workshop.toml").write_text(f'kind = "{kind}"\nname = "acme-{name}"\n')
    (directory / "pyproject.toml").write_text(
        f'[project]\nname = "acme-{name}"\nversion = "0.2.0"\n'
    )
    return Package(
        directory=directory,
        path=f"packages/{name}",
        name=f"acme-{name}",
        kind=kind,
        depends=(),
    )


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _commit(root: Path, where: str, subject: str) -> None:
    """Commit a change under ``packages/<where>`` with *subject*."""
    directory = root / "packages" / where
    count = len(list(directory.glob("change-*.txt")))
    (directory / f"change-{count}.txt").write_text(subject)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", subject)


def _repository(root: Path) -> None:
    """Make *root* a repository whose first commit holds the workspace."""
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "dev@acme.test")
    _git(root, "config", "user.name", "Acme")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "chore: seed")


# The fallbacks first: unlisted, the extension writes nothing and
# registers no notes; then a credential nobody holds, a refused lookup,
# a missing git-cliff and a package without its cliff.toml.


def test_unlisted_it_writes_no_cliff_toml_and_registers_no_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.extensions.changelog import _cliff
    from livery.footman import _registry as footman_registry
    from livery.workshop import _checks as registry
    from livery.workshop import _release_notes
    from livery.workshop._extensions import mount_extensions
    from livery.workshop._shipped_files import deliver, shipped_drift

    root = _workspace(tmp_path / "ws", "[]")
    _member(root, "thing")
    monkeypatch.setattr(
        "livery.workshop._extensions.workspace_root", lambda start=None: root
    )
    monkeypatch.setattr(_release_notes, "_PROVIDER", [])
    state = registry.snapshot()
    try:
        with footman_registry.capture():
            assert mount_extensions(root) == ()
        assert _release_notes.release_notes() is None
        deliver(root)
        assert not (root / "packages" / "thing" / "cliff.toml").exists()
        # Listed, the mount registers the provider, imported only when
        # the train first asks it, and the sync writes each member's
        # cliff.toml, which the gate then keeps.
        _workspace(root, '["changelog"]')
        with footman_registry.capture():
            assert mount_extensions(root) == ("changelog",)
        found = _release_notes.release_notes()
        assert isinstance(found, _release_notes.DeclaredNotes)
        assert found.reference.resolve() is _cliff.NOTES
        assert "packages/thing/cliff.toml: missing; `fm sync` writes it" in (
            shipped_drift(root)
        )
        assert "  wrote packages/thing/cliff.toml" in deliver(root)
        assert shipped_drift(root) == []
        (root / "packages" / "thing" / "cliff.toml").write_text("# edited by hand\n")
        (drift,) = shipped_drift(root)
        assert drift.startswith("packages/thing/cliff.toml: differs from what")
    finally:
        registry.restore(state)


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
        kind="python",
        depends=(),
    )
    return tmp_path, package


def test_a_workspace_on_no_named_forge_has_no_credential(tmp_path: Path) -> None:
    # git-cliff reads a token under a per-kind name; a contract that
    # names no forge has no name to hand one under.
    from livery.extensions.changelog import _cliff

    (tmp_path / "workshop.toml").write_text("[workspace]\n")
    assert _cliff._credential(tmp_path) == ("", "")


def test_a_per_kind_variable_already_set_wins_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.extensions.changelog import _cliff

    root, _package = _cliff_workspace(tmp_path, "gitea")
    monkeypatch.setenv("FORGE_TOKEN", "the-workshop-s")
    monkeypatch.setenv("GITEA_TOKEN", "git-cliff-s-own")
    assert _cliff._credential(root) == ("GITEA_TOKEN", "git-cliff-s-own")


def test_the_changelog_credits_with_the_token_the_lane_connects_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No variable names a token, but the lane's backend finds one on
    # its own (the gh keyring): the changelog credits with it. A
    # backend that refuses to connect without a token means the lane
    # has none, and the entry goes offline as before.
    from types import SimpleNamespace

    from livery.extensions.changelog import _cliff
    from livery.forge import ForgeError

    root, _package = _cliff_workspace(tmp_path, "github")
    for name in ("GITHUB_TOKEN", "FORGE_TOKEN", "FORGE_TOKEN__GITHUB_COM"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        "livery.workshop._forge_lane._connect",
        lambda kind, url, token: SimpleNamespace(token="from-the-keyring"),
    )
    assert _cliff._credential(root) == ("GITHUB_TOKEN", "from-the-keyring")
    assert _cliff.credit_is_reachable(root)

    def _refuses(kind: str, url: str, token: str | None) -> Any:
        raise ForgeError("no token")

    monkeypatch.setattr("livery.workshop._forge_lane._connect", _refuses)
    assert _cliff._credential(root) == ("", "")
    assert not _cliff.credit_is_reachable(root)


def test_the_changelog_runs_offline_when_no_credential_is_in_reach(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A private repository answers the author lookup only for a caller
    # it can authenticate, and git-cliff stops rather than degrading.
    # Without the token the run must go offline instead, or every
    # release on a private forge dies at the changelog.
    from livery.extensions.changelog import _cliff
    from livery.toolroom.tools.testing import answers

    root, package = _cliff_workspace(tmp_path, "gitea")
    for name in ("GITEA_TOKEN", "FORGE_TOKEN", "FORGE_TOKEN__GITEA_COM"):
        monkeypatch.delenv(name, raising=False)
    # The run leaves through the tool's handle; the seam answers for it.
    with answers({("git-cliff",): "## [1.0.0]\n"}) as calls:
        _cliff.unreleased_entry(root, package, "1.0.0")
    assert "--offline" in calls[0].argv
    out = capsys.readouterr().out
    assert "without its authors" in out and "FORGE_TOKEN" in out
    # With the credential in reach the lookup is asked for, and the
    # one mapping site hands FORGE_TOKEN to git-cliff under the name
    # its own contract reads.
    monkeypatch.setenv("FORGE_TOKEN", "a-token")
    with answers({("git-cliff",): "## [1.0.0]\n"}) as calls:
        _cliff.unreleased_entry(root, package, "1.0.0")
    assert "--offline" not in calls[0].argv
    assert (calls[0].env or {}).get("GITEA_TOKEN") == "a-token"


def test_a_dev_build_s_excerpt_asks_the_forge_for_no_author(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Nobody credits a dev build's authors, and the lookup pages through
    # every pull request the forge holds, once per package: the excerpt
    # goes offline with the credential in reach, and says nothing of it.
    from livery.extensions.changelog import _cliff
    from livery.toolroom.tools.testing import answers

    root, package = _cliff_workspace(tmp_path, "gitea")
    monkeypatch.delenv("GITEA_TOKEN", raising=False)
    monkeypatch.setenv("FORGE_TOKEN", "a-token")
    with answers({("git-cliff",): "## [Unreleased]\n"}) as calls:
        _cliff.unreleased_entry(root, package)
    assert "--offline" in calls[0].argv
    assert "GITEA_TOKEN" not in (calls[0].env or {})
    assert "without its authors" not in capsys.readouterr().out


def _lookup_answers(monkeypatch: pytest.MonkeyPatch, refusal: Exception | None) -> None:
    """The forge lane's one read, answered with *refusal* or nothing."""
    from types import SimpleNamespace

    def get(number: int) -> None:
        del number
        if refusal is not None:
            raise refusal

    monkeypatch.setattr(
        "livery.workshop.this_repository",
        lambda root: SimpleNamespace(pr=SimpleNamespace(get=get)),
    )


def test_a_refused_author_lookup_says_what_to_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    from livery.extensions.changelog import _cliff
    from livery.forge import ForgeError, RateLimited
    from livery.toolroom.tools import Result
    from livery.toolroom.tools.testing import answers

    root, package = _cliff_workspace(tmp_path, "gitea")
    monkeypatch.delenv("GITEA_TOKEN", raising=False)
    monkeypatch.setenv("FORGE_TOKEN", "a-token")
    # A failure that is not the lookup carries git-cliff's words alone.
    broken = Result(1, stderr="unknown field `bump`")
    with answers({("git-cliff",): broken}), pytest.raises(_FAILURES) as caught:
        _cliff.unreleased_entry(root, package, "1.0.0")
    assert "git-cliff exited 1:\nunknown field `bump`" in str(caught.value)
    assert "FORGE_TOKEN" not in str(caught.value)
    refused = Result(101, stderr="Could not get gitea metadata: Status(403)")
    # A spent API budget first: the forge answers it as it answers a
    # refused token, and the next act is to wait, which the read names.
    renews = time.time() + 600
    _lookup_answers(monkeypatch, RateLimited("budget spent", reset_at=renews))
    with answers({("git-cliff",): refused}), pytest.raises(_FAILURES) as caught:
        _cliff.unreleased_entry(root, package, "1.0.0")
    when = time.strftime("%H:%M", time.localtime(renews))
    assert f"the forge's API budget is spent: it renews at {when}" in str(caught.value)
    assert "api_url" not in str(caught.value)
    # A budget the forge does not time still says what to do.
    _lookup_answers(monkeypatch, RateLimited("budget spent", reset_at=None))
    with answers({("git-cliff",): refused}), pytest.raises(_FAILURES) as caught:
        _cliff.unreleased_entry(root, package, "1.0.0")
    assert "it renews within the hour" in str(caught.value)
    # The forge refuses the read too, or answers it: the token or the url.
    for refusal in (ForgeError("refused"), None):
        _lookup_answers(monkeypatch, refusal)
        with answers({("git-cliff",): refused}), pytest.raises(_FAILURES) as caught:
            _cliff.unreleased_entry(root, package, "1.0.0")
        message = str(caught.value)
        assert "FORGE_TOKEN" in message and "api_url" in message


def test_a_missing_git_cliff_names_the_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.extensions.changelog import _cliff

    root, package = _cliff_workspace(tmp_path, "github")

    def _absent(*args: Any, **kwargs: Any) -> Any:
        raise FileNotFoundError("git-cliff")

    # The seam every handle call leaves through, standalone or hosted.
    monkeypatch.setattr("livery.toolroom.tools._host.run", _absent)
    with pytest.raises(_FAILURES) as caught:
        _cliff.unreleased_entry(root, package)
    assert "git-cliff" in str(caught.value) and "fm sync" in str(caught.value)


def test_a_package_without_the_contract_is_named(tmp_path: Path) -> None:
    from livery.extensions.changelog import _cliff

    root, package = _cliff_workspace(tmp_path, "github")
    (package.directory / "cliff.toml").unlink()
    with pytest.raises(_FAILURES) as caught:
        _cliff.unreleased_entry(root, package)
    assert "cliff.toml" in str(caught.value)


def test_a_member_born_while_it_is_listed_starts_its_history(tmp_path: Path) -> None:
    from livery.workshop._templates import render_member

    # Unlisted, a birth writes no history: the first record creates it.
    bare = _workspace(tmp_path / "bare", "[]")
    render_member(bare, "thing")
    assert not (bare / "packages" / "thing" / "CHANGELOG.md").exists()
    assert not (bare / "packages" / "thing" / "cliff.toml").exists()
    root = _workspace(tmp_path / "ws", '["changelog"]')
    render_member(root, "thing")
    text = (root / "packages" / "thing" / "CHANGELOG.md").read_text()
    assert "All notable changes to acme-thing are documented here." in text
    assert (root / "packages" / "thing" / "cliff.toml").is_file()


# Listed: every member's own cliff.toml, and the entries git-cliff
# writes through it.


def test_every_member_takes_a_cliff_toml_of_its_own_whatever_its_kind(
    tmp_path: Path,
) -> None:
    from livery.workshop._shipped_files import deliver

    root = _workspace(tmp_path / "ws", '["changelog"]')
    _member(root, "thing")
    _member(root, "ext", kind="python-nanobind")
    written = deliver(root)
    for name in ("thing", "ext"):
        assert f"  wrote packages/{name}/cliff.toml" in written
        body = (root / "packages" / name / "cliff.toml").read_text()
        assert f'include_paths = ["packages/{name}/**"]' in body
        assert f'tag_pattern = "^packages/{name}/v?(.+)$"' in body


@pytest.mark.parametrize(
    ("kind", "url", "api_url"),
    [
        ("gitlab", "http://gitlab:8929", "http://gitlab:8929/api/v4"),
        ("gitea", "http://gitea:3000", "http://gitea:3000"),
    ],
)
def test_the_remote_carries_the_api_prefix_gitlab_alone_needs(
    tmp_path: Path, kind: str, url: str, api_url: str
) -> None:
    # git-cliff completes a Gitea root with /api/v1 itself and a
    # GitLab address with nothing, so the render spells the prefix
    # for GitLab alone; a doubled prefix on Gitea answers 404.
    from livery.workshop._shipped_files import deliver

    forge = f'[forge]\nkind = "{kind}"\nowner = "acme-org"\nurl = "{url}"\n'
    root = _workspace(tmp_path / "ws", '["changelog"]', forge)
    _member(root, "thing")
    assert "  wrote packages/thing/cliff.toml" in deliver(root)
    body = (root / "packages" / "thing" / "cliff.toml").read_text()
    assert f"[remote.{kind}]" in body
    assert f'api_url = "{api_url}"' in body


def test_an_entry_groups_the_package_s_commits_and_links_the_pull_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.extensions.changelog import _cliff
    from livery.workshop._shipped_files import deliver

    root = _workspace(tmp_path / "ws", '["changelog"]')
    package = _member(root, "thing")
    _member(root, "other")
    deliver(root)
    _repository(root)
    _git(root, "tag", "packages/thing/v0.2.0")
    _commit(root, "thing", "feat: the tool grows a verb (#41)")
    _commit(root, "thing", "fix: a crash")
    _commit(root, "other", "feat: a sibling's verb")
    # No credential in reach: the entry is written offline, without
    # its authors, and nothing reaches for the forge.
    monkeypatch.setattr(_cliff, "_credential", lambda root: ("", ""))
    entry = _cliff.NOTES.entry(root, package, "0.3.0")
    assert entry.startswith("## [0.3.0] - ")
    assert "### Added" in entry and "### Fixed" in entry
    assert (
        "- The tool grows a verb ([#41](https://github.com/acme-org/acme/pull/41))"
        in entry
    )
    assert "- A crash" in entry
    assert "sibling" not in entry
    # Unreleased, the same commits head an unreleased entry.
    assert _cliff.NOTES.entry(root, package).startswith("## [Unreleased]")


def test_the_first_record_creates_the_history_and_a_stranded_entry_regenerates(
    tmp_path: Path,
) -> None:
    from livery.extensions.changelog import _cliff

    root = _workspace(tmp_path / "ws", '["changelog"]')
    package = _member(root, "thing")
    changelog = package.directory / "CHANGELOG.md"
    # Nothing recorded yet: the version's notes are missing, and the
    # first record creates the history.
    assert _cliff.NOTES.verify(package, "0.1.0") == [
        "CHANGELOG.md has no '## 0.1.0' entry"
    ]
    assert _cliff.NOTES.record(package, "0.1.0", "## [0.1.0]\n\n- Born.") == [
        "CHANGELOG.md (review the entry before tagging)"
    ]
    assert changelog.read_text() == "# Changelog\n\n## [0.1.0]\n\n- Born.\n"
    assert _cliff.NOTES.verify(package, "0.1.0") == []
    # An empty entry for a version already recorded changes nothing.
    assert _cliff.NOTES.record(package, "0.1.0", "") == []
    # A newer entry goes on top; an empty one is a placeholder to write.
    _cliff.NOTES.record(package, "0.2.0", "")
    text = changelog.read_text()
    assert text.index("## [0.2.0]\n\n-") < text.index("## [0.1.0]")
    # A heading whose tag never cut is regenerated by a new entry, and
    # the same entry again changes nothing.
    assert _cliff.NOTES.record(package, "0.2.0", "## [0.2.0]\n\n- Grown.") == [
        "CHANGELOG.md (the stranded entry regenerated; review it)"
    ]
    assert "- Grown." in changelog.read_text()
    assert _cliff.NOTES.record(package, "0.2.0", "## [0.2.0]\n\n- Grown.") == []


#: (tag before this commit or "", subject, body, member) for one scenario.
Step = tuple[str, str, str, str]

#: The bump rules the template states: (baseline, steps).
SCENARIOS: dict[str, tuple[str, list[Step]]] = {
    "no tag yet: the baseline": ("0.1.0", [("", "feat: start", "", "thing")]),
    "no tag and no baseline": ("", [("", "feat: start", "", "thing")]),
    "a commit outside the member": (
        "",
        [("0.3.1", "feat: start", "", "thing"), ("", "feat: x", "", "other")],
    ),
    "a release commit alone": (
        "",
        [("0.3.1", "feat: start", "", "thing"), ("", "chore(release): x", "", "thing")],
    ),
    "an unconventional subject": (
        "",
        [("0.3.1", "feat: start", "", "thing"), ("", "tidy things", "", "thing")],
    ),
    "a feature before 1.0": (
        "",
        [("0.3.1", "feat: start", "", "thing"), ("", "feat(x): y", "", "thing")],
    ),
    "a break before 1.0": (
        "",
        [("0.3.1", "feat: start", "", "thing"), ("", "fix!: y", "", "thing")],
    ),
    "a fix after 1.0": (
        "",
        [("1.2.3", "feat: start", "", "thing"), ("", "fix: x", "", "thing")],
    ),
    "a break after 1.0": (
        "",
        [("1.2.3", "feat: start", "", "thing"), ("", "refactor!: x", "", "thing")],
    ),
    "a breaking footer after 1.0": (
        "",
        [
            ("1.2.3", "feat: start", "", "thing"),
            ("", "fix: x", "BREAKING CHANGE: the flag went", "thing"),
        ],
    ),
}


@pytest.mark.parametrize(("baseline", "steps"), SCENARIOS.values(), ids=SCENARIOS)
def test_git_cliff_bumps_as_the_workshop_derives(
    tmp_path: Path, baseline: str, steps: list[Step]
) -> None:
    # The release train derives the version itself and hands it to
    # git-cliff; the template's [bump] rules answer the same version,
    # so git-cliff run by hand agrees with the train.
    import livery.toolroom.tools as tools
    from livery.workshop._shipped_files import deliver
    from livery.workshop._versions import derive_version

    root = _workspace(tmp_path / "ws", '["changelog"]')
    package = _member(root, "thing")
    _member(root, "other")
    if baseline:
        contract = package.directory / "workshop.toml"
        contract.write_text(
            contract.read_text() + f'\n[release]\nbaseline = "{baseline}"\n'
        )
    deliver(root)
    _repository(root)
    for tag, subject, body, where in steps:
        if subject:
            _commit(root, where, subject + (f"\n\n{body}" if body else ""))
        if tag:
            _git(root, "tag", f"packages/thing/v{tag}")
    result = tools.git_cliff.opts(cwd=root, nofail=True, recorded=False)(
        "--config", "packages/thing/cliff.toml", "--bumped-version", "--offline"
    )
    assert result.code == 0, result.stderr
    bumped = result.stdout.strip().rsplit("/", 1)[-1].removeprefix("v")
    assert bumped == derive_version(root, package)
