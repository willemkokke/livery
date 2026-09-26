"""The tool record: one tool, every version tracked, each host's deployment and surface.

A record is inert data, authored in git as one file of JSON lines per
tool, ``records/<tool>.jsonl``: the first line is the tool axis, what
does not move with a version; a version line is one version's
arrival, forward and append-only; and the statement lines under a
version line are what its reading changed, one option, one verb's own
fields, one withdrawal or one absence per line. A deployment resolves
through four layers, most specific winning: the tool's layout, the
host's override of it, the version's override, and the version's
override for one host. A version's surface, what its command line
accepts, is carried one option at a time: a reading names the options
and the verb fields the version changed and the tool's description
when it changed, and everything else is inherited from the nearest
earlier version that has a surface. Resolution is total and checked:
loading a record resolves every host of every version and every
version's surface, and refuses a host that resolves incomplete, an
override that names a host or version the record does not carry, an
override, a verb field or an option that restates the value it
inherits, a withdrawal of what no earlier version has, a statement
under a version that was not read, and a reading below the record's
``prime``, the oldest version its history reaches. A record names a
kind, per host an artifact with a mandatory digest, and the layout; it
never runs a command. A version with no artifact is tracked for its
surface alone: it has no host and nothing installs it.

Reach for [livery.toolroom.store.Record.load][] to read a record,
[livery.toolroom.store.resolve][] for one host's deployment at one
version, and [livery.toolroom.store.surface_at][] for one version's
whole surface; the rest is what a load validates.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from livery.strongroom import Digest, canonical, digest_of
from livery.toolroom.tools import version_tuple

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

KINDS = ("download", "uv-tool", "uv-python", "bun-install", "system-check")
"""The installer kinds. A `download` is fetched by URL and lands in the
store: an archive is unpacked and its root hoisted, a bare file is
saved under its `file` name, and the layout says what reaches the
outside, entry points and `paths` for a program, `env` alone for a
file that is never run (a CMake module, a plugin bundle). `uv-tool`
and `uv-python` delegate to uv; `bun-install` to bun; `system-check`
verifies a system tool against `min_version`, and may carry an
artifact for a host that has no system tool."""

DOWNLOAD_KINDS = frozenset({"download"})
"""The kinds whose every host needs an artifact."""

FORMATS = ("zip", "tar", "file")
"""What a downloaded artifact is: sniffed from its URL's suffix unless
the layout's `format` says, for an artifact whose name lies."""

MODES = ("link", "path", "none")
"""How a materialised tool reaches PATH: its entry points linked into the
checkout's bin directory, its own directories on PATH, or not at all,
for a tool reached only through a typed handle."""


def default_mode(kind: str, paths: tuple[str, ...] = ()) -> str:
    """The materialisation mode a kind takes unless the record or the project says.

    A download with *paths* puts them on PATH, since a tool finds its
    own data by its real path; one with none is reached through its
    env alone and takes `none`; a system tool is on PATH already; an
    installer's tool puts its directories on PATH.
    """
    if kind == "download":
        return "path" if paths else "none"
    if kind == "system-check":
        return "none"
    return "path"


PACKAGE_VAR = "$package"
"""The one substitution a deployment's env values may carry: the install root."""

VERSION_VAR = "{version}"
"""The one substitution a layout's `root` may carry: the version being resolved.

An archive whose top directory is named after its version
(`git-cliff-2.14.2/`) declares `root = "git-cliff{version}"` once on
the tool, instead of a layout override restating the version on every
version line. [livery.toolroom.store.resolve][] substitutes it, so a
deployment always carries the concrete directory.
""".replace("git-cliff{version}", "git-cliff-{version}")

RECORD_SUFFIX = ".jsonl"
"""A record file's suffix: `records/<tool>.jsonl`, named by the tool."""

_VERB_FIELDS = ("help", "wraps", "positional", "lead")
"""A verb's own fields, the ones a statement may set one at a time."""

_SHA256 = re.compile(r"^[a-f0-9]{64}$")

LAYOUT_KEYS = (
    "root",
    "file",
    "format",
    "entry_points",
    "paths",
    "env",
    "shims",
    "exclude",
)
"""The layout fields an override may set, in the record's key order."""

SURFACE_PLATFORMS = ("Linux", "macOS", "Windows")
"""The platforms a reading of a tool's command line may name."""

VERB_KEYS = ("help", "wraps", "positional", "lead", "options")
"""A verb's fields in a surface, in the record's key order; every one is present."""

OPTION_KEYS = ("flags", "negation", "help", "type", "default", "choices")
"""An option's fields in a surface, in the record's key order; every one is present."""

