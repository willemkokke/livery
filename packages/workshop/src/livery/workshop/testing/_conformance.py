"""The conformance clauses, the subject they judge, and the violations they name."""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import inspect
import io
import sys
import tempfile
import threading
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from livery.workshop import _checks
from livery.workshop._categories import (
    CategoryError,
    CategoryRule,
    category_of,
    category_rules,
    matches,
)
from livery.workshop._checks import (
    BASE_EXTENSION,
    WORKSPACE,
    CheckRecord,
    GateContext,
    answering,
    checks_by_name,
)
from livery.workshop._extensions import FOR_ATTRIBUTE
from livery.workshop._fragments import Fragment, package_fragment
from livery.workshop._kinds import Backend, KindRecord, all_kinds, kind_chain
from livery.workshop._packages import Package


@dataclass(frozen=True)
class Subject:
    """What an extension registers, as the kit judges it.

    The records are the ones the extension registered, and the kit reads
    the registries as they stand, so the extension's registrations run
    before the kit does.

    Attributes:
        extension: The extension's import path, ``acme.extension``: its plugin
            module, where the kit reads what the extension declares, its
            contributions to other extensions among them.
        kinds: The kinds the extension registers, and the kinds whose
            category tables it extends.
        checks: The checks the extension registers.
    """

    extension: str
    kinds: tuple[KindRecord, ...] = ()
    checks: tuple[CheckRecord, ...] = ()


@dataclass(frozen=True)
class Violation:
    """One way a subject breaks a clause.

    Attributes:
        clause: The clause's name.
        where: What the violation concerns: a kind, a check, a file or
            a path.
        reason: What is wrong, and what the gate does because of it.
    """

    clause: str
    where: str
    reason: str

    def __str__(self) -> str:
        """The violation as one line: clause, what it concerns, reason."""
        return f"{self.clause}: {self.where}: {self.reason}"


@dataclass(frozen=True)
class Clause:
    """One thing the gate relies on of what an extension registers.

    Attributes:
        name: The clause's name, ``backend-protocol``.
        rule: The rule in one sentence.
        judge: The violations a subject commits; empty when it conforms.
    """

    name: str
    rule: str
    judge: Callable[[Subject], list[Violation]]


BACKEND_PROTOCOL = "backend-protocol"
NEAREST_FRAGMENT = "nearest-fragment"
CATEGORY_TABLE = "category-table"
CHECK_ORDER = "check-order"
CONTRIBUTION_MODULES = "contribution-modules"
FRAGMENT_DRIFT = "fragment-drift"
WITHDRAWN_FILE = "withdrawn-file"
WALK_ORDER = "walk-order"
GATE_LINES = "gate-lines"


# The backend protocol.


def _protocol_methods() -> dict[str, inspect.Signature]:
    """The backend protocol's methods by name, read from the protocol itself."""
    return {
        name: inspect.signature(member)
        for name, member in vars(Backend).items()
        if not name.startswith("_") and inspect.isfunction(member)
    }


def _signature_problems(
    wanted: inspect.Signature, found: inspect.Signature
) -> list[str]:
    """How *found* fails to take every call *wanted* allows; empty when it does."""
    problems: list[str] = []
    wanted_params = [p for p in wanted.parameters.values() if p.name != "self"]
    found_params = dict(found.parameters)
    variadic = {p.kind for p in found_params.values()} & {
        inspect.Parameter.VAR_POSITIONAL,
        inspect.Parameter.VAR_KEYWORD,
    }
    positional = (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    )
    wanted_order = [
        p.name
        for p in wanted_params
        if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    ]
    found_order = [p.name for p in found_params.values() if p.kind in positional]
    if found_order[: len(wanted_order)] != wanted_order and not variadic:
        problems.append(
            f"takes ({', '.join(found_order)}) where the protocol passes"
            f" ({', '.join(wanted_order)}) in that order"
        )
    for param in wanted_params:
        own = found_params.get(param.name)
        if own is None:
            if param.kind is inspect.Parameter.KEYWORD_ONLY and not variadic:
                problems.append(
                    f"does not take {param.name!r}, which the protocol passes"
                )
            continue
        if param.kind is inspect.Parameter.KEYWORD_ONLY and own.kind is (
            inspect.Parameter.POSITIONAL_ONLY
        ):
            problems.append(
                f"takes {param.name!r} by position only; the protocol passes it by name"
            )
        if param.default is not inspect.Parameter.empty and (
            own.default is inspect.Parameter.empty
        ):
            problems.append(
                f"requires {param.name!r}, which the protocol lets a caller leave out"
            )
    names = {p.name for p in wanted_params}
    for name, own in found_params.items():
        if name in names or own.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        if own.default is inspect.Parameter.empty:
            problems.append(f"requires {name!r}, which the protocol never passes")
    return problems


