"""Readings and records for the bench's tests: specs, surfaces, a record built from them.

Every tests directory shares one `pythonpath`, so this helper carries
the package's name. A test module imports what it needs from here and
never from another test module.
"""

from __future__ import annotations

import pathlib
from collections.abc import Mapping
from typing import Any

from livery.toolroom.bench import _surfaces
from livery.toolroom.store import Option, Record, ToolSpec, Verb


def spec_of(**over: Any) -> ToolSpec:
    """A demo tool: a bare verb with one flag, and a `build` verb with three options."""
    base = ToolSpec(
        name="demo",
        help="A demo tool.",
        version="1.0.0",
        verbs=(
            Verb(
                name="",
                help="The tool itself.",
                options=(Option("quiet", ("-q", "--quiet"), type_name="bool"),),
            ),
            Verb(
                name="build",
                help="Build it.",
                wraps=False,
                positional="required",
                lead="target",
                options=(
                    Option(
                        "output",
                        ("-o", "--output"),
                        help="Where to write.",
                        type_name="str",
                        default="dist",
                    ),
                    Option(
                        "clean",
                        ("--clean",),
                        negation="--dirty",
                        help="Clean first.",
                        type_name="bool",
                        default=True,
                    ),
                    Option(
                        "mode",
                        ("--mode",),
                        type_name="choice",
                        choices=("fast", "safe"),
                    ),
                ),
            ),
        ),
    )
    return ToolSpec(**{**base.__dict__, **over})


def platform() -> str:
    """The platform the suite runs on, as a reading names it."""
    from livery.toolroom.bench import _tasks

    return _tasks._platform()


def elsewhere() -> str:
    """A platform name that is never the one running the suite.

    The tests run on all three, so "a platform that has not looked" cannot
    be spelled with a literal: on the Linux runner, `"Linux"` is the host.
    """
    return next(p for p in ("Linux", "Windows", "macOS") if p != platform())


def history(name: str, *reads: tuple[Any, ...], kind: str = "uv-tool") -> Record:
    """A record built from `(version, date, surface[, platforms])` tuples.

    The first tuple opens the record and the rest are placed wherever
    they belong, so the tuples may arrive in any order. A tuple with no
    platforms was read on this machine.
    """
    first, *rest = reads
    record = _surfaces.new(
        name,
        kind=kind,
        version=first[0],
        date=first[1],
        surface=first[2],
        platforms=list(first[3]) if len(first) > 3 else [platform()],
    )
    for version, date, surface, *who in rest:
        placed = _surfaces.place(
            record,
            version=version,
            date=date,
            surface=surface,
            platforms=list(who[0]) if who else [platform()],
        )
        assert placed is not None, version
        record = placed
    return record


def chain_of(*surfaces: dict[str, Any]) -> tuple[Record, list[str]]:
    """A record built newest-last, the way a refresh adds to one: `1.0.0`, `1.0.1`, and on."""
    versions = [f"1.0.{n}" for n in range(len(surfaces))]
    record = history(
        "demo",
        *(
            (v, f"2026-01-0{n + 1}", s)
            for n, (v, s) in enumerate(zip(versions, surfaces, strict=True))
        ),
    )
    return record, versions


def with_options(*options: str, verb: str = "") -> dict[str, Any]:
    """A one-verb surface whose options carry a short and a long spelling."""
    return _surfaces.surface_of(
        spec_of(
            verbs=(
                Verb(
                    name=verb,
                    options=tuple(
                        Option(n, (f"-{n[0]}", f"--{n.replace('_', '-')}"))
                        for n in options
                    ),
                ),
            )
        )
    )


def with_flags(*names: str) -> dict[str, Any]:
    """A bare-verb surface with one long flag per name."""
    return _surfaces.surface_of(
        spec_of(
            verbs=(Verb(name="", options=tuple(Option(n, (f"--{n}",)) for n in names)),)
        )
    )


def reading(
    *options: str, verb: str = "", help_of: dict[str, str] | None = None
) -> dict[str, Any]:
    """One platform's surface for one release."""
    help_of = help_of or {}
    return _surfaces.surface_of(
        spec_of(
            verbs=(
                Verb(
                    name=verb,
                    options=tuple(
                        Option(n, (f"--{n}",), help=help_of.get(n, f"The {n}."))
                        for n in options
                    ),
                ),
            )
        )
    )


