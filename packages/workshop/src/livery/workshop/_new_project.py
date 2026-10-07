"""``fm new.project``: birth, end to end, one idempotent verb.

Seed the contract, write the project's seeds, lock and sync, deliver
the extension content, generate the CI files, initialise git, then the
forge half: create the repository, assert its configuration, push,
and open the unarmed setup pull request. Every step detects done
and walks past it, so re-running is the recovery procedure and a
kill between any two steps costs nothing. ``--local`` is everything
that stays on the machine and nothing that leaves it.

The task is ``expose="global_only"``: it lives above any repository,
so the workspace it makes never needed one. The default extension stack
is the base extension; a branded App's stack arrives with the extension
axis.
"""

from __future__ import annotations

import datetime
import os
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import livery.footman as footman
import livery.toolroom.tools as tools
from livery.footman import Arg, doc, fail, hidden
from livery.forge import Forge, ForgeError, Repository
from livery.workshop._contract import toml_string
from livery.workshop._templates import new as new_group

if TYPE_CHECKING:
    from livery.toolroom.tools import Result

#: The web host each kind means when the contract carries no URL.
_PUBLIC_HOSTS = {"github": "https://github.com", "gitlab": "https://gitlab.com"}


#: The catalogue a newborn resolves its tools against: this extension's own
#: published index, a strongroom store served as static files under the
#: site the extension publishes. Hardcoded, and it moves when the site does.
PUBLISHED_INDEX = "https://docs.willem.net/livery/tools/"


def _sync_tools(root: Path) -> None:
    """Write the newborn's first `tools.lock` and install what it names.

    Without this a newborn's own gate reaches for checkers the store
    never installed: the lock says what to install and nothing writes
    one for a project that has never had one. Idempotent, like every
    other step of the birth. The engine takes the newborn's root as a
    value: this runs inside the birth's own task, and a task may not
    change the process directory.
    """
    from livery.workshop._tool_tasks import sync_tools

    sync_tools(root)


def _hand_off(
    root: Path,
    *,
    forge: str = "",
    owner: str = "",
    url: str = "",
    local: bool = False,
    extension: str = "",
) -> None:
    """Finish the birth with the runner *root*'s environment holds, inside *root*.

    Run as ``new.project --resume`` from the project's own directory,
    where the project's extensions are mounted at the versions it
    locked: its tools, its composed and generated files and its forge
    half come from them, which a process started elsewhere cannot
    promise. The answers ride in the contract; the flags pass what it
    does not hold. The child shares the console and its failure is the
    birth's.

    Raises:
        Failed: when the environment has no runner, naming it.
    """
    import sys

    from livery.workshop._pythons import scripts_dir

    prog = footman.prog()
    runner = scripts_dir(root / ".venv") / (
        f"{prog}.exe" if sys.platform == "win32" else prog
    )
    if not runner.is_file():
        fail(
            f"{runner} is missing: {root}'s environment has no {prog} to finish"
            f" the birth with. `uv sync` in {root} installs it; then run"
            f" `{prog} new.project --resume` there"
        )
    argv = [str(runner), "new.project", "--resume"]
    argv += [
        f"--{key}={value}"
        for key, value in (
            ("forge", forge),
            ("owner", owner),
            ("url", url),
            ("extension", extension),
        )
        if value
    ]
    if local:
        argv.append("--local")
    print(f"  environment: the birth continues with {runner}")
    footman.run(argv, cwd=root, capture=False)


def _git(root: Path, *args: str) -> str:
    """Run git under *root*; stdout, or fail with git's own words."""
    result = tools.git.opts(cwd=root, nofail=True, recorded=False)(*args)
    if result.code != 0:
        spelled = " ".join(args)
        fail(f"git {spelled} exited {result.code}:\n{result.stdout}{result.stderr}")
    return result.stdout


