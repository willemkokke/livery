"""Read an extension's declaration file, ``extension.toml``, without importing it.

An extension declares itself in one ``extension.toml``, beside the
package its ``workshop.extensions`` entry point names: the workshop API
version it is written for, the levels it may be listed at, its plugin,
the extensions it requires, the phases it declares and the steps it
adds to a package's lifecycle phases and to the gate's phases, the
queries it declares and the ones it answers, the tools its verbs
need, its CI jobs, the contract keys it owns, what it adds to
another extension while that one is listed, and the earlier
extensions' shipped files it replaces or deletes. The file is a
contract, read as ``workshop.toml`` is: kebab-case keys, every key
judged, every refusal naming the file.

A ``[phases.<phase>]`` table declares the phase when it carries
``level``, ``gated``, ``after``, ``verb`` or ``keys``
([livery.workshop._phases.PhaseSpec][]), in the grammar the base's own
contract file declares its phases in; a phase no declaration names
refuses naming the declared ones. Beside those keys the table holds
the phase's steps, and nothing else.

A ``[queries.<name>]`` table declares the query when it carries
``combine``, ``level``, ``default`` or ``values``
([livery.workshop._queries.QuerySpec][]), in the grammar the base's own
contract file declares its queries in; a table carrying ``value`` is a
literal answer, and ``<name> = "module:function"`` under ``[queries]``
answers by a function. An answer to a query no declaration names
refuses naming the declared ones. A ``[for.<target>.queries]`` table
answers in the same grammar, asked while the target is listed, and
declares no query. A ``[for.<target>.fragments]`` table writes its files
while the target is listed, and so are the files the extension ships
under ``content/for/<target>/``; a directory there for a target the file
declares no ``[for.<target>]`` table for refuses, naming both. A
``[for.<target>.toolroom]`` table's ``requires`` names the tools the
workspace requires while the table applies.

``[extension] dormant-until`` names, as a verb, what another extension
does for this one by declaring a ``[for.<this>]`` table: until a listed
extension does, this one is dormant
([livery.workshop._extensions.dormancy][]).

A step is declared at ``[phases.<phase>.<step>]`` and reads into one
record whatever its phase's level
([livery.workshop._steps.StepRecord][]): a workspace-level phase's
steps register at mount, and a package-level phase's run over each
package whose set holds the extension. A step whose verdict is its
tool's exit code is its tool's words (``main`` as a template, with
``fix``, ``safe-fix``, ``env`` and ``matrix``), which the engine runs
([livery.workshop._words.Command][]). Other behaviour is a reference,
``"module:function"``, imported when it runs. The read imports
nothing. A reference whose module is not in the extension's package,
or whose module does not define the name at its top level, refuses
here, read from the module's source. A ``[checks]`` table or an
``[options]`` table refuses naming the step spelling, as does a key
of a step table that the spelling dropped, ``pre`` and ``post`` among
them; a ``[root-files]`` table refuses naming the fragments spelling,
and a ``[rules]`` table the layering phase's steps.
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
    from collections.abc import Callable, Mapping

    from livery.workshop._contract_keys import ContractKind, Declared, Type
    from livery.workshop._influence import Inputs
    from livery.workshop._phases import PhaseContext, PhaseSpec
    from livery.workshop._points import JobContribution
    from livery.workshop._queries import QuerySpec
    from livery.workshop._steps import StepRecord
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
class CodedFix:
    """A step's rewrite in code with a safe mode of its own, run as the record's fix.

    Attributes:
        fix: The function run under ``--fix``.
        safe_fix: The function run under ``--safe-fix`` instead, which
            removes no code.
    """

    fix: Reference
    safe_fix: Reference

    def __call__(self, ctx: PhaseContext) -> None:
        """Run the mode the run asks for: the safe one under ``--safe-fix``."""
        (self.safe_fix if ctx.safe else self.fix)(ctx)

    def __str__(self) -> str:
        """Both references, as the declaration spells them."""
        return f"{self.fix} (safe-fix: {self.safe_fix})"


@dataclass(frozen=True)
class Additions:
    """What a declaration adds while it is mounted.

    Attributes:
        steps: The steps of the ``[phases.<phase>.<step>]`` tables of
            the workspace-level phases, as records under the
            extension's name, which the mount registers.
        jobs: The CI jobs it adds to the builtin points, with their entries.
    """

    steps: tuple[StepRecord, ...] = ()
    jobs: tuple[JobContribution, ...] = ()


@dataclass(frozen=True)
class DeclaredValue:
    """A literal answer to a query: ``[queries.<name>] value = ...``.

    Attributes:
        value: The answer, as the file spells it.
    """

    value: object


@dataclass(frozen=True)
class DeclaredOutput:
    """A file an extension's code writes: a ``[fragments."<target>"]`` table.

    A package-level extension's file is written while a package lists
    the extension and withdrawn with the last package that did.

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
        dormant_until: What another extension does for this one by
            declaring a ``[for.<this>]`` table, a verb; empty for an
            extension that is never dormant.
        tools: The tools its verbs need, as requirement strings.
        additions: What it adds whenever it is mounted.
        targets: What it adds to each target while the target is listed.
        target_tables: Each ``[for.<target>]`` table as the file holds it.
        tables: The file's top-level tables but ``[extension]`` and
            ``[for]``, as the file holds them.
        replaces: Each earlier extension's shipped file, ``<owner>:<name>``,
            its own file of that name replaces, to the reason.
        deletes: Each earlier extension's shipped file it deletes, to the
            reason.
        fragments: The files its code writes, by target.
        target_fragments: The files each ``[for.<target>]`` table's code
            writes, by target; a target whose table writes none is left
            out.
        target_tools: The tools each ``[for.<target>]`` table requires
            under ``[toolroom] requires``, by target, as requirement
            strings; a target whose table requires none is left out.
        phase_specs: The phases it declares, by name.
        phases: The steps its top-level ``[phases.<phase>.<step>]``
            tables add, by phase, each the one step record whatever
            the phase's level: a workspace-level phase's are among
            ``additions`` too, which the mount registers; a
            package-level phase's run through the walk of a package's
            phase ([livery.workshop._phases.run_phase][]).
        query_specs: The queries it declares, by name.
        queries: The queries it answers, each query's name to the
            function that answers it or to its literal answer.
        target_queries: The queries each ``[for.<target>]`` table
            answers, by target, in the shape of ``queries``; a target
            whose table answers none is left out.
        categories: The categories it gives its packages' paths, each
            category to the patterns it takes, relative to the package.
    """

    extension: str
    package: str
    path: Path
    api_version: int = API_VERSION
    levels: tuple[str, ...] = ("workspace",)
    plugin: str = ""
    requires: tuple[str, ...] = ()
    dormant_until: str = ""
    tools: tuple[str, ...] = ()
    additions: Additions = Additions()
    targets: dict[str, Additions] = field(default_factory=dict[str, Additions])
    target_tables: dict[str, dict[str, Any]] = field(
        default_factory=dict[str, dict[str, Any]]
    )
    tables: dict[str, Any] = field(default_factory=dict[str, Any])
    replaces: dict[str, str] = field(default_factory=dict[str, str])
    deletes: dict[str, str] = field(default_factory=dict[str, str])
    fragments: tuple[DeclaredOutput, ...] = ()
    target_fragments: dict[str, tuple[DeclaredOutput, ...]] = field(
        default_factory=dict[str, tuple[DeclaredOutput, ...]]
    )
    target_tools: dict[str, tuple[str, ...]] = field(
        default_factory=dict[str, tuple[str, ...]]
    )
    phase_specs: dict[str, PhaseSpec] = field(default_factory=dict[str, "PhaseSpec"])
    phases: dict[str, tuple[StepRecord, ...]] = field(
        default_factory=dict[str, tuple["StepRecord", ...]]
    )
    query_specs: dict[str, QuerySpec] = field(default_factory=dict[str, "QuerySpec"])
    queries: dict[str, Reference | DeclaredValue] = field(
        default_factory=dict[str, "Reference | DeclaredValue"]
    )
    target_queries: dict[str, dict[str, Reference | DeclaredValue]] = field(
        default_factory=dict[str, "dict[str, Reference | DeclaredValue]"]
    )
    categories: tuple[tuple[str, tuple[str, ...]], ...] = ()


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


#: The tables whose keys the author names, a file or a query's answer or
#: a shipped file's reference or an environment variable: their keys are
#: data, never judged kebab-case.
_NAMED = (
    ("phases", "*", "*", "fragments"),
    ("for", "*", "phases", "*", "*", "fragments"),
    ("phases", "*", "*", "env"),
    ("for", "*", "phases", "*", "*", "env"),
    ("phases", "*", "*", "replaces"),
    ("phases", "*", "*", "deletes"),
    ("replaces",),
    ("for", "*", "replaces"),
    ("deletes",),
    ("for", "*", "deletes"),
    ("queries", "*", "default"),
    ("queries", "*", "value"),
    ("for", "*", "queries", "*", "value"),
    ("fragments",),
    ("for", "*", "fragments"),
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
    reader = _Reader(extension, package, path)
    reader.removed(data)
    reader.requires_alone(data.get("extension", {}))
    problems = judge(data, contract="extension", where=str(path), listed=None)
    if problems:
        raise DeclarationError(
            f"{path}:\n" + "\n".join(f"  {line}" for line in problems)
        )
    reader.shipped_for(data)
    own = phase_specs_of(data, path, extension=extension)
    reader.specs = _known_phases(own, path)
    own_queries = query_specs_of(data, path, extension=extension, package=package)
    reader.query_specs = _known_queries(own_queries, path)
    identity = data.get("extension", {})
    reader.levels = tuple(
        str(level) for level in identity.get("levels", ("workspace",))
    )
    phases = reader.steps(data, ())
    additions = reader.additions(data, (), steps=phases)
    targets = {
        str(target): reader.additions(table, ("for", target))
        for target, table in data.get("for", {}).items()
    }
    contract_keys_of(data, path)  # judged here as well, so a mount names it
    queries = reader.answers(data, (), identity)
    answered = {
        str(target): reader.answers(table, ("for", str(target)), identity)
        for target, table in data.get("for", {}).items()
    }
    if "executables" in queries and not phases.get("run"):
        raise reader.refuse(
            ("queries", "executables"),
            "names the executables fm run starts, and no [phases.run.<step>] starts"
            " them; add a step to the run phase",
        )
    return Declaration(
        extension,
        package,
        path,
        api_version=int(identity.get("api-version", API_VERSION)),
        levels=tuple(str(level) for level in identity.get("levels", ("workspace",))),
        plugin=str(identity.get("plugin", "")),
        requires=tuple(str(name) for name in identity.get("requires", ())),
        dormant_until=str(identity.get("dormant-until", "")),
        tools=tuple(str(text) for text in data.get("toolroom", {}).get("requires", ())),
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
        fragments=tuple(
            reader.output(str(target), table)
            for target, table in data.get("fragments", {}).items()
        ),
        target_fragments={
            str(target): tuple(
                reader.output(str(written), table, ("for", str(target)))
                for written, table in held["fragments"].items()
            )
            for target, held in data.get("for", {}).items()
            if held.get("fragments")
        },
        target_tools={
            str(target): tuple(str(text) for text in held["toolroom"]["requires"])
            for target, held in data.get("for", {}).items()
            if held.get("toolroom", {}).get("requires")
        },
        phase_specs=own,
        phases=phases,
        query_specs=own_queries,
        queries=queries,
        target_queries={target: found for target, found in answered.items() if found},
        categories=tuple(
            (str(category), tuple(str(pattern) for pattern in patterns))
            for category, patterns in data.get("categories", {}).items()
        ),
    )


class _Reader:
    """Build what one declaration file declares, refusing what does not resolve."""

    def __init__(self, extension: str, package: str, path: Path) -> None:
        self.extension = extension
        self.package = package
        self.path = path
        #: The declared phases a table of this file may name: every
        #: installed declaration's and this file's own.
        self.specs: dict[str, PhaseSpec] = {}
        #: The declared queries an answer in this file may name, the
        #: same way.
        self.query_specs: dict[str, QuerySpec] = {}
        #: The levels the extension may be listed at, from its identity.
        self.levels: tuple[str, ...] = ("workspace",)

    def refuse(self, where: tuple[str, ...], text: str) -> DeclarationError:
        from livery.workshop._contract_keys import shown

        return DeclarationError(f"{self.path}: {shown(where)} {text}")

    def spec(self, name: str, prefix: tuple[str, ...]) -> PhaseSpec:
        """The declared phase *name* a ``[phases.<name>]`` table names.

        Refuses one no declaration names, naming the declared ones and
        the nearest spelling.
        """
        found = self.specs.get(name)
        if found is None:
            near = difflib.get_close_matches(name, list(self.specs), n=1)
            hint = f"; did you mean {near[0]!r}?" if near else ""
            raise self.refuse(
                (*prefix, "phases", name),
                f"names no phase; the phases are {', '.join(self.specs)}{hint}",
            )
        return found

    def requires_alone(self, identity: object) -> None:
        """Refuse ``compatible``, ``before`` or ``after`` under ``[extension]``.

        A package's set is its list and what the list requires, so an
        extension names what it cannot work without in ``requires`` and
        composes by nothing else. Judged before the contract's keys, so
        the refusal names what composes instead of the nearest key.
        """
        if not isinstance(identity, dict):
            return
        for key in ("compatible", "before", "after"):
            if key in cast("dict[str, Any]", identity):
                raise self.refuse(
                    ("extension", key),
                    "is not a key: a package's set is its list and what the list"
                    " requires, so composition is requires alone; drop it, and name"
                    " in requires what this extension cannot work without",
                )

    def additions(
        self,
        data: dict[str, Any],
        prefix: tuple[str, ...],
        *,
        steps: Mapping[str, tuple[StepRecord, ...]] | None = None,
    ) -> Additions:
        """What *data* adds: the gate phases' steps and jobs.

        The steps are those of the workspace-level phases among
        *steps*, read from *data* when none are given; a package-level
        phase's steps run through the walk of a package's phase and
        register nothing. A step serving a phase's isolated side alone
        registers nothing either: the gate runs the tree side, and the
        release train runs that step through the walk of the package's
        phase.
        """
        from livery.workshop._phases import WORKSPACE_LEVEL

        found = steps if steps is not None else self.steps(data, prefix)
        registered = [
            record
            for phase, records in found.items()
            if self.specs[phase].level == WORKSPACE_LEVEL
            for record in records
            if record.isolated is not True
        ]
        jobs: list[JobContribution] = []
        for point, named in data.get("ci", {}).get("jobs", {}).items():
            for name, entry in named.items():
                jobs.append(
                    self.job(
                        str(point),
                        str(name),
                        entry,
                        (*prefix, "ci", "jobs"),
                        earlier=tuple(jobs),
                    )
                )
        return Additions(tuple(registered), tuple(jobs))

    def job(
        self,
        point: str,
        name: str,
        entry: dict[str, Any],
        prefix: tuple[str, ...],
        *,
        earlier: tuple[JobContribution, ...] = (),
    ) -> JobContribution:
        """The job *entry* adds to the builtin *point*, its functions resolved.

        A table naming a job the point already has, declared,
        inherited, contributed, or declared *earlier* in this file,
        joins that job: its entries and plumbing become the job's on
        this point. A join that ``gates`` refuses, since the verdict's
        needs are the job's own. A join's ``needs`` join the needs of a
        job the point has of its own; on a job the point inherits they
        refuse, since that job's needs are the point's it inherits
        from, whose runs never have the joining point's jobs. A job
        naming ``labels`` fans out to one leg per runner label the
        function names (a ``runners`` matrix). ``judges`` names the
        tasks that judge the run before the verdict, on a join of a
        point's own verdict job alone; on any other job they refuse
        ([livery.workshop._points.judges_refusal][]).
        """
        from livery.workshop._points import (
            DECLARED,
            Entry,
            Job,
            JobContribution,
            contributed_jobs,
            judges_refusal,
            point_has_job,
        )

        where = (*prefix, point, name)
        builtin = {declared.name: declared for declared in DECLARED}
        if point not in builtin:
            raise self.refuse(
                where,
                f"names the point {point!r}, which is not builtin; a job joins"
                f" one of {', '.join(builtin)}",
            )
        lineage = (point, builtin[point].inherits)
        joins = point_has_job(point, name) or any(
            item.point in lineage and item.job.name == name for item in earlier
        )
        if "judges" in entry and (why := judges_refusal(point, name)):
            raise self.refuse((*where, "judges"), why)
        if joins and entry.get("gates"):
            raise self.refuse(
                (*where, "gates"),
                f"joins the {point} point's {name} job and gates; a join adds"
                " entries and plumbing, and the verdict's needs stay the job's own",
            )
        own = {job.name for job in builtin[point].jobs}
        own |= {item.job.name for item in contributed_jobs(point)}
        own |= {
            item.job.name
            for item in earlier
            if item.point == point and not item.job.joins
        }
        if joins and "needs" in entry and name not in own:
            parent = builtin[point].inherits
            raise self.refuse(
                (*where, "needs"),
                f"joins the {name} job the {point} point inherits from the"
                f" {parent} point; that job waits for the {parent} point's jobs"
                " alone, so a join of it names no needs",
            )
        labels = (
            self.reference(entry["labels"], (*where, "labels"))
            if "labels" in entry
            else None
        )
        job = Job(
            name,
            matrix="runners" if labels is not None else "",
            labels=labels,
            needs=tuple(str(need) for need in entry.get("needs", ())),
            fetch=str(entry.get("fetch", "")),
            token=str(entry.get("token", "")),
            installs=self.reference(entry["installs"], (*where, "installs"))
            if "installs" in entry
            else None,
            conan_cache=bool(entry.get("conan-cache", False)),
            deploy=self.reference(entry["deploy"], (*where, "deploy"))
            if "deploy" in entry
            else None,
            publishes=str(entry.get("publishes", "")),
            paths=tuple(str(path) for path in entry.get("paths", ())),
            collects=str(entry.get("collects", "")),
            inputs=self.inputs(entry["inputs"], (*where, "inputs"))
            if "inputs" in entry
            else None,
            note=str(entry.get("note", "")),
            joins=joins,
        )
        return JobContribution(
            point,
            job,
            entries=tuple(
                Entry(point, name, str(task), source=self.extension)
                for task in entry.get("entries", ())
            )
            + tuple(
                Entry(point, name, str(task), source=self.extension, judges=True)
                for task in entry.get("judges", ())
            ),
            gates=bool(entry.get("gates", False)),
            extension=self.extension,
        )

    def removed(self, data: dict[str, Any]) -> None:
        """Refuse the check tables' spelling, naming the step's.

        A ``[checks.<step>.<phase>]`` table is the step
        ``[phases.<phase>.<step>]``, an ``[options]`` table is
        ``enabled`` on the step, a ``[root-files."<path>"]`` table is
        ``[fragments."<path>"]``, a ``[rules.<name>]`` table is
        ``[phases.layering.<name>]``, and a key the step table dropped
        says what says it now. Read before the contract judges the file,
        which would refuse the key as unknown without saying what
        replaced it. The tables under ``[for.<target>]`` are judged
        the same way.
        """
        tables: list[tuple[tuple[str, ...], dict[str, Any]]] = [((), data)]
        targets = data.get("for")
        if isinstance(targets, dict):
            tables += [
                (("for", target), cast("dict[str, Any]", table))
                for target, table in cast("dict[str, Any]", targets).items()
                if isinstance(table, dict)
            ]
        for prefix, table in tables:
            self.removed_tables(table, prefix)

    def removed_tables(self, data: dict[str, Any], prefix: tuple[str, ...]) -> None:
        """Refuse the check tables' spelling in *data*, one table of the file."""
        if "checks" in data:
            # The first check table names the step it is: [checks.<step>.<phase>]
            # is [phases.<phase>.<step>]; a bare table names the shape.
            where = [*prefix, "checks"]
            step, phase = "<step>", "<phase>"
            checks = data["checks"]
            if isinstance(checks, dict) and cast("dict[str, Any]", checks):
                step, phases = next(iter(cast("dict[str, Any]", checks).items()))
                where.append(step)
                named = (
                    [key for key, value in phases.items() if isinstance(value, dict)]
                    if isinstance(phases, dict)
                    else []
                )
                if named:
                    phase = str(named[0])
                    where.append(phase)
            raise self.refuse(
                tuple(where),
                f"is the check tables' spelling; a step is [phases.{phase}.{step}]"
                " with main, its body: a template, the tool's name then its"
                " arguments, or a reference to its code, module:function",
            )
        if "options" in data:
            raise self.refuse(
                (*prefix, "options"),
                "is not a table: a step a workspace turns on declares enabled = false"
                " under [phases.<phase>.<step>], and the workspace turns it on with"
                " [phases.<phase>.<step>] enabled = true in its root contract",
            )
        if "root-files" in data:
            # The first file names the fragment it is; a bare table
            # names the shape.
            where = [*prefix, "root-files"]
            path = "<path>"
            files = data["root-files"]
            if isinstance(files, dict) and cast("dict[str, Any]", files):
                path = next(iter(cast("dict[str, Any]", files)))
                where.append(path)
            raise self.refuse(
                tuple(where),
                f'is [fragments."{path}"]: a file the extension\'s code writes names'
                " its render there, module:function called with the workspace root,"
                " and a package-level extension's fragment lives while a package"
                " lists it",
            )
        if "rules" in data:
            # The first rule names the step it is; a bare table names
            # the shape.
            where = [*prefix, "rules"]
            step = "<step>"
            rules = data["rules"]
            if isinstance(rules, dict) and cast("dict[str, Any]", rules):
                step = next(iter(cast("dict[str, Any]", rules)))
                where.append(step)
            raise self.refuse(
                tuple(where),
                f"is [phases.layering.{step}]: a rule is a step of the layering"
                " phase, its main a reference to its code, module:function called"
                " with the gate's context over the one parse, and its fix the same"
                " shape where it rewrites",
            )
        phases = data.get("phases")
        if not isinstance(phases, dict):
            return
        for phase, table in cast("dict[str, Any]", phases).items():
            if not isinstance(table, dict):
                continue
            held = cast("dict[str, Any]", table)
            # The phase table's own body keys, pre and post among them:
            # a step named so is a table, and stays.
            for key in ("pre", "main", "post", "provides", "reads"):
                if key not in held or isinstance(held[key], dict):
                    continue
                if key in _REMOVED:
                    raise self.refuse(
                        (*prefix, "phases", phase, key),
                        _REMOVED[key].format(phase=phase, step="<step>"),
                    )
                raise self.refuse(
                    (*prefix, "phases", phase, key),
                    "belongs to a step: a phase's table holds steps,"
                    f" [phases.{phase}.<step>], each with main",
                )
            for step, entry in _step_tables(held).items():
                for key, spelling in _REMOVED.items():
                    if key in entry:
                        raise self.refuse(
                            (*prefix, "phases", phase, step, key),
                            spelling.format(phase=phase, step=step),
                        )

    def steps(
        self, data: dict[str, Any], prefix: tuple[str, ...]
    ) -> dict[str, tuple[StepRecord, ...]]:
        """Every step *data*'s ``[phases.<phase>.<step>]`` tables declare, by phase.

        A phase with no step is left out. A step of a package-level
        phase runs over the packages whose set holds the extension, so
        an extension listed at the workspace alone declares none.
        """
        from livery.workshop._phases import PACKAGE_LEVEL

        found: dict[str, tuple[StepRecord, ...]] = {}
        for phase, table in data.get("phases", {}).items():
            spec = self.spec(str(phase), prefix)
            named = _step_tables(table)
            if not named:
                continue
            if spec.level == PACKAGE_LEVEL and "package" not in self.levels:
                raise self.refuse(
                    (*prefix, "phases", str(phase)),
                    "adds steps to a package's phase, and this extension's levels"
                    " are workspace; add 'package' to levels, or drop the table",
                )
            found[str(phase)] = tuple(
                self.step(str(phase), step, entry, (*prefix, "phases"))
                for step, entry in named.items()
            )
        return found

    def step(
        self, phase: str, step: str, entry: dict[str, Any], prefix: tuple[str, ...]
    ) -> StepRecord:
        """The step ``[phases.<phase>.<step>]`` declares: a record placing its paths.

        ``main`` is the body: a template, the tool's name as ``tools``
        names it then its arguments, or a reference to its code,
        ``module:function``; ``fix`` and ``safe-fix`` take the body's
        shape. A template narrows by the paths the run reaches when
        ``{paths}`` stands among its words and runs whole without it,
        and takes the words after ``--`` where ``{arguments}`` stands.
        A step in code places neither, so its record says what its code
        does: a workspace step that declares no ``inputs`` narrows by
        the packages the run reaches, which its code asks
        [livery.workshop.scoped_packages][] for, and one with ``inputs``
        is selected by them and judges the whole; a step that names
        ``tools`` wraps them and takes the words after ``--``, and one
        that names none refuses them. ``isolated`` names the side of the
        phase the step serves, and refuses on a step of a phase whose
        declaration gives no ``isolated`` key.
        """
        from dataclasses import replace

        from livery.workshop._phases import ISOLATED, PACKAGE_LEVEL
        from livery.workshop._steps import NONE, PACKAGE, PACKAGES, PATHS, WORKSPACE

        where = (*prefix, phase, step)
        if ISOLATED in entry and ISOLATED not in self.specs[phase].keys:
            raise self.refuse(
                (*where, ISOLATED),
                f"is not read on a step of the {phase} phase, which has one side:"
                " its declaration gives no isolated key",
            )
        run, fix, words = self.step_bodies(entry, where)
        provides, reads = self.context_keys(entry, where, "another step")
        others = {
            key: self.other_steps(entry.get(key, {}), (*where, key))
            for key in ("replaces", "deletes")
        }
        record = self.record(step, phase, entry, where, run, fix, words)
        if self.specs[phase].level == PACKAGE_LEVEL:
            # The phase's level says how often the step runs: once per
            # package whose set holds the extension.
            for key in ("scope", "extensions"):
                if key in entry:
                    raise self.refuse(
                        (*where, key),
                        f"is not read on a step of the {phase} phase, which runs"
                        " once per package whose set holds the extension, as the"
                        " phase's level says",
                    )
            record = replace(record, scope=PACKAGE, extensions=(self.extension,))
        if words is not None:
            named = words.placeholders()
            narrowing = PATHS if "paths" in named else NONE
            arguments = "arguments" in named
        else:
            narrowing = (
                PACKAGES
                if record.scope == WORKSPACE and record.inputs is None
                else NONE
            )
            arguments = bool(record.tools)
        return replace(
            record,
            narrowing=narrowing,
            arguments=arguments,
            enabled=bool(entry.get("enabled", True)),
            provides=tuple(provides.items()),
            reads=reads,
            isolated=None if "isolated" not in entry else bool(entry["isolated"]),
            replaces=others["replaces"],
            deletes=others["deletes"],
        )

    def other_steps(
        self, table: dict[str, Any], where: tuple[str, ...]
    ) -> tuple[tuple[str, str], ...]:
        """The steps of other extensions *table* names, each to the reason it states.

        A step is ``<owner>:<phase>.<step>``, and the reason is a line
        of text; a step named with no reason refuses.
        """
        found: list[tuple[str, str]] = []
        for text, reason in table.items():
            if not _step_address(text):
                raise self.refuse(
                    where,
                    f"names {text!r}; a step of another extension is"
                    " <owner>:<phase>.<step>",
                )
            if not str(reason).strip():
                raise self.refuse(
                    (*where, text),
                    "states no reason; a step names another with the reason it"
                    " takes its place",
                )
            found.append((text, str(reason)))
        return tuple(found)

    def record(
        self,
        step: str,
        phase: str,
        entry: dict[str, Any],
        where: tuple[str, ...],
        run: Callable[[PhaseContext], None],
        fix: Callable[[PhaseContext], None] | None,
        words: Words | None,
    ) -> StepRecord:
        """The record from a step table's shared keys, its body resolved.

        ``scope``, ``claims``, ``extensions``, ``options``, ``fragments``,
        ``inputs``, ``after``, ``tools``, ``tests-only`` and ``flags``;
        ``step`` sets the step's own fields after.
        """
        from livery.workshop._fragments import verify
        from livery.workshop._steps import Claim, Option, StepRecord

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
        record = StepRecord(
            step,
            phase,
            run,
            scope=str(entry.get("scope", "workspace")),
            fix=fix,
            words=words,
            extensions=tuple(str(name) for name in entry.get("extensions", ())),
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
            claims=tuple(claims),
            flags=tuple(entry.get("flags", ())),
            inputs=inputs,
        )
        for option in record.options:
            if option.kind not in ("bool", "str", "int", "strs"):
                raise self.refuse(
                    (*where, "options", option.name, "type"),
                    "is required: bool, str, int or strs",
                )
        try:
            verify(record.fragments, record.name)
        except ValueError as error:
            raise self.refuse((*where, "fragments"), f"refuses: {error}") from error
        return record

    def step_bodies(
        self, entry: dict[str, Any], where: tuple[str, ...]
    ) -> tuple[
        Callable[[PhaseContext], None],
        Callable[[PhaseContext], None] | None,
        Words | None,
    ]:
        """A step's main and fix, from its template or from references to its code.

        ``main`` is a list, the template, or a string, the reference;
        ``fix`` and ``safe-fix`` take its shape, and ``env`` and
        ``matrix`` vary a template's calls alone. ``{paths}`` and
        ``{arguments}`` each stand alone as one word of a template.
        """
        from livery.workshop._words import PATHS_WORD, PLACED, Command

        if "main" not in entry:
            raise self.refuse(
                where,
                "names no main: a step's body is a template, the tool's name then"
                " its arguments, or a reference to its code, module:function",
            )
        if isinstance(entry["main"], list):
            words = self.words(entry, where)
            spelled = [
                ("main", words.judge),
                ("fix", words.fix),
                ("safe-fix", words.safe_fix),
                ("env", tuple(value for _name, value in words.env)),
            ]
            for key, texts in spelled:
                for text in texts:
                    for placed in PLACED:
                        if placed in text and text != placed:
                            goes = (
                                "the run's paths"
                                if placed == PATHS_WORD
                                else "the words after --"
                            )
                            raise self.refuse(
                                (*where, key),
                                f"names {placed} in {text!r}; it stands alone as one"
                                f" word, where {goes} go",
                            )
            name = f"{where[-2]}.{where[-1]}"
            fixing = Command(words, name, fixing=True) if words.fix else None
            return Command(words, name), fixing, words
        for key in ("env", "matrix"):
            if key in entry:
                raise self.refuse(
                    (*where, key),
                    "belongs to a step in words, and this one runs code; declare"
                    " main as words, the tool's name then its arguments, or drop"
                    " the key",
                )
        for key in ("fix", "safe-fix"):
            if key in entry and not isinstance(entry[key], str):
                raise self.refuse(
                    (*where, key),
                    "is words, and main is a reference to code: a step that runs"
                    " code fixes with code, module:function",
                )
        if "safe-fix" in entry and "fix" not in entry:
            raise self.refuse(
                (*where, "safe-fix"),
                "is declared without fix: a step that fixes safely fixes too",
            )
        coded: Callable[[PhaseContext], None] | None = None
        if "fix" in entry:
            fix = self.reference(entry["fix"], (*where, "fix"))
            coded = fix
            if "safe-fix" in entry:
                coded = CodedFix(
                    fix, self.reference(entry["safe-fix"], (*where, "safe-fix"))
                )
        return self.reference(entry["main"], (*where, "main")), coded, None

    def words(self, entry: dict[str, Any], where: tuple[str, ...]) -> Words:
        """The words *entry* declares: its main, fix, safe-fix, env and matrix."""
        from livery.workshop._words import Words

        def argv(key: str) -> tuple[str, ...]:
            value = entry.get(key, [])
            if isinstance(value, str):
                raise self.refuse(
                    (*where, key),
                    f"is {value!r}, a reference to code; in a step in words,"
                    f" {key} is words: the tool's name, then its arguments",
                )
            return tuple(str(word) for word in value)

        judge, fix, safe_fix = argv("main"), argv("fix"), argv("safe-fix")
        if not judge:
            raise self.refuse((*where, "main"), "is empty: the tool's name comes first")
        if safe_fix and not fix:
            raise self.refuse(
                (*where, "safe-fix"),
                "is declared without fix: a step that fixes safely fixes too",
            )
        for key, value in (("fix", fix), ("safe-fix", safe_fix)):
            if value and value[0] != judge[0]:
                raise self.refuse(
                    (*where, key),
                    f"runs {value[0]}, and main runs {judge[0]}: a step runs one tool",
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
        """A project file's fragment from its text; a package file's, its extensions."""
        from livery.workshop._fragments import Fragment

        found: list[Fragment] = []
        for file, value in table.items():
            if isinstance(value, str):
                found.append(Fragment(file, value))
                continue
            entry = cast("dict[str, Any]", value)
            if "text" not in entry or not entry.get("extensions"):
                raise self.refuse(
                    (*where, file),
                    "is a package file's fragment: its text, and the extensions"
                    " whose packages render it",
                )
            extensions = tuple(str(name) for name in entry["extensions"])
            found.append(Fragment(file, str(entry["text"]), extensions=extensions))
        return tuple(found)

    def output(
        self, target: str, table: dict[str, Any], prefix: tuple[str, ...] = ()
    ) -> DeclaredOutput:
        """The file *target* names, written by the render its table references.

        *prefix* is the ``[for.<target>]`` table the fragment is under,
        empty for the top level.
        """
        where = (*prefix, "fragments", target)
        if "render" not in table:
            raise self.refuse(
                where,
                "names no render; a fragment's code is render = 'module:function'",
            )
        return DeclaredOutput(
            target,
            self.reference(table["render"], (*where, "render")),
            local=bool(table.get("local", False)),
        )

    def shipped_for(self, data: dict[str, Any]) -> None:
        """Refuse ``content/for/<target>/`` for a target with no ``[for]`` table.

        The files under it are delivered while the extension contributes
        to the target, which a ``[for.<target>]`` table declares, so a
        directory for a target the file declares nothing for would never
        be delivered: a misspelled target, or a table that went.
        """
        from livery.workshop._contract_keys import shown

        shipped = self.path.parent / "content" / "for"
        if not shipped.is_dir():
            return
        declared = data.get("for", {})
        for held in sorted(shipped.iterdir()):
            if held.is_dir() and held.name not in declared:
                raise DeclarationError(
                    f"{self.path}: content/for/{held.name}/ ships files for"
                    f" {held.name}, and the file declares no"
                    f" [{shown(('for', held.name))}] table; declare one, or"
                    " move the files"
                )

    def context_keys(
        self, table: dict[str, Any], where: tuple[str, ...], provider: str
    ) -> tuple[dict[str, str], tuple[str, ...]]:
        """The context keys *table* provides, each to its type, and reads.

        A key in both refuses, naming *provider* as who provides what a
        step reads.
        """
        provides = {
            str(key): str(kind) for key, kind in table.get("provides", {}).items()
        }
        reads = tuple(str(key) for key in table.get("reads", ()))
        both = sorted(set(provides) & set(reads))
        if both:
            raise self.refuse(
                (*where, "reads"),
                f"names {', '.join(both)}, which its provides names too; a step"
                f" reads what {provider} provides",
            )
        return provides, reads

    def answers(
        self, data: dict[str, Any], prefix: tuple[str, ...], identity: dict[str, Any]
    ) -> dict[str, Reference | DeclaredValue]:
        """The queries *data*'s ``[queries]`` table answers, each to its answer.

        Under a ``[for.<target>]`` table, *prefix*, a table that declares
        a query refuses: a declaration holds whatever is listed, so it
        belongs to the file's top level.
        """
        from livery.workshop._contract_keys import shown

        found: dict[str, Reference | DeclaredValue] = {}
        for name, value in data.get("queries", {}).items():
            if not _declares_query(value):
                found[str(name)] = self.query(str(name), value, identity, prefix)
            elif prefix:
                raise self.refuse(
                    (*prefix, "queries", str(name)),
                    "declares a query, and a [for] table only answers one; declare"
                    f" it at the top level, [{shown(('queries', str(name)))}]",
                )
        return found

    def query(
        self,
        name: str,
        value: object,
        identity: dict[str, Any],
        prefix: tuple[str, ...] = (),
    ) -> Reference | DeclaredValue:
        """The answer to the query *name*: a function, resolved, or a literal.

        The name is a declared query. A package-level query is answered
        by the package's set alone, so an extension answers one only
        when a package may list it. A workspace-level query is asked of
        every extension the workspace mounts, a package-level one while
        a package holds it, so an extension of either level answers one.
        A literal answer is one of the query's values when it declares
        them. *prefix* is the ``[for.<target>]`` table the answer is
        under, empty for the top level.
        """
        from livery.workshop._queries import PACKAGE_LEVEL

        where = (*prefix, "queries", name)
        spec = self.query_specs.get(name)
        if spec is None:
            near = difflib.get_close_matches(name, list(self.query_specs), n=1)
            hint = f"; did you mean {near[0]!r}?" if near else ""
            raise self.refuse(
                where,
                f"names no query; the queries are {', '.join(self.query_specs)}{hint}",
            )
        levels = tuple(str(level) for level in identity.get("levels", ("workspace",)))
        if spec.level == PACKAGE_LEVEL and PACKAGE_LEVEL not in levels:
            raise self.refuse(
                where,
                "answers a question about a package, and this extension's levels"
                f" are {', '.join(levels)}; add {PACKAGE_LEVEL!r} to levels, or drop"
                " the answer",
            )
        if isinstance(value, dict):
            table = cast("dict[str, Any]", value)
            if "value" not in table:
                raise self.refuse(
                    where,
                    "is a table without value; an answer is 'module:function', or"
                    " a table with value alone",
                )
            held: object = table["value"]
            if spec.values is not None and held not in spec.values:
                listed = ", ".join(repr(known) for known in spec.values)
                raise self.refuse(
                    (*where, "value"),
                    f"is {held!r}; the query's values are {listed}",
                )
            return DeclaredValue(held)
        return self.reference(value, where)

    def inputs(self, table: dict[str, Any], where: tuple[str, ...]) -> Inputs:
        """The files a workspace step or a job reads, from its ``inputs`` table."""
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


#: The keys that declare a phase in its ``[phases.<phase>]`` table,
#: beside which a sub-table is a step.
_DECLARATION_KEYS = ("level", "gated", "after", "verb", "keys")


def phase_specs_of(
    data: dict[str, Any], path: Path, *, extension: str
) -> dict[str, PhaseSpec]:
    """The phases *data*'s ``[phases.<phase>]`` tables declare, judged, by name.

    A table declares its phase when it carries ``level``, ``gated``,
    ``after``, ``verb`` or ``keys``; one that adds steps alone declares
    nothing. The same reader serves the base's contract file and an
    extension's declaration file, *extension* naming the declarer.

    Raises:
        DeclarationError: when a declaration is off its shape: a level
            that is not ``package`` or ``workspace``, a key of a type
            no context value has, or a value of the wrong type.
    """
    from livery.workshop._contract_keys import shown
    from livery.workshop._phases import LEVELS, PACKAGE_LEVEL, TYPES, PhaseSpec

    def refuse(where: tuple[str, ...], text: str) -> DeclarationError:
        return DeclarationError(f"{path}: {shown(where)} {text}")

    found: dict[str, PhaseSpec] = {}
    phases = data.get("phases")
    if not isinstance(phases, dict):
        return found
    for name, table in cast("dict[str, Any]", phases).items():
        if not isinstance(table, dict):
            continue
        entry = cast("dict[str, Any]", table)
        if not any(key in entry for key in _DECLARATION_KEYS):
            continue
        where = ("phases", name)
        level = entry.get("level", PACKAGE_LEVEL)
        if level not in LEVELS:
            raise refuse(
                (*where, "level"),
                f"is {level!r}; a phase runs at one of {', '.join(LEVELS)}",
            )
        for key in ("gated", "verb"):
            if not isinstance(entry.get(key, False), bool):
                raise refuse((*where, key), "is true or false")
        after = entry.get("after", [])
        if not isinstance(after, list) or not all(
            isinstance(item, str) for item in cast("list[object]", after)
        ):
            raise refuse((*where, "after"), "is a list of the phases this one follows")
        keys = entry.get("keys", {})
        if not isinstance(keys, dict):
            raise refuse(
                (*where, "keys"), "is a table of the engine's keys, each to its type"
            )
        typed = cast("dict[str, Any]", keys)
        for key, kind in typed.items():
            if kind not in TYPES:
                raise refuse(
                    (*where, "keys", key),
                    f"is {kind!r}; a key's type is one of {', '.join(TYPES)}",
                )
        found[name] = PhaseSpec(
            name,
            keys={key: str(kind) for key, kind in typed.items()},
            level=str(level),
            gated=bool(entry.get("gated", False)),
            after=tuple(str(item) for item in cast("list[object]", after)),
            verb=bool(entry.get("verb", False)),
            extension=extension,
        )
    return found


def phase_declarations(extension: str, package: str) -> tuple[PhaseSpec, ...]:
    """The phases the declaration beside *package* declares, for *extension*.

    Empty without a declaration file. Read without judging the rest of
    the file, and without importing anything, so every installed
    extension's phases are known before any declaration is judged.

    Raises:
        DeclarationError: when the file does not parse or a phase's
            declaration is off its shape.
    """
    path = declaration_file(package)
    if path is None:
        return ()
    data = _parsed(path, path.read_text("utf-8"))
    return tuple(phase_specs_of(data, path, extension=extension).values())


#: The keys that declare a query in its ``[queries.<name>]`` table.
_QUERY_KEYS = ("combine", "level", "default", "values")


def _declares_query(entry: object) -> bool:
    """Whether a ``[queries.<name>]`` entry declares the query, not answers it."""
    return isinstance(entry, dict) and any(
        key in cast("dict[str, Any]", entry) for key in _QUERY_KEYS
    )


def query_specs_of(
    data: dict[str, Any], path: Path, *, extension: str, package: str
) -> dict[str, QuerySpec]:
    """The queries *data*'s ``[queries.<name>]`` tables declare, judged, by name.

    A table declares its query when it carries ``combine``, ``level``,
    ``default`` or ``values``; one carrying ``value`` answers a query
    instead. The same reader serves the base's contract file and an
    extension's declaration file, *extension* naming the declarer and
    *package* the package a ``combine`` function lives in.

    Raises:
        DeclarationError: when a declaration is off its shape: a
            ``combine`` that is no rule and no reference, a level that
            is not ``package`` or ``workspace``, ``values`` that are no
            list, or a table that declares and answers at once.
    """
    from livery.workshop._queries import LEVELS, PACKAGE_LEVEL, RULES, UNION, QuerySpec

    reader = _Reader(extension, package, path)
    found: dict[str, QuerySpec] = {}
    queries = data.get("queries")
    if not isinstance(queries, dict):
        return found
    for name, table in cast("dict[str, Any]", queries).items():
        if not _declares_query(table):
            continue
        entry = cast("dict[str, Any]", table)
        where = ("queries", name)
        if "value" in entry:
            raise reader.refuse(
                where,
                "declares the query and answers it in one table; a declaration"
                " takes default for the answer while nobody answers",
            )
        rule = entry.get("combine", UNION)
        if isinstance(rule, str) and rule in RULES:
            combine: str | Reference = rule
        elif isinstance(rule, str) and ":" in rule:
            combine = reader.reference(rule, (*where, "combine"))
        else:
            rules = ", ".join(repr(known) for known in RULES)
            raise reader.refuse(
                (*where, "combine"),
                f"is {rule!r}; a query combines its answers by {rules}, or a"
                " function 'module:name'",
            )
        level = entry.get("level", PACKAGE_LEVEL)
        if level not in LEVELS:
            raise reader.refuse(
                (*where, "level"),
                f"is {level!r}; a query is asked at one of {', '.join(LEVELS)}",
            )
        values = entry.get("values")
        if values is not None and not isinstance(values, list):
            raise reader.refuse(
                (*where, "values"), "is a list of the answers the query takes"
            )
        found[name] = QuerySpec(
            name,
            combine,
            level=str(level),
            default=entry.get("default"),
            values=tuple(cast("list[object]", values)) if values is not None else None,
            extension=extension,
        )
    return found


def query_declarations(extension: str, package: str) -> tuple[QuerySpec, ...]:
    """The queries the declaration beside *package* declares, for *extension*.

    Empty without a declaration file. Read without judging the rest of
    the file, and without importing anything, so every installed
    extension's queries are known before any declaration is judged.

    Raises:
        DeclarationError: when the file does not parse or a query's
            declaration is off its shape.
    """
    path = declaration_file(package)
    if path is None:
        return ()
    data = _parsed(path, path.read_text("utf-8"))
    return tuple(
        query_specs_of(data, path, extension=extension, package=package).values()
    )


def _known_queries(own: dict[str, QuerySpec], path: Path) -> dict[str, QuerySpec]:
    """Every declared query an answer in a file may name: the installed ones and *own*.

    Raises:
        DeclarationError: when *own* declares a query another extension
            declares.
    """
    from livery.workshop import _queries
    from livery.workshop._contract_keys import shown

    known = dict(_queries.declared_queries())
    for name, spec in own.items():
        held = known.get(name)
        if held is not None and held.extension != spec.extension:
            raise DeclarationError(
                f"{path}: {shown(('queries', name))} declares the {name} query, which"
                f" {held.extension} declares; one extension declares a query"
            )
        known[name] = spec
    return known


def _known_phases(own: dict[str, PhaseSpec], path: Path) -> dict[str, PhaseSpec]:
    """Every declared phase a file may name: the installed declarations' and *own*.

    Raises:
        DeclarationError: when *own* declares a phase another extension
            declares.
    """
    from livery.workshop import _phases

    known = dict(_phases.declared_phases())
    for name, spec in own.items():
        held = known.get(name)
        if held is not None and held.extension != spec.extension:
            raise DeclarationError(
                f"{path}: phases.{name} declares the {name} phase, which"
                f" {held.extension} declares; one extension declares a phase"
            )
        known[name] = spec
    return known


#: The keys a step table does not take, each to what says it in the
#: step's spelling, so a stale declaration is told what to write;
#: ``{phase}`` and ``{step}`` name the step.
_REMOVED: dict[str, str] = {
    "narrowing": "is said by {{paths}} in main: a template with it narrows by the"
    " paths the run reaches, one without runs whole",
    "transport": "goes: the paths go on the command line, where {{paths}} stands"
    " in main",
    "threshold": "goes: a template with {{paths}} runs over the paths the run"
    " reaches, and one without runs whole",
    "roles": "goes: a step belongs to one phase; declare a step under the other"
    " phase too",
    "listed-with": "is enabled = false on the step, which a workspace turns on"
    " with [phases.{phase}.{step}] enabled = true in its root contract",
    "judge": "is main: the step's body, a template or a reference",
    "run": "is main: the step's body, a template or a reference",
    "arguments": "is said by {{arguments}} in main, where the words after -- go",
    "editor-extension": "is the extension's answer to the editor-extensions query:"
    ' [queries.editor-extensions] value = ["<marketplace id>"]',
    "pre": "goes: a step is named, and a step that runs after it names it in after",
    "post": "goes: a step is named, runs after the steps it names in after, and"
    " does its own cleanup on failure",
}


def _step_tables(table: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The steps under a phase's table: its sub-tables beside the declaration keys."""
    return {
        key: cast("dict[str, Any]", value)
        for key, value in table.items()
        if key not in _DECLARATION_KEYS and isinstance(value, dict)
    }


def _step_address(text: str) -> bool:
    """Whether *text* is ``<owner>:<phase>.<step>``, its step one word."""
    owner, colon, address = text.partition(":")
    phase, dot, step = address.partition(".")
    return bool(owner and colon and phase and dot and step) and "." not in step


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
#: names a table is a key of its own, declared beneath: a query's
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
