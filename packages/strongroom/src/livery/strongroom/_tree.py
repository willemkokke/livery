"""The tree: a directory as a manifest, git's model with sizes.

An entry names a blob, a tree, or a symlink. Entries sort by their
name's UTF-8 bytes, a tree's digest is the digest of its canonical
JSON, and every name obeys the portable-name rules so a tree is
refused where the producer can fix it, never discovered at checkout
on the platform that cannot represent it.

Reach for [livery.strongroom.Tree][] to build or decode one and
[livery.strongroom.check_name][] to validate a name on its own.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from livery.strongroom._canonical import FormatError, Value, canonical
from livery.strongroom._digest import Digest, digest_of
from livery.strongroom._fields import (
    expect_bool,
    expect_digest,
    expect_int,
    expect_list,
    expect_object,
    expect_str,
    nfc,
)

EntryKind = Literal["blob", "tree"]
"""What a digest-bearing entry names."""

NAME_BUDGET = 255
"""The most UTF-8 bytes one name may take."""

# Windows refuses these as file names whatever their extension, and
# their case is ignored, so `Con.txt` is refused with `CON`.
_RESERVED_STEMS = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{n}" for n in range(1, 10)]
    + [f"LPT{n}" for n in range(1, 10)]
)

# Characters no tier-1 platform accepts in a name, plus the separators.
_FORBIDDEN = re.compile(r'[\x00-\x1f<>:"|?*/\\]')

# A drive letter, a UNC prefix, or a root: the target names one
# machine's disk and is never portable.
_ABSOLUTE_TARGET = re.compile(r"^(?:/|[A-Za-z]:|\\\\)")


def check_name(name: str) -> str:
    """Return *name* when it is portable, or refuse it.

    The rules: non-empty, not `.` or `..`, Unicode NFC, at most
    [livery.strongroom.NAME_BUDGET][] UTF-8 bytes, no separator, no
    control character, none of `<>:"|?*`, no trailing dot or space,
    and not a Windows-reserved stem such as `CON` or `LPT1` in any
    case, with or without an extension.

    Args:
        name: one path component.

    Returns:
        The name, unchanged.

    Raises:
        FormatError: naming the rule the name breaks.
    """
    where = f"entry name {name!r}"
    if not name:
        raise FormatError("entry name is empty")
    if name in (".", ".."):
        raise FormatError(f"{where} is a relative directory marker")
    nfc(name, where=where)
    if len(name.encode("utf-8")) > NAME_BUDGET:
        raise FormatError(f"{where} is longer than {NAME_BUDGET} UTF-8 bytes")
    if _FORBIDDEN.search(name) is not None:
        raise FormatError(
            f'{where} contains a separator, a control character, or one of <>:"|?*'
        )
    if name[-1] in ". ":
        raise FormatError(f"{where} ends in a dot or a space")
    stem = name.split(".", 1)[0]
    if stem.upper() in _RESERVED_STEMS:
        raise FormatError(f"{where} is a Windows-reserved name")
    return name


def check_target(target: str) -> str:
    """Return a symlink *target* when it is portable, or refuse it.

    A target is content: UTF-8 in NFC, forward slashes only, relative
    always. Whether it may escape its view is the view's policy.

    Args:
        target: the link's target string.

    Returns:
        The target, unchanged.

    Raises:
        FormatError: naming the rule the target breaks.
    """
    where = f"symlink target {target!r}"
    if not target:
        raise FormatError("symlink target is empty")
    nfc(target, where=where)
    if "\\" in target or "\x00" in target:
        raise FormatError(f"{where} contains a backslash or NUL; use forward slashes")
    if _ABSOLUTE_TARGET.match(target) is not None:
        raise FormatError(f"{where} is absolute; a target names a path inside a view")
    return target


@dataclass(frozen=True)
class Entry:
    """A blob or a subtree inside a tree.

    Attributes:
        name: the portable name, one path component.
        kind: `blob` or `tree`.
        digest: the object the entry names.
        size: the blob's byte count, or the subtree's canonical JSON
            byte count, so a materialiser plans a view without opening
            a blob.
        executable: whether a blob is marked executable. Always false
            for a tree.
    """

    name: str
    kind: EntryKind
    digest: Digest
    size: int
    executable: bool = False

    def __post_init__(self) -> None:
        check_name(self.name)
        if self.kind not in ("blob", "tree"):
            raise FormatError(
                f"entry {self.name!r} kind {self.kind!r} is not blob or tree"
            )
        expect_int(self.size, where=f"entry {self.name!r} size")
        if self.kind == "tree" and self.executable:
            raise FormatError(f"entry {self.name!r} is a tree and cannot be executable")

    def to_json(self) -> dict[str, Value]:
        """The entry as a JSON object."""
        return {
            "name": self.name,
            "kind": self.kind,
            "digest": str(self.digest),
            "size": self.size,
            "executable": self.executable,
        }


@dataclass(frozen=True)
class Link:
    """A symlink inside a tree; the target string is its whole content.

    Attributes:
        name: the portable name, one path component.
        target: the link's target, relative and forward-slashed.
    """

    name: str
    target: str

    def __post_init__(self) -> None:
        check_name(self.name)
        check_target(self.target)

    def to_json(self) -> dict[str, Value]:
        """The link as a JSON object."""
        return {"name": self.name, "kind": "symlink", "target": self.target}


@dataclass(frozen=True)
class Tree:
    """A directory: its entries, sorted by name bytes.

    Build one with [livery.strongroom.Tree.of][], which sorts and
    validates; decode one with [livery.strongroom.Tree.decode][],
    which refuses a tree whose bytes are not already canonical.

    Attributes:
        entries: the entries in name-byte order.
    """

    entries: tuple[Entry | Link, ...]

    @classmethod
    def of(cls, entries: Iterable[Entry | Link]) -> Tree:
        """Build a tree from *entries*, sorting them and refusing clashes.

        Args:
            entries: the directory's entries, in any order.

        Returns:
            The tree.

        Raises:
            FormatError: when two entries share a name, or two names are
                equal when case is ignored.
        """
        ordered = tuple(sorted(entries, key=lambda entry: entry.name.encode("utf-8")))
        seen: dict[str, str] = {}
        for entry in ordered:
            folded = entry.name.casefold()
            other = seen.get(folded)
            if other is not None:
                raise FormatError(
                    f"entries {other!r} and {entry.name!r} are the same name"
                    " when case is ignored"
                )
            seen[folded] = entry.name
        return cls(ordered)

    def to_json(self) -> dict[str, Value]:
        """The tree as a JSON object."""
        return {"entries": [entry.to_json() for entry in self.entries]}

    @classmethod
    def from_json(cls, value: Value) -> Tree:
        """Decode a tree from its JSON value.

        Args:
            value: the JSON value.

        Returns:
            The tree.

        Raises:
            FormatError: when the shape is wrong, an entry breaks a
                rule, or the entries are not in name-byte order.
        """
        fields = expect_object(value, ("entries",), where="tree")
        items = expect_list(fields["entries"], where="tree.entries")
        entries = [_entry_from_json(item, index) for index, item in enumerate(items)]
        names = [entry.name.encode("utf-8") for entry in entries]
        if names != sorted(names):
            raise FormatError("tree.entries are not sorted by name bytes")
        return cls.of(entries)

    def encode(self) -> bytes:
        """The tree's canonical JSON bytes, which its digest names."""
        return canonical(self.to_json())

    @classmethod
    def decode(cls, data: bytes) -> Tree:
        """Decode a tree from its stored bytes.

        Args:
            data: the object's bytes.

        Returns:
            The tree.

        Raises:
            FormatError: when the bytes are not JSON, not a tree, or not
                the canonical encoding of the tree they describe. A
                non-canonical tree would have a second name for the
                same content.
        """
        import json

        try:
            value = json.loads(data)
        except ValueError as error:
            raise FormatError(f"tree bytes are not JSON: {error}") from None
        tree = cls.from_json(value)
        if tree.encode() != data:
            raise FormatError("tree bytes are not canonical JSON")
        return tree

    def digest(self) -> Digest:
        """The tree's name: the digest of its canonical JSON."""
        return digest_of(self.encode())


def _entry_from_json(value: Value, index: int) -> Entry | Link:
    where = f"tree.entries[{index}]"
    if not isinstance(value, dict):
        raise FormatError(f"{where} is not a JSON object")
    kind = expect_str(value.get("kind"), where=f"{where}.kind")
    if kind == "symlink":
        fields = expect_object(value, ("name", "kind", "target"), where=where)
        return Link(
            expect_str(fields["name"], where=f"{where}.name"),
            expect_str(fields["target"], where=f"{where}.target"),
        )
    entry_kind = _entry_kind(kind, where=where)
    fields = expect_object(
        value, ("name", "kind", "digest", "size", "executable"), where=where
    )
    return Entry(
        expect_str(fields["name"], where=f"{where}.name"),
        entry_kind,
        expect_digest(fields["digest"], where=f"{where}.digest"),
        expect_int(fields["size"], where=f"{where}.size"),
        expect_bool(fields["executable"], where=f"{where}.executable"),
    )


def _entry_kind(kind: str, *, where: str) -> EntryKind:
    # Spelled as two comparisons so every checker narrows the literal.
    if kind == "blob":
        return "blob"
    if kind == "tree":
        return "tree"
    raise FormatError(f"{where}.kind {kind!r} is not blob, tree or symlink")
