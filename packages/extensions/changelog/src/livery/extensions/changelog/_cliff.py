"""The changelog extension's engine: git-cliff, driven per package.

Each package carries a ``cliff.toml``, the extension's per-package
content, which states its tag line, its paths, and the entry's shape.
This module runs git-cliff against that config for the entry the
commits since the last release earn, and `NOTES`, which the
extension's ``extension.toml`` names as its ``release-notes``, writes
it into the package's ``CHANGELOG.md``. The next version is not
git-cliff's: the workshop derives it from the same commits.
"""

from __future__ import annotations

import os
from pathlib import Path

import livery.footman as footman
import livery.toolroom.tools as tools
from livery.footman import fail
from livery.workshop import Package

#: Where a package's changelog contract lives.
CONFIG_NAME = "cliff.toml"

#: git-cliff's own environment contract speaks per-kind names; this
#: is the one mapping site that feeds them, from FORGE_TOKEN. A
#: per-kind variable already in the environment reaches git-cliff
#: untouched.
TOKEN_VARIABLE = {
    "github": "GITHUB_TOKEN",
    "gitea": "GITEA_TOKEN",
    "gitlab": "GITLAB_TOKEN",
}


def _forge_facts(root: Path) -> tuple[str, str]:
    """The contract's forge kind and url, empty when unstated."""
    from livery.workshop import read_contract

    forge = read_contract(root).get("forge") or {}
    return str(forge.get("kind", "")), str(forge.get("url", ""))


def _credential(root: Path) -> tuple[str, str]:
    """(git-cliff variable, value) for this forge, or two empties.

    A per-kind variable already set wins untouched; otherwise the token
    the workshop's forge connects with: ``FORGE_TOKEN``, host-qualified
    first, and failing that the backend's own (the GitHub backend's
    ``gh auth token``, say), so the changelog credits authors wherever
    the workspace can ask for them. The value is handed to git-cliff
    under the name its own contract reads.
    """
    kind, _url = _forge_facts(root)
    variable = TOKEN_VARIABLE.get(kind, "")
    if not variable:
        return "", ""
    ambient = os.environ.get(variable, "")
    if ambient:
        return variable, ambient
    token = _lane_token(root)
    return (variable, token) if token else ("", "")


def _lane_token(root: Path) -> str:
    """The token the workshop's forge connects with, or empty when it has none.

    A backend whose own fallback finds a token answers it; one that
    refuses to connect without a token, as Gitea and GitLab do, means
    the workspace has none either.
    """
    from livery.forge import ForgeError
    from livery.workshop import this_forge

    try:
        return this_forge(root).token
    except ForgeError:
        return ""


def credit_is_reachable(root: Path) -> bool:
    """Whether a credential is in reach to ask the forge who wrote what.

    The changelog names authors by asking the forge, which a private
    repository answers only for a caller it can authenticate. Without
    the credential git-cliff stops rather than degrading, so the
    caller runs it offline instead.
    """
    return bool(_credential(root)[1])


def config_path(package: Package) -> Path:
    """*package*'s changelog contract, or fail naming what to render."""
    path = package.directory / CONFIG_NAME
    if not path.is_file():
        fail(
            f"{package.path} has no {CONFIG_NAME}:"
            f" run `{footman.prog()} sync`, which composes it from the changelog"
            " extension's fragment"
        )
    return path


def _run(root: Path, package: Package, *args: str, offline: bool = False) -> str:
    """Run git-cliff for *package* under *root*; stdout, or fail.

    Runs offline when *offline* asks it to, and when no credential is
    in reach, which is what a private repository without its token
    looks like: git-cliff would otherwise stop on the forge's refusal,
    and an entry without its authors beats no entry at all. The failure
    is git-cliff's own words, and a refused author lookup says why the
    forge refused it. The run goes through the tool's handle, so it
    carries a receipt like every other tool the lock supplies. A
    missing binary is named as the dependency it is, because the
    message a bare ``FileNotFoundError`` carries says nothing a reader
    can act on.
    """
    command = ["--config", str(config_path(package)), *args]
    variable, token = ("", "") if offline else _credential(root)
    if offline:
        command.append("--offline")
    elif not token:
        print("  writing the entry without its authors: set FORGE_TOKEN to credit them")
        command.append("--offline")
    child_env = {**os.environ}
    if token:
        child_env[variable] = token
    try:
        result = tools.git_cliff.opts(
            cwd=root, env=child_env, nofail=True, recorded=False
        )(*command)
    except (FileNotFoundError, tools.ToolError):
        fail(
            "git-cliff is not installed: it writes the changelogs, and the"
            f" lock supplies it. Run `{footman.prog()} sync`."
        )
    if result.code != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "(no output)"
        if "metadata" in detail:
            detail += _refused_lookup(root)
        fail(f"git-cliff exited {result.code}:\n{detail}")
    return result.stdout


