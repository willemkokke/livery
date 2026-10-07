"""The cross-references in docstrings, resolved the way the API site resolves them.

A docstring names another object as ``[pkg.module.Name][]``, and
the site build turns the name into a link; one that names nothing
stops the strict build. `lint.docrefs` finds them in the changed
sources and resolves each with griffe, the library the site build
reads the sources with, so the two agree on what a name reaches:
through a package's re-export, to a method by its class. Only a
reference under a namespace a workspace member provides is judged; the
site build judges the rest against the inventories it is given.

A package loads once per run, when a reference first needs it.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

#: A cross-reference: a dotted path of at least two names, in the
#: empty-title form ``[path][]``.
REFERENCE = re.compile(r"\[(?P<path>[A-Za-z_]\w*(?:\.\w+)+)\]\[\]")

#: Code the site renders as text: a fenced block, or an inline span in
#: single or double backticks. A reference written inside one links
#: nothing, so it names nothing either.
_CODE = re.compile(r"```.*?```|``.*?``|`[^`\n]*`", re.S)


def _without_code(text: str) -> str:
    """*text* with its code blanked out, its newlines kept so lines still count."""
    return _CODE.sub(
        lambda match: "".join("\n" if char == "\n" else " " for char in match[0]), text
    )


def docstring_references(source: Path) -> list[tuple[int, str]]:
    """Each cross-reference in *source*'s docstrings, with the line it is on.

    A docstring is any string literal standing as a statement: a
    module's, a class's or a function's first, or one under an
    assignment, which the site reads as that name's. A source that
    does not parse has none here; the syntax gate names it.
    """
    try:
        tree = ast.parse(source.read_text("utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            continue
        text = _without_code(node.value.value)
        for match in REFERENCE.finditer(text):
            found.append(
                (node.lineno + text.count("\n", 0, match.start()), match["path"])
            )
    return found


def namespaces(sources: list[Path]) -> set[str]:
    """The top-level names the members' sources provide.

    A package directory, namespace or regular, gives its name, and so
    does a module that is a distribution's whole source (``tool.py``
    gives ``tool``).
    """
    names: set[str] = set()
    for src in sources:
        for child in src.iterdir():
            if child.is_dir() and child.name.isidentifier():
                names.add(child.name)
            elif child.suffix == ".py" and child.stem.isidentifier():
                names.add(child.stem)
    return names


class Resolver:
    """Resolves dotted names against the workspace's sources, one top-level name a load.

    griffe loads the whole top-level package, namespace or regular,
    whatever dotted path it is handed, so one load per top-level name
    is all a walk needs; a load per subpackage would parse the same
    namespace again for each.
    """

    def __init__(self, sources: list[Path]) -> None:
        """Read *sources*, every member's ``src`` directory, on demand."""
        import griffe

        self._griffe: Any = griffe
        self._loader: Any = griffe.GriffeLoader(
            search_paths=[str(src) for src in sources], allow_inspection=False
        )
        self._tops = namespaces(sources)
        self._loaded: set[str] = set()

    def _load(self, path: str) -> bool:
        """Load the top-level package of *path*; whether anything new was loaded."""
        top = path.split(".")[0]
        if top not in self._tops or top in self._loaded:
            return False
        self._loaded.add(top)
        try:
            # A dotted name, never a path: the working directory has no say.
            self._loader.load(top, try_relative_path=False)
        except (ImportError, KeyError):
            return False
        return True

    def resolves(self, path: str) -> bool:
        """Whether *path* names an object, following re-exports to their source."""
        griffe = self._griffe
        # Each turn loads one more package or ends the walk.
        for _ in range(32):
            try:
                found = self._loader.modules_collection.get_member(path)
            except (KeyError, ValueError):
                if not self._load(path):
                    return False
                continue
            if not found.is_alias:
                return True
            try:
                found.final_target  # noqa: B018 - resolving is the check
            except griffe.CyclicAliasError:
                return False
            except griffe.AliasResolutionError as error:
                if not self._load(error.alias.target_path):
                    return False
                continue
            return True
        return False


def unresolved(root: Path, paths: list[str], sources: list[Path]) -> set[str]:
    """The *paths* that name nothing, resolved in a child process.

    The child is the interpreter running this one, the workspace's own.
    Loading a package makes griffe ask git where the source lives, for
    the site's source links, and a gate's own process spawns nothing
    behind the runner's back.
    """
    import livery.footman as footman

    result = footman.run(
        [sys.executable, "-m", "livery.extensions.docs._refs"],
        cwd=root,
        input=json.dumps({"sources": [str(src) for src in sources], "paths": paths}),
        nofail=True,
    )
    if result.code != 0:
        footman.fail(
            f"the cross-reference probe exited {result.code}:\n{result.stderr.rstrip()}"
        )
    return {str(path) for path in json.loads(result.stdout)["unresolved"]}


def reference_problems(root: Path, files: list[Path], sources: list[Path]) -> list[str]:
    """One line per cross-reference in *files*' docstrings that names nothing.

    A reference under a namespace a member provides is judged, so a
    misspelt module there refuses too; one outside every such namespace
    is left to the site build. When no reference is judged, nothing is
    started.
    """
    judged = namespaces(sources)
    found = [
        (source, line, path)
        for source in files
        for line, path in docstring_references(source)
        if path.split(".")[0] in judged
    ]
    if not found:
        return []
    missing = unresolved(root, sorted({path for _, _, path in found}), sources)
    return [
        f"{source.relative_to(root).as_posix()}:{line}: [{path}][] names nothing"
        " the API site can link"
        for source, line, path in found
        if path in missing
    ]


def main() -> None:
    """The probe: resolve the paths the request names; print the ones that fail."""
    request = json.load(sys.stdin)
    resolver = Resolver([Path(src) for src in request["sources"]])
    failed = [path for path in request["paths"] if not resolver.resolves(path)]
    json.dump({"unresolved": failed}, sys.stdout)


def defined_names(text: str) -> set[str]:
    """The names a module's source defines at its top and in its classes, dotted."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    names: set[str] = set()

    def walk(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                names.add(prefix + node.name)
            elif isinstance(node, ast.ClassDef):
                names.add(prefix + node.name)
                walk(node.body, f"{prefix}{node.name}.")
            elif isinstance(node, ast.Assign):
                names.update(
                    prefix + target.id
                    for target in node.targets
                    if isinstance(target, ast.Name)
                )
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.add(prefix + node.target.id)

    walk(tree.body, "")
    return names


if __name__ == "__main__":
    main()
