"""Read an extension's declaration file, ``extension.toml``, without importing it.

An extension declares itself in one ``extension.toml``, beside the
package its ``workshop.extensions`` entry point names: the workshop API
version it is written for, the levels it may be listed at, its plugin,
the extensions it requires, the tools its verbs need, the options a
listing may turn on, its checks, its CI jobs, the values it puts into
slots, the contract keys it owns, what it adds to another extension
while that one is listed, and the earlier extensions' shipped files it
replaces or deletes. The file is a contract, read as ``workshop.toml`` is: kebab-case
keys, every key judged, every refusal naming the file.

A check whose verdict is its tool's exit code is its tool's words
(``judge``, ``fix``, ``safe-fix``, ``env``, ``matrix``), which the engine
runs ([livery.workshop._words.Command][]). Other behaviour is a
reference, ``"module:function"``, imported when it runs. The read
imports nothing. A reference whose module is not in the extension's
package, or whose module does not define the name at its top level,
refuses here, read from the module's source.
"""

from __future__ import annotations

import ast
import difflib
import importlib
import importlib.util
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Callable

    from livery.workshop._checks import CheckRecord, GateContext
    from livery.workshop._contract_keys import ContractKind, Declared, Type
    from livery.workshop._influence import Inputs
    from livery.workshop._points import JobContribution
    from livery.workshop._words import Words

#: The declaration file's name, beside the package an entry point names.
FILE = "extension.toml"

#: The workshop API version a declaration takes when it names none.
API_VERSION = 1

#: The contracts an extension may declare keys in.
CONTRACTS = ("root", "package")


class DeclarationError(RuntimeError):
    """An extension's declaration file says something the workshop cannot take.

    The extension's own mistake, which no sync repairs: the message
    names the file, the key, and what it takes.
    """


