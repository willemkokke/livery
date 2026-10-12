"""Refs, objects and config read in-process, and a root commit written in-process.

Every reader here opens the repository with dulwich, reads the files
git itself would read, and closes it again: no process starts, and
nothing is held between calls. A revision a reader cannot resolve on
its own, a reflog spelling or a date, is handed to git by the caller;
what this module resolves is the common grammar: a sha, ``HEAD``, a
branch, a tag, ``origin/<branch>``, a full ref, ``<rev>~<n>``,
``<rev>^{commit}``, ``<rev>^{tree}`` and ``<rev>:<path>``.
"""

from __future__ import annotations

import re
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dulwich import graph
from dulwich.diff_tree import tree_changes
from dulwich.errors import NotGitRepository
from dulwich.objects import Blob, Commit, ObjectID, Tag, Tree
from dulwich.refs import Ref
from dulwich.repo import Repo

_SHA = re.compile(r"^[0-9a-f]{4,40}$")


class Unresolved(LookupError):
    """A revision the in-process resolver cannot name; git may still."""


@contextmanager
def opened(root: Path) -> Generator[Repo]:
    """The repository at *root*, a worktree included, closed after the block.

    Raises:
        NotGitRepository: When *root* is not inside a repository.
    """
    repo = Repo(str(root))
    try:
        yield repo
    finally:
        repo.close()


def holds(repo: Repo, sha: bytes) -> bool:
    """Whether the object store has the object *sha* names."""
    return ObjectID(sha) in repo.object_store


def ref_value(repo: Repo, name: bytes) -> bytes | None:
    """The sha the ref *name* points at, followed to its end; ``None`` when absent."""
    try:
        return repo.refs[Ref(name)]
    except KeyError:
        return None


def head_target(repo: Repo) -> bytes:
    """What ``HEAD`` holds: ``ref: refs/heads/<branch>`` or a sha; empty without."""
    return repo.refs.read_ref(Ref(b"HEAD")) or b""


def point(repo: Repo, ref: bytes, sha: bytes) -> None:
    """Point *ref* at *sha*, whatever it held before."""
    repo.refs[Ref(ref)] = ObjectID(sha)


def commit_of(repo: Repo, sha: bytes) -> Commit:
    """The commit *sha* names, a tag peeled; raises `Unresolved` for anything else."""
    obj = repo[ObjectID(sha)]
    while isinstance(obj, Tag):
        obj = repo[obj.object[1]]
    if not isinstance(obj, Commit):
        raise Unresolved(sha.decode())
    return obj


def name_of(repo: Repo, rev: str) -> bytes:
    """The object id *rev* names in the common grammar, a tag left unpeeled.

    Raises:
        Unresolved: When *rev* is outside the grammar or names nothing.
    """
    rev = rev.strip()
    if rev.endswith("^{commit}"):
        return resolve(repo, rev[: -len("^{commit}")])
    if "~" in rev:
        head, _, count = rev.rpartition("~")
        steps = int(count) if count.isdigit() else 1 if count == "" else -1
        if steps < 0 or not head:
            raise Unresolved(rev)
        sha = resolve(repo, head)
        for _ in range(steps):
            parents = commit_of(repo, sha).parents
            if not parents:
                raise Unresolved(rev)
            sha = parents[0]
        return sha
    if rev == "HEAD":
        try:
            return repo.head()
        except KeyError as error:
            raise Unresolved(rev) from error
    name = rev.encode()
    candidates = (
        name,
        b"refs/heads/" + name,
        b"refs/tags/" + name,
        b"refs/remotes/" + name,
    )
    for candidate in candidates:
        found = ref_value(repo, candidate)
        if found is not None:
            return found
    if _SHA.match(rev):
        if len(rev) == 40:
            if holds(repo, name):
                return name
            raise Unresolved(rev)
        matches = [sha for sha in repo.object_store if sha.startswith(name)]
        if len(matches) == 1:
            return matches[0]
    raise Unresolved(rev)


def resolve(repo: Repo, rev: str) -> bytes:
    """The commit sha *rev* names, in the common grammar, a tag peeled.

    Raises:
        Unresolved: When *rev* is outside the grammar or names no commit.
    """
    return commit_of(repo, name_of(repo, rev)).id


def tree_at(repo: Repo, sha: bytes) -> Tree:
    """The tree of the commit *sha*, or the tree *sha* itself."""
    obj = repo[ObjectID(sha)]
    if isinstance(obj, Tree):
        return obj
    commit = commit_of(repo, sha)
    tree = repo[commit.tree]
    assert isinstance(tree, Tree)
    return tree


