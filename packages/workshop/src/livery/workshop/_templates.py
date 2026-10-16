"""The workspace's generated files: the drift check, the writer, new members.

The listed extensions' files are composed by the fragment engine
(`livery.workshop._shipped_files`); the CI files and the codeowners file
are generated from the contract. `drift_check` judges both against the
repository as the gate's `fm drift.check`; `apply_project` writes both,
and `fm sync` runs it, which is the recovery for drift.

Each installed template is a verb, ``fm new.package.<template> <name>
--<option>=<answer>``, made from its declaration by
`generate_template_verbs` when the plugin loads. It writes a member
born from the template into ``packages/<name>`` through
`render_member` (`livery.workshop._seeds`), the one function every
creation calls, and a fresh runner adds the member to the project
files.
"""

from __future__ import annotations

import inspect
import re
import weakref
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, cast

import livery.footman as footman
from livery.footman import Group, doc, fail, group
from livery.workshop._contract import load_contract
from livery.workshop._extensions import (
    stack_entries,
    workspace_root,
)
from livery.workshop._identity import default_description, identity, python_dirs
from livery.workshop._pythons import python_floor, runner_script

if TYPE_CHECKING:
    from livery.workshop._declaration import DeclaredTemplate, TemplateOption

#: What ``fm new`` does, and what ``fm new.package`` does.
NEW_HELP = "Create a project from the extensions' seeds, or a package from a template"
PACKAGE_HELP = "Create a package from an installed template: one task per template"

new = group("new", help=NEW_HELP)

#: The group of the templates' verbs: ``fm new.package.<template>``.
package = new.group("package", help=PACKAGE_HELP)


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


#: The package's name, which every template's verb takes first. Module
#: level, where footman reads a task's annotations.
_Name = Annotated[str, doc("directory name under packages/ (e.g. scratch)")]

#: Each verb the generator made, to what it was made from: the template
#: and the options its verb offers.
_MADE: weakref.WeakKeyDictionary[
    Callable[..., None], tuple[DeclaredTemplate, tuple[TemplateOption, ...]]
] = weakref.WeakKeyDictionary()


def generate_template_verbs(
    templates: Mapping[str, DeclaredTemplate], into: Group | None = None
) -> None:
    """Make a verb per template, ``fm new.package.<template>``, from *templates*.

    *templates* are the installed ones by name: the base's, and those
    of each extension of the installed set an installed distribution
    declares, as the mount read them. Each verb takes the package's
    name, then each option the template's chain offers as a keyword
    option (`livery.workshop._seeds.verb_options`): choices as a
    `Literal`, the doc as its help line, the default as its default,
    and an option without a default required. Nothing else is read, so
    the command line, its help and its completion follow from the
    declarations alone.

    Run again after the installed set changed, it makes the verb of a
    new template, remakes a verb whose template or options changed, and
    removes the verb of a template that is gone. A task at a template's
    address that the generator did not make is left alone.
    """
    from livery.footman import root_group
    from livery.workshop._seeds import verb_options

    parent = _package_group(into if into is not None else root_group)
    for key, task in list(parent.tasks.items()):
        if _made(task) is not None and key not in templates:
            del parent.tasks[key]
    for name, template in templates.items():
        options = verb_options(name, templates)
        existing = parent.tasks.get(name)
        if existing is not None:
            made = _made(existing)
            if made is None or made == (template, options):
                continue
            del parent.tasks[name]
        parent.task(name=name)(_template_verb(template, options))


def _package_group(target: Group) -> Group:
    """The ``new.package`` group under *target*, made when it is missing."""
    held = target.groups.get("new")
    if held is None:
        held = target.group("new", help=NEW_HELP)
    found = held.groups.get("package")
    if found is None:
        found = held.group("package", help=PACKAGE_HELP)
    return found


def _made(
    task: Callable[..., object],
) -> tuple[DeclaredTemplate, tuple[TemplateOption, ...]] | None:
    """What the generator made *task* from; None for a task it did not make."""
    return _MADE.get(inspect.unwrap(task))


def _template_verb(
    template: DeclaredTemplate, options: Sequence[TemplateOption]
) -> Callable[..., None]:
    """The verb that creates a package from *template*, offering *options*."""
    born = template.name

    def verb(name: str, **answers: object) -> None:
        _create(born, name, answers)

    parameters = [
        inspect.Parameter(
            "name", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=_Name
        ),
        *(_parameter(option) for option in options),
    ]
    # footman reads a verb's parameters through inspect.signature, which
    # answers with __signature__ when a function carries one: the options
    # are data, so the signature is built here.
    vars(verb)["__signature__"] = inspect.Signature(parameters, return_annotation=None)
    verb.__doc__ = _verb_doc(template)
    _MADE[verb] = (template, tuple(options))
    return verb


