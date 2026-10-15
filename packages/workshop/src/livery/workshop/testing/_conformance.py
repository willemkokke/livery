"""The conformance clauses, the subject they judge, and the violations they name."""

from __future__ import annotations

import contextlib
import io
import re
import tempfile
import threading
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from livery.workshop import _steps
from livery.workshop._categories import (
    BASE_TABLE,
    CategoryError,
    CategoryRule,
    category_of,
    category_rules,
    matches,
)
from livery.workshop._composition import extension_set
from livery.workshop._fragments import Fragment, package_fragment
from livery.workshop._packages import Package
from livery.workshop._phases import PhaseContext
from livery.workshop._steps import (
    BASE_EXTENSION,
    WORKSPACE,
    StepRecord,
    answering,
    installed_step,
    steps_by_name,
)

if TYPE_CHECKING:
    from livery.workshop._declaration import (
        Additions,
        Declaration,
        DeclaredTemplate,
        Reference,
    )


@dataclass(frozen=True)
class Subject:
    """What an extension registers, as the kit judges it.

    The records are the ones the extension registered, and the kit reads
    the registries as they stand, so the extension's registrations run
    before the kit does.

    Attributes:
        extension: The extension's package, ``acme.extension``: where its
            ``extension.toml`` sits, which the kit reads as the mount
            does.
        sets: The sets of extensions the kit probes, each as the list a
            package writes for it, ``("acme",)``. The
            category-table clause judges a package listing each, and the
            gate's walk runs over a workspace holding one such package
            for each.
        steps: The steps the extension registers.
    """

    extension: str
    sets: tuple[tuple[str, ...], ...] = ()
    steps: tuple[StepRecord, ...] = ()


@dataclass(frozen=True)
class Violation:
    """One way a subject breaks a clause.

    Attributes:
        clause: The clause's name.
        where: What the violation concerns: a set, a step, a file or
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
        name: The clause's name, ``fragment-owner``.
        rule: The rule in one sentence.
        judge: The violations a subject commits; empty when it conforms.
    """

    name: str
    rule: str
    judge: Callable[[Subject], list[Violation]]


FRAGMENT_OWNER = "fragment-owner"
CATEGORY_TABLE = "category-table"
STEP_ORDER = "step-order"
DECLARATION_VALIDATES = "declaration-validates"
REFERENCES_REGISTER_NOTHING = "references-register-nothing"
ENTRIES_NAME_DEFINED_TASKS = "entries-name-defined-tasks"
CLAIMS_NAME_CATEGORIES = "claims-name-categories"
WORDS_ARE_ANSWERABLE = "words-are-answerable"
PLUGIN_IS_AN_ENTRY_POINT = "plugin-is-an-entry-point"
REQUIREMENTS_IN_METADATA = "requirements-in-metadata"
CONTRACT_KEYS_DOCUMENTED = "contract-keys-documented"
TEMPLATE_COMPOSES = "template-composes"
FRAGMENT_DRIFT = "fragment-drift"
WITHDRAWN_FILE = "withdrawn-file"
WALK_ORDER = "walk-order"
GATE_LINES = "gate-lines"


# One owner for a per-package file.


def _fragment_owners(extension: str, file: str) -> list[str]:
    """The steps carrying a fragment for *file* that names *extension*, by name."""
    return sorted(
        name
        for name, record in steps_by_name().items()
        for fragment in record.fragments
        if fragment.file == file and extension in fragment.extensions
    )