def _git_outside(*args: str) -> Result:
    """Run git where no repository is implied; the result, never a refusal.

    The birth verb runs above any repository, and several of its
    questions must not be answered by whatever repository the
    person happens to stand in. The temporary directory is that
    nowhere. Every caller reads the exit code itself.
    """
    return tools.git.opts(
        cwd=tempfile.gettempdir(),
        env=dict(os.environ),
        nofail=True,
        recorded=False,
    )(*args)


def _git_config(name: str) -> str:
    """A global git config value, empty when unset."""
    result = _git_outside("config", "--global", "--get", name)
    return result.stdout.strip() if result.code == 0 else ""


def _clone_url(kind: str, url: str, owner: str, name: str) -> str:
    """The remote the newborn pushes to, credential-free."""
    web = (url or _PUBLIC_HOSTS.get(kind, "")).rstrip("/")
    if not web:
        fail("the forge needs a URL: pass --url (gitea has no public default)")
    return f"{web}/{owner}/{name}.git"


def _remote_is_foreign(clone_url: str) -> bool:
    """Whether the remote already has commits (foreign, never adopted)."""
    listing = _git_outside("ls-remote", clone_url)
    return listing.code == 0 and bool(listing.stdout.strip())


def _connect(kind: str, url: str) -> tuple[Forge, str]:
    """The target forge on the everyday token ladder, and the token.

    The token rides back so the pushes can authenticate with it: the
    committed remote stays credential-free (a human's pushes ride
    their credential helper), and automation pushes to a
    token-carrying URL instead, the way the emitted GitLab lane and
    the stamp transport already do.
    """
    from livery.workshop._forge_lane import _connect as connect
    from livery.workshop._tokens import forge_token

    token, _ = forge_token(kind, url)
    forge = connect(kind, url, token or None)
    if not token:
        # The connection resolved its own dialect variable; the push
        # credential mirrors that resolution or automation cannot
        # push at all.
        token = forge.token
    return forge, token


def _push_target(clone_url: str, token: str) -> str:
    """Where pushes go: the token-embedded URL when one resolved.

    Only http(s) URLs can carry the credential; anything else pushes
    to the remote as-is. Never printed and never recorded: the
    caller passes it to git alone.
    """
    if token and clone_url.startswith(("http://", "https://")):
        scheme, _, rest = clone_url.partition("://")
        return f"{scheme}://oauth2:{token}@{rest}"
    return "origin"


def birth_extensions(builtin: list[str] | tuple[str, ...]) -> list[str]:
    """The extensions a birth lists, from the running App's builtin providers.

    Footman's own providers and the base are never listed. A stock App
    (the base among its builtins) lists the site's extension, the
    python formatter's, the type checker's and the test runner's first,
    as ruled; the list is in precedence order, so a brand's own
    extensions follow them and win.
    """
    kept = [entry for entry in builtin if not entry.startswith("footman.")]
    kept = kept or ["livery.workshop"]
    stack = [entry for entry in kept if entry != "livery.workshop"]
    if "livery.workshop" in kept:
        stack[:0] = ["docs", "ruff", "basedpyright", "pytest"]
    return stack