def _refused_lookup(root: Path) -> str:
    """Why the forge refused the author lookup the credit needs, as the next act.

    The forge answers a spent API budget with the same status as a
    refused token, so one read through the forge lane tells them apart:
    a spent budget names when it renews. Otherwise the token cannot
    read this repository, or the contract's forge url is not the server
    git-cliff reached.
    """
    import time

    from livery.forge import ForgeError, RateLimited
    from livery.workshop import this_repository

    try:
        this_repository(root).pr.get(1)
    except RateLimited as spent:
        renews = (
            f"it renews at {time.strftime('%H:%M', time.localtime(spent.reset_at))}"
            if spent.reset_at is not None
            else "it renews within the hour"
        )
        return f"\n  the forge's API budget is spent: {renews}; run this again then"
    except ForgeError:
        pass
    return (
        "\n  the forge refused the author lookup: check FORGE_TOKEN"
        f" can read this repository, and that {CONFIG_NAME}'s api_url"
        " names the server root (with /api/v4 on GitLab)"
    )


def unreleased_entry(root: Path, package: Package, version: str = "") -> str:
    """The changelog entry for what is unreleased in *package*.

    With *version*, the entry is headed by it and dated today, and
    credits its authors where a credential is in reach. Without one it
    is headed ``## [Unreleased]``, which is what a dev build's excerpt
    wants, and credits no one: nobody credits a dev build's authors,
    and the lookup pages through every pull request the forge holds,
    once per package, against the forge's API budget. Empty when no
    commit touches the package.
    """
    args = ["--unreleased", "--strip", "all"]
    if version:
        args += ["--tag", f"packages/{package.member}/v{version}"]
    return _run(root, package, *args, offline=not version).strip()


class CliffChangelog:
    """Release notes as git-cliff entries in each package's ``CHANGELOG.md``.

    The extension's [livery.workshop.ReleaseNotes][] provider: the entry
    is git-cliff's, through the package's ``cliff.toml``; the history is
    the package's ``CHANGELOG.md``, created at the first record, newest
    entry first.
    """

    def entry(self, root: Path, package: Package, version: str = "") -> str:
        """The entry git-cliff writes for what is unreleased in *package*."""
        return unreleased_entry(root, package, version)

    def record(self, package: Package, version: str, entry: str) -> list[str]:
        """Write *version*'s *entry* at the top of ``CHANGELOG.md``.

        An entry already under *version*'s heading is replaced when a
        new one is given: the stranded shape, a heading whose tag never
        cut, regenerates rather than under-documenting what ships.
        """
        changelog = package.directory / "CHANGELOG.md"
        text = changelog.read_text("utf-8") if changelog.is_file() else "# Changelog\n"
        if f"## {version}" not in text and f"## [{version}]" not in text:
            # A blank line on each side, so the new entry and the one it
            # sits above stay separate blocks.
            insert = "\n" + (entry or f"## [{version}]\n\n-").strip() + "\n"
            first_entry = text.find("\n## ")
            if first_entry == -1:
                text = text.rstrip("\n") + "\n" + insert
            else:
                text = text[:first_entry] + insert + text[first_entry:]
            changelog.write_text(text, encoding="utf-8")
            return ["CHANGELOG.md (review the entry before tagging)"]
        if entry:
            rewritten = _replace_entry(text, version, entry)
            if rewritten != text:
                changelog.write_text(rewritten, encoding="utf-8")
                return ["CHANGELOG.md (the stranded entry regenerated; review it)"]
        return []

    def verify(self, package: Package, version: str) -> list[str]:
        """A missing ``## <version>`` entry in ``CHANGELOG.md``, or nothing."""
        changelog = package.directory / "CHANGELOG.md"
        body = changelog.read_text("utf-8") if changelog.is_file() else ""
        if f"## {version}" in body or f"## [{version}]" in body:
            return []
        return [f"CHANGELOG.md has no '## {version}' entry"]


def _replace_entry(text: str, version: str, entry_body: str) -> str:
    """*text* with *version*'s entry block replaced by *entry_body*."""
    import re

    pattern = re.compile(
        rf"^## \[?{re.escape(version)}\]?[^\n]*\n.*?(?=^## |\Z)",
        flags=re.M | re.S,
    )
    replacement = entry_body.strip() + "\n\n"
    rewritten, count = pattern.subn(lambda _m: replacement, text, count=1)
    return rewritten if count else text


#: The provider the extension's ``extension.toml`` names as its
#: ``release-notes``, which the workshop registers at mount.
NOTES = CliffChangelog()