def entry(repo: Repo, tree: Tree, path: str) -> tuple[int, bytes] | None:
    """The mode and sha of *path* under *tree*, or ``None`` when the tree lacks it."""
    try:
        mode, sha = tree.lookup_path(repo.__getitem__, path.encode())
    except KeyError:
        return None
    return mode, sha


def object_named(repo: Repo, spec: str) -> bytes | None:
    """The object id *spec* names: ``<rev>:<path>``, ``<rev>^{tree}``, a revision.

    ``None`` when the revision resolves and the path is absent;
    `Unresolved` when the revision itself cannot be named.
    """
    if spec.endswith("^{tree}"):
        return tree_at(repo, resolve(repo, spec[: -len("^{tree}")])).id
    rev, sep, path = spec.partition(":")
    if sep:
        tree = tree_at(repo, resolve(repo, rev))
        if not path:
            return tree.id
        found = entry(repo, tree, path)
        return None if found is None else found[1]
    return name_of(repo, spec)


def message_of(commit: Commit) -> str:
    """The commit's whole message as text."""
    return bytes(commit.message).decode("utf-8", "replace")


def blob_text(repo: Repo, sha: bytes) -> str:
    """The blob *sha* as text; empty when *sha* names no blob."""
    obj = repo[ObjectID(sha)]
    if not isinstance(obj, Blob):
        return ""
    return obj.data.decode("utf-8", "replace")


def subject_and_body(message: bytes) -> tuple[str, str]:
    """A commit message as git prints ``%s`` and ``%b``: first paragraph, and rest."""
    text = message.decode("utf-8", "replace").strip("\n")
    first, _, rest = text.partition("\n\n")
    subject = " ".join(line.strip() for line in first.splitlines() if line.strip())
    return subject, rest.strip()


def walk(
    repo: Repo,
    include: list[bytes],
    *,
    exclude: list[bytes] | None = None,
    paths: list[bytes] | None = None,
    limit: int | None = None,
) -> Generator[Commit]:
    """The commits reachable from *include* and not from *exclude*, newest first."""
    walker = repo.get_walker(
        include=[ObjectID(sha) for sha in include],
        exclude=[ObjectID(sha) for sha in exclude or []],
        paths=paths,
        max_entries=limit,
    )
    for item in walker:
        yield item.commit


def changed_paths_between(repo: Repo, old: bytes | None, new: bytes) -> list[str]:
    """The paths that differ between the trees *old* (none for a root) and *new*."""
    old_tree = tree_at(repo, old).id if old is not None else None
    paths: set[str] = set()
    for change in tree_changes(repo.object_store, old_tree, tree_at(repo, new).id):
        for side in (change.old, change.new):
            if side is not None and side.path is not None:
                paths.add(side.path.decode("utf-8", "replace"))
    return sorted(paths)


def config_value(repo: Repo, key: str) -> str:
    """The config value *key* (``section.name`` or ``section.sub.name``) or empty."""
    parts = key.split(".")
    if len(parts) < 2:
        return ""
    section = (
        (parts[0].encode(),)
        if len(parts) == 2
        else (
            parts[0].encode(),
            ".".join(parts[1:-1]).encode(),
        )
    )
    try:
        value = repo.get_config_stack().get(section, parts[-1].encode())
    except KeyError:
        return ""
    return value.decode("utf-8", "replace")


def config_matching(repo: Repo, pattern: str) -> dict[str, str]:
    """Every config key matching *pattern*, spelled ``section.sub.name``, by value.

    The files are read from the least to the most specific, so the
    repository's own value wins over the person's, as git resolves it.
    """
    found: dict[str, str] = {}
    matcher = re.compile(pattern)
    for backend in reversed(repo.get_config_stack().backends):
        for section in backend.sections():
            head = b".".join(section).decode("utf-8", "replace")
            for name, value in backend.items(section):
                key = f"{head}.{name.decode('utf-8', 'replace')}"
                if matcher.search(key):
                    found[key] = value.decode("utf-8", "replace")
    return found


def refs_under(repo: Repo, prefix: bytes, *, whole: bool = False) -> dict[str, str]:
    """The refs under *prefix* (``refs/heads/``), loose and packed, by sha.

    With *whole*, the prefix names whole path components, as a
    ``for-each-ref`` pattern does: ``refs/tags/pkg`` lists
    ``refs/tags/pkg/v1`` and not ``refs/tags/pkgx``.
    """
    if whole and not prefix.endswith(b"/"):
        prefix += b"/"
    found: dict[str, str] = {}
    for name in repo.refs.allkeys():
        if name.startswith(prefix) and name != b"HEAD":
            sha = ref_value(repo, name)
            if sha is not None:
                found[name.decode()] = sha.decode()
    return found