def _backend_protocol(subject: Subject) -> list[Violation]:
    violations: list[Violation] = []
    methods = _protocol_methods()
    for kind in subject.kinds:
        if kind.abstract:
            continue
        where = f"kind {kind.name}"
        backend = kind.backend
        if backend is None:
            violations.append(
                Violation(
                    BACKEND_PROTOCOL,
                    where,
                    "names no backend; a concrete kind builds, tests and"
                    " releases through one",
                )
            )
            continue
        for name, wanted in methods.items():
            found: object = getattr(backend, name, None)
            if not callable(found):
                violations.append(
                    Violation(
                        BACKEND_PROTOCOL,
                        f"{where} backend",
                        f"defines no {name}(), which the gate calls for the"
                        " kind's packages",
                    )
                )
                continue
            for problem in _signature_problems(wanted, inspect.signature(found)):
                violations.append(
                    Violation(BACKEND_PROTOCOL, f"{where} backend {name}()", problem)
                )
    return violations


# A fragment per kind down the chain.


def _fragment_owners(kind: str, file: str) -> list[str]:
    """The checks carrying a fragment for *file* for *kind* itself, by name."""
    return sorted(
        name
        for name, record in checks_by_name().items()
        for fragment in record.fragments
        if fragment.file == file and fragment.kind == kind
    )


def _kinds_in_play(subject: Subject) -> list[str]:
    """The subject's kinds and the kinds its checks' fragments are for, in order."""
    names = [kind.name for kind in subject.kinds]
    names += [
        fragment.kind
        for record in subject.checks
        for fragment in record.fragments
        if fragment.kind
    ]
    known = {kind.name for kind in all_kinds()}
    return [name for name in dict.fromkeys(names) if name in known]


def _nearest_fragment(subject: Subject) -> list[Violation]:
    violations: list[Violation] = []
    files = sorted(
        {
            fragment.file
            for record in checks_by_name().values()
            for fragment in record.fragments
            if fragment.kind
        }
    )
    for kind in _kinds_in_play(subject):
        nearest_first = [record.name for record in reversed(kind_chain(kind))]
        for file in files:
            owners = _fragment_owners(kind, file)
            if len(owners) > 1:
                violations.append(
                    Violation(
                        NEAREST_FRAGMENT,
                        f"kind {kind} {file}",
                        f"{' and '.join(owners)} both carry it for the kind; the"
                        " render would pick one by name, so one of them yields",
                    )
                )
            nearest = next(
                (name for name in nearest_first if _fragment_owners(name, file)), None
            )
            if nearest is None:
                continue
            wanted = _fragment_owners(nearest, file)[0]
            found = package_fragment(kind, file)
            if found is None or found[1] != wanted:
                rendered = "nothing" if found is None else f"{found[1]}'s fragment"
                violations.append(
                    Violation(
                        NEAREST_FRAGMENT,
                        f"kind {kind} {file}",
                        f"renders {rendered}; the nearest kind with one is"
                        f" {nearest}, whose {wanted} renders it",
                    )
                )
    return violations


# The category tables.


def _representative(pattern: str) -> str:
    """A path *pattern* claims: each ``**`` one directory, each wildcard one letter."""
    parts = [
        "a" if segment == "**" else segment.replace("*", "x").replace("?", "y")
        for segment in pattern.split("/")
    ]
    return "/".join(parts)


