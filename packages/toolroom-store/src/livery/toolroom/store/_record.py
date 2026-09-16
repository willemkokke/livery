"""The tool record: one tool, every version tracked, and each host's deployment.

A record is inert data, authored in git as one directory per tool:
``tool.json`` carries the tool axis, what does not move with a
version, and ``deltas/<nnnn>-<version>.json`` carries one version's
arrival each, forward and append-only. A deployment resolves through
four layers, most specific winning: the tool's layout, the host's
override of it, the version's override, and the version's override for
one host. Resolution is total and checked: loading a record resolves
every host of every version and refuses one that resolves incomplete,
an override that names a host or version the record does not carry,
an override that restates the value it inherits, and a delta out of
sequence. A record names a kind, per host an artifact with a mandatory
digest, and the layout; it never runs a command.

Reach for [livery.toolroom.store.Record.load][] to read a record and
[livery.toolroom.store.resolve][] for one host's deployment at one
version; the rest is what a load validates.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PLATFORMS = ("windows", "macos", "linux")
"""The operating systems a record may name."""

ARCHES = ("x64", "arm")
"""The CPU architectures a record may name."""

HOSTS = (
    "macos-arm",
    "macos-x64",
    "linux-x64",
    "linux-arm",
    "windows-x64",
    "windows-arm",
)
"""The six host keys, `<platform>-<arch>`. A record carries any subset."""

KINDS = ("archive", "binary", "uv-tool", "uv-python", "bun-install", "system-check")
"""The installer kinds: `archive` and `binary` download by URL and land
in the store; `uv-tool` and `uv-python` delegate to uv; `bun-install`
to bun; `system-check` verifies a system tool against `min_version`,
and may carry an artifact for a host that has no system tool."""

DOWNLOAD_KINDS = frozenset({"archive", "binary"})
"""The kinds whose every host needs an artifact."""

PACKAGE_VAR = "$package"
"""The one substitution a deployment's env values may carry: the install root."""

TOOL_FILE = "tool.json"
"""The tool axis, in the record's directory."""

DELTAS_DIR = "deltas"
"""The deltas' directory, in the record's directory."""

_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_DELTA_NAME = re.compile(r"^(?P<sequence>\d{4})-(?P<version>.+)\.json$")

LAYOUT_KEYS = ("root", "exe", "entry_points", "paths", "env", "shims", "exclude")
"""The layout fields an override may set, in the record's key order."""

_TOOL_KEYS = (
    "name",
    "description",
    "kind",
    "min_version",
    "hosts",
    "layout",
    "host_layouts",
)
_DELTA_KEYS = ("sequence", "version", "date", "artifacts", "layout", "host_layouts")
_ARTIFACT_KEYS = ("url", "sha256")


class RecordError(ValueError):
    """A record that breaks a rule; the message names the field."""


