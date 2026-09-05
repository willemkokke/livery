"""The ``issue`` family: the shared work pool and its lifecycle.

``issue.create``, ``issue.list``, and ``issue.search`` are the pool's
reads and the one write that files work. ``issue.start`` picks an
issue up: assign within the workspace's limit, branch from a fetched
base, and open the work, in a linked worktree by default.
``issue.stop`` is start's inverse: unassign, clean up the local
state, and leave the remote branch as the pool's copy.
``issue.close`` closes the issue itself, tearing the submission down
through the shared mechanism and recording where the work got to.

One destruction rule holds everywhere: work that exists nowhere else
is never destroyed without ``--discard``, and the refusal's prose
names the situation it found.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Annotated

import footman
import toolroom
from footman import Arg, ask, doc, fail, group, suggest

from livery.forge import ForgeError, Repository
from livery.workshop._git_ops import GitOps

_KINDS = ("feat", "fix", "chore", "docs", "refactor")
_SLUG_CAP = 40
_BRANCH_RE = re.compile(r"^(?:feat|fix|chore|docs|refactor)/(\d+)-")

issue = group("issue", help="Issues: the shared work pool")


def _workspace() -> Path:
    from livery.workshop._layers import workspace_root

    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    return root


def _repo(root: Path) -> Repository:
    from livery.workshop._forge_lane import this_repository

    return this_repository(root)


def assignee_limit(root: Path) -> int:
    """The workspace's assignee limit; policy, not a forge fact.

    ``[issues] assignees`` in the root ``workshop.toml``, default 1 so
    a free GitLab needs nothing. Enforced by the workshop on every
    forge, including ones whose native limit is higher; whether the
    forge itself can honour it is ``configure``'s check.
    """
    import tomllib

    contract = root / "workshop.toml"
    if not contract.is_file():
        return 1
    data = tomllib.loads(contract.read_text("utf-8"))
    return int(data.get("issues", {}).get("assignees", 1))


def parse_ref(token: str) -> tuple[int, str]:
    """*token* as ``(number, "")`` or ``(0, title)``.

    A ``#``-stripped all-digits token is a number; anything else is a
    title to file first. The strip has a reason: read as a title it
    would file a junk issue named with the hash.
    """
    bare = token.lstrip("#")
    if bare.isdigit():
        return int(bare), ""
    return 0, token


def _slug(title: str) -> str:
    kebab = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return kebab[:_SLUG_CAP].rstrip("-") or "work"


def branch_name(kind: str, number: int, title: str) -> str:
    """``<kind>/<number>-<slug>``: the number carries identity."""
    return f"{kind}/{number}-{_slug(title)}"


def worktree_path(root: Path, number: int, title: str) -> Path:
    """Where the issue's worktree lives: under the runner's home.

    Outside every repository, so repo tooling never walks into a
    worktree, and under a per-user home so a worktree's venv
    hardlinks from the same filesystem's cache. The home is the
    runner's own data directory, asked of footman: a footman plugin
    owns no home of its own.
    """
    import footman

    return footman.data_dir() / "worktrees" / root.name / f"{number}-{_slug(title)}"


def _open_numbers() -> list[str]:
    """Completion: the open issue numbers; never raises.

    Offline, token-less, or outside a repo must cost the
    suggestions, never the command.
    """
    try:
        root = _workspace()
        return [str(row.number) for row in _repo(root).issue.list(state="open")]
    except Exception:
        return []


def _issue_kind(labels: tuple[str, ...], override: str) -> str:
    if override:
        return override
    for label in labels:
        _, _, kind = label.partition("kind/")
        if kind in _KINDS:
            return kind
    return "feat"


def _find_branch(git: GitOps, number: int) -> str:
    """The issue's branch, from the local then remote listings."""
    needle = f"/{number}-"
    for name in git.local_branches(""):
        if _BRANCH_RE.match(name) and needle in name:
            return name
    for name in git.remote_branches(""):
        if _BRANCH_RE.match(name) and needle in name:
            return name
    return ""


def _worktree_for(git: GitOps, root: Path, branch: str) -> str:
    """The linked worktree holding *branch*; "" when none does."""
    listing = git._run("worktree", "list", "--porcelain")
    tree = ""
    current: dict[str, str] = {}
    for line in [*listing.splitlines(), ""]:
        if not line.strip():
            if current.get("branch", "").endswith(f"refs/heads/{branch}"):
                tree = current.get("worktree", "")
            current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    if tree and Path(tree).resolve() == root.resolve():
        return ""
    return tree