@new_group.task(name="project", expose="always", interactive=True, cwd="asinvoked")
def new_project(
    folder: Annotated[
        Arg[str],
        doc("where the project is born, an empty or new folder; this one by default"),
    ] = "",
    name: Annotated[
        str, doc("the workspace's name, also the repository's; the folder's by default")
    ] = "",
    forge: Annotated[str, doc("github, gitea, or gitlab; github by default")] = "",
    owner: Annotated[
        str, doc("the owner or organisation the repository lives under")
    ] = "",
    url: Annotated[
        str, doc("the forge server (empty for github.com and gitlab.com)")
    ] = "",
    description: Annotated[str, doc("one sentence for the virtual root")] = "",
    author: Annotated[str, doc("the authors entry's name (default: git config)")] = "",
    email: Annotated[str, doc("the authors entry's email (default: git config)")] = "",
    namespace: Annotated[str, doc("dotted namespace packages live in")] = "",
    extension: Annotated[
        str,
        doc(
            "also scaffold this named extension package and self-host it:"
            " the workspace becomes the extension's home"
        ),
    ] = "",
    local: Annotated[
        bool, doc("everything that stays on the machine, nothing that leaves it")
    ] = False,
    stack: Annotated[
        str,
        doc(
            "the extensions the workspace lists, comma-separated in precedence"
            " order, each as name or name[option,option]; the stock list by"
            " default"
        ),
    ] = "",
    resume: Annotated[bool, hidden] = False,
) -> None:
    """Create a workspace in an empty or new folder: W1 as one verb.

    The birth seeds the contract and the project's files, locks and
    syncs its environment, then hands the rest to the runner that
    environment holds, run as ``new.project --resume`` inside the
    project. There the project's own extensions are mounted, so its
    tools, its composed and generated files and its forge half come
    from them at the versions it locked. A folder that holds a project
    refuses, naming ``--resume``, which finishes that project's birth:
    every step of it detects done and walks past it, so it is also
    the recovery after a birth that stopped part way. Headless runs
    never hang; a missing required answer is a refusal listing what to
    pass.
    """
    # The task's directory is the launch directory (`asinvoked`), not
    # the directory of whichever tasks file mounted the verb: the folder
    # means where the caller stands.
    here = footman.cwd()
    root = (here / folder).resolve() if folder else here
    prog = footman.prog()
    held = (root / "workshop.toml").is_file()
    if resume:
        if not held:
            fail(
                f"{root} holds no project to resume: `{prog} new.project`"
                " births one into an empty or new folder"
            )
        from livery.workshop._extensions import workspace_root

        # Inside the project this process is its own runner; elsewhere
        # the project's runner finishes it.
        finish = _finish if workspace_root() == root else _hand_off
        finish(
            root, forge=forge, owner=owner, url=url, local=local, extension=extension
        )
        return
    if held:
        fail(
            f"{root} holds a project already; `{prog} new.project --resume`"
            " finishes its birth"
        )
    if root.exists() and any(root.iterdir()):
        fail(f"{root} is not empty: a project is born into an empty or new folder")
    name = name or root.name
    forge = forge or "github"
    _check_answers(name, forge=forge, owner=owner, url=url, local=local)
    root.mkdir(parents=True, exist_ok=True)

    # The contract: a birth-time seed the render never touches. The
    # list is the running App's own builtin providers, minus footman's
    # and the base, which is never listed (contract 19): a branded App's
    # children carry its extensions. Stock fm's carry the site's
    # extension, which rides in the workshop wheel.
    # footman.BUILTIN is an import-time snapshot of the stock brand;
    # the running App's own list lives in _paths.builtin() (a public
    # runtime accessor is footman#536's family).
    from livery.footman import _paths

    # A comma inside an entry's brackets separates its options.
    listed = [e.strip() for e in re.split(r",(?![^\[]*\])", stack) if e.strip()]
    stack_list = listed or birth_extensions(_paths.builtin())
    contract = root / "workshop.toml"
    spelled = ", ".join(f'"{entry}"' for entry in stack_list)
    year = str(datetime.datetime.now(tz=datetime.UTC).year)
    author_name = author or _git_config("user.name") or f"{name} authors"
    author_email = email or _git_config("user.email")
    person = f"{{ name = {toml_string(author_name)}"
    if author_email:
        person += f", email = {toml_string(author_email)}"
    person += " }"
    lines = [
        "[workspace]",
        f"name = {toml_string(name)}",
        "description = "
        + toml_string(description or f"The {name} monorepo (virtual root)."),
        f"namespace = {toml_string(namespace or name.replace('-', '_'))}",
        f"authors = [{person}]",
        f'copyright-year = "{year}"',
        f"extensions = [{spelled}]",
    ]
    lines += ["", "[forge]", f'kind = "{forge}"']
    if owner:
        lines.append(f'owner = "{owner}"')
    if url:
        lines.append(f'url = "{url}"')
    lines += [
        "",
        "[tools]",
        f'index = "{PUBLISHED_INDEX}"',
        "",
        "[ci]",
        'runners = ["ubuntu-latest"]',
        'required-context = "gate"',
    ]
    contract.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("  workshop.toml: seeded")

    _prepare(root)
    _hand_off(root, local=local, extension=extension)