def _object(value: Any, allowed: tuple[str, ...], *, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RecordError(f"{where}: not a JSON object")
    extra = sorted(set(value) - set(allowed))
    if extra:
        raise RecordError(f"{where}: unknown keys {', '.join(extra)}")
    return value


def _text(value: Any, *, where: str) -> str:
    if not isinstance(value, str):
        raise RecordError(f"{where}: not a string")
    return value


def _texts(value: Any, *, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise RecordError(f"{where}: not a list of strings")
    return tuple(value)


def _mapping(value: Any, *, where: str) -> dict[str, str]:
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise RecordError(f"{where}: not an object of strings")
    return dict(value)


@dataclass(frozen=True)
class Layout:
    """One layer's layout fields; a field left `None` is inherited.

    Attributes:
        root: The directory inside the archive to hoist to the install
            root; empty keeps the archive's own top.
        exe: The executable's name for a `binary` download.
        entry_points: Install-relative paths of the executables the
            deployment puts on PATH, annotated here and never
            discovered: they are what a bin directory links and what
            the collected tree marks executable on every platform.
        paths: Install-relative directories to put on PATH.
        env: Environment variables to set; a value may carry
            `PACKAGE_VAR`, the install root.
        shims: Link name to an executable the install carries, such as
            `node` to `bun`.
        exclude: Patterns of archive members left out before import,
            install-relative and forward-slashed, `fnmatch` style.
    """

    root: str | None = None
    exe: str | None = None
    entry_points: tuple[str, ...] | None = None
    paths: tuple[str, ...] | None = None
    env: Mapping[str, str] | None = None
    shims: Mapping[str, str] | None = None
    exclude: tuple[str, ...] | None = None

    def set_fields(self) -> tuple[str, ...]:
        """The fields this layer sets, in `LAYOUT_KEYS` order."""
        return tuple(key for key in LAYOUT_KEYS if getattr(self, key) is not None)

    def to_json(self) -> dict[str, Any]:
        """The set fields as a JSON object, in `LAYOUT_KEYS` order."""
        out: dict[str, Any] = {}
        for key in LAYOUT_KEYS:
            value = getattr(self, key)
            if value is None:
                continue
            out[key] = list(value) if isinstance(value, tuple) else _plain(value)
        return out

    @classmethod
    def from_json(cls, value: Any, *, where: str) -> Layout:
        """A layer from its JSON object.

        Raises:
            RecordError: when the object is not a layout, naming *where*.
        """
        data = _object(value, LAYOUT_KEYS, where=where)
        return cls(
            _text(data["root"], where=f"{where} root") if "root" in data else None,
            _text(data["exe"], where=f"{where} exe") if "exe" in data else None,
            _texts(data["entry_points"], where=f"{where} entry_points")
            if "entry_points" in data
            else None,
            _texts(data["paths"], where=f"{where} paths") if "paths" in data else None,
            _mapping(data["env"], where=f"{where} env") if "env" in data else None,
            _mapping(data["shims"], where=f"{where} shims")
            if "shims" in data
            else None,
            _texts(data["exclude"], where=f"{where} exclude")
            if "exclude" in data
            else None,
        )


def _plain(value: Any) -> Any:
    return dict(value) if isinstance(value, Mapping) else value


@dataclass(frozen=True)
class Artifact:
    """One host's artifact for one version.

    Attributes:
        url: Where the artifact downloads from.
        sha256: The artifact's lowercase hex sha256, the object's name
            in the store; mandatory, a record without one is refused.
    """

    url: str
    sha256: str

    def __post_init__(self) -> None:
        if not _SHA256.match(self.sha256):
            raise RecordError(
                f"artifact sha256 {self.sha256!r} is not 64 lowercase hex digits"
            )
        if not self.url:
            raise RecordError("artifact without a url")

    def to_json(self) -> dict[str, str]:
        """The artifact as a JSON object."""
        return {"url": self.url, "sha256": self.sha256}

    @classmethod
    def from_json(cls, value: Any, *, where: str) -> Artifact:
        """An artifact from its JSON object.

        Raises:
            RecordError: when the object is not an artifact.
        """
        data = _object(value, _ARTIFACT_KEYS, where=where)
        for required in _ARTIFACT_KEYS:
            if required not in data:
                raise RecordError(f"{where}: no {required}")
        try:
            return cls(
                _text(data["url"], where=f"{where} url"),
                _text(data["sha256"], where=f"{where} sha256"),
            )
        except RecordError as error:
            raise RecordError(f"{where}: {error}") from None


@dataclass(frozen=True)
class Deployment:
    """One host's complete deployment of one version: the artifact and the layout.

    Every field carries a value: the layers resolved it, and a load
    refused any host that would leave one open.

    Attributes:
        url: Where the artifact downloads from.
        sha256: The artifact's sha256.
        root: See [livery.toolroom.store.Layout][].
        exe: See [livery.toolroom.store.Layout][].
        entry_points: See [livery.toolroom.store.Layout][]; never empty
            for a kind the store downloads.
        paths: See [livery.toolroom.store.Layout][]; never empty.
        env: See [livery.toolroom.store.Layout][].
        shims: See [livery.toolroom.store.Layout][].
        exclude: See [livery.toolroom.store.Layout][].
    """

    url: str
    sha256: str
    root: str
    exe: str
    entry_points: tuple[str, ...]
    paths: tuple[str, ...]
    env: dict[str, str]
    shims: dict[str, str]
    exclude: tuple[str, ...]


#: What a field resolves to when no layer sets it.
_BUILTIN: dict[str, Any] = {
    "root": "",
    "exe": "",
    "entry_points": (),
    "paths": (),
    "env": {},
    "shims": {},
    "exclude": (),
}


@dataclass(frozen=True)
class Delta:
    """One version's arrival: its artifacts and what it overrides.

    Attributes:
        sequence: The delta's place in the record, from 1, consecutive.
        version: The version string this delta adds.
        date: When the version arrived, `YYYY-MM-DD`; empty when the
            record does not know.
        artifacts: Per host key, the artifact; the keys are the hosts
            this version has.
        layout: The version's override of the tool's layout.
        host_layouts: The version's override for one host each.
    """

    sequence: int
    version: str
    date: str = ""
    artifacts: dict[str, Artifact] = field(default_factory=dict)
    layout: Layout = field(default_factory=Layout)
    host_layouts: dict[str, Layout] = field(default_factory=dict)

    @property
    def hosts(self) -> tuple[str, ...]:
        """The hosts this version has, the artifacts' keys."""
        return tuple(self.artifacts)

    @property
    def file_name(self) -> str:
        """The delta's file name, `<nnnn>-<version>.json`."""
        return f"{self.sequence:04d}-{self.version}.json"

    def to_json(self) -> dict[str, Any]:
        """The delta as a JSON object, keys in the record's order."""
        out: dict[str, Any] = {
            "sequence": self.sequence,
            "version": self.version,
            "date": self.date,
            "artifacts": {k: v.to_json() for k, v in self.artifacts.items()},
        }
        if self.layout.set_fields():
            out["layout"] = self.layout.to_json()
        if self.host_layouts:
            out["host_layouts"] = {k: v.to_json() for k, v in self.host_layouts.items()}
        return out

    @classmethod
    def from_json(cls, value: Any, *, where: str) -> Delta:
        """A delta from its JSON object.

        Raises:
            RecordError: when the object is not a delta.
        """
        data = _object(value, _DELTA_KEYS, where=where)
        for required in ("sequence", "version", "artifacts"):
            if required not in data:
                raise RecordError(f"{where}: no {required}")
        sequence = data["sequence"]
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
            raise RecordError(f"{where}: sequence is not a positive integer")
        raw_artifacts = data["artifacts"]
        if not isinstance(raw_artifacts, dict):
            raise RecordError(f"{where}: artifacts is not an object")
        raw_hosts = data.get("host_layouts", {})
        if not isinstance(raw_hosts, dict):
            raise RecordError(f"{where}: host_layouts is not an object")
        return cls(
            sequence,
            _text(data["version"], where=f"{where} version"),
            _text(data.get("date", ""), where=f"{where} date"),
            {
                str(k): Artifact.from_json(v, where=f"{where} artifacts[{k}]")
                for k, v in raw_artifacts.items()
            },
            Layout.from_json(data.get("layout", {}), where=f"{where} layout"),
            {
                str(k): Layout.from_json(v, where=f"{where} host_layouts[{k}]")
                for k, v in raw_hosts.items()
            },
        )


@dataclass(frozen=True)
class Record:
    """A tool across every version tracked and every host it has.

    Loading a record validates it whole; see the module docstring for
    what a load refuses.

    Attributes:
        name: The tool's name, the record directory's name and the
            store's `tools/<name>@<version>` stem.
        description: One line on what the tool is.
        kind: One of `KINDS`.
        min_version: The floor a `system-check` tool must reach.
        hosts: The host keys the tool has, from `HOSTS`.
        layout: The tool's layout, the layer every host and version
            inherits.
        host_layouts: The tool's override for one host each.
        deltas: The versions, in sequence.
    """

    name: str
    description: str = ""
    kind: str = "archive"
    min_version: str = ""
    hosts: tuple[str, ...] = ()
    layout: Layout = field(default_factory=Layout)
    host_layouts: dict[str, Layout] = field(default_factory=dict)
    deltas: tuple[Delta, ...] = ()

    def __post_init__(self) -> None:
        validate(self)

    @property
    def versions(self) -> tuple[str, ...]:
        """The versions tracked, in sequence."""
        return tuple(delta.version for delta in self.deltas)

    def delta_for(self, version: str) -> Delta:
        """The delta that added *version*.

        Raises:
            RecordError: when the record does not track it, naming the
                versions it does.
        """
        for delta in self.deltas:
            if delta.version == version:
                return delta
        raise RecordError(
            f"{self.name}: no version {version!r}; the record tracks"
            f" {', '.join(self.versions) or 'none'}"
        )

    def hosts_of(self, version: str) -> tuple[str, ...]:
        """The hosts *version* has."""
        return self.delta_for(version).hosts

    def to_json(self) -> dict[str, Any]:
        """The tool axis as a JSON object, keys in the record's order."""
        out: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "kind": self.kind,
            "min_version": self.min_version,
            "hosts": list(self.hosts),
        }
        if self.layout.set_fields():
            out["layout"] = self.layout.to_json()
        if self.host_layouts:
            out["host_layouts"] = {k: v.to_json() for k, v in self.host_layouts.items()}
        return out

    @classmethod
    def from_json(
        cls, value: Any, deltas: tuple[Delta, ...] = (), *, where: str = TOOL_FILE
    ) -> Record:
        """A record from the tool axis's JSON object and its *deltas*.

        Raises:
            RecordError: when the object is not a tool axis, or the
                record does not validate.
        """
        data = _object(value, _TOOL_KEYS, where=where)
        if "name" not in data:
            raise RecordError(f"{where}: no name")
        raw_hosts = data.get("host_layouts", {})
        if not isinstance(raw_hosts, dict):
            raise RecordError(f"{where}: host_layouts is not an object")
        return cls(
            _text(data["name"], where=f"{where} name"),
            _text(data.get("description", ""), where=f"{where} description"),
            _text(data.get("kind", "archive"), where=f"{where} kind"),
            _text(data.get("min_version", ""), where=f"{where} min_version"),
            _texts(data.get("hosts", []), where=f"{where} hosts"),
            Layout.from_json(data.get("layout", {}), where=f"{where} layout"),
            {
                str(k): Layout.from_json(v, where=f"{where} host_layouts[{k}]")
                for k, v in raw_hosts.items()
            },
            deltas,
        )

    @classmethod
    def load(cls, directory: Path) -> Record:
        """The record at *directory*: its tool axis and every delta, in sequence.

        Raises:
            RecordError: naming the file, for a file that is not JSON,
                a delta whose name and sequence disagree, or a record
                that does not validate.
        """
        tool_path = directory / TOOL_FILE
        if not tool_path.is_file():
            raise RecordError(f"{directory}: no {TOOL_FILE}")
        deltas: list[Delta] = []
        deltas_dir = directory / DELTAS_DIR
        names = (
            sorted(p.name for p in deltas_dir.glob("*.json"))
            if deltas_dir.is_dir()
            else []
        )
        for name in names:
            match = _DELTA_NAME.match(name)
            if match is None:
                raise RecordError(
                    f"{directory.name}/{DELTAS_DIR}/{name}: not named"
                    " <nnnn>-<version>.json"
                )
            delta = Delta.from_json(
                _read_json(
                    deltas_dir / name, where=f"{directory.name}/{DELTAS_DIR}/{name}"
                ),
                where=f"{directory.name}/{DELTAS_DIR}/{name}",
            )
            if delta.file_name != name:
                raise RecordError(
                    f"{directory.name}/{DELTAS_DIR}/{name}: the file says"
                    f" {delta.file_name} (sequence {delta.sequence},"
                    f" version {delta.version})"
                )
            deltas.append(delta)
        record = cls.from_json(
            _read_json(tool_path, where=f"{directory.name}/{TOOL_FILE}"),
            tuple(deltas),
            where=f"{directory.name}/{TOOL_FILE}",
        )
        if record.name != directory.name:
            raise RecordError(
                f"{directory.name}/{TOOL_FILE}: the record is named"
                f" {record.name!r}, its directory {directory.name!r}"
            )
        return record

    def save(self, directory: Path) -> None:
        """Write the record under *directory*: the tool axis and every delta."""
        directory.mkdir(parents=True, exist_ok=True)
        (directory / TOOL_FILE).write_text(_dumps(self.to_json()), encoding="utf-8")
        deltas_dir = directory / DELTAS_DIR
        deltas_dir.mkdir(exist_ok=True)
        for delta in self.deltas:
            (deltas_dir / delta.file_name).write_text(
                _dumps(delta.to_json()), encoding="utf-8"
            )


def _read_json(path: Path, *, where: str) -> Any:
    try:
        return json.loads(path.read_text("utf-8"))
    except json.JSONDecodeError as error:
        raise RecordError(f"{where}: not JSON ({error})") from None


def _dumps(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def _layers(record: Record, delta: Delta, host: str) -> tuple[tuple[str, Layout], ...]:
    """The four layers for *host* at *delta*, least specific first, each named."""
    return (
        ("the tool's layout", record.layout),
        (f"the tool's {host} layout", record.host_layouts.get(host, Layout())),
        (f"version {delta.version}'s layout", delta.layout),
        (
            f"version {delta.version}'s {host} layout",
            delta.host_layouts.get(host, Layout()),
        ),
    )


def _resolve_fields(
    record: Record, delta: Delta, host: str
) -> tuple[dict[str, Any], list[str]]:
    """Every layout field resolved for *host* at *delta*, and the refusals met.

    Returns the values and the restating overrides found: an override
    whose value is what the layers below already resolve to.
    """
    values = dict(_BUILTIN)
    restated: list[str] = []
    for name, layer in _layers(record, delta, host):
        for key in layer.set_fields():
            value = getattr(layer, key)
            plain = dict(value) if isinstance(value, Mapping) else value
            if plain == values[key]:
                restated.append(f"{name} restates {key} as it inherits it")
            values[key] = plain
    return values, restated


def resolve(record: Record, version: str, host: str) -> Deployment:
    """The complete deployment of *version* on *host*, through the four layers.

    Raises:
        RecordError: when the record does not track *version*, or
            *version* has no *host*, naming what it does have.
    """
    delta = record.delta_for(version)
    if host not in delta.artifacts:
        raise RecordError(
            f"{record.name} {version}: no host {host}; the version has"
            f" {', '.join(delta.hosts) or 'none'}"
        )
    values, _ = _resolve_fields(record, delta, host)
    artifact = delta.artifacts[host]
    return Deployment(
        artifact.url,
        artifact.sha256,
        str(values["root"]),
        str(values["exe"]),
        tuple(values["entry_points"]),
        tuple(values["paths"]),
        dict(values["env"]),
        dict(values["shims"]),
        tuple(values["exclude"]),
    )


def validate(record: Record) -> None:
    """Refuse a record that breaks a rule; every host of every version resolves.

    Raises:
        RecordError: naming the first rule broken.
    """
    where = record.name
    if not record.name:
        raise RecordError("a record needs a name")
    if record.kind not in KINDS:
        raise RecordError(
            f"{where}: kind {record.kind!r} is not one of {', '.join(KINDS)}"
        )
    for host in record.hosts:
        if host not in HOSTS:
            raise RecordError(
                f"{where}: host {host!r} is not one of {', '.join(HOSTS)}"
            )
    if len(set(record.hosts)) != len(record.hosts):
        raise RecordError(f"{where}: a host is listed twice")
    for host in record.host_layouts:
        if host not in record.hosts:
            raise RecordError(
                f"{where}: the tool's {host} layout names a host the record"
                f" lacks; it has {', '.join(record.hosts) or 'none'}"
            )
    seen: set[str] = set()
    for index, delta in enumerate(record.deltas, start=1):
        at = f"{where} {DELTAS_DIR}/{delta.file_name}"
        if delta.sequence != index:
            raise RecordError(
                f"{at}: out of sequence; expected {index:04d}, the deltas run"
                " consecutively from 0001"
            )
        if delta.version in seen:
            raise RecordError(f"{at}: version {delta.version} was added before")
        seen.add(delta.version)
        for host in delta.artifacts:
            if host not in record.hosts:
                raise RecordError(
                    f"{at}: an artifact for {host}, a host the record lacks; it"
                    f" has {', '.join(record.hosts) or 'none'}"
                )
        for host in delta.host_layouts:
            if host not in delta.artifacts:
                raise RecordError(
                    f"{at}: a {host} layout for a host the version lacks; it has"
                    f" {', '.join(delta.hosts) or 'none'}"
                )
        if record.kind in DOWNLOAD_KINDS and not delta.artifacts:
            raise RecordError(f"{at}: a {record.kind} version needs an artifact")
        if delta.layout.set_fields() and not delta.artifacts:
            raise RecordError(f"{at}: a layout for a version with no host")
        for host in delta.hosts:
            values, restated = _resolve_fields(record, delta, host)
            if restated:
                raise RecordError(f"{at} {host}: {restated[0]}")
            missing = _incomplete(record, values)
            if missing:
                raise RecordError(
                    f"{at} {host}: resolves incomplete, {missing}; every host of"
                    " every version must resolve to a whole deployment"
                )
    # A tool-level layout that no version and host ever reads is not a
    # restatement, so a tool with no version is validated on its own.
    if not record.deltas:
        _, restated = _resolve_fields(
            record, Delta(1, "-"), record.hosts[0] if record.hosts else "-"
        )
        if restated:
            raise RecordError(f"{where}: {restated[0]}")


def _incomplete(record: Record, values: Mapping[str, Any]) -> str:
    """What a resolved layout still lacks, or empty when it is whole."""
    if not values["paths"]:
        return "paths is empty"
    if record.kind == "binary" and not values["exe"]:
        return "a binary names no exe"
    if record.kind in DOWNLOAD_KINDS and not values["entry_points"]:
        return "no entry point is declared"
    if record.kind == "binary" and values["exe"] not in values["entry_points"]:
        return f"the binary's exe {values['exe']!r} is not among its entry points"
    return ""


def host_key(system: str, machine: str) -> str:
    """The host key for a `platform.system()` and `platform.machine()` pair.

    Raises:
        RecordError: for a platform or architecture no record can name.
    """
    systems = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}
    machines = {
        "amd64": "x64",
        "x86_64": "x64",
        "x64": "x64",
        "arm64": "arm",
        "aarch64": "arm",
    }
    platform = systems.get(system)
    arch = machines.get(machine.lower())
    if platform is None or arch is None:
        raise RecordError(f"no record names the host {system}/{machine}")
    return f"{platform}-{arch}"


def schema() -> dict[str, Any]:
    """The JSON schema of a record's two documents, for editor completion.

    One schema, two shapes under `$defs`: `Tool` for `tool.json` and
    `Delta` for a delta file; a document is one or the other. Every
    object is closed to unknown keys.
    """
    layout = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "root": {"type": "string"},
            "exe": {"type": "string"},
            "entry_points": {"type": "array", "items": {"type": "string"}},
            "paths": {"type": "array", "items": {"type": "string"}},
            "env": {"type": "object", "additionalProperties": {"type": "string"}},
            "shims": {"type": "object", "additionalProperties": {"type": "string"}},
            "exclude": {"type": "array", "items": {"type": "string"}},
        },
    }
    artifact = {
        "type": "object",
        "additionalProperties": False,
        "required": ["url", "sha256"],
        "properties": {
            "url": {"type": "string", "minLength": 1},
            "sha256": {"type": "string", "pattern": _SHA256.pattern},
        },
    }
    host_layouts = {
        "type": "object",
        "propertyNames": {"enum": list(HOSTS)},
        "additionalProperties": {"$ref": "#/$defs/Layout"},
    }
    tool = {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "hosts"],
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "description": {"type": "string", "default": ""},
            "kind": {"type": "string", "enum": list(KINDS), "default": "archive"},
            "min_version": {"type": "string", "default": ""},
            "hosts": {
                "type": "array",
                "items": {"type": "string", "enum": list(HOSTS)},
                "uniqueItems": True,
            },
            "layout": {"$ref": "#/$defs/Layout"},
            "host_layouts": host_layouts,
        },
    }
    delta = {
        "type": "object",
        "additionalProperties": False,
        "required": ["sequence", "version", "artifacts"],
        "properties": {
            "sequence": {"type": "integer", "minimum": 1},
            "version": {"type": "string", "minLength": 1},
            "date": {"type": "string", "default": ""},
            "artifacts": {
                "type": "object",
                "propertyNames": {"enum": list(HOSTS)},
                "additionalProperties": {"$ref": "#/$defs/Artifact"},
            },
            "layout": {"$ref": "#/$defs/Layout"},
            "host_layouts": host_layouts,
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Record",
        "oneOf": [{"$ref": "#/$defs/Tool"}, {"$ref": "#/$defs/Delta"}],
        "$defs": {"Layout": layout, "Artifact": artifact, "Tool": tool, "Delta": delta},
    }


def export_schema(path: Path) -> None:
    """Write `schema()` to *path* as indented JSON with a trailing newline."""
    path.write_text(json.dumps(schema(), indent=2) + "\n", encoding="utf-8")
