"""The site's config beyond the docs extension's own tables, and the members policy.

The ``docs.config`` query's handle and its combine rule live here,
apart from the site's verbs, so the declaration can name the rule and
importing this module registers nothing. A listed extension answers
the query with the function that writes its tables for one config,
given what the config covers (`Scope`); `joined` puts each answer's
tables after the docs extension's own, in composition order, and
parses the result. The ``docs.members`` query's handle and its two answers live
here too, for the extension whose tables render the reference.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from livery.footman import fail
from livery.workshop import Query, answer

if TYPE_CHECKING:
    from livery.workshop import Package


@dataclass(frozen=True)
class Scope:
    """What one assembled config covers, as each writer of tables is handed it.

    Attributes:
        root: The workspace root.
        packages: The packages whose sections the config carries, in
            the site's order: every package for the site's own config,
            one for a package's preview.
        anchor: The path from the config's directory to the root, with
            a closing slash: empty for the site's own config at the
            root, ``../../`` for a preview two levels below it. A path
            relative to the root reaches the same file from the config
            with this before it.
        published: The published site's address when the config is a
            preview's and the root contract names one, so a reference
            the preview does not carry resolves on the published site;
            empty for the site's own config.
    """

    root: Path
    packages: tuple[Package, ...]
    anchor: str = ""
    published: str = ""


Writer = Callable[[Scope], str]
"""An answer to the ``docs.config`` query.

The function that writes an extension's tables for the config a
`Scope` describes, as TOML text, empty when the extension has nothing
to say there.
"""

#: The query asking the installed extensions for their tables in the
#: site's config: each answers with a `Writer`, and the answers keep
#: composition order.
CONFIG: Query[tuple[tuple[str, Writer], ...]] = Query("docs.config")

#: The query deciding whether private members are documented: ``public``
#: keeps the reference's default filter, ``all`` documents every
#: member. The nearest answering extension decides; the base answers
#: nothing, so the declared default stands until an extension answers.
MEMBERS: Query[str] = Query("docs.members")

#: The two answers ``docs.members`` takes, as its declaration lists them.
PUBLIC_MEMBERS = "public"
ALL_MEMBERS = "all"
MEMBERS_POLICIES = (PUBLIC_MEMBERS, ALL_MEMBERS)


def members_policy(root: Path) -> str:
    """The members policy the listed extensions answer, ``public`` or ``all``."""
    return answer(root, MEMBERS)


def writers(
    answers: Mapping[str, Any], lookup: object
) -> tuple[tuple[str, Writer], ...]:
    """``docs.config``'s rule: each answering extension with its writer, in order.

    The answers come in composition order.

    Raises:
        ValueError: when an answer is not a function, naming the
            extension; the asker names the query.
    """
    del lookup
    found: list[tuple[str, Writer]] = []
    for extension, value in answers.items():
        if not callable(value):
            raise ValueError(
                f"{extension} answers {value!r}, and an answer is the function that"
                " writes the extension's tables for a config"
            )
        found.append((extension, cast("Writer", value)))
    return tuple(found)


def written_tables(text: str) -> list[str]:
    """The tables *text* writes keys into, as dotted paths, in the order met.

    A table that holds only other tables, such as ``project`` above
    ``[project.theme]``, is the path to them and writes nothing of its
    own; an array of tables is written where it stands.

    Raises:
        tomllib.TOMLDecodeError: when *text* is not TOML.
    """
    found: list[str] = []

    def walk(table: Mapping[str, object], path: tuple[str, ...]) -> None:
        nested = {key: value for key, value in table.items() if isinstance(value, dict)}
        if path and (len(nested) < len(table) or not table):
            found.append(".".join(path))
        for key, value in nested.items():
            walk(cast("Mapping[str, object]", value), (*path, key))

    walk(tomllib.loads(text), ())
    return found


def joined(own: str, scope: Scope) -> str:
    """*own*, the docs extension's config, with each answer's tables after it.

    The installed extensions' answers to ``docs.config`` are asked
    about *scope*'s root and each writes its tables for *scope*, in
    composition order; an answer that writes nothing adds nothing. Each answer's
    tables follow the text before them after one blank line, and the
    text is parsed once each answer joins it, so the answer that
    breaks it is the one named.

    Raises:
        Failed: when an answer's text is not TOML, when two answers
            write one table, naming both and the table, or when the
            config stops parsing once an answer joins it, naming the
            answer.
    """
    text = own.rstrip("\n") + "\n"
    writing: dict[str, str] = {}
    for extension, writer in answer(scope.root, CONFIG):
        tables = writer(scope).strip("\n")
        if not tables:
            continue
        try:
            names = written_tables(tables)
        except tomllib.TOMLDecodeError as error:
            fail(
                f"{extension} writes tables for the site's config that are not"
                f" TOML: {error}"
            )
        for name in names:
            if name in writing:
                fail(
                    f"{writing[name]} and {extension} both write [{name}] in the"
                    " site's config; one extension writes a table"
                )
            writing[name] = extension
        text += "\n" + tables + "\n"
        try:
            tomllib.loads(text)
        except tomllib.TOMLDecodeError as error:
            fail(
                f"the site's config does not parse once {extension}'s tables join"
                f" it: {error}"
            )
    return text
