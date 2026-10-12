"""The catalogue's root ref: a write the store refuses leaves the read standing."""

from __future__ import annotations

from typing import Any, cast

from livery.strongroom import Digest
from livery.strongroom import Store as ObjectStore
from livery.toolroom.store import _catalogue


def test_a_root_ref_the_store_refuses_to_write_leaves_the_read_standing() -> None:
    # The fallback: the ref is a courtesy to the next sweep, never a
    # condition of the read, so a refused write is swallowed and the
    # next read roots the tree instead.
    asked: list[str] = []

    class Refusing:
        def ref(self, namespace: str, path: str) -> Digest | None:
            return None

        def set_ref(self, namespace: str, path: str, *args: Any, **kwargs: Any) -> None:
            asked.append(path)
            raise OSError("the index is read-only here")

    digest = Digest("sha256", "0" * 64)
    _catalogue._root(cast("ObjectStore", Refusing()), "tool", digest, "https://x/")
    assert asked == ["index/tool"]  # the write was tried, and its refusal swallowed

    class Agreeing(Refusing):
        def ref(self, namespace: str, path: str) -> Digest | None:
            return digest  # already rooted: nothing is written

    asked.clear()
    _catalogue._root(cast("ObjectStore", Agreeing()), "tool", digest, "https://x/")
    assert asked == []  # already rooted: nothing is written
