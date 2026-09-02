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
import sys
import tempfile
from pathlib import Path
from typing import Annotated, Any

import yaml
from footman import doc, fail, group

from livery.workshop._layers import workspace_root
from livery.workshop._materialise import write_lf

template = group("template", help="The template source and its render gate")
new = group("new", help="Render new pieces from the template source")

_ANSWERS = ".copier-answers.yml"

#: Where instances take their templates from when the contract is
#: silent: the published artifact repository, readable anonymously.
DEFAULT_TEMPLATE_SOURCE = "https://github.com/willemkokke/workshop-templates"


def template_source(root: Path) -> str:
    """The workspace's declared template source.

    ``[workspace] templates`` in ``livery.toml``: a directory relative
    to the root (the monorepo says ``templates``), or a git URL (a
    fork, at its own risk). Silent means the published artifact
    repository.
    """
    import tomllib

    contract = tomllib.loads((root / "livery.toml").read_text("utf-8"))
    workspace = contract.get("workspace") or {}
    return str(workspace.get("templates", "")) or DEFAULT_TEMPLATE_SOURCE


def local_template_dir(root: Path) -> Path | None:
    """The template source as a local directory, or None when remote."""
    source = template_source(root)
    if "://" in source or source.startswith("git@"):
        return None
    directory = root / source
    return directory if directory.is_dir() else None


def _root() -> Path:
    """The workspace root, or fail."""
    root = workspace_root()
    if root is None:
        fail("no workspace: no livery.toml above the working directory")
    return root


def read_answers(path: Path) -> dict[str, Any]:
    """The adopted answers at *path*, copier's own bookkeeping stripped."""
    if not path.is_file():
        fail(f"no answers file at {path}: adopt one before rendering")
    answers = yaml.safe_load(path.read_text("utf-8")) or {}
    if not isinstance(answers, dict):
        fail(f"{path} is not a mapping")
    return {key: value for key, value in answers.items() if not key.startswith("_")}


