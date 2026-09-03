"""The changelog engine: git-cliff, driven per package.

Each package carries a ``cliff.toml`` rendered from the template,
which states its tag line, its paths, and the entry's shape. This
module runs git-cliff against that config: what the next version
would be, and the entry the commits since the last release earn.

The release verbs call these; nothing else reads commit history.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

import footman
import toolroom
from footman import fail

from livery.workshop._packages import Package

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
    contract = tomllib.loads((root / "workshop.toml").read_text("utf-8"))
    forge = contract.get("forge") or {}
    return str(forge.get("kind", "")), str(forge.get("url", ""))


def _forge_kind(root: Path) -> str:
    """The workspace contract's forge kind, or empty when unstated."""
    return _forge_facts(root)[0]


def _credential(root: Path) -> tuple[str, str]:
    """(git-cliff variable, value) for this forge, or two empties.

    A per-kind variable already set wins untouched; otherwise
    ``FORGE_TOKEN`` resolves through livery.workshop._tokens and is
    handed to git-cliff under the name its own contract reads.
    """
    from livery.workshop._tokens import forge_token

    kind, url = _forge_facts(root)
    variable = TOKEN_VARIABLE.get(kind, "")
    if not variable:
        return "", ""
    ambient = os.environ.get(variable, "")
    if ambient:
        return variable, ambient
    token, _ = forge_token(kind, url)
    return (variable, token) if token else ("", "")


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
            f" run `{footman.prog()} template.apply`"
            " (or re-render the package) so the changelog contract exists"
        )
    return path


def _run(root: Path, package: Package, *args: str) -> str:
    """Run git-cliff for *package* under *root*; stdout, or fail.

    Runs offline when no credential is in reach, which is what a
    private repository without its token looks like: git-cliff would
    otherwise stop on the forge's refusal, and an entry without its
    authors beats no entry at all. The failure is git-cliff's own
    words. A missing binary is named as the dependency it is, because
    the message a bare ``FileNotFoundError`` carries says nothing a
    reader can act on.
    """
    command = ["--config", str(config_path(package)), *args]
    variable, token = _credential(root)
    if not token:
        print("  writing the entry without its authors: set FORGE_TOKEN to credit them")
        command.append("--offline")
    child_env = {**os.environ}
    if token:
        child_env[variable] = token
    try:
        result = footman.run(
            ["git-cliff", *command],
            cwd=root,
            env=child_env,
            nofail=True,
            recorded=False,
        )
    except (FileNotFoundError, toolroom.ToolError):
        fail(
            "git-cliff is not installed: it writes the changelogs, and the"
            f" dev group declares it. Run `{footman.prog()} sync`."
        )
    if result.code != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "(no output)"
        if "metadata" in detail:
            # The forge refused the lookup the credit needs: the token
            # cannot read this repository, or the contract's forge url
            # is not the server git-cliff reached.
            detail += (
                "\n  the forge refused the author lookup: check FORGE_TOKEN"
                f" can read this repository, and that {CONFIG_NAME}'s api_url"
                " names the server root"
            )
        fail(f"git-cliff exited {result.code}:\n{detail}")
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
