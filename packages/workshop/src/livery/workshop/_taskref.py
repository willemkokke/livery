"""The per-package task reference: footman's renderer, one mechanism.

Every package that advertises ``footman.tasks`` entry points gets its
tasks documented in its own site section, and the section documents
the package's whole offering: each advertised provider renders in
isolation (a one-line tasks file mounting only that provider, no
cascade, no base), so no workspace composition, shadowing, disabling,
or ``exclude=`` mount can change what a package's docs say. Hidden
tasks stay out by the package's own flags. Pages render with
``livery.footman.markdown.render_site`` over the isolated tree, into the
owner's gitignored ``docs/_generated/tasks/`` tree, and the owner's
``nav.toml`` ``tasks`` marker block is rewritten so the section's
nav stays committed state and the drift gate stays offline.

The runner's ``docs_url`` is one URL template, so a generated alias
tree at the uniform ``_generated/tasks/<slug>/`` address redirects
each task to its page in the owning package's section.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import tomllib
from pathlib import Path
from typing import cast

from livery.footman import fail
from livery.workshop._docs import rewrite_nav_block
from livery.workshop._packages import Package, discover_packages

#: The nav marker block the reference generator owns in a providing
#: package's ``nav.toml``.
NAV_BLOCK = "tasks"


def advertised_providers(package: Package) -> list[str]:
    """The ``footman.tasks`` entry-point names *package* advertises.

    Read from the package's own committed ``pyproject.toml``, so the
    enumeration is offline and deterministic; a package without the
    table provides no tasks and gets no section.
    """
    pyproject = package.directory / "pyproject.toml"
    if not pyproject.is_file():
        return []
    parsed = tomllib.loads(pyproject.read_text("utf-8"))
    project = parsed.get("project")
    points = project.get("entry-points") if isinstance(project, dict) else None
    table = points.get("footman.tasks") if isinstance(points, dict) else None
    if not isinstance(table, dict):
        return []
    return sorted(str(name) for name in table)


def provider_tree(root: Path, identity: str) -> dict[str, object]:
    """The provider's advertised tree, rendered in isolation.

    A one-line tasks file mounts only *identity* (``--tasks-file``
    means one file, no cascade, no base), so the answer is the
    package's whole offering regardless of how any workspace
    composes it.
    """
    import shutil as _shutil

    import livery.footman as footman

    runner = _shutil.which(footman.prog())
    if not runner:
        fail(
            f"{footman.prog()} is not on PATH, so the task reference"
            " cannot read the advertised trees; enter the environment"
            " (source setup.sh) first"
        )
    with tempfile.TemporaryDirectory() as scratch:
        probe = Path(scratch) / "only.py"
        probe.write_text(
            f'from livery.footman import plugin\n\nplugin("{identity}")\n',
            encoding="utf-8",
        )
        result = footman.run(
            [runner, f"--tasks-file={probe}", "--json", "--list"],
            cwd=root,
            nofail=True,
            recorded=False,
        )
    if int(result) != 0:
        fail(
            f"{footman.prog()} --tasks-file --json --list for"
            f" {identity!r} exited {int(result)}"
        )
    return cast("dict[str, object]", json.loads(result.stdout)["tree"])


def _alias_page(target: str, name: str) -> str:
    """A redirect stub at the uniform address, linking and forwarding."""
    return (
        f'<script>window.location.replace("{target}");</script>\n\n'
        f"# {name}\n\nThis task's page lives in its owning package's"
        f" section: [{name}]({target}).\n"
    )


def _addresses(tree: dict[str, object], prefix: str = "") -> list[str]:
    """Every task address in *tree*, depth-first."""
    found: list[str] = []
    tasks = tree.get("tasks")
    if isinstance(tasks, dict):
        found += [prefix + name for name in tasks]
    groups = tree.get("groups")
    if isinstance(groups, dict):
        for name, sub in groups.items():
            if isinstance(sub, dict):
                found += _addresses(sub, f"{prefix}{name}.")
    return found


def generate_task_reference(root: Path) -> list[str]:
    """Render every advertising package's task reference; the packages.

    An index per group and a page per task of each provider's
    isolated tree, into the owner's ``docs/_generated/tasks/`` tree;
    the owner's ``tasks`` nav block rewritten; the site-root alias
    tree refreshed. A providing package whose ``nav.toml`` lacks the
    marker pair refuses naming the file: where the section sits in
    the tree is the author's decision.
    """
    import livery.footman as footman
    from livery.footman import markdown

    prog = footman.prog()
    providing = [
        (package, names)
        for package in discover_packages(root)
        if (names := advertised_providers(package))
    ]
    if not providing:
        return []
    aliases = root / "docs" / "_generated" / "tasks"
    shutil.rmtree(aliases, ignore_errors=True)
    aliases.mkdir(parents=True)
    rendered: list[str] = []
    for package, names in providing:
        trees = [provider_tree(root, identity) for identity in names]
        trees = [tree for tree in trees if tree.get("groups") or tree.get("tasks")]
        if not trees:
            continue
        owner = package.directory.name
        out = package.directory / "docs" / "_generated" / "tasks"
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True)
        block: list[str] = [
            '{ "Tasks" = [',
            '    { "Overview" = "_generated/tasks/index.md" },',
        ]
        index = ["# Task reference", ""]
        addresses: list[str] = []
        for tree in trees:
            addresses += _addresses(tree)
            groups = tree.get("groups")
            top_tasks = tree.get("tasks")
            if isinstance(groups, dict):
                for head in sorted(groups):
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
                        f'        {{ "Overview" ='
                        f' "_generated/tasks/{head}/index.md" }},'
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
            if isinstance(top_tasks, dict):
                for head in sorted(top_tasks):
                    page = markdown.render_page(
                        tree, path=(head,), heading=1, flavor="material", prog=prog
                    )
                    (out / f"{head}.md").write_text(page, encoding="utf-8")
                    index.append(f"- [{head}]({head}.md)")
                    block.append(f'    {{ "{head}" = "_generated/tasks/{head}.md" }},')
        block.append("] },")
        (out / "index.md").write_text("\n".join(index) + "\n", encoding="utf-8")
        rewrite_nav_block(package.directory / "docs" / "nav.toml", NAV_BLOCK, block)
        for address in sorted(addresses):
            section = "/".join(address.split("."))
            destination = f"../../packages/{owner}/_generated/tasks/{section}/"
            (aliases / f"{address.replace('.', '-')}.md").write_text(
                _alias_page(destination, address), encoding="utf-8"
            )
        rendered.append(owner)
    return sorted(rendered)
