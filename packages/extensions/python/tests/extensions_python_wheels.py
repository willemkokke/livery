"""Write minimal installable wheels, so a test resolves with no index at all.

A test that installs a real distribution reaches an index, and a
network fault then fails the test on the fetch rather than on the
property it pins. These wheels hold metadata, an empty module and,
when asked, entry points: all a resolution and an install read.
"""

from __future__ import annotations

import base64
import hashlib
import zipfile
from pathlib import Path


def write_wheel(
    directory: Path, name: str, version: str, *, entry_points: str = ""
) -> Path:
    """Write a minimal installable wheel for *name* at *version* into *directory*.

    *entry_points* is the text of the wheel's ``entry_points.txt``,
    which the wheel leaves out when it is empty.

    Returns:
        The wheel's path.
    """
    module = name.replace("-", "_")
    dist = f"{module}-{version}"
    path = directory / f"{module}-{version}-py3-none-any.whl"
    records: list[str] = []

    def _add(archive: zipfile.ZipFile, arcname: str, data: bytes) -> None:
        archive.writestr(arcname, data)
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
        records.append(f"{arcname},sha256={digest.decode()},{len(data)}")

    directory.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        _add(archive, f"{module}/__init__.py", f'__version__ = "{version}"\n'.encode())
        _add(
            archive,
            f"{dist}.dist-info/METADATA",
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n".encode(),
        )
        _add(
            archive,
            f"{dist}.dist-info/WHEEL",
            b"Wheel-Version: 1.0\nGenerator: livery\nRoot-Is-Purelib: true\n"
            b"Tag: py3-none-any\n",
        )
        if entry_points:
            _add(archive, f"{dist}.dist-info/entry_points.txt", entry_points.encode())
        records.append(f"{dist}.dist-info/RECORD,,")
        archive.writestr(f"{dist}.dist-info/RECORD", "\n".join(records) + "\n")
    return path
