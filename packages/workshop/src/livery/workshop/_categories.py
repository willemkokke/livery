"""The two questions the workshop asks about a path, on two axes.

A path's **category** says what it is to its package: source, test,
test support, configuration, and whatever a table adds, prose,
example, asset, nav. Its **channel** says who wrote it and where to
edit it: rendered, generated, materialised, seed, contract, yours.
Neither answer can be read off the other, so the vocabularies stay
apart: `category_of` answers the first, and `channel_of` the second
from the rules `register_channels` adds.

A category rule is a pattern in a table, since nothing a category
needs reads state: an extension declares its own table under
``[categories]`` in its ``extension.toml``, and the base's two tables
are data here, `BASE_RULES` beneath every package's extensions and
`WORKSPACE_RULES` for the workspace's own unit. A channel rule is a
callable with a rank, since it reads the delivery manifest, the
emitted set and the template source.

The most specific category rule wins. Between rules of one
specificity from two tables, the nearer table wins: of the extensions
a package holds, the one later in composition order, where an
extension comes after the extensions it requires and the others go
alphabetically, and the base's table last. Two rules of one table and
one specificity that claim one path for different categories, or from
different extensions, refuse naming both, so two checkouts of one
commit answer alike and the order a table lists its rules in settles
nothing in silence. A package's contract may reassign its own paths
among the categories,
``[categories] vendored = ["docs/assets/vendor/**"]``, and that table
wins over every rule of its extensions and the base.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from livery.workshop._packages import Package

#: What a path is to its package: a change to a test runs that test
#: alone; a change to anything else runs the package's suite and its
#: dependents'. An extension adds labels beside these.
SOURCE = "source"
TEST = "test"
TEST_SUPPORT = "test-support"
CONFIGURATION = "configuration"
#: A documentation example: a python file under ``docs/examples/`` a
#: page shows by snippet, run by the examples step and no suite.
EXAMPLE = "example"

#: The workspace's own unit, the root's tests and the files beside
#: them: its kind, and the name of the base's table for it.
WORKSPACE = "workspace"

#: The name of the base's table, whose rules (the documentation's
#: paths, and configuration for the rest) answer for every package,
#: beneath the tables of the extensions it holds.
BASE_TABLE = "base"

#: The extension the builtin rules belong to.
_BASE = "livery.workshop"


class CategoryError(ValueError):
    """Two rules claim one path at one level; the message names both.

    Two category rules of one table and one specificity, or two
    channel rules of one rank.
    """


@dataclass(frozen=True)
class CategoryRule:
    """One pattern's claim on a category.

    Attributes:
        table: The table the rule belongs to: the extension that
            declares it, or ``base`` or ``workspace``, the base's two.
        pattern: A path pattern relative to the package, ``src/**``,
            ``tests/**/test_*.py``, ``**``.
        category: The label the pattern claims.
        extension: The extension that supplied the rule; the base's own is
            ``livery.workshop``.
    """

    table: str
    pattern: str
    category: str
    extension: str

    @property
    def specificity(self) -> tuple[int, int]:
        """How specific the pattern is: its literal characters, then its segments."""
        return specificity(self.pattern)


@dataclass(frozen=True)
class Category:
    """A path's category, and who supplied the rule that answered.

    Attributes:
        name: The category.
        supplier: The extension whose rule answered, or ``the package`` for
            its own contract's exception.
        pattern: The pattern that matched.
    """

    name: str
    supplier: str
    pattern: str


@dataclass(frozen=True)
class Provenance:
    """One file's channel answer: the channel, the source, the edit path.

    Attributes:
        channel: The owning channel's short name.
        source: Where the content comes from.
        edit: What to change, and through which verb.
    """

    channel: str
    source: str
    edit: str


ChannelJudge = Callable[[Path, Path, "frozenset[str] | None"], "Provenance | None"]


@dataclass(frozen=True)
class ChannelRule:
    """One callable's claim on the channel axis.

    Attributes:
        name: The rule's name, for a refusal.
        judge: Answers for a path, or None when the rule does not
            claim it; called with the root, the relative path, and the
            emitted set when the caller computed one.
        rank: Higher ranks answer first; two rules of one rank both
            answering refuse naming both.
        extension: The extension that registered the rule.
    """

    name: str
    judge: ChannelJudge
    rank: int
    extension: str


@dataclass(frozen=True)
class Channel:
    """A path's channel answer and who supplied it.

    Attributes:
        provenance: The answer.
        supplier: The extension whose rule answered.
    """

    provenance: Provenance
    supplier: str


#: The base's table, beneath the tables of every package's extensions:
#: the documentation's paths, and configuration for the rest.
BASE_RULES: tuple[CategoryRule, ...] = tuple(
    CategoryRule(BASE_TABLE, pattern, category, _BASE)
    for pattern, category in (
        ("docs/**/*.md", "prose"),
        ("docs/nav.toml", "nav"),
        ("docs/assets/**", "asset"),
        ("docs/examples/**/*.py", EXAMPLE),
        ("docs/_generated/**", "generated"),
        ("**", CONFIGURATION),
    )
)

#: The workspace's own unit's table: its tests as python's table reads
#: them, and the files beside them that only the site or nobody reads.
WORKSPACE_RULES: tuple[CategoryRule, ...] = tuple(
    CategoryRule(WORKSPACE, pattern, category, _BASE)
    for pattern, category in (
        ("tests/**/test_*.py", TEST),
        ("tests/**/*_test.py", TEST),
        ("tests/**", TEST_SUPPORT),
        ("notes/**", "notes"),
        ("docs/**", "site"),
        ("zensical.toml", "site"),
        ("README.md", "readme"),
        ("**", CONFIGURATION),
    )
)

_CHANNELS: list[ChannelRule] = []


def specificity(pattern: str) -> tuple[int, int]:
    """*pattern*'s specificity: the literal characters it names, then its segments.

    ``tests/**/test_*.py`` names 13 characters and beats ``tests/**``
    with 5, which beats ``**`` with none; a longer path prefix beats
    a shorter one the same way.
    """
    literal = sum(
        len(segment.replace("*", "").replace("?", "")) for segment in pattern.split("/")
    )
    return (literal, len(pattern.split("/")))


def _regex(pattern: str) -> re.Pattern[str]:
    """*pattern* as an anchored regex: ``**`` spans directories, ``*`` one segment."""
    parts: list[str] = []
    for segment in pattern.split("/"):
        if segment == "**":
            parts.append("(?:.*/)?")
            continue
        escaped = re.escape(segment).replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
        parts.append(escaped + "/")
    body = "".join(parts)
    if body.endswith("/"):
        body = body[:-1]
    body = body.replace("(?:.*/)?/", "(?:.*/)?")
    if pattern.endswith("/**"):
        body = body[: -len("(?:.*/)?")] + ".*"
    return re.compile("^" + body + "$")