def _check_answers(name: str, *, forge: str, owner: str, url: str, local: bool) -> None:
    """Refuse answers no birth can use, listing what to pass."""
    if not re.fullmatch(r"[a-z][a-z0-9-]*", name):
        fail(
            f"project name {name!r}: use lowercase letters, digits, hyphens"
            " (the folder's name, unless --name gives one)"
        )
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


def _prepare(root: Path) -> None:
    """Write what a newborn starts with, then lock and sync its environment.

    The seeds are the files a workspace starts with, its own from then
    on: one that exists is never touched, so a resumed birth writes
    only what a stopped one did not. The project file is composed from
    the answers and the lock reads it, so the composed files are
    written before the first lock.
    """
    from livery.workshop._identity import project_facts
    from livery.workshop._seeds import PROJECT, create
    from livery.workshop._shipped_files import deliver
    from livery.workshop._templates import package_injections
    from livery.workshop._uv import run_uv

    seeded = create(
        root, root, (PROJECT,), {**project_facts(root), **package_injections(root)}
    )
    print(f"  seeds: {len(seeded)} written" if seeded else "  seeds: already written")
    for line in deliver(root):
        print(line)
    run_uv("lock", root=root)
    run_uv("sync", root=root)
    print("  environment: locked and synced")


def _finish(
    root: Path,
    *,
    forge: str = "",
    owner: str = "",
    url: str = "",
    local: bool = False,
    extension: str = "",
) -> None:
    """Finish *root*'s birth from inside it: everything after its environment.

    The answers come from the contract the birth seeded; a flag passes
    what it does not hold, the owner a local birth left out, say.
    """
    from livery.workshop._contract import load_contract

    contract = load_contract(root / "workshop.toml")
    workspace = contract.get("workspace") or {}
    seeded = contract.get("forge") or {}
    name = str(workspace.get("name", root.name))
    description = str(workspace.get("description", ""))
    forge = forge or str(seeded.get("kind", "github"))
    owner = owner or str(seeded.get("owner", ""))
    url = url or str(seeded.get("url", ""))
    _check_answers(name, forge=forge, owner=owner, url=url, local=local)
    _prepare(root)

    _sync_tools(root)

    from livery.workshop._sync import sync_workspace

    for line in sync_workspace(root):
        print(line)

    from livery.workshop._templates import apply_project

    for changed in apply_project(root):
        print(f"  rendered: {changed}")

    if extension:
        _add_extension(root, extension)

    if not (root / ".git").is_dir():
        _git(root, "init", "-q", "--initial-branch=main")
        print("  git: initialised")
    else:
        print("  git: already initialised")
    heads = tools.git.opts(cwd=root, env=dict(os.environ), nofail=True, recorded=False)(
        "rev-parse", "--verify", "-q", "HEAD"
    )
    if heads.code != 0:
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
            f" the setup PR. `{footman.prog()} new.project --resume"
            " --owner=...` here finishes the forge half."
        )
        return

    clone_url = _clone_url(forge, url, owner, name)
    target, push_token = _connect(forge, url)
    push_to = _push_target(clone_url, push_token)
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
        _git(root, "push", "-q", "--force", "-u", push_to, "main")
        print("  pushed: main")
    else:
        pushed = tools.git.opts(cwd=root, nofail=True, recorded=False)(
            "push", "-q", "-u", push_to, "main"
        )
        if pushed.code == 0:
            print("  pushed: main")
        elif "protected branch" in f"{pushed.stdout}{pushed.stderr}":
            # The resumed repository already governs itself: main
            # moves through pull requests now, so local aftercare
            # stays here and rides the next submitted branch.
            print(
                "  main is protected (the birth already configured it):"
                " the local commits stay here; submit them as a pull"
                f" request with `{footman.prog()} submit`"
            )
        else:
            fail(f"git push exited {pushed.code}:\n{pushed.stdout}{pushed.stderr}")

    from livery.workshop._workflow_tasks import assert_configuration

    assert_configuration(root)

    _open_setup_pr(root, target.repository(owner, name), push_to)
    print(
        "  done: merge the setup PR to prove the gate; the repository"
        " is protected and live"
    )


