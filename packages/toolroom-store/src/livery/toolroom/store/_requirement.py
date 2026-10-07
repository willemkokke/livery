"""The one requirement grammar: ``name[option,...]?>=floor@scope``, parsed once.

Every site that names a tool or an extension spells it the same way,
and [livery.toolroom.store.Spec][] is the one parser:

- ``[...]`` names options: a tool's profile, or an extension's
  switches;
- ``?`` marks the requirement optional;
- ``>=floor`` is the lowest version that satisfies;
- ``@scope`` limits it to some hosts: comma-separated platforms
  (``windows``) or host keys (``windows-x64``), each excluded with a
  leading ``!``.

The parser enforces the grammar alone. What a site allows of it (a
floor, options, ``?``) is the site's own rule, so a refusal there names
the site. [livery.toolroom.store.Scope][] resolves a scope against
the hosts a workspace supports.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from livery.toolroom.store._record import HOSTS, PLATFORMS

_SPEC = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9_.\-]+)\s*"
    r"(?:\[(?P<options>[^\]]*)\])?\s*"
    r"(?P<optional>\?)?\s*"
    r"(?:>=\s*(?P<floor>[^\s@]+))?\s*"
    r"(?:@\s*(?P<scope>[A-Za-z0-9,!\-\s]*))?\s*$"
)

_OPTION = re.compile(r"^[A-Za-z0-9_.\-]+$")


class SpecError(ValueError):
    """A spelling the requirement grammar does not read; the message names why."""


def _matches(host: str, tokens: tuple[str, ...]) -> bool:
    """Whether *host* is one of *tokens*, by its key or by its platform."""
    return host in tokens or host.partition("-")[0] in tokens


@dataclass(frozen=True)
class Scope:
    """The hosts a requirement is limited to, as written after ``@``.

    Attributes:
        include: The platforms and host keys named without ``!``; empty
            for every supported host.
        exclude: The platforms and host keys named with ``!``.
    """

    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()

    @classmethod
    def parse(
        cls, text: str, *, where: str = "a requirement", spelled: str = ""
    ) -> Scope:
        """The scope comma-separated *text* spells, every token checked.

        Args:
            text: The tokens after ``@``, ``linux,!linux-arm`` say.
            where: The site a refusal names.
            spelled: The whole spelling the scope came from, for a
                refusal; *text* itself when empty.

        Raises:
            SpecError: for a scope naming no token, or a token that is
                neither a platform nor a host key.
        """
        whole = spelled or text
        tokens = tuple(token.strip() for token in text.split(",") if token.strip())
        if not tokens:
            raise SpecError(
                f"{where}: {whole!r} names no host after @; a scope names a"
                f" platform ({', '.join(PLATFORMS)}) or a host key"
                f" ({', '.join(HOSTS)})"
            )
        include: list[str] = []
        exclude: list[str] = []
        for token in tokens:
            name = token.removeprefix("!")
            if name not in PLATFORMS and name not in HOSTS:
                raise SpecError(
                    f"{where}: {name!r} in {whole!r} is neither a platform"
                    f" ({', '.join(PLATFORMS)}) nor a host key ({', '.join(HOSTS)})"
                )
            (exclude if token.startswith("!") else include).append(name)
        return cls(tuple(include), tuple(exclude))

    def hosts(self, supported: Iterable[str]) -> tuple[str, ...]:
        """The hosts of *supported* this scope reaches, in their order.

        No inclusion reaches every supported host; an exclusion then
        removes its hosts, so a scope of exclusions alone starts from
        every supported host.
        """
        return tuple(
            host
            for host in supported
            if (not self.include or _matches(host, self.include))
            and not _matches(host, self.exclude)
        )

    def __bool__(self) -> bool:
        """Whether the scope limits anything: false for every host."""
        return bool(self.include or self.exclude)

    def __str__(self) -> str:
        """The scope as written after ``@``."""
        return ",".join([*self.include, *(f"!{name}" for name in self.exclude)])


@dataclass(frozen=True)
class Spec:
    """One requirement as the grammar spells it: ``name[options]?>=floor@scope``.

    Attributes:
        name: The tool's or the extension's name.
        options: The names inside ``[...]``, in their order.
        optional: Whether ``?`` marks it optional.
        floor: The lowest version that satisfies; empty for any.
        scope: The hosts it is limited to; empty for every host.
    """

    name: str
    options: tuple[str, ...] = ()
    optional: bool = False
    floor: str = ""
    scope: Scope = field(default_factory=Scope)

    @classmethod
    def parse(cls, text: str, *, where: str = "a requirement") -> Spec:
        """The requirement *text* spells.

        Args:
            text: ``ruff``, ``llvm[slim]>=20``, ``docker?@!windows``.
            where: The site a refusal names: the one that declared it.

        Raises:
            SpecError: for a spelling the grammar does not read, an
                empty or malformed option, or a scope
                [livery.toolroom.store.Scope.parse][] refuses.
        """
        found = _SPEC.match(text)
        if found is None:
            raise SpecError(
                f"{where}: {text!r} is not a requirement; spell it"
                " `name[options]?>=floor@scope`, every part after the name"
                " optional"
            )
        options: tuple[str, ...] = ()
        if found["options"] is not None:
            options = tuple(part.strip() for part in found["options"].split(","))
            bad = [option for option in options if not _OPTION.match(option)]
            if bad:
                raise SpecError(
                    f"{where}: {text!r} names an option that is not a name:"
                    f" {', '.join(repr(option) for option in bad)}"
                )
        scope = Scope()
        if found["scope"] is not None:
            scope = Scope.parse(found["scope"], where=where, spelled=text)
        return cls(
            found["name"],
            options,
            found["optional"] is not None,
            found["floor"] or "",
            scope,
        )

    def __str__(self) -> str:
        """The requirement as the grammar spells it."""
        spelled = self.name
        if self.options:
            spelled += f"[{','.join(self.options)}]"
        if self.optional:
            spelled += "?"
        if self.floor:
            spelled += f">={self.floor}"
        if self.scope:
            spelled += f"@{self.scope}"
        return spelled