_TOOL_KEYS = (
    "name",
    "description",
    "kind",
    "package",
    "mode",
    "min_version",
    "prime",
    "hosts",
    "layout",
    "host_layouts",
)
_VERSION_KEYS = ("version", "date", "artifacts", "layout", "host_layouts", "read")
_READ_KEYS = ("platforms", "extractor", "help")
_STATEMENT_KEYS = ("verb", "option", "gone", "absent", *_VERB_FIELDS, *OPTION_KEYS)
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
            root; empty keeps the archive's own top. May carry
            `VERSION_VAR`, replaced by the version resolved, for an
            archive whose top directory is named after its version.
        file: The name a bare download (one file, no archive) is saved
            under, since the artifact carries none of its own.
        format: What the artifact is, one of `FORMATS`, for an artifact
            whose URL suffix would sniff wrong; empty sniffs.
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
    file: str | None = None
    format: str | None = None
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
            _text(data["file"], where=f"{where} file") if "file" in data else None,
            _text(data["format"], where=f"{where} format")
            if "format" in data
            else None,
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
        file: See [livery.toolroom.store.Layout][].
        format: See [livery.toolroom.store.Layout][]; empty when the URL
            suffix decides.
        entry_points: See [livery.toolroom.store.Layout][]; empty for a
            download reached through its env alone.
        paths: See [livery.toolroom.store.Layout][]; empty likewise.
        env: See [livery.toolroom.store.Layout][].
        shims: See [livery.toolroom.store.Layout][].
        exclude: See [livery.toolroom.store.Layout][].
    """

    url: str
    sha256: str
    root: str
    file: str
    format: str
    entry_points: tuple[str, ...]
    paths: tuple[str, ...]
    env: dict[str, str]
    shims: dict[str, str]
    exclude: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        """The deployment as a JSON object, every field, lists for the tuples.

        The canonical encoding of this object is what the index lands
        for the host and what a receipt's deployment digest names.
        """
        return {
            "url": self.url,
            "sha256": self.sha256,
            "root": self.root,
            "file": self.file,
            "format": self.format,
            "entry_points": list(self.entry_points),
            "paths": list(self.paths),
            "env": dict(self.env),
            "shims": dict(self.shims),
            "exclude": list(self.exclude),
        }

    @classmethod
    def from_json(cls, value: Any, *, where: str) -> Deployment:
        """A deployment from its JSON object, as the index carries it.

        Raises:
            RecordError: when the object is not a deployment, naming *where*.
        """
        data = _object(value, _DEPLOYMENT_KEYS, where=where)
        for required in _DEPLOYMENT_KEYS:
            if required not in data:
                raise RecordError(f"{where}: no {required}")
        return cls(
            _text(data["url"], where=f"{where} url"),
            _text(data["sha256"], where=f"{where} sha256"),
            _text(data["root"], where=f"{where} root"),
            _text(data["file"], where=f"{where} file"),
            _text(data["format"], where=f"{where} format"),
            _texts(data["entry_points"], where=f"{where} entry_points"),
            _texts(data["paths"], where=f"{where} paths"),
            _mapping(data["env"], where=f"{where} env"),
            _mapping(data["shims"], where=f"{where} shims"),
            _texts(data["exclude"], where=f"{where} exclude"),
        )

    def digest(self) -> Digest:
        """The deployment's name: the digest of its canonical JSON."""
        return digest_of(canonical(self.to_json()))


_DEPLOYMENT_KEYS = (
    "url",
    "sha256",
    "root",
    "file",
    "format",
    "entry_points",
    "paths",
    "env",
    "shims",
    "exclude",
)


#: What a field resolves to when no layer sets it.
_BUILTIN: dict[str, Any] = {
    "root": "",
    "file": "",
    "format": "",
    "entry_points": (),
    "paths": (),
    "env": {},
    "shims": {},
    "exclude": (),
}


@dataclass(frozen=True)
class Surface:
    """One version's reading of the tool's command line, as its record carries it.

    Sparse at the option: the tool's description, each verb's own
    fields and each option are inherited from the nearest earlier
    version with a surface unless this version sets them. A verb's
    first patch carries every field, since there is nothing to
    inherit; a field or an option set to the value it inherits is
    refused at load as dead data. The facts about the observation
    ride beside it and are never inherited.

    Attributes:
        platforms: The platforms that read this version, from
            `SURFACE_PLATFORMS`; at least one.
        extractor: The generation of the reader that took the reading,
            a positive integer.
        help: The tool's own description, or `None` to inherit it; the
            first surface of a record sets it.
        verbs: Verb name to its patch: an object with any of the verb's
            own fields (`help`, `wraps`, `positional`, `lead`) and
            `options`, option name to the option whole, with
            `OPTION_KEYS`, or `None` for an option this version
            withdraws; or `None` for a verb withdrawn whole. A verb not
            named is inherited. The tool's own options hang off the
            verb named `""`.
        absent: Verb name to option name to the platforms that read the
            version and did not find that option; the option name `""`
            stands for the verb itself. Every platform named is among
            `platforms`, every verb among the resolved verbs and every
            option among that verb's options.
    """

    platforms: tuple[str, ...]
    extractor: int
    help: str | None = None
    verbs: Mapping[str, Mapping[str, Any] | None] = field(default_factory=dict)
    absent: Mapping[str, Mapping[str, tuple[str, ...]]] = field(default_factory=dict)


