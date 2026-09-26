"""The store's home: one directory per machine, shared by every checkout.

Under the home: `store/`, the strongroom store with the `tools` and
`urls` namespaces; `tools/<name>@<version>/`, the views of the tools'
trees a shell runs from; `uv/` and `npm/`, the delegated kinds'
directories. Nothing here reads an environment variable: the caller
passes the home, toolroom's machinery its data directory, hse its own.

Reach for [livery.toolroom.store.Home][] and its `open_store`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from livery.strongroom import Namespace, Source, Store

TOOLS = "tools"
"""The namespace of installed tools: `tools/<name>@<version>` names the
extracted archive's tree; write-once, so a version never changes under
a checkout that pinned it."""

URLS = "urls"
"""The namespace of URL-keyed downloads, the convention strongroom
publishes; volatile."""

NAMESPACES = (Namespace(TOOLS, "volatile"), Namespace(URLS, "volatile"))
"""The store's namespaces. `tools/<name>@<version>` names the tree a
version runs from and moves by compare-and-swap when the record's
layout for the version changes; the deployment that produced the tree
is recorded beside the ref. The artifact never changes under a
version, since landing verifies its bytes."""
"""The namespaces the home's store is created and opened with."""


@dataclass(frozen=True)
class Home:
    """The store's directories under one root.

    Attributes:
        root: The home, `footman.data_dir() / "toolroom"` for the
            machinery, whatever the consumer passes otherwise.
    """

    root: Path

    @property
    def store(self) -> Path:
        """The strongroom store."""
        return self.root / "store"

    @property
    def tools(self) -> Path:
        """Where each tool version's view lives, `tools/<name>@<version>`."""
        return self.root / "tools"

    @property
    def uv(self) -> Path:
        """The uv tool installs, one directory per `<name>@<version>`, and pythons."""
        return self.root / "uv"

    @property
    def npm(self) -> Path:
        """The npm installs, one directory per `<name>@<version>`, by either runtime."""
        return self.root / "npm"

    def tool_dir(self, name: str, version: str) -> Path:
        """The view of *name* at *version*."""
        return self.tools / f"{name}@{version}"

    def open_store(
        self, *, sources: Iterable[Source] = (), offline: bool = False
    ) -> Store:
        """Open the home's strongroom store, creating it on first use.

        A store that exists is opened with the same namespaces it was
        created with; a directory that holds another layout refuses
        as strongroom refuses it. *sources* are the tiers the store
        consults for an object it lacks, and *offline* keeps it from
        every origin.
        """
        if (self.store / "strongroom.json").is_file():
            return Store.open(
                self.store, namespaces=NAMESPACES, sources=sources, offline=offline
            )
        self.root.mkdir(parents=True, exist_ok=True)
        return Store.create(
            self.store, namespaces=NAMESPACES, sources=sources, offline=offline
        )
