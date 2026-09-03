"""``fm new.project``: birth, end to end, one idempotent verb.

Seed the contract, render the project kind, lock and sync, deliver
the layer content, generate the CI files, initialise git, then the
forge half: create the repository, assert its configuration, push,
and open the unarmed setup pull request. Every step detects done
and walks past it, so re-running is the recovery procedure and a
kill between any two steps costs nothing. ``--local`` is everything
that stays on the machine and nothing that leaves it.

The task is ``expose="global_only"``: it lives above any repository,
so the workspace it makes never needed one. The default layer stack
is the base layer; a branded App's stack arrives with the layer
axis.
"""

from __future__ import annotations

import datetime
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated

import footman
from footman import doc, fail

from livery.forge import Forge, ForgeError, Repository
from livery.workshop._templates import new as new_group

#: The web host each kind means when the contract carries no URL.
_PUBLIC_HOSTS = {"github": "https://github.com", "gitlab": "https://gitlab.com"}


def _git(root: Path, *args: str) -> str:
    """Run git under *root*; stdout, or fail with git's own words."""
    result = footman.run(["git", *args], cwd=root, nofail=True, recorded=False)
    if result.code != 0:
        spelled = " ".join(args)
        fail(f"git {spelled} exited {result.code}:\n{result.stdout}{result.stderr}")
    return result.stdout


