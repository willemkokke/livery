"""Deliver the files the listed extensions ship, through the fragment engine.

An extension ships a workspace file as a template in its wheel, under
`content/root/<path>` for a file at the workspace root and
`content/package/<path>` for that file in every package listing the
extension; a `.jinja` suffix on the source is dropped for the
target. Under `content/for/<target>/root/` and
`content/for/<target>/package/` it ships the files it adds to another
extension, delivered while its `[for.<target>]` table applies
([livery.workshop._extensions.contributing][]). `fm sync` composes them with
[livery.workshop._fragment_engine.plan][] and writes them with
[livery.workshop._fragment_engine.apply][]; the gate's ``drift.check``
judges them with [livery.workshop._fragment_engine.drift][].

An extension takes the place of a shipped file of the base or of an
extension it requires by declaring it in its `extension.toml`:
`[replaces] "<owner>:<name>" = "<reason>"`, where its own file of the
same name is the replacement, or `[deletes]` with the same shape to
remove it. Each applies after what it requires, so the replacement
follows no list's order. The same declarations govern seeds
([livery.workshop._seeds.create][]).

An extension also writes files from its own code: each
`[fragments."<target>"]` table in its `extension.toml` names a render
the engine calls with the workspace root, for one file or, with a
target ending in `/`, a directory of them
([livery.workshop._shipped_files.computed_outputs][]), and a
`[for.<target>.fragments]` table does the same while it applies. An
extension's files are written while the installed set holds it, the
root's set or a package's, and withdrawn when no list holds it any
more ([livery.workshop._shipped_files.delivery_order][]).
"""

from __future__ import annotations

import json
from dataclasses import replace
from operator import itemgetter
from pathlib import Path
from typing import Any, cast

import livery.footman as footman
from livery.footman import fail
from livery.workshop._extensions import (
    SELF,
    contributing,
    declaration,
    extension_content,
    stack_names,
)
from livery.workshop._fragment_engine import (
    PACKAGE_PREFIX,
    Fragment,
    Output,
    apply,
    drift,
    plan,
    write_root_file,
)

ROOT_CONTENT = "root"
"""The `content/` subdirectory whose files land at the workspace root."""

PACKAGE_CONTENT = "package"
"""The `content/` subdirectory whose files land in each package."""

FOR_CONTENT = "for"
"""The `content/` subdirectory holding, per target, the files an extension
adds to another: `for/<target>/root/` and `for/<target>/package/`."""

TEMPLATE = ".jinja"
"""The suffix a shipped template may carry, dropped for its target: a
template named like a tool's configuration file (`pyproject.toml`) would
otherwise be read as one by the tool that searches the tree for it."""

PROJECT_FILE = "pyproject.toml"
"""The workspace's project file, which renders from the contract's identity."""


def delivery_order(root: Path) -> list[str]:
    """The owners whose files a sync delivers to *root*, in the order they apply.

    The stack ([livery.workshop._extensions.stack_names][]): the base,
    then every extension of the installed set in composition order,
    which holds every extension a package's set holds.
    """
    return list(stack_names(root))


def shipped(root: Path) -> tuple[list[Fragment], list[str]]:
    """Every listed extension's shipped files as fragments, and the order they apply.

    The order is the delivery's
    ([livery.workshop._shipped_files.delivery_order][]). A file under
    `content/for/<target>/` comes while the extension's `[for.<target>]`
    table applies, named `for/<target>/<path>` among the extension's
    fragments, after its own.
    """
    order = delivery_order(root)
    contributes = contributing(root)
    fragments: list[Fragment] = []
    for extension in order:
        content = extension_content(extension)
        if content is None:
            continue
        trees = [(content, "")] + [
            (content / FOR_CONTENT / target, f"{FOR_CONTENT}/{target}/")
            for target in contributes.get(extension, ())
        ]
        for tree, named in trees:
            for kind, prefix in ((ROOT_CONTENT, ""), (PACKAGE_CONTENT, PACKAGE_PREFIX)):
                base = tree / kind
                if not base.is_dir():
                    continue
                for source in sorted(base.rglob("*")):
                    if not source.is_file() or "__pycache__" in source.parts:
                        continue
                    relative = (
                        source.relative_to(base).as_posix().removesuffix(TEMPLATE)
                    )
                    fragments.append(
                        Fragment(
                            extension,
                            f"{named}{relative}",
                            f"{prefix}{relative}",
                            source=source,
                        )
                    )
    fragments = declared(fragments, order)
    fragments += _editor_contributions(order, root)
    from livery.workshop._identity import is_born

    if not is_born(root):
        # The project file renders from the contract's identity: the
        # name, the namespace, the authors. A workspace whose contract
        # names no project composes none.
        fragments = [f for f in fragments if f.target != PROJECT_FILE]
    return fragments, order


