"""Local git operations for one repository, as functions of its root.

Every function takes the repository's working directory first and
returns strings or raises [livery.git.GitError][]. Nothing is held
between calls: a fact is read when it is asked for, and a function
that moves the repository, a fetch, a push, a commit, a switch, says
so in its name.

A reader of refs, objects, history or config opens the repository's
own files in-process and starts no git; a mover, and a reader of the
index, the working tree or the remote, runs git through the tool
store's pinned handle and carries git's own words in its error.
[livery.git.call][] is the one door every such call takes and returns
the result whatever the exit code; [livery.git.run][] raises on a
non-zero exit.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import livery.toolroom.tools as tools
from livery.git import _objects

if TYPE_CHECKING:
    from dulwich.objects import Commit
    from dulwich.repo import Repo


_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


class GitError(RuntimeError):
    """A git command failed; the message carries git's words verbatim."""


@contextmanager
def _opened(root: Path) -> Generator[Repo]:
    """The repository at *root* for the block; a `GitError` names what is not there.

    No repository at *root*, and an object a reader in the block asks
    for that the repository lacks, both raise `GitError`, as the git
    process they replace exited non-zero.
    """
    try:
        with _objects.opened(root) as repo:
            yield repo
    except _objects.NotGitRepository as error:
        raise GitError(f"{root} is not a git repository") from error
    except KeyError as error:
        missing = error.args[0] if error.args else error
        name = missing.decode() if isinstance(missing, bytes) else str(missing)
        raise GitError(f"{name} is not in the repository at {root}") from error


def _resolved(repo: Repo, rev: str, root: Path) -> bytes:
    """*rev* as a commit sha: in-process in the common grammar, else through git."""
    try:
        return _objects.resolve(repo, rev)
    except _objects.Unresolved:
        return _parsed(root, f"{rev}^{{commit}}")


def _named(repo: Repo, rev: str, root: Path) -> bytes:
    """The object *rev* names, a tree or tag included: in-process, else through git."""
    try:
        return _objects.name_of(repo, rev)
    except _objects.Unresolved:
        return _parsed(root, rev)


def _parsed(root: Path, rev: str) -> bytes:
    """The sha ``rev-parse --verify`` gives *rev*; a `GitError` for any other answer."""
    sha = run(root, "rev-parse", "--verify", rev).strip()
    if not _FULL_SHA.match(sha):
        raise GitError(f"git rev-parse --verify {rev} named no object: {sha!r}")
    return sha.encode()


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
    with _opened(root) as repo:
        common = os.path.normpath(os.path.abspath(repo.commondir()))
    return Path(common).parent


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
    with _opened(root) as repo:
        return _branch_of(repo)


def _branch_of(repo: Repo) -> str:
    target = _objects.head_target(repo)
    if target.startswith(b"ref: refs/heads/"):
        return target.removeprefix(b"ref: refs/heads/").decode()
    return ""


def position(root: Path) -> tuple[str, str]:
    """The checked-out branch name, empty when detached, and HEAD's commit.

    Both read in-process from the checkout's own files, so a flow that
    asks where it stands pays no process for either.

    Raises:
        GitError: When HEAD names no commit yet.
    """
    with _opened(root) as repo:
        return _branch_of(repo), _head_of(repo)


def _head_of(repo: Repo) -> str:
    try:
        return repo.head().decode()
    except KeyError as error:
        raise GitError("HEAD names no commit") from error


def object_id(root: Path, spec: str) -> str:
    """The object id *spec* names (``HEAD:packages/forge`` is a tree id).

    Raises:
        GitError: When *spec* names nothing.
    """
    with _opened(root) as repo:
        try:
            found = _objects.object_named(repo, spec)
        except (_objects.Unresolved, KeyError):
            found = None
    if found is None:
        raise GitError(f"{spec} names nothing in {root}")
    return found.decode()