def _git_config(name: str) -> str:
    """A global git config value, empty when unset."""
    result = subprocess.run(
        ["git", "config", "--global", "--get", name],
        capture_output=True,
        text=True,
        check=False,
        cwd=tempfile.gettempdir(),
        env=dict(os.environ),
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _clone_url(kind: str, url: str, owner: str, name: str) -> str:
    """The remote the newborn pushes to, credential-free."""
    web = (url or _PUBLIC_HOSTS.get(kind, "")).rstrip("/")
    if not web:
        fail("the forge needs a URL: pass --url (gitea has no public default)")
    return f"{web}/{owner}/{name}.git"


def _remote_is_foreign(clone_url: str) -> bool:
    """Whether the remote already has commits (foreign, never adopted)."""
    listing = subprocess.run(
        ["git", "ls-remote", clone_url],
        capture_output=True,
        text=True,
        check=False,
        cwd=tempfile.gettempdir(),
        env=dict(os.environ),
    )
    return listing.returncode == 0 and bool(listing.stdout.strip())


def _connect(kind: str, url: str) -> Forge:
    """The target forge, on the everyday token ladder."""
    from livery.workshop._forge_lane import _connect as connect
    from livery.workshop._tokens import forge_token

    token, _ = forge_token(kind, url)
    return connect(kind, url, token or None)


@new_group.task(name="project", expose="global_only", interactive=True)
def new_project(
    name: Annotated[str, doc("the workspace's name (also the repository name)")],
    forge: Annotated[str, doc("github, gitea, or gitlab")] = "github",
    owner: Annotated[
        str, doc("the owner or organisation the repository lives under")
    ] = "",
    url: Annotated[
        str, doc("the forge server (empty for github.com and gitlab.com)")
    ] = "",
    templates: Annotated[
        str, doc("template source override: a git URL, or a local directory")
    ] = "",
    description: Annotated[str, doc("one sentence for the virtual root")] = "",
    author: Annotated[str, doc("the authors entry's name (default: git config)")] = "",
    email: Annotated[str, doc("the authors entry's email (default: git config)")] = "",
    namespace: Annotated[str, doc("dotted namespace packages live in")] = "",
    local: Annotated[
        bool, doc("everything that stays on the machine, nothing that leaves it")
    ] = False,
) -> None:
    """Create a workspace from nothing: W1 as one idempotent verb.

    Renders into ``./<name>``. Re-running resumes: every step
    detects done and walks past it. Headless runs never hang; a
    missing required answer is a refusal listing what to pass.
    """
    if not re.fullmatch(r"[a-z][a-z0-9-]*", name):
        fail(f"project name {name!r}: use lowercase letters, digits, hyphens")
    if forge not in ("github", "gitea", "gitlab"):
        fail(f"unknown forge kind {forge!r}: use github, gitea, or gitlab")
    if not local and not owner:
        fail(
            "answers missing for the forge half: pass --owner (and --url"
            " for a self-hosted server), or --local for everything that"
            " stays on the machine"
        )
    if forge == "gitea" and not local and not url:
        fail("gitea has no public default server: pass --url")

    root = footman.cwd() / name
    root.mkdir(exist_ok=True)

    # The contract: a birth-time seed the render never touches.
    contract = root / "workshop.toml"
    if contract.is_file():
        print("  workshop.toml: already seeded")
    else:
        lines = [
            "[workspace]",
            'layers = ["livery.workshop"]',
        ]
        if templates:
            lines.append(f'templates = "{templates}"')
        lines += ["", "[forge]", f'kind = "{forge}"']
        if owner:
            lines.append(f'owner = "{owner}"')
        if url:
            lines.append(f'url = "{url}"')
        lines += [
            "",
            "[ci]",
            'runners = ["ubuntu-latest"]',
            'required_context = "gate"',
        ]
        contract.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("  workshop.toml: seeded")

    if (root / "pyproject.toml").is_file():
        print("  render: already born")
    else:
        from livery.workshop._templates import (
            read_answers,
            render,
            render_injections,
            resolve_source,
        )

        source, ref = resolve_source(root)
        year = str(datetime.datetime.now(tz=datetime.UTC).year)
        answers = {
            "kind": "project",
            "project_name": name,
            "project_description": description
            or f"The {name} monorepo (virtual root).",
            "author_name": author or _git_config("user.name") or f"{name} authors",
            "author_email": email or _git_config("user.email"),
            "copyright_year": year,
            "namespace_package": namespace or name.replace("-", "_"),
            "packages": [],
        }
        render(
            source,
            root,
            {**answers, **render_injections(root, answers)},
            ref=ref,
        )
        # The receipt's source line follows the contract, display-safe.
        stored = read_answers(root / ".copier-answers.yml")
        from livery.workshop._templates import _write_root_answers

        _write_root_answers(root, stored)
        print("  render: born")

    from livery.workshop._uv import run_uv

    run_uv("lock", root=root)
    run_uv("sync", root=root)
    print("  environment: locked and synced")

    from livery.workshop._sync import sync_workspace

    for line in sync_workspace(root):
        print(line)

    from livery.workshop._templates import apply_project

    for changed in apply_project(root):
        print(f"  rendered: {changed}")

    if not (root / ".git").is_dir():
        _git(root, "init", "-q", "--initial-branch=main")
        print("  git: initialised")
    else:
        print("  git: already initialised")
    heads = subprocess.run(
        ["git", "rev-parse", "--verify", "-q", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
        cwd=root,
        env=dict(os.environ),
    )
    if heads.returncode != 0:
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "chore: birth")
        print("  git: birth committed")
    else:
        _git(root, "add", "-A")
        status = _git(root, "status", "--porcelain")
        if status.strip():
            _git(root, "commit", "-q", "-m", "chore: birth aftercare")
            print("  git: aftercare committed")

    if local:
        print(
            "  --local: done. Skipped, because they leave the machine:"
            " repo.create, the repository configuration, the push, and"
            f" the setup PR. Re-run `{footman.prog()} new.project {name}"
            " --owner=...` without --local to finish the forge half."
        )
        return

    clone_url = _clone_url(forge, url, owner, name)
    target = _connect(forge, url)
    existing = target.get_repo(owner, name)
    created_now = False
    if existing is None:
        target.create_repo(
            owner,
            name,
            private=True,
            description=description or f"The {name} monorepo (virtual root).",
        )
        created_now = True
        print(f"  repository: created {owner}/{name}")
    elif _remote_is_foreign(clone_url) and not _pushed_by_us(root, clone_url):
        fail(
            f"{owner}/{name} already exists on the forge with its own"
            " history: pick another name, or delete the foreign"
            " repository first. Nothing was pushed."
        )
    else:
        print(f"  repository: {owner}/{name} already exists; adopting")

    remotes = _git(root, "remote")
    if "origin" in remotes.split():
        _git(root, "remote", "set-url", "origin", clone_url)
    else:
        _git(root, "remote", "add", "origin", clone_url)
    if created_now:
        # The protocol initialises a created repository with a default
        # branch, so the birth history replaces that init commit; the
        # repository is seconds old and this checkout is its author.
        _git(root, "push", "-q", "--force", "-u", "origin", "main")
    else:
        _git(root, "push", "-q", "-u", "origin", "main")
    print("  pushed: main")

    from livery.workshop._workflow_tasks import assert_configuration

    assert_configuration(root)

    _open_setup_pr(root, target.repository(owner, name))
    print(
        "  done: merge the setup PR to prove the gate; the repository"
        " is protected and live"
    )


def _pushed_by_us(root: Path, clone_url: str) -> bool:
    """Whether the remote's main is this checkout's history (a resume)."""
    listing = subprocess.run(
        ["git", "ls-remote", clone_url, "refs/heads/main"],
        capture_output=True,
        text=True,
        check=False,
        cwd=root,
        env=dict(os.environ),
    )
    sha = listing.stdout.split()[0] if listing.stdout.strip() else ""
    if not sha:
        return True  # exists but empty: get-or-create proceeds
    known = subprocess.run(
        ["git", "cat-file", "-e", sha],
        capture_output=True,
        check=False,
        cwd=root,
        env=dict(os.environ),
    )
    return known.returncode == 0


#: The setup branch: a trivial change whose PR proves the gate wires
#: end to end before any real work rides it.
_SETUP_BRANCH = "chore/setup-check"


def _open_setup_pr(root: Path, repo: Repository) -> None:
    """Open the unarmed setup PR; find it instead when it exists."""
    found = repo.pr.find_by_head(_SETUP_BRANCH)
    if found is not None:
        print(f"  setup PR: already open (#{found.number})")
        return
    branches = _git(root, "branch", "--list", _SETUP_BRANCH)
    if not branches.strip():
        _git(root, "branch", _SETUP_BRANCH, "main")
    _git(root, "push", "-q", "origin", _SETUP_BRANCH)
    # The branch needs a diff or some forges refuse the PR; an empty
    # commit rides it, evaporating in the squash.
    tip = _git(root, "rev-parse", _SETUP_BRANCH).strip()
    main = _git(root, "rev-parse", "main").strip()
    if tip == main:
        _git(root, "switch", "-q", _SETUP_BRANCH)
        _git(root, "commit", "-q", "--allow-empty", "-m", "chore: setup check")
        _git(root, "push", "-q", "origin", _SETUP_BRANCH)
        _git(root, "switch", "-q", "main")
    try:
        opened = repo.pr.open(
            _SETUP_BRANCH,
            "main",
            "chore: setup check",
            "The birth verb's proof: CI runs, the gate reports, branch"
            " protection holds. Merge when green; the squash evaporates"
            " the empty commit.",
        )
    except ForgeError as error:
        fail(f"the setup PR did not open:\n{error}")
    print(f"  setup PR: opened #{opened.number}, unarmed")
