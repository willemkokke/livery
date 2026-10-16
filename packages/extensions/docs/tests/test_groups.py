"""The site's release history of a package in a group directory.

A group directory is ``packages/<group>/<name>/``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _package(root: Path, member: str) -> Path:
    directory = root / "packages" / member
    directory.mkdir(parents=True)
    name = "livery-" + member.replace("/", "-")
    (directory / "workshop.toml").write_text(
        f'extensions = ["python"]\nname = "{name}"\n'
    )
    (directory / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\ndependencies = []\n'
    )
    return directory


def test_the_release_history_lists_a_grouped_package_newest_first(
    tmp_path: Path,
) -> None:
    from livery.extensions.docs._site import _receipt_tags

    _package(tmp_path, "forge")
    _package(tmp_path, "extensions/ruff")

    def git(*args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )

    git("init", "-q")
    git("commit", "-q", "--allow-empty", "--no-gpg-sign", "-m", "seed")
    for tag in (
        "packages/forge/v0.9.0",
        "packages/forge/v0.10.0",
        "packages/extensions/ruff/v0.1.0",
        "archive/setup",
    ):
        git("tag", tag)
    rows = [row[1:] for row in _receipt_tags(tmp_path)]
    assert rows == [
        ("forge", "livery-forge", "0.10.0"),
        ("forge", "livery-forge", "0.9.0"),
        ("extensions/ruff", "livery-extensions-ruff", "0.1.0"),
    ]