def render(template_dir: Path, destination: Path, data: dict[str, Any]) -> None:
    """Render *template_dir* into *destination* with *data*, no prompts.

    The working tree is the source: uncommitted template edits render
    too, which is what lets the gate judge a change before its commit.
    Runs copier in a child process because it chdirs while rendering,
    which a parallel task must never do to the one real directory.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as handle:
        yaml.safe_dump(data, handle)
        data_file = handle.name
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "copier",
                "copy",
                "--defaults",
                "--trust",
                "--overwrite",
                "--quiet",
                "--data-file",
                data_file,
                str(template_dir),
                str(destination),
            ],
            capture_output=True,
            text=True,
        )
    finally:
        Path(data_file).unlink(missing_ok=True)
    if result.returncode != 0:
        fail(f"copier exited {result.returncode}:\n{result.stdout}{result.stderr}")


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
    data = read_answers(root / _ANSWERS)
    from livery.workshop._brand import runner_prog

    # Injected at render time, never stored in the answers: the
    # runner's name belongs to the process, so a branded CLI's
    # drift gate demands branded files and apply re-emits them.
    data = {**data, "runner_prog": runner_prog()}
    source = local_template_dir(root)
    if source is None:
        fail("no local template source: the render gate needs one")
    drift = []
    with tempfile.TemporaryDirectory() as scratch:
        render(source, Path(scratch), data)
        for rendered in rendered_files(Path(scratch)):
            relative = rendered.relative_to(scratch)
            committed = root / relative
            if not committed.is_file():
                drift.append(f"{relative}: rendered, but missing from the repository")
            elif _lf(committed.read_bytes()) != _lf(rendered.read_bytes()):
                drift.append(f"{relative}: differs from its render")
    from livery.workshop._ci_generate import generated_files
    from livery.workshop._governance import codeowners_file

    generated = dict(generated_files(root, data))
    rendered_owners = codeowners_file(root)
    if rendered_owners is not None:
        generated[root / rendered_owners.path] = rendered_owners.content
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


def package_drift(root: Path) -> list[str]:
    """The drift report for every package's managed rendered files.

    Only the files in
    livery.workshop._templates.PACKAGE_MANAGED are judged: a package
    is rendered once and then written, so its seeds are not the
    template's to keep. A package whose managed file is missing is
    named, which is what a package rendered before the file existed
    looks like.
    """
    source = local_template_dir(root)
    if source is None:
        fail("no local template source: the render gate needs one")
    drift = []
    packages = root / "packages"
    for answers_path in sorted(packages.glob("*/.copier-answers.yml")):
        directory = answers_path.parent
        data = read_answers(answers_path)
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
    data = read_answers(root / _ANSWERS)
    from livery.workshop._brand import runner_prog

    # Injected at render time, never stored in the answers: the
    # runner's name belongs to the process, so a branded CLI's
    # drift gate demands branded files and apply re-emits them.
    data = {**data, "runner_prog": runner_prog()}
    source = local_template_dir(root)
    if source is None:
        fail("no local template source: nothing to apply from")
    changed = []
    with tempfile.TemporaryDirectory() as scratch:
        render(source, Path(scratch), data)
        for rendered in rendered_files(Path(scratch)):
            relative = rendered.relative_to(scratch)
            committed = root / relative
            body = _lf(rendered.read_bytes())
            if not committed.is_file() or _lf(committed.read_bytes()) != body:
                committed.parent.mkdir(parents=True, exist_ok=True)
                committed.write_bytes(body)
                changed.append(str(relative))
    from livery.workshop._ci_generate import generated_files
    from livery.workshop._governance import codeowners_file

    generated = dict(generated_files(root, data))
    rendered_owners = codeowners_file(root)
    if rendered_owners is not None:
        generated[root / rendered_owners.path] = rendered_owners.content
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
    source = local_template_dir(root)
    if source is None:
        fail("no local template source: nothing to apply from")
    changed = []
    for answers_path in sorted((root / "packages").glob("*/.copier-answers.yml")):
        directory = answers_path.parent
        data = read_answers(answers_path)
        with tempfile.TemporaryDirectory() as scratch:
            render(source, Path(scratch), data)
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
            " `fm template.apply`"
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
    if local_template_dir(root) is None:
        fail("no local template source here: nothing to apply")
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
    root = _root()
    template_dir = local_template_dir(root)
    if template_dir is None:
        fail(
            f"the template source is {template_source(root)}, and"
            " new.package can only render from a local checkout today:"
            " the global create verb will read the artifact repository"
        )
    if not re.fullmatch(r"[a-z][a-z0-9-]*", name):
        fail(f"package name {name!r}: use lowercase letters, digits, hyphens")
    destination = root / "packages" / name
    if destination.exists():
        fail(f"{destination} already exists")
    answers = read_answers(root / _ANSWERS)
    from livery.workshop._brand import runner_prog

    package_name = f"livery-{name}"
    render(
        template_dir,
        destination,
        {
            "kind": "package-python",
            "package_dir": name,
            "package_name": package_name,
            "package_description": f"{package_name}: a livery workspace package.",
            "namespace_package": "livery",
            "author_name": answers.get("author_name", ""),
            "author_email": answers.get("author_email", ""),
            "copyright_year": answers.get("copyright_year", ""),
            "forge_owner": answers.get("forge_owner", ""),
            # The forge the workspace is on, carried down: a package's
            # changelog links its own pull requests, and a default
            # taken from the template would put a Gitea workspace's
            # entries on github.com.
            "forge_kind": answers.get("forge_kind", ""),
            "forge_url": answers.get("forge_url", ""),
            "project_name": answers.get("project_name", ""),
            "python_versions": answers.get("python_versions", []),
            "runner_prog": runner_prog(),
        },
    )
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


def _write_root_answers(root: Path, answers: dict[str, Any]) -> None:
    """Rewrite the root answers file, header and bookkeeping kept."""
    body = (
        "# Managed by copier: this instance's identity and template\n"
        "# provenance. `fm new.package` appends to `packages`; edit other\n"
        "# values only when the workspace itself changes.\n"
        "_src_path: templates\n"
        + yaml.safe_dump(answers, sort_keys=False, allow_unicode=True)
    )
    write_lf(root / _ANSWERS, body)