def object_ids(root: Path, ref: str, paths: Sequence[str]) -> dict[str, str]:
    """The object ids *ref* holds at *paths*, read in-process.

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
    with _opened(root) as repo:
        tree = _objects.tree_at(repo, _named(repo, ref, root))
        found: dict[str, str] = {}
        for path in paths:
            item = _objects.entry(repo, tree, path)
            if item is not None:
                found[path] = item[1].decode()
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
    with _opened(root) as repo:
        return _objects.changed_paths_between(repo, old.encode(), new.encode())


def first_parent_trees(root: Path, count: int) -> list[str]:
    """HEAD's tree id and its first-parent ancestors', newest first.

    *count* commits deep at most.
    """
    trees: list[str] = []
    with _opened(root) as repo:
        sha: bytes | None = _resolved(repo, "HEAD", root)
        while sha is not None and len(trees) < count:
            commit = _objects.commit_of(repo, sha)
            trees.append(commit.tree.decode())
            sha = commit.parents[0] if commit.parents else None
    return trees


def head_sha(root: Path) -> str:
    """The commit HEAD points at.

    Raises:
        GitError: When HEAD names no commit yet.
    """
    with _opened(root) as repo:
        return _head_of(repo)


def has_commits(root: Path) -> bool:
    """Whether HEAD names a commit; false with no commit or no repository."""
    try:
        with _objects.opened(root) as repo:
            repo.head()
    except (_objects.NotGitRepository, KeyError):
        return False
    return True


def head_subject(root: Path) -> str:
    """HEAD's commit subject."""
    return head_message(root)[0]


def head_body(root: Path) -> str:
    """HEAD's commit body, without the subject."""
    return head_message(root)[1]


def head_message(root: Path) -> tuple[str, str]:
    """HEAD's commit subject and its body, read in-process."""
    with _opened(root) as repo:
        commit = _objects.commit_of(repo, _resolved(repo, "HEAD", root))
        return _objects.subject_and_body(commit.message)


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
    with _opened(root) as repo:
        return [
            f"{commit.id.decode()[:7]} {_objects.subject_and_body(commit.message)[0]}"
            for commit in _walk_span(repo, span, root)
        ]


def _walk_span(
    repo: Repo, span: str, root: Path, paths: Sequence[str] = ()
) -> list[Commit]:
    """The commits of *span*, ``a..b`` or a single revision, newest first."""
    left, sep, right = span.partition("..")
    include = _resolved(repo, right if sep else left, root)
    exclude = [_resolved(repo, left, root)] if sep else []
    # "." and "" name the whole tree to git, and dulwich takes them as
    # literal paths that match nothing: no filter is the same answer.
    wanted = [path.encode() for path in paths if path not in (".", "")]
    return list(_objects.walk(repo, [include], exclude=exclude, paths=wanted or None))


def unpushed(root: Path) -> list[str]:
    """The commits on any local branch that origin holds on no branch, one line each."""
    with _opened(root) as repo:
        heads = [
            sha.encode() for sha in _objects.refs_under(repo, b"refs/heads/").values()
        ]
        remote = [
            sha.encode()
            for sha in _objects.refs_under(repo, b"refs/remotes/origin/").values()
        ]
        if not heads:
            return []
        return [
            f"{commit.id.decode()[:7]} {_objects.subject_and_body(commit.message)[0]}"
            for commit in _objects.walk(repo, heads, exclude=remote)
        ]


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
    with _opened(root) as repo:
        try:
            if len(spec) == 40 and all(c in "0123456789abcdef" for c in spec):
                return _objects.holds(repo, spec.encode())
            return _objects.object_named(repo, spec) is not None
        except (_objects.Unresolved, KeyError):
            return False


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
    with _opened(root) as repo:
        url = _objects.config_value(repo, f"remote.{name}.url")
    if not url:
        raise GitError(f"{root} has no remote {name}")
    return url


def set_remote_url(root: Path, name: str, url: str) -> None:
    """Point the remote *name* at *url*."""
    run(root, "remote", "set-url", name, url)


def add_remote(root: Path, name: str, url: str) -> None:
    """Add the remote *name* at *url*."""
    run(root, "remote", "add", name, url)


def remotes(root: Path) -> tuple[str, ...]:
    """The names of the repository's remotes."""
    with _opened(root) as repo:
        found = _objects.config_matching(repo, r"^remote\.[^.]+\.url$")
    return tuple(sorted(key.split(".", 1)[1].rsplit(".", 1)[0] for key in found))