@dataclass(frozen=True)
class Reference:
    """A function a declaration names, ``"module:function"``, imported when it runs.

    Attributes:
        module: The module that defines it, in the extension's package.
        name: The name the module defines it under, at its top level.
    """

    module: str
    name: str

    def resolve(self) -> Any:
        """The object the reference names, its module imported now."""
        return getattr(importlib.import_module(self.module), self.name)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Call the named function with *args* and *kwargs*, importing it first."""
        return self.resolve()(*args, **kwargs)

    def __str__(self) -> str:
        """The reference as the declaration spells it."""
        return f"{self.module}:{self.name}"


@dataclass(frozen=True)
class Additions:
    """What a declaration adds while it is mounted.

    Attributes:
        checks: The checks, as records under the extension's name.
        contributions: The values it puts into slots, each ``(slot, value)``.
        jobs: The CI jobs it adds to the builtin points, with their entries.
    """

    checks: tuple[CheckRecord, ...] = ()
    contributions: tuple[tuple[str, str], ...] = ()
    jobs: tuple[JobContribution, ...] = ()


@dataclass(frozen=True)
class DeclaredSlot:
    """A slot a declaration declares, which extensions put values into.

    Attributes:
        name: The slot's name, ``docs.theme``.
        compose: How the values compose: ``union``, ``nearest``, or the
            function that composes them.
        default: The value with nothing contributed; a union's is an
            empty list.
        values: The only values the slot accepts; None accepts any.
    """

    name: str
    compose: str | Reference = "union"
    default: object = None
    values: tuple[object, ...] | None = None


@dataclass(frozen=True)
class DeclaredOutput:
    """A file an extension's code writes: a ``[fragments."<target>"]`` table.

    Attributes:
        target: The file's path from the workspace root; a path ending
            in ``/`` is a directory whose files the render names.
        render: The function the workshop calls with the workspace root.
            For a file it answers the text, or nothing for no file; for
            a directory, each file's path under it to its text, or to
            the shipped file or directory the file links to.
        local: Whether the files belong to this checkout alone and are
            never committed.
    """

    target: str
    render: Reference
    local: bool = False


@dataclass(frozen=True)
class Declaration:
    """An extension's declaration file, read and judged.

    Attributes:
        extension: The name the extension is listed by.
        package: The package its entry point names, where its references live.
        path: The declaration file.
        api_version: The workshop API version it is written for.
        levels: The levels it may be listed at.
        plugin: The footman plugin carrying its verbs; empty for none.
        requires: The extensions it cannot work without.
        tools: The tools its verbs need, as requirement strings.
        options: Each option a listing may turn on, to what it turns on.
        additions: What it adds whenever it is mounted.
        targets: What it adds to each target while the target is listed.
        target_tables: Each ``[for.<target>]`` table as the file holds it.
        tables: The file's top-level tables but ``[extension]`` and
            ``[for]``, as the file holds them.
        replaces: Each earlier extension's shipped file, ``<owner>:<name>``,
            its own file of that name replaces, to the reason.
        deletes: Each earlier extension's shipped file it deletes, to the
            reason.
        slots: The slots it declares.
        release_notes: The release-notes provider it names, which the
            mount registers; None for an extension that writes no notes.
        fragments: The files its code writes, by target.
    """

    extension: str
    package: str
    path: Path
    api_version: int = API_VERSION
    levels: tuple[str, ...] = ("workspace",)
    plugin: str = ""
    requires: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    options: dict[str, str] = field(default_factory=dict[str, str])
    additions: Additions = Additions()
    targets: dict[str, Additions] = field(default_factory=dict[str, Additions])
    target_tables: dict[str, dict[str, Any]] = field(
        default_factory=dict[str, dict[str, Any]]
    )
    tables: dict[str, Any] = field(default_factory=dict[str, Any])
    replaces: dict[str, str] = field(default_factory=dict[str, str])
    deletes: dict[str, str] = field(default_factory=dict[str, str])
    slots: tuple[DeclaredSlot, ...] = ()
    release_notes: Reference | None = None
    fragments: tuple[DeclaredOutput, ...] = ()


_LOCATED: dict[tuple[str, tuple[str, ...]], Path] = {}


def directory(package: str) -> Path | None:
    """Where *package* lives, found without importing it; None when it is absent.

    The directory that carries the declaration file when several hold
    the package's parts, as a namespace's portions do. Found once per
    import path, since a mount asks for each extension's several times;
    an absence is never kept, so a package installed later is found.
    """
    key = (package, tuple(sys.path))
    if key in _LOCATED:
        return _LOCATED[key]
    found = _locate(package)
    if found is not None:
        _LOCATED[key] = found
    return found


def _locate(package: str) -> Path | None:
    try:
        spec = importlib.util.find_spec(package)
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.submodule_search_locations:
        return None
    locations = [Path(location) for location in spec.submodule_search_locations]
    for location in locations:
        if (location / FILE).is_file():
            return location
    return locations[0]


def declaration_file(package: str) -> Path | None:
    """*package*'s declaration file; None when the package or its file is absent."""
    found = directory(package)
    if found is None or not (found / FILE).is_file():
        return None
    return found / FILE


#: The tables whose keys the author names, a file or a slot or a shipped
#: file's reference or an environment variable: their keys are data,
#: never judged kebab-case.
_NAMED = (
    ("checks", "*", "*", "fragments"),
    ("for", "*", "checks", "*", "*", "fragments"),
    ("checks", "*", "*", "env"),
    ("for", "*", "checks", "*", "*", "env"),
    ("contributions",),
    ("for", "*", "contributions"),
    ("replaces",),
    ("deletes",),
    ("slots", "*", "default"),
    ("fragments",),
)


def _underscored(table: dict[str, Any], parts: tuple[str, ...] = ()) -> list[str]:
    """Each key under *table* spelled with an underscore that is not a name."""
    from livery.workshop._contract_keys import shown

    named = any(
        len(pattern) == len(parts)
        and all(want in ("*", part) for want, part in zip(pattern, parts, strict=True))
        for pattern in _NAMED
    )
    found: list[str] = []
    for key, value in table.items():
        path = (*parts, key)
        if "_" in key and not named:
            found.append(shown(path))
        if isinstance(value, dict):
            found += _underscored(cast("dict[str, Any]", value), path)
        elif isinstance(value, list):
            for item in cast("list[object]", value):
                if isinstance(item, dict):
                    found += _underscored(cast("dict[str, Any]", item), path)
    return found


