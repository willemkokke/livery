"""Local git operations for one repository, as functions of its root.

Every function takes the repository's working directory first, runs
git through the tool store's pinned handle in it, and returns strings
or raises [livery.git.GitError][] with git's own words. Nothing is
held between calls: a fact is read when it is asked for, and a
function that moves the repository, a fetch, a push, a commit, a
switch, says so in its name.

[livery.git.call][] is the one door every call takes and returns the
result whatever the exit code; [livery.git.run][] raises on a non-zero
exit. The named functions are the readers and movers a flow asks
for, each one process, several of them one process where a caller
asks two facts at once.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path

import livery.toolroom.tools as tools


class GitError(RuntimeError):
    """A git command failed; the message carries git's words verbatim."""


def call(root: Path, *args: str) -> tools.Result:
    """Run git with *args* in *root* and return the result whatever the exit code.

    The one door every git call takes. A caller that treats a non-zero
    exit as an answer reads the code; one that treats it as a failure
    calls [livery.git.run][] instead.
    """
    return tools.git.opts(cwd=root, nofail=True, recorded=False)(*args)


def run(root: Path, *args: str) -> str:
    """Run git with *args* in *root* and return its standard output.

    Raises:
        GitError: On a non-zero exit, with git's words.
    """
    result = call(root, *args)
    if result.code != 0:
        raise GitError(
            f"git {' '.join(args)} exited {result.code}:"
            f"\n{result.stdout}{result.stderr}"
        )
    return result.stdout


