"""Compose, render and deliver the files the extensions keep current.

Every piece of content an extension delivers into a workspace is a
[livery.workshop._fragment_engine.Fragment][]: an owner, a name, a
target path and the template its content renders from. `plan` turns the
listed extensions' fragments into one
[livery.workshop._fragment_engine.Output][] per file, and `apply`
writes them and records what it wrote; `drift` compares them with the
committed files instead.

How the fragments of one target combine depends on the target's type:

- a TOML file joins its fragments' tables in extension order;
- an ignore or attribute file joins their lines, each line once, and
  puts the repository's regions after every extension's lines;
- a JSON file merges their objects key by key;
- any other file has one owner.

The extensions apply in the order the workspace lists them, the base
first and the repository's own fragments last. An upper extension that
takes over or removes another's fragment declares it, with a reason,
through `replaces` or `deletes`; a second owner of a one-owner file
without that declaration refuses, and so does a declaration naming a
fragment no listed extension ships.

A target is a path at the root, or `package/<path>` for that path in
every package whose extensions include the owner.

The receipts in `.workshop-rendered`, at the root and in each package,
record the digest of each file as the engine last wrote it, its
regions left out, so an edit inside a region does not count as an
edit of the file. A file
whose fragments are gone is removed only while it still has those
bytes; a file edited since is kept and named as the repository's own,
and so is an edited file the engine would otherwise rewrite. Regions,
the repository's own lines between marker comments, are read from the
committed file and written back in place on every render.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

from livery.footman.api import fail, prog

PACKAGE_PREFIX = "package/"
"""The target prefix that names a path inside each package of the owner."""

RENDERED_MANIFEST = ".workshop-rendered"
"""The receipt file, name to digest, at the root and in each package."""

Composition = Literal["tables", "lines", "json", "whole"]
"""How the fragments of one target combine."""


@dataclass(frozen=True)
class Fragment:
    """One piece of content an extension delivers.

    Exactly one of *content*, *source* and *dynamic* carries the
    template, except on a fragment that only `deletes`.

    Attributes:
        owner: The extension that ships it, as the workspace's order
            names it.
        name: The fragment's name, unique within its owner.
        target: The path it renders into, relative to the root, or
            `package/<path>` for every package of the owner.
        content: The template source as a string.
        source: A file in the extension's wheel holding the template.
        dynamic: A function of the render data returning the template
            source, for content no static template can express.
        replaces: `<owner>:<name>` of a lower fragment this one takes
            the place of.
        deletes: `<owner>:<name>` of a lower fragment this one removes.
        reason: Why the replacement or deletion is wanted; required
            with either.
    """

    owner: str
    name: str
    target: str
    content: str = ""
    source: Path | None = None
    dynamic: Callable[[Mapping[str, Any]], str] | None = field(
        default=None, compare=False
    )
    replaces: str = ""
    deletes: str = ""
    reason: str = ""

    @property
    def ref(self) -> str:
        """The fragment as `replaces` and `deletes` name it: `<owner>:<name>`."""
        return f"{self.owner}:{self.name}"

    def template(self, data: Mapping[str, Any]) -> str:
        """The template source this fragment renders from."""
        if self.dynamic is not None:
            return self.dynamic(data)
        if self.source is not None:
            return self.source.read_text(encoding="utf-8")
        return self.content


@dataclass(frozen=True)
class Output:
    """One file as the engine renders it.

    Attributes:
        path: The file, relative to the workspace root, with `/`.
        body: The bytes the file should hold.
        owners: The fragments it was composed from, as `<owner>:<name>`,
            in the order they applied.
    """

    path: str
    body: bytes
    owners: tuple[str, ...]


def composition(target: str) -> Composition:
    """How the fragments of *target* combine, by the file's type."""
    name = PurePosixPath(target).name
    if name.endswith(".toml"):
        return "tables"
    if name in (".gitignore", ".gitattributes") or name.endswith("ignore"):
        return "lines"
    if name.endswith(".json"):
        return "json"
    return "whole"