def _patch_fault(patch: Mapping[str, Any]) -> str:
    """What is wrong with a verb patch's shape, or empty when it is one."""
    extra = sorted(set(patch) - set(VERB_KEYS))
    if extra:
        return f"a patch carries only {', '.join(VERB_KEYS)}, not {', '.join(extra)}"
    for key in ("help", "positional", "lead"):
        if key in patch and not isinstance(patch[key], str):
            return f"{key} is not a string"
    if "wraps" in patch and not isinstance(patch["wraps"], bool):
        return "wraps is not a boolean"
    options = patch.get("options", {})
    if not isinstance(options, Mapping):
        return "options is not an object"
    for name, option in options.items():
        if option is None:
            continue
        fault = _option_fault(option)
        if fault:
            joint = " " if fault.startswith("carries") else ": "
            return f"option {name!r}{joint}{fault}"
    return ""


def _option_fault(option: Any) -> str:
    """What is wrong with an option's shape; empty when it has `OPTION_KEYS` whole."""
    if not isinstance(option, Mapping) or set(option) != set(OPTION_KEYS):
        return f"carries exactly {', '.join(OPTION_KEYS)}"
    for key in ("negation", "help", "type"):
        if not isinstance(option[key], str):
            return f"{key} is not a string"
    for key in ("flags", "choices"):
        if not isinstance(option[key], list | tuple) or not all(
            isinstance(item, str) for item in option[key]
        ):
            return f"{key} is not a list of strings"
    return ""


def _verb_fault(verb: Mapping[str, Any]) -> str:
    """What is wrong with a resolved verb, or empty when it has `VERB_KEYS` whole."""
    missing = [key for key in VERB_KEYS if key not in verb]
    if missing:
        return f"first appears without {', '.join(missing)}"
    return _patch_fault(verb)


def _applied(
    current: Mapping[str, Any] | None, patch: Mapping[str, Any]
) -> dict[str, Any]:
    """*patch* laid over *current*: the verb after this version, options folded."""
    folded: dict[str, Any] = dict(current) if current is not None else {}
    options: dict[str, Any] = dict(folded.get("options", {}))
    for key in _VERB_FIELDS:
        if key in patch:
            folded[key] = patch[key]
    for name, option in patch.get("options", {}).items():
        if option is None:
            options.pop(name, None)
        else:
            options[name] = option
    folded["options"] = options
    return folded


def _canonical_verb(verb: Mapping[str, Any]) -> dict[str, Any]:
    """A verb as plain data in the record's key order, options in name order."""
    return {
        "help": verb["help"],
        "wraps": verb["wraps"],
        "positional": verb["positional"],
        "lead": verb["lead"],
        "options": {
            name: {
                "flags": list(option["flags"]),
                "negation": option["negation"],
                "help": option["help"],
                "type": option["type"],
                "default": option["default"],
                "choices": list(option["choices"]),
            }
            for name, option in sorted(verb["options"].items())
        },
    }


@dataclass(frozen=True)
class Observation:
    """What one version accepted, whole, and who looked: its surface resolved.

    Attributes:
        version: The version observed.
        date: The version's date, as its delta carries it.
        platforms: The platforms that read the version.
        extractor: The generation of the reader that took the reading.
        help: The tool's own description at this version.
        verbs: Every verb whole, in name order, each in `VERB_KEYS` shape
            with its options in name order; treat it as read-only.
        absent: See [livery.toolroom.store.Surface][].
    """

    version: str
    date: str
    platforms: tuple[str, ...]
    extractor: int
    help: str
    verbs: dict[str, dict[str, Any]]
    absent: dict[str, dict[str, tuple[str, ...]]]


@dataclass(frozen=True)
class Delta:
    """One version's arrival: its artifacts, what it overrides, and its surface.

    Attributes:
        sequence: The delta's place in the record, from 1, consecutive:
            the version line's place in the file.
        version: The version string this delta adds.
        date: When the version arrived, `YYYY-MM-DD`; empty when the
            record does not know.
        artifacts: Per host key, the artifact; the keys are the hosts
            this version has.
        layout: The version's override of the tool's layout.
        host_layouts: The version's override for one host each.
        surface: The version's reading of the command line, or `None`
            when no reading was taken; a delta carries an artifact, a
            surface, or both.
    """

    sequence: int
    version: str
    date: str = ""
    artifacts: dict[str, Artifact] = field(default_factory=dict)
    layout: Layout = field(default_factory=Layout)
    host_layouts: dict[str, Layout] = field(default_factory=dict)
    surface: Surface | None = None

    @property
    def hosts(self) -> tuple[str, ...]:
        """The hosts this version has, the artifacts' keys."""
        return tuple(self.artifacts)


