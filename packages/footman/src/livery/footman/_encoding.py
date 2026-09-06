"""Byte-order marks — the only encoding hint a file carries about itself.

footman is UTF-8 end to end. The interfaces it does not control are the
files other tools wrote: a shell rc that Windows PowerShell 5 saved as
UTF-16, a config a Windows editor saved as UTF-8 with a leading mark. A
byte-order mark is the one autodetection that is never a guess, so the
table lives here once — the completion installer rewrites rc files by it
(`_shellcomp`), and the config reader accepts or refuses by it
(`_config`). Two copies would eventually disagree, and a disagreement
about a file's encoding corrupts the file.

Stdlib only and import-free of the rest of footman: `_config` is on every
invocation's path, so this module must cost nothing to import.
"""

from __future__ import annotations

# UTF-8 with a mark is still UTF-8 — the mark is stripped, never refused.
UTF8_BOM = b"\xef\xbb\xbf"

# (mark, decode-with, append-with). The append encoder is BOM-free
# (`utf-8`, `utf-16-le`/`-be`) so writing a tail never injects a second mark.
BOM_ENCODINGS: tuple[tuple[bytes, str, str], ...] = (
    (UTF8_BOM, "utf-8-sig", "utf-8"),
    (b"\xff\xfe", "utf-16", "utf-16-le"),
    (b"\xfe\xff", "utf-16", "utf-16-be"),
)


def sniff_bom(raw: bytes) -> tuple[str, str] | None:
    """The (decode, append) encodings *raw*'s leading mark implies, or `None`
    when it carries no byte-order mark at all."""
    for bom, decode, append in BOM_ENCODINGS:
        if raw.startswith(bom):
            return decode, append
    return None