_PARSED: dict[tuple[Path, str], dict[str, Any]] = {}


def _parsed(path: Path, text: str) -> dict[str, Any]:
    """*path*'s *text* parsed, its keys judged kebab-case; refused naming the file.

    Parsed once per content: the result is kept by the text itself,
    since a file rewritten within one tick of the clock keeps its
    modification time.
    """
    key = (path, text)
    if key not in _PARSED:
        _PARSED[key] = _parse(path, text)
    return _PARSED[key]


def _parse(path: Path, text: str) -> dict[str, Any]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise DeclarationError(f"{path}: not valid TOML: {error}") from error
    wrong = _underscored(data)
    if wrong:
        listed = ", ".join(
            f"{key} (spell it {key.rpartition('.')[2].replace('_', '-')})"
            for key in wrong
        )
        raise DeclarationError(f"{path}: keys are kebab-case; found {listed}")
    return data


_CACHE: dict[tuple[str, str, Path, str], Declaration] = {}


def read(extension: str, package: str) -> Declaration | None:
    """*extension*'s declaration, from the file beside *package*; None without one.

    Judged once per content: the result is kept by the file's text.

    Raises:
        DeclarationError: when the file does not parse, a key is unknown
            or of the wrong type, or a reference does not resolve.
    """
    path = declaration_file(package)
    if path is None:
        return None
    text = path.read_text("utf-8")
    key = (extension, package, path, text)
    found = _CACHE.get(key)
    if found is None:
        found = _read(extension, package, path, text)
        _CACHE[key] = found
    return found


def _read(extension: str, package: str, path: Path, text: str) -> Declaration:
    from livery.workshop._contract_keys import judge

    data = _parsed(path, text)
    problems = judge(data, contract="extension", where=str(path), listed=None)
    if problems:
        raise DeclarationError(
            f"{path}:\n" + "\n".join(f"  {line}" for line in problems)
        )
    reader = _Reader(extension, package, path)
    identity = data.get("extension", {})
    options = {str(name): str(text) for name, text in data.get("options", {}).items()}
    additions = reader.additions(data, (), options)
    targets = {
        str(target): reader.additions(table, ("for", target), options)
        for target, table in data.get("for", {}).items()
    }
    contract_keys_of(data, path)  # judged here as well, so a mount names it
    return Declaration(
        extension,
        package,
        path,
        api_version=int(identity.get("api-version", API_VERSION)),
        levels=tuple(str(level) for level in identity.get("levels", ("workspace",))),
        plugin=str(identity.get("plugin", "")),
        requires=tuple(str(name) for name in identity.get("requires", ())),
        tools=tuple(str(text) for text in data.get("toolroom", {}).get("requires", ())),
        options=options,
        additions=additions,
        targets=targets,
        target_tables={
            str(target): dict(table) for target, table in data.get("for", {}).items()
        },
        tables={
            key: value for key, value in data.items() if key not in ("extension", "for")
        },
        replaces={str(ref): str(why) for ref, why in data.get("replaces", {}).items()},
        deletes={str(ref): str(why) for ref, why in data.get("deletes", {}).items()},
        slots=tuple(
            reader.slot(str(name), table)
            for name, table in data.get("slots", {}).items()
        ),
        release_notes=reader.reference(data["release-notes"], ("release-notes",))
        if "release-notes" in data
        else None,
        fragments=tuple(
            reader.output(str(target), table)
            for target, table in data.get("fragments", {}).items()
        ),
    )