def _add_extension(root: Path, extension: str) -> None:
    """Scaffold *extension* and self-host it: contract 19's home shape.

    The extension package renders from the ``package-extension``
    kind; the contract's stack gains its import path last, so the
    home composes with its own overlay at HEAD from the first
    commit. Idempotent: an already-listed extension walks past.
    """
    from livery.workshop._sync import sync_workspace
    from livery.workshop._templates import wire_package

    if (root / "packages" / extension).exists():
        print(f"  extension: packages/{extension} already scaffolded")
        return
    import_path = wire_package(root, extension, kind="package-extension")
    contract = root / "workshop.toml"
    text = contract.read_text("utf-8")
    if f'"{import_path}"' not in text:
        match = re.search(r"^extensions = \[(.*)\]$", text, flags=re.M)
        if match is None:
            fail(
                f"cannot self-host {import_path}: the contract has no"
                " extensions line; add it by hand, last"
            )
        listed = [item for item in (match.group(1).strip(),) if item]
        appended = (
            f"extensions = [{', '.join([*listed, f'{chr(34)}{import_path}{chr(34)}'])}]"
        )
        text = text[: match.start()] + appended + text[match.end() :]
        contract.write_text(text, encoding="utf-8")
        print(f"  extensions: {import_path} self-hosted, last in the stack")
    # The stack changed: re-deliver content and re-render through the
    # composed source, so the home's files carry its own overlay.
    from livery.workshop._templates import apply_project as reapply

    for line in sync_workspace(root):
        print(line)
    for changed in reapply(root):
        print(f"  rendered: {changed}")


def _pushed_by_us(root: Path, clone_url: str) -> bool:
    """Whether the remote's main is this checkout's history (a resume)."""
    git = tools.git.opts(cwd=root, env=dict(os.environ), nofail=True, recorded=False)
    listing = git("ls-remote", clone_url, "refs/heads/main")
    sha = listing.stdout.split()[0] if listing.stdout.strip() else ""
    if not sha:
        return True  # exists but empty: get-or-create proceeds
    return git("cat-file", "-e", sha).code == 0


#: The setup branch: a trivial change whose PR proves the gate wires
#: end to end before any real work rides it.
_SETUP_BRANCH = "chore/setup-check"


def _open_setup_pr(root: Path, repo: Repository, push_to: str) -> None:
    """Open the unarmed setup PR; find it instead when it exists."""
    found = repo.pr.find_by_head(_SETUP_BRANCH)
    if found is not None:
        print(f"  setup PR: already open (#{found.number})")
        return
    branches = _git(root, "branch", "--list", _SETUP_BRANCH)
    if not branches.strip():
        _git(root, "branch", _SETUP_BRANCH, "main")
    _git(root, "push", "-q", push_to, _SETUP_BRANCH)
    # The branch needs a diff or some forges refuse the PR; an empty
    # commit rides it, evaporating in the squash.
    tip = _git(root, "rev-parse", _SETUP_BRANCH).strip()
    main = _git(root, "rev-parse", "main").strip()
    if tip == main:
        _git(root, "switch", "-q", _SETUP_BRANCH)
        _git(root, "commit", "-q", "--allow-empty", "-m", "chore: setup check")
        _git(root, "push", "-q", push_to, _SETUP_BRANCH)
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
