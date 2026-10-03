"""Deliver the files the listed extensions ship, through the fragment engine.

An extension ships a workspace file as a template in its wheel, under
`content/root/<path>` for a file at the workspace root and
`content/package/<path>` for that file in every package listing the
extension; a `.jinja` suffix on the source is dropped for the
target. `fm sync` composes them with
[livery.workshop._fragment_engine.plan][] and writes them with
[livery.workshop._fragment_engine.apply][]; the gate's template check
judges them with [livery.workshop._fragment_engine.drift][].

The prose fragments, skills and hooks under the same `content/`
directory are delivered by `livery.workshop._prose` and
`livery.workshop._materialise` instead.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any, cast

from livery.footman import api as footman
from livery.footman.api import fail
from livery.workshop._extensions import extension_content, stack_names
from livery.workshop._fragment_engine import (
    PACKAGE_PREFIX,
    Fragment,
    Output,
    apply,
    drift,
    plan,
)

ROOT_CONTENT = "root"
"""The `content/` subdirectory whose files land at the workspace root."""

PACKAGE_CONTENT = "package"
"""The `content/` subdirectory whose files land in each package."""

TEMPLATE = ".jinja"
"""The suffix a shipped template may carry, dropped for its target: a
template named like a tool's configuration file (`pyproject.toml`) would
otherwise be read as one by the tool that searches the tree for it."""

PROJECT_FILE = "pyproject.toml"
"""The workspace's project file, which renders from the contract's identity."""


def shipped(root: Path) -> tuple[list[Fragment], list[str]]:
    """Every listed extension's shipped files as fragments, and the order they apply."""
    order = list(stack_names(root))
    fragments: list[Fragment] = []
    for extension in order:
        content = extension_content(extension)
        if content is None:
            continue
        for kind, prefix in ((ROOT_CONTENT, ""), (PACKAGE_CONTENT, PACKAGE_PREFIX)):
            base = content / kind
            if not base.is_dir():
                continue
            for source in sorted(base.rglob("*")):
                if not source.is_file() or "__pycache__" in source.parts:
                    continue
                relative = source.relative_to(base).as_posix().removesuffix(TEMPLATE)
                fragments.append(
                    Fragment(extension, relative, f"{prefix}{relative}", source=source)
                )
    fragments += _check_contributions(order)
    from livery.workshop._identity import is_born

    if not is_born(root):
        # The project file renders from the contract's identity: the
        # name, the namespace, the authors. A workspace whose contract
        # names no project composes none.
        fragments = [f for f in fragments if f.target != PROJECT_FILE]
    return fragments, order


def _check_contributions(order: list[str]) -> list[Fragment]:
    """What each registered check says to the editor, as data its extension contributes.

    A check's settings land in `.vscode/settings.json` and its marketplace
    id in `.vscode/extensions.json`, each a fragment of its own, so a check
    that is unregistered takes its lines with it.
    """
    from livery.workshop._checks import checks_by_name

    found: list[Fragment] = []
    for name, record in sorted(checks_by_name().items()):
        if record.extension not in order:
            continue
        for fragment in record.fragments:
            if fragment.file == PROJECT_FILE:
                found.append(
                    Fragment(
                        record.extension,
                        f"check {name} tables",
                        PROJECT_FILE,
                        fragment.text,
                    )
                )
            if fragment.file == ".vscode/settings.json":
                found.append(
                    Fragment(
                        record.extension,
                        f"check {name} settings",
                        fragment.file,
                        fragment.text,
                        contributes=True,
                    )
                )
        if record.editor_extension:
            found.append(
                Fragment(
                    record.extension,
                    f"check {name} recommendation",
                    ".vscode/extensions.json",
                    json.dumps({"recommendations": [record.editor_extension]}),
                    contributes=True,
                )
            )
    return found


def _data(root: Path) -> dict[str, Any]:
    """What every shipped template reads.

    The runner's name and the project's always; once the contract names
    the project, also its identity and everything the workspace's state
    gives the render: the roster, the Python floor, the slots, the
    extension requirements and the registry.
    """
    from livery.workshop._identity import is_born, project_facts
    from livery.workshop._templates import fragment_data, render_injections

    data: dict[str, Any] = {"prog": footman.prog(), "project_name": root.resolve().name}
    if is_born(root):
        answers = project_facts(root)
        data = fragment_data({**data, **answers, **render_injections(root, answers)})
    return data