class _Reader:
    """Build what one declaration file declares, refusing what does not resolve."""

    def __init__(self, extension: str, package: str, path: Path) -> None:
        self.extension = extension
        self.package = package
        self.path = path

    def refuse(self, where: tuple[str, ...], text: str) -> DeclarationError:
        from livery.workshop._contract_keys import shown

        return DeclarationError(f"{self.path}: {shown(where)} {text}")

    def additions(
        self, data: dict[str, Any], prefix: tuple[str, ...], options: dict[str, str]
    ) -> Additions:
        checks = tuple(
            self.check(str(tool), str(role), entry, (*prefix, "checks"), options)
            for tool, roles in data.get("checks", {}).items()
            for role, entry in roles.items()
        )
        contributions = tuple(
            (str(slot), str(value))
            for slot, values in data.get("contributions", {}).items()
            for value in values
        )
        jobs = tuple(
            self.job(str(point), str(name), entry, (*prefix, "ci", "jobs"))
            for point, named in data.get("ci", {}).get("jobs", {}).items()
            for name, entry in named.items()
        )
        return Additions(checks, contributions, jobs)

    def job(
        self, point: str, name: str, entry: dict[str, Any], prefix: tuple[str, ...]
    ) -> JobContribution:
        """The job *entry* adds to the builtin *point*, its functions resolved."""
        from livery.workshop._points import DECLARED, Entry, Job, JobContribution

        where = (*prefix, point, name)
        builtin = {declared.name: declared for declared in DECLARED}
        if point not in builtin:
            raise self.refuse(
                where,
                f"names the point {point!r}, which is not builtin; a job joins"
                f" one of {', '.join(builtin)}",
            )
        if name in {job.name for job in builtin[point].jobs}:
            raise self.refuse(where, f"names a job the {point} point declares already")
        job = Job(
            name,
            needs=tuple(str(need) for need in entry.get("needs", ())),
            fetch=str(entry.get("fetch", "")),
            token=str(entry.get("token", "")),
            installs=self.reference(entry["installs"], (*where, "installs"))
            if "installs" in entry
            else None,
            deploy=self.reference(entry["deploy"], (*where, "deploy"))
            if "deploy" in entry
            else None,
            inputs=self.inputs(entry["inputs"], (*where, "inputs"))
            if "inputs" in entry
            else None,
            note=str(entry.get("note", "")),
        )
        return JobContribution(
            point,
            job,
            entries=tuple(
                Entry(point, name, str(task), source=self.extension)
                for task in entry.get("entries", ())
            ),
            gates=bool(entry.get("gates", False)),
            extension=self.extension,
        )

    def check(
        self,
        tool: str,
        role: str,
        entry: dict[str, Any],
        prefix: tuple[str, ...],
        options: dict[str, str],
    ) -> CheckRecord:
        from livery.workshop._checks import CheckRecord, Claim, Option
        from livery.workshop._fragments import verify

        where = (*prefix, tool, role)
        run, fix, words = self.bodies(entry, where)
        listed_with = str(entry.get("listed-with", ""))
        if listed_with and listed_with not in options:
            raise self.refuse(
                (*where, "listed-with"),
                f"is {listed_with!r}, which [options] does not declare; its"
                f" options are {', '.join(options) or 'none'}",
            )
        claims: list[Claim] = []
        for index, claim in enumerate(entry.get("claims", ())):
            if "category" not in claim:
                raise self.refuse(
                    (*where, f"claims[{index}]"), "names no category: a claim has one"
                )
            claims.append(
                Claim(
                    str(claim["category"]),
                    ignore=tuple(claim.get("ignore", ())),
                    suffixes=tuple(claim.get("suffixes", ())),
                )
            )
        inputs = (
            self.inputs(entry["inputs"], (*where, "inputs"))
            if "inputs" in entry
            else None
        )
        record = CheckRecord(
            tool,
            role,
            run,
            scope=str(entry.get("scope", "workspace")),
            narrowing=str(entry.get("narrowing", "none")),
            transport=str(entry.get("transport", "argv")),
            threshold=float(entry.get("threshold", 1.0)),
            fix=fix,
            words=words,
            kinds=tuple(entry.get("kinds", ())),
            tests_only=bool(entry.get("tests-only", False)),
            after=tuple(entry.get("after", ())),
            extension=self.extension,
            tools=tuple(entry.get("tools", ())),
            options=tuple(
                Option(
                    str(name),
                    str(option.get("type", "")),
                    option.get("default"),
                    str(option.get("doc", "")),
                )
                for name, option in entry.get("options", {}).items()
            ),
            fragments=self.fragments(entry.get("fragments", {}), (*where, "fragments")),
            editor_extension=str(entry.get("editor-extension", "")),
            claims=tuple(claims),
            roles=tuple(entry.get("roles", ())),
            flags=tuple(entry.get("flags", ())),
            listed_with=listed_with,
            arguments=bool(entry.get("arguments", False)),
            inputs=inputs,
        )
        for option in record.options:
            if option.kind not in ("bool", "str", "int"):
                raise self.refuse(
                    (*where, "options", option.name, "type"),
                    "is required: bool, str or int",
                )
        try:
            verify(record.fragments, record.name)
        except ValueError as error:
            raise self.refuse((*where, "fragments"), f"refuses: {error}") from error
        return record

    def bodies(
        self, entry: dict[str, Any], where: tuple[str, ...]
    ) -> tuple[
        Callable[[GateContext], None],
        Callable[[GateContext], None] | None,
        Words | None,
    ]:
        """A check's judge and fix, from its words or from references to its code.

        A check is its tool's words, ``judge``, or a reference to code,
        ``run``, never both. Words fix in words and code in code, and
        ``safe-fix``, ``env`` and ``matrix`` are words alone.
        """
        from livery.workshop._words import Command

        if "judge" in entry and "run" in entry:
            raise self.refuse(
                where,
                "declares both judge and run: a check is its tool's words or a"
                " reference to its code, never both",
            )
        if "judge" in entry:
            words = self.words(entry, where)
            name = f"{where[-1]}.{where[-2]}"
            fixing = Command(words, name, fixing=True) if words.fix else None
            return Command(words, name), fixing, words
        if "run" not in entry:
            raise self.refuse(
                where,
                "names neither judge nor run: a check is its tool's words, judge,"
                " or a reference to its code, run",
            )
        for key in ("safe-fix", "env", "matrix"):
            if key in entry:
                raise self.refuse(
                    (*where, key),
                    "belongs to a check in words, and this one runs code; declare"
                    " judge instead of run, or drop the key",
                )
        if "fix" in entry and not isinstance(entry["fix"], str):
            raise self.refuse(
                (*where, "fix"),
                "is words, and run is a reference to code: a check that runs code"
                " fixes with code, module:function",
            )
        coded = (
            self.reference(entry["fix"], (*where, "fix")) if "fix" in entry else None
        )
        return self.reference(entry["run"], (*where, "run")), coded, None

    def words(self, entry: dict[str, Any], where: tuple[str, ...]) -> Words:
        """The words *entry* declares: judge, fix, safe-fix, env and matrix."""
        from livery.workshop._words import Words

        def argv(key: str) -> tuple[str, ...]:
            value = entry.get(key, [])
            if isinstance(value, str):
                raise self.refuse(
                    (*where, key),
                    f"is {value!r}, a reference to code; in a check in words,"
                    f" {key} is words: the tool's name, then its arguments",
                )
            return tuple(str(word) for word in value)

        judge, fix, safe_fix = argv("judge"), argv("fix"), argv("safe-fix")
        if not judge:
            raise self.refuse(
                (*where, "judge"), "is empty: the tool's name comes first"
            )
        if safe_fix and not fix:
            raise self.refuse(
                (*where, "safe-fix"),
                "is declared without fix: a check that fixes safely fixes too",
            )
        for key, value in (("fix", fix), ("safe-fix", safe_fix)):
            if value and value[0] != judge[0]:
                raise self.refuse(
                    (*where, key),
                    f"runs {value[0]}, and judge runs {judge[0]}: a check runs"
                    " one tool",
                )
        matrix: list[tuple[str, tuple[str, ...]]] = []
        for key, values in entry.get("matrix", {}).items():
            if not values:
                raise self.refuse(
                    (*where, "matrix", key),
                    "has no values: a matrix key runs one call per value",
                )
            matrix.append((str(key), tuple(str(value) for value in values)))
        env = tuple(
            (str(name), str(value)) for name, value in entry.get("env", {}).items()
        )
        return Words(judge, fix, safe_fix, env, tuple(matrix))

    def fragments(
        self, table: dict[str, Any], where: tuple[str, ...]
    ) -> tuple[Any, ...]:
        """A project file's fragment from its text; a package file's from each kind."""
        from livery.workshop._fragments import Fragment

        found: list[Fragment] = []
        for file, value in table.items():
            if isinstance(value, str):
                found.append(Fragment(file, value))
                continue
            entry = cast("dict[str, Any]", value)
            if "text" not in entry or not entry.get("kinds"):
                raise self.refuse(
                    (*where, file),
                    "is a package file's fragment: its text, and the kinds"
                    " whose packages render it",
                )
            found += (
                Fragment(file, str(entry["text"]), kind=str(kind))
                for kind in entry["kinds"]
            )
        return tuple(found)

    def output(self, target: str, table: dict[str, Any]) -> DeclaredOutput:
        """The file *target* names, written by the render its table references."""
        if "render" not in table:
            raise self.refuse(
                ("fragments", target),
                "names no render; a fragment's code is render = 'module:function'",
            )
        return DeclaredOutput(
            target,
            self.reference(table["render"], ("fragments", target, "render")),
            local=bool(table.get("local", False)),
        )

    def slot(self, name: str, table: dict[str, Any]) -> DeclaredSlot:
        """The slot *name* declares, its compose a rule or a reference."""
        from livery.workshop._slots import NEAREST, UNION

        where = ("slots", name)
        rule = str(table.get("compose", UNION))
        if rule in (UNION, NEAREST):
            compose: str | Reference = rule
        elif ":" in rule:
            compose = self.reference(rule, (*where, "compose"))
        else:
            raise self.refuse(
                (*where, "compose"),
                f"is {rule!r}; a slot composes by {UNION!r}, {NEAREST!r}, or a"
                " reference 'module:function'",
            )
        values = table.get("values")
        return DeclaredSlot(
            name,
            compose,
            default=table.get("default"),
            values=tuple(values) if values is not None else None,
        )

    def inputs(self, table: dict[str, Any], where: tuple[str, ...]) -> Inputs:
        """The files a workspace check or a job reads, from its ``inputs`` table."""
        from livery.workshop._influence import Inputs

        if "reads" not in table:
            raise self.refuse(where, "names no reads: inputs name the files it reads")
        return Inputs(
            reads=tuple(table["reads"]),
            per_file=bool(table.get("per-file", True)),
            widens=tuple(table.get("widens", ())),
            on_removal=bool(table.get("on-removal", False)),
            widen=self.reference(table["widen"], (*where, "widen"))
            if "widen" in table
            else None,
            ignores=tuple(table.get("ignores", ())),
        )

    def reference(self, text: object, where: tuple[str, ...]) -> Reference:
        """The reference *text* spells, resolved against the package's sources."""
        module, colon, name = str(text).partition(":")
        if not colon or not module or not name.isidentifier():
            raise self.refuse(where, f"is {text!r}; a reference is 'module:function'")
        if module != self.package and not module.startswith(f"{self.package}."):
            raise self.refuse(
                where,
                f"names {module}, outside the extension's package {self.package};"
                " a reference names the extension's own code",
            )
        source = self.source(module)
        if source is None:
            raise self.refuse(
                where, f"names {module}, which {self.package} does not contain"
            )
        defined = top_level_names(source)
        if name not in defined:
            near = difflib.get_close_matches(name, sorted(defined), n=1)
            hint = f"; did you mean {near[0]!r}?" if near else ""
            raise self.refuse(
                where,
                f"names {name}, which {module} does not define at its top level{hint}",
            )
        return Reference(module, name)

    def source(self, module: str) -> Path | None:
        """*module*'s source file beside the declaration; None when it is absent."""
        parts = [part for part in module[len(self.package) :].split(".") if part]
        base = self.path.parent.joinpath(*parts)
        candidates = [base / "__init__.py"]
        if parts:
            candidates.insert(0, base.with_suffix(".py"))
        return next((path for path in candidates if path.is_file()), None)