def declared(
    fragments: list[Fragment], order: list[str], *, seeds: bool = False
) -> list[Fragment]:
    """*fragments* with each listed extension's `[replaces]` and `[deletes]` applied.

    A replacement marks the declaring extension's own fragment of the
    lower one's name; a deletion adds a fragment that only deletes. The
    lower fragment is the base's or that of an extension the declaring
    one requires, transitively, so it applies first in *order*, which is
    composition order. The engine's resolution then checks that the
    lower fragment exists. Seed declarations (names under `seeds/`)
    apply with *seeds* set, every other one without.

    Raises:
        Failed: for a replacement whose extension ships no file of that
            name, and for a replacement or a deletion of a file of an
            extension the declaring one does not require.
    """
    from livery.workshop._composition import closure
    from livery.workshop._extensions import installed_declaration
    from livery.workshop._seeds import SEEDS

    by_ref = {fragment.ref: index for index, fragment in enumerate(fragments)}
    result = list(fragments)
    for extension in order:
        found = None if extension == SELF else declaration(extension)
        if found is None:
            continue
        reached = {SELF, *closure(found.requires, installed_declaration)}
        for verb, held in (("replaces", found.replaces), ("deletes", found.deletes)):
            for ref in held:
                owner = ref.partition(":")[0]
                named = ref.partition(":")[2].startswith(f"{SEEDS}/") == seeds
                if named and owner not in reached:
                    fail(
                        f"{extension} {verb} {ref} in its extension.toml, and does"
                        f" not require {owner}; add {owner} to its [extension]"
                        " requires"
                    )
        for ref, reason in found.deletes.items():
            if ref.partition(":")[2].startswith(f"{SEEDS}/") == seeds:
                result.append(
                    Fragment(
                        extension, f"deletes {ref}", "", deletes=ref, reason=reason
                    )
                )
        for ref, reason in found.replaces.items():
            name = ref.partition(":")[2]
            if name.startswith(f"{SEEDS}/") != seeds:
                continue
            index = by_ref.get(f"{extension}:{name}")
            if index is None:
                fail(
                    f"{extension} replaces {ref} in its extension.toml and ships no"
                    f" {name} of its own to take its place"
                )
            result[index] = replace(result[index], replaces=ref, reason=reason)
    return result


def _editor_contributions(order: list[str], root: Path) -> list[Fragment]:
    """What the steps and the listed extensions say to the editor, as contributed data.

    A step's settings land in `.vscode/settings.json`, a fragment of
    its own, so a step that is unregistered takes its lines with it;
    each listed extension's answer to the ``editor-extensions`` query
    lands in `.vscode/extensions.json`, so an extension taken off the
    list takes its recommendations with it.
    """
    from livery.workshop._queries import EDITOR_EXTENSIONS, answers
    from livery.workshop._steps import steps_by_name

    found: list[Fragment] = []
    for extension, ids in answers(root, EDITOR_EXTENSIONS).items():
        if extension in order:
            found.append(
                Fragment(
                    extension,
                    f"{extension} recommendations",
                    ".vscode/extensions.json",
                    json.dumps({"recommendations": list(ids)}),
                    contributes=True,
                )
            )
    for name, record in sorted(steps_by_name().items()):
        if record.extension not in order:
            continue
        for fragment in record.fragments:
            if fragment.file == PROJECT_FILE:
                found.append(
                    Fragment(
                        record.extension,
                        f"step {name} tables",
                        PROJECT_FILE,
                        fragment.text,
                    )
                )
            if fragment.file == ".vscode/settings.json":
                found.append(
                    Fragment(
                        record.extension,
                        f"step {name} settings",
                        fragment.file,
                        fragment.text,
                        contributes=True,
                    )
                )
    return found