def _resolve(fragments: Sequence[Fragment], order: Sequence[str]) -> list[Fragment]:
    """The fragments left once every `replaces` and `deletes` has applied."""
    rank = {owner: index for index, owner in enumerate(order)}
    by_ref: dict[str, Fragment] = {}
    for fragment in fragments:
        if fragment.owner not in rank:
            fail(
                f"fragment {fragment.ref}: {fragment.owner} is not a listed"
                f" extension; the order is {', '.join(order)}"
            )
        if fragment.ref in by_ref:
            fail(
                f"fragment {fragment.ref} is shipped twice; a name is unique per owner"
            )
        by_ref[fragment.ref] = fragment
    gone: set[str] = set()
    for fragment in fragments:
        for verb, ref in (
            ("replaces", fragment.replaces),
            ("deletes", fragment.deletes),
        ):
            if not ref:
                continue
            if not fragment.reason:
                fail(f"fragment {fragment.ref} {verb} {ref} without a reason; give one")
            lower = by_ref.get(ref)
            if lower is None:
                fail(
                    f"fragment {fragment.ref} {verb} {ref}, which no listed"
                    " extension ships"
                )
            if rank[lower.owner] >= rank[fragment.owner]:
                fail(
                    f"fragment {fragment.ref} {verb} {ref}, which applies after"
                    f" it; only a later extension {verb} an earlier one's fragment"
                )
            gone.add(ref)
    return [
        fragment
        for fragment in fragments
        if fragment.ref not in gone and not fragment.deletes
    ]


def _targets(fragment: Fragment, packages: Mapping[str, Sequence[str]]) -> list[str]:
    """The root-relative paths *fragment* renders into."""
    if not fragment.target.startswith(PACKAGE_PREFIX):
        return [fragment.target]
    inner = fragment.target.removeprefix(PACKAGE_PREFIX)
    return [
        f"{package}/{inner}"
        for package, extensions in sorted(packages.items())
        if fragment.owner in extensions
    ]


def _package_of(path: str, packages: Mapping[str, Sequence[str]]) -> str:
    """The package *path* lies in, or empty for the root."""
    for package in packages:
        if path.startswith(f"{package}/"):
            return package
    return ""


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


_ENVIRONMENT: Any = None
_COMPILED: set[str] = set()
_CACHE: dict[tuple[str, str], str] = {}


def _template_error(fragment: Fragment, error: Any) -> str:
    """The refusal for a template that does not compile or render."""
    # The template is registered under its digest, which says nothing
    # to a reader; the fragment's name and the line do.
    message = str(error.message).split(" (in ")[0]
    return f"fragment {fragment.ref}, line {error.line}: {message}"


def _render(fragment: Fragment, data: Mapping[str, Any]) -> str:
    """*fragment*'s text under *data*, rendered once per content and data."""
    global _ENVIRONMENT
    source = fragment.template(data)
    plain = json.dumps(data, sort_keys=True, default=str)
    key = (_digest(source.encode()), _digest(plain.encode()))
    if key in _CACHE:
        return _CACHE[key]
    import minijinja

    if _ENVIRONMENT is None:
        _ENVIRONMENT = minijinja.Environment(
            keep_trailing_newline=True, undefined_behavior="strict"
        )
    if key[0] not in _COMPILED:
        try:
            _ENVIRONMENT.add_template(key[0], source)
        except minijinja.TemplateError as error:
            fail(_template_error(fragment, error))
        _COMPILED.add(key[0])
    try:
        text = cast("str", _ENVIRONMENT.render_template(key[0], **json.loads(plain)))
    except minijinja.TemplateError as error:
        fail(_template_error(fragment, error))
    _CACHE[key] = text
    return text


def _merge(
    into: dict[str, Any], part: Mapping[str, Any], owners: dict[str, str], ref: str
) -> None:
    """Merge *part* into *into* by key; two owners of one value refuse."""

    def walk(target: dict[str, Any], incoming: Mapping[str, Any], prefix: str) -> None:
        for key, value in incoming.items():
            where = f"{prefix}{key}"
            if key not in target:
                target[key] = value
                owners[where] = ref
            elif isinstance(target[key], dict) and isinstance(value, Mapping):
                walk(
                    cast("dict[str, Any]", target[key]),
                    cast("Mapping[str, Any]", value),
                    f"{where}.",
                )
            elif isinstance(target[key], list) and isinstance(value, list):
                merged = cast("list[Any]", target[key])
                merged.extend(item for item in value if item not in merged)
            elif target[key] != value:
                # A value arrives inside the object that first carried
                # it, so its owner is the nearest owned ancestor's.
                path = where.split(".")
                first = next(
                    owners[".".join(path[:end])]
                    for end in range(len(path), 0, -1)
                    if ".".join(path[:end]) in owners
                )
                fail(
                    f"{where} is set by both {first} and {ref}; declare"
                    " `replaces` with a reason on the later one"
                )

    walk(into, part, "")