def main_checkout(root: Path) -> Path:
    """The repository's main checkout, the same from any of its worktrees.

    Every worktree shares the main checkout's ``.git`` directory,
    which git reports as the common directory; its parent is the
    main checkout.
    """
    common = run(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(common.strip()).parent


def unmerged_paths(root: Path) -> list[str]:
    """The paths a stopped merge or rebase left conflicted, as git names them."""
    out = run(root, "diff", "--name-only", "--diff-filter=U")
    return [line for line in out.splitlines() if line]


def staged_text(root: Path, path: str, stage: int) -> str:
    """*path*'s text at index *stage* of a conflict; empty when that side has none.

    Stage 1 is the common ancestor, 2 the side being merged into
    (the base, in a rebase), 3 the side merged in (the commit
    replayed, in a rebase).
    """
    result = call(root, "show", f":{stage}:{path}")
    return result.stdout if result.code == 0 else ""


def add(root: Path, path: str) -> None:
    """Stage *path* as it stands in the working tree, a removal included."""
    run(root, "add", "--all", "--", path)


def current_branch(root: Path) -> str:
    """The checked-out branch name; empty when detached."""
    out = run(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    return "" if out == "HEAD" else out


def position(root: Path) -> tuple[str, str]:
    """The checked-out branch name, empty when detached, and HEAD's commit.

    One process for both: a flow that asks where it stands asks
    for the branch and the head together, and every process costs
    tens of milliseconds on a Windows runner. The head is asked
    first: ``--abbrev-ref`` applies to every revision after it, so
    the branch's name comes second.
    """
    out = run(root, "rev-parse", "HEAD", "--abbrev-ref", "HEAD").split()
    head = out[0] if out else ""
    branch = "" if len(out) < 2 or out[1] == "HEAD" else out[1]
    return branch, head


def object_id(root: Path, spec: str) -> str:
    """The object id *spec* names (``HEAD:packages/forge`` is a tree id).

    Raises:
        GitError: When *spec* names nothing.
    """
    return run(root, "rev-parse", "--verify", "--quiet", spec).strip()


def object_ids(root: Path, ref: str, paths: Sequence[str]) -> dict[str, str]:
    """The object ids *ref* holds at *paths*, read in one process.

    One ``ls-tree`` answers whatever the caller asks for, where a
    read per path is a git process per path: the process is the
    cost, several times over on Windows.

    Args:
        root: The repository's working directory.
        ref: The commit or tree to read, ``HEAD`` for the tip.
        paths: Repository-relative paths, files or directories.

    Returns:
        Each path the tree holds, mapped to its object id: a tree
        id for a directory, a blob id for a file. A path the tree
        lacks is absent, so a caller that tolerates one reads the
        mapping and a caller that does not raises on its own
        behalf.
    """
    if not paths:
        return {}
    # -z, so a name is never C-quoted and a path with a space or a
    # backslash reads back as it was written.
    out = run(root, "ls-tree", "-z", ref, "--", *paths)
    found: dict[str, str] = {}
    for record in out.split("\0"):
        entry, _, name = record.partition("\t")
        fields = entry.split()
        if name and len(fields) == 3:
            found[name] = fields[2]
    return found


def working_tree_id(root: Path) -> str:
    """The tree id of the working tree as it stands.

    Tracked changes and untracked files count and ignored files do
    not, the view ``status`` gives, written through a scratch copy
    of the index so the real index is never touched. A clean
    checkout answers HEAD's tree, so a commit that takes everything
    has the id a check on the dirty tree recorded.
    """
    index = run(root, "rev-parse", "--git-path", "index").strip()
    real = Path(index) if Path(index).is_absolute() else root / index
    with tempfile.TemporaryDirectory() as scratch:
        copy = Path(scratch) / "index"
        if real.is_file():
            # copy2, so the copy keeps the index's own timestamps.
            # git trusts a cached stat only when the entry is older
            # than the index file; an entry as new as the index is
            # racily clean and its content is read again. A copy
            # stamped with the time of the copy looks newer than
            # every entry, which switches that rule off, and a
            # rewrite of the same size in the same second as the
            # cached stat then reads as no change at all (git
            # compares whole seconds where it is built without
            # nanosecond stat, as it is on macOS).
            shutil.copy2(real, copy)
        env = {**os.environ, "GIT_INDEX_FILE": str(copy)}
        git = tools.git.opts(cwd=root, env=env, nofail=True, recorded=False)
        for args in (("add", "-A"), ("write-tree",)):
            result = git(*args)
            if result.code != 0:
                raise GitError(
                    f"git {' '.join(args)} exited {result.code}:"
                    f"\n{result.stdout}{result.stderr}"
                )
        return result.stdout.strip()


def tree_diff(root: Path, old: str, new: str) -> list[str]:
    """The paths differing between the trees *old* and *new*.

    A rename is both sides.
    """
    out = run(root, "diff-tree", "-r", "--name-only", "--no-renames", old, new)
    return sorted({line.strip() for line in out.splitlines() if line.strip()})


def first_parent_trees(root: Path, count: int) -> list[str]:
    """HEAD's tree id and its first-parent ancestors', newest first.

    *count* commits deep at most.
    """
    out = run(root, "log", "--first-parent", f"-{count}", "--format=%T", "HEAD")
    return [line.strip() for line in out.splitlines() if line.strip()]


def head_sha(root: Path) -> str:
    """The commit HEAD points at."""
    return run(root, "rev-parse", "HEAD").strip()


def has_commits(root: Path) -> bool:
    """Whether HEAD names a commit; false with no commit or no repository."""
    result = call(root, "rev-parse", "--verify", "-q", "HEAD")
    return result.code == 0


def head_subject(root: Path) -> str:
    """HEAD's commit subject."""
    return run(root, "log", "-1", "--format=%s").strip()


def head_body(root: Path) -> str:
    """HEAD's commit body, without the subject."""
    return run(root, "log", "-1", "--format=%b").strip()


def head_message(root: Path) -> tuple[str, str]:
    """HEAD's commit subject and its body, in one process."""
    out = run(root, "log", "-1", "--format=%s%x00%b")
    subject, _, body = out.partition("\0")
    return subject.strip(), body.strip()


def fetch(root: Path, *refs: str, prune: bool = False, force: bool = False) -> None:
    """Freshen ``origin/*``, or the *refs* named, from origin.

    *prune* drops the remote-tracking refs origin no longer has, so a
    branch the forge deleted does not linger; *force* takes a ref that
    moved to an unrelated commit, a receipt tag recut by a later wave.
    """
    flags = [flag for flag, on in (("--prune", prune), ("--force", force)) if on]
    run(root, "fetch", "--quiet", *flags, "origin", *refs)


def fetch_tag(root: Path, tag: str) -> None:
    """Take *tag* from origin as it is there, replacing the one held here."""
    run(root, "fetch", "--quiet", "--force", "origin", "tag", tag)


def switch_new(root: Path, name: str, start: str = "") -> None:
    """Create or reset the branch *name* at *start* (HEAD when empty); switch to it."""
    run(root, "switch", "-C", name, *([start] if start else []))


def reset_hard(root: Path, ref: str) -> None:
    """Move the branch, the index and the working tree to *ref*; every change goes."""
    run(root, "reset", "--hard", ref)


def clean(root: Path, *, keep: Sequence[str] = (), dry_run: bool = False) -> list[str]:
    """Remove every untracked and ignored file but the patterns in *keep*; the paths.

    Under *dry_run* nothing goes and the paths are what would.
    """
    kept = [flag for name in keep for flag in ("-e", name)]
    out = run(root, "clean", "-ndx" if dry_run else "-fdx", *kept)
    return [
        line.removeprefix("Would remove ").removeprefix("Removing ").strip()
        for line in out.splitlines()
        if line.strip()
    ]


def oneline(root: Path, span: str) -> list[str]:
    """The commits in *span* (``a..b``), one line each, newest first."""
    out = run(root, "log", "--oneline", span)
    return [line for line in out.splitlines() if line.strip()]


def unpushed(root: Path) -> list[str]:
    """The commits on any local branch that origin holds on no branch, one line each."""
    out = run(root, "log", "--oneline", "--branches", "--not", "--remotes=origin")
    return [line for line in out.splitlines() if line.strip()]


def ls_remote(
    root: Path, *refs: str, tags: bool = False, remote: str = "origin"
) -> dict[str, str]:
    """The refs *remote* holds among *refs* (every one when none is named), by sha.

    *tags* lists the tags alone, the peeled entries left out; *remote*
    is a name or a URL, and asking a URL needs no remote configured.
    """
    out = run(root, "ls-remote", *(["--tags"] if tags else []), remote, *refs)
    found: dict[str, str] = {}
    for line in out.splitlines():
        sha, _, name = line.partition("\t")
        if name.strip() and not name.endswith("^{}"):
            found[name.strip()] = sha.strip()
    return found


def push_refspec(root: Path, refspec: str, *, remote: str = "origin") -> None:
    """Push *refspec* to *remote* as given: a tag, or ``:refs/tags/x`` to delete one."""
    run(root, "push", "--quiet", remote, refspec)


def worktree_add(root: Path, path: Path, ref: str, *, detach: bool = False) -> None:
    """Check *ref* out as a linked worktree at *path*, detached under *detach*."""
    run(root, "worktree", "add", *(["--detach"] if detach else []), str(path), ref)


def worktree_remove(root: Path, path: Path) -> None:
    """Remove the linked worktree at *path*, its changes with it."""
    run(root, "worktree", "remove", "--force", str(path))


def restore(root: Path, *paths: str) -> str:
    """Put *paths* back as HEAD has them; empty, or git's words when it could not.

    A path HEAD lacks refuses the whole pathspec, so a caller restoring
    several files it is unsure of restores them one by one.
    """
    result = call(root, "checkout", "HEAD", "--", *paths)
    return "" if result.code == 0 else (result.stderr or result.stdout).strip()


def tracks(root: Path, spec: str) -> bool:
    """Whether the repository holds the object *spec* names: ``HEAD:uv.lock``, a sha."""
    return call(root, "cat-file", "-e", spec).code == 0


def unstage(root: Path, path: str) -> None:
    """Take *path* out of the index and leave the file; an untracked path is fine."""
    run(root, "rm", "--cached", "--quiet", "--ignore-unmatch", "--", path)


def ls_files(
    root: Path, *paths: str, others: bool = False, cached: bool = True
) -> list[str]:
    """The paths git lists under *paths* (the whole tree when none), sorted.

    *cached* lists what the index holds, *others* the untracked files
    the ignore rules do not cover; both together are what a check
    reads. Names come back as written, through ``-z``.

    Raises:
        GitError: When git cannot list, with its words.
    """
    flags = [flag for flag, on in (("--cached", cached), ("--others", others)) if on]
    if others:
        flags.append("--exclude-standard")
    out = run(root, "ls-files", "-z", *flags, "--", *paths)
    return sorted({name for name in out.split("\0") if name})


def remote_url(root: Path, name: str = "origin") -> str:
    """The URL of the remote *name*.

    Raises:
        GitError: When *root* has no such remote.
    """
    return run(root, "remote", "get-url", name).strip()


def set_remote_url(root: Path, name: str, url: str) -> None:
    """Point the remote *name* at *url*."""
    run(root, "remote", "set-url", name, url)


def add_remote(root: Path, name: str, url: str) -> None:
    """Add the remote *name* at *url*."""
    run(root, "remote", "add", name, url)


def remotes(root: Path) -> tuple[str, ...]:
    """The names of the repository's remotes."""
    return tuple(run(root, "remote").split())


def tag_dates(root: Path, prefix: str) -> list[tuple[str, str]]:
    """Every tag under *prefix* with the date it was made, ``YYYY-MM-DD``."""
    out = run(
        root, "for-each-ref", prefix, "--format=%(refname:short) %(creatordate:short)"
    )
    pairs: list[tuple[str, str]] = []
    for line in out.splitlines():
        tag, _, date = line.strip().partition(" ")
        if tag:
            pairs.append((tag, date))
    return pairs


def fetch_tags(root: Path) -> None:
    """Freshen ``origin/*`` and take every tag origin holds.

    A tag cut on the remote at a commit this clone already has is
    fetched here too, which a plain fetch's tag following does not
    promise.
    """
    run(root, "fetch", "--tags", "origin")


def has_remote(root: Path, name: str = "origin") -> bool:
    """Whether this clone has a remote called *name*; any other failure raises."""
    result = call(root, "remote", "get-url", name)
    if result.code in (0, 2):
        return result.code == 0
    raise GitError(
        f"git remote get-url exited {result.code}:\n{result.stdout}{result.stderr}"
    )


def is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    """Whether *ancestor* reaches *descendant* in this clone.

    A commit this clone does not know cannot be an ancestor, so
    an unknown *ancestor* answers False rather than raising.
    """
    result = call(root, "merge-base", "--is-ancestor", ancestor, descendant)
    return result.code == 0


def push(
    root: Path, branch: str, *, remote: str = "origin", force: bool = False
) -> None:
    """Push *branch* to *remote*, setting upstream; *force* replaces what is there.

    A bare force, for a repository seconds old whose history this
    checkout authored; a branch others may have moved takes
    [livery.git.push_force][] and its lease.
    """
    run(root, "push", "--quiet", *(["--force"] if force else []), "-u", remote, branch)


def push_force(root: Path, branch: str) -> None:
    """Force-push *branch*, leased on the last-seen remote state.

    ``--force-with-lease``, never a bare force: the push is
    refused when origin moved past what this clone last fetched,
    so a colleague's commits pushed since then cannot be
    clobbered blind.
    """
    run(root, "push", "--force-with-lease", "-u", "origin", branch)


def subjects_ahead(root: Path, base: str) -> list[str]:
    """The subjects of the commits HEAD carries beyond ``origin/<base>``."""
    out = run(root, "log", "--format=%s", f"origin/{base}..HEAD")
    return [line for line in out.splitlines() if line]


def behind_base(root: Path, base: str) -> int:
    """How many commits ``origin/<base>`` is ahead of HEAD."""
    out = run(root, "rev-list", "--count", f"HEAD..origin/{base}").strip()
    return int(out or "0")


def conflicts_with_base(root: Path, base: str) -> bool:
    """Whether merging ``origin/<base>`` into HEAD would conflict.

    Probed with ``git merge-tree`` so nothing in the working tree
    moves; exit 1 with conflicts is the answer, any other failure
    raises.
    """
    result = call(
        root, "merge-tree", "--write-tree", "--name-only", "HEAD", f"origin/{base}"
    )
    if result.code in (0, 1):
        return result.code == 1
    raise GitError(
        f"git merge-tree exited {result.code}:\n{result.stdout}{result.stderr}"
    )


def integrate(root: Path, base: str) -> None:
    """Merge ``origin/<base>`` into the current branch, after a fetch.

    A conflict raises [livery.git.GitError][] with git's words and
    leaves the merging state in place for a person to resolve;
    re-running the caller after the resolution is the recovery.
    """
    fetch(root)
    run(root, "merge", "--no-edit", f"origin/{base}")


def changed_between(
    root: Path, old: str, new: str, patterns: tuple[str, ...]
) -> list[str]:
    """The paths matching *patterns* that differ between *old* and *new*.

    Repo-relative, as git prints them; empty when nothing matching
    changed.
    """
    out = run(root, "diff", "--name-only", old, new, "--", *patterns)
    return [line.strip() for line in out.splitlines() if line.strip()]


def is_clean(root: Path) -> bool:
    """Whether the working tree has no changes, staged or not."""
    return not run(root, "status", "--porcelain").strip()


def dirty_paths(root: Path) -> list[str]:
    """The paths the working tree changed, staged or not, in status order."""
    lines = run(root, "status", "--porcelain").splitlines()
    return [line[3:].strip() for line in lines if line.strip()]


def commits_since(root: Path, ref: str) -> int:
    """How many commits HEAD carries beyond *ref*."""
    out = run(root, "rev-list", "--count", f"{ref}..HEAD").strip()
    return int(out or "0")


def detach(root: Path, ref: str) -> None:
    """Check *ref* out as a detached HEAD."""
    run(root, "switch", "--detach", ref)


def delete_branch(root: Path, name: str) -> None:
    """Delete the local branch *name*, merged or not."""
    run(root, "branch", "-D", name)


def create_branch(root: Path, name: str) -> None:
    """Create and switch to *name*."""
    run(root, "checkout", "-b", name)


def switch(root: Path, name: str) -> None:
    """Switch to the existing branch *name*."""
    run(root, "switch", name)


def commit_all(root: Path, message: str) -> None:
    """Stage everything and commit with *message*."""
    run(root, "add", "-A")
    run(root, "commit", "-m", message)


def amend_all(root: Path) -> None:
    """Stage everything and fold it into HEAD, message kept."""
    run(root, "add", "-A")
    run(root, "commit", "--amend", "--no-edit")


def local_branch_exists(root: Path, branch: str) -> bool:
    """Whether *branch* exists locally; any other failure raises."""
    result = call(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    if result.code in (0, 1):
        return result.code == 0
    raise GitError(
        f"git rev-parse exited {result.code}:\n{result.stdout}{result.stderr}"
    )


def remote_head(root: Path, branch: str) -> str:
    """``origin/<branch>``'s commit, or empty when it does not exist.

    Reads the local remote-tracking ref, so call [livery.git.fetch][]
    first for a fresh answer.
    """
    result = call(root, "rev-parse", f"origin/{branch}")
    return result.stdout.strip() if result.code == 0 else ""


def author_email(root: Path) -> str:
    """The address the next commit in *root* would carry as its author.

    Resolved as git resolves it, the environment's `GIT_AUTHOR_EMAIL`
    ahead of the config, so a checkout whose identity rides the
    environment answers the same as one configured.
    """
    ident = run(root, "var", "GIT_AUTHOR_IDENT")
    _, _, rest = ident.partition("<")
    email, _, _ = rest.partition(">")
    return email.strip()


def config_get(root: Path, key: str) -> str:
    """The repository's git config value for *key*; empty when unset."""
    result = call(root, "config", "--get", key)
    return result.stdout.strip() if result.code == 0 else ""


def config_set(root: Path, key: str, value: str) -> None:
    """Set *key* to *value* in the repository's git config."""
    run(root, "config", key, value)


def config_unset(root: Path, key: str) -> None:
    """Remove *key* from the repository's git config; a key already absent is fine."""
    call(root, "config", "--unset", key)


def config_regexp(root: Path, pattern: str) -> dict[str, str]:
    """Every config key matching *pattern*, with its value, in one process.

    Empty when nothing matches, which git reports as a non-zero
    exit; a key set twice keeps its last value.
    """
    result = call(root, "config", "--get-regexp", pattern)
    found: dict[str, str] = {}
    if result.code == 0:
        for line in result.stdout.splitlines():
            key, _, value = line.partition(" ")
            found[key] = value.strip()
    return found


def sha_of(root: Path, ref: str) -> str:
    """The commit *ref* resolves to; empty when this checkout cannot.

    For a caller taking a commit-ish from a person: a sha, a branch,
    a tag, ``HEAD``, ``HEAD~3``. Empty rather than raising, because
    a name that resolves to nothing is an answer the caller reports.
    """
    # `~0` peels to the commit as `^{commit}` does. A caret never
    # reaches git on Windows: the store runs git through a `.cmd`
    # launcher, and cmd.exe takes `^` as its escape character.
    result = call(root, "rev-parse", "--verify", f"{ref}~0")
    return result.stdout.strip() if result.code == 0 else ""


def merge_base(root: Path, base: str) -> str:
    """The merge base of HEAD and ``origin/<base>``; raises without one.

    A shallow clone, or a base the fetch did not bring, has none.

    Raises:
        GitError: When HEAD and ``origin/<base>`` share no commit.
    """
    return run(root, "merge-base", "HEAD", f"origin/{base}").strip()


def merge_parents(root: Path) -> tuple[str, str] | None:
    """HEAD's two parents when HEAD is a merge: the base, then the head merged in.

    A pull request's run on GitHub checks out the merge the forge
    made of the pull request's head onto its base branch; any other
    checkout answers None.
    """
    line = run(root, "rev-list", "--parents", "-n", "1", "HEAD").split()
    return (line[1], line[2]) if len(line) == 3 else None


def merge_tree(root: Path, base: str, head: str) -> str:
    """The tree a merge of *head* onto *base* yields, written to this clone.

    No working tree or index is touched; only the objects are
    written.

    Raises:
        GitError: On a conflict, with git's words.
    """
    return run(root, "merge-tree", "--write-tree", base, head).splitlines()[0].strip()


def changed_paths(root: Path, base: str) -> list[str]:
    """Repo-relative paths this branch touches, committed or not.

    The committed half diffs against the merge base with
    ``origin/<base>``; the uncommitted half comes from the status
    listing, renames counted on both sides and untracked files
    named one by one (a collapsed directory reads as neither
    prose nor package).
    """
    since = merge_base(root, base)
    committed = run(root, "diff", "--name-only", since, "HEAD").splitlines()
    pending: list[str] = []
    for line in run(
        root, "status", "--porcelain", "--untracked-files=all"
    ).splitlines():
        pending.extend(part for part in line[3:].split(" -> ") if part)
    return sorted({path for path in committed + pending if path})


def tags(root: Path) -> tuple[str, ...]:
    """Every tag name the repository knows."""
    return tuple(run(root, "tag", "-l").split())


def remote_tags(root: Path) -> tuple[str, ...]:
    """Tag names on origin, asked of the remote.

    ``ls-remote``, never the local list: a tag cut elsewhere (a
    CI wave's receipt) is only guaranteed visible at the remote.
    """
    out = run(root, "ls-remote", "--tags", "origin")
    names: list[str] = []
    for line in out.splitlines():
        _, _, ref = line.partition("refs/tags/")
        ref = ref.strip().removesuffix("^{}")
        if ref:
            names.append(ref)
    return tuple(dict.fromkeys(names))


def recent_commits(
    root: Path, count: int, ref: str = "HEAD"
) -> tuple[tuple[str, str], ...]:
    """(sha, subject) for the newest *count* commits reachable from *ref*."""
    out = run(root, "log", f"-{count}", "--format=%H%x09%s", ref)
    pairs: list[tuple[str, str]] = []
    for line in out.splitlines():
        sha, _, subject = line.partition("\t")
        if sha.strip():
            pairs.append((sha.strip(), subject.strip()))
    return tuple(pairs)


def local_branches(root: Path, prefix: str) -> tuple[str, ...]:
    """Local branch names under *prefix* (``workflow/``), short form."""
    out = run(root, "for-each-ref", "--format=%(refname:short)", f"refs/heads/{prefix}")
    return tuple(line.strip() for line in out.splitlines() if line.strip())


def remote_branches(root: Path, prefix: str) -> tuple[str, ...]:
    """Branch names under *prefix* on origin, asked of the remote.

    ``ls-remote``, never the remote-tracking refs: a plain fetch
    does not prune, so a stale tracking ref outlives the forge's
    auto-delete and would resurrect a finished workflow.
    """
    out = run(root, "ls-remote", "--heads", "origin", f"refs/heads/{prefix}*")
    names: list[str] = []
    for line in out.splitlines():
        _, _, ref = line.partition("refs/heads/")
        if ref.strip():
            names.append(ref.strip())
    return tuple(names)


def tracking_branches(root: Path, prefix: str) -> tuple[str, ...]:
    """Branch names under *prefix* among origin's remote-tracking refs.

    Read locally, so no network and no wait, and as fresh as the
    last fetch: a branch pushed since is missing, and one the forge
    deleted lingers until a fetch prunes it. A caller that only
    names a branch takes that; one that deletes or protects asks
    origin through [livery.git.remote_branches][].
    """
    out = run(
        root, "for-each-ref", "--format=%(refname)", f"refs/remotes/origin/{prefix}"
    )
    names = (
        line.strip().removeprefix("refs/remotes/origin/") for line in out.splitlines()
    )
    return tuple(name for name in names if name and name != "HEAD")


def any_head(root: Path, branch: str) -> str:
    """*branch*'s head sha: local when present, else the remote's, else empty."""
    result = call(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    if result.code == 0 and result.stdout.strip():
        return result.stdout.strip()
    result = call(root, "ls-remote", "origin", f"refs/heads/{branch}")
    if result.code == 0 and result.stdout.strip():
        return result.stdout.split()[0]
    return ""


def log_paths(root: Path, span: str, paths: tuple[str, ...]) -> tuple[str, ...]:
    """The subjects in *span* touching *paths* (``a..b`` git range)."""
    out = run(root, "log", "--format=%s", span, "--", *paths)
    return tuple(line for line in out.splitlines() if line.strip())


def last_commit_touching(root: Path, path: str, *, ref: str = "HEAD") -> str:
    """The newest commit at or before *ref* that changed *path*; empty if none."""
    return run(root, "log", "-1", "--format=%H", ref, "--", path).strip()


def files_in_commit(root: Path, ref: str) -> list[str]:
    """The paths the commit at *ref* changed."""
    listed = run(root, "show", "--pretty=", "--name-only", ref)
    return [line for line in listed.splitlines() if line]


def file_at(root: Path, ref: str, path: str) -> str:
    """The contents of *path* as committed at *ref*; empty when absent."""
    result = call(root, "show", f"{ref}:{path}")
    return result.stdout if result.code == 0 else ""


def commit_message(root: Path, ref: str) -> str:
    """*ref*'s full commit message, subject and body."""
    return run(root, "log", "-1", "--format=%B", ref)
