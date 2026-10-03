"""The workspace's generated files: the drift check, the applier, new members.

The listed extensions' files are composed by the fragment engine
(`livery.workshop._shipped_files`); the CI files and the codeowners file
are generated from the contract. ``fm template.check`` judges both
against the repository and is part of ``fm check``; ``fm template.apply``
writes both, which is the recovery procedure for drift. ``fm
new.package`` writes a member's seeds (`livery.workshop._seeds`) into
``packages/<name>`` and adds it to the project files.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any

import livery.footman.api as footman
from livery.footman.api import doc, fail, group
from livery.workshop._contract import load_contract
from livery.workshop._extensions import (
    stack_entries,
    workspace_root,
)
from livery.workshop._identity import identity
from livery.workshop._pythons import python_floor

template = group(
    "template", help="The composed and generated files, and their drift check"
)
new = group("new", help="Create a project or a package from the extensions' seeds")


def _root() -> Path:
    """The workspace root, or fail."""
    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    return root


def _requirement_name(spec: str) -> str:
    """The distribution a requirement spec names, extras and floors cut."""
    return re.split(r"[\[<>=!~; ]", spec.strip(), maxsplit=1)[0]


def registry_injections(root: Path) -> dict[str, str]:
    """The contract's python registry, as render inputs.

    Only the committed ``[registries]`` table feeds a render: the
    resolution ladder's environment rung is machine truth, and a
    rendered file must derive from the repository alone or the drift
    check would judge each machine differently. Nothing declared
    renders nothing, and uv resolves from the ecosystem default.
    """
    contract = load_contract(root / "workshop.toml")
    table = contract.get("registries") or {}
    entry = table.get("python") if isinstance(table, dict) else None
    url, prerelease = "", ""
    if isinstance(entry, str):
        url = entry
    elif isinstance(entry, dict):
        url = str(entry.get("url", ""))
        prerelease = str(entry.get("prerelease", ""))
    return {"python_registry": url, "python_prerelease": prerelease}


def render_injections(root: Path, answers: dict[str, Any]) -> dict[str, Any]:
    """The render-time values no answer stores.

    Identity is answered, configuration is declared: the runner's
    name belongs to the process, the Python floor to the root
    ``pyproject.toml``, the extensions and the registry to
    ``workshop.toml``. Every project render mixes these in, so the
    answers hold identity and the ``packages`` roster alone.
    """
    entries = stack_entries(root)
    if not entries:
        fail(
            "workshop.toml declares no [workspace] extensions: the render"
            " needs the stack (the base extension is livery.workshop)"
        )
    members = {
        _requirement_name(str(entry.get("dev", "")))
        for entry in answers.get("packages", [])
        if isinstance(entry, dict)
    }
    from livery.workshop._checks import editor_extensions
    from livery.workshop._docs_contract import docs_table
    from livery.workshop._slots import all_composed

    injected: dict[str, Any] = {
        "runner_prog": footman.prog(),
        "python_floor": python_floor(root),
        "docs_site_url": str(docs_table(root).get("site-url", "")),
        # The slots the check records fill: the dev group's tool lines,
        # pytest's addopts. An extension's contribution lands here, and a
        # withdrawn check takes its line with it.
        "slots": all_composed(),
        "extension_imports": [import_path for import_path, _ in entries],
        # One line per distribution: a wheel may ship several extensions.
        "extension_requirements": list(
            dict.fromkeys(
                dist for _, dist in entries if _requirement_name(dist) not in members
            )
        ),
        # The editor extension ids the registered checks carry.
        "extensions": list(editor_extensions()),
        **registry_injections(root),
    }
    injected["fragments"] = compose_fragments({**answers, **injected})
    return injected


def compose_fragments(data: dict[str, Any]) -> dict[str, str]:
    """The check records' fragments composed per rendered file over *data*."""
    from livery.workshop._fragments import compose_project

    return compose_project(fragment_data(data))


def fragment_data(data: dict[str, Any]) -> dict[str, Any]:
    """*data* with what the check records' fragments read beside it.

    The roster is split the way the project file splits it, ``py`` for
    the members with a dev entry and ``native`` for the rest, and the
    per-file ignores the claims render are added for the kinds present.
    The fragment engine reads the check tables through this.
    """
    from livery.workshop._checks import per_file_ignores
    from livery.workshop._kinds import kind_names, record_for_template
    from livery.workshop._slots import all_composed

    roster = [entry for entry in data.get("packages", []) if isinstance(entry, dict)]
    kinds = []
    for entry in roster:
        # A roster entry names its kind's record; a template name maps
        # to its record, and anything else is python.
        spelled = str(entry.get("kind", "python"))
        record = record_for_template(spelled)
        if spelled in kind_names():
            name = spelled
        else:
            name = record.name if record is not None else "python"
        if name not in kinds:
            kinds.append(name)
    return {
        # The slots come from the registry when the caller's data
        # carries none, as the render injection would supply them.
        "slots": all_composed(),
        **data,
        "py": [entry for entry in roster if entry.get("dev")],
        "native": [entry for entry in roster if not entry.get("dev")],
        # The per-file ignores the claims render, for the kinds present.
        "per_file_ignores": per_file_ignores(tuple(kinds) or ("python",)),
    }


