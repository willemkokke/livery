"""The conformance clauses, the subject they judge, and the violations they name."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from livery.workshop._categories import (
    CategoryError,
    CategoryRule,
    category_of,
    category_rules,
    matches,
)
from livery.workshop._checks import (
    BASE_LAYER,
    CheckRecord,
    answering,
    checks_by_name,
)
from livery.workshop._fragments import package_fragment
from livery.workshop._kinds import Backend, KindRecord, all_kinds, kind_chain
from livery.workshop._layers import FOR_ATTRIBUTE
from livery.workshop._packages import Package


@dataclass(frozen=True)
class Subject:
    """What a layer registers, as the kit judges it.

    The records are the ones the layer registered, and the kit reads
    the registries as they stand, so the layer's registrations run
    before the kit does.

    Attributes:
        layer: The layer's import path, ``acme.layer``: its plugin
            module, where the kit reads what the layer declares, its
            contributions to other layers among them.
        kinds: The kinds the layer registers, and the kinds whose
            category tables it extends.
        checks: The checks the layer registers.
    """

    layer: str
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
    """One thing the gate relies on of what a layer registers.

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
    claiming *path* for different categories or layers tie.
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
        rule.category != first.category or rule.layer != first.layer
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
                        f"{first.pattern!r} ({first.category}, {first.layer}) and"
                        f" {second.pattern!r} ({second.category}, {second.layer})"
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


# The order of a layer's checks.


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


# A layer's contributions to other layers.


def _imports(module: str) -> bool:
    """Whether *module* is imported already, or can be found without running it."""
    if module in sys.modules:
        return True
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _contribution_modules(subject: Subject) -> list[Violation]:
    if not _imports(subject.layer):
        return []
    declared: object = getattr(
        importlib.import_module(subject.layer), FOR_ATTRIBUTE, {}
    )
    if not isinstance(declared, dict) or not all(
        isinstance(target, str) and isinstance(module, str)
        for target, module in declared.items()  # pyright: ignore[reportUnknownVariableType]
    ):
        return [
            Violation(
                CONTRIBUTION_MODULES,
                f"layer {subject.layer}",
                f"{FOR_ATTRIBUTE} is not a map from a target layer's import path"
                " to a module; the mount refuses the layer",
            )
        ]
    return [
        Violation(
            CONTRIBUTION_MODULES,
            f"layer {subject.layer} for {target}",
            f"names {module}, which does not import; the mount refuses once"
            f" {target} is listed",
        )
        for target, module in declared.items()  # pyright: ignore[reportUnknownVariableType]
        if not _imports(str(module))  # pyright: ignore[reportUnknownArgumentType]
    ]


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
        "A layer declares its contributions to other layers as a map from a"
        " target layer's import path to a module that imports.",
        _contribution_modules,
    ),
)
"""Every clause, in the order the kit judges them."""


def judge(subject: Subject) -> list[Violation]:
    """Every violation *subject* commits, clause by clause; empty when it conforms."""
    return [violation for clause in CLAUSES for violation in clause.judge(subject)]


def builtin_subject() -> Subject:
    """The workshop's own kinds and checks as a subject, for its own suite."""
    return Subject(
        BASE_LAYER,
        kinds=all_kinds(),
        checks=tuple(
            record for record in checks_by_name().values() if record.layer == BASE_LAYER
        ),
    )
