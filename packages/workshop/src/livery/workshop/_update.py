"""``fm update``: the update wave's instance half (W7).

One idempotent verb, run from a clean checkout of the base branch:
bump every ``[[depends]]`` floor to the latest released tag, refresh
the content channel (``fm sync``), refresh the rendered files (the
render applier where the template source lives here, ``copier
update`` at the installed workshop's tag everywhere else), and then
become W3: branch, commit, and hand the branch to the submit flow.
Nothing changed means nothing happens: no branch, no pull request,
one line saying so.
"""

from __future__ import annotations

import datetime
import re
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import footman
from footman import doc, fail

from livery.workshop._git_ops import GitOps
from livery.workshop._layers import workspace_root
from livery.workshop._packages import discover_packages

_RELEASE_TAG_RE = re.compile(r"^(packages/[^/]+)/v(\d+)\.(\d+)\.(\d+)$")


def latest_released(tags: tuple[str, ...]) -> dict[str, str]:
    """The newest released version per package path, from *tags*."""
    latest: dict[str, tuple[int, int, int]] = {}
    for tag in tags:
        match = _RELEASE_TAG_RE.fullmatch(tag)
        if match is None:
            continue
        path = match.group(1)
        version = (int(match.group(2)), int(match.group(3)), int(match.group(4)))
        if version > latest.get(path, (-1, -1, -1)):
            latest[path] = version
    return {path: ".".join(map(str, v)) for path, v in latest.items()}


def bump_floors(root: Path, git: GitOps) -> list[str]:
    """Raise every floor to the latest released tag; what changed.

    A floor names the oldest version a dependant accepts; the wave
    raises it to the newest release so instances move together. Both
    homes move in step: the ``[[depends]]`` edge in ``livery.toml``
    and the ``>=`` constraint in ``pyproject.toml``.
    """
    released = latest_released(git.tags())
    changed = []
    for package in discover_packages(root):
        for edge in package.depends:
            newest = released.get(edge.path, "")
            if not edge.floor or not newest or newest == edge.floor:
                continue
            contract = package.directory / "livery.toml"
            text = contract.read_text("utf-8")
            scoped = _bump_edge_floor(text, edge.path, edge.floor, newest)
            contract.write_text(scoped, encoding="utf-8")
            pyproject = package.directory / "pyproject.toml"
            text = pyproject.read_text("utf-8")
            pyproject.write_text(
                text.replace(f">={edge.floor}", f">={newest}"), encoding="utf-8"
            )
            changed.append(
                f"{package.path}: floor on {edge.path} {edge.floor} -> {newest}"
            )
    return changed


def _bump_edge_floor(text: str, dep_path: str, old: str, new: str) -> str:
    """The contract text with one edge's floor raised, scoped to its block."""
    anchor = text.find(f'path = "{dep_path}"')
    if anchor == -1:
        fail(f"no [[depends]] edge on {dep_path} found to bump")
    tail = text[anchor:]
    bumped, count = re.subn(
        rf'floor = "{re.escape(old)}"', f'floor = "{new}"', tail, count=1
    )
    if count != 1:
        fail(f"the edge on {dep_path} has no floor {old!r} line to bump")
    return text[:anchor] + bumped


def refresh_rendered(root: Path) -> list[str]:
    """Refresh the rendered files; what changed.

    Where the contract's template source is a local directory the
    render applier is the truth; a remote source (the artifact
    repository by default, a fork if the contract says so) is pulled
    by ``copier update`` at the installed workshop's own tag, so an
    instance moves to exactly the templates its workshop shipped
    with. The contract's source is written into the answers file
    first, because that is where copier reads it.
    """
    from livery.workshop._templates import (
        apply_project,
        local_template_dir,
        template_source,
    )

    if local_template_dir(root) is not None:
        return apply_project(root)
    notes = _align_answers_source(root, template_source(root))
    from livery.workshop import __version__

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "copier",
            "update",
            "--defaults",
            "--trust",
            "--skip-answered",
            "--vcs-ref",
            f"v{__version__}",
            str(root),
        ],
        capture_output=True,
        text=True,
        cwd=root,
    )
    if result.returncode != 0:
        fail(
            f"copier update exited {result.returncode}:\n{result.stdout}{result.stderr}"
        )
    return [*notes, "copier update ran; review the working tree"]


def _align_answers_source(root: Path, source: str) -> list[str]:
    """Make the answers' ``_src_path`` follow the contract; what changed.

    ``copier update`` reads its source from the answers file, so the
    contract's declared source (a fork, say) must be written there
    first or the update would quietly pull from the old place.
    """
    answers = root / ".copier-answers.yml"
    if not answers.is_file():
        return []
    text = answers.read_text("utf-8")
    aligned, count = re.subn(
        r"^_src_path: .*$", f"_src_path: {source}", text, count=1, flags=re.M
    )
    if count and aligned != text:
        answers.write_text(aligned, encoding="utf-8")
        return [f"answers _src_path now follows the contract: {source}"]
    return []


def update_flow(
    root: Path,
    git: GitOps,
    *,
    armed: bool,
    armed_reason: str = "",
    base: str = "main",
) -> None:
    """The whole wave step for this instance; see the task docstring."""
    if git.current_branch() != base:
        fail(f"fm update starts from {base}: switch branches first")
    if not git.is_clean():
        fail("the working tree is not clean: commit or drop the changes first")
    git.integrate(base)
    from livery.workshop._sync import sync_workspace

    notes = bump_floors(root, git)
    notes += [f"sync: {line.strip()}" for line in sync_workspace(root)]
    notes += [f"render: {line}" for line in refresh_rendered(root)]
    if git.is_clean():
        print("  nothing to update: floors, content, and render all current")
        return
    day = datetime.date.today().isoformat().replace("-", "")
    branch = f"chore/update-{day}"
    git.create_branch(branch)
    body = "\n".join(f"- {note}" for note in notes)
    git.commit_all(f"chore: the update wave\n\n{body}")
    from livery.workshop._forge_lane import this_repository
    from livery.workshop._submit import submit_flow

    submit_flow(
        this_repository(root),
        git,
        title="chore: the update wave",
        body=body,
        base=base,
        armed=armed,
        armed_reason=armed_reason,
    )


@footman.task
def update(
    armed: Annotated[bool, doc("arm the update's pull request")] = False,
) -> None:
    """Run the update wave on this instance (W7): floors, content, render, W3.

    Idempotent, and quiet when there is nothing to move: no branch and
    no pull request are created for a current instance. A template
    conflict surfaces as the submit flow's own verdict, never a silent
    forced merge.
    """
    root = workspace_root()
    if root is None:
        fail("no workspace: no livery.toml above the working directory")
    from livery.workshop._submit import arming_reason

    reason = arming_reason(armed=armed, flag_given=footman.given("armed"))
    update_flow(root, GitOps(root), armed=armed, armed_reason=reason)