@dataclass(frozen=True)
class Record:
    """A tool across every version tracked and every host it has.

    Loading a record validates it whole; see the module docstring for
    what a load refuses.

    Attributes:
        name: The tool's name, the record file's stem and the store's
            `tools/<name>@<version>` stem.
        description: One line on what the tool is.
        kind: One of `KINDS`.
        package: What a delegated kind's installer installs, when it
            differs from the tool's name: the PyPI or npm package.
        mode: How the tool reaches PATH once materialised, one of
            `MODES`; empty takes the kind's default.
        min_version: The floor a `system-check` tool must reach.
        hosts: The host keys the tool has, from `HOSTS`.
        layout: The tool's layout, the layer every host and version
            inherits.
        host_layouts: The tool's override for one host each.
        deltas: The versions, in sequence.
        prime: The oldest version the reading history reaches: no
            version below it is read, so an option present at `prime`
            claims no `since`. Empty when the history reaches as far
            back as the releases go.
    """

    name: str
    description: str = ""
    kind: str = "download"
    package: str = ""
    mode: str = ""
    min_version: str = ""
    hosts: tuple[str, ...] = ()
    layout: Layout = field(default_factory=Layout)
    host_layouts: dict[str, Layout] = field(default_factory=dict)
    deltas: tuple[Delta, ...] = ()
    prime: str = ""

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
        """The tool axis as a JSON object in the record's key order: the first line."""
        out: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "kind": self.kind,
        }
        if self.package:
            out["package"] = self.package
        if self.mode:
            out["mode"] = self.mode
        out["min_version"] = self.min_version
        if self.prime:
            out["prime"] = self.prime
        out["hosts"] = list(self.hosts)
        if self.layout.set_fields():
            out["layout"] = self.layout.to_json()
        if self.host_layouts:
            out["host_layouts"] = {k: v.to_json() for k, v in self.host_layouts.items()}
        return out

    @classmethod
    def from_json(
        cls, value: Any, deltas: tuple[Delta, ...] = (), *, where: str = "line 1"
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
            _text(data.get("kind", "download"), where=f"{where} kind"),
            _text(data.get("package", ""), where=f"{where} package"),
            _text(data.get("mode", ""), where=f"{where} mode"),
            _text(data.get("min_version", ""), where=f"{where} min_version"),
            _texts(data.get("hosts", []), where=f"{where} hosts"),
            Layout.from_json(data.get("layout", {}), where=f"{where} layout"),
            {
                str(k): Layout.from_json(v, where=f"{where} host_layouts[{k}]")
                for k, v in raw_hosts.items()
            },
            deltas,
            _text(data.get("prime", ""), where=f"{where} prime"),
        )

    def lines(self) -> list[dict[str, Any]]:
        """The record as its file's lines: the axis, each version, its statements."""
        out: list[dict[str, Any]] = [self.to_json()]
        for delta in self.deltas:
            out.append(_version_line(delta))
            if delta.surface is not None:
                out.extend(_statements(delta.surface))
        return out

    @classmethod
    def parse(cls, text: str, *, where: str) -> Record:
        """A record from its file's text, one JSON object per line.

        Raises:
            RecordError: naming *where* and the line, for a line that is
                not JSON, a statement before any version line or under
                a version that was not read, an option stated twice,
                or a record that does not validate.
        """
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            raise RecordError(f"{where}: empty")
        objects: list[Any] = []
        for number, line in enumerate(lines, start=1):
            try:
                objects.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise RecordError(
                    f"{where} line {number}: not JSON ({error})"
                ) from None
        deltas: list[Delta] = []
        pending: _Pending | None = None
        for number, obj in enumerate(objects[1:], start=2):
            at = f"{where} line {number}"
            if not isinstance(obj, dict):
                raise RecordError(f"{at}: not a JSON object")
            if "version" in obj:
                if pending is not None:
                    deltas.append(pending.delta(len(deltas) + 1))
                pending = _Pending.from_json(obj, where=at)
                continue
            if "verb" not in obj:
                raise RecordError(f"{at}: neither a version line nor a statement")
            if pending is None:
                raise RecordError(f"{at}: a statement before any version line")
            pending.statement(obj, where=at)
        if pending is not None:
            deltas.append(pending.delta(len(deltas) + 1))
        return cls.from_json(objects[0], tuple(deltas), where=f"{where} line 1")

    @classmethod
    def load(cls, path: Path) -> Record:
        """The record in the file *path*, `records/<tool>.jsonl`.

        Raises:
            RecordError: naming the file, for a directory (the earlier
                form, converted by `fm tools.convert-records`), a file
                that is not a record, a record named other than its
                file, or one that does not validate.
        """
        if path.is_dir():
            raise RecordError(
                f"{path}: a directory, not a record file; a record is"
                f" `<tool>{RECORD_SUFFIX}`, and the directory form converts with"
                " `fm tools.convert-records`"
            )
        if not path.is_file():
            raise RecordError(f"{path}: no such record")
        record = cls.parse(path.read_text("utf-8"), where=path.name)
        stem = path.name.removesuffix(RECORD_SUFFIX)
        if record.name != stem:
            raise RecordError(
                f"{path.name}: the record is named {record.name!r}, its file {stem!r}"
            )
        return record

    def save(self, directory: Path) -> None:
        """Write the record as `<name>.jsonl` under *directory*, one object per line."""
        directory.mkdir(parents=True, exist_ok=True)
        text = "".join(_line(obj) for obj in self.lines())
        (directory / f"{self.name}{RECORD_SUFFIX}").write_text(text, encoding="utf-8")


def records_in(directory: Path) -> list[Path]:
    """The record files under *directory*, by name; empty when it is not one."""
    if not directory.is_dir():
        return []
    return sorted(
        path
        for path in directory.iterdir()
        if path.suffix == RECORD_SUFFIX and path.is_file()
    )