def _parameter(option: TemplateOption) -> inspect.Parameter:
    """*option* as a keyword option of its template's verb."""
    from livery.workshop._declaration import OPTION_TYPES

    answer: Any = OPTION_TYPES[option.type]
    if option.choices:
        # The choices are data, so the Literal is built at run time.
        answer = cast("Any", Literal)[option.choices]
    return inspect.Parameter(
        option.variable,
        inspect.Parameter.KEYWORD_ONLY,
        default=inspect.Parameter.empty if option.default is None else option.default,
        annotation=cast("Any", Annotated)[answer, doc(option.doc)],
    )


def _verb_doc(template: DeclaredTemplate) -> str:
    """The help of *template*'s verb: its doc first, then what a creation does.

    The first line is the summary listings show, so it stays one line;
    the paragraphs after it are wrapped as a docstring is, since footman
    prints a verb's help as written.
    """
    import textwrap

    extends = (
        f", after the files of {template.extends} and each template it extends"
        if template.extends
        else ""
    )
    paragraphs = (
        "Writes the package-base seeds, then the files of the template"
        f" {template.name}{extends}, into packages/<name>, with the"
        " distribution named <namespace>-<name> and the contract listing"
        " the template's extensions. Each option's answer is checked"
        " before anything is written, and renders the files; nothing"
        " stores it.",
        "A creation installs nothing: a template that needs an extension"
        " the workspace's lists do not hold, or that no installed"
        " distribution declares yet, refuses before anything is written,"
        " naming each. Then the runner the workspace's environment holds"
        " wires the member, run as `new.wire <name>`: it writes the"
        " project files again, and locks and syncs the environment and"
        " the tools.",
    )
    wrapped = (
        textwrap.fill(paragraph, 72, break_on_hyphens=False) for paragraph in paragraphs
    )
    return "\n\n".join((f"Create {template.doc}.", *wrapped))


def _create(template: str, name: str, answers: Mapping[str, object]) -> None:
    """Create ``packages/<name>`` from *template* with *answers*, then wire it.

    The body of every template's verb: the render (`render_member`),
    then the wiring in the runner the workspace's environment holds, a
    fresh process whose mount holds the member's extensions.
    """
    root = _root()
    render_member(root, name, template, answers)
    prog = footman.prog()
    runner = runner_script(root / ".venv")
    if not runner.is_file():
        fail(
            f"{runner} is missing: {root}'s environment has no {prog} to wire"
            f" packages/{name} with. `uv sync` in {root} installs it; then run"
            f" `{prog} new.wire {name}` there"
        )
    print(f"  packages/{name}: seeded; the wiring continues with {runner}")
    footman.run([str(runner), "new.wire", name], cwd=root, capture=False)


@new.task(name="wire", hidden=True)
def new_wire(name: _Name) -> None:
    """Wire the package under packages/<name> into the workspace.

    Writes the project files again, so every per-package list picks the
    member up, then locks and syncs the environment and the tools. A
    template's verb runs it in the runner the workspace's environment
    holds once the member is written, and it is the recovery after a
    wiring that stopped part way. A member that does not hold python
    joins the roster without a dev-group entry: there is no
    distribution to install.
    """
    wire_member(_root(), name)


def wire_member(root: Path, name: str) -> None:
    """Wire the package under ``packages/<name>`` into *root*.

    Writes the composed and generated files again, so every
    per-package list picks the member up, then locks and syncs the
    environment and the tools. ``fm new.wire`` runs it in the runner
    the workspace's environment holds, after a template's verb wrote the
    member.

    Raises:
        Failed: when *name* breaks the package naming rule, or
            ``packages/<name>`` holds no package.
    """
    _check_member_name(name)
    if not (root / "packages" / name / "workshop.toml").is_file():
        fail(
            f"packages/{name} holds no package to wire:"
            f" `{footman.prog()} new.package.<template> {name}` creates one"
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


def render_member(
    root: Path,
    name: str,
    template: str,
    answers: Mapping[str, object] | None = None,
) -> str:
    """Write a member born from *template* into ``packages/<name>``; the import path.

    The one function behind every creation: each template's verb, ``fm
    ci.scale``, and ``fm new.project --extension`` call it. Writes the
    ``package-base`` seeds and the files of the template's chain
    (`livery.workshop._seeds.create_package`), rendered with *answers*,
    each answer to one of the chain's options by its variable, and the
    files the records compose for the member. The member's own contract
    is one of the template's files, and discovery finds the member
    through it, so the roster needs no write. The project files, the
    lock and the install are the caller's: `wire_member` runs them for
    one member, and a caller adding many members runs them once after
    the last. Refuses an existing directory, naming it, a template the
    birth cannot take, and an answer the template's options refuse,
    before anything is written.
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
    create_package(root, destination, template, data, answers)
    from livery.workshop._shipped_files import deliver

    deliver(root)
    return str(data["import_path"])
