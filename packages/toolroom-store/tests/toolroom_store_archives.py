"""Archives for the store's tests: a zip and a tar with modes, and a digest."""

from __future__ import annotations

import hashlib
import io
import tarfile
import zipfile


def make_zip(files: dict[str, bytes], *, executable: tuple[str, ...] = ()) -> bytes:
    """A zip whose members carry Unix modes, an executable bit where named."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            info = zipfile.ZipInfo(name)
            mode = 0o755 if name in executable else 0o644
            info.external_attr = mode << 16
            archive.writestr(info, data)
    return buffer.getvalue()


def make_tar(files: dict[str, bytes], *, executable: tuple[str, ...] = ()) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755 if name in executable else 0o644
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