def matches(pattern: str, path: str) -> bool:
    """Whether *pattern* claims *path*, a relative posix path."""
    return _regex(pattern).match(path) is not None


def category_rules(extensions: Iterable[str]) -> tuple[CategoryRule, ...]:
    """The rules that answer for a package holding *extensions*, the nearest first.

    Each extension's declared table, the one later in composition
    order first, then the base's table. While the extensions' declared
    order has no start, they come in alphabetical order. An extension
    nothing installed declares adds no rule.
    """
    from livery.workshop._composition import order_or_sorted
    from livery.workshop._extensions import installed_declaration

    found: list[CategoryRule] = []
    for name in reversed(order_or_sorted(extensions, installed_declaration)):
        found.extend(extension_rules(name))
    found.extend(BASE_RULES)
    return tuple(found)


def workspace_rules() -> tuple[CategoryRule, ...]:
    """The rules that answer for the workspace's own unit: its table alone."""
    return WORKSPACE_RULES


def extension_rules(extension: str) -> tuple[CategoryRule, ...]:
    """The category rules *extension* declares, in a table named after it.

    Empty for an extension nothing installed declares.
    """
    from livery.workshop._extensions import installed_declaration

    declared = installed_declaration(extension)
    if declared is None:
        return ()
    return tuple(
        CategoryRule(extension, pattern, category, extension)
        for category, patterns in declared.categories
        for pattern in patterns
    )


