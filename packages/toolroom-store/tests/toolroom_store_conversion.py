"""The one-time conversion of a spec into a record; deleted with the specs."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

from livery.toolroom.store import LAYOUT_KEYS, Artifact, Layout, Record
from livery.toolroom.store import RecordDelta as Delta
from livery.toolroom.store._spec import Definition, Spec

_BUILTIN: dict[str, Any] = {"root": "", "exe": "", "paths": (), "env": {}, "shims": {}}
_ORDER = (
    "macos-arm",
    "macos-x64",
    "linux-x64",
    "linux-arm",
    "windows-x64",
    "windows-arm",
)
_KEYS = tuple(key for key in LAYOUT_KEYS if key != "exclude")


def _plain(definition: Definition, key: str) -> Any:
    value = getattr(definition, key)
    return tuple(value) if isinstance(value, (list, tuple)) else value


def _freeze(value: Any) -> Any:
    return tuple(sorted(value.items())) if isinstance(value, dict) else value


def _majority(values: Iterable[Any]) -> Any | None:
    """The value more than half the cells share, or None."""
    listed = list(values)
    counts = Counter(_freeze(v) for v in listed)
    top, count = counts.most_common(1)[0]
    if count * 2 <= len(listed):
        return None
    return next(v for v in listed if _freeze(v) == top)


def _resolved(layers: list[dict[str, Any]], key: str) -> Any:
    for layer in reversed(layers):
        if key in layer:
            return layer[key]
    return _BUILTIN[key]


def convert(spec: Spec) -> Record:
    """The record for *spec*: every definition placed at the widest layer it fits."""
    versions = list(spec.versions.values())
    cells = {(v.version, h): d for v in versions for h, d in v.definitions.items()}
    hosts = tuple(sorted({h for _, h in cells}, key=_ORDER.index))
    tool: dict[str, Any] = {}
    for key in _KEYS:
        top = _majority(_plain(d, key) for d in cells.values())
        if top is not None and top != _BUILTIN[key]:
            tool[key] = top
    host_layouts: dict[str, dict[str, Any]] = {}
    for host in hosts:
        mine = [d for (_, h), d in cells.items() if h == host]
        for key in _KEYS:
            top = _majority(_plain(d, key) for d in mine)
            if top is not None and top != _resolved([tool], key):
                host_layouts.setdefault(host, {})[key] = top
    deltas: list[Delta] = []
    for sequence, version in enumerate(versions, start=1):
        vlayout: dict[str, Any] = {}
        for key in _KEYS:
            values = [_plain(d, key) for d in version.definitions.values()]
            shared = len({_freeze(v) for v in values}) == 1
            differs = any(
                values[0] != _resolved([tool, host_layouts.get(h, {})], key)
                for h in version.definitions
            )
            if shared and differs and values[0] != _resolved([tool], key):
                vlayout[key] = values[0]
        vhosts: dict[str, dict[str, Any]] = {}
        for host, definition in version.definitions.items():
            for key in _KEYS:
                want = _plain(definition, key)
                below = _resolved([tool, host_layouts.get(host, {}), vlayout], key)
                if want != below:
                    vhosts.setdefault(host, {})[key] = want
        artifacts = {
            h: Artifact(d.url, d.sha256 or "") for h, d in version.definitions.items()
        }
        deltas.append(
            Delta(
                sequence,
                version.version,
                "",
                artifacts,
                Layout(**vlayout),
                {h: Layout(**layout) for h, layout in vhosts.items()},
            )
        )
    return Record(
        spec.name,
        spec.description,
        spec.kind,
        spec.min_version,
        hosts,
        Layout(**tool),
        {h: Layout(**layout) for h, layout in host_layouts.items()},
        tuple(deltas),
    )