def _only_local_work(git: GitOps, root: Path, branch: str) -> str:
    """Why *branch* holds work that exists nowhere else; "" when safe.

    Dirt is looked for where the branch actually lives: this
    checkout when it stands on the branch, the branch's linked
    worktree otherwise. Ahead is measured against the remote branch
    when one exists, and against the base when none does.
    """
    if git.current_branch() == branch and not git.is_clean():
        return "uncommitted changes"
    tree = _worktree_for(git, root, branch)
    if tree:
        status = git._run("-C", tree, "status", "--porcelain").strip()
        if status:
            return f"uncommitted changes in the worktree {tree}"
    remote = f"origin/{branch}"
    upstream = remote if branch in git.remote_branches("") else "origin/main"
    count = git._run("rev-list", "--count", f"{upstream}..{branch}").strip()
    if count.isdigit() and int(count) > 0:
        where = "the remote branch" if upstream == remote else "any remote"
        return f"{count} commit(s) not on {where}"
    return ""


@issue.default
def issue_default() -> None:
    """List the open issues (the bare ``fm issue``)."""
    issue_list()


@issue.task(name="list")
def issue_list() -> None:
    """The open issues: number, title, who is on them."""
    root = _workspace()
    rows = _repo(root).issue.list(state="open")
    if not rows:
        print("  no open issues")
        return
    for row in rows:
        holders = f"  ({', '.join(row.assignees)})" if row.assignees else ""
        print(f"  #{row.number}  {row.title}{holders}")


@issue.task(name="search")
def issue_search(text: Arg[str] = "") -> None:
    """The open issues whose title or body contains *text*."""
    if not text:
        fail(f"name the text to search for: `{footman.prog()} issue.search watcher`")
    root = _workspace()
    for row in _repo(root).issue.search(text):
        print(f"  #{row.number}  {row.title}")


@issue.task(name="create")
def issue_create(
    title: Arg[str] = "",
    type: Annotated[str, doc("kind label: feat, fix, chore, docs, refactor")] = "feat",
    body: Annotated[str, doc("the issue body, readable by an outsider")] = "",
) -> None:
    """File a new issue; ``issue.start`` with a title files and starts.

    The kind label is best-effort: a forge or token that refuses
    labels costs the label, never the issue. Every filed issue
    should carry a body an outsider can read; correct one later
    with ``issue.update``.
    """
    if not title:
        fail(f'name the issue: `{footman.prog()} issue.create "fix the flaky watch"`')
    root = _workspace()
    repo = _repo(root)
    if type not in _KINDS:
        fail(f"unknown type {type!r}: one of {', '.join(_KINDS)}")
    try:
        created = repo.issue.create(title, body=body, labels=(f"kind/{type}",))
    except ForgeError:
        created = repo.issue.create(title, body=body)
        print("  Note: the kind label was refused; the issue is filed without it")
    print(f"  filed #{created.number}: {created.title}")
    if created.url:
        print(f"  {created.url}")


@issue.task(name="update")
def issue_update(
    number: Arg[int] = 0,
    title: Annotated[str, doc("the corrected title (empty keeps it)")] = "",
    body: Annotated[str, doc("the corrected body (empty keeps it)")] = "",
) -> None:
    """Rewrite an issue's title, body, or both.

    Only the provided fields change; clearing a text to empty is not
    part of the contract. The issue is read first, so a wrong number
    refuses by name instead of writing nowhere.
    """
    if not number:
        fail(f"name the issue: `{footman.prog()} issue.update 123 --body=...`")
    if not title and not body:
        fail("nothing to change: pass --title, --body, or both")
    root = _workspace()
    repo = _repo(root)
    found = repo.issue.get(number)
    if found is None:
        fail(f"issue #{number} does not exist in this repository")
    repo.issue.update(number, title=title, body=body)
    changed = ", ".join(
        name for name, value in (("title", title), ("body", body)) if value
    )
    print(f"  #{number}: {changed} updated")


