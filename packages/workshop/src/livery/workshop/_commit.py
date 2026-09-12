"""The ``commit`` verb: a conventional commit on a proved tree.

Stages the change, derives the scope from the packages it touches,
runs the affected gate first so every commit sits on a proved tree,
validates the subject the way ``submit`` validates a title, and
commits. ``git commit`` keeps working; the verb is the spelling that
never mistypes the scope the release train reads and never commits an
unproved tree.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import livery.footman as footman
from livery.footman import Arg, doc, fail
from livery.workshop._conventional import TITLE_RE, TYPES
from livery.workshop._git_ops import GitOps
from livery.workshop._layers import workspace_root


def scope_of(root: Path, paths: list[str]) -> str:
    """The scope of a change to *paths*: the packages it touches, by directory.

    Empty when the change touches no package (root files, notes), and
    the subject carries no scope then. Several packages join with a
    comma, the spelling the release train reads.
    """
    from livery.workshop._packages import discover_packages

    names: set[str] = set()
    for package in discover_packages(root):
        prefix = package.path + "/"
        if any(path.startswith(prefix) for path in paths):
            names.add(package.directory.name)
    return ",".join(sorted(names))


def _run_check() -> None:
    """The reflex before a commit: the affected gate in its fix mode."""
    from livery.workshop._quality import check

    check(affected=True, fix=True)


@footman.task(serial=True)
def commit(
    type: Arg[str] = "",
    subject: Arg[str] = "",
    body: Annotated[str, doc("the commit body, after the subject")] = "",
    scope: Annotated[
        str, doc("the scope; derived from the changed paths when absent")
    ] = "",
    only: Annotated[
        str, doc("stage these paths alone, comma-separated; everything when absent")
    ] = "",
    breaking: Annotated[
        bool, doc("mark the commit as a break (the ! before the colon)")
    ] = False,
    check: Annotated[
        bool, doc("run the affected gate first, healing what is mechanical")
    ] = True,
) -> None:
    """Commit the change as ``type(scope): subject`` on a proved tree.

    TYPE is one of the conventional types the release train reads and
    SUBJECT says what the change does. The scope is the packages the
    staged change touches, by directory name, or none for a change
    outside every package; ``--scope`` overrides it. The affected gate
    runs first in its fix mode, so mechanical findings heal into the
    commit and nothing unproved is committed; ``--no-check`` skips it.
    Everything is staged unless ``--only`` names the paths. Refuses on
    ``main``, on a reserved branch, and with nothing to commit.
    """
    if type not in TYPES:
        fail(
            f"the type is one of {', '.join(TYPES)}:"
            f' `{footman.prog()} commit feat "the subject"`'
        )
    if not subject.strip():
        fail("name the subject: what the change does, in the imperative")
    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    git = GitOps(root)
    branch = git.current_branch()
    if not branch:
        fail("HEAD is detached: check out a branch first")
    if branch == "main":
        fail(
            f"on main: start a branch first (`{footman.prog()} start"
            " docs/the-plan`, or an issue)"
        )
    if branch.startswith("workflow/"):
        fail(f"{branch} is a reserved branch; its commits are the workflow engine's")
    if check:
        _run_check()
    if only:
        git._run("add", "--", *[p.strip() for p in only.split(",") if p.strip()])
    else:
        git._run("add", "-A")
    staged = [
        line
        for line in git._run("diff", "--cached", "--name-only").splitlines()
        if line
    ]
    if not staged:
        fail(
            "nothing to commit: the working tree matches HEAD"
            + (" for the paths named" if only else "")
        )
    given = bool(scope)
    scope = scope or scope_of(root, staged)
    title = f"{type}({scope})" if scope else type
    title += ("!" if breaking else "") + f": {subject.strip()}"
    if not TITLE_RE.match(title):
        fail(f"{title!r} is not a conventional subject: type(scope)!: subject")
    args = ["commit", "-m", title]
    if body.strip():
        args += ["-m", body.strip()]
    git._run(*args)
    print(
        f"  scope: {scope or 'none'} ({'given' if given else 'from the changed paths'})"
    )
    print(f"  committed {git.head_sha()[:8]}: {title}")