def package_injections(root: Path) -> dict[str, Any]:
    """The render-time values a package render takes from the contract.

    The forge facts feed the changelog's link bases; asking them per
    package would let one workspace's packages disagree about where
    they live.
    """
    contract = load_contract(root / "workshop.toml")
    forge_table = contract.get("forge") or {}
    from livery.workshop._docs_contract import docs_table

    return {
        "runner_prog": footman.prog(),
        "python_floor": python_floor(root),
        "docs_site_url": str(docs_table(root).get("site-url", "")),
        "forge_kind": str(forge_table.get("kind", "github")),
        "forge_owner": str(forge_table.get("owner", "")),
        "forge_url": str(forge_table.get("url", "")),
        "project_name": identity(root)["project_name"],
    }


def _lf(data: bytes) -> bytes:
    """The bytes with LF endings, whatever the platform wrote.

    Git holds LF, and a file written on Windows may carry CRLF, so both
    sides normalise before any compare or write. Without this the
    Windows gate drifts on every file.
    """
    return data.replace(b"\r\n", b"\n")


def _member_directories(root: Path) -> list[Path]:
    """Each member package's directory, in path order: those with a contract."""
    from livery.workshop._packages import discover_packages

    if not (root / "packages").is_dir():
        return []
    return [package.directory for package in discover_packages(root)]


def project_drift(root: Path) -> list[str]:
    """The drift report: generated files (CI, codeowners) that disagree with *root*.

    Empty when every generated file matches the repository byte for
    byte.
    """
    drift: list[str] = []
    from livery.workshop._ci_generate import generated_files
    from livery.workshop._governance import codeowners_file

    generated = dict(generated_files(root))
    rendered_owners = codeowners_file(root)
    if rendered_owners is not None:
        from livery.workshop._provenance import generated_header

        generated[root / rendered_owners.path] = (
            generated_header("#") + rendered_owners.content
        )
    for path, content in generated.items():
        relative_generated = path.relative_to(root).as_posix()
        if not path.is_file():
            drift.append(
                f"{relative_generated}: generated, but missing from the repository"
            )
        elif _lf(path.read_bytes()) != _lf(content.encode()):
            drift.append(f"{relative_generated}: differs from its generation")
    from livery.workshop._ci_generate import retired_files

    for path in retired_files(root):
        drift.append(
            f"{path.relative_to(root).as_posix()}: retired, still present;"
            " the apply deletes it"
        )
    return drift


def _release_baseline(directory: Path) -> str:
    """The [release] baseline a package's contract declares, or empty.

    A migrated distribution's version line predates this workspace;
    the baseline names the version it continues from, and the cliff
    render anchors the first release's derivation on it.
    """
    contract = directory / "workshop.toml"
    if not contract.is_file():
        return ""
    data = load_contract(contract)
    return str((data.get("release") or {}).get("baseline", ""))


def apply_project(root: Path) -> list[str]:
    """Write the composed and generated files over *root*; the files that changed.

    The fragment engine composes the project file, the editor files, the
    ignore and attribute lines, so a new member reaches `pyproject.toml`
    here; the CI files and the codeowners file are generated after.
    """
    changed: list[str] = []
    from livery.workshop._shipped_files import deliver

    for line in deliver(root):
        verb, _, rest = line.strip().partition(" ")
        path = rest.partition(":")[0].split(" ")[0]
        if path.startswith((".workshop/", ".claude/")):
            continue  # this checkout's own, not the project's render
        if verb in ("wrote", "updated", "removed"):
            changed.append(path)
        else:
            print(line)
    changed.extend(apply_generated(root))
    return changed


