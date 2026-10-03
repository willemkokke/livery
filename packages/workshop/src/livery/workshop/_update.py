"""The update family's file movers.

The pieces ``workflow.update`` drives: raise ``[[depends]]`` floors
to the latest released tags, refresh the rendered files (the render
applier where the template source lives here, ``copier update`` at
the installed workshop's tag everywhere else), and read the newest
release per package from the tags. The driver that branches,
commits, and submits lives beside this module.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import livery.toolroom.tools.api as tools
from livery.footman.api import fail
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
    move in step: the ``[[depends]]`` edge in ``workshop.toml`` and the
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
            contract = package.directory / "workshop.toml"
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
    by ``copier update`` at the resolved artifact tag, so an
    instance moves to exactly the templates its workshop shipped
    with. copier reads its source and its answers from an answers
    file, so one is written from the contract for the run and removed
    after it; the contract is the record.
    """
    from livery.workshop._templates import (
        apply_project,
        local_template_dir,
        redacted_source,
        template_source,
    )

    if local_template_dir(root) is not None:
        return apply_project(root)
    from livery.workshop._identity import project_facts
    from livery.workshop._templates import render_injections, template_ref

    # The injected values ride as render data: the runner's name belongs
    # to the process, the floor and the extensions to the contract, so
    # rebranding or re-layering an instance is exactly this run under the
    # new contract.
    answers = project_facts(root)
    # A data file rather than --data pairs: the regions, the slots and
    # the fragments are tables of multi-line text, which no command
    # line spells safely.
    import tempfile

    import yaml

    with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as handle:
        yaml.safe_dump(render_injections(root, answers), handle)
        data_file = handle.name
    # The display-safe source: a credentialled clone of a private source
    # is git's credential machinery's job, never a byte on disk. The
    # file lives in the git directory, where it is never part of the
    # working tree: copier refuses a destination with untracked changes.
    git_dir = Path(
        tools.git.opts(cwd=root, recorded=False)(
            "rev-parse", "--absolute-git-dir"
        ).stdout.strip()
    )
    receipt = git_dir / "workshop-copier-answers.yml"
    from livery.workshop._templates import record_templates_ref, templates_ref

    previous = templates_ref(root)
    receipt.write_text(
        (f"_commit: {previous}\n" if previous else "")
        + f"_src_path: {redacted_source(template_source(root))}\n"
        + yaml.safe_dump(answers, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    try:
        result = tools.copier.opts(cwd=root)(
            "update",
            "--defaults",
            "--trust",
            "--skip-answered",
            "--data-file",
            data_file,
            "--answers-file",
            os.path.relpath(receipt, root),
            "--vcs-ref",
            template_ref(root),
            str(root),
        )
    finally:
        Path(data_file).unlink(missing_ok=True)
        receipt.unlink(missing_ok=True)
    if result.code != 0:
        fail(f"copier update exited {result.code}:\n{result.stdout}{result.stderr}")
    record_templates_ref(root, template_ref(root))
    from livery.workshop._shipped_files import deliver
    from livery.workshop._templates import apply_generated

    # The composed files come from the installed extensions, not from
    # the template copier just updated from.
    composed = [line.strip() for line in deliver(root)]
    generated = apply_generated(root)
    return ["copier update ran; review the working tree", *composed, *generated]
