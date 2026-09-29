"""The two questions the workshop asks about a path, one store, two axes.

A path's **category** says what it is to its package: source, test,
test support, configuration, and whatever a layer adds, prose,
example, asset, nav. Its **channel** says who wrote it and where to
edit it: rendered, generated, materialised, seed, contract, yours.
Neither answer can be read off the other, so the vocabularies stay
apart, and one store keyed by axis holds both with a typed pair of
functions per axis: [livery.workshop._categories.register_categories][]
and [livery.workshop._categories.category_of][],
[livery.workshop._categories.register_channels][] and
[livery.workshop._categories.channel_of][].

A category rule is a pattern table a kind or a layer registers, since
nothing a category needs reads state, and the table renders into the
documentation. A channel rule is a callable with a rank, since it
reads the delivery manifest, the emitted set and the template source.
Rules order by specificity, the most specific claim first, and two
rules of one specificity claiming one path refuse naming both, so two
checkouts of one commit answer alike and nothing is settled by
registration order in silence. A package's contract may reassign its
own paths among the categories, ``[categories] vendored =
["docs/assets/vendor/**"]``, and that table wins over every kind's
rule, since the package is the innermost layer over its kind.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from livery.workshop._packages import Package

#: What a path is to its kind: a change to a test runs that test
#: alone; a change to anything else runs the package's suite and its
#: dependents'. A layer adds labels beside these.
SOURCE = "source"
TEST = "test"
TEST_SUPPORT = "test-support"
CONFIGURATION = "configuration"

#: The kind name of the workspace's own unit, the root's tests and the
#: files beside them; the base registers its category rules.
WORKSPACE = "workspace"

#: The layer the builtin rules belong to.
_BASE = "livery.workshop"


class CategoryError(ValueError):
    """A registry cannot answer: two rules of one specificity claim one path."""


@dataclass(frozen=True)
class CategoryRule:
    """One pattern's claim on a category.

    Attributes:
        kind: The package kind the rule is registered for; a derived
            kind inherits it through the kind chain.
        pattern: A path pattern relative to the package, ``src/**``,
            ``tests/**/test_*.py``, ``**``.
        category: The label the pattern claims.
        layer: The layer that registered the rule; the base's own is
            ``livery.workshop``.
    """

    kind: str
    pattern: str
    category: str
    layer: str

    @property
    def specificity(self) -> tuple[int, int]:
        """How specific the pattern is: its literal characters, then its segments."""
        return specificity(self.pattern)


@dataclass(frozen=True)
class Category:
    """A path's category, and who supplied the rule that answered.

    Attributes:
        name: The category.
        supplier: The layer whose rule answered, or ``the package`` for
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
        layer: The layer that registered the rule.
    """

    name: str
    judge: ChannelJudge
    rank: int
    layer: str


@dataclass(frozen=True)
class Channel:
    """A path's channel answer and who supplied it.

    Attributes:
        provenance: The answer.
        supplier: The layer whose rule answered.
    """

    provenance: Provenance
    supplier: str


_CATEGORIES: dict[str, list[CategoryRule]] = {}
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


def register_categories(
    kind: str, rules: Iterable[tuple[str, str]], *, layer: str = _BASE
) -> None:
    """Register *rules*, ``(pattern, category)`` pairs, for *kind*.

    A derived kind inherits them through the kind chain, its own rules
    winning a tie. Registering the same pattern for the same kind from
    the same layer replaces the earlier claim.
    """
    table = _CATEGORIES.setdefault(kind, [])
    for pattern, category in rules:
        table[:] = [
            rule
            for rule in table
            if not (rule.pattern == pattern and rule.layer == layer)
        ]
        table.append(CategoryRule(kind, pattern, category, layer))


def unregister_categories(kind: str, *, layer: str) -> None:
    """Withdraw every rule *layer* registered for *kind*."""
    table = _CATEGORIES.get(kind, [])
    table[:] = [rule for rule in table if rule.layer != layer]


def category_rules(kind: str) -> tuple[CategoryRule, ...]:
    """The rules that answer for *kind*: its own, then each ancestor's.

    A kind the registry does not know contributes its own table alone,
    which is how the workspace's unit answers.
    """
    from livery.workshop._kinds import kind_chain, kind_names

    names = [kind]
    if kind in kind_names():
        names = [record.name for record in kind_chain(kind)]
    found: list[CategoryRule] = []
    for name in names:
        found.extend(_CATEGORIES.get(name, []))
    return tuple(found)


def category_of(package: Package, path: str) -> Category:
    """What *path*, relative to *package*, is to it.

    The package's own ``[categories]`` exception answers first; then
    the most specific rule of the kind chain, the nearer kind winning
    a tie between kinds. Nothing matching is configuration, which
    every builtin table also says last.

    Raises:
        CategoryError: when two rules of one specificity and one kind
            claim *path* for different categories or from different
            layers.
    """
    for category, patterns in package.categories:
        for pattern in patterns:
            if matches(pattern, path):
                return Category(category, "the package", pattern)
    rules = category_rules(package.kind)
    best: list[tuple[int, CategoryRule]] = []
    kinds_order = {
        name: index for index, name in enumerate(dict.fromkeys(r.kind for r in rules))
    }
    for rule in rules:
        if not matches(rule.pattern, path):
            continue
        if not best:
            best = [(kinds_order[rule.kind], rule)]
            continue
        _, current = best[0]
        if rule.specificity > current.specificity:
            best = [(kinds_order[rule.kind], rule)]
        elif rule.specificity == current.specificity:
            best.append((kinds_order[rule.kind], rule))
    if not best:
        return Category(CONFIGURATION, _BASE, "")
    nearest = min(depth for depth, _ in best)
    at_nearest = [rule for depth, rule in best if depth == nearest]
    if len(at_nearest) > 1 and any(
        rule.category != at_nearest[0].category or rule.layer != at_nearest[0].layer
        for rule in at_nearest
    ):
        first, second = at_nearest[0], at_nearest[1]
        raise CategoryError(
            f"{package.path}/{path}: two rules of one specificity claim it,"
            f" {first.pattern!r} as {first.category} ({first.layer}) and"
            f" {second.pattern!r} as {second.category} ({second.layer});"
            " the more specific pattern wins, so one of them names the file"
            " more closely"
        )
    rule = at_nearest[0]
    return Category(rule.category, rule.layer, rule.pattern)


def register_channels(rules: Iterable[ChannelRule]) -> None:
    """Register channel *rules*; a rule of the same name is replaced."""
    for rule in rules:
        _CHANNELS[:] = [known for known in _CHANNELS if known.name != rule.name]
        _CHANNELS.append(rule)


def unregister_channels(*, layer: str) -> None:
    """Withdraw every channel rule *layer* registered."""
    _CHANNELS[:] = [rule for rule in _CHANNELS if rule.layer != layer]


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
                f" it, {first.name} ({first.layer}) and {second.name}"
                f" ({second.layer}); give one the higher rank"
            )
        if answers:
            rule, answer = answers[0]
            return Channel(answer, rule.layer)
    return None


def table(kind: str) -> list[str]:
    """The category table for *kind* as lines, one rule each, for documentation."""
    return [
        f"{rule.pattern}: {rule.category} ({rule.layer})"
        for rule in category_rules(kind)
    ]
