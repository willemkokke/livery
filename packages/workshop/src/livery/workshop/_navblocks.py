"""The nav blocks generators write, and the markers that place them in a package's nav.

A markdown convention the base keeps: a generator emits a block's
entries into the package's generated tree, and the authored
``docs/nav.toml`` places the block with a marker pair. The site
assembly, a layer, renders the block where the markers sit; the base
needs only the format, so a generator in any layer can write one.
"""

from __future__ import annotations

from pathlib import Path

from livery.footman import fail

#: The nav block the emitter owns; an edit between these is drift.
NAV_BEGIN = "# docs-nav:begin (generated; the emitter owns this block)"


NAV_END = "# docs-nav:end"


def nav_block_markers(name: str) -> tuple[str, str]:
    """The begin and end marker lines for a generated block in ``nav.toml``.

    The lines between a pair belong to the generator that owns
    *name*; the surrounding tree stays hand-authored.
    """
    return (f"# nav:begin {name}", f"# nav:end {name}")


def rewrite_nav_block(path: Path, name: str, entries: list[str]) -> None:
    """Replace the *name* block's lines inside the ``nav.toml`` at *path*.

    *entries* arrive unindented; each is re-indented to the begin
    marker's own indentation, so a generator never bakes indentation
    into its output and the author stays free to move the block. Both
    markers must already exist: where the block sits in the tree is
    the author's decision, and a missing marker refuses naming the
    file and the block. Idempotent: rewriting the same entries leaves
    the file byte-identical.
    """
    begin, end = nav_block_markers(name)
    if not path.is_file():
        fail(f"{path} does not exist: the nav block {name!r} has no home")
    lines = path.read_text("utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == begin]
    ends = [i for i, line in enumerate(lines) if line.strip() == end]
    if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
        fail(
            f"{path} does not carry the {name!r} block markers"
            f" ({begin!r} then {end!r}, once each): add them where the"
            " block belongs in the tree"
        )
    indent = lines[starts[0]][: len(lines[starts[0]]) - len(lines[starts[0]].lstrip())]
    body = [indent + entry if entry else "" for entry in entries]
    rewritten = lines[: starts[0] + 1] + body + lines[ends[0] :]
    path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")


def nav_block_file(generated: Path, name: str) -> Path:
    """Where a generator emits the *name* block's entries: `nav.<name>.toml`."""
    return generated / f"nav.{name}.toml"


def write_nav_block(generated: Path, name: str, entries: list[str]) -> Path:
    """Emit the *name* block's *entries* into the package's generated tree; the path.

    The file carries a `nav` list, the block's entries unindented, the
    way `rewrite_nav_block` writes them between markers. The emitter
    renders it where the authored ``nav.toml`` places the block's
    marker pair, so nothing committed changes when the block does.
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
