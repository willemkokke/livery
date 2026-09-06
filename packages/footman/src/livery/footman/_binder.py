"""The document binder: a JSON value from the boundary into a typed shape.

The inbound half of "dataclass in, dataclass out" (`_describe.json_default`
serialises the way out). Structural rules, in priority order:

- **Unknown keys are ignored, never refused.** A producer adds fields over
  time; a consumer that breaks when its input gains a field is worse than
  one that ignores it.
- **Missing keys follow the dataclass**: a field with a default is optional,
  a defaultless field that is absent is a taught refusal naming its path.
- **No aliasing layer.** Keys map to field names directly.
- **Recursion** covers nested dataclasses, `list[T]`, `dict[str, V]` and
  `T | None`; scalar leaves reuse the one coercion pipeline, so `Path`,
  `Literal`, enums and `datetime` behave exactly as a CLI token would.
- **Refuse the rest, taught.** This is a binder, not a validation DSL —
  `check(…)` owns validation, and every error names the JSON path
  (`event.tool_input.file_path: expected text, got 3`).
"""

from __future__ import annotations

import dataclasses
import enum
import typing
from typing import Any

from livery.footman import _coerce

_MISSING = dataclasses.MISSING


def record_fields(target: Any) -> tuple[tuple[str, ...], frozenset[str]] | None:
    """`(field names, the ones with no default)` for a record — the named
    view of `_coerce.fields_of`, which is what a JSON object binds to.

    One source so `is_document_target` and the binding branch cannot
    disagree about what a record is. They did: a `NamedTuple` failed every
    test in the old `is_document_target` (it is a `tuple` subclass, so
    `get_origin` is `None`), fell through to the text path, and handed the
    body a raw string with no warning at all.
    """
    fields = _coerce.fields_of(target)
    if fields is None:
        return None
    return (
        tuple(f.name for f in fields),
        frozenset(f.name for f in fields if f.required),
    )


def is_document_target(ann: Any) -> bool:
    """Whether an annotation names a shape a JSON document binds to — a
    record (dataclass / `NamedTuple` / `TypedDict`), a `dict`, or a `list`
    (bare or subscripted)."""
    if record_fields(ann) is not None:
        return True
    origin = typing.get_origin(ann)
    return ann in (dict, list) or origin in (dict, list)


def _json_name(value: Any) -> str:
    return {
        type(None): "null",
        bool: "true/false",
        int: "a number",
        float: "a number",
        str: "text",
        list: "an array",
        dict: "an object",
    }.get(type(value), type(value).__name__)


def _strip(ann: Any) -> Any:
    """Peel `Annotated[...]` wrappers off a field annotation (markers inside a
    payload dataclass carry no meaning here)."""
    while typing.get_origin(ann) is typing.Annotated:
        ann = typing.get_args(ann)[0]
    return ann


def _optional_member(ann: Any) -> Any:
    """The union minus its `None` member, or `None` when *ann* admits none.

    Any union carrying `None` admits a JSON null — `int | str | None` no
    less than `int | None`. The old exactly-two-members reading refused
    null for the wider shape: the union kept its other members and lost
    its nullability. More than one member left keeps union shape, so the
    remainder binds through the same walk.
    """
    if _coerce._is_union(ann):
        members = [m for m in typing.get_args(ann) if m is not type(None)]
        if members and len(members) < len(typing.get_args(ann)):
            remainder = members[0]
            for member in members[1:]:
                remainder = remainder | member
            return remainder
    return None


def _field_types(target: Any, path: str) -> dict[str, Any]:
    try:
        return typing.get_type_hints(target, include_extras=True)
    except NameError as exc:
        raise ValueError(
            f"{path}: the annotations of {target.__name__} did not resolve "
            f"({exc}) — a stdin payload dataclass and everything it names "
            f"must be module-level, where `eval_str` can see them"
        ) from exc