def _documented_answer(kind: str, path: str) -> list[CategoryRule]:
    """The rule the documented order picks for *path*, or the rules that tie.

    The most specific pattern wins; between kinds of equal specificity
    the nearer kind wins; two rules of one kind and one specificity
    claiming *path* for different categories or extensions tie.
    """
    nearest_first = [record.name for record in reversed(kind_chain(kind))]
    matching = [rule for rule in category_rules(kind) if matches(rule.pattern, path)]
    if not matching:
        return []
    top = max(rule.specificity for rule in matching)
    best = [rule for rule in matching if rule.specificity == top]
    depth = min(nearest_first.index(rule.kind) for rule in best)
    at_nearest = [rule for rule in best if nearest_first.index(rule.kind) == depth]
    first = at_nearest[0]
    if any(
        rule.category != first.category or rule.extension != first.extension
        for rule in at_nearest
    ):
        return at_nearest
    return [first]


def _category_table(subject: Subject) -> list[Violation]:
    violations: list[Violation] = []
    for kind in subject.kinds:
        probe = Package(Path("."), "packages/probe", "probe", kind.name, ())
        for rule in category_rules(kind.name):
            path = _representative(rule.pattern)
            if not matches(rule.pattern, path):
                continue
            where = f"kind {kind.name} {path}"
            wanted = _documented_answer(kind.name, path)
            if len(wanted) > 1:
                first, second = wanted[0], wanted[1]
                violations.append(
                    Violation(
                        CATEGORY_TABLE,
                        where,
                        f"{first.pattern!r} ({first.category}, {first.extension}) and"
                        f" {second.pattern!r} ({second.category}, {second.extension})"
                        " claim it at one specificity for one kind; one of them"
                        " names the file more closely",
                    )
                )
                continue
            try:
                found = category_of(probe, path)
            except CategoryError as error:
                violations.append(
                    Violation(
                        CATEGORY_TABLE,
                        where,
                        f"refuses ({error}) where the nearer kind's rule decides",
                    )
                )
                continue
            rule_wanted = wanted[0]
            if (found.name, found.pattern) != (
                rule_wanted.category,
                rule_wanted.pattern,
            ):
                violations.append(
                    Violation(
                        CATEGORY_TABLE,
                        where,
                        f"answers {found.name} from {found.pattern!r}; the most"
                        f" specific rule of the nearest kind, {rule_wanted.kind},"
                        f" says {rule_wanted.category} from {rule_wanted.pattern!r}",
                    )
                )
    return list(dict.fromkeys(violations))


# The order of an extension's checks.


def _loop(start: str) -> list[str]:
    """The checks from *start* back to itself through ``after``; empty without one."""
    records = checks_by_name()

    def visit(name: str, path: list[str], seen: set[str]) -> list[str]:
        record = records.get(name)
        if record is None:
            return []
        for earlier in record.after:
            target = answering(earlier)
            if target is None:
                continue
            if target == start:
                return [*path, target]
            if target in seen:
                continue
            seen.add(target)
            found = visit(target, [*path, target], seen)
            if found:
                return found
        return []

    return visit(start, [], {start})


def _check_order(subject: Subject) -> list[Violation]:
    violations: list[Violation] = []
    for record in subject.checks:
        where = f"check {record.name}"
        for earlier in record.after:
            if answering(earlier) is None:
                violations.append(
                    Violation(
                        CHECK_ORDER,
                        where,
                        f"runs after {earlier}, which no registered check"
                        " answers to; the gate stops there",
                    )
                )
        loop = _loop(record.name)
        if loop:
            violations.append(
                Violation(
                    CHECK_ORDER,
                    where,
                    f"runs after itself through {', '.join(loop)}; the gate"
                    " would wait on it for ever",
                )
            )
    return violations


# An extension's contributions to other extensions.


def _imports(module: str) -> bool:
    """Whether *module* is imported already, or can be found without running it."""
    if module in sys.modules:
        return True
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _contribution_modules(subject: Subject) -> list[Violation]:
    if not _imports(subject.extension):
        return []
    declared: object = getattr(
        importlib.import_module(subject.extension), FOR_ATTRIBUTE, {}
    )
    if not isinstance(declared, dict) or not all(
        isinstance(target, str) and isinstance(module, str)
        for target, module in declared.items()  # pyright: ignore[reportUnknownVariableType]
    ):
        return [
            Violation(
                CONTRIBUTION_MODULES,
                f"extension {subject.extension}",
                f"{FOR_ATTRIBUTE} is not a map from a target extension's import path"
                " to a module; the mount refuses the extension",
            )
        ]
    return [
        Violation(
            CONTRIBUTION_MODULES,
            f"extension {subject.extension} for {target}",
            f"names {module}, which does not import; the mount refuses once"
            f" {target} is listed",
        )
        for target, module in declared.items()  # pyright: ignore[reportUnknownVariableType]
        if not _imports(str(module))  # pyright: ignore[reportUnknownArgumentType]
    ]


