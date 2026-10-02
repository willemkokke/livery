"""The next version, derived from the commits, agrees with git-cliff.

Each scenario builds a throwaway repository with one member, its
receipt tags and its commits, then asks both git-cliff (through the
member's rendered ``cliff.toml``) and the base's derivation. The two
must agree on every scenario: the derivation replaces git-cliff's
answer in the release train, so git-cliff's behaviour is the
contract it is pinned to.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import livery.toolroom.tools.api as tools
from livery.workshop._packages import discover_packages
from livery.workshop._versions import bump, derive_version
from workshop_seeds import cliff_config

#: (name, baseline, [(tag before this commit or "", subject, body, path)], expected)
Step = tuple[str, str, str, str]
SCENARIOS: list[tuple[str, str, list[Step], str]] = [
    ("no tag yet: the baseline", "0.1.0", [("", "feat: start", "", "member")], "0.1.0"),
    ("no tag and no baseline: 0.0.0", "", [("", "feat: start", "", "member")], "0.0.0"),
    (
        "tagged with nothing since: unchanged",
        "",
        [("", "feat: start", "", "member"), ("0.3.1", "", "", "")],
        "0.3.1",
    ),
    (
        "a commit outside the member: unchanged",
        "",
        [("0.3.1", "feat: start", "", "member"), ("", "feat: elsewhere", "", "other")],
        "0.3.1",
    ),
    (
        "a release commit alone: unchanged",
        "",
        [
            ("0.3.1", "feat: start", "", "member"),
            ("", "chore(release): x", "", "member"),
        ],
        "0.3.1",
    ),
    (
        "an unconventional subject: a patch",
        "",
        [("0.3.1", "feat: start", "", "member"), ("", "tidy things", "", "member")],
        "0.3.2",
    ),
    (
        "a fix before 1.0: a patch",
        "",
        [("0.3.1", "feat: start", "", "member"), ("", "fix: x", "", "member")],
        "0.3.2",
    ),
    (
        "a feature before 1.0: a minor",
        "",
        [("0.3.1", "feat: start", "", "member"), ("", "feat(x): y", "", "member")],
        "0.4.0",
    ),
    (
        "a break before 1.0: a minor",
        "",
        [("0.3.1", "feat: start", "", "member"), ("", "fix!: y", "", "member")],
        "0.4.0",
    ),
    (
        "a fix after 1.0: a patch",
        "",
        [("1.2.3", "feat: start", "", "member"), ("", "fix: x", "", "member")],
        "1.2.4",
    ),
    (
        "a feature after 1.0: a minor",
        "",
        [("1.2.3", "feat: start", "", "member"), ("", "feat: x", "", "member")],
        "1.3.0",
    ),
    (
        "a break after 1.0: a major",
        "",
        [("1.2.3", "feat: start", "", "member"), ("", "refactor!: x", "", "member")],
        "2.0.0",
    ),
    (
        "a breaking footer after 1.0: a major",
        "",
        [
            ("1.2.3", "feat: start", "", "member"),
            ("", "fix: x", "BREAKING CHANGE: the flag went", "member"),
        ],
        "2.0.0",
    ),
    (
        "the strongest commit decides",
        "",
        [
            ("0.3.1", "feat: start", "", "member"),
            ("", "fix: a", "", "member"),
            ("", "feat: b", "", "member"),
            ("", "docs: c", "", "member"),
        ],
        "0.4.0",
    ),
]


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def _repository(tmp_path: Path, baseline: str, steps: list[Step]) -> Path:
    root = tmp_path / "repo"
    member = root / "packages" / "member"
    member.mkdir(parents=True)
    other = root / "packages" / "other"
    other.mkdir(parents=True)
    (other / "workshop.toml").write_text(
        'kind = "python"\nname = "livery-other"\n', encoding="utf-8"
    )
    for name, directory in (("member", member), ("other", other)):
        (directory / "pyproject.toml").write_text(
            f'[project]\nname = "livery-{name}"\nversion = "0.0.0"\n',
            encoding="utf-8",
        )
    (root / "workshop.toml").write_text("[workspace]\n", encoding="utf-8")
    release = f'\n[release]\nbaseline = "{baseline}"\n' if baseline else ""
    (member / "workshop.toml").write_text(
        f'kind = "python"\nname = "livery-member"\n{release}', encoding="utf-8"
    )
    config = cliff_config("member").replace(
        'initial_tag = "packages/member/v0.0.0"',
        f'initial_tag = "packages/member/v{baseline or "0.0.0"}"',
    )
    (member / "cliff.toml").write_text(config, encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "commit.gpgsign", "false")
    for index, (tag, subject, body, where) in enumerate(steps):
        if subject:
            changed = root / "packages" / where / f"file{index}.txt"
            changed.write_text(str(index), encoding="utf-8")
            _git(root, "add", "-A")
            message = subject + (f"\n\n{body}" if body else "")
            _git(root, "commit", "-q", "-m", message)
        if tag:
            _git(root, "tag", f"packages/member/v{tag}")
    return root


def _git_cliff(root: Path) -> str:
    result = tools.git_cliff.opts(cwd=root, nofail=True, recorded=False)(
        "--config", "packages/member/cliff.toml", "--bumped-version", "--offline"
    )
    assert result.code == 0, result.stderr
    return result.stdout.strip().rsplit("/", 1)[-1].removeprefix("v")


@pytest.mark.parametrize(
    ("baseline", "steps", "expected"),
    [pytest.param(b, s, e, id=name) for name, b, s, e in SCENARIOS],
)
def test_the_derivation_agrees_with_git_cliff(
    tmp_path: Path, baseline: str, steps: list[Step], expected: str
) -> None:
    root = _repository(tmp_path, baseline, steps)
    (package,) = (p for p in discover_packages(root) if p.directory.name == "member")
    assert derive_version(root, package) == expected
    assert _git_cliff(root) == expected


def test_a_version_that_is_not_semver_refuses() -> None:
    with pytest.raises(ValueError):
        bump("1.2", [("fix: x", "")])


def test_a_given_receipt_skips_reading_the_tags(tmp_path: Path) -> None:
    root = _repository(
        tmp_path,
        "",
        [("0.3.1", "feat: start", "", "member"), ("", "fix: x", "", "member")],
    )
    (package,) = (p for p in discover_packages(root) if p.directory.name == "member")
    assert derive_version(root, package, released="0.3.1") == "0.3.2"