def _data(root: Path) -> dict[str, Any]:
    """What every shipped template reads.

    The runner's name and the project's always; once the contract names
    the project, also its identity and everything the workspace's state
    gives the render: the roster, the Python floor, the queries'
    answers, the extension requirements and the registry.
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

    A package's own files, its native tools' configuration, come from the
    first step by name whose fragment names an extension the package
    holds ([livery.workshop.extension_set][]).
    """
    from livery.workshop._composition import extension_set
    from livery.workshop._fragments import package_files, package_fragment
    from livery.workshop._packages import discover_packages
    from livery.workshop._steps import steps_by_name

    packages = discover_packages(root) if (root / "packages").is_dir() else ()
    fragments: list[Fragment] = []
    files = package_files()
    for package in packages:
        for file in files:
            found = package_fragment(extension_set(package), file)
            if found is None:
                continue
            text, step = found
            owner = steps_by_name()[step].extension
            if owner in order:
                fragments.append(
                    Fragment(
                        owner,
                        f"step {step} {package.path}",
                        f"{package.path}/{file}",
                        text,
                    )
                )
    from livery.workshop._identity import package_facts
    from livery.workshop._templates import _release_baseline, package_injections

    # Every package takes the base and every extension the workspace
    # lists, so each one's `content/package/` files (the changelog
    # extension's cliff.toml) reach every member, rendered with the
    # member's own facts.
    paths: dict[str, tuple[str, ...]] = {
        package.path: tuple(order) for package in packages
    }
    injected = package_injections(root) if packages else {}
    data: dict[str, dict[str, Any]] = {
        package.path: {
            **package_facts(root, package.directory),
            **injected,
            "release_baseline": _release_baseline(package.directory),
        }
        for package in packages
    }
    return fragments, paths, data


def outputs(root: Path) -> tuple[Output, ...]:
    """The shipped files as the listed extensions render them for *root*."""
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
    # Every output beside the composed ones: an output the list leaves
    # out is one the delivery withdraws.
    rest = _schema_outputs(root)
    taken = {output.path: output.owners[0] for output in (*planned, *rest)}
    return (*planned, *computed_outputs(root, order, taken), *rest)


def _schema_outputs(root: Path) -> list[Output]:
    """Each contract's composed JSON Schema, this checkout's own.

    The schema follows what is installed and listed, so it is written
    for this checkout alone, as the agent's fragments are, and the
    committed ``.taplo.toml`` points the editor at it.
    """
    from livery.workshop._schema import schema_files

    return [
        Output(path, body, (f"{SELF}:{path}",), local=True)
        for path, body in schema_files(root).items()
    ]


def deliver(root: Path, *, local_only: bool = False) -> list[str]:
    """Write the shipped files into *root*; one line per file that changed.

    A pass that changed anything is followed by one more: a rendered
    fragment may read a file the same pass wrote (the verbs fragment
    reads `tasks.py`), and the second pass renders it from what is now
    on disk, so a sync leaves a tree the next sync finds settled.
    *local_only* writes only the local outputs git does not track
    ([livery.workshop._fragment_engine.apply_untracked][]): what a
    commit holds is judged by the drift check, never rewritten.
    """
    from livery.workshop._packages import discover_packages

    if local_only:
        from livery.workshop._fragment_engine import apply_untracked

        lines = apply_untracked(root, outputs(root))
        if lines:
            lines += apply_untracked(root, outputs(root))
        return lines

    homes = (
        [package.path for package in discover_packages(root)]
        if (root / "packages").is_dir()
        else []
    )
    lines = apply(root, outputs(root), packages=homes)
    if lines:
        lines += apply(root, outputs(root), packages=homes)
    return lines


def deliver_project_file(root: Path) -> list[str]:
    """Write the workspace's project file alone, as the installed set renders it now.

    The dev group the file renders names the distribution of each
    extension of the installed set, one no installed distribution
    declares yet among them, so a ``uv sync`` after this write locks
    and installs that distribution through the file's
    ``[tool.uv.sources]`` and indexes. No other file is written or
    withdrawn: a whole delivery while a listed extension is not
    installed would withdraw that extension's files. A file edited
    here is kept and named, as a delivery keeps it, and a workspace
    whose contract names no project renders no project file.

    Returns:
        The line for the file when it changed or was kept; empty when
        it already held the render or none renders.
    """
    fragments, order = shipped(root)
    for output in plan(root, fragments, order, _data(root)):
        if output.path == PROJECT_FILE:
            return write_root_file(root, output)
    return []


