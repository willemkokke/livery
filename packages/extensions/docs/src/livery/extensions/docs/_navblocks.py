"""A package's generated tree, the nav blocks written into it, and their markers.

The generated trees, ``docs/_generated/`` in each package and at the
root, are the docs extension's: the steps of the docs phase and the
site's own passes write them during a build, and they are removed
before each run of the docs phase, so a tree holds only what the
current build wrote. The mount merges a package's tree into its
section and strips the tree's name from every link into it.

A generator emits a block's entries into the package's generated tree,
and the authored ``docs/nav.toml`` places the block with a marker pair,
``# nav:begin <name>`` then ``# nav:end <name>``; the site assembly
renders the block where the markers sit. The block is data: a file
``docs/_generated/nav.<name>.toml`` holding a ``nav`` list of one-key
tables, each a label to a page path inside the package's ``docs/``. A
generator in any package writes that file itself: the file is what this
extension reads, and it serves no code for writing one.
"""

from __future__ import annotations

from pathlib import Path

#: A generated tree's name under ``docs/``: a name on disk, never a
#: published path. The mount merges a package's tree into the package's
#: section and strips the prefix from every link into it.
GENERATED_DIR = "_generated"

#: The generated tree's name with its slash, as a link into it is
#: written: where a generator writes a package's pages, under the
#: package's ``docs/``.
GENERATED = GENERATED_DIR + "/"

#: The package-owned nav file inside ``docs/``, which places the blocks.
NAV_TOML = "nav.toml"

#: The nav block the emitter owns; an edit between these is drift.
NAV_BEGIN = "# docs-nav:begin (generated; the emitter owns this block)"


NAV_END = "# docs-nav:end"


def nav_block_markers(name: str) -> tuple[str, str]:
    """The begin and end marker lines for a generated block in ``nav.toml``.

    The lines between a pair belong to the generator that owns
    *name*; the surrounding tree stays hand-authored.
    """
    return (f"# nav:begin {name}", f"# nav:end {name}")


def nav_block_file(generated: Path, name: str) -> Path:
    """Where a generator emits the *name* block's entries: `nav.<name>.toml`."""
    return generated / f"nav.{name}.toml"


def write_nav_block(generated: Path, name: str, entries: list[str]) -> Path:
    """Emit the *name* block's *entries* into the package's generated tree; the path.

    The file carries a `nav` list, the block's entries unindented. The
    site assembly renders it where the authored ``nav.toml`` places the
    block's marker pair, so nothing committed changes when the block
    does.
    """
    generated.mkdir(parents=True, exist_ok=True)
    path = nav_block_file(generated, name)
    body = "\n".join(entries)
    path.write_text(
        f"# The {name!r} nav block, emitted by its generator; the authored"
        f" nav.toml places it.\nnav = [\n{body}\n]\n",
        encoding="utf-8",
    )
    return path