def _compose(path: str, parts: list[tuple[Fragment, str]]) -> str:
    """The text of *path* from its rendered *parts*, in the order they apply."""
    kind = composition(path)
    if kind == "whole":
        if len(parts) > 1:
            names = " and ".join(fragment.ref for fragment, _text in parts)
            fail(
                f"{path} has one owner, and {names} both render it; declare"
                " `replaces` or `deletes` with a reason on the later one"
            )
        return parts[0][1]
    if kind == "tables":
        import tomllib

        text = "\n".join(text.strip("\n") + "\n" for _fragment, text in parts)
        try:
            tomllib.loads(text)
        except tomllib.TOMLDecodeError as error:
            names = ", ".join(fragment.ref for fragment, _text in parts)
            fail(f"{path}: the tables of {names} do not compose: {error}")
        return text
    if kind == "lines":
        from livery.workshop._regions import regions_in

        seen: set[str] = set()
        lines: list[str] = []
        regions: list[str] = []
        for _fragment, text in parts:
            # A region is the repository's, and its lines come after
            # every extension's, whichever fragment carries the markers.
            split = text.splitlines()
            spans = [(region.first, region.last) for region in regions_in(text)]
            inside = {n for first, last in spans for n in range(first, last + 1)}
            regions.extend(split[n - 1] for n in sorted(inside))
            for number, line in enumerate(split, start=1):
                if number in inside:
                    continue
                keyed = line.strip()
                if keyed and not keyed.startswith("#"):
                    if keyed in seen:
                        continue
                    seen.add(keyed)
                lines.append(line)
        return "\n".join([*lines, *regions]) + "\n"
    merged: dict[str, Any] = {}
    owners: dict[str, str] = {}
    for fragment, text in parts:
        try:
            part = json.loads(text)
        except ValueError as error:
            fail(f"{path}: fragment {fragment.ref} is not JSON: {error}")
        if not isinstance(part, dict):
            fail(f"{path}: fragment {fragment.ref} is not a JSON object")
        _merge(merged, cast("dict[str, Any]", part), owners, fragment.ref)
    return json.dumps(merged, indent=2) + "\n"


def _splice(rendered: str, committed: str) -> str:
    """*rendered* with each region's content taken from *committed*."""
    from livery.workshop._regions import regions_in

    kept = {region.name: region.content for region in regions_in(committed)}
    lines = rendered.split("\n")
    out: list[str] = []
    cursor = 0
    for region in regions_in(rendered):
        out.extend(lines[cursor : region.first])
        body = kept.get(region.name, region.content)
        out.extend(body.split("\n")[:-1] if body else [])
        cursor = region.last - 1
    out.extend(lines[cursor:])
    return "\n".join(out)


def plan(
    root: Path,
    fragments: Sequence[Fragment],
    order: Sequence[str],
    data: Mapping[str, Any],
    *,
    packages: Mapping[str, Sequence[str]] | None = None,
) -> tuple[Output, ...]:
    """Every file the listed fragments render, composed and with its regions kept.

    Args:
        root: The workspace root, where the committed files are read
            for their regions.
        fragments: What the listed extensions ship.
        order: The owners in the order they apply, the base first and
            the repository last.
        data: The render data every template sees. A package target
            also sees `package`, the package's path.
        packages: Each package's path, relative to the root, to the
            extensions it lists.

    Returns:
        One output per file, sorted by path.

    Raises:
        Failed: for a second owner of a one-owner file, a `replaces`
            or `deletes` without a reason or naming a fragment no
            listed extension ships, a template that does not render,
            and parts that do not compose.
    """
    packaged = dict(packages or {})
    rank = {owner: index for index, owner in enumerate(order)}
    grouped: dict[str, list[Fragment]] = {}
    for fragment in _resolve(fragments, order):
        for path in _targets(fragment, packaged):
            grouped.setdefault(path, []).append(fragment)
    outputs: list[Output] = []
    for path, members in sorted(grouped.items()):
        members.sort(key=lambda fragment: rank[fragment.owner])
        package = _package_of(path, packaged)
        seen = {**data, "package": package} if package else dict(data)
        parts = [(fragment, _render(fragment, seen)) for fragment in members]
        text = _compose(path, parts)
        committed = root / path
        if committed.is_file():
            text = _splice(text, committed.read_text(encoding="utf-8"))
        outputs.append(
            Output(path, text.encode(), tuple(fragment.ref for fragment in members))
        )
    return tuple(outputs)


