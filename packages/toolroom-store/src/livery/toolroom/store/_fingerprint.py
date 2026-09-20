"""A stat fingerprint of files: whether anything moved, without reading them."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path


def tree_fingerprint(paths: Iterable[Path | str]) -> str:
    """The fingerprint of *paths*, each a file or a directory walked whole.

    Every file contributes its path under the argument that named it,
    its size and its modification time in nanoseconds; a path that
    does not exist contributes its absence, and the arguments
    themselves contribute their spelling. Stats alone are read, so a
    build can ask whether anything moved before it loads a byte. A
    touched file with the same bytes reads as moved, which costs one
    rebuild and never a stale one.
    """
    digest = hashlib.sha256()
    for given in paths:
        root = Path(given)
        digest.update(f"{root}\0".encode())
        if root.is_dir():
            files = sorted(p for p in root.rglob("*") if p.is_file())
        elif root.is_file():
            files = [root]
        else:
            digest.update(b"absent\0")
            continue
        for path in files:
            stat = path.stat()
            relative = path.relative_to(root) if root.is_dir() else Path(path.name)
            digest.update(
                f"{relative.as_posix()}\0{stat.st_size}\0{stat.st_mtime_ns}\0".encode()
            )
    return "sha256:" + digest.hexdigest()