def bind_document(value: Any, target: Any, path: str) -> Any:
    """Bind one JSON *value* to *target*, recursing; *path* names where we are
    (`event`, `event.tool_input.file_path`, `rows[2]`) so a refusal points at
    the exact spot in the document."""
    target = _strip(target)

    if target is Any or target is object:
        return value

    member = _optional_member(target)
    if member is not None:
        return None if value is None else bind_document(value, member, path)

    record = record_fields(target)
    if record is not None:
        names, required = record
        if not isinstance(value, dict):
            raise ValueError(
                f"{path}: expected an object for {target.__name__}, "
                f"got {_json_name(value)}"
            )
        hints = _field_types(target, path)
        kwargs: dict[str, Any] = {}
        for name in names:
            if name in value:
                kwargs[name] = bind_document(
                    value[name], hints.get(name, Any), f"{path}.{name}"
                )
            elif name in required:
                raise ValueError(
                    f"{path}: the document has no {name!r} field and "
                    f"{target.__name__}.{name} has no default"
                )
        # A dataclass and a NamedTuple construct from keywords; a TypedDict
        # called this way is simply the dict it always was at runtime.
        return target(**kwargs)

    origin = typing.get_origin(target)
    if target is list or origin is list:
        if not isinstance(value, list):
            raise ValueError(f"{path}: expected an array, got {_json_name(value)}")
        element = (typing.get_args(target) or (Any,))[0]
        return [
            bind_document(item, element, f"{path}[{index}]")
            for index, item in enumerate(value)
        ]

    if target is dict or origin is dict:
        if not isinstance(value, dict):
            raise ValueError(f"{path}: expected an object, got {_json_name(value)}")
        args = typing.get_args(target)
        if args and _strip(args[0]) is not str:
            raise ValueError(
                f"{path}: a JSON object's keys are text — dict keys must be "
                f"str, not {getattr(args[0], '__name__', args[0])}"
            )
        element = args[1] if len(args) > 1 else Any
        return {
            key: bind_document(item, element, f"{path}.{key}")
            for key, item in value.items()
        }

    return _leaf(value, target, path)


def _leaf(value: Any, target: Any, path: str) -> Any:
    """One scalar JSON value to one scalar annotation, through the same
    pipeline a CLI token gets when the value arrives as text."""
    if value is None or isinstance(value, (dict, list)):
        raise ValueError(f"{path}: expected {_phrase(target)}, got {_json_name(value)}")
    if isinstance(value, str):
        try:
            out = _coerce.coerce_token(value, target)
        except ValueError as exc:
            raise ValueError(f"{path}: {exc}") from exc
        choices = _coerce.all_choices(target)
        if choices is not None:
            # Choices speak tokens, so the coerced result is compared in
            # the same face: a member's token, or the raw text for a
            # Literal. Comparing `.value` here would refuse every member
            # of a value-carrying enum against its own token list.
            shown = _coerce.token_of(out) if isinstance(out, enum.Enum) else str(out)
            if shown not in choices:
                raise ValueError(
                    f"{path}: must be one of {'|'.join(choices)} (got {value!r})"
                )
        return out
    # A JSON number or bool: no token to parse, so the fit is structural.
    for member in _coerce.union_members(target):
        member = _strip(member)
        if isinstance(value, bool):
            if member is bool:
                return value
        elif isinstance(value, int):
            if member is int:
                return value
            if member is float:
                return float(value)
        elif isinstance(value, float) and member is float:
            return value
        literal_values = (
            typing.get_args(member)
            if typing.get_origin(member) is typing.Literal
            else ()
        )
        if any(value is lit or value == lit for lit in literal_values):
            return value
        found = _enum_member(member, value)
        if found is not None:
            return found
    raise ValueError(f"{path}: expected {_phrase(target)}, got {_json_name(value)}")


def _enum_member(member: Any, value: Any) -> Any:
    """The member of *member* whose value a JSON number or bool **is**, or
    None when *member* is not an enum or nothing matches.

    `json_default` writes an enum out as its `.value`, so a numeric-valued
    enum arrives back as a number and has to bind — footman has to be able
    to read the document it just wrote. Matching goes by value *and* type,
    because `1 == True` in Python: a JSON `true` must not answer an
    int-valued member, nor a JSON `1` a bool-valued one.
    """
    if not (isinstance(member, type) and issubclass(member, enum.Enum)):
        return None
    for candidate in member:
        if isinstance(candidate.value, bool) != isinstance(value, bool):
            continue
        if candidate.value == value:
            return candidate
    return None


def _phrase(target: Any) -> str:
    tags = _coerce.element_tags(target)
    if tags:
        return _coerce.type_phrase(tags)
    choices = _coerce.all_choices(target)
    if choices:
        return "one of " + "|".join(choices)
    return getattr(target, "__name__", str(target))
