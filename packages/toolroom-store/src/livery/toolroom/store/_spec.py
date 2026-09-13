"""The tool spec: a tool, its pinned version, and each host's artifact.

A spec is inert data. It names a kind, the pinned version, a version
floor for a system tool, and per version one definition per host with
the artifact's URL and its mandatory sha256, the archive's root, the
executable's name, the directories to put on PATH, the environment and
the shims. It never runs a command. The JSON on disk is hse's
`specs/<name>.json`: the same keys, in the same order, with the same
meaning, so a file moves between the two without an edit; only the
whitespace is this writer's.

Reach for [livery.toolroom.store.Spec.load][] and
[livery.toolroom.store.Spec.definition_for][]; the rest is what a load
validates.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PLATFORMS = ("windows", "macos", "linux")
"""The operating systems a spec may name."""

ARCHES = ("x64", "arm")
"""The CPU architectures a spec may name."""

HOSTS = (
    "macos-arm",
    "macos-x64",
    "linux-x64",
    "linux-arm",
    "windows-x64",
    "windows-arm",
)
"""The six host keys, `<platform>-<arch>`. A spec carries any subset."""

KINDS = ("archive", "binary", "uv-tool", "uv-python", "bun-install", "system-check")
"""The installer kinds: `archive` and `binary` download by URL and land
in the store; `uv-tool` and `uv-python` delegate to uv; `bun-install`
to bun; `system-check` verifies a system tool against `min_version`."""

DOWNLOAD_KINDS = frozenset({"archive", "binary"})
"""The kinds whose definitions need a URL."""

PACKAGE_VAR = "$package"
"""The one substitution a definition's env values may carry: the install root."""

_SHA256 = re.compile(r"^[a-f0-9]{64}$")

_DEFINITION_KEYS = (
    "platform",
    "arch",
    "url",
    "sha256",
    "root",
    "exe",
    "paths",
    "env",
    "shims",
)
_VERSION_KEYS = ("version", "definitions")
_SPEC_KEYS = ("name", "description", "kind", "pinned", "min_version", "versions")


class SpecError(ValueError):
    """A spec that breaks a rule; the message names the field."""