def apply_generated(root: Path) -> list[str]:
    """Write the emitted artifacts (CI files, codeowners); what changed.

    The fragment engine's delivery and this together are apply_project,
    which an update runs, so an instance's generated workflows move
    with its workshop.
    """
    changed: list[str] = []
    from livery.workshop._ci_generate import generated_files
    from livery.workshop._governance import codeowners_file

    generated = dict(generated_files(root))
    rendered_owners = codeowners_file(root)
    if rendered_owners is not None:
        from livery.workshop._provenance import generated_header

        generated[root / rendered_owners.path] = (
            generated_header("#") + rendered_owners.content
        )
    for path, content in generated.items():
        body = _lf(content.encode())
        if not path.is_file() or _lf(path.read_bytes()) != body:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            changed.append(path.relative_to(root).as_posix())
    from livery.workshop._ci_generate import retired_files

    for path in retired_files(root):
        path.unlink()
        changed.append(f"{path.relative_to(root).as_posix()} (retired)")
    return changed


@template.task(name="check")
def template_check() -> None:
    """Fail when a composed or generated file drifts from what writes it.

    Part of the gate.
    """
    from livery.workshop._shipped_files import shipped_drift

    root = _root()
    drift = shipped_drift(root) + project_drift(root)
    if not drift:
        return
    fail(
        "committed files drift from their generation:\n  "
        + "\n  ".join(drift)
        + f"\n  a composed file: run `{footman.prog()} sync`; a generated one:"
        f" run `{footman.prog()} template.apply`"
    )


@template.task(name="apply")
def template_apply() -> None:
    """Write the composed and generated files over the workspace.

    The recovery procedure for drift. Seeds are never rewritten.
    Idempotent: a clean tree changes nothing.
    """
    root = _root()
    changed = apply_project(root)
    for name in changed:
        print(f"  rendered: {name}")
    if not changed:
        print("  everything already matches what writes it")


@new.task(name="package")
def new_package(
    name: Annotated[str, doc("directory name under packages/ (e.g. scratch)")],
    kind: Annotated[
        str, doc("seed tree (package-python, package-cpp-conan, ...)")
    ] = "package-python",
) -> None:
    """Create a package from *kind*'s seeds and wire it into the workspace.

    Writes the seeds of the kind's chain into ``packages/<name>``, with
    the distribution named ``<namespace>-<name>``, writes the project
    files again so every per-package list picks the member up, and
    locks the environment again. A non-python kind joins the roster
    without a dev-group entry: there is no distribution to install.
    """
    wire_package(_root(), name, kind=kind)


def wire_package(root: Path, name: str, *, kind: str = "package-python") -> str:
    """Create one *kind* package in *root* and wire it; the import path.

    The shared core of ``new.package`` and the birth verb's extension
    arm: `render_member`, then the project re-apply, lock and sync.
    Idempotent by refusal: an existing directory is named, never
    overwritten.
    """
    import_path = render_member(root, name, kind=kind)
    for changed in apply_project(root):
        print(f"  rendered: {changed}")
    from livery.workshop._tool_tasks import sync_tools
    from livery.workshop._uv import run_uv

    run_uv("lock", root=root)
    run_uv("sync", root=root)
    # A kind brings tools of its own (a native kind's build tools and
    # the checks that judge it), so the tool lock moves with the
    # member and the store supplies what it names: the gate that
    # follows finds them, as it does after a birth.
    sync_tools(root)
    print(f"  packages/{name}: seeded, wired, and installed")
    return import_path


def render_member(root: Path, name: str, *, kind: str = "package-python") -> str:
    """Write one *kind* package's seeds into *root*; the import path.

    Writes the seeds of the kind's chain (livery.workshop._seeds.create)
    and the files the records compose for it. The member's own contract
    is one of the seeds, and discovery finds the member through it, so
    the roster needs no write. The project files, the lock and the
    install are the caller's: `wire_package` runs them for one member,
    and a caller adding many members runs them once after the last.
    Refuses an existing directory, naming it.
    """
    if not re.fullmatch(r"[a-z][a-z0-9-]*", name):
        fail(f"package name {name!r}: use lowercase letters, digits, hyphens")
    destination = root / "packages" / name
    if destination.exists():
        fail(f"{destination} already exists")
    facts = identity(root)
    namespace = str(facts.get("namespace_package", ""))
    prefix = namespace.replace(".", "-")
    package_name = f"{prefix}-{name}" if prefix else name
    data: dict[str, Any] = {
        **facts,
        # The forge facts ride the contract: a package's changelog
        # links its own pull requests on the workspace's forge.
        **package_injections(root),
        "package_dir": name,
        "package_name": package_name,
        "package_description": (
            f"{package_name}: a {facts.get('project_name', '')} workspace package."
        ),
    }
    from livery.workshop._kinds import template_chain
    from livery.workshop._seeds import create, derived

    data.update(derived(data))
    create(root, destination, template_chain(kind), data)
    from livery.workshop._shipped_files import deliver

    deliver(root)
    return str(data["import_path"])
