"""The cross-references in docstrings, judged against the anchors the site renders.

A docstring names another object as ``[pkg.module.Name][]``, and the
site build links the name to the anchor of the object's heading. A
name with no anchor stops the strict build, whether or not the object
exists. An anchor exists for each object a ``:::`` directive renders,
and for each member the python handler renders with it. The API
reference has a page of one directive per module of each package it
documents, and an authored page may carry directives of its own. The
handler's options and the ``docs.members`` policy decide which members
render: under the ``public`` policy an underscore function has no
anchor, and a module no page renders, such as one of an extension that
ships inside another member's wheel, has none.

`lint.docrefs` reads every docstring the site renders. On the API
pages and the authored pages, those are the docstrings of what they
render. On the task reference, they are the parts of a task's
docstring its page shows: its summary, its long description, its
parameters and its return value. Each reference there names an
anchor, or it refuses with the docstring's place and the page it
renders on. A docstring the site renders nowhere is not judged. Two
kinds of reference are left to the strict build: one outside every
member's namespace, which the site resolves against its inventories,
and one into a member that declines its reference with
``[docs] api = false``, since that member documents itself through
pages its own generators write while the site builds, which the gate
does not run. The pages render with the handler's options as the
site's config sets them; options written under a ``:::`` line of an
authored page are not read.

The site is computed in a child process of the workspace's
interpreter. It reads the sources with griffe, the library the site
build reads them with, and mounts each task provider the members
advertise with footman, as the task reference does.
"""

from __future__ import annotations

import ast
import contextlib
import inspect
import json
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from livery.workshop import Package, PhaseContext

#: A cross-reference: a dotted path of at least two names, in the
#: empty-title form ``[path][]``.
REFERENCE = re.compile(r"\[(?P<path>[A-Za-z_]\w*(?:\.\w+)+)\]\[\]")

#: Code the site renders as text: a fenced block, or an inline span in
#: single or double backticks. A reference written inside one links
#: nothing, so it names nothing either.
_CODE = re.compile(r"```.*?```|``.*?``|`[^`\n]*`", re.S)

#: A ``:::`` line of a page, as mkdocstrings reads one: the object it
#: documents, after an optional heading mark.
DIRECTIVE = re.compile(r"^(?:#{1,6} *)?::: ?(?P<name>\S+) *$", re.M)

#: The python handler's member filter when its options set none: every
#: name but one that starts with a single underscore. The ``public``
#: members policy keeps it, and ``all`` replaces it with no filter.
DEFAULT_FILTERS = ("!^_[^_]",)

#: The group a task provider's tree is mounted under in the child, its
#: index appended, so two providers' trees never meet.
_MOUNT = "provider"


def _without_code(text: str) -> str:
    """*text* with its code blanked out, its newlines kept so lines still count."""
    return _CODE.sub(
        lambda match: "".join("\n" if char == "\n" else " " for char in match[0]), text
    )


def references_in(text: str) -> set[str]:
    """The paths *text* references outside its code."""
    return {match["path"] for match in REFERENCE.finditer(_without_code(text))}


class Reference(NamedTuple):
    """One cross-reference in a source's docstrings.

    Attributes:
        docstring: The line its docstring starts on, which is the line
            griffe gives the docstring when the site reads it.
        line: The line the reference is on.
        path: The dotted path it names.
    """

    docstring: int
    line: int
    path: str