@issue.task(name="start", interactive=True)
def issue_start(
    ref: Annotated[Arg[str], ask(), suggest(_open_numbers, strict=False)] = "",
    type: Annotated[str, doc("kind override: feat, fix, chore, docs, refactor")] = "",
    body: Annotated[str, doc("the issue body when the title form files one")] = "",
    wip: Annotated[
        bool, doc("park the dirty tree as a commit and reuse this checkout")
    ] = False,
    worktree: Annotated[
        bool, doc("open the work in a linked worktree (the default)")
    ] = True,
    agent: Annotated[
        str, doc("hand the issue to a coding agent (implies a worktree)")
    ] = "",
    prompt: Annotated[
        str, doc("an initial prompt appended to the agent's briefing")
    ] = "",
    open: Annotated[str, doc("how to open a worktree: code, shell, or none")] = "",
) -> None:
    """Start work on an issue: assign it, branch, and open the work.

    REF is a number (a leading ``#`` is accepted) or a quoted title,
    which files the issue first. Assignment is documentation: at the
    workspace's assignee limit, or when the forge refuses the write,
    the start proceeds with a warning that others will not see you
    on the issue. The branch is
    ``<kind>/<number>-<slug>``, always from a fetched
    ``origin/main`` so a stale base is impossible. A worktree under
    the runner's home is the default; ``--no-worktree`` reuses this
    checkout, where a dirty tree refuses naming ``--wip`` (park the
    tree as a commit; squash-only merging evaporates it) as the
    escape. ``--agent`` launches the named agent in the worktree
    with a minimal briefing; the worktree's own instructions are the
    real ones.
    """
    if not ref:
        fail(
            f"name an issue: a number (`{footman.prog()} issue.start 123`) or a quoted"
            f' title (`{footman.prog()} issue.start "fix the flaky watch"`)'
        )
    root = _workspace()
    repo = _repo(root)
    git = GitOps(root)
    me = _me(repo)
    if agent:
        worktree = True
    if type and type not in _KINDS:
        fail(f"unknown type {type!r}: one of {', '.join(_KINDS)}")

    number, title = parse_ref(ref)
    if number:
        found = repo.issue.get(number)
        if found is None:
            fail(f"issue #{number} does not exist in this repository")
        work = found
    else:
        try:
            work = repo.issue.create(
                title, body=body, labels=(f"kind/{type or 'feat'}",)
            )
        except ForgeError:
            work = repo.issue.create(title, body=body)
            print("  Note: the kind label was refused; filed without it")
        print(f"  filed #{work.number}: {work.title}")

    # Assignment is documentation, never a gate: it tells the pool
    # who is working, and not being listed costs visibility, not the
    # work. At the limit the assign is skipped with the warning; a
    # forge that refuses the write costs the same visibility.
    limit = assignee_limit(root)
    if me not in work.assignees and len(work.assignees) >= limit:
        listed = ", ".join(work.assignees)
        print(
            f"  Warning: #{work.number} is assigned to {listed} and the"
            f" workspace's assignee limit is {limit}, so you will not be"
            " listed as working on it until something lands. Coordinate"
            " with them; `[issues] assignees` in workshop.toml raises the"
            " limit if this issue takes more people."
        )
    else:
        try:
            if me and me not in work.assignees:
                repo.issue.assign(work.number, me)
        except ForgeError as error:
            print(
                f"  Note: could not assign the issue ({error}); others"
                " will not see you working on it, so communicate."
            )

    kind = _issue_kind(work.labels, type)
    branch = branch_name(kind, work.number, work.title)
    git.fetch()

    if worktree:
        path = worktree_path(root, work.number, work.title)
        if path.is_dir() or git.local_branch_exists(branch):
            # Re-running start is its recovery: the work is already
            # open, so say where and open it again.
            print(f"  already started: {branch} at {path}")
            if agent:
                _launch_agent(
                    agent, path, work.number, work.title, work.body, branch, prompt
                )
                return
            _open_work(path, open)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        git._run("worktree", "add", str(path), "-b", branch, "origin/main")
        print(f"  worktree {path} on {branch}")
        provision = toolroom.uv.opts(cwd=path, nofail=True, recorded=False)(
            "run", footman.prog(), "sync"
        )
        if provision.code != 0:
            # A linked worktree does not inherit the venv; failing to
            # provision degrades to a note, not a refusal, because
            # the worktree itself is ready to work in.
            print(
                f"  Note: `{footman.prog()} sync` in the worktree failed; run it there"
            )
        if agent:
            _launch_agent(
                agent, path, work.number, work.title, work.body, branch, prompt
            )
            return
        _open_work(path, open)
        return

    if not git.is_clean():
        if wip:
            git.commit_all(f"chore(wip): parked by issue.start ({branch})")
            print("  parked the working tree (squash-only merging evaporates it)")
        else:
            fail(
                "the working tree has uncommitted changes.\n"
                f"  Park them and reuse this checkout:  fm issue.start --wip"
                f" --no-worktree {work.number}\n"
                "  Or work in a linked worktree:       "
                f"{footman.prog()} issue.start"
                f" {work.number}"
            )
    if git.local_branch_exists(branch):
        git.switch(branch)
        print(f"  already started: back on {branch}")
        return
    git._run("checkout", "-b", branch, "origin/main")
    print(f"  on {branch}")