def tag_dates(root: Path, prefix: str) -> list[tuple[str, str]]:
    """Every tag under *prefix* with the date it was made, ``YYYY-MM-DD``."""
    with _opened(root) as repo:
        refs = _objects.refs_under(repo, prefix.encode(), whole=True)
        return [
            (
                name.removeprefix("refs/tags/"),
                _objects.when_made(repo, sha.encode()).date().isoformat(),
            )
            for name, sha in sorted(refs.items())
        ]


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
    with _opened(root) as repo:
        try:
            first = _resolved(repo, ancestor, root)
            second = _resolved(repo, descendant, root)
        except GitError:
            return False
        return _objects.is_ancestor(repo, first, second)


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
    with _opened(root) as repo:
        return [
            _objects.subject_and_body(commit.message)[0]
            for commit in _walk_span(repo, f"origin/{base}..HEAD", root)
        ]


def behind_base(root: Path, base: str) -> int:
    """How many commits ``origin/<base>`` is ahead of HEAD."""
    with _opened(root) as repo:
        return len(_walk_span(repo, f"HEAD..origin/{base}", root))


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
    changed. A pattern is a path or a glob as git reads one.
    """
    import fnmatch

    with _opened(root) as repo:
        changed = _objects.changed_paths_between(
            repo, _resolved(repo, old, root), _resolved(repo, new, root)
        )
    return [
        path
        for path in changed
        if any(
            fnmatch.fnmatch(path, pattern)
            or path.startswith(pattern.rstrip("/") + "/")
            or path == pattern
            for pattern in patterns
        )
    ]


def is_clean(root: Path) -> bool:
    """Whether the working tree has no changes, staged or not."""
    return not run(root, "status", "--porcelain").strip()


def dirty_paths(root: Path) -> list[str]:
    """The paths the working tree changed, staged or not, in status order."""
    lines = run(root, "status", "--porcelain").splitlines()
    return [line[3:].strip() for line in lines if line.strip()]


def commits_since(root: Path, ref: str) -> int:
    """How many commits HEAD carries beyond *ref*."""
    with _opened(root) as repo:
        return len(_walk_span(repo, f"{ref}..HEAD", root))


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
    """Whether *branch* exists locally."""
    with _opened(root) as repo:
        return _objects.ref_value(repo, b"refs/heads/" + branch.encode()) is not None


def remote_head(root: Path, branch: str) -> str:
    """``origin/<branch>``'s commit, or empty when it does not exist.

    Reads the local remote-tracking ref, so call [livery.git.fetch][]
    first for a fresh answer.
    """
    with _opened(root) as repo:
        sha = _objects.ref_value(repo, b"refs/remotes/origin/" + branch.encode())
    return sha.decode() if sha is not None else ""


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
    """The repository's git config value for *key*; empty when unset or no repository.

    Read in-process from the config files git itself reads.
    """
    try:
        with _objects.opened(root) as repo:
            return _objects.config_value(repo, key)
    except _objects.NotGitRepository:
        return ""


def config_set(root: Path, key: str, value: str) -> None:
    """Set *key* to *value* in the repository's git config."""
    run(root, "config", key, value)


def config_unset(root: Path, key: str) -> None:
    """Remove *key* from the repository's git config; a key already absent is fine."""
    call(root, "config", "--unset", key)


def config_regexp(root: Path, pattern: str) -> dict[str, str]:
    """Every config key matching *pattern*, with its value, read in-process.

    Empty when nothing matches or *root* is no repository; a key set
    twice keeps its last value.
    """
    try:
        with _objects.opened(root) as repo:
            return _objects.config_matching(repo, pattern)
    except _objects.NotGitRepository:
        return {}


def sha_of(root: Path, ref: str) -> str:
    """The commit *ref* resolves to; empty when this checkout cannot.

    For a caller taking a commit-ish from a person: a sha, a branch,
    a tag, ``HEAD``, ``HEAD~3``. The common grammar resolves in-process;
    anything else is asked of git, and a name that resolves to nothing
    is an empty answer the caller reports.
    """
    with _opened(root) as repo:
        try:
            return _objects.resolve(repo, ref).decode()
        except _objects.Unresolved:
            pass
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
    with _opened(root) as repo:
        found = _objects.merge_base(
            repo, _resolved(repo, "HEAD", root), _resolved(repo, f"origin/{base}", root)
        )
    if found is None:
        raise GitError(f"HEAD and origin/{base} share no commit")
    return found.decode()


