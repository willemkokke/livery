"""The version: provenance over a tree, git's commit with a subject.

A version names a tree, its parents, what produced it, when, a
message, the receipt of the call that produced it when there is one,
and attachments: blobs a consumer hangs on the version without
touching the tree. Its digest is the digest of its canonical JSON.

Reach for [livery.strongroom.Version][].
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field

from livery.strongroom._canonical import FormatError, Value, canonical
from livery.strongroom._digest import Digest, digest_of
from livery.strongroom._fields import (
    Subject,
    check_timestamp,
    expect_digest,
    expect_list,
    expect_object,
    expect_optional_digest,
    expect_str,
)
from livery.strongroom._tree import check_name


@dataclass(frozen=True)
class Version:
    """Provenance over a tree.

    Attributes:
        tree: the content.
        parents: the versions this one continues, in order.
        producer: who or what produced it.
        created: when, as an RFC 3339 UTC instant.
        message: free text.
        receipt: the receipt of the producing call, or None.
        attachments: name to blob digest, for a consumer's own metadata.
            Names obey the portable-name rules.
    """

    tree: Digest
    parents: tuple[Digest, ...]
    producer: Subject
    created: str
    message: str
    receipt: Digest | None = None
    attachments: Mapping[str, Digest] = field(default_factory=dict)

    def __post_init__(self) -> None:
        check_timestamp(self.created, where="version.created")
        for name in self.attachments:
            check_name(name)

    def to_json(self) -> dict[str, Value]:
        """The version as a JSON object."""
        return {
            "tree": str(self.tree),
            "parents": [str(parent) for parent in self.parents],
            "producer": self.producer.to_json(),
            "created": self.created,
            "message": self.message,
            "receipt": None if self.receipt is None else str(self.receipt),
            "attachments": {
                name: str(digest) for name, digest in self.attachments.items()
            },
        }

    @classmethod
    def from_json(cls, value: Value) -> Version:
        """Decode a version from its JSON value.

        Args:
            value: the JSON value.

        Returns:
            The version.

        Raises:
            FormatError: when the shape or a field is wrong.
        """
        fields = expect_object(
            value,
            (
                "tree",
                "parents",
                "producer",
                "created",
                "message",
                "receipt",
                "attachments",
            ),
            where="version",
        )
        parents = expect_list(fields["parents"], where="version.parents")
        attachments = fields["attachments"]
        if not isinstance(attachments, dict):
            raise FormatError("version.attachments is not a JSON object")
        return cls(
            tree=expect_digest(fields["tree"], where="version.tree"),
            parents=tuple(
                expect_digest(parent, where=f"version.parents[{index}]")
                for index, parent in enumerate(parents)
            ),
            producer=Subject.from_json(fields["producer"], where="version.producer"),
            created=expect_str(fields["created"], where="version.created"),
            message=expect_str(fields["message"], where="version.message"),
            receipt=expect_optional_digest(fields["receipt"], where="version.receipt"),
            attachments={
                expect_str(name, where="version.attachments key"): expect_digest(
                    digest, where=f"version.attachments[{name!r}]"
                )
                for name, digest in attachments.items()
            },
        )

    def encode(self) -> bytes:
        """The version's canonical JSON bytes, which its digest names."""
        return canonical(self.to_json())

    @classmethod
    def decode(cls, data: bytes) -> Version:
        """Decode a version from its stored bytes.

        Args:
            data: the object's bytes.

        Returns:
            The version.

        Raises:
            FormatError: when the bytes are not JSON, not a version, or
                not canonical.
        """
        try:
            value = json.loads(data)
        except ValueError as error:
            raise FormatError(f"version bytes are not JSON: {error}") from None
        version = cls.from_json(value)
        if version.encode() != data:
            raise FormatError("version bytes are not canonical JSON")
        return version

    def digest(self) -> Digest:
        """The version's name: the digest of its canonical JSON."""
        return digest_of(self.encode())