_DEFINED: dict[tuple[Path, str], frozenset[str]] = {}


def top_level_names(path: Path) -> frozenset[str]:
    """The names *path*'s module defines at its top level, read from its source.

    Its functions, classes, assignments and imports, those inside a
    top-level ``if`` or ``try`` among them. Parsed once per content.
    """
    text = path.read_text("utf-8")
    key = (path, text)
    found = _DEFINED.get(key)
    if found is None:
        tree = ast.parse(text, filename=str(path))
        found = frozenset(_defined(tree.body))
        _DEFINED[key] = found
    return found


def _defined(body: list[ast.stmt]) -> set[str]:
    names: set[str] = set()
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                names |= {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(
            node.target, ast.Name
        ):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names |= {
                alias.asname or alias.name.partition(".")[0] for alias in node.names
            }
        elif isinstance(node, ast.If):
            names |= _defined(node.body) | _defined(node.orelse)
        elif isinstance(node, ast.Try):
            names |= _defined(node.body) | _defined(node.orelse)
            for handler in node.handlers:
                names |= _defined(handler.body)
    return names


def defined_tasks(directory: Path) -> frozenset[str]:
    """The tasks the modules under *directory* define, ``<group>.<task>``, read by AST.

    A module-level ``<name> = group("<group>")`` defines a group, and
    ``<name> = <parent>.group("<sub>")`` one beneath another. A function
    decorated ``@<name>.task`` defines a task in that group, named by the
    decorator's ``name=`` or else as footman names it: the function's
    name, a trailing underscore dropped, underscores as dashes. A group
    another module defines is followed through ``from ... import``.
    Nothing is imported.
    """
    package = ".".join(directory.parts[-1:])
    modules: dict[str, ast.Module] = {}
    for path in sorted(directory.rglob("*.py")):
        relative = path.relative_to(directory).with_suffix("")
        dotted = ".".join(part for part in relative.parts if part != "__init__")
        modules[dotted] = ast.parse(path.read_text("utf-8"), filename=str(path))
    groups: dict[tuple[str, str], str] = {}
    for _round in range(2):
        for dotted, tree in modules.items():
            for node in tree.body:
                found = _group_assigned(node, dotted, groups)
                if found is not None:
                    groups[(dotted, found[0])] = found[1]
    for dotted, tree in modules.items():
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                source = node.module.rpartition(f"{package}.")[2]
                for alias in node.names:
                    group = groups.get((source, alias.name))
                    if group is not None:
                        groups.setdefault((dotted, alias.asname or alias.name), group)
    tasks: set[str] = set()
    for dotted, tree in modules.items():
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                call = decorator if isinstance(decorator, ast.Call) else None
                target = call.func if call is not None else decorator
                if not (
                    isinstance(target, ast.Attribute)
                    and target.attr == "task"
                    and isinstance(target.value, ast.Name)
                ):
                    continue
                group = groups.get((dotted, target.value.id))
                if group is None:
                    continue
                named = next(
                    (
                        keyword.value.value
                        for keyword in (call.keywords if call is not None else ())
                        if keyword.arg == "name"
                        and isinstance(keyword.value, ast.Constant)
                        and isinstance(keyword.value.value, str)
                    ),
                    node.name.rstrip("_").replace("_", "-"),
                )
                tasks.add(f"{group}.{named}")
    return frozenset(tasks)


def _group_assigned(
    node: ast.stmt, module: str, groups: dict[tuple[str, str], str]
) -> tuple[str, str] | None:
    """The variable and the group *node* assigns: ``group("x")``, ``<g>.group("y")``."""
    if not (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Call)
        and node.value.args
        and isinstance(node.value.args[0], ast.Constant)
        and isinstance(node.value.args[0].value, str)
    ):
        return None
    name = node.value.args[0].value
    func = node.value.func
    if isinstance(func, ast.Name) and func.id == "group":
        return node.targets[0].id, name
    if (
        isinstance(func, ast.Attribute)
        and func.attr == "group"
        and isinstance(func.value, ast.Name)
        and (module, func.value.id) in groups
    ):
        return node.targets[0].id, f"{groups[(module, func.value.id)]}.{name}"
    return None