def merge_parents(root: Path) -> tuple[str, str] | None:
    """HEAD's two parents when HEAD is a merge: the base, then the head merged in.

    A pull request's run on GitHub checks out the merge the forge
    made of the pull request's head onto its base branch; any other
    checkout answers None.
    """
    with _opened(root) as repo:
        parents = _objects.commit_of(repo, _resolved(repo, "HEAD", root)).parents
    if len(parents) != 2:
        return None
    return parents[0].decode(), parents[1].decode()


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
    with _opened(root) as repo:
        committed = _objects.changed_paths_between(
            repo, since.encode(), _resolved(repo, "HEAD", root)
        )
    pending: list[str] = []
    for line in run(
        root, "status", "--porcelain", "--untracked-files=all"
    ).splitlines():
        pending.extend(part for part in line[3:].split(" -> ") if part)
    return sorted({path for path in committed + pending if path})


def tags(root: Path) -> tuple[str, ...]:
    """Every tag name the repository knows."""
    with _opened(root) as repo:
        return tuple(
            sorted(
                name.removeprefix("refs/tags/")
                for name in _objects.refs_under(repo, b"refs/tags/")
            )
        )


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
    with _opened(root) as repo:
        return tuple(
            (commit.id.decode(), _objects.subject_and_body(commit.message)[0])
            for commit in _objects.walk(repo, [_resolved(repo, ref, root)], limit=count)
        )


def local_branches(root: Path, prefix: str) -> tuple[str, ...]:
    """Local branch names under *prefix* (``workflow/``), short form."""
    with _opened(root) as repo:
        found = _objects.refs_under(repo, b"refs/heads/" + prefix.encode(), whole=True)
    return tuple(sorted(name.removeprefix("refs/heads/") for name in found))


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
    with _opened(root) as repo:
        found = _objects.refs_under(
            repo, b"refs/remotes/origin/" + prefix.encode(), whole=True
        )
    names = (name.removeprefix("refs/remotes/origin/") for name in sorted(found))
    return tuple(name for name in names if name and name != "HEAD")


def any_head(root: Path, branch: str) -> str:
    """*branch*'s head sha: local when present, else the remote's, else empty.

    A *root* that is no repository has no local branch, so the remote
    is asked.
    """
    sha: bytes | None = None
    try:
        with _objects.opened(root) as repo:
            sha = _objects.ref_value(repo, b"refs/heads/" + branch.encode())
    except _objects.NotGitRepository:
        pass
    if sha is not None:
        return sha.decode()
    result = call(root, "ls-remote", "origin", f"refs/heads/{branch}")
    if result.code == 0 and result.stdout.strip():
        return result.stdout.split()[0]
    return ""


def log_paths(root: Path, span: str, paths: tuple[str, ...]) -> tuple[str, ...]:
    """The subjects in *span* touching *paths* (``a..b`` git range)."""
    with _opened(root) as repo:
        return tuple(
            _objects.subject_and_body(commit.message)[0]
            for commit in _walk_span(repo, span, root, paths)
        )


def last_commit_touching(root: Path, path: str, *, ref: str = "HEAD") -> str:
    """The newest commit at or before *ref* that changed *path*; empty if none."""
    with _opened(root) as repo:
        for commit in _objects.walk(
            repo, [_resolved(repo, ref, root)], paths=[path.encode()], limit=1
        ):
            return commit.id.decode()
    return ""


def files_in_commit(root: Path, ref: str) -> list[str]:
    """The paths the commit at *ref* changed, against its first parent."""
    with _opened(root) as repo:
        sha = _resolved(repo, ref, root)
        commit = _objects.commit_of(repo, sha)
        parent = commit.parents[0] if commit.parents else None
        return _objects.changed_paths_between(repo, parent, sha)


def file_at(root: Path, ref: str, path: str) -> str:
    """The contents of *path* as committed at *ref*; empty when absent."""
    with _opened(root) as repo:
        try:
            found = _objects.object_named(repo, f"{ref}:{path}")
        except (_objects.Unresolved, KeyError):
            return ""
        if found is None:
            return ""
        return _objects.blob_text(repo, found)


def commit_message(root: Path, ref: str) -> str:
    """*ref*'s full commit message, subject and body."""
    with _opened(root) as repo:
        commit = _objects.commit_of(repo, _resolved(repo, ref, root))
    return _objects.message_of(commit)