def _packaged(
    root: Path, order: list[str]
) -> tuple[list[Fragment], dict[str, tuple[str, ...]], dict[str, dict[str, Any]]]:
    """Each package's per-package files, with the package map and its data.

    A package's `.clang-format` and `.clang-tidy` come from the check
    whose fragment is the nearest one down the package's kind chain, and
    render with the kind as data. Until packages list extensions of their
    own, the kind chain is what picks them.
    """
    from livery.workshop._checks import checks_by_name
    from livery.workshop._fragments import PACKAGE_FILES, package_fragment
    from livery.workshop._packages import discover_packages

    packages = discover_packages(root) if (root / "packages").is_dir() else ()
    fragments: list[Fragment] = []
    for package in packages:
        for file in PACKAGE_FILES:
            found = package_fragment(package.kind, file)
            if found is None:
                continue
            text, check = found
            owner = checks_by_name()[check].extension
            if owner in order:
                fragments.append(
                    Fragment(
                        owner,
                        f"check {check} {package.path}",
                        f"{package.path}/{file}",
                        text,
                    )
                )
    paths: dict[str, tuple[str, ...]] = {package.path: () for package in packages}
    data: dict[str, dict[str, Any]] = {
        package.path: {"kind": package.kind} for package in packages
    }
    return fragments, paths, data


def outputs(root: Path) -> tuple[Output, ...]:
    """The shipped files as the listed extensions render them for *root*."""
    return _composed(root)[0]


def _composed(root: Path) -> tuple[tuple[Output, ...], list[str]]:
    """The shipped files for *root*, and a line naming the LFS rules left out.

    While the workspace has Git LFS off, an `.gitattributes` keeps no
    LFS line; the line says which patterns were left out and the
    setting that composes them.
    """
    from livery.workshop._lfs import KEY, lfs_enabled, without_lfs

    fragments, order = shipped(root)
    per_package, packages, package_data = _packaged(root, order)
    planned = plan(
        root,
        [*fragments, *per_package],
        order,
        _data(root),
        packages=packages,
        package_data=package_data,
    )
    if lfs_enabled(root):
        return planned, []

    kept: list[Output] = []
    notes: list[str] = []
    for output in planned:
        if PurePosixPath(output.path).name == ".gitattributes":
            text, left = without_lfs(output.body.decode())
            if left:
                notes.append(
                    f"  {output.path}: Git LFS is off, so the LFS rules for"
                    f" {', '.join(left)} are left out; `[workspace] {KEY} = true`"
                    " in workshop.toml composes them"
                )
                output = Output(output.path, text.encode(), output.owners)
        kept.append(output)
    return (*kept, *_agent_outputs(root, order)), notes


def deliver(root: Path) -> list[str]:
    """Write the shipped files into *root*; one line per file that changed."""
    from livery.workshop._packages import discover_packages

    planned, notes = _composed(root)
    homes = (
        [package.path for package in discover_packages(root)]
        if (root / "packages").is_dir()
        else []
    )
    return notes + apply(root, planned, packages=homes)


def shipped_drift(root: Path) -> list[str]:
    """One line per shipped file whose committed bytes are not its render."""
    from livery.workshop._packages import discover_packages

    homes = (
        [package.path for package in discover_packages(root)]
        if (root / "packages").is_dir()
        else []
    )
    return [line.strip() for line in drift(root, outputs(root), packages=homes)]


def relocate(root: Path) -> list[str]:
    """Move what a composed JSON file has and its render lacks into its region.

    VS Code writes a setting changed through its UI into `settings.json`
    just before the closing brace, and a recommendation into the list,
    both outside the repository's region. A top-level key the render does
    not produce, or an item a list holds that the render's list does not,
    is the repository's: it moves into the file's first region, and the
    file is written as the render with that region. A key the render does
    produce, set here to another value, is an override of an extension's
    or a check's setting; it refuses, naming the key and both values,
    since which one should stand is a person's call.

    Returns:
        One line per key or item moved.

    Raises:
        Failed: for a key the render owns that is set to another value.
    """
    from livery.workshop._regions import regions_in

    lines: list[str] = []
    for output in outputs(root):
        target = root / output.path
        if not output.path.endswith(".json") or not target.is_file():
            continue
        rendered = output.body.decode()
        regions = regions_in(rendered)
        theirs = _jsonc(target.read_text(encoding="utf-8"))
        ours = _jsonc(rendered)
        if not regions or theirs is None or ours is None:
            continue
        added: list[str] = []
        clashes: list[str] = []
        for key, value in theirs.items():
            if key not in ours:
                body = json.dumps(value, indent=2).replace("\n", "\n  ")
                added.append(f"  {json.dumps(key)}: {body},")
                lines.append(f"  {output.path}: moved {key} into the region")
            elif isinstance(value, list) and isinstance(ours[key], list):
                for item in cast("list[object]", value):
                    if item not in ours[key]:
                        added.append(f"    {json.dumps(item)},")
                        lines.append(f"  {output.path}: moved {item} into the region")
            elif value != ours[key]:
                clashes.append(
                    f"{key}: the render sets {json.dumps(ours[key])}, the file"
                    f" {json.dumps(value)}"
                )
        if clashes:
            fail(
                f"{output.path}: a setting the render owns was changed here; keep"
                " the render's value, or change it where its extension takes"
                " configuration:\n  " + "\n  ".join(clashes)
            )
        if not added:
            continue
        text = rendered.split("\n")
        text[regions[0].last - 1 : regions[0].last - 1] = added
        target.write_text("\n".join(text), encoding="utf-8")
    return lines