def shipped_drift(root: Path) -> list[str]:
    """One line per shipped file whose committed bytes are not its render."""
    from livery.workshop._packages import discover_packages

    homes = (
        [package.path for package in discover_packages(root)]
        if (root / "packages").is_dir()
        else []
    )
    from livery.workshop._fragment_engine import tracked_local

    found = outputs(root)
    lines = [line.strip() for line in drift(root, found, packages=homes)]
    return lines + [
        f"{path}: written for this checkout alone, and git tracks it;"
        f" `git rm --cached {path}` stops tracking it, and its directory's"
        " .gitignore keeps it out"
        for path in tracked_local(root, found)
    ]


def relocate(root: Path) -> list[str]:
    """Move what a composed JSON file has and its render lacks into its region.

    VS Code writes a setting changed through its UI into `settings.json`
    just before the closing brace, and a recommendation into the list,
    both outside the repository's region. A top-level key the render does
    not produce, or an item a list holds that the render's list does not,
    is the repository's: it moves into the file's first region, and the
    file is written as the render with that region. A key the render does
    produce, set here to another value, is an override of an extension's
    or a step's setting; it refuses, naming the key and both values,
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


def _package_outputs(member: Path, extensions: tuple[str, ...]) -> tuple[Output, ...]:
    """The per-package files a package holding *extensions* renders, under *member*."""
    from livery.workshop._fragments import package_files, package_fragment
    from livery.workshop._steps import steps_by_name

    fragments: list[Fragment] = []
    owners: list[str] = []
    for file in package_files():
        found = package_fragment(extensions, file)
        if found is None:
            continue
        text, step = found
        owner = steps_by_name()[step].extension
        owners.append(owner)
        fragments.append(Fragment(owner, f"step {step}", file, text))
    return plan(member, fragments, list(dict.fromkeys(owners)), {})


def settle_package(member: Path, extensions: tuple[str, ...]) -> list[str]:
    """Compose the per-package files of a package holding *extensions*, at *member*.

    What a sync does for one package, without a workspace around it: the
    conformance kit and a birth's probe settle a package directory this
    way. *extensions* is the set the package holds, its list completed.
    """
    return apply(member, _package_outputs(member, extensions))


def judge_package(member: Path, extensions: tuple[str, ...]) -> list[str]:
    """The drift lines for the per-package files of a package holding *extensions*."""
    outputs = _package_outputs(member, extensions)
    return [line.strip() for line in drift(member, outputs)]


def computed_outputs(
    root: Path, order: list[str], taken: dict[str, str] | None = None
) -> list[Output]:
    """The files the listed extensions' code writes, in *order*.

    *order* is the delivery's
    ([livery.workshop._shipped_files.delivery_order][]), so an
    extension's files are written while the installed set holds it and
    withdrawn when no list holds it any more.
    Each ``[fragments."<target>"]`` table names a render the engine
    calls with *root*, and so does each ``[for.<target>.fragments]``
    table while its ``[for.<target>]`` table applies. For a file target
    the render answers the text, or nothing for no file; for a target
    ending in ``/`` it answers each file's path under the target, to its
    text or to the shipped file or directory the file links to. Every
    file is owned ``<extension>:<target>``, and a ``local`` table's files
    belong to this checkout alone.

    Raises:
        Failed: for a path two writers claim: another extension's
            render, or a file *taken* names by its owner.
    """
    held = dict(taken or {})
    outputs: list[Output] = []
    contributes = contributing(root)
    for extension in order:
        found = None if extension == SELF else declaration(extension)
        if found is None:
            continue
        renders = [*found.fragments]
        for target in contributes.get(extension, ()):
            renders += found.target_fragments.get(target, ())
        for fragment in renders:
            owner = f"{extension}:{fragment.target}"
            answer = fragment.render(root)
            written: list[tuple[str, object]]
            if fragment.target.endswith("/"):
                files = cast("dict[str, object]", dict(answer or {}))
                written = [(fragment.target + name, files[name]) for name in files]
            else:
                written = [(fragment.target, answer)] if answer else []
            for path, value in sorted(written, key=itemgetter(0)):
                if path in held:
                    fail(
                        f"{path} has one writer, and {held[path]} and {owner}"
                        " both write it"
                    )
                held[path] = owner
                if isinstance(value, Path):
                    outputs.append(
                        Output(path, b"", (owner,), link=value, local=fragment.local)
                    )
                else:
                    body = str(value).encode("utf-8")
                    outputs.append(Output(path, body, (owner,), local=fragment.local))
    return outputs
