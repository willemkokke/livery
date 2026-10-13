"""Read what the environment's installed distributions ship and require.

The declared-imports rule asks the environment two questions: which
installed distribution ships the module a source imports, and what a
distribution brings with it. Both answers come from the distributions'
own metadata (``importlib.metadata``) beside the running interpreter,
which is the workspace's environment when the gate runs, read once per
process. The answer reads the same files on every platform: a regular
install's ``RECORD``, and the path an editable install's ``.pth`` file
adds, so a workspace member installed for development and the same
distribution installed from its wheel ship the same modules.
"""

from __future__ import annotations

import csv
import functools
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from importlib import machinery, metadata
from pathlib import Path

#: The name at the start of a requirement, before its extras, version,
#: marker or URL.
_NAME = re.compile(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)")

#: A marker that makes a requirement part of an optional extra.
_EXTRA = re.compile(r"\bextra\b")

#: The suffixes a module's file ends with on this interpreter, longest
#: first, so ``.cpython-314-darwin.so`` wins over ``.so``.
_SUFFIXES = tuple(sorted(machinery.all_suffixes(), key=len, reverse=True))


def normalized(name: str) -> str:
    """*name* as distributions are compared: lower case, each run of ``-_.`` a dash."""
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_name(requirement: str) -> str:
    """The normalized distribution name a requirement string names; empty for none."""
    match = _NAME.match(requirement)
    return normalized(match.group(1)) if match else ""


@dataclass(frozen=True)
class Installed:
    """What the environment's distributions ship, and what each requires.

    Attributes:
        modules: Each module path a distribution ships, and each package
            path above it, to the distributions shipping it, by
            normalized name.
        requires: Each installed distribution, by normalized name, to
            the distributions it requires outside its optional extras.
    """

    modules: Mapping[str, frozenset[str]]
    requires: Mapping[str, frozenset[str]]

    def providers(self, name: str) -> frozenset[str]:
        """The distributions shipping the module that *name* names; empty for none.

        The longest dotted prefix of *name* that is a module decides,
        so ``yaml.safe_load`` resolves to the distribution that ships
        ``yaml``, and a namespace several distributions share to all
        of them.
        """
        parts = name.split(".")
        for end in range(len(parts), 0, -1):
            found = self.modules.get(".".join(parts[:end]))
            if found:
                return found
        return frozenset()

    def brings(self, distribution: str) -> frozenset[str]:
        """*distribution* and every distribution it requires, transitively.

        A requirement under any marker but an extra's counts, whatever
        the platform, so the answer is the same on every machine.
        """
        found: set[str] = set()
        pending = [normalized(distribution)]
        while pending:
            name = pending.pop()
            if name not in found:
                found.add(name)
                pending.extend(self.requires.get(name, ()))
        return frozenset(found)


@functools.cache
def installed() -> Installed:
    """The environment's distributions, read from their metadata once per process."""
    return read_installed(metadata.distributions())


def read_installed(distributions: Iterable[metadata.Distribution]) -> Installed:
    """What *distributions* ship and require, read from their metadata."""
    modules: dict[str, set[str]] = {}
    requires: dict[str, frozenset[str]] = {}
    for distribution in distributions:
        about = distribution.metadata
        name = normalized(about["Name"]) if "Name" in about else ""
        if not name:
            continue
        requires[name] = requires.get(name, frozenset()) | _required(distribution)
        for module in _shipped(distribution):
            for end in range(1, len(module) + 1):
                modules.setdefault(".".join(module[:end]), set()).add(name)
    return Installed(
        modules={module: frozenset(names) for module, names in modules.items()},
        requires=requires,
    )


def _required(distribution: metadata.Distribution) -> frozenset[str]:
    """The distributions *distribution* requires outside its optional extras."""
    found: set[str] = set()
    for requirement in distribution.requires or ():
        spec, _, marker = requirement.partition(";")
        if _EXTRA.search(marker):
            continue
        name = requirement_name(spec)
        if name:
            found.add(name)
    return frozenset(found)


def _shipped(distribution: metadata.Distribution) -> Iterator[tuple[str, ...]]:
    """Each module *distribution* ships, as its dotted parts.

    A regular install lists every file in its ``RECORD``. An editable
    install lists a ``.pth`` file at the top of the environment instead,
    whose lines add directories to the import path: what those hold is
    the distribution's.
    """
    for file in _files(distribution):
        parts = file.split("/")
        if len(parts) == 1 and file.endswith(".pth"):
            pth = Path(str(distribution.locate_file(file)))
            for directory in _pth_directories(pth):
                yield from _modules_under(directory, ())
            continue
        module = _module_name(parts[-1])
        if module is None or not all(part.isidentifier() for part in parts[:-1]):
            continue
        yield tuple(parts[:-1] if module == "__init__" else [*parts[:-1], module])


def _files(distribution: metadata.Distribution) -> list[str]:
    """The files *distribution* installed, each relative to its environment, posix.

    Read from ``RECORD`` as text, which costs a fraction of building
    a path object for each of the tens of thousands of lines an
    environment lists; an install without one answers through
    ``importlib.metadata``.
    """
    record = distribution.read_text("RECORD")
    if record is None:
        return [str(file) for file in distribution.files or ()]
    return [row[0] for row in csv.reader(record.splitlines()) if row and row[0]]


def _pth_directories(pth: Path) -> Iterator[Path]:
    """The directories the ``.pth`` file *pth* adds to the import path.

    A line that is empty, a comment, or code (``import ...``) adds
    none; a relative line is read against the file's own directory, as
    the interpreter reads it.
    """
    try:
        lines = pth.read_text("utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return
    for line in lines:
        entry = line.strip()
        if not entry or entry.startswith(("#", "import ", "import\t")):
            continue
        directory = pth.parent / entry
        if directory.is_dir():
            yield directory


def _modules_under(
    directory: Path, prefix: tuple[str, ...]
) -> Iterator[tuple[str, ...]]:
    """What *directory* on the import path makes importable, under *prefix*.

    A regular package (a directory holding ``__init__.py``) is named
    whole, since everything under it is the package's. A directory
    without one is a namespace portion, read through to what it holds.
    """
    try:
        entries = sorted(directory.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.is_dir():
            if entry.name == "__pycache__" or not entry.name.isidentifier():
                continue
            if (entry / "__init__.py").is_file():
                yield (*prefix, entry.name)
            else:
                yield from _modules_under(entry, (*prefix, entry.name))
            continue
        module = _module_name(entry.name)
        if module is not None and module != "__init__":
            yield (*prefix, module)


def _module_name(file: str) -> str | None:
    """The module a file named *file* is, by its suffix; None for no module."""
    for suffix in _SUFFIXES:
        if file.endswith(suffix):
            module = file[: -len(suffix)]
            return module if module.isidentifier() else None
    return None