def _me(repo: Repository) -> str:
    try:
        from livery.workshop._forge_lane import this_forge
        from livery.workshop._layers import workspace_root

        root = workspace_root()
        return this_forge(root).whoami() if root is not None else ""
    except Exception:
        return ""


def _launch_agent(
    name: str,
    path: Path,
    number: int,
    title: str,
    body: str,
    branch: str,
    prompt: str,
) -> None:
    """Hand the issue to the agent in its worktree; a terminal handoff.

    The briefing is minimal on purpose: the worktree carries the
    project's own instructions, and repeating them here would drift.
    Nothing after the exec runs.
    """
    briefing = (
        f"Work on issue #{number}: {title}\n\n{body}\n\nYou are on branch {branch}."
    )
    if prompt:
        briefing += f"\n\n{prompt}"
    executable = "claude" if name in ("", "claude") else name
    os.chdir(path)
    try:
        os.execvp(executable, [executable, briefing])
    except OSError:
        fail(f"agent '{executable}' is not installed on this machine")


def _open_work(path: Path, how: str) -> None:
    mode = how or ("code" if sys.stdout.isatty() else "none")
    if mode == "code":
        try:
            toolroom.code.opts(nofail=True, recorded=False)(str(path))
        except (OSError, toolroom.ToolError):
            # Opening an editor is a convenience, never the work: a
            # machine without `code` gets the path instead.
            print(f"  `code` is not on PATH; the worktree is at {path}")
    elif mode == "shell":
        from livery.workshop._shell import launch_shell

        os.chdir(path)
        launch_shell("")
    elif mode != "none":
        fail(f"unknown open mode {how!r}: code, shell, or none")


@issue.task(name="stop")
def issue_stop(
    ref: Annotated[Arg[str], ask(), suggest(_open_numbers, strict=False)] = "",
    discard: Annotated[bool, doc("destroy work that exists nowhere else")] = False,
) -> None:
    """Stop working on an issue: unassign, clean up the local state.

    The remote branch is never touched; while the issue is open it
    stays the pool's copy. Work that exists nowhere else is never
    destroyed without ``--discard``, and the refusal names what it
    found: a delta the remote is missing, a remote gone while the
    issue is open (exceptional), or an issue already closed.
    """
    root = _workspace()
    repo = _repo(root)
    git = GitOps(root)
    number = 0
    if ref:
        number, _title = parse_ref(ref)
        if not number:
            fail("issue.stop takes a number; a title names nothing to stop")
    else:
        match = _BRANCH_RE.match(git.current_branch())
        if match is None:
            fail(
                "not on an issue branch: name the issue"
                f" (`{footman.prog()} issue.stop 123`) or run it from the branch."
            )
        number = int(match.group(1))
    branch = _find_branch(git, number)
    if not branch:
        fail(f"no branch for issue #{number} exists locally or on the remote")

    work = repo.issue.get(number)
    state = work.state if work is not None else "unknown"
    remote_exists = branch in git.remote_branches("")
    only_local = (
        _only_local_work(git, root, branch) if git.local_branch_exists(branch) else ""
    )
    if only_local and not discard:
        if state == "closed":
            fail(
                f"issue #{number} is already closed, but this branch holds"
                f" {only_local} that exists nowhere else. Push it somewhere"
                " it matters, or pass --discard to destroy it."
            )
        if not remote_exists:
            fail(
                f"the remote branch for issue #{number} is gone while the"
                f" issue is still open, and this branch holds {only_local}."
                " That is not a state stop creates; someone removed the"
                " branch. Push the branch back, or pass --discard to"
                " destroy the local work."
            )
        fail(
            f"this branch holds {only_local} that the remote branch does"
            " not. Push first (`git push`), or pass --discard to destroy"
            " the delta; the remote branch stays as the pool's copy."
        )

    if work is not None and state == "open":
        try:
            repo.issue.unassign(number)
            print(f"  unassigned you from #{number}")
        except ForgeError as error:
            print(f"  Note: could not unassign ({error}); continuing")

    removed = _remove_local(root, git, branch)
    what = " and ".join(removed) if removed else "nothing local to remove"
    print(f"  stopped #{number}: {what}; the remote branch stays")