def _line(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False) + "\n"


def _version_line(delta: Delta) -> dict[str, Any]:
    """A delta as its version line, keys in the record's order."""
    out: dict[str, Any] = {
        "version": delta.version,
        "date": delta.date,
        "artifacts": {k: v.to_json() for k, v in delta.artifacts.items()},
    }
    if delta.layout.set_fields():
        out["layout"] = delta.layout.to_json()
    if delta.host_layouts:
        out["host_layouts"] = {k: v.to_json() for k, v in delta.host_layouts.items()}
    if delta.surface is not None:
        read: dict[str, Any] = {
            "platforms": list(delta.surface.platforms),
            "extractor": delta.surface.extractor,
        }
        if delta.surface.help is not None:
            read["help"] = delta.surface.help
        out["read"] = read
    return out


def _statements(surface: Surface) -> list[dict[str, Any]]:
    """A surface as its statement lines: verbs and options in name order."""
    out: list[dict[str, Any]] = []
    for name, patch in sorted(surface.verbs.items()):
        if patch is None:
            out.append({"verb": name, "gone": True})
            continue
        fields = {key: patch[key] for key in _VERB_FIELDS if key in patch}
        if fields:
            out.append({"verb": name, **fields})
        for option, value in sorted(patch.get("options", {}).items()):
            if value is None:
                out.append({"verb": name, "option": option, "gone": True})
            else:
                out.append({"verb": name, "option": option, **_option_json(value)})
    for verb, options in sorted(surface.absent.items()):
        for option, who in sorted(options.items()):
            line: dict[str, Any] = {"verb": verb}
            if option:
                line["option"] = option
            line["absent"] = list(who)
            out.append(line)
    return out


def _option_json(option: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "flags": list(option["flags"]),
        "negation": option["negation"],
        "help": option["help"],
        "type": option["type"],
        "default": option["default"],
        "choices": list(option["choices"]),
    }


class _Pending:
    """A version line and the statements read under it so far."""

    def __init__(
        self,
        version: str,
        date: str,
        artifacts: dict[str, Artifact],
        layout: Layout,
        host_layouts: dict[str, Layout],
        read: tuple[tuple[str, ...], int, str | None] | None,
    ) -> None:
        self.version = version
        self.date = date
        self.artifacts = artifacts
        self.layout = layout
        self.host_layouts = host_layouts
        self.read = read
        self.verbs: dict[str, dict[str, Any] | None] = {}
        self.absent: dict[str, dict[str, tuple[str, ...]]] = {}

    @classmethod
    def from_json(cls, data: dict[str, Any], *, where: str) -> _Pending:
        data = _object(data, _VERSION_KEYS, where=where)
        raw_artifacts = data.get("artifacts", {})
        if not isinstance(raw_artifacts, dict):
            raise RecordError(f"{where}: artifacts is not an object")
        raw_hosts = data.get("host_layouts", {})
        if not isinstance(raw_hosts, dict):
            raise RecordError(f"{where}: host_layouts is not an object")
        read: tuple[tuple[str, ...], int, str | None] | None = None
        if "read" in data:
            raw = _object(data["read"], _READ_KEYS, where=f"{where} read")
            for required in ("platforms", "extractor"):
                if required not in raw:
                    raise RecordError(f"{where} read: no {required}")
            extractor = raw["extractor"]
            if (
                not isinstance(extractor, int)
                or isinstance(extractor, bool)
                or extractor < 1
            ):
                raise RecordError(f"{where} read: extractor is not a positive integer")
            read = (
                _texts(raw["platforms"], where=f"{where} read platforms"),
                extractor,
                _text(raw["help"], where=f"{where} read help")
                if "help" in raw
                else None,
            )
        return cls(
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
            read,
        )

    def statement(self, data: dict[str, Any], *, where: str) -> None:
        """Fold one statement line in.

        Raises:
            RecordError: for a statement under a version that was not
                read, one off its shape, or an option stated twice.
        """
        if self.read is None:
            raise RecordError(
                f"{where}: a statement under version {self.version}, which was not read"
            )
        data = _object(data, _STATEMENT_KEYS, where=where)
        verb = _text(data["verb"], where=f"{where} verb")
        option = (
            _text(data["option"], where=f"{where} option") if "option" in data else None
        )
        if "absent" in data:
            extra = sorted(set(data) - {"verb", "option", "absent"})
            if extra:
                raise RecordError(f"{where}: an absence carries no {', '.join(extra)}")
            who = _texts(data["absent"], where=f"{where} absent")
            slot = self.absent.setdefault(verb, {})
            if (option or "") in slot:
                raise RecordError(f"{where}: an absence stated twice")
            slot[option or ""] = who
            return
        if data.get("gone") is True:
            extra = sorted(set(data) - {"verb", "option", "gone"})
            if extra:
                raise RecordError(
                    f"{where}: a withdrawal carries no {', '.join(extra)}"
                )
            if option is None:
                if verb in self.verbs:
                    raise RecordError(f"{where}: verb {verb!r} stated twice")
                self.verbs[verb] = None
                return
            self._option(verb, option, None, where=where)
            return
        if "gone" in data:
            raise RecordError(f"{where}: gone is not true")
        if option is None:
            fields = {key: data[key] for key in _VERB_FIELDS if key in data}
            extra = sorted(set(data) - {"verb", *_VERB_FIELDS})
            if extra:
                raise RecordError(f"{where}: a verb line carries no {', '.join(extra)}")
            if not fields:
                raise RecordError(f"{where}: a verb line sets nothing")
            patch = self._patch(verb, where=where)
            for key in fields:
                if key in patch:
                    raise RecordError(f"{where}: {key} of verb {verb!r} stated twice")
            patch.update(fields)
            return
        extra = sorted(set(data) - {"verb", "option", *OPTION_KEYS})
        if extra:
            raise RecordError(f"{where}: an option line carries no {', '.join(extra)}")
        if any(key not in data for key in OPTION_KEYS):
            raise RecordError(
                f"{where}: option {option!r} carries exactly {', '.join(OPTION_KEYS)}"
            )
        value = {key: data[key] for key in OPTION_KEYS}
        fault = _option_fault(value)
        if fault:
            joint = " " if fault.startswith("carries") else ": "
            raise RecordError(f"{where}: option {option!r}{joint}{fault}")
        self._option(verb, option, value, where=where)

    def _patch(self, verb: str, *, where: str) -> dict[str, Any]:
        patch = self.verbs.get(verb)
        if verb in self.verbs and patch is None:
            raise RecordError(f"{where}: verb {verb!r} was withdrawn above")
        if patch is None:
            patch = {"options": {}}
            self.verbs[verb] = patch
        return patch

    def _option(
        self, verb: str, option: str, value: dict[str, Any] | None, *, where: str
    ) -> None:
        patch = self._patch(verb, where=where)
        if option in patch["options"]:
            raise RecordError(
                f"{where}: option {option!r} of verb {verb!r} stated twice"
            )
        patch["options"][option] = value

    def delta(self, sequence: int) -> Delta:
        surface: Surface | None = None
        if self.read is not None:
            platforms, extractor, help_ = self.read
            verbs: dict[str, dict[str, Any] | None] = {}
            for name, patch in self.verbs.items():
                if patch is None:
                    verbs[name] = None
                    continue
                trimmed = {k: v for k, v in patch.items() if k != "options"}
                if patch["options"]:
                    trimmed["options"] = patch["options"]
                verbs[name] = trimmed
            surface = Surface(platforms, extractor, help_, verbs, self.absent)
        return Delta(
            sequence,
            self.version,
            self.date,
            self.artifacts,
            self.layout,
            self.host_layouts,
            surface,
        )


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
        str(values["root"]).replace(VERSION_VAR, version),
        str(values["file"]),
        str(values["format"]),
        tuple(values["entry_points"]),
        tuple(values["paths"]),
        dict(values["env"]),
        dict(values["shims"]),
        tuple(values["exclude"]),
    )