def _jsonc(text: str) -> dict[str, Any] | None:
    """*text* read as JSON with whole-line comments and trailing commas, or None."""
    import re

    kept = "\n".join(
        line for line in text.split("\n") if not line.lstrip().startswith("//")
    )
    kept = re.sub(r",(\s*[}\]])", r"\1", kept)
    try:
        loaded = json.loads(kept)
    except ValueError:
        return None
    return cast("dict[str, Any]", loaded) if isinstance(loaded, dict) else None


def _package_outputs(member: Path, kind: str) -> tuple[Output, ...]:
    """The per-package files a package of *kind* renders, relative to *member*."""
    from livery.workshop._checks import checks_by_name
    from livery.workshop._fragments import PACKAGE_FILES, package_fragment

    fragments: list[Fragment] = []
    owners: list[str] = []
    for file in PACKAGE_FILES:
        found = package_fragment(kind, file)
        if found is None:
            continue
        text, check = found
        owner = checks_by_name()[check].extension
        owners.append(owner)
        fragments.append(Fragment(owner, f"check {check}", file, text))
    return plan(member, fragments, list(dict.fromkeys(owners)), {"kind": kind})


def settle_package(member: Path, kind: str) -> list[str]:
    """Compose the per-package files of a package of *kind* at *member* alone.

    What a sync does for one package, without a workspace around it: the
    conformance kit and a birth's probe settle a package directory this way.
    """
    return apply(member, _package_outputs(member, kind))


def judge_package(member: Path, kind: str) -> list[str]:
    """The drift lines for the per-package files of a package of *kind* at *member*."""
    return [line.strip() for line in drift(member, _package_outputs(member, kind))]


STUB_HEADER = (
    "<!-- Managed by `{prog} sync`: one import per fragment, in section\n"
    "     order, the repository's own fragments/ after them, then its\n"
    "     CLAUDE.project.md, which always wins. Edit CLAUDE.project.md,\n"
    "     never this file. -->\n"
)
"""The `CLAUDE.md` stub's header, formatted with the runner's name at write time."""


def _agent_outputs(root: Path, order: list[str]) -> list[Output]:
    """What the listed extensions give the agent, and the stub that imports it.

    Each extension's skills and hooks are links into its shipped content,
    and its `settings.json` a copy; the prose fragments are copies in
    section order under `.workshop/fragments/`. All of them belong to this
    checkout alone. The `CLAUDE.md` stub that imports the fragments, then
    the repository's own, then `CLAUDE.project.md`, is committed.
    """
    from livery.workshop import _prose

    outputs: list[Output] = []
    settings: Output | None = None
    listed: list[_prose.Prose] = []
    for extension in order:
        content = extension_content(extension)
        if content is None:
            continue
        listed += _prose.shipped(extension, content)
        for kind in ("skills", "hooks"):
            shipped = content / kind
            if not shipped.is_dir():
                continue
            for entry in sorted(shipped.iterdir()):
                if entry.name.startswith(".") or entry.name == "__pycache__":
                    continue
                outputs.append(
                    Output(
                        f".claude/{kind}/{entry.name}",
                        b"",
                        (f"{extension}:{kind}/{entry.name}",),
                        link=entry,
                        local=True,
                    )
                )
        source = content / "settings.json"
        if source.is_file():
            if settings is not None:
                fail(
                    f".claude/settings.json has one owner, and {settings.owners[0]}"
                    f" and {extension} both ship it"
                )
            settings = Output(
                ".claude/settings.json",
                source.read_bytes().replace(b"\r\n", b"\n"),
                (f"{extension}:settings.json",),
                local=True,
            )
    if settings is not None:
        outputs.append(settings)
    listed += _prose.repository_fragments(root)
    chosen, own = _prose.agent_set(root, listed)
    delivered: list[str] = []
    for prose in chosen:
        if prose.render is not None:
            text = prose.render(root, _prose.AGENT)
            if not text:
                continue
            body = (_prose.rendered_header(prose) + text).encode("utf-8")
        elif prose.source is not None:
            body = prose.source.read_bytes().replace(b"\r\n", b"\n")
        else:
            continue
        outputs.append(
            Output(
                f"{_prose.DELIVERED}/{prose.name}",
                body,
                (f"{prose.extension}:{prose.name}",),
                local=True,
            )
        )
        delivered.append(prose.name)
    stub = STUB_HEADER.format(prog=footman.prog())
    stub += "".join(f"@{_prose.DELIVERED}/{name}\n" for name in delivered)
    stub += "".join(f"@{_prose.OWN}/{name}\n" for name in own)
    stub += "@CLAUDE.project.md\n"
    outputs.append(Output("CLAUDE.md", stub.encode(), ("livery.workshop:CLAUDE.md",)))
    return outputs
