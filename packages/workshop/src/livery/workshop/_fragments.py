"""The configuration a check owns, composed into the files the render writes.

A check record carries one fragment per rendered file it has
something to say in: its tables in ``pyproject.toml``, its lines in
``.vscode/settings.json``, its ``.clang-tidy`` in each C or C++ package.
The render composes the base template with the registered fragments
in check-name order, so two machines write the same bytes, and the
drift gate judges the result. A fragment is jinja text rendered with
the same data the template reads. A project file's fragment applies
once per workspace, with per-package variation spelled inside it the
tool's own way; a package file's fragment applies in each package
whose set holds one of the extensions it names, the first check by
name winning when two have one. A fragment leaves the render
with its record: unregistering the check removes the section from the
next render, and a per-package file it wrote alone goes when its
bytes are the bytes the render last wrote.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: The project files a fragment may address; a fragment naming another
#: file refuses at registration.
PROJECT_FILES = ("pyproject.toml", ".vscode/settings.json", ".vscode/extensions.json")


@dataclass(frozen=True)
class Fragment:
    """One check's configuration for one rendered file.

    Attributes:
        file: The rendered file, a project file relative to the root
            or a per-package file name.
        text: The fragment's jinja text, rendered with the file's own
            render data.
        extensions: For a per-package file, the package-level extensions
            whose packages render it; empty for a project file.
    """

    file: str
    text: str
    extensions: tuple[str, ...] = ()


def verify(fragments: tuple[Fragment, ...], check: str) -> None:
    """Refuse a fragment naming a file the render does not write.

    Raises:
        ValueError: naming the check, the file, and the files a
            fragment may address.
    """
    for fragment in fragments:
        if fragment.file in PROJECT_FILES:
            if fragment.extensions:
                raise ValueError(
                    f"check {check!r}: a fragment for {fragment.file} applies to"
                    " the whole workspace and names no extension"
                )
            continue
        if fragment.extensions:
            continue
        raise ValueError(
            f"check {check!r}: a fragment for {fragment.file!r} names a file the"
            f" render does not write; the project files are"
            f" {', '.join(PROJECT_FILES)}, and a fragment that names extensions is"
            " rendered in each package whose set holds one"
        )


def _render(text: str, data: dict[str, Any]) -> str:
    """*text* rendered with *data*."""
    import json

    import minijinja

    # Lenient on an undefined name, so a render over partial data draws
    # the file the full data would, less the missing parts; the drift
    # gate reads the full data and judges the bytes.
    environment = minijinja.Environment(
        keep_trailing_newline=True, undefined_behavior="lenient"
    )
    plain = json.loads(json.dumps(data, default=str))
    return environment.render_str(text, **plain)


def compose_project(data: dict[str, Any]) -> dict[str, str]:
    """Each project file's composed fragments, by file, in check-name order.

    A file no check has a fragment for maps to the empty string, so a
    template's ``{{ fragments['...'] }}`` always resolves.
    """
    from livery.workshop._checks import checks_by_name

    found: dict[str, str] = {}
    for file in PROJECT_FILES:
        found[file] = ""
    for name in sorted(checks_by_name()):
        record = checks_by_name()[name]
        for fragment in record.fragments:
            if fragment.file in PROJECT_FILES:
                rendered = _render(fragment.text, data)
                if found[fragment.file] and not found[fragment.file].endswith("\n\n"):
                    found[fragment.file] += "\n"
                found[fragment.file] += rendered
    return found


def package_files() -> tuple[str, ...]:
    """The per-package files the registered checks' fragments render, sorted.

    A fragment that names extensions is rendered in each package whose
    set holds one, so the files a C or C++ package carries are the
    registered checks' to say, and leave with them.
    """
    from livery.workshop._checks import checks_by_name

    return tuple(
        sorted(
            {
                fragment.file
                for record in checks_by_name().values()
                for fragment in record.fragments
                if fragment.extensions
            }
        )
    )


def package_fragment(extensions: tuple[str, ...], file: str) -> tuple[str, str] | None:
    """The fragment text a package holding *extensions* renders as *file*, and its check.

    The first check by name whose fragment names one of *extensions*
    wins; None when no check has one, which is what a withdrawn check
    looks like to the apply.
    """
    from livery.workshop._checks import checks_by_name

    held = set(extensions)
    for name in sorted(checks_by_name()):
        for fragment in checks_by_name()[name].fragments:
            if fragment.file == file and held & set(fragment.extensions):
                return fragment.text, name
    return None


def compose_package(
    extensions: tuple[str, ...], file: str, data: dict[str, Any]
) -> str | None:
    """*file* rendered for a package holding *extensions*; None when no check owns it."""
    found = package_fragment(extensions, file)
    if found is None:
        return None
    text, _check = found
    return _render(text, data)
