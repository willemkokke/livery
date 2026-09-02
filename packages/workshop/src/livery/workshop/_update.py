"""The update family's file movers.

The pieces ``workflow.update`` drives: raise ``[[depends]]`` floors
to the latest released tags, refresh the rendered files (the render
applier where the template source lives here, ``copier update`` at
the installed workshop's tag everywhere else), and read the newest
release per package from the tags. The driver that branches,
commits, and submits lives beside this module.
"""

from __future__ import annotations

import re
from pathlib import Path

import footman
import toolroom
from footman import fail

from livery.workshop._git_ops import GitOps
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


def bump_floors(root: Path, git: GitOps, *, only: tuple[str, ...] = ()) -> list[str]:
    """Raise floors to the latest released tags; what changed.

    A floor names the oldest version a dependant accepts; this raises
    it to the newest release so instances move together. Both homes
    move in step: the ``[[depends]]`` edge in ``livery.toml`` and the
    ``>=`` constraint in ``pyproject.toml``. *only* scopes the move
    to floors on the named distributions (``livery-forge``); empty
    moves every floor.
    """
    packages = discover_packages(root)
    dist_names = {p.path: p.name for p in packages}
    released = latest_released(git.tags())
    changed = []
    for package in packages:
        for edge in package.depends:
            if only and dist_names.get(edge.path, "") not in only:
                continue
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

    result = toolroom.copier.opts(cwd=root)(
        "update",
        "--defaults",
        "--trust",
        "--skip-answered",  # The runner's name rides as render data, never an
        # answer: rebranding an instance is exactly this run
        # under the branded CLI.
        "--data",
        f"runner_prog={footman.prog()}",
        "--vcs-ref",
        f"v{__version__}",
        str(root),
    )
    if result.code != 0:
        fail(f"copier update exited {result.code}:\n{result.stdout}{result.stderr}")
    from livery.workshop._templates import apply_generated

    generated = apply_generated(root)
    return [*notes, "copier update ran; review the working tree", *generated]


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