# An extension's configuration files, through the workshop's own render.


def _probe_answers(kind: str = "") -> dict[str, Any]:
    """The answers the kit renders an extension's fragments with.

    A workspace of one python member and one native member, so a
    fragment's loops over either run, rendering *kind*'s package files.
    """
    return {
        "packages": [
            {
                "dir": "probe",
                "name": "acme-probe",
                "dev": "acme-probe",
                "kind": "package-python",
            },
            {"dir": "native", "name": "acme-native", "kind": "package-cpp-conan"},
        ],
        "python_floor": "3.11",
        "namespace_package": "acme",
        "runner_prog": "fm",
        "project_name": "acme",
        "docs_site_url": "",
        "kind": kind,
    }


def _package_fragments(subject: Subject) -> list[tuple[CheckRecord, Fragment]]:
    """The subject's fragments for per-package files, each with its check."""
    return [
        (record, fragment)
        for record in subject.checks
        for fragment in record.fragments
        if fragment.kind
    ]


def _project_drift(subject: Subject) -> list[Violation]:
    """The composed project files, judged where the subject carries fragments."""
    from livery.workshop import _templates

    owners = sorted(
        record.name
        for record in subject.checks
        for fragment in record.fragments
        if fragment.file == "pyproject.toml"
    )
    if not owners:
        return []
    try:
        composed = _templates.compose_fragments(_probe_answers())
    except Exception as error:
        return [
            Violation(FRAGMENT_DRIFT, "the project files", f"do not render: {error}")
        ]
    try:
        tomllib.loads(composed.get("pyproject.toml", ""))
    except tomllib.TOMLDecodeError as error:
        return [
            Violation(
                FRAGMENT_DRIFT,
                "pyproject.toml",
                f"composed with the fragment of {', '.join(owners)}, it is not"
                f" TOML: {error}",
            )
        ]
    return []


def _package_drift(fragment: Fragment) -> str:
    """What goes wrong rendering *fragment* into a probe package; empty when nothing."""
    from livery.workshop import _shipped_files

    with tempfile.TemporaryDirectory() as scratch:
        member = Path(scratch)
        try:
            _shipped_files.settle_package(member, fragment.kind)
        except Exception as error:
            return f"does not render: {error}"
        drift = _shipped_files.judge_package(member, fragment.kind)
        if drift:
            return f"drifts from its own render: {drift[0]}"
        copy = member / fragment.file
        copy.write_bytes(b"# a hand edit\n" + copy.read_bytes())
        if not _shipped_files.judge_package(member, fragment.kind):
            return "a hand edit of the rendered file is not named as drift"
    return ""


def _fragment_drift(subject: Subject) -> list[Violation]:
    violations = _project_drift(subject)
    for record, fragment in _package_fragments(subject):
        problem = _package_drift(fragment)
        if problem:
            where = f"check {record.name} {fragment.file} for {fragment.kind}"
            violations.append(Violation(FRAGMENT_DRIFT, where, problem))
    return violations


def _withdraw(record: CheckRecord, fragment: Fragment, *, edited: bool) -> str:
    """What goes wrong when *record* leaves a rendered file behind; empty when nothing.

    The render writes the file into a probe package, a person edits it
    or not, the check is withdrawn, and the render settles the file
    again. A file another check still renders for the kind is not
    withdrawn at all, and says nothing here.
    """
    from livery.workshop import _shipped_files

    if record.name not in checks_by_name():
        return ""
    state = _checks.snapshot()
    with tempfile.TemporaryDirectory() as scratch:
        member = Path(scratch)
        copy = member / fragment.file
        try:
            _shipped_files.settle_package(member, fragment.kind)
            if not copy.is_file():
                return ""  # it did not render: the drift clause names that
            if edited:
                copy.write_bytes(b"# a hand edit\n" + copy.read_bytes())
            _checks.unregister_check(record.name)
            if package_fragment(fragment.kind, fragment.file) is not None:
                return ""
            _shipped_files.settle_package(member, fragment.kind)
        except Exception:
            return ""  # a render that fails is the drift clause's to name
        finally:
            _checks.restore(state)
        if edited and not copy.is_file():
            return (
                "an edited copy is removed once the check is withdrawn;"
                " contract 11 keeps it as a local override"
            )
        if not edited and copy.is_file():
            return (
                "an unedited copy stays once the check is withdrawn; contract 11"
                " removes it"
            )
    return ""