def _owned(body: bytes) -> str:
    """The digest of what the engine owns in *body*: everything but its regions.

    A region's lines are the repository's, so an edit inside one is not
    an edit of the engine's file, and a receipt never records them.
    """
    from livery.workshop._regions import regions_in

    text = body.decode("utf-8", errors="replace")
    regions = regions_in(text)
    if not regions:
        return _digest(body)
    lines = text.split("\n")
    inside = {n for r in regions for n in range(r.first + 1, r.last)}
    kept = [line for number, line in enumerate(lines, start=1) if number not in inside]
    return _digest("\n".join(kept).encode())


def _committed(path: Path) -> bytes:
    """*path*'s bytes with LF line endings, as the engine writes and compares them.

    A Windows checkout may hold CRLF where the repository has LF; the
    file is the same file, so neither an edit nor drift.
    """
    return path.read_bytes().replace(b"\r\n", b"\n")


def read_rendered(directory: Path) -> dict[str, str]:
    """The receipts in *directory*, name to digest; empty without any."""
    path = directory / RENDERED_MANIFEST
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text("utf-8"))
    except ValueError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {str(k): str(v) for k, v in cast("dict[object, object]", loaded).items()}


def write_rendered(directory: Path, receipts: Mapping[str, str]) -> None:
    """Write the receipts, or remove the file when there are none."""
    path = directory / RENDERED_MANIFEST
    if not receipts:
        if path.is_file():
            path.unlink()
        return
    path.write_bytes(
        (json.dumps(dict(sorted(receipts.items())), indent=2) + "\n").encode()
    )


def apply(
    root: Path,
    outputs: Sequence[Output],
    *,
    packages: Sequence[str] = (),
) -> list[str]:
    """Write *outputs*, withdraw what is gone, and say what changed.

    A file is written when it is missing, and rewritten when its bytes
    are still what the engine last wrote. A file edited since is kept
    and named, and so is a file the engine has no receipt for whose
    bytes differ. A receipted file no output renders any more is
    removed when unedited and kept and named otherwise; either way its
    receipt goes, so a kept file is the repository's from then on.

    Args:
        root: The workspace root.
        outputs: What `plan` rendered.
        packages: The package paths whose receipts the engine keeps,
            relative to the root.

    Returns:
        One line per file written, removed or kept, in path order.
    """
    homes = ["", *sorted(packages, key=len, reverse=True)]

    def home_of(path: str) -> str:
        for home in homes[1:]:
            if path.startswith(f"{home}/"):
                return home
        return ""

    receipts = {home: read_rendered(root / home) for home in homes}
    lines: list[str] = []
    rendered: set[str] = set()
    for output in outputs:
        rendered.add(output.path)
        home = home_of(output.path)
        name = output.path.removeprefix(f"{home}/") if home else output.path
        target = root / output.path
        recorded = receipts[home].get(name)
        if not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(output.body)
            lines.append(f"  wrote {output.path}")
        elif (existing := _committed(target)) == output.body:
            pass
        elif recorded is not None and _owned(existing) == recorded:
            target.write_bytes(output.body)
            lines.append(f"  updated {output.path}")
        else:
            lines.append(
                f"  kept {output.path}: edited here, so it is not rewritten;"
                " delete it to take the rendered file"
            )
            continue
        receipts[home][name] = _owned(output.body)
    for home in homes:
        for name, recorded in sorted(receipts[home].items()):
            path = f"{home}/{name}" if home else name
            if path in rendered:
                continue
            del receipts[home][name]
            target = root / path
            if not target.is_file():
                continue
            if _owned(_committed(target)) == recorded:
                target.unlink()
                lines.append(f"  removed {path}: no listed extension renders it")
            else:
                lines.append(
                    f"  kept {path}: no listed extension renders it, and it was"
                    " edited here, so it stays as the repository's own"
                )
        write_rendered(root / home, receipts[home])
    return sorted(lines, key=lambda line: line.split()[1])


def drift(root: Path, outputs: Sequence[Output]) -> list[str]:
    """One line per file whose committed bytes are not what it renders."""
    lines: list[str] = []
    for output in outputs:
        target = root / output.path
        if not target.is_file():
            lines.append(f"  {output.path}: missing; `{prog()} sync` writes it")
        elif _committed(target) != output.body:
            owners = ", ".join(output.owners)
            lines.append(f"  {output.path}: differs from what {owners} render")
    return lines
