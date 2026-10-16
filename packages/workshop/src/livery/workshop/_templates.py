"""The workspace's generated files: the drift check, the writer, new members.

The listed extensions' files are composed by the fragment engine
(`livery.workshop._shipped_files`); the CI files and the codeowners file
are generated from the contract. `drift_check` judges both against the
repository as the gate's `fm drift.check`; `apply_project` writes both,
and `fm sync` runs it, which is the recovery for drift. ``fm
new.package <name> <template>`` writes a member born from an installed
template into ``packages/<name>`` (`livery.workshop._seeds`), and a
fresh runner adds it to the project files.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any

import livery.footman as footman
from livery.footman import Arg, doc, fail, group, hidden, suggest
from livery.workshop._contract import load_contract
from livery.workshop._extensions import (
    stack_entries,
    workspace_root,
)
from livery.workshop._identity import default_description, identity, python_dirs
from livery.workshop._pythons import python_floor, runner_script

new = group(
    "new",
    help="Create a project from the extensions' seeds, or a package from a template",
)


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
    from livery.workshop._queries import EDITOR_EXTENSIONS, answer, workspace_answers

    injected: dict[str, Any] = {
        "runner_prog": footman.prog(),
        "python_floor": python_floor(root),
        # The workspace's own tests directory, which the checkers and
        # the test runner read only while it exists: a newborn has none.
        "root_tests": ["tests"] if (root / "tests").is_dir() else [],
        # Each python member's src and tests that hold a python file: a
        # checker such as mypy refuses a named directory with none, and
        # an extension's src may hold data alone.
        "python_dirs": python_dirs(root),
        # The workspace-level queries' answers, by name, each answered
        # when a template reads it: the dev group's tool lines, the docs
        # group's lines, pytest's addopts. A listed extension's answer
        # lands here, and an unlisted one's leaves with it.
        "queries": workspace_answers(root),
        "extension_imports": [import_path for import_path, _ in entries],
        # One line per distribution: a wheel may ship several extensions.
        "extension_requirements": _extension_requirements(entries, members),
        # The editor extension ids the listed extensions answer.
        "extensions": list(answer(root, EDITOR_EXTENSIONS)),
        **registry_injections(root),
    }
    injected["fragments"] = compose_fragments({**answers, **injected})
    return injected


def _extension_requirements(
    entries: tuple[tuple[str, str], ...], members: set[str]
) -> list[str]:
    """The dev group's line for each stack distribution that is not a member.

    One line per distribution, since a wheel may ship several
    extensions.
    """
    found = [dist for _name, dist in entries if _requirement_name(dist) not in members]
    return list(dict.fromkeys(found))


def compose_fragments(data: dict[str, Any]) -> dict[str, str]:
    """The step records' fragments composed per rendered file over *data*."""
    from livery.workshop._fragments import compose_project

    return compose_project(fragment_data(data))


def fragment_data(data: dict[str, Any]) -> dict[str, Any]:
    """*data* with what the step records' fragments read beside it.

    The roster is split the way the project file splits it, ``py`` for
    the members with a dev entry and ``native`` for the rest, and the
    per-file ignores the claims render are added for the sets of
    extensions the members hold, each entry's ``extensions``. A roster
    with no member renders the ignores of the
    workspace's own unit alone. The fragment engine reads the step
    records' fragments through this.
    """
    from livery.workshop._steps import per_file_ignores

    roster = [entry for entry in data.get("packages", []) if isinstance(entry, dict)]
    held = {
        tuple(str(name) for name in entry.get("extensions", ())) for entry in roster
    }
    return {
        **data,
        "py": [entry for entry in roster if entry.get("dev")],
        "native": [entry for entry in roster if not entry.get("dev")],
        # The per-file ignores the claims render, for the sets present.
        "per_file_ignores": per_file_ignores(sorted(held)),
    }


