"""Digest strings: the name of every object.

A name is `<algorithm>:<encoded>` in the OCI digest grammar, and the
registry says which algorithms may name bytes. sha256 is required and
is the only entry; the registry exists so a successor is an entry and
never a migration. One address space uses one algorithm, recorded in
its root manifest.

Reach for [livery.strongroom.Digest][] to parse a name and
[livery.strongroom.digest_of][] to name bytes.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import IO, Final, Protocol

from livery.strongroom._canonical import FormatError

# The OCI grammar: algorithm ":" encoded. The registry narrows the
# encoded form per algorithm; the grammar alone admits nothing.
_GRAMMAR = re.compile(
    r"^(?P<algorithm>[a-z0-9]+(?:[.+_-][a-z0-9]+)*):(?P<encoded>[a-zA-Z0-9=_-]+)$"
)

_STREAM_CHUNK = 1 << 20


class Hasher(Protocol):
    """What a registry entry's constructor returns: hashlib's shape."""

    def update(self, data: bytes, /) -> None: ...

    def hexdigest(self) -> str: ...


class HashConstructor(Protocol):
    """A hashlib-style constructor: optional initial bytes, a hasher back."""

    def __call__(self, data: bytes = b"", /) -> Hasher: ...


@dataclass(frozen=True)
class Algorithm:
    """One registry entry: how an algorithm names bytes.

    Attributes:
        name: the algorithm's spelling in a digest string.
        encoded: the pattern the encoded part must match in full.
        constructor: the hashlib constructor that computes it.
    """

    name: str
    encoded: re.Pattern[str]
    constructor: HashConstructor


SHA256: Final = Algorithm("sha256", re.compile(r"^[a-f0-9]{64}$"), hashlib.sha256)
"""The required algorithm, and the only one in the registry."""

ALGORITHMS: Final[dict[str, Algorithm]] = {SHA256.name: SHA256}
"""The registry. Only cryptographic, full-length digests may name bytes."""


@dataclass(frozen=True, order=True)
class Digest:
    """A parsed digest string.

    Attributes:
        algorithm: the registry entry's name, `sha256`.
        encoded: the lowercase hex of the digest.
    """

    algorithm: str
    encoded: str

    def __str__(self) -> str:
        return f"{self.algorithm}:{self.encoded}"

    @classmethod
    def parse(cls, text: str) -> Digest:
        """Parse a digest string, refusing anything the registry lacks.

        Args:
            text: the `<algorithm>:<encoded>` spelling.

        Returns:
            The digest.

        Raises:
            FormatError: when the text is not in the OCI grammar, the
                algorithm is not registered, or the encoded part is
                not the full lowercase form the algorithm requires. A
                truncated or uppercase digest is refused so one object
                has one name.
        """
        match = _GRAMMAR.match(text)
        if match is None:
            raise FormatError(
                f"digest {text!r} is not '<algorithm>:<encoded>' in the OCI grammar"
            )
        algorithm = match["algorithm"]
        entry = ALGORITHMS.get(algorithm)
        if entry is None:
            known = ", ".join(sorted(ALGORITHMS))
            raise FormatError(
                f"digest algorithm {algorithm!r} is not in the registry"
                f" (registered: {known})"
            )
        encoded = match["encoded"]
        if entry.encoded.match(encoded) is None:
            raise FormatError(
                f"digest {text!r} is not a full lowercase {algorithm}"
                " digest; a truncated or re-cased digest is a second"
                " name for the same bytes"
            )
        return cls(algorithm, encoded)

    @property
    def path_parts(self) -> tuple[str, str, str]:
        """The object's path under `objects/`: algorithm, fan-out, rest."""
        return (self.algorithm, self.encoded[:2], self.encoded[2:])


def digest_of(data: bytes, *, algorithm: str = SHA256.name) -> Digest:
    """Name *data* under *algorithm*.

    Args:
        data: the bytes to name.
        algorithm: a registry entry's name; `sha256` by default.

    Returns:
        The digest.

    Raises:
        FormatError: when the algorithm is not registered.
    """
    entry = registered(algorithm)
    return Digest(entry.name, entry.constructor(data).hexdigest())


def digest_stream(stream: IO[bytes], *, algorithm: str = SHA256.name) -> Digest:
    """Name the bytes read from *stream* to its end, without holding them.

    Args:
        stream: an open binary stream, read in chunks until empty.
        algorithm: a registry entry's name; `sha256` by default.

    Returns:
        The digest.

    Raises:
        FormatError: when the algorithm is not registered.
    """
    entry = registered(algorithm)
    hasher = entry.constructor(b"")
    while chunk := stream.read(_STREAM_CHUNK):
        hasher.update(chunk)
    return Digest(entry.name, hasher.hexdigest())


def registered(algorithm: str) -> Algorithm:
    """The registry entry for *algorithm*, or a refusal naming the registry."""
    entry = ALGORITHMS.get(algorithm)
    if entry is None:
        known = ", ".join(sorted(ALGORITHMS))
        raise FormatError(
            f"digest algorithm {algorithm!r} is not in the registry"
            f" (registered: {known})"
        )
    return entry
