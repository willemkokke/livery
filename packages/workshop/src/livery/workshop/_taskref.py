"""The per-package task reference: footman's renderer, one mechanism.

Every package that provides task groups gets its tasks documented in
its own site section. Ownership is the task's defining source file:
a task whose function lives under a workspace package belongs to
that package, and a task defined outside the workspace (a runner
builtin) is not documented here; it arrives with its package at the
migration. Pages render with ``footman.markdown.render_site`` over
the same tree ``--json --list`` emits, into the owner's gitignored
``docs/_generated/tasks/`` tree, and the owner's ``nav.toml``
``tasks`` marker block is rewritten so the section's nav stays
committed state and the drift gate stays offline.

The runner's ``docs_url`` is one URL template, so a generated alias
tree at the uniform ``_generated/tasks/<slug>/`` address redirects
each task to its page in the owning package's section.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from footman import fail

from livery.workshop._docs import rewrite_nav_block
from livery.workshop._packages import Package, discover_packages

#: The nav marker block the reference generator owns in a providing
#: package's ``nav.toml``.
NAV_BLOCK = "tasks"


def owning_package(source_file: str, packages: Sequence[Package]) -> Package | None:
    """The workspace package whose tree defines *source_file*, or None."""
    try:
        source = Path(source_file).resolve()
    except OSError:
        return None
    for package in packages:
        if source.is_relative_to(package.directory.resolve()):
            return package
    return None


def task_ownership(root: Path) -> dict[str, str]:
    """Owning package directory name per public task, by full name.

    Read from the source map the cascade hook stashed for this run.
    Tasks defined outside the workspace map to nothing and are not
    documented here.
    """
    import footman

    from livery.workshop import _env_tasks

    if not _env_tasks.TASK_SOURCES:
        fail(
            "the task reference needs the merged tree, which only a"
            " real runner invocation carries; run it as"
            f" `{footman.prog()} docs.task-reference`"
        )
    packages = discover_packages(root)
    owners: dict[str, str] = {}
    for name, source in _env_tasks.TASK_SOURCES.items():
        if not source:
            continue
        owner = owning_package(source, packages)
        if owner is not None:
            owners[name] = owner.directory.name
    return owners


def _group_owners(owners: dict[str, str]) -> dict[str, str]:
    """The owning package per top-level entry (group, or root task).

    A group belongs to the package defining most of its tasks; the
    workspace has no mixed groups today, and a mixed group's minority
    tasks still page under the group, where the runner shows them.
    """
    tallies: dict[str, dict[str, int]] = {}
    for name, owner in owners.items():
        head = name.split(".", 1)[0]
        tally = tallies.setdefault(head, {})
        tally[owner] = tally.get(owner, 0) + 1
    return {
        head: max(tally, key=lambda owner: tally[owner])
        for head, tally in tallies.items()
    }


def _tree(root: Path) -> dict[str, object]:
    """The manifest tree, from the runner's own ``--json --list``."""
    import shutil as _shutil

    import footman

    runner = _shutil.which(footman.prog())
    if not runner:
        fail(
            f"{footman.prog()} is not on PATH, so the task reference"
            " cannot read the tree; enter the environment"
            " (source setup.sh) first"
        )
    result = footman.run(
        [runner, "--json", "--list"], cwd=root, nofail=True, recorded=False
    )
    if int(result) != 0:
        fail(f"{footman.prog()} --json --list exited {int(result)}")
    return cast("dict[str, object]", json.loads(result.stdout)["tree"])


def _alias_page(target: str, name: str) -> str:
    """A redirect stub at the uniform address, linking and forwarding."""
    return (
        f'<script>window.location.replace("{target}");</script>\n\n'
        f"# {name}\n\nThis task's page lives in its owning package's"
        f" section: [{name}]({target}).\n"
    )


def generate_task_reference(root: Path) -> list[str]:
    """Render every providing package's task reference; the packages.

    An index per group and a page per public task into the owner's
    ``docs/_generated/tasks/`` tree, the owner's ``tasks`` nav block
    rewritten, and the site-root alias tree refreshed. A providing
    package whose ``nav.toml`` lacks the marker pair refuses naming
    the file: where the section sits in the tree is the author's
    decision.
    """
    import footman
    from footman import markdown

    owners = task_ownership(root)
    if not owners:
        return []
    heads = _group_owners(owners)
    tree = _tree(root)
    prog = footman.prog()
    packages = {p.directory.name: p for p in discover_packages(root)}
    per_package: dict[str, list[str]] = {}
    for head, owner in sorted(heads.items()):
        per_package.setdefault(owner, []).append(head)
    aliases = root / "docs" / "_generated" / "tasks"
    shutil.rmtree(aliases, ignore_errors=True)
    aliases.mkdir(parents=True)
    groups = tree.get("groups")
    top_tasks = tree.get("tasks")
    for owner, names in per_package.items():
        package = packages[owner]
        out = package.directory / "docs" / "_generated" / "tasks"
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True)
        block: list[str] = [
            '{ "Tasks" = [',
            '    { "Overview" = "_generated/tasks/index.md" },',
        ]
        index = ["# Task reference", ""]
        for head in names:
            if isinstance(groups, dict) and head in groups:
                site = markdown.render_site(
                    tree, path=(head,), flavor="material", prog=prog
                )
                for relative, content in site.items():
                    target = out / head / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
                index.append(f"- [{head}]({head}/index.md)")
                block.append(f'    {{ "{head}" = [')
                block.append(
                    f'        {{ "Overview" = "_generated/tasks/{head}/index.md" }},'
                )
                for relative in sorted(site):
                    if relative.rsplit("/", 1)[-1] == "index.md":
                        continue
                    dotted = relative.removesuffix(".md").replace("/", ".")
                    block.append(
                        f'        {{ "{dotted}" ='
                        f' "_generated/tasks/{head}/{relative}" }},'
                    )
                block.append("    ] },")
            elif isinstance(top_tasks, dict) and head in top_tasks:
                page = markdown.render_page(
                    tree, path=(head,), heading=1, flavor="material", prog=prog
                )
                (out / f"{head}.md").write_text(page, encoding="utf-8")
                index.append(f"- [{head}]({head}.md)")
                block.append(f'    {{ "{head}" = "_generated/tasks/{head}.md" }},')
        block.append("] },")
        (out / "index.md").write_text("\n".join(index) + "\n", encoding="utf-8")
        rewrite_nav_block(package.directory / "docs" / "nav.toml", NAV_BLOCK, block)
    for name in sorted(owners):
        head = name.split(".", 1)[0]
        if heads.get(head) is None:
            continue
        section = "/".join(name.split("."))
        destination = f"../../packages/{heads[head]}/_generated/tasks/{section}/"
        (aliases / f"{name.replace('.', '-')}.md").write_text(
            _alias_page(destination, name), encoding="utf-8"
        )
    return sorted(per_package)