def _package_rules(package: Package) -> tuple[CategoryRule, ...]:
    """The rules that answer for *package*, the nearest first.

    The workspace's own unit takes its table alone. Every other package
    takes the tables of the package-level extensions it holds, over the
    base's.
    """
    if package.kind == WORKSPACE:
        return workspace_rules()
    from livery.workshop._composition import extension_set

    return category_rules(extension_set(package))


def category_of(package: Package, path: str) -> Category:
    """What *path*, relative to *package*, is to it.

    The package's own ``[categories]`` exception answers first. Then
    the most specific rule of the tables that answer for the package
    wins, and between rules of one specificity the nearer table's: the
    extension later in composition order, the base's table last.
    Nothing matching is configuration, which the base's table also says
    last.

    Raises:
        CategoryError: when two rules of one specificity and one table
            claim *path* for different categories or from different
            extensions.
    """
    for category, patterns in package.categories:
        for pattern in patterns:
            if matches(pattern, path):
                return Category(category, "the package", pattern)
    rules = _package_rules(package)
    best: list[tuple[int, CategoryRule]] = []
    tables = {
        name: index for index, name in enumerate(dict.fromkeys(r.table for r in rules))
    }
    for rule in rules:
        if not matches(rule.pattern, path):
            continue
        if not best:
            best = [(tables[rule.table], rule)]
            continue
        _, current = best[0]
        if rule.specificity > current.specificity:
            best = [(tables[rule.table], rule)]
        elif rule.specificity == current.specificity:
            best.append((tables[rule.table], rule))
    if not best:
        return Category(CONFIGURATION, _BASE, "")
    nearest = min(depth for depth, _ in best)
    at_nearest = [rule for depth, rule in best if depth == nearest]
    if len(at_nearest) > 1 and any(
        rule.category != at_nearest[0].category
        or rule.extension != at_nearest[0].extension
        for rule in at_nearest
    ):
        first, second = at_nearest[0], at_nearest[1]
        raise CategoryError(
            f"{package.path}/{path}: two rules of one specificity claim it,"
            f" {first.pattern!r} as {first.category} ({first.extension}) and"
            f" {second.pattern!r} as {second.category} ({second.extension});"
            " the more specific pattern wins, so one of them names the file"
            " more closely"
        )
    rule = at_nearest[0]
    return Category(rule.category, rule.extension, rule.pattern)


def register_channels(rules: Iterable[ChannelRule]) -> None:
    """Register channel *rules*; a rule of the same name is replaced."""
    for rule in rules:
        _CHANNELS[:] = [known for known in _CHANNELS if known.name != rule.name]
        _CHANNELS.append(rule)


def unregister_channels(*, extension: str) -> None:
    """Withdraw every channel rule *extension* registered."""
    _CHANNELS[:] = [rule for rule in _CHANNELS if rule.extension != extension]


def channel_rules() -> tuple[ChannelRule, ...]:
    """Every channel rule, highest rank first, registration order within a rank."""
    return tuple(sorted(_CHANNELS, key=lambda rule: -rule.rank))


def channel_of(
    root: Path, relative: Path, *, emitted: frozenset[str] | None = None
) -> Channel | None:
    """Who wrote *relative* inside *root*, or None when no rule claims it.

    Raises:
        CategoryError: when two rules of one rank both claim the path.
    """
    ranked: dict[int, list[ChannelRule]] = {}
    for rule in channel_rules():
        ranked.setdefault(rule.rank, []).append(rule)
    for rank in sorted(ranked, reverse=True):
        answers = [
            (rule, answer)
            for rule in ranked[rank]
            if (answer := rule.judge(root, relative, emitted)) is not None
        ]
        if len(answers) > 1:
            (first, _), (second, _) = answers[0], answers[1]
            raise CategoryError(
                f"{relative.as_posix()}: two channel rules of rank {rank} claim"
                f" it, {first.name} ({first.extension}) and {second.name}"
                f" ({second.extension}); give one the higher rank"
            )
        if answers:
            rule, answer = answers[0]
            return Channel(answer, rule.extension)
    return None


def known_categories() -> frozenset[str]:
    """The four builtin categories, and every category the base's two tables name."""
    found = {SOURCE, TEST, TEST_SUPPORT, CONFIGURATION}
    found.update(rule.category for rule in (*BASE_RULES, *WORKSPACE_RULES))
    return frozenset(found)