def _remove_local(root: Path, git: GitOps, branch: str) -> list[str]:
    """Remove the branch's worktree and local branch; what was removed."""
    removed: list[str] = []
    tree = _worktree_for(git, root, branch)
    if tree:
        git._run("worktree", "remove", "--force", tree)
        print(f"  removed worktree {tree}")
        removed.append("the worktree")
    elif git.current_branch() == branch:
        git.switch("main")
    if git.local_branch_exists(branch):
        git.delete_local_branch(branch)
        removed.append("the local branch")
    return removed


@issue.task(name="close", interactive=True)
def issue_close(
    ref: Annotated[Arg[str], ask(), suggest(_open_numbers, strict=False)] = "",
    reason: Annotated[str, doc("why: done, wontfix, duplicate, stale, ...")] = "",
    message: Annotated[str, doc("free-text detail for the close comment")] = "",
    keep_branch: Annotated[bool, doc("retain the local and remote branch")] = False,
    discard: Annotated[bool, doc("destroy work that exists nowhere else")] = False,
) -> None:
    """Close the issue itself, and tear its submission down.

    The first act is the shared teardown's disarm, so a green CI
    cannot race the close into a merge; a PR that already merged
    means the work landed, the supplied reason no longer applies,
    and the close degrades to cleanup: "issue already resolved". The
    local and remote branch are removed by default with the removed
    head sha recorded in the close comment; ``--keep-branch``
    retains them.
    """
    if not ref:
        fail(
            "name the issue to close:"
            f" `{footman.prog()} issue.close 123 --reason=wontfix`"
        )
    if not reason:
        fail(
            "closing needs a --reason (done, wontfix, duplicate, stale,"
            " ...): the pool reads why the work ended."
        )
    root = _workspace()
    repo = _repo(root)
    git = GitOps(root)
    number, _title = parse_ref(ref)
    if not number:
        fail("issue.close takes a number; a title names nothing to close")
    work = repo.issue.get(number)
    if work is None:
        fail(f"issue #{number} does not exist in this repository")

    git.fetch()
    branch = _find_branch(git, number)
    tip = git.any_head(branch) if branch else ""

    # The destruction rule runs before anything is torn down: the
    # teardown deletes branches, and a refusal that checks afterwards
    # would be checking a branch that is already gone.
    only_local = (
        _only_local_work(git, root, branch)
        if branch and git.local_branch_exists(branch)
        else ""
    )

    merged = False
    live = None
    if branch:
        try:
            live = repo.pr.find_by_head(branch, state="all")
        except ForgeError:
            live = None
        if live is not None and live.merged:
            merged = True

    if merged:
        if reason:
            print("  Note: the PR merged, so the supplied reason does not apply")
        if git.local_branch_exists(branch):
            if only_local and not discard:
                print(
                    f"  kept {branch}: it holds {only_local}; pass --discard"
                    " to remove it"
                )
            else:
                _remove_local(root, git, branch)
        if work.state == "open":
            repo.issue.comment(number, "closed: resolved by the merged PR")
            repo.issue.close(number)
        print("  issue already resolved, nothing left to do")
        return

    if only_local and not discard and not keep_branch:
        fail(
            f"the branch for #{number} holds {only_local} that exists"
            " nowhere else, and close removes branches by default. Pass"
            " --keep-branch to retain them, or --discard to destroy the"
            " work with the issue."
        )

    if live is not None and live.state == "open":
        from livery.workshop._submit import teardown_branch

        print(f"  ending PR #{live.number} through the shared teardown")
        teardown_branch(repo, git, branch, "main", keep_branches=keep_branch)

    removed_sha = ""
    if branch and not keep_branch:
        # The tip was read before the teardown deleted the branch:
        # the close comment records where the work got to.
        removed_sha = tip
        _remove_local(root, git, branch)
        if branch in git.remote_branches(""):
            git._run("push", "origin", "--delete", branch)
            print(f"  removed the remote branch {branch}")

    note = f"closed: {reason}"
    if message:
        note += f"\n\n{message}"
    if removed_sha:
        note += f"\n\nremoved branch {branch} at {removed_sha}"
    repo.issue.comment(number, note)
    repo.issue.close(number)
    print(f"  closed #{number}: {reason}")