def contract_keys(package: str) -> tuple[Declared, ...]:
    """The contract keys the declaration beside *package* owns; empty without one.

    Read without judging the rest of the file, and without importing
    anything: the contract judge asks every installed extension.

    Raises:
        DeclarationError: when the file does not parse or a key's
            declaration is malformed.
    """
    path = declaration_file(package)
    if path is None:
        return ()
    return contract_keys_of(_parsed(path, path.read_text("utf-8")), path)


def contract_keys_of(
    data: dict[str, Any], path: Path, *, contracts: tuple[str, ...] = CONTRACTS
) -> tuple[Declared, ...]:
    """The contract keys *data*'s ``[contract]`` tables declare, judged.

    *contracts* are the contracts the file may declare keys in: a
    workspace's for an extension; the base's own file declares the keys
    of ``extension.toml`` as well.
    """
    found: list[Declared] = []
    for contract, table in data.get("contract", {}).items():
        if contract not in contracts:
            raise DeclarationError(
                f"{path}: [contract.{contract}] names no contract; an extension"
                f" declares keys in {', '.join(contracts)}"
            )
        found += _keys(cast("ContractKind", contract), table, (), path)
    return tuple(found)


#: The keys a contract key's own declaration holds. Under one of these
#: names a table is a key of its own, declared beneath: a slot's
#: ``values`` is a key of ``extension.toml``, and its declaration is a
#: table.
_OWN = ("types", "values", "doc")