def _withdrawn_file(subject: Subject) -> list[Violation]:
    violations: list[Violation] = []
    for record, fragment in _package_fragments(subject):
        where = f"check {record.name} {fragment.file} for {fragment.kind}"
        for edited in (False, True):
            problem = _withdraw(record, fragment, edited=edited)
            if problem:
                violations.append(Violation(WITHDRAWN_FILE, where, problem))
    return violations


# The gate's walk over an extension's checks.


class _Recorder:
    """The calls a probe walk makes into the subject's checks, in order."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self._lock = threading.Lock()

    def body(self, mode: str, name: str) -> Callable[[GateContext], None]:
        def record(ctx: GateContext) -> None:
            del ctx
            with self._lock:
                self.calls.append((mode, name))

        return record


#: A file of the probe workspace no check's claims reach.
_UNCLAIMED = "probe/unclaimed.probe"


def _probe_packages(root: Path) -> tuple[Package, ...]:
    """One probe package of each concrete kind, so a package check finds its own."""
    return tuple(
        Package(
            root / "packages" / kind.name,
            f"packages/{kind.name}",
            f"probe-{kind.name}",
            kind.name,
            (),
        )
        for kind in all_kinds()
        if not kind.abstract
    )


def _probed(record: CheckRecord, packages: tuple[Package, ...]) -> bool:
    """Whether the probe workspace gives *record* something to judge."""
    if record.scope == WORKSPACE:
        return True
    return any(
        kind in {link.name for link in kind_chain(package.kind)}
        for package in packages
        for kind in record.kinds
    )


def _probe_walk(
    subject: Subject, *, fix: bool, named: bool
) -> tuple[list[tuple[str, str]], str, tuple[Package, ...]]:
    """Walk the gate over a probe workspace with the subject's checks recording.

    The checks' bodies are replaced by recorders and their ``after``
    lists emptied, so no tool runs; the registry is restored after.
    *named* walks over one file no claim reaches; otherwise the walk
    is whole and the checks' claims are emptied, so every check reads
    whatever git lists around the probe. Returns the calls, the gate's
    output, and the probe's packages.
    """
    from livery.workshop import _quality

    recorder = _Recorder()
    names = [
        record.name for record in subject.checks if record.name in checks_by_name()
    ]
    state = _checks.snapshot()
    out = io.StringIO()
    try:
        for record in subject.checks:
            if record.name not in names:
                continue
            _checks.register_check(
                replace(
                    record,
                    run=recorder.body("run", record.name),
                    fix=None
                    if record.fix is None
                    else recorder.body("fix", record.name),
                    after=(),
                    claims=record.claims if named else (),
                )
            )
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root / _UNCLAIMED).parent.mkdir(parents=True)
            (root / _UNCLAIMED).write_text("probe\n")
            packages = _probe_packages(root)
            ctx = GateContext(
                root=root,
                packages=packages,
                fix=fix,
                files=(_UNCLAIMED,) if named else (),
            )
            with contextlib.redirect_stdout(out):
                _quality.walk(ctx, only=frozenset(names))
    finally:
        _checks.restore(state)
    return recorder.calls, out.getvalue(), packages


def _walk_order(subject: Subject) -> list[Violation]:
    calls, _out, packages = _probe_walk(subject, fix=True, named=False)
    first_judge = next(
        (index for index, (mode, _name) in enumerate(calls) if mode == "run"), None
    )
    violations: list[Violation] = []
    for record in subject.checks:
        if record.name not in checks_by_name():
            continue
        where = f"check {record.name}"
        fixes = [
            index for index, call in enumerate(calls) if call == ("fix", record.name)
        ]
        judged = ("run", record.name) in calls
        if record.fix is not None:
            if not fixes:
                violations.append(
                    Violation(WALK_ORDER, where, "never rewrote under --fix")
                )
            elif first_judge is not None and fixes[-1] > first_judge:
                violations.append(
                    Violation(
                        WALK_ORDER,
                        where,
                        "rewrote after a judge started; every fixer runs before any"
                        " judge",
                    )
                )
            if judged:
                violations.append(
                    Violation(
                        WALK_ORDER,
                        where,
                        "judged after it rewrote; a check that rewrote is not judged"
                        " again",
                    )
                )
        elif not judged and _probed(record, packages):
            violations.append(Violation(WALK_ORDER, where, "never judged under --fix"))
    return violations


def _gate_lines(subject: Subject) -> list[Violation]:
    calls, out, _packages = _probe_walk(subject, fix=False, named=True)
    lines = out.splitlines()
    violations: list[Violation] = []
    for record in subject.checks:
        if record.name not in checks_by_name():
            continue
        where = f"check {record.name}"
        if (
            subject.extension != BASE_EXTENSION
            and record.extension != subject.extension
        ):
            violations.append(
                Violation(
                    GATE_LINES,
                    where,
                    f"names {record.extension} as its extension; the gate says who"
                    f" registered a check by it, and {subject.extension} registered"
                    " this one",
                )
            )
        elif (
            record.extension != BASE_EXTENSION
            and f"  {record.name}: registered by {record.extension}" not in lines
        ):
            violations.append(
                Violation(
                    GATE_LINES,
                    where,
                    f"the gate does not name {record.extension} as the extension that"
                    " registered it",
                )
            )
        said = f"  {record.name}: no file it reads in the named files; not run"
        if said not in lines:
            violations.append(
                Violation(
                    GATE_LINES,
                    where,
                    'not named when it had no file to read; the gate says "no file'
                    ' it reads" and starts nothing',
                )
            )
        if any(name == record.name for _mode, name in calls):
            violations.append(
                Violation(GATE_LINES, where, "started with no file to read")
            )
    return violations


CLAUSES: tuple[Clause, ...] = (
    Clause(
        BACKEND_PROTOCOL,
        "A concrete kind's backend defines every method of the backend"
        " protocol and takes every call the protocol allows.",
        _backend_protocol,
    ),
    Clause(
        NEAREST_FRAGMENT,
        "A per-package configuration file resolves per kind down the kind"
        " chain, the nearest kind's fragment winning, and one kind has one"
        " owner per file.",
        _nearest_fragment,
    ),
    Clause(
        CATEGORY_TABLE,
        "A path's category is the most specific rule's, the nearer kind"
        " winning a tie between kinds, and two rules of one kind never tie.",
        _category_table,
    ),
    Clause(
        CHECK_ORDER,
        "Every check a check runs after is registered, and following the"
        " checks it runs after never leads back to it.",
        _check_order,
    ),
    Clause(
        CONTRIBUTION_MODULES,
        "An extension declares its contributions to other extensions as a map from a"
        " target extension's import path to a module that imports.",
        _contribution_modules,
    ),
    Clause(
        FRAGMENT_DRIFT,
        "A check's fragments render, the composed pyproject.toml parses, and a"
        " per-package file matches its render until a person edits it, when"
        " the drift gate names it.",
        _fragment_drift,
    ),
    Clause(
        WITHDRAWN_FILE,
        "A per-package file whose check is withdrawn, and which no other check"
        " renders for the kind, is removed when unedited and kept when edited.",
        _withdrawn_file,
    ),
    Clause(
        WALK_ORDER,
        "Under --fix every check with a fix mode rewrites before any judge"
        " starts and is not judged again, and every other check is judged.",
        _walk_order,
    ),
    Clause(
        GATE_LINES,
        "Every check names the extension that registered it, which the gate prints,"
        " and a check with no file to read is named and never started.",
        _gate_lines,
    ),
)
"""Every clause, in the order the kit judges them."""


def judge(subject: Subject) -> list[Violation]:
    """Every violation *subject* commits, clause by clause; empty when it conforms."""
    return [violation for clause in CLAUSES for violation in clause.judge(subject)]


def builtin_subject() -> Subject:
    """The workshop's own kinds and checks as a subject, for its own suite."""
    return Subject(
        BASE_EXTENSION,
        kinds=all_kinds(),
        checks=tuple(
            record
            for record in checks_by_name().values()
            if record.extension == BASE_EXTENSION
        ),
    )