def docstring_references(source: Path) -> list[Reference]:
    """Each cross-reference in *source*'s docstrings, with its place.

    A docstring is any string literal standing as a statement: a
    module's, a class's or a function's first, or one under an
    assignment, which the site reads as that name's. A source that
    does not parse has none here; the syntax gate names it.
    """
    try:
        tree = ast.parse(source.read_text("utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    found: list[Reference] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            continue
        text = _without_code(node.value.value)
        for match in REFERENCE.finditer(text):
            line = node.lineno + text.count("\n", 0, match.start())
            found.append(Reference(node.lineno, line, match["path"]))
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


def _authored_pages(
    root: Path, packages: tuple[Package, ...]
) -> list[tuple[str, Path]]:
    """Every authored page, by the path it publishes at, the root tree's first.

    A member's ``docs/`` publishes under ``packages/<member>/``. What
    the build writes is not authored: a member's generated tree, and
    the root tree's mounts and generated sections.
    """
    from livery.extensions.docs._navblocks import GENERATED_DIR
    from livery.extensions.docs._site import SITE_TREES

    trees = [("", root / "docs")]
    trees += [
        (f"packages/{package.member}/", package.directory / "docs")
        for package in packages
    ]
    pages: list[tuple[str, Path]] = []
    for prefix, tree in trees:
        if not tree.is_dir():
            continue
        for page in sorted(tree.rglob("*.md")):
            parts = page.relative_to(tree).parts
            if GENERATED_DIR in parts or (not prefix and parts[0] in SITE_TREES):
                continue
            pages.append((prefix + page.relative_to(tree).as_posix(), page))
    return pages


def site_request(root: Path) -> dict[str, Any]:
    """What the child computes the site from: the inputs the docs build reads.

    The members' sources; every ``:::`` directive the python handler
    renders, with the page it publishes on, the API reference's one
    per module of each package it documents and then the authored
    pages'; the handler's options with the members policy applied, as
    the site's config writes them; the task providers each member
    advertises, with the member, while the root contract turns the
    task reference's step on; and the sources of the members that
    decline their reference.
    """
    from livery.extensions.docs._taskref import STEP, advertised_providers
    from livery.extensions.mkdocstrings._config import options as handler_options
    from livery.extensions.mkdocstrings._pages import (
        API_DIR,
        declines_api,
        documented,
        pages,
    )
    from livery.workshop import discover_packages
    from livery.workshop._extensions import enabled_steps

    packages = discover_packages(root)
    directives: list[tuple[str, str]] = []
    reached = [package for package in packages if documented(package)]
    for package in reached:
        directives += [
            (f"packages/{package.member}/{API_DIR}/{page}", dotted)
            for page, dotted in pages(package)
        ]
    for page, path in _authored_pages(root, packages):
        text = _without_code(path.read_text("utf-8"))
        directives += [(page, match["name"]) for match in DIRECTIVE.finditer(text)]
    with_sources = [
        package for package in packages if (package.directory / "src").is_dir()
    ]
    referenced = STEP in enabled_steps(root)
    return {
        "sources": [
            str((package.directory / "src").resolve()) for package in with_sources
        ],
        "pages": directives,
        "options": handler_options(root) if reached else {},
        "providers": [
            (identity, package.member)
            for package in (packages if referenced else ())
            for identity in advertised_providers(package)
        ],
        "declined": [
            str((package.directory / "src").resolve())
            for package in with_sources
            if declines_api(package)
        ],
    }


def reference_problems(root: Path) -> list[str]:
    """One line per reference in a docstring the site renders that names no anchor.

    Every member's sources are read, since a change anywhere may take
    an anchor away or put a docstring on a page. Each line names the
    reference's place, the reference, and the pages its docstring
    renders on. When no docstring holds a reference under a member's
    namespace, nothing is started.
    """
    from livery.workshop import discover_packages

    sources = [
        package.directory / "src"
        for package in discover_packages(root)
        if (package.directory / "src").is_dir()
    ]
    judged = namespaces(sources)
    found = [
        (source, reference)
        for src in sources
        for source in sorted(src.rglob("*.py"))
        for reference in docstring_references(source)
        if reference.path.split(".")[0] in judged
    ]
    if not found:
        return []
    request = site_request(root)
    request["paths"] = sorted({reference.path for _, reference in found})
    request["files"] = sorted({str(source.resolve()) for source, _ in found})
    answer = _probe(root, request)
    missing = {str(path) for path in answer["missing"]}
    shown: dict[tuple[str, int], list[tuple[str, set[str] | None]]] = {}
    for file, line, page, paths in answer["rendered"]:
        shown.setdefault((str(file), int(line)), []).append(
            (str(page), None if paths is None else {str(path) for path in paths})
        )
    problems: list[str] = []
    for source, reference in found:
        if reference.path not in missing:
            continue
        place = (str(source.resolve()), reference.docstring)
        pages = sorted(
            {
                page
                for page, paths in shown.get(place, ())
                if paths is None or reference.path in paths
            }
        )
        if pages:
            problems.append(
                f"{source.relative_to(root).as_posix()}:{reference.line}:"
                f" [{reference.path}][] has no anchor on the site, where the"
                f" docstring renders on {', '.join(pages)}; write the name as"
                f" plain code instead: `{reference.path}`"
            )
    return problems


def docrefs_run(ctx: PhaseContext) -> None:
    """Refuse every cross-reference the site cannot link: the lint phase's step.

    The lines of `reference_problems` over the workspace, under one
    heading.
    """
    from livery.footman import fail

    problems = reference_problems(ctx.root)
    if problems:
        fail("cross-references the site cannot link:\n  " + "\n  ".join(problems))


def _probe(root: Path, request: dict[str, Any]) -> dict[str, Any]:
    """The site *request* describes, computed in a child process.

    The child is the interpreter running this one, the workspace's own.
    Loading a package makes griffe ask git where the source lives, for
    the site's source links, and mounting a task provider imports it
    into a task tree: a gate's own process spawns nothing behind the
    runner's back, and its own task tree stays its own.
    """
    import livery.footman as footman

    result = footman.run(
        [sys.executable, "-m", "livery.extensions.mkdocstrings._refs"],
        cwd=root,
        input=json.dumps(request),
        nofail=True,
    )
    if result.code != 0:
        footman.fail(
            f"the cross-reference probe exited {result.code}:\n{result.stderr.rstrip()}"
        )
    answer: dict[str, Any] = json.loads(result.stdout)
    return answer


def _keep(name: str, filters: list[tuple[re.Pattern[str], bool]]) -> bool:
    """Whether the handler's *filters* keep a member named *name*.

    The last filter that matches decides. When none matches, the name
    is kept, unless every filter only selects.
    """
    keep: bool | None = None
    for pattern, exclude in filters:
        if pattern.search(name):
            keep = not exclude
    if keep is None:
        return {exclude for _, exclude in filters} != {False}
    return keep


class _Walk:
    """The python handler's rendering, reduced to its anchors and its docstrings.

    A ``:::`` directive renders its object under a heading whose anchor
    is the object's path, then the members the options keep, each
    under a heading of its own, through modules and classes. Each
    anchor also answers to the object's canonical path, and to each
    re-export of it that a module lists in its ``__all__``. The site
    build registers further names, the imports griffe resolved before
    a page was rendered; those depend on the order the pages are built
    in, so no anchor here rests on one.

    Of the options that choose members, the walk reads the ones the
    site's config sets: ``filters``, ``show_if_no_docstring`` and
    ``merge_init_into_class``. The handler's defaults stand for the
    rest: no inherited member and no submodule renders, and every
    member the filters keep does.

    Attributes:
        errors: What griffe raises for an import it cannot follow.
        missing: What looking up a dotted name raises when it names
            nothing.
        anchors: Every name an anchor rendered so far answers to.
        rendered: The pages each docstring rendered so far is on, by
            its source and the line it starts on.
    """

    def __init__(self, griffe: Any, options: dict[str, Any]) -> None:
        """Render with the handler's *options*, read through *griffe*."""
        self._kind = griffe.Kind
        self.errors: tuple[type[Exception], ...] = (
            griffe.AliasResolutionError,
            griffe.CyclicAliasError,
        )
        self.missing: tuple[type[Exception], ...] = (KeyError, ValueError, *self.errors)
        self._filters = [
            (re.compile(str(rule).removeprefix("!")), str(rule).startswith("!"))
            for rule in options.get("filters", DEFAULT_FILTERS)
        ]
        self._undocumented = bool(options.get("show_if_no_docstring", False))
        self._merge_init = bool(options.get("merge_init_into_class", False))
        self.anchors: set[str] = set()
        self.rendered: dict[tuple[str, int], set[str]] = {}

    def render(self, obj: Any, page: str) -> None:
        """Render *obj* on *page*: its anchors, its docstring, then its members."""
        self.anchors.update(self._identifiers(obj))
        try:
            target = obj.final_target if obj.is_alias else obj
            kind = target.kind
        except self.errors:
            return
        self._shows(target, page)
        if kind is self._kind.CLASS and self._merge_init:
            # The class's own __init__ documents the class with it.
            init = target.members.get("__init__")
            if init is not None and not init.is_alias:
                self._shows(init, page)
        if kind in (self._kind.MODULE, self._kind.CLASS):
            for member in self._children(obj):
                self.render(member, page)

    def _identifiers(self, obj: Any) -> set[str]:
        """The names the anchor of *obj* answers to."""
        found = {str(obj.path)}
        with contextlib.suppress(*self.errors):
            found.add(str(obj.canonical_path))
            target = obj.final_target if obj.is_alias else obj
            found.update(
                str(path) for path, alias in target.aliases.items() if alias.is_exported
            )
        return found

    def _shows(self, target: Any, page: str) -> None:
        """Note that *target*'s docstring renders on *page*.

        A namespace package, which lies in several directories, has no
        docstring to place.
        """
        docstring = target.docstring
        if docstring is None or docstring.lineno is None:
            return
        place = (str(Path(target.filepath).resolve()), int(docstring.lineno))
        self.rendered.setdefault(place, set()).add(page)

    def _children(self, obj: Any) -> list[Any]:
        """The members of *obj* the handler renders, as its template picks them.

        An import griffe cannot follow has no kind to render under, an
        import the module does not export renders nowhere, and a class's
        ``__init__`` renders with the class when the options merge it.
        """
        members = list(obj.all_members.values())
        is_class = bool(obj.is_class)
        shown = (
            self._kind.ATTRIBUTE,
            self._kind.TYPE_ALIAS,
            self._kind.CLASS,
            self._kind.FUNCTION,
        )
        return [
            member
            for member in members
            if member.kind in shown
            and not member.inherited
            and _keep(str(member.name), self._filters)
            and (self._undocumented or member.has_docstrings)
            and not (is_class and member.name == "__init__" and self._merge_init)
            and (not member.is_imported or member.is_public)
        ]


def _declined(
    collection: Any,
    path: str,
    declined: list[Path],
    missing: tuple[type[Exception], ...],
) -> bool:
    """Whether *path* lies in a module of a member that declines its reference.

    The module is the longest prefix of *path* that names one. A
    namespace counts only when every member that holds part of it
    declines. *missing* is what a lookup of a name raises when it
    names nothing.
    """
    if not declined:
        return False
    parts = path.split(".")
    for end in range(len(parts), 0, -1):
        try:
            found = collection.get_member(".".join(parts[:end]))
        except missing:
            continue
        if found.is_alias or not found.is_module:
            continue
        held = found.filepath if isinstance(found.filepath, list) else [found.filepath]
        return all(
            any(Path(file).resolve().is_relative_to(src) for src in declined)
            for file in held
        )
    return False


def _docstring_line(
    function: Any, parsed: dict[str, ast.Module]
) -> tuple[str, int] | None:
    """Where *function*'s docstring starts, its source and line; None without one.

    The function's first line is its first decorator's, the line the
    syntax tree starts it on too. A function made at run time, or one
    without a literal docstring, has none.
    """
    function = inspect.unwrap(function)
    code = getattr(function, "__code__", None)
    if code is None:
        return None
    source = str(Path(code.co_filename).resolve())
    if source not in parsed:
        try:
            parsed[source] = ast.parse(Path(source).read_text("utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            return None
    for node in ast.walk(parsed[source]):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        first = min([node.lineno, *(mark.lineno for mark in node.decorator_list)])
        if node.name != function.__name__ or first != code.co_firstlineno:
            continue
        head = node.body[0]
        if (
            isinstance(head, ast.Expr)
            and isinstance(head.value, ast.Constant)
            and isinstance(head.value.value, str)
        ):
            return source, head.lineno
        return None
    return None


def _task_docstrings(
    providers: list[tuple[str, str]],
) -> list[tuple[str, int, str, list[str]]]:
    """The task docstrings the task reference renders: place, page, and the paths shown.

    Each provider is mounted under a group of its own, as the task
    reference reads each one alone. A task's page shows its summary,
    its long description, and its parameters' and its return value's
    text, so only the references in those parts are on the page. What
    a provider prints while it is imported stays out of the answer.
    """
    import livery.footman as footman
    from livery.extensions.docs._taskref import task_page

    with contextlib.redirect_stdout(sys.stderr):
        for index, (identity, _member) in enumerate(providers):
            footman.plugin(identity, into=f"{_MOUNT}{index}")
    tree = footman.task_tree()
    views = {view.address: view for view in footman.Tasks(footman.root_group)}
    parsed: dict[str, ast.Module] = {}
    found: list[tuple[str, int, str, list[str]]] = []

    def visit(node: dict[str, Any], prefix: str, index: int, member: str) -> None:
        for name, task in (node.get("tasks") or {}).items():
            address = prefix + name
            view = views.get(f"{_MOUNT}{index}.{address}")
            place = None if view is None else _docstring_line(view.fn, parsed)
            if place is None:
                continue
            texts = [str(task.get(key, "")) for key in ("help", "long", "returned_doc")]
            texts += [str(param.get("doc", "")) for param in task.get("params", [])]
            paths = sorted({path for text in texts for path in references_in(text)})
            found.append((*place, task_page(member, address), paths))
        for name, group in (node.get("groups") or {}).items():
            visit(group, f"{prefix}{name}.", index, member)

    for index, (_identity, member) in enumerate(providers):
        visit(tree["groups"].get(f"{_MOUNT}{index}", {}), "", index, member)
    return found


def main() -> None:
    """The probe: the site the request on stdin describes, as JSON on stdout.

    It answers with the docstrings of the requested files the site
    renders, each with its page, and with the requested paths that
    name no anchor and lie in no member that declines its reference.
    """
    import griffe

    request = json.load(sys.stdin)
    sources = [Path(src) for src in request["sources"]]
    loader = griffe.GriffeLoader(
        search_paths=[str(src) for src in sources], allow_inspection=False
    )
    for top in sorted(namespaces(sources)):
        with contextlib.suppress(ImportError, KeyError):
            # A dotted name, never a path: the working directory has no say.
            loader.load(top, try_relative_path=False)
    loader.resolve_aliases(implicit=False, external=None)
    walk = _Walk(griffe, request["options"])
    collection = loader.modules_collection
    for page, identifier in request["pages"]:
        try:
            found = collection.get_member(identifier)
        except walk.missing:
            continue
        walk.render(found, page)
    files = set(request["files"])
    rendered: list[tuple[str, int, str, list[str] | None]] = [
        (file, line, page, None)
        for (file, line), pages in sorted(walk.rendered.items())
        if file in files
        for page in sorted(pages)
    ]
    providers = [
        (str(identity), str(member)) for identity, member in request["providers"]
    ]
    rendered += [entry for entry in _task_docstrings(providers) if entry[0] in files]
    declined = [Path(src) for src in request["declined"]]
    missing = [
        path
        for path in request["paths"]
        if path not in walk.anchors
        and not _declined(collection, path, declined, walk.missing)
    ]
    json.dump({"rendered": rendered, "missing": missing}, sys.stdout)


if __name__ == "__main__":
    main()
