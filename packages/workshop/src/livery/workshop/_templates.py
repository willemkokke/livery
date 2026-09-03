"""The template channel: the render gate, the applier, the generator.

The template source lives in the workspace's ``templates/`` directory:
one copier template, two kinds (``project`` for the workspace root,
``package-python`` for one package), rendered from the adopted
answers file at the workspace root.

``fm template.check`` renders the ``project`` kind into a scratch
directory and compares every rendered file byte for byte against the
repository, failing on any drift; it is part of ``fm check``, and
does nothing in a workspace without a ``templates/`` directory.
``fm template.apply`` writes the same render over the repository,
which is the recovery procedure for drift. ``fm new.package`` renders
the ``package-python`` kind into ``packages/<name>`` and wires the
member into the workspace by appending it to the answers file and
re-applying the ``project`` render.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated, Any

import footman
import toolroom
import yaml
from footman import doc, fail, group

from livery.workshop._layers import layer_entries, workspace_root
from livery.workshop._materialise import write_lf
from livery.workshop._pythons import python_floor

template = group("template", help="The template source and its render gate")
new = group("new", help="Render new pieces from the template source")

_ANSWERS = ".copier-answers.yml"

#: Where instances take their templates from when the contract is
#: silent: the published artifact repository, readable anonymously.
DEFAULT_TEMPLATE_SOURCE = "https://github.com/willemkokke/workshop-templates"


def template_source(root: Path) -> str:
    """The workspace's declared template source.

    ``[workspace] templates`` in ``workshop.toml``: a directory relative
    to the root (the monorepo names the base layer's own tree under
    ``packages/workshop``), or a git URL (a fork, at its own risk).
    Silent means the published artifact repository.
    """
    import tomllib

    contract = tomllib.loads((root / "workshop.toml").read_text("utf-8"))
    workspace = contract.get("workspace") or {}
    return str(workspace.get("templates", "")) or DEFAULT_TEMPLATE_SOURCE


def local_template_dir(root: Path) -> Path | None:
    """The template source as a local directory, or None when remote."""
    source = template_source(root)
    if "://" in source or source.startswith("git@"):
        return None
    directory = root / source
    return directory if directory.is_dir() else None


def redacted_source(source: str) -> str:
    """*source* with URL credentials stripped, for anything displayed.

    A contract may carry a credentialled URL (it should not: tokens
    are environment facts), and the rendered headers, refusals, and
    labels must never repeat the secret. The working value the
    machinery clones with is untouched.
    """
    return re.sub(r"^(\w+(?:\+\w+)?://)[^/@]+@", r"\1", source)


def template_ref(root: Path) -> str:
    """The tag a remote template source renders at.

    The publishing layer's installed version: the base layer
    publishes the one artifact repository today, so the ref is the
    base distribution's version, ``v`` prefixed. A composed artifact
    (a layer home's, once one exists) extends this to the layer
    whose home publishes the contract's source; "topmost layer"
    would be wrong, because a layer may own no templates at all.
    """
    from importlib.metadata import PackageNotFoundError, version

    entries = layer_entries(root)
    if not entries:
        fail(
            "workshop.toml declares no [workspace] layers: the template"
            " ref is the publishing layer's version and there is none"
        )
    _, base_dist = entries[0]
    try:
        return "v" + version(base_dist)
    except PackageNotFoundError:
        fail(
            f"the base layer's distribution {base_dist} is not"
            " installed, so the template ref is unknowable: `uv sync`"
            " installs the dev group"
        )


def resolve_source(root: Path) -> tuple[str, str | None]:
    """The render source and ref; every channel verb resolves through this.

    A local directory renders the working tree directly, ref-less:
    the monorepo edits its templates at HEAD (contract 12). A git
    URL renders at livery.workshop._templates.template_ref, the
    artifact tag the installed workshop shipped with. A declared
    local directory that does not exist is a taught refusal, never
    a guess.
    """
    source = template_source(root)
    if "://" not in source and not source.startswith("git@"):
        directory = root / source
        if directory.is_dir():
            return str(directory), None
        fail(
            f"[workspace] templates names {source!r} and no such"
            " directory exists here: create the checkout, or point the"
            " contract at a git URL"
        )
    return source, template_ref(root)


def render_source(root: Path) -> tuple[str, str | None, dict[str, str]]:
    """The render's source, its ref, and each file's owning layer.

    A self-hosting layer home composes: a workspace layer above the
    base that ships a template tree turns the source into the stack,
    composed bottom to top into ``.workshop/composed-templates``,
    regenerated on every call so the home's gate judges the local
    overlay at HEAD, never the released artifact. Everything else is
    livery.workshop._templates.resolve_source unchanged with no
    owners: a child consumes its parent's composed artifact and
    never composes at update time (the answers anchor exactly one
    source).
    """
    import shutil

    from livery.workshop._compose import compose_source, layer_template_tree
    from livery.workshop._layers import layer_entries

    def _member_overlay() -> bool:
        for layer, _dist in layer_entries(root)[1:]:
            tree = layer_template_tree(root, layer)
            if tree is not None and tree.is_relative_to(root):
                return True
        return False

    if _member_overlay():
        destination = root / ".workshop" / "composed-templates"
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True)
        composed = compose_source(root, destination)
        return str(composed.path), None, composed.owners
    source, ref = resolve_source(root)
    return source, ref, {}


def _root() -> Path:
    """The workspace root, or fail."""
    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    return root


def read_answers(path: Path) -> dict[str, Any]:
    """The adopted answers at *path*, copier's own bookkeeping stripped."""
    if not path.is_file():
        fail(f"no answers file at {path}: adopt one before rendering")
    answers = yaml.safe_load(path.read_text("utf-8")) or {}
    if not isinstance(answers, dict):
        fail(f"{path} is not a mapping")
    return {key: value for key, value in answers.items() if not key.startswith("_")}


def _requirement_name(spec: str) -> str:
    """The distribution a requirement spec names, extras and floors cut."""
    return re.split(r"[\[<>=!~; ]", spec.strip(), maxsplit=1)[0]


def render_injections(root: Path, answers: dict[str, Any]) -> dict[str, Any]:
    """The render-time values no answer stores.

    Identity is answered, configuration is declared: the runner's
    name belongs to the process, the Python floor to the root
    ``pyproject.toml``, and the layers to ``workshop.toml``. Every
    project render mixes these in, so the answers hold identity and
    the ``packages`` roster alone.
    """
    entries = layer_entries(root)
    if not entries:
        fail(
            "workshop.toml declares no [workspace] layers: the render"
            " needs the stack (the base layer is livery.workshop)"
        )
    members = {
        _requirement_name(str(entry.get("dev", "")))
        for entry in answers.get("packages", [])
        if isinstance(entry, dict)
    }
    return {
        "runner_prog": footman.prog(),
        "python_floor": python_floor(root),
        "template_source_label": redacted_source(template_source(root)),
        "layer_imports": [import_path for import_path, _ in entries],
        "layer_requirements": [
            dist for _, dist in entries if _requirement_name(dist) not in members
        ],
    }


def package_injections(root: Path) -> dict[str, Any]:
    """The render-time values a package render takes from the contract.

    The forge facts feed the changelog's link bases; asking them per
    package would let one workspace's packages disagree about where
    they live.
    """
    import tomllib

    contract = tomllib.loads((root / "workshop.toml").read_text("utf-8"))
    forge_table = contract.get("forge") or {}
    root_answers = read_answers(root / _ANSWERS)
    return {
        "runner_prog": footman.prog(),
        "python_floor": python_floor(root),
        "template_source_label": redacted_source(template_source(root)),
        "forge_kind": str(forge_table.get("kind", "github")),
        "forge_owner": str(forge_table.get("owner", "")),
        "forge_url": str(forge_table.get("url", "")),
        "project_name": str(root_answers.get("project_name", "")),
    }


def render(
    template_dir: Path | str,
    destination: Path,
    data: dict[str, Any],
    *,
    ref: str | None = None,
) -> None:
    """Render *template_dir* into *destination* with *data*, no prompts.

    A local directory renders the working tree: uncommitted template
    edits render too, which is what lets the gate judge a change
    before its commit. A git source renders at *ref*, the artifact
    tag. Runs copier in a child process because it chdirs while
    rendering, which a parallel task must never do to the one real
    directory. The templates carry their own provenance headers,
    parameterised by ``template_source_label``: injecting them after
    the render would make every managed file read as locally
    modified to ``copier update``, whose merge then drops real
    template changes.
    """
    if ref is not None:
        _probe_ref(str(template_dir), ref)
    with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as handle:
        yaml.safe_dump(data, handle)
        data_file = handle.name
    pinned = ["--vcs-ref", ref] if ref else []
    try:
        result = toolroom.copier(
            "copy",
            "--defaults",
            "--trust",
            "--overwrite",
            "--quiet",
            *pinned,
            "--data-file",
            data_file,
            str(template_dir),
            str(destination),
        )
    finally:
        Path(data_file).unlink(missing_ok=True)
    if result.code != 0:
        _taught_render_failure(str(template_dir), ref, data, result)


def _probe_ref(source: str, ref: str) -> None:
    """Refuse before cloning when *source* lacks *ref*.

    copier falls back to rendering HEAD when a requested ref does
    not exist, which would silently hand an instance templates its
    workshop never shipped; the probe turns the missing tag into a
    taught refusal instead.
    """
    # copier spells an explicit git source with a `git+` prefix; git
    # itself does not understand it.
    listing = subprocess.run(
        ["git", "ls-remote", source.removeprefix("git+"), f"refs/tags/{ref}"],
        capture_output=True,
        text=True,
        check=False,
        # ls-remote never reads the working directory; the explicit
        # value satisfies the deliberate-spawn rule and always exists.
        cwd=tempfile.gettempdir(),
    )
    shown = redacted_source(source)
    if listing.returncode != 0:
        fail(
            f"cannot reach the template source {shown} (wanted {ref}):"
            " check the network, or point [workspace] templates at a"
            f" local checkout.\n{listing.stderr}"
        )
    if not listing.stdout.strip():
        fail(
            f"the template source {shown} has no {ref} tag: a workshop"
            " release publishes it. Update the workspace to a released"
            " workshop, or point [workspace] templates at a source that"
            f" carries {ref}."
        )


def _taught_render_failure(
    source: str, ref: str | None, data: dict[str, Any], result: Any
) -> None:
    """Fail with what stopped the render and what to do about it.

    The raw copier output rides along verbatim: a failure reason is
    never summarised into a boolean.
    """
    output = f"{result.stdout}{result.stderr}"
    lowered = output.lower()
    kind = str(data.get("kind", "project"))
    source = redacted_source(source)
    if (
        ref
        and ("revision" in lowered or "reference" in lowered)
        and (
            "not found" in lowered or "unknown" in lowered or "did not match" in lowered
        )
    ):
        fail(
            f"the template source {source} has no {ref} tag: a workshop"
            " release publishes it. Update the workspace to a released"
            " workshop, or point [workspace] templates at a source that"
            f" carries {ref}.\n{output}"
        )
    if (
        "could not resolve host" in lowered
        or "unable to access" in lowered
        or "failed to connect" in lowered
        or "connection refused" in lowered
        or "operation timed out" in lowered
    ):
        wanted = f" (wanted {ref})" if ref else ""
        fail(
            f"cannot reach the template source {source}{wanted}: check"
            " the network, or point [workspace] templates at a local"
            f" checkout.\n{output}"
        )
    if "subdirectory" in lowered or "invalid choice" in lowered or "choice" in lowered:
        fail(
            f"the template source {source} does not carry the {kind}"
            " kind: point [workspace] templates at a source that ships"
            f" it, or render a kind the source has.\n{output}"
        )
    fail(f"copier exited {result.code}:\n{output}")


def _lf(data: bytes) -> bytes:
    """The bytes with LF endings, whatever the platform wrote.

    The channel is LF end to end: git holds LF, and copier writes the
    platform's endings, so both sides normalise before any compare or
    write. Without this the Windows gate drifts on every file.
    """
    return data.replace(b"\r\n", b"\n")


def rendered_files(destination: Path) -> list[Path]:
    """Every rendered file, the answers file excluded.

    The answers file records provenance, not content, so the drift
    comparison never judges it.
    """
    return sorted(
        path
        for path in destination.rglob("*")
        if path.is_file() and path.name != _ANSWERS
    )


def project_drift(root: Path) -> list[str]:
    """The drift report: rendered files that disagree with *root*.

    Empty when every rendered file matches the repository byte for
    byte.
    """
    answers = read_answers(root / _ANSWERS)
    data = {**answers, **render_injections(root, answers)}
    if local_template_dir(root) is None:
        fail("no local template source: the render gate needs one")
    source, _ref, owners = render_source(root)
    drift = []
    with tempfile.TemporaryDirectory() as scratch:
        render(source, Path(scratch), data)
        for rendered in rendered_files(Path(scratch)):
            relative = rendered.relative_to(scratch)
            if relative.as_posix() in PROJECT_SEEDS:
                continue
            committed = root / relative
            # The owners map keys the composed tree: kind-prefixed,
            # usually with the template suffix.
            owner = owners.get(f"project/{relative}.jinja") or owners.get(
                f"project/{relative}"
            )
            named = f" (the {owner} layer owns it)" if owner else ""
            if not committed.is_file():
                drift.append(
                    f"{relative}: rendered, but missing from the repository{named}"
                )
            elif _lf(committed.read_bytes()) != _lf(rendered.read_bytes()):
                drift.append(f"{relative}: differs from its render{named}")
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
        relative_generated = path.relative_to(root)
        if not path.is_file():
            drift.append(
                f"{relative_generated}: generated, but missing from the repository"
            )
        elif _lf(path.read_bytes()) != _lf(content.encode()):
            drift.append(f"{relative_generated}: differs from its generation")
    return drift


#: What a package's own render owns for good. Every other rendered
#: file is a package's seed, which its authors then write: comparing
#: those would report a living package as drift from its own birth.
PACKAGE_MANAGED = ("cliff.toml",)

#: The project render's seeds: born with the workspace, then the
#: workspace's own. Written only when missing; the drift gate never
#: judges them.
PROJECT_SEEDS = ("tests/test_workspace_contracts.py", "README.md", "LICENSE")


def package_drift(root: Path) -> list[str]:
    """The drift report for every package's managed rendered files.

    Only the files in
    livery.workshop._templates.PACKAGE_MANAGED are judged: a package
    is rendered once and then written, so its seeds are not the
    template's to keep. A package whose managed file is missing is
    named, which is what a package rendered before the file existed
    looks like.
    """
    if local_template_dir(root) is None:
        fail("no local template source: the render gate needs one")
    source, _ref, _owners = render_source(root)
    drift = []
    packages = root / "packages"
    for answers_path in sorted(packages.glob("*/.copier-answers.yml")):
        directory = answers_path.parent
        data = {**read_answers(answers_path), **package_injections(root)}
        with tempfile.TemporaryDirectory() as scratch:
            render(source, Path(scratch), data)
            for name in PACKAGE_MANAGED:
                rendered = Path(scratch) / name
                if not rendered.is_file():
                    continue
                committed = directory / name
                relative = committed.relative_to(root)
                if not committed.is_file():
                    drift.append(
                        f"{relative}: rendered, but missing from the repository"
                    )
                elif _lf(committed.read_bytes()) != _lf(rendered.read_bytes()):
                    drift.append(f"{relative}: differs from its render")
    return drift


def apply_project(root: Path) -> list[str]:
    """Write the ``project`` render over *root*; the files that changed."""
    answers = read_answers(root / _ANSWERS)
    data = {**answers, **render_injections(root, answers)}
    source, ref, _owners = render_source(root)
    changed = []
    with tempfile.TemporaryDirectory() as scratch:
        render(source, Path(scratch), data, ref=ref)
        for rendered in rendered_files(Path(scratch)):
            relative = rendered.relative_to(scratch)
            committed = root / relative
            if relative.as_posix() in PROJECT_SEEDS and committed.is_file():
                continue  # a seed is the workspace's own once it exists
            body = _lf(rendered.read_bytes())
            if not committed.is_file() or _lf(committed.read_bytes()) != body:
                committed.parent.mkdir(parents=True, exist_ok=True)
                committed.write_bytes(body)
                changed.append(str(relative))
    changed.extend(apply_generated(root))
    return changed


def apply_generated(root: Path) -> list[str]:
    """Write the emitted artifacts (CI files, codeowners); what changed.

    The render half and this half together are apply_project; the
    remote-source update runs copier itself and then this, so an
    instance's generated workflows move with its workshop too.
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
            changed.append(str(path.relative_to(root)))
    return changed


def apply_packages(root: Path) -> list[str]:
    """Write each package's managed rendered files; what changed.

    Only livery.workshop._templates.PACKAGE_MANAGED is written. A
    package's seeds belong to whoever has been writing them since it
    was born, and rewriting those would replace a living package's
    README, changelog, and sources with the template's stubs.
    """
    source, ref, _owners = render_source(root)
    changed = []
    for answers_path in sorted((root / "packages").glob("*/.copier-answers.yml")):
        directory = answers_path.parent
        data = {**read_answers(answers_path), **package_injections(root)}
        with tempfile.TemporaryDirectory() as scratch:
            render(source, Path(scratch), data, ref=ref)
            for name in PACKAGE_MANAGED:
                rendered = Path(scratch) / name
                if not rendered.is_file():
                    continue
                committed = directory / name
                body = _lf(rendered.read_bytes())
                if not committed.is_file() or _lf(committed.read_bytes()) != body:
                    committed.write_bytes(body)
                    changed.append(str(committed.relative_to(root)))
    return changed


@template.task(name="check")
def template_check() -> None:
    """Fail when a rendered file drifts from the template source.

    Part of the gate. A workspace without a ``templates/`` directory
    is an instance, not the template source, and passes vacuously.
    """
    root = _root()
    if local_template_dir(root) is None:
        return

    drift = project_drift(root) + package_drift(root)
    if drift:
        fail(
            "rendered files drift from templates/:\n  "
            + "\n  ".join(drift)
            + "\n  edit templates/ (never the rendered copy) and run"
            f" `{footman.prog()} template.apply`"
        )


@template.task(name="apply")
def template_apply() -> None:
    """Re-render the ``project`` kind and each package's managed files.

    The recovery procedure for drift, and the delivery step after a
    template edit. A package's seeds are never rewritten: only the
    files the template keeps owning
    (livery.workshop._templates.PACKAGE_MANAGED). Idempotent: a clean
    tree changes nothing.
    """
    root = _root()
    changed = apply_project(root) + apply_packages(root)
    for name in changed:
        print(f"  rendered: {name}")
    if not changed:
        print("  everything already matches the render")


@new.task(name="package")
def new_package(
    name: Annotated[str, doc("directory name under packages/ (e.g. scratch)")],
) -> None:
    """Render a new Python package and wire it into the workspace.

    Renders ``package-python`` into ``packages/<name>`` with the
    distribution named after the workspace convention
    (``livery-<name>``), appends the member to the root answers file,
    re-applies the ``project`` render so every per-package list picks
    it up, and re-locks the environment.
    """
    wire_package(_root(), name)


def wire_package(root: Path, name: str, *, kind: str = "package-python") -> str:
    """Render one *kind* package into *root* and wire it; the import path.

    The shared core of ``new.package`` and the birth verb's layer
    arm: render, receipt, roster, project re-apply, lock and sync.
    Idempotent by refusal: an existing directory is named, never
    overwritten.
    """
    template_dir, ref, _owners = render_source(root)
    if not re.fullmatch(r"[a-z][a-z0-9-]*", name):
        fail(f"package name {name!r}: use lowercase letters, digits, hyphens")
    destination = root / "packages" / name
    if destination.exists():
        fail(f"{destination} already exists")
    answers = read_answers(root / _ANSWERS)

    namespace = str(answers.get("namespace_package", ""))
    prefix = namespace.replace(".", "-")
    package_name = f"{prefix}-{name}" if prefix else name
    project = str(answers.get("project_name", ""))
    slug = name.replace("-", "_")
    render(
        template_dir,
        destination,
        {
            "kind": kind,
            "package_dir": name,
            "package_name": package_name,
            "package_description": f"{package_name}: a {project} workspace package.",
            "namespace_package": namespace,
            "author_name": answers.get("author_name", ""),
            "author_email": answers.get("author_email", ""),
            "copyright_year": answers.get("copyright_year", ""),
            # The forge facts ride the contract, not the answers: a
            # package's changelog links its own pull requests, and a
            # default taken from the template would put a Gitea
            # workspace's entries on github.com.
            **package_injections(root),
        },
        ref=ref,
    )
    _redact_answers_source(destination / _ANSWERS)
    members = list(answers.get("packages", []))
    members.append({"dir": name, "name": package_name, "dev": package_name})
    answers["packages"] = members
    _write_root_answers(root, answers)
    for changed in apply_project(root):
        print(f"  rendered: {changed}")
    from livery.workshop._uv import run_uv

    run_uv("lock", root=root)
    run_uv("sync", root=root)
    print(f"  packages/{name}: rendered, wired, and installed")
    return f"{namespace}.{slug}" if namespace else slug


def _redact_answers_source(path: Path) -> None:
    """Strip URL credentials from an answers file copier just wrote.

    copier records the clone source verbatim; the committed receipt
    must never carry a token.
    """
    if not path.is_file():
        return
    text = path.read_text("utf-8")
    cleaned = re.sub(
        r"^(_src_path: )(.+)$",
        lambda m: m.group(1) + redacted_source(m.group(2)),
        text,
        count=1,
        flags=re.M,
    )
    if cleaned != text:
        write_lf(path, cleaned)


def _write_root_answers(root: Path, answers: dict[str, Any]) -> None:
    """Rewrite the root answers file, header and bookkeeping kept."""
    body = (
        "# Managed by copier: this instance's identity and template\n"
        f"# provenance. `{footman.prog()} new.package` appends to `packages`;"
        " edit other\n"
        "# values only when the workspace itself changes.\n"
        f"_src_path: {redacted_source(template_source(root))}\n"
        + yaml.safe_dump(answers, sort_keys=False, allow_unicode=True)
    )
    write_lf(root / _ANSWERS, body)