def _object(value: Any, allowed: tuple[str, ...], *, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SpecError(f"{where}: not a JSON object")
    extra = sorted(set(value) - set(allowed))
    if extra:
        raise SpecError(f"{where}: unknown keys {', '.join(extra)}")
    return value


def _text(value: Any, *, where: str) -> str:
    if not isinstance(value, str):
        raise SpecError(f"{where}: not a string")
    return value


def _texts(value: Any, *, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise SpecError(f"{where}: not a list of strings")
    return tuple(value)


def _mapping(value: Any, *, where: str) -> dict[str, str]:
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise SpecError(f"{where}: not an object of strings")
    return dict(value)


@dataclass(frozen=True)
class Definition:
    """One host's artifact and layout for a version.

    Attributes:
        platform: One of `PLATFORMS`.
        arch: One of `ARCHES`.
        url: Where the artifact downloads from; required for a
            download kind, and never without `sha256`.
        sha256: The artifact's lowercase hex sha256, the object's name
            in the store.
        root: The directory inside the archive to hoist to the install
            root; empty keeps the archive's own top.
        exe: The executable's name for a `binary` download.
        paths: Install-relative directories to put on PATH.
        env: Environment variables to set; a value may carry
            `PACKAGE_VAR`, the install root.
        shims: Link name to an executable the install carries, such as
            `node` to `bun`.
    """

    platform: str
    arch: str
    url: str = ""
    sha256: str | None = None
    root: str = ""
    exe: str = ""
    paths: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    shims: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.platform not in PLATFORMS:
            raise SpecError(
                f"definition platform {self.platform!r} is not one of"
                f" {', '.join(PLATFORMS)}"
            )
        if self.arch not in ARCHES:
            raise SpecError(
                f"definition arch {self.arch!r} is not one of {', '.join(ARCHES)}"
            )
        if self.sha256 is not None and not _SHA256.match(self.sha256):
            raise SpecError(
                f"definition {self.key}: sha256 {self.sha256!r} is not 64 lowercase"
                " hex digits"
            )
        if self.url and self.sha256 is None:
            raise SpecError(f"definition {self.key}: url without sha256")

    @property
    def key(self) -> str:
        """`<platform>-<arch>`, the host key this definition serves."""
        return f"{self.platform}-{self.arch}"

    def to_json(self) -> dict[str, Any]:
        """The definition as a JSON object, keys in the spec's order."""
        return {
            "platform": self.platform,
            "arch": self.arch,
            "url": self.url,
            "sha256": self.sha256,
            "root": self.root,
            "exe": self.exe,
            "paths": list(self.paths),
            "env": dict(self.env),
            "shims": dict(self.shims),
        }

    @classmethod
    def from_json(cls, value: Any, *, where: str) -> Definition:
        """A definition from its JSON object.

        Raises:
            SpecError: when the object is not a definition, naming
                *where*.
        """
        data = _object(value, _DEFINITION_KEYS, where=where)
        for required in ("platform", "arch"):
            if required not in data:
                raise SpecError(f"{where}: no {required}")
        sha = data.get("sha256")
        if sha is not None and not isinstance(sha, str):
            raise SpecError(f"{where}: sha256 is not a string")
        return cls(
            _text(data["platform"], where=f"{where} platform"),
            _text(data["arch"], where=f"{where} arch"),
            _text(data.get("url", ""), where=f"{where} url"),
            sha,
            _text(data.get("root", ""), where=f"{where} root"),
            _text(data.get("exe", ""), where=f"{where} exe"),
            _texts(data.get("paths", []), where=f"{where} paths"),
            _mapping(data.get("env", {}), where=f"{where} env"),
            _mapping(data.get("shims", {}), where=f"{where} shims"),
        )


@dataclass(frozen=True)
class Version:
    """One version of a tool: a definition per host it is built for.

    Attributes:
        version: The version string, also the key under `Spec.versions`.
        definitions: Per host key; may be empty for a delegated kind.
    """

    version: str
    definitions: dict[str, Definition] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for key, definition in self.definitions.items():
            if key != definition.key:
                raise SpecError(
                    f"version {self.version}: definition key {key!r} is not its"
                    f" host {definition.key!r}"
                )

    def to_json(self) -> dict[str, Any]:
        """The version as a JSON object."""
        return {
            "version": self.version,
            "definitions": {k: d.to_json() for k, d in self.definitions.items()},
        }

    @classmethod
    def from_json(cls, value: Any, *, where: str) -> Version:
        """A version from its JSON object.

        Raises:
            SpecError: when the object is not a version.
        """
        data = _object(value, _VERSION_KEYS, where=where)
        if "version" not in data:
            raise SpecError(f"{where}: no version")
        raw = data.get("definitions", {})
        if not isinstance(raw, dict):
            raise SpecError(f"{where}: definitions is not an object")
        return cls(
            _text(data["version"], where=f"{where} version"),
            {
                str(k): Definition.from_json(v, where=f"{where} definitions[{k}]")
                for k, v in raw.items()
            },
        )


@dataclass(frozen=True)
class Spec:
    """A tool the store can supply, across versions and hosts.

    Attributes:
        name: The tool's name, the spec file's stem and the store's
            `tools/<name>@<version>` stem.
        description: One line on what the tool is.
        kind: One of `KINDS`.
        pinned: The version a checkout pins; must be in `versions`.
        min_version: The floor a `system-check` tool must reach.
        versions: Known versions by their version string.
    """

    name: str
    description: str = ""
    kind: str = "archive"
    pinned: str = ""
    min_version: str = ""
    versions: dict[str, Version] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise SpecError("a spec needs a name")
        if self.kind not in KINDS:
            raise SpecError(
                f"{self.name}: kind {self.kind!r} is not one of {', '.join(KINDS)}"
            )
        for key, version in self.versions.items():
            if key != version.version:
                raise SpecError(
                    f"{self.name}: version key {key!r} is not its version"
                    f" {version.version!r}"
                )
        if self.pinned and self.pinned not in self.versions:
            raise SpecError(
                f"{self.name}: pinned version {self.pinned!r} is not among"
                f" {', '.join(self.versions) or 'no versions'}"
            )
        if self.kind in DOWNLOAD_KINDS:
            for version in self.versions.values():
                for definition in version.definitions.values():
                    if not definition.url:
                        raise SpecError(
                            f"{self.name} {version.version} {definition.key}:"
                            f" {self.kind} definitions need a url"
                        )

    def pinned_version(self) -> Version:
        """The version the spec pins.

        Raises:
            SpecError: when the spec pins nothing.
        """
        if not self.pinned:
            raise SpecError(f"{self.name}: no pinned version")
        return self.versions[self.pinned]

    def definition_for(self, host: str, version: str = "") -> Definition:
        """The definition for *host* at *version*, the pinned one by default.

        Raises:
            SpecError: when the version is unknown, or the spec carries
                no definition for the host; the message names the hosts
                it does carry.
        """
        wanted = version or self.pinned
        if wanted not in self.versions:
            raise SpecError(
                f"{self.name}: no version {wanted!r}; the spec knows"
                f" {', '.join(self.versions) or 'none'}"
            )
        found = self.versions[wanted]
        if host not in found.definitions:
            raise SpecError(
                f"{self.name} {wanted}: no definition for {host}; the spec carries"
                f" {', '.join(found.definitions) or 'none'}"
            )
        return found.definitions[host]

    def to_json(self) -> dict[str, Any]:
        """The spec as a JSON object, keys in hse's order."""
        return {
            "name": self.name,
            "description": self.description,
            "kind": self.kind,
            "pinned": self.pinned,
            "min_version": self.min_version,
            "versions": {k: v.to_json() for k, v in self.versions.items()},
        }

    @classmethod
    def from_json(cls, value: Any, *, where: str = "spec") -> Spec:
        """A spec from its JSON object.

        Raises:
            SpecError: when the object is not a spec.
        """
        data = _object(value, _SPEC_KEYS, where=where)
        if "name" not in data:
            raise SpecError(f"{where}: no name")
        raw = data.get("versions", {})
        if not isinstance(raw, dict):
            raise SpecError(f"{where}: versions is not an object")
        return cls(
            _text(data["name"], where=f"{where} name"),
            _text(data.get("description", ""), where=f"{where} description"),
            _text(data.get("kind", "archive"), where=f"{where} kind"),
            _text(data.get("pinned", ""), where=f"{where} pinned"),
            _text(data.get("min_version", ""), where=f"{where} min_version"),
            {
                str(k): Version.from_json(v, where=f"{where} versions[{k}]")
                for k, v in raw.items()
            },
        )

    @classmethod
    def loads(cls, text: str, *, where: str = "spec") -> Spec:
        """A spec from its JSON text.

        Raises:
            SpecError: when the text is not JSON, or not a spec.
        """
        try:
            value = json.loads(text)
        except json.JSONDecodeError as error:
            raise SpecError(f"{where}: not JSON ({error})") from None
        return cls.from_json(value, where=where)

    @classmethod
    def load(cls, path: Path) -> Spec:
        """The spec at *path*.

        Raises:
            SpecError: naming the file.
        """
        return cls.loads(path.read_text("utf-8"), where=path.name)

    def dumps(self) -> str:
        """The spec as indented JSON, a trailing newline, hse's keys in hse's order."""
        return json.dumps(self.to_json(), indent=2, ensure_ascii=False) + "\n"

    def save(self, path: Path) -> None:
        """Write the spec to *path*."""
        path.write_text(self.dumps(), encoding="utf-8")


def host_key(system: str, machine: str) -> str:
    """The host key for a `platform.system()` and `platform.machine()` pair.

    Raises:
        SpecError: for a platform or architecture no spec can name.
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
        raise SpecError(f"no spec names the host {system}/{machine}")
    return f"{platform}-{arch}"


def schema() -> dict[str, Any]:
    """The JSON schema of a spec file, for editor completion.

    The same shape hse exports: the definitions and versions as
    objects keyed by host and version, every object closed to unknown
    keys.
    """
    definition = {
        "type": "object",
        "additionalProperties": False,
        "required": ["platform", "arch"],
        "properties": {
            "platform": {"type": "string", "enum": list(PLATFORMS)},
            "arch": {"type": "string", "enum": list(ARCHES)},
            "url": {"type": "string", "default": ""},
            "sha256": {
                "anyOf": [
                    {"type": "string", "pattern": _SHA256.pattern},
                    {"type": "null"},
                ],
                "default": None,
            },
            "root": {"type": "string", "default": ""},
            "exe": {"type": "string", "default": ""},
            "paths": {"type": "array", "items": {"type": "string"}, "default": []},
            "env": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "default": {},
            },
            "shims": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "default": {},
            },
        },
    }
    version = {
        "type": "object",
        "additionalProperties": False,
        "required": ["version"],
        "properties": {
            "version": {"type": "string"},
            "definitions": {
                "type": "object",
                "additionalProperties": {"$ref": "#/$defs/Definition"},
                "default": {},
            },
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Spec",
        "type": "object",
        "additionalProperties": False,
        "required": ["name"],
        "properties": {
            "name": {"type": "string"},
            "description": {"type": "string", "default": ""},
            "kind": {"type": "string", "enum": list(KINDS), "default": "archive"},
            "pinned": {"type": "string", "default": ""},
            "min_version": {"type": "string", "default": ""},
            "versions": {
                "type": "object",
                "additionalProperties": {"$ref": "#/$defs/Version"},
                "default": {},
            },
        },
        "$defs": {"Definition": definition, "Version": version},
    }


def export_schema(path: Path) -> None:
    """Write `schema()` to *path* as indented JSON with a trailing newline."""
    path.write_text(json.dumps(schema(), indent=2) + "\n", encoding="utf-8")