def package_injections(root: Path) -> dict[str, Any]:
    """The render-time values a package render takes from the contract.

    The forge facts feed the changelog's link bases; asking them per
    package would let one workspace's packages disagree about where
    they live.
    """
    contract = load_contract(root / "workshop.toml")
    forge_table = contract.get("forge") or {}

    return {
        "runner_prog": footman.prog(),
        "python_floor": python_floor(root),
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
    the baseline names the version it continues from, and a package
    template reads it as ``release_baseline`` to anchor the first
    release on it.
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


def drift_check(files: frozenset[str] | None = None) -> None:
    """Fail when a composed or generated file drifts from what writes it.

    The gate's drift check, `fm drift.check`. `fm sync` writes both
    kinds of file, so it is the recovery for every line this names.
    *files* keeps the findings to those root-relative files: the tree
    a change is measured from was judged already, so only a file the
    change touched can have drifted since, unless an input of every
    file changed, and then the caller passes None.
    """
    from livery.workshop._shipped_files import shipped_drift

    root = _root()
    drift = shipped_drift(root) + project_drift(root)
    if files is not None:
        drift = [line for line in drift if line.split(":", 1)[0] in files]
    if not drift:
        return
    fail(
        "committed files drift from what writes them:\n  "
        + "\n  ".join(drift)
        + f"\n  run `{footman.prog()} sync`, which writes them again"
    )


def template_names() -> list[str]:
    """The names of the templates the workspace offers, which completion offers.

    The base's, and those of the extensions the workspace's installed
    set holds.
    """
    from livery.workshop._seeds import installed_templates

    return list(installed_templates())


def _installed(root: Path) -> str:
    """The templates *root* offers, each on a line with its doc, for a refusal."""
    from livery.workshop._seeds import described, installed_templates

    return described(installed_templates(root))


@new.task(name="package")
def new_package(
    name: Annotated[str, doc("directory name under packages/ (e.g. scratch)")],
    template: Annotated[
        Arg[str],
        doc("the template the package is born from (python-library, ...)"),
        suggest(template_names, strict=False),
    ] = "",
    resume: Annotated[bool, hidden] = False,
) -> None:
    """Create a package from an installed template and wire it into the workspace.

    Writes the ``package-base`` seeds, then the files of the template
    and of each template it extends, the one extended first, into
    ``packages/<name>``, with the distribution named
    ``<namespace>-<name>`` and the contract listing the template's
    extensions. The installed templates are the base's and those of
    the extensions the workspace's installed set holds: the root list's
    set and every package's. Without a template it refuses, naming
    each installed one with its doc.

    A creation installs nothing. A template needs its own
    ``extensions``, those of each template it extends, and the
    extension that declares each template of that chain, the base
    aside. Before anything is written it refuses, in this order: a
    template no installed extension declares while the workspace's
    lists hold an extension that is not installed yet, naming each and
    ``fm sync``; a template no installed extension declares, naming the
    installed templates; a template that needs an extension the
    workspace's lists do not hold, naming each with its distribution,
    to list in ``[workspace] extensions`` before ``fm sync``; and a
    template that needs an extension the lists hold and no installed
    distribution declares yet, naming each and ``fm sync``.

    Then the runner the workspace's environment holds wires the member,
    run as ``new.package <name> --resume``: it writes the project files
    again so every per-package list picks the member up, and locks and
    syncs the environment and the tools. ``--resume`` wires a
    member that exists, which is also the recovery after a wiring that
    stopped part way. A member that does not hold python joins the
    roster without a dev-group entry: there is no distribution to
    install.
    """
    root = _root()
    if resume:
        wire_member(root, name)
        return
    prog = footman.prog()
    if not template:
        fail(
            f"name the template packages/{name} is born from:"
            f" `{prog} new.package {name} <template>`; the installed templates"
            f" are:\n{_installed(root)}"
        )
    render_member(root, name, template)
    runner = runner_script(root / ".venv")
    if not runner.is_file():
        fail(
            f"{runner} is missing: {root}'s environment has no {prog} to wire"
            f" packages/{name} with. `uv sync` in {root} installs it; then run"
            f" `{prog} new.package {name} --resume` there"
        )
    print(f"  packages/{name}: seeded; the wiring continues with {runner}")
    footman.run([str(runner), "new.package", name, "--resume"], cwd=root, capture=False)


def wire_member(root: Path, name: str) -> None:
    """Wire the package under ``packages/<name>`` into *root*.

    Writes the composed and generated files again, so every
    per-package list picks the member up, then locks and syncs the
    environment and the tools. ``new.package`` runs it in the runner
    the workspace's environment holds.

    Raises:
        Failed: when *name* breaks the package naming rule, or
            ``packages/<name>`` holds no package.
    """
    _check_member_name(name)
    if not (root / "packages" / name / "workshop.toml").is_file():
        fail(
            f"packages/{name} holds no package to wire:"
            f" `{footman.prog()} new.package {name}` creates one"
        )
    for changed in apply_project(root):
        print(f"  rendered: {changed}")
    from livery.workshop._tool_tasks import sync_tools
    from livery.workshop._uv import run_uv

    run_uv("lock", root=root)
    run_uv("sync", root=root)
    # A member's extensions bring tools of their own (a native
    # member's build tools and the steps that judge it), so the tool
    # lock moves with the member and the store supplies what it names:
    # the gate that follows finds them, as it does after a birth.
    sync_tools(root)
    print(f"  packages/{name}: seeded, wired, and installed")


def _check_member_name(name: str) -> None:
    """Refuse a package name ``packages/<name>`` cannot hold."""
    if not re.fullmatch(r"([a-z][a-z0-9-]*/)?[a-z][a-z0-9-]*", name):
        fail(
            f"package name {name!r}: use lowercase letters, digits, hyphens,"
            " and at most one group directory (extensions/widgets)"
        )


def render_member(root: Path, name: str, template: str) -> str:
    """Write a member born from *template* into ``packages/<name>``; the import path.

    Writes the ``package-base`` seeds and the files of the template's
    chain (`livery.workshop._seeds.create_package`), and the files the
    records compose for the member. The member's own contract is one of
    the template's files, and discovery finds the member through it,
    so the roster needs no write. The project files, the lock and the
    install are the caller's: `wire_member` runs them for one member,
    and a caller adding many members runs them once after the last.
    Refuses an existing directory, naming it, and a template the birth
    cannot take, before anything is written.
    """
    _check_member_name(name)
    destination = root / "packages" / name
    if destination.exists():
        fail(f"{destination} already exists")
    group = destination.parent
    if group.name != "packages" and (group / "workshop.toml").is_file():
        fail(f"{group} is a package: a group directory has no workshop.toml")
    facts = identity(root)
    namespace = str(facts.get("namespace_package", ""))
    prefix = namespace.replace(".", "-")
    flat = name.replace("/", "-")
    package_name = f"{prefix}-{flat}" if prefix else flat
    data: dict[str, Any] = {
        **facts,
        # The forge facts ride the contract: a package's changelog
        # links its own pull requests on the workspace's forge.
        **package_injections(root),
        "package_dir": name,
        "package_name": package_name,
        "package_description": default_description(
            package_name, str(facts.get("project_name", ""))
        ),
    }
    from livery.workshop._seeds import create_package, derived

    data.update(derived(data))
    create_package(root, destination, template, data)
    from livery.workshop._shipped_files import deliver

    deliver(root)
    return str(data["import_path"])