def _keys(
    contract: ContractKind, table: dict[str, Any], parts: tuple[str, ...], path: Path
) -> list[Declared]:
    from livery.workshop._contract_keys import TYPES, Declared

    where = f"[contract.{contract}{''.join('.' + part for part in parts)}]"
    found: list[Declared] = []
    own = {key for key in _OWN if key in table and not isinstance(table[key], dict)}
    if own:
        types = table.get("types") if "types" in own else None
        if not (
            isinstance(types, list)
            and types
            and all(kind in TYPES for kind in cast("list[object]", types))
        ):
            raise DeclarationError(
                f"{path}: {where} types is {types!r}; it lists one or more of"
                f" {', '.join(TYPES)}"
            )
        values = table.get("values", []) if "values" in own else []
        doc = table.get("doc", "") if "doc" in own else ""
        if not isinstance(values, list) or not isinstance(doc, str):
            raise DeclarationError(
                f"{path}: {where}: values is a list of strings, doc a string"
            )
        found.append(
            Declared(
                contract,
                ".".join(parts),
                cast(
                    "tuple[Type, ...]",
                    tuple(str(kind) for kind in cast("list[object]", types)),
                ),
                tuple(str(value) for value in cast("list[object]", values)),
                doc,
            )
        )
    for key, value in table.items():
        if key in own:
            continue
        if not isinstance(value, dict) or "." in key:
            raise DeclarationError(
                f"{path}: {where} {key} is not a key's declaration; a key is a"
                " table with types, and values and doc where it has them"
            )
        found += _keys(contract, cast("dict[str, Any]", value), (*parts, key), path)
    return found