def when_made(repo: Repo, sha: bytes) -> datetime:
    """When the commit or tag *sha* was made, as an aware moment."""
    obj = repo[ObjectID(sha)]
    if isinstance(obj, Tag):
        stamp, offset = obj.tag_time, obj.tag_timezone
    else:
        commit = commit_of(repo, sha)
        stamp, offset = commit.commit_time, commit.commit_timezone
    return datetime.fromtimestamp(stamp, tz=timezone(timedelta(seconds=offset)))


def is_ancestor(repo: Repo, ancestor: bytes, descendant: bytes) -> bool:
    """Whether *ancestor* reaches *descendant*."""
    return graph.can_fast_forward(repo, ObjectID(ancestor), ObjectID(descendant))


def merge_base(repo: Repo, one: bytes, other: bytes) -> bytes | None:
    """The merge base of the two commits, or ``None`` without one."""
    bases = graph.find_merge_base(repo, [ObjectID(one), ObjectID(other)])
    return bases[0] if bases else None


def tree_files(repo: Repo, sha: bytes) -> dict[str, bytes]:
    """Every blob of the tree *sha* names (a commit's tree, or a tree), by path."""
    files: dict[str, bytes] = {}
    stack: list[tuple[str, Tree]] = [("", tree_at(repo, sha))]
    while stack:
        prefix, tree = stack.pop()
        for name, _mode, child in tree.iteritems():
            path = f"{prefix}{name.decode('utf-8', 'replace')}"
            obj = repo[child]
            if isinstance(obj, Tree):
                stack.append((path + "/", obj))
            elif isinstance(obj, Blob):
                files[path] = obj.data
    return files


def root_commit(
    repo: Repo,
    files: dict[str, bytes],
    message: str,
    *,
    identity: tuple[str, str],
    when: datetime,
) -> bytes:
    """Write a root commit holding *files* and return its sha; nothing else moves.

    Every object is written loose, as git writes a small commit: a
    pack written per commit would be walked against the pack list
    on its way in (dulwich 1.2.17, ``_complete_pack``), and a pack
    that git's own background ``gc --auto`` removed between the
    listing and the walk raises there, where the loose path lists
    nothing. Git packs the loose objects at its own pace, after the
    refs that reach them are in place.
    """
    objects: list[Blob | Tree | Commit] = []
    trees: dict[str, Tree] = {"": Tree()}
    for path in sorted(files):
        blob = Blob.from_string(files[path])
        objects.append(blob)
        directory, _, name = path.rpartition("/")
        parts = directory.split("/") if directory else []
        for depth in range(len(parts)):
            parent = "/".join(parts[:depth])
            child = "/".join(parts[: depth + 1])
            trees.setdefault(child, Tree())
            trees[parent].add(parts[depth].encode(), 0o040000, trees[child].id)
        trees[directory].add(name.encode(), 0o100644, blob.id)
    # A parent tree's id depends on its children's: write the deepest first,
    # re-adding each child's entry so the parent carries the final id.
    for directory in sorted(trees, key=lambda d: d.count("/"), reverse=True):
        if directory:
            parent, _, leaf = directory.rpartition("/")
            trees[parent].add(leaf.encode(), 0o040000, trees[directory].id)
        objects.append(trees[directory])
    commit = Commit()
    commit.tree = trees[""].id
    name, email = identity
    commit.author = commit.committer = f"{name} <{email}>".encode()
    commit.author_time = commit.commit_time = int(when.timestamp())
    commit.author_timezone = commit.commit_timezone = 0
    commit.encoding = b"UTF-8"
    commit.message = message.encode()
    objects.append(commit)
    for obj in objects:
        repo.object_store.add_object(obj)
    return commit.id


def set_ref(repo: Repo, ref: bytes, new: bytes, old: bytes | None) -> bool:
    """Point *ref* at *new* when it is at *old* (``None``: absent); whether it moved."""
    return repo.refs.set_if_equals(
        Ref(ref), None if old is None else ObjectID(old), ObjectID(new)
    )


def drop_ref(repo: Repo, ref: bytes) -> bool:
    """Delete *ref*; whether it was there."""
    if ref_value(repo, ref) is None:
        return False
    return repo.refs.remove_if_equals(Ref(ref), None)


__all__ = [
    "NotGitRepository",
    "Unresolved",
    "blob_text",
    "changed_paths_between",
    "commit_of",
    "config_matching",
    "config_value",
    "drop_ref",
    "entry",
    "head_target",
    "holds",
    "is_ancestor",
    "merge_base",
    "message_of",
    "name_of",
    "object_named",
    "opened",
    "point",
    "ref_value",
    "refs_under",
    "resolve",
    "root_commit",
    "set_ref",
    "subject_and_body",
    "tree_at",
    "tree_files",
    "walk",
    "when_made",
]
