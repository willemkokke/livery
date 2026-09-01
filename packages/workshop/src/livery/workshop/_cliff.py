"""The changelog engine: git-cliff, driven per package.

Each package carries a ``cliff.toml`` rendered from the template,
which states its tag line, its paths, and the entry's shape. This
module runs git-cliff against that config: what the next version
would be, and the entry the commits since the last release earn.

The release verbs call these; nothing else reads commit history.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from footman import fail

from livery.workshop._packages import Package

#: Where a package's changelog contract lives.
CONFIG_NAME = "cliff.toml"


def config_path(package: Package) -> Path:
    """*package*'s changelog contract, or fail naming what to render."""
    path = package.directory / CONFIG_NAME
    if not path.is_file():
        fail(
            f"{package.path} has no {CONFIG_NAME}: run `fm template.apply`"
            " (or re-render the package) so the changelog contract exists"
        )
    return path


def _run(root: Path, package: Package, *args: str) -> str:
    """Run git-cliff for *package* under *root*; stdout, or fail.

    The failure is git-cliff's own words. A missing binary is named
    as the dependency it is, because the message a bare
    ``FileNotFoundError`` carries says nothing a reader can act on.
    """
    command = ["git-cliff", "--config", str(config_path(package)), *args]
    try:
        result = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        fail(
            "git-cliff is not installed: it writes the changelogs, and the"
            " dev group declares it. Run `fm sync`."
        )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "(no output)"
        fail(f"git-cliff exited {result.returncode}:\n{detail}")
    return result.stdout


def bumped_version(root: Path, package: Package) -> str:
    """The version *package*'s unreleased commits earn, bare semver.

    git-cliff answers with the whole tag (``packages/forge/v0.2.0``);
    the tag line is the config's business, so only the version
    crosses back. An empty answer means it could not decide, which
    the caller reports as nothing to release.
    """
    raw = _run(root, package, "--bumped-version").strip()
    if not raw:
        return ""
    return raw.rsplit("/", 1)[-1].removeprefix("v")


def unreleased_entry(root: Path, package: Package, version: str = "") -> str:
    """The changelog entry for what is unreleased in *package*.

    With *version*, the entry is headed by it and dated today; without
    one it is headed ``## [Unreleased]``, which is what a dev build's
    excerpt wants. Empty when no commit touches the package.
    """
    args = ["--unreleased", "--strip", "all"]
    if version:
        args += ["--tag", f"packages/{package.directory.name}/v{version}"]
    return _run(root, package, *args).strip()