def _fragment_owner(subject: Subject) -> list[Violation]:
    violations: list[Violation] = []
    files = sorted(
        {
            fragment.file
            for record in steps_by_name().values()
            for fragment in record.fragments
            if fragment.extensions
        }
    )
    named = dict.fromkeys(
        extension
        for record in subject.steps
        for fragment in record.fragments
        for extension in fragment.extensions
    )
    for extension in named:
        for file in files:
            owners = _fragment_owners(extension, file)
            if len(owners) > 1:
                violations.append(
                    Violation(
                        FRAGMENT_OWNER,
                        f"extension {extension} {file}",
                        f"{' and '.join(owners)} both carry it for the extension's"
                        " packages; the render picks one by name, so one of them"
                        " yields",
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


def _documented_answer(held: tuple[str, ...], path: str) -> list[CategoryRule]:
    """The rule the documented order picks for *path*, or the rules that tie.

    *held* is the package's set of extensions. The most specific pattern
    wins; between tables of equal specificity the nearer table wins, the
    extension later in composition order first and the base's table
    last; two rules of one table and one specificity claiming *path* for
    different categories or extensions tie.
    """
    from livery.workshop._composition import order_or_sorted
    from livery.workshop._extensions import installed_declaration

    nearest_first = [
        *reversed(order_or_sorted(held, installed_declaration)),
        BASE_TABLE,
    ]
    matching = [rule for rule in category_rules(held) if matches(rule.pattern, path)]
    if not matching:
        return []
    top = max(rule.specificity for rule in matching)
    best = [rule for rule in matching if rule.specificity == top]
    depth = min(nearest_first.index(rule.table) for rule in best)
    at_nearest = [rule for rule in best if nearest_first.index(rule.table) == depth]
    first = at_nearest[0]
    if any(
        rule.category != first.category or rule.extension != first.extension
        for rule in at_nearest
    ):
        return at_nearest
    return [first]


def _set_name(listed: tuple[str, ...]) -> str:
    """*listed* as a violation names it: its names joined with ``+``, or none."""
    return "+".join(listed) or "none"


def _category_table(subject: Subject) -> list[Violation]:
    violations: list[Violation] = []
    for listed in subject.sets:
        probe = Package(
            Path("."),
            "packages/probe",
            "probe",
            (),
            extensions=listed,
        )
        held = extension_set(probe)
        for rule in category_rules(held):
            path = _representative(rule.pattern)
            if not matches(rule.pattern, path):
                continue
            where = f"set {_set_name(listed)} {path}"
            wanted = _documented_answer(held, path)
            if len(wanted) > 1:
                first, second = wanted[0], wanted[1]
                violations.append(
                    Violation(
                        CATEGORY_TABLE,
                        where,
                        f"{first.pattern!r} ({first.category}, {first.extension}) and"
                        f" {second.pattern!r} ({second.category}, {second.extension})"
                        " claim it at one specificity in one table; one of them"
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
                        f"refuses ({error}) where the nearer table's rule decides",
                    )
                )
                continue
            rule_wanted = wanted[0]
            if (found.name, found.pattern, found.supplier) != (
                rule_wanted.category,
                rule_wanted.pattern,
                rule_wanted.extension,
            ):
                violations.append(
                    Violation(
                        CATEGORY_TABLE,
                        where,
                        f"answers {found.name} from {found.pattern!r}"
                        f" ({found.supplier}); the most specific rule of the"
                        f" nearest table, {rule_wanted.table}, says"
                        f" {rule_wanted.category} from {rule_wanted.pattern!r}"
                        f" ({rule_wanted.extension})",
                    )
                )
    return list(dict.fromkeys(violations))


# The order of an extension's steps.


def _loop(start: str) -> list[str]:
    """The steps from *start* back to itself through ``after``; empty without one."""
    records = steps_by_name()

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


def _step_order(subject: Subject) -> list[Violation]:
    violations: list[Violation] = []
    for record in subject.steps:
        where = f"step {record.name}"
        for earlier in record.after:
            # A step an installed extension declares orders nothing
            # while the workspace does not mount it, and the gate goes on.
            if answering(earlier) is None and not installed_step(earlier):
                violations.append(
                    Violation(
                        STEP_ORDER,
                        where,
                        f"runs after {earlier}, which no registered step"
                        " answers to and no installed extension declares;"
                        " the gate stops there",
                    )
                )
        loop = _loop(record.name)
        if loop:
            violations.append(
                Violation(
                    STEP_ORDER,
                    where,
                    f"runs after itself through {', '.join(loop)}; the gate"
                    " would wait on it for ever",
                )
            )
    return violations


# An extension's declaration file.


def _declaration_validates(subject: Subject) -> list[Violation]:
    from livery.workshop._declaration import DeclarationError, declaration_file, read

    if declaration_file(subject.extension) is None:
        return []
    try:
        read(subject.extension, subject.extension)
    except DeclarationError as error:
        return [
            Violation(
                DECLARATION_VALIDATES,
                f"extension {subject.extension}",
                f"{error}; the mount refuses the extension",
            )
        ]
    return []


def _declared_of(subject: Subject) -> tuple[Path, Declaration] | None:
    """The subject's declaration file and what it declares; None without one.

    None as well for a file that refuses, which ``declaration-validates``
    names: the clauses that read a declaration judge only one that reads.
    """
    from livery.workshop._declaration import DeclarationError, declaration_file, read

    path = declaration_file(subject.extension)
    if path is None:
        return None
    try:
        declared = read(subject.extension, subject.extension)
    except DeclarationError:
        return None
    return None if declared is None else (path, declared)


def _additions_of(declared: Declaration) -> list[tuple[tuple[str, ...], Additions]]:
    """The top level's additions, then each ``[for.<target>]``'s, with their keys."""
    tables: list[tuple[tuple[str, ...], Additions]] = [((), declared.additions)]
    tables += [(("for", target), table) for target, table in declared.targets.items()]
    return tables


def _references(declared: Declaration) -> list[tuple[str, Reference]]:
    """Each reference *declared* names, with the key that names it."""
    from livery.workshop._contract_keys import shown
    from livery.workshop._declaration import Reference

    found: list[tuple[str, object]] = []
    for prefix, additions in _additions_of(declared):
        for record in additions.steps:
            where = shown((*prefix, "phases", record.phase, record.step))
            found += [(f"{where}.main", record.run), (f"{where}.fix", record.fix)]
            if record.inputs is not None:
                found.append((f"{where}.inputs.widen", record.inputs.widen))
        for item in additions.jobs:
            where = shown((*prefix, "ci", "jobs", item.point, item.job.name))
            found += [
                (f"{where}.installs", item.job.installs),
                (f"{where}.labels", item.job.labels),
                (f"{where}.deploy", item.job.deploy),
            ]
    found += [
        (f"{shown(('queries', name))}.combine", spec.combine)
        for name, spec in declared.query_specs.items()
    ]
    found += [
        (f"{shown(('fragments', output.target))}.render", output.render)
        for output in declared.fragments
    ]
    registered = {id(record) for record in declared.additions.steps}
    for phase, records in declared.phases.items():
        for record in records:
            if id(record) in registered:
                continue  # named above, with the additions
            where = shown(("phases", phase, record.step))
            found += [(f"{where}.main", record.run), (f"{where}.fix", record.fix)]
    found += [
        (shown(("queries", name)), reference)
        for name, reference in declared.queries.items()
    ]
    found += [
        (shown(("for", target, "queries", name)), reference)
        for target, answered in declared.target_queries.items()
        for name, reference in answered.items()
    ]
    return [(key, value) for key, value in found if isinstance(value, Reference)]


def _registered_by_import(module: str) -> list[str]:
    """What importing *module* registers, in a process of its own."""
    import json
    import os
    import sys

    import livery.footman as footman

    path = os.pathsep.join(entry for entry in sys.path if entry)
    result = footman.run(
        [sys.executable, "-m", "livery.workshop.testing._import_probe", module],
        env={**os.environ, "PYTHONPATH": path},
        nofail=True,
    )
    if result.code != 0:
        lines = result.stderr.strip().splitlines()
        return [f"its import fails: {lines[-1] if lines else result.code}"]
    return [str(line) for line in json.loads(result.stdout)]


def _references_register_nothing(subject: Subject) -> list[Violation]:
    found = _declared_of(subject)
    if found is None:
        return []
    named: dict[str, list[str]] = {}
    for key, reference in _references(found[1]):
        named.setdefault(reference.module, []).append(key)
    violations: list[Violation] = []
    for module, keys in named.items():
        added = _registered_by_import(module)
        if added:
            violations.append(
                Violation(
                    REFERENCES_REGISTER_NOTHING,
                    f"{module}, named by {', '.join(keys)}",
                    f"importing it registers {'; '.join(added)}; the mount"
                    " registers what the declaration says, so a reference's"
                    " module only defines",
                )
            )
    return violations


def _entries_name_defined_tasks(subject: Subject) -> list[Violation]:
    import difflib

    from livery.workshop._contract_keys import shown
    from livery.workshop._declaration import defined_tasks

    found = _declared_of(subject)
    if found is None:
        return []
    path, declared = found
    jobs = [
        (prefix, item)
        for prefix, additions in _additions_of(declared)
        for item in additions.jobs
    ]
    if not jobs:
        return []
    defined = sorted(defined_tasks(path.parent))
    violations: list[Violation] = []
    for prefix, item in jobs:
        where = shown((*prefix, "ci", "jobs", item.point, item.job.name, "entries"))
        for entry in item.entries:
            if entry.task in defined:
                continue
            if not defined:
                reason = f"names {entry.task}, and the extension defines no task"
            else:
                near = difflib.get_close_matches(entry.task, defined, n=1)
                hint = f"; did you mean {near[0]!r}?" if near else ""
                reason = (
                    f"names {entry.task}, which the extension does not define{hint}"
                )
            violations.append(Violation(ENTRIES_NAME_DEFINED_TASKS, where, reason))
    return violations


def _words_are_answerable(subject: Subject) -> list[Violation]:
    from livery.workshop._contract_keys import shown
    from livery.workshop._steps import PACKAGE, PACKAGES
    from livery.workshop._words import PACKAGE_PLACEHOLDERS, PLACEHOLDERS

    found = _declared_of(subject)
    if found is None:
        return []
    violations: list[Violation] = []
    for prefix, additions in _additions_of(found[1]):
        for record in additions.steps:
            words = record.words
            if words is None:
                continue
            where = shown((*prefix, "phases", record.phase, record.step))
            reasons: list[str] = []
            if words.tool not in record.tools:
                reasons.append(
                    f"runs {words.tool}, which its tools do not name"
                    f" ({', '.join(record.tools) or 'none'}); a step runs a tool"
                    " it requires"
                )
            reasons += [
                f"names {{{name}}}, which neither the engine nor its matrix"
                f" answers; the engine answers {', '.join(PLACEHOLDERS)}"
                for name in words.unanswered()
            ]
            reasons += [
                f"varies {key} in its matrix, which no word names; every call"
                " would run the same words"
                for key in words.unvaried()
            ]
            if record.scope != PACKAGE and record.narrowing != PACKAGES:
                reasons += [
                    f"names {{{name}}}, which only a call that judges one package"
                    " answers, and the step judges the workspace whole"
                    for name in PACKAGE_PLACEHOLDERS
                    if name in words.placeholders()
                ]
            violations += [
                Violation(WORDS_ARE_ANSWERABLE, where, reason) for reason in reasons
            ]
    return violations


def _claims_name_categories(subject: Subject) -> list[Violation]:
    import difflib

    from livery.workshop._categories import known_categories
    from livery.workshop._contract_keys import shown

    found = _declared_of(subject)
    if found is None:
        return []
    known = sorted(known_categories())
    violations: list[Violation] = []
    for prefix, additions in _additions_of(found[1]):
        for record in additions.steps:
            where = shown((*prefix, "phases", record.phase, record.step, "claims"))
            for claim in record.claims:
                if claim.category not in known:
                    near = difflib.get_close_matches(claim.category, known, n=1)
                    hint = f"; did you mean {near[0]!r}?" if near else ""
                    violations.append(
                        Violation(
                            CLAIMS_NAME_CATEGORIES,
                            where,
                            f"names the category {claim.category!r}, which the"
                            f" workshop does not know{hint}; the step claims no"
                            " file of it",
                        )
                    )
                violations += [
                    Violation(
                        CLAIMS_NAME_CATEGORIES,
                        where,
                        f"names the suffix {suffix!r}; a suffix starts with a dot,"
                        f" {'.' + suffix!r}",
                    )
                    for suffix in claim.suffixes
                    if not suffix.startswith(".")
                ]
    return violations


def _plugin_is_an_entry_point(subject: Subject) -> list[Violation]:
    from livery.footman import installed_entry_points

    found = _declared_of(subject)
    if found is None or not found[1].plugin:
        return []
    plugin = found[1].plugin
    if plugin in {entry.name for entry in installed_entry_points("footman.tasks")}:
        return []
    return [
        Violation(
            PLUGIN_IS_AN_ENTRY_POINT,
            f"extension {subject.extension}",
            f"names the plugin {plugin!r}, which no installed distribution"
            " declares in footman.tasks; the mount cannot mount its verbs",
        )
    ]


#: A requirement's distribution, at the start of a ``Requires-Dist`` line.
_REQUIRED = re.compile(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
#: An extra a ``Requires-Dist`` line's marker names.
_EXTRA = re.compile(r"""\bextra\s*==\s*["']([^"']+)["']""")


def _required(lines: list[str]) -> list[tuple[str, frozenset[str]]]:
    """Each ``Requires-Dist`` line as its distribution and the extras its marker names.

    Both in the packaging standards' normal form. The core metadata
    format is a published standard, so reading it pins no tool's text.
    """
    from livery.workshop._extensions import normal_name

    found: list[tuple[str, frozenset[str]]] = []
    for line in lines:
        named = _REQUIRED.match(line)
        if named is None:
            continue
        marker = line.partition(";")[2]
        extras = frozenset(normal_name(extra) for extra in _EXTRA.findall(marker))
        found.append((normal_name(named.group(1)), extras))
    return found


def _requirements_in_metadata(subject: Subject) -> list[Violation]:
    from livery.workshop._extensions import (
        distribution_of,
        normal_name,
        shipping_distribution,
        ships_elsewhere,
    )

    found = _declared_of(subject)
    if found is None:
        return []
    declared = found[1]
    if not declared.requires:
        return []
    where = f"extension {subject.extension}"
    dist = shipping_distribution(subject.extension)
    if dist is None:
        return [
            Violation(
                REQUIREMENTS_IN_METADATA,
                where,
                "declares requires, and no installed distribution declares it in"
                " workshop.extensions, so its wheel's metadata cannot be read",
            )
        ]
    required = _required(list(dist.requires or ()))
    violations: list[Violation] = []
    # A target the same wheel ships is held to it by being in it.
    requires = [name for name in declared.requires if ships_elsewhere(name, dist.name)]
    for target in requires:
        wanted = normal_name(distribution_of(target))
        if (wanted, frozenset()) not in required:
            violations.append(
                Violation(
                    REQUIREMENTS_IN_METADATA,
                    where,
                    f"requires {target}, and its wheel does not depend on"
                    f" {distribution_of(target)}; a requires range is a dependency"
                    " of the wheel, so the resolver holds the two to it",
                )
            )
    return violations


def _contract_keys_documented(subject: Subject) -> list[Violation]:
    from livery.workshop._declaration import (
        DeclarationError,
        contract_keys,
        declaration_file,
    )

    if declaration_file(subject.extension) is None:
        return []
    try:
        keys = contract_keys(subject.extension)
    except DeclarationError:
        return []  # declaration-validates names the file
    return [
        Violation(
            CONTRACT_KEYS_DOCUMENTED,
            f"extension {subject.extension}",
            f"declares the {item.contract} contract key {item.path} with no doc;"
            " the editor shows a key by its doc, and so will the reference pages",
        )
        for item in keys
        if not item.doc
    ]


# The templates an extension declares.


def _own_templates(subject: Subject) -> dict[str, DeclaredTemplate]:
    """The templates the subject's declaration file declares, by name.

    The base's are those of its ``contract.toml``. A declaration file
    that refuses declares none here: ``declaration-validates`` names
    it. A template takes the name an installed distribution declares
    its extension under, which a chain names, and the package's own
    without one.
    """
    from livery.workshop._contract_keys import BASE_FILE
    from livery.workshop._declaration import template_declarations, template_specs_of
    from livery.workshop._extensions import extension_package, installed_extensions

    if subject.extension == BASE_EXTENSION:
        data = tomllib.loads(BASE_FILE.read_text("utf-8"))
        return template_specs_of(data, BASE_FILE, extension=BASE_EXTENSION)
    if _declared_of(subject) is None:
        return {}
    named = [
        name
        for name in installed_extensions()
        if extension_package(name) == subject.extension
    ]
    extension = named[0] if named else subject.extension
    found = template_declarations(extension, subject.extension)
    return {template.name: template for template in found}


def _template_composes(subject: Subject) -> list[Violation]:
    from livery.footman import Failed
    from livery.workshop._composition import closure, implied
    from livery.workshop._declaration import DeclarationError
    from livery.workshop._extensions import GROUP, installed_declaration, why_inert
    from livery.workshop._seeds import available_templates, chain_needs, template_chain

    own = _own_templates(subject)
    if not own:
        return []
    try:
        known = {**available_templates(), **own}
    except Failed as error:
        return [
            Violation(TEMPLATE_COMPOSES, f"extension {subject.extension}", str(error))
        ]
    violations: list[Violation] = []
    for name, template in sorted(own.items()):
        where = f"template {name}"
        try:
            members = closure(template.extensions, installed_declaration)
            required = implied(members, installed_declaration)
            for entry in dict.fromkeys(template.extensions):
                if entry in required:
                    continue
                if installed_declaration(entry) is None:
                    violations.append(
                        Violation(
                            TEMPLATE_COMPOSES,
                            where,
                            f"lists {entry}, which no installed distribution declares"
                            f" in {GROUP}, so nothing shows that it composes"
                            " something in a package",
                        )
                    )
                elif why := why_inert(entry):
                    violations.append(
                        Violation(
                            TEMPLATE_COMPOSES,
                            where,
                            f"lists {entry}, which composes nothing in a package:"
                            f" {why}; fm new.package refuses the template",
                        )
                    )
        except DeclarationError as error:
            violations.append(Violation(TEMPLATE_COMPOSES, where, str(error)))
            continue
        try:
            chain = template_chain(name, known)
        except Failed as error:
            violations.append(Violation(TEMPLATE_COMPOSES, where, str(error)))
            continue
        violations += [
            Violation(
                TEMPLATE_COMPOSES,
                where,
                f"its chain needs {needed}, which the set of its extensions does not"
                " hold; a package born from it holds that set alone, so no step that"
                f" names {needed} judges it",
            )
            for needed in chain_needs(chain)
            if needed not in members
        ]
    return violations


# An extension's configuration files, through the workshop's own render.


def _probe_answers() -> dict[str, Any]:
    """The answers the kit renders an extension's fragments with.

    A workspace of one python member and one native member, so a
    fragment's loops over either run.
    """
    return {
        "packages": [
            {"dir": "probe", "name": "acme-probe", "dev": "acme-probe"},
            {"dir": "native", "name": "acme-native"},
        ],
        "python_floor": "3.11",
        "namespace_package": "acme",
        "runner_prog": "fm",
        "project_name": "acme",
    }


def _package_fragments(subject: Subject) -> list[tuple[StepRecord, Fragment]]:
    """The subject's fragments for per-package files, each with its step."""
    return [
        (record, fragment)
        for record in subject.steps
        for fragment in record.fragments
        if fragment.extensions
    ]


def _project_drift(subject: Subject) -> list[Violation]:
    """The composed project files, judged where the subject carries fragments."""
    from livery.workshop import _templates

    owners = sorted(
        record.name
        for record in subject.steps
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
            _shipped_files.settle_package(member, fragment.extensions)
        except Exception as error:
            return f"does not render: {error}"
        drift = _shipped_files.judge_package(member, fragment.extensions)
        if drift:
            return f"drifts from its own render: {drift[0]}"
        copy = member / fragment.file
        copy.write_bytes(b"# a hand edit\n" + copy.read_bytes())
        if not _shipped_files.judge_package(member, fragment.extensions):
            return "a hand edit of the rendered file is not named as drift"
    return ""


def _fragment_drift(subject: Subject) -> list[Violation]:
    violations = _project_drift(subject)
    for record, fragment in _package_fragments(subject):
        problem = _package_drift(fragment)
        if problem:
            held = " or ".join(fragment.extensions)
            where = f"step {record.name} {fragment.file} for {held}"
            violations.append(Violation(FRAGMENT_DRIFT, where, problem))
    return violations


def _withdraw(record: StepRecord, fragment: Fragment, *, edited: bool) -> str:
    """What goes wrong when *record* leaves a rendered file behind; empty when nothing.

    The render writes the file into a probe package, a person edits it
    or not, the step is withdrawn, and the render settles the file
    again. A file another step still renders for the same extensions is
    not withdrawn at all, and says nothing here.
    """
    from livery.workshop import _shipped_files

    if record.name not in steps_by_name():
        return ""
    state = _steps.snapshot()
    with tempfile.TemporaryDirectory() as scratch:
        member = Path(scratch)
        copy = member / fragment.file
        try:
            _shipped_files.settle_package(member, fragment.extensions)
            if not copy.is_file():
                return ""  # it did not render: the drift clause names that
            if edited:
                copy.write_bytes(b"# a hand edit\n" + copy.read_bytes())
            _steps.unregister_step(record.name)
            if package_fragment(fragment.extensions, fragment.file) is not None:
                return ""
            _shipped_files.settle_package(member, fragment.extensions)
        except Exception:
            return ""  # a render that fails is the drift clause's to name
        finally:
            _steps.restore(state)
        if edited and not copy.is_file():
            return (
                "an edited copy is removed once the step is withdrawn;"
                " contract 11 keeps it as a local override"
            )
        if not edited and copy.is_file():
            return (
                "an unedited copy stays once the step is withdrawn; contract 11"
                " removes it"
            )
    return ""


def _withdrawn_file(subject: Subject) -> list[Violation]:
    violations: list[Violation] = []
    for record, fragment in _package_fragments(subject):
        held = " or ".join(fragment.extensions)
        where = f"step {record.name} {fragment.file} for {held}"
        for edited in (False, True):
            problem = _withdraw(record, fragment, edited=edited)
            if problem:
                violations.append(Violation(WITHDRAWN_FILE, where, problem))
    return violations


# The gate's walk over an extension's steps.


class _Recorder:
    """The calls a probe walk makes into the subject's steps, in order."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self._lock = threading.Lock()

    def body(self, mode: str, name: str) -> Callable[[PhaseContext], None]:
        def record(ctx: PhaseContext) -> None:
            del ctx
            with self._lock:
                self.calls.append((mode, name))

        return record


#: A file of the probe workspace no step's claims reach.
_UNCLAIMED = "probe/unclaimed.probe"


def _probe_packages(
    root: Path, sets: tuple[tuple[str, ...], ...]
) -> tuple[Package, ...]:
    """One probe package for each of *sets*, listing it.

    So a package step finds its own.
    """
    return tuple(
        Package(
            root / "packages" / f"probe-{index}",
            f"packages/probe-{index}",
            f"probe-{index}",
            (),
            extensions=listed,
        )
        for index, listed in enumerate(sets)
    )


def _probed(record: StepRecord, packages: tuple[Package, ...]) -> bool:
    """Whether the probe workspace gives *record* something to judge."""
    from livery.workshop._composition import extension_set

    if record.scope == WORKSPACE:
        return True
    return any(
        set(record.extensions) & set(extension_set(package)) for package in packages
    )


def _probe_walk(
    subject: Subject, *, fix: bool, named: bool
) -> tuple[list[tuple[str, str]], str, tuple[Package, ...]]:
    """Walk the gate over a probe workspace with the subject's steps recording.

    The steps' bodies are replaced by recorders and their ``after``
    lists emptied, so no tool runs; the registry is restored after.
    *named* walks over one file no claim reaches; otherwise the walk
    is whole and the steps' claims are emptied, so every step reads
    whatever git lists around the probe. Returns the calls, the gate's
    output, and the probe's packages.
    """
    from livery.workshop import _quality

    recorder = _Recorder()
    names = [record.name for record in subject.steps if record.name in steps_by_name()]
    state = _steps.snapshot()
    out = io.StringIO()
    try:
        for record in subject.steps:
            if record.name not in names:
                continue
            _steps.register_step(
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
            packages = _probe_packages(root, subject.sets)
            ctx = PhaseContext(
                root=root,
                packages=packages,
                fix=fix,
                files=(_UNCLAIMED,) if named else (),
            )
            with contextlib.redirect_stdout(out):
                _quality.walk(ctx, only=frozenset(names))
    finally:
        _steps.restore(state)
    return recorder.calls, out.getvalue(), packages


def _walk_order(subject: Subject) -> list[Violation]:
    calls, _out, packages = _probe_walk(subject, fix=True, named=False)
    first_judge = next(
        (index for index, (mode, _name) in enumerate(calls) if mode == "run"), None
    )
    violations: list[Violation] = []
    for record in subject.steps:
        if record.name not in steps_by_name():
            continue
        where = f"step {record.name}"
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
                        "judged after it rewrote; a step that rewrote is not judged"
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
    for record in subject.steps:
        if record.name not in steps_by_name():
            continue
        where = f"step {record.name}"
        if (
            subject.extension != BASE_EXTENSION
            and record.extension != subject.extension
        ):
            violations.append(
                Violation(
                    GATE_LINES,
                    where,
                    f"names {record.extension} as its extension; the gate says who"
                    f" registered a step by it, and {subject.extension} registered"
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
        FRAGMENT_OWNER,
        "A per-package configuration file has one owner among the steps"
        " whose fragments name an extension.",
        _fragment_owner,
    ),
    Clause(
        CATEGORY_TABLE,
        "A path's category is the most specific rule's, the nearer table"
        " winning a tie between tables, the extension later in composition"
        " order first and the base's table last, and two rules of one table"
        " never tie.",
        _category_table,
    ),
    Clause(
        STEP_ORDER,
        "Every step a step runs after is registered or declared by an"
        " installed extension, and following the steps it runs after never"
        " leads back to it.",
        _step_order,
    ),
    Clause(
        DECLARATION_VALIDATES,
        "An extension's extension.toml holds only the keys the workshop declares,"
        " each of its type, and every reference in it names a function its"
        " module defines.",
        _declaration_validates,
    ),
    Clause(
        REFERENCES_REGISTER_NOTHING,
        "Importing a module a declaration's reference names registers nothing:"
        " the declaration says what the extension adds, and the mount"
        " registers it.",
        _references_register_nothing,
    ),
    Clause(
        ENTRIES_NAME_DEFINED_TASKS,
        "Every task a CI job's entries name is one the extension defines, read"
        " from its sources: a group and a task decorated in it.",
        _entries_name_defined_tasks,
    ),
    Clause(
        CLAIMS_NAME_CATEGORIES,
        "Every category a step's claims name is one the workshop knows, and"
        " every suffix starts with a dot.",
        _claims_name_categories,
    ),
    Clause(
        WORDS_ARE_ANSWERABLE,
        "A step in words runs a tool it requires, every placeholder its words"
        " name is one the engine or its matrix answers, every matrix key is"
        " named in its words, and a package's placeholder sits in a step that"
        " judges one package at a time.",
        _words_are_answerable,
    ),
    Clause(
        PLUGIN_IS_AN_ENTRY_POINT,
        "The plugin an extension.toml names is a footman.tasks entry point an"
        " installed distribution declares.",
        _plugin_is_an_entry_point,
    ),
    Clause(
        REQUIREMENTS_IN_METADATA,
        "Every extension an extension.toml requires is a dependency of its wheel.",
        _requirements_in_metadata,
    ),
    Clause(
        CONTRACT_KEYS_DOCUMENTED,
        "Every contract key an extension declares carries a doc.",
        _contract_keys_documented,
    ),
    Clause(
        TEMPLATE_COMPOSES,
        "Every extension a template lists composes something in a package,"
        " unless another it lists requires it, and the set of the template's"
        " list holds every extension its chain needs.",
        _template_composes,
    ),
    Clause(
        FRAGMENT_DRIFT,
        "A step's fragments render, the composed pyproject.toml parses, and a"
        " per-package file matches its render until a person edits it, when"
        " the drift gate names it.",
        _fragment_drift,
    ),
    Clause(
        WITHDRAWN_FILE,
        "A per-package file whose step is withdrawn, and which no other step"
        " renders for the package's extensions, is removed when unedited and kept"
        " when edited.",
        _withdrawn_file,
    ),
    Clause(
        WALK_ORDER,
        "Under --fix every step with a fix mode rewrites before any judge"
        " starts and is not judged again, and every other step is judged.",
        _walk_order,
    ),
    Clause(
        GATE_LINES,
        "Every step names the extension that registered it, which the gate prints,"
        " and a step with no file to read is named and never started.",
        _gate_lines,
    ),
)
"""Every clause, in the order the kit judges them."""


def judge(subject: Subject) -> list[Violation]:
    """Every violation *subject* commits, clause by clause; empty when it conforms."""
    return [violation for clause in CLAUSES for violation in clause.judge(subject)]


def builtin_subject() -> Subject:
    """The workshop's own steps as a subject, for its own suite.

    Its sets are the empty one, whose package the base's table answers
    for alone, and, for each installed extension that composes
    something in a package
    ([livery.workshop._extensions.composes_per_package][]), the set of
    that extension alone.
    """
    from livery.workshop._extensions import composes_per_package, installed_extensions

    composing = [name for name in installed_extensions() if composes_per_package(name)]
    return Subject(
        BASE_EXTENSION,
        sets=((), *((name,) for name in composing)),
        steps=tuple(
            record
            for record in steps_by_name().values()
            if record.extension == BASE_EXTENSION
        ),
    )