def observations(record: Record) -> tuple[Observation, ...]:
    """Every version's whole surface, in sequence, resolved through inheritance.

    A version whose delta carries no surface is left out: nothing was
    read for it, and inheriting a reading would claim one.
    """
    out: list[Observation] = []
    help_ = ""
    verbs: dict[str, dict[str, Any]] = {}
    for delta in record.deltas:
        surface = delta.surface
        if surface is None:
            continue
        if surface.help is not None:
            help_ = surface.help
        for name, patch in surface.verbs.items():
            if patch is None:
                verbs.pop(name, None)
            else:
                verbs[name] = _canonical_verb(_applied(verbs.get(name), patch))
        out.append(
            Observation(
                delta.version,
                delta.date,
                surface.platforms,
                surface.extractor,
                help_,
                {name: verbs[name] for name in sorted(verbs)},
                {
                    verb: {option: tuple(who) for option, who in options.items()}
                    for verb, options in surface.absent.items()
                },
            )
        )
    return tuple(out)


def surface_at(record: Record, version: str) -> Observation | None:
    """The whole surface of *version*, or `None` when no reading was taken for it.

    Raises:
        RecordError: when the record does not track *version*, naming
            the versions it does.
    """
    record.delta_for(version)
    for observation in observations(record):
        if observation.version == version:
            return observation
    return None


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
    if record.mode and record.mode not in MODES:
        raise RecordError(
            f"{where}: mode {record.mode!r} is not one of {', '.join(MODES)}"
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
    help_: str | None = None
    verbs: dict[str, dict[str, Any]] = {}
    for index, delta in enumerate(record.deltas, start=1):
        at = f"{where} version {delta.version}"
        if delta.sequence != index:
            raise RecordError(
                f"{at}: out of sequence; expected {index}, the versions run"
                " consecutively from 1"
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
        if delta.layout.set_fields() and not delta.artifacts:
            raise RecordError(f"{at}: a layout for a version with no host")
        if not delta.artifacts and delta.surface is None:
            raise RecordError(f"{at}: a version with neither an artifact nor a surface")
        for host in delta.hosts:
            values, restated = _resolve_fields(record, delta, host)
            if restated:
                raise RecordError(f"{at} {host}: {restated[0]}")
            missing = _incomplete(record, values, delta.artifacts[host].url)
            if missing:
                raise RecordError(
                    f"{at} {host}: resolves incomplete, {missing}; every host of"
                    " every version must resolve to a whole deployment"
                )
        if delta.surface is not None:
            if record.prime and version_key(delta.version) < version_key(record.prime):
                raise RecordError(
                    f"{at}: read below prime {record.prime}; the history reaches"
                    " no further back"
                )
            help_ = _validate_surface(delta.surface, help_, verbs, at=f"{at} read")
    # A tool-level layout that no version and host ever reads is not a
    # restatement, so a tool with no version is validated on its own.
    if not record.deltas:
        _, restated = _resolve_fields(
            record, Delta(1, "-"), record.hosts[0] if record.hosts else "-"
        )
        if restated:
            raise RecordError(f"{where}: {restated[0]}")


def _validate_surface(
    surface: Surface, help_: str | None, verbs: dict[str, dict[str, Any]], *, at: str
) -> str:
    """Refuse a surface that breaks a rule, and fold it into the inherited state.

    *help_* is the description inherited so far, `None` before any
    surface, and *verbs* the verbs inherited so far, rewritten in place.
    Returns the description after this surface.
    """
    if not surface.platforms:
        raise RecordError(f"{at}: no platform read it")
    for platform in surface.platforms:
        if platform not in SURFACE_PLATFORMS:
            raise RecordError(
                f"{at}: platform {platform!r} is not one of"
                f" {', '.join(SURFACE_PLATFORMS)}"
            )
    if len(set(surface.platforms)) != len(surface.platforms):
        raise RecordError(f"{at}: a platform is listed twice")
    if surface.extractor < 1:
        raise RecordError(f"{at}: extractor is not a positive integer")
    if surface.help is None and help_ is None:
        raise RecordError(f"{at}: the record's first surface names no help")
    if surface.help is not None and surface.help == help_:
        raise RecordError(f"{at}: restates help as it inherits it")
    for name, patch in surface.verbs.items():
        current = verbs.get(name)
        if patch is None:
            if current is None:
                raise RecordError(
                    f"{at}: withdraws verb {name!r}, which no earlier version has"
                )
            del verbs[name]
            continue
        fault = _patch_fault(patch)
        if fault:
            raise RecordError(f"{at} verb {name!r}: {fault}")
        if current is not None:
            for key in _VERB_FIELDS:
                if key in patch and patch[key] == current[key]:
                    raise RecordError(
                        f"{at}: restates {key} of verb {name!r} as it inherits it"
                    )
        for option, value in patch.get("options", {}).items():
            held = None if current is None else current["options"].get(option)
            if value is None and held is None:
                raise RecordError(
                    f"{at}: withdraws option {option!r} of verb {name!r}, which the"
                    " version lacks"
                )
            if value is not None and held is not None and _option_json(value) == held:
                raise RecordError(
                    f"{at}: restates option {option!r} of verb {name!r} as it"
                    " inherits it"
                )
        folded = _applied(current, patch)
        fault = _verb_fault(folded)
        if fault:
            raise RecordError(f"{at} verb {name!r}: {fault}")
        verbs[name] = _canonical_verb(folded)
    for verb_name, options in surface.absent.items():
        if verb_name not in verbs:
            raise RecordError(
                f"{at}: absent names verb {verb_name!r}, which the version lacks"
            )
        for option, who in options.items():
            if option and option not in verbs[verb_name]["options"]:
                raise RecordError(
                    f"{at}: absent names option {option!r} of verb {verb_name!r},"
                    " which the version lacks"
                )
            if not who:
                raise RecordError(
                    f"{at}: absent names no platform for {verb_name!r} {option!r}"
                )
            for platform in who:
                if platform not in surface.platforms:
                    raise RecordError(
                        f"{at}: absent names {platform}, which did not read the"
                        f" version; it was read on {', '.join(surface.platforms)}"
                    )
    return surface.help if surface.help is not None else str(help_)


def artifact_format(url: str) -> str:
    """What the artifact at *url* is by its suffix: `zip`, `tar`, or `file`."""
    name = url.rsplit("/", 1)[-1].lower()
    if name.endswith(".zip"):
        return "zip"
    if name.endswith(TAR_SUFFIXES):
        return "tar"
    return "file"


TAR_SUFFIXES = (".tar.gz", ".tgz", ".tar.xz", ".tar.bz2")
"""The suffixes of the tar archives the store unpacks."""

ARCHIVE_SUFFIXES = (*TAR_SUFFIXES, ".zip")
"""The suffixes of every archive the store unpacks."""


def _incomplete(record: Record, values: Mapping[str, Any], url: str = "") -> str:
    """What a resolved layout still lacks, or empty when it is whole.

    *url* is the host's artifact, which decides whether a download
    lands an archive or one bare file when the layout's `format` does
    not say.
    """
    if record.kind != "download":
        return "" if values["paths"] else "paths is empty"
    form = str(values["format"] or artifact_format(url))
    if form not in FORMATS:
        return f"format {form!r} is not one of {', '.join(FORMATS)}"
    exposes = bool(values["paths"] or values["entry_points"])
    named = any(PACKAGE_VAR in value for value in values["env"].values())
    if not exposes and not named:
        return (
            "nothing reaches it: no paths, no entry point, and no env value"
            f" under {PACKAGE_VAR}"
        )
    # What goes on PATH is declared, never discovered: a path directory
    # comes with the entry points in it, and an entry point with a
    # directory to be found in.
    if values["paths"] and not values["entry_points"]:
        return "no entry point is declared"
    if values["entry_points"] and not values["paths"]:
        return "an entry point is declared with no paths to find it on"
    if form == "file":
        if not values["file"]:
            return "a bare download names no file"
        # One file lands, so at most one entry point exists: that file.
        entries = tuple(values["entry_points"])
        if entries and entries != (values["file"],):
            return (
                f"a bare download's entry points are its file {values['file']!r}"
                f" alone, not {', '.join(entries)}"
            )
    return ""


_PATCHLEVEL = re.compile(r"p(\d+)$")


def class_name(name: str) -> str:
    """The class a tool's stub declares: `ruff_format` is `RuffFormat`.

    Each part of the name, split on `_` and `-`, is title-cased and the
    parts are joined. The workshop composes a typing package's index
    from this rule, and the bench renders the stub's class with it, so
    the two agree without either reading the other's output.
    """
    return "".join(part.title() for part in name.replace("-", "_").split("_"))


def version_key(version: str, date: str = "") -> tuple[tuple[int, ...], int, str]:
    """How versions order: the numeric run, OpenSSH's patchlevel, then the date.

    `version_tuple` reads two builds of one base as equal and leaves the
    tie to the caller; the patchlevel places `9.9p2` after `9.9p1`, and
    the date breaks what remains. A record's deltas run in this order.
    """
    match = _PATCHLEVEL.search(version)
    return version_tuple(version), int(match[1]) if match else 0, date


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
    """The JSON schema of a record file's lines, for editor completion.

    One schema, three shapes under `$defs`: `Tool` for the first line,
    `Version` for a version line and `Statement` for a line under it;
    a line is one of the three. Every object is closed to unknown keys.
    """
    layout = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "root": {"type": "string"},
            "file": {"type": "string"},
            "format": {"type": "string", "enum": list(FORMATS)},
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
            "kind": {"type": "string", "enum": list(KINDS), "default": "download"},
            "package": {"type": "string", "default": ""},
            "mode": {"type": "string", "enum": list(MODES)},
            "min_version": {"type": "string", "default": ""},
            "prime": {"type": "string", "default": ""},
            "hosts": {
                "type": "array",
                "items": {"type": "string", "enum": list(HOSTS)},
                "uniqueItems": True,
            },
            "layout": {"$ref": "#/$defs/Layout"},
            "host_layouts": host_layouts,
        },
    }
    option = {
        "flags": {"type": "array", "items": {"type": "string"}},
        "negation": {"type": "string"},
        "help": {"type": "string"},
        "type": {"type": "string"},
        "default": {},
        "choices": {"type": "array", "items": {"type": "string"}},
    }
    platforms = {
        "type": "array",
        "items": {"type": "string", "enum": list(SURFACE_PLATFORMS)},
        "minItems": 1,
        "uniqueItems": True,
    }
    version = {
        "type": "object",
        "additionalProperties": False,
        "required": ["version"],
        "properties": {
            "version": {"type": "string", "minLength": 1},
            "date": {"type": "string", "default": ""},
            "artifacts": {
                "type": "object",
                "propertyNames": {"enum": list(HOSTS)},
                "additionalProperties": {"$ref": "#/$defs/Artifact"},
            },
            "layout": {"$ref": "#/$defs/Layout"},
            "host_layouts": host_layouts,
            "read": {
                "type": "object",
                "additionalProperties": False,
                "required": ["platforms", "extractor"],
                "properties": {
                    "platforms": platforms,
                    "extractor": {"type": "integer", "minimum": 1},
                    "help": {"type": "string"},
                },
            },
        },
    }
    statement = {
        "type": "object",
        "additionalProperties": False,
        "required": ["verb"],
        "properties": {
            "verb": {"type": "string"},
            "option": {"type": "string"},
            "gone": {"const": True},
            "absent": platforms,
            "wraps": {"type": "boolean"},
            "positional": {"type": "string"},
            "lead": {"type": "string"},
            **option,
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Record",
        "oneOf": [
            {"$ref": "#/$defs/Tool"},
            {"$ref": "#/$defs/Version"},
            {"$ref": "#/$defs/Statement"},
        ],
        "$defs": {
            "Layout": layout,
            "Artifact": artifact,
            "Tool": tool,
            "Version": version,
            "Statement": statement,
        },
    }


def export_schema(path: Path) -> None:
    """Write `schema()` to *path* as indented JSON with a trailing newline."""
    path.write_text(json.dumps(schema(), indent=2) + "\n", encoding="utf-8")
