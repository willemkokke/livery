"""The `[toolroom]` table a directory configures its tools in, and its schema.

Every key of the table is toolroom's: the catalogue, the requirements,
the host allowances, the modes and the mirrors. A workspace the
workshop manages keeps the table in its `workshop.toml`; a directory
without one keeps it in `toolroom.toml`, under the same name, so the
store reads one shape whoever else reads the directory.

The table's schema ships beside this module as the keys it declares
in each contract, in the grammar a workshop extension declares its own
keys in; the workshop composes it into every workspace's schema.

Reach for [livery.toolroom.store.read_table][] to read a directory's
table, and [livery.toolroom.store.SCHEMA_FRAGMENT][] for its schema.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, cast

TABLE = "toolroom"
"""The table every contract keeps the tool configuration under."""

CONFIG_FILES = ("workshop.toml", "toolroom.toml")
"""The files a directory's table is read from; the first one present wins."""

SCHEMA_FRAGMENT = Path(__file__).with_name("contract.toml")
"""The table's schema: its keys in the root, package and extension contracts."""


class TableError(ValueError):
    """A configuration file the table cannot be read from; the message names it."""


def read_table(directory: Path) -> dict[str, object]:
    """The `[toolroom]` table of *directory*; empty when it declares none.

    Read from `workshop.toml` where the directory has one, and from
    `toolroom.toml` otherwise; with neither the table is empty. The
    table comes back as written: the workshop judges a workspace's keys
    against the schema it composes, and this reader judges none.

    Raises:
        TableError: when the file is not TOML, or holds `toolroom` as
            something other than a table, naming the file.
    """
    path = next(
        (directory / name for name in CONFIG_FILES if (directory / name).is_file()),
        None,
    )
    if path is None:
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise TableError(f"{path}: not TOML: {error}") from error
    table: object = data.get(TABLE, {})
    if not isinstance(table, dict):
        raise TableError(
            f"{path}: [{TABLE}] is {type(table).__name__}; it must be a table"
        )
    return dict(cast("dict[str, Any]", table))