def described(text: str) -> dict[str, Any]:
    """A one-verb surface whose tool description is *text*."""
    return {
        "help": text,
        "verbs": {
            "": {
                "help": text,
                "wraps": False,
                "positional": "any",
                "lead": "",
                "options": {},
            }
        },
    }


def release_surface(n: int) -> dict[str, Any]:
    """A surface that differs at every release, so a wrong step cannot pass."""
    return _surfaces.surface_of(
        spec_of(
            verbs=(
                Verb(
                    name="",
                    help=f"Release {n}.",
                    options=tuple(
                        Option(f"opt{i}", (f"--opt{i}",), help=f"Option {i} at {n}.")
                        for i in range(n + 1)
                    ),
                ),
            )
        )
    )


def save(record: Record, root: pathlib.Path) -> None:
    """Write *record* under *root*, the records directory a test isolated."""
    _surfaces.save(record, root / f"{record.name}.jsonl")


def load(root: pathlib.Path, name: str) -> Record:
    """The record *name* under *root*; a test reading one expects it there."""
    record = _surfaces.load(root / f"{name}.jsonl")
    assert record is not None, name
    return record


def tools_run(line: str | list[str]) -> Any:
    """Drive the real CLI in-process.

    A list of arguments is passed through unsplit: a Windows path in a
    command string would be shlex-split and lose its backslashes, which
    is a fine way to make a cross-platform feature fail only on the
    platform it is about.
    """
    from livery.footman.testing import Runner
    from livery.toolroom.bench._tasks import tasks as tools_group

    return Runner().invoke(line, tasks=tools_group)


def isolate(tools: Any, monkeypatch: Any, tmp_path: pathlib.Path) -> pathlib.Path:
    """Point the bench at a scratch records directory and changelog.

    Returns the records directory.
    """
    monkeypatch.setattr(tools, "_RECORDS", tmp_path / "records")
    monkeypatch.setattr(tools, "_CHANGELOG", tmp_path / "CHANGELOG.md")
    (tmp_path / "records").mkdir()
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n", encoding="utf-8"
    )
    return tmp_path / "records"


def serve(
    monkeypatch: Any,
    listings: dict[str, list[Any]],
    surfaces: Mapping[tuple[str, str], dict[str, Any] | None],
    installed: list[tuple[str, str]] | None = None,
) -> list[tuple[str, str]]:
    """The fake tiers: a listing per tool, a surface per (tool, version).

    Called from worker threads, so mutation is `list.append`-shaped. An
    absent (tool, version) makes the install fail: a hole.
    """
    from livery.toolroom.bench import _artifacts, _drivers, _toolfetch

    installed = installed if installed is not None else []
    monkeypatch.setattr(
        _toolfetch, "releases", lambda driver: list(listings[driver.key])
    )

    def no_forge(
        name: str, forge: str, repo: str, version: str, tag: str
    ) -> list[tuple[str, str]]:
        # The fake tiers publish no assets: a forge-tier tool's fresh
        # version is folded and reported, never fetched from the network.
        raise _artifacts.ArtifactError(f"{name} {version}: no forge in the fake tiers")

    monkeypatch.setattr(_artifacts, "_assets", no_forge)

    def install(driver: Any, release: Any, into: pathlib.Path) -> pathlib.Path | None:
        installed.append((driver.key, release.version))
        if surfaces.get((driver.key, release.version)) is None:
            return None
        (into / "bin").mkdir(parents=True, exist_ok=True)
        (into / "bin" / driver.name).write_text("x")
        return into / "bin"

    monkeypatch.setattr(_toolfetch, "install", install)

    def extract(driver: Any) -> ToolSpec:
        import os

        spot = os.environ.get("PATH", "").split(os.pathsep)[0]
        version = pathlib.Path(spot).parent.name.split("==")[-1].rsplit("-", 1)[-1]
        found = surfaces[(driver.key, version)]
        assert found is not None
        return _surfaces.spec_from(found, name=driver.name)

    monkeypatch.setattr(_drivers, "extract", extract)
    return installed


def gathered(observed: int, missed: int, **over: Any) -> Any:
    """A gather's document with *observed* readings and *missed* holes."""
    from livery.toolroom.bench._tasks import Gathered

    return Gathered(
        platform="Linux",
        observations={"ruff": {f"1.0.{n}": {} for n in range(observed)}},
        holes={"ruff": [f"9.0.{n}" for n in range(missed)]} if missed else {},
        **over,
    )
