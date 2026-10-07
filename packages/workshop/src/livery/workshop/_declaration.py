"""Read an extension's declaration file, ``extension.toml``, without importing it.

An extension declares itself in one ``extension.toml``, beside the
package its ``workshop.extensions`` entry point names: the workshop API
version it is written for, the levels it may be listed at, its plugin,
the extensions it requires, the tools its verbs need, the options a
listing may turn on, its checks, the values it puts into slots, the
contract keys it owns, what it adds to another extension while that one
is listed, and the earlier extensions' shipped files it replaces or
deletes. The file is a contract, read as ``workshop.toml`` is: kebab-case
keys, every key judged, every refusal naming the file.

Behaviour is a reference, ``"module:function"``, imported when it runs.
The read imports nothing. A reference whose module is not in the
extension's package, or whose module does not define the name at its
top level, refuses here, read from the module's source.
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
    from livery.workshop._checks import CheckRecord
    from livery.workshop._contract_keys import ContractKind, Declared, Type

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
    """

    checks: tuple[CheckRecord, ...] = ()
    contributions: tuple[tuple[str, str], ...] = ()


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
        replaces: Each earlier extension's shipped file, ``<owner>:<name>``,
            its own file of that name replaces, to the reason.
        deletes: Each earlier extension's shipped file it deletes, to the
            reason.
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
    replaces: dict[str, str] = field(default_factory=dict[str, str])
    deletes: dict[str, str] = field(default_factory=dict[str, str])


_LOCATED: dict[tuple[str, tuple[str, ...]], Path | None] = {}


def directory(package: str) -> Path | None:
    """Where *package* lives, found without importing it; None when it is absent.

    The directory that carries the declaration file when several hold
    the package's parts, as a namespace's portions do. Found once per
    import path: a mount asks for each extension's several times.
    """
    key = (package, tuple(sys.path))
    if key in _LOCATED:
        return _LOCATED[key]
    found = _locate(package)
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
#: file's reference: their keys are data, never judged kebab-case.
_NAMED = (
    ("checks", "*", "*", "fragments"),
    ("for", "*", "checks", "*", "*", "fragments"),
    ("contributions",),
    ("for", "*", "contributions"),
    ("replaces",),
    ("deletes",),
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
        replaces={str(ref): str(why) for ref, why in data.get("replaces", {}).items()},
        deletes={str(ref): str(why) for ref, why in data.get("deletes", {}).items()},
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
        return Additions(checks, contributions)

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
        if "run" not in entry:
            raise self.refuse(where, "names no run: a check runs a reference")
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
        record = CheckRecord(
            tool,
            role,
            self.reference(entry["run"], (*where, "run")),
            scope=str(entry.get("scope", "workspace")),
            narrowing=str(entry.get("narrowing", "none")),
            transport=str(entry.get("transport", "argv")),
            threshold=float(entry.get("threshold", 1.0)),
            fix=self.reference(entry["fix"], (*where, "fix"))
            if "fix" in entry
            else None,
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


def contract_keys_of(data: dict[str, Any], path: Path) -> tuple[Declared, ...]:
    """The contract keys *data*'s ``[contract]`` tables declare, judged."""
    found: list[Declared] = []
    for contract, table in data.get("contract", {}).items():
        if contract not in CONTRACTS:
            raise DeclarationError(
                f"{path}: [contract.{contract}] names no contract; an extension"
                f" declares keys in {', '.join(CONTRACTS)}"
            )
        found += _keys(cast("ContractKind", contract), table, (), path)
    return tuple(found)


#: The keys a contract key's own declaration holds.
_OWN = ("types", "values", "doc")


def _keys(
    contract: ContractKind, table: dict[str, Any], parts: tuple[str, ...], path: Path
) -> list[Declared]:
    from livery.workshop._contract_keys import TYPES, Declared

    where = f"[contract.{contract}{''.join('.' + part for part in parts)}]"
    found: list[Declared] = []
    if any(key in table for key in _OWN):
        types = table.get("types")
        if not (
            isinstance(types, list)
            and types
            and all(kind in TYPES for kind in cast("list[object]", types))
        ):
            raise DeclarationError(
                f"{path}: {where} types is {types!r}; it lists one or more of"
                f" {', '.join(TYPES)}"
            )
        values = table.get("values", [])
        doc = table.get("doc", "")
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
        if key in _OWN:
            continue
        if not isinstance(value, dict) or "." in key:
            raise DeclarationError(
                f"{path}: {where} {key} is not a key's declaration; a key is a"
                " table with types, and values and doc where it has them"
            )
        found += _keys(contract, cast("dict[str, Any]", value), (*parts, key), path)
    return found
