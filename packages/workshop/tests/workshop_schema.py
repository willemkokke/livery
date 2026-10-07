"""A JSON Schema validator over the subset the composed contract schemas use.

Types, properties, ``additionalProperties``, items, ``enum`` and
``anyOf``: what ``livery.workshop._schema`` writes. Enough to prove a
composed file accepts the contracts it describes and refuses what the
judge refuses.
"""

from __future__ import annotations

from typing import Any, cast

_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


def problems(schema: dict[str, Any], value: object, where: str = "") -> list[str]:
    """Each place *value* breaks *schema*, as ``<dotted path>: <what>``."""
    shown = where or "<top>"
    if "anyOf" in schema:
        branches = cast("list[dict[str, Any]]", schema["anyOf"])
        if all(problems(branch, value, where) for branch in branches):
            return [f"{shown}: fits none of its {len(branches)} shapes"]
        return []
    kind = schema.get("type")
    if kind is not None:
        numeric = kind in ("integer", "number")
        if not isinstance(value, _TYPES[kind]) or (numeric and isinstance(value, bool)):
            return [f"{shown}: is not {kind}"]
    found: list[str] = []
    if "enum" in schema and value not in schema["enum"]:
        found.append(f"{shown}: {value!r} is not one of {schema['enum']}")
    if isinstance(value, dict):
        properties = cast("dict[str, dict[str, Any]]", schema.get("properties", {}))
        extra = schema.get("additionalProperties", True)
        for key, item in cast("dict[str, object]", value).items():
            path = f"{where}.{key}" if where else key
            if key in properties:
                found += problems(properties[key], item, path)
            elif extra is False:
                found.append(f"{path}: not declared")
            elif isinstance(extra, dict):
                found += problems(cast("dict[str, Any]", extra), item, path)
    if isinstance(value, list) and "items" in schema:
        entries = cast("dict[str, Any]", schema["items"])
        for index, item in enumerate(cast("list[object]", value)):
            found += problems(entries, item, f"{where}[{index}]")
    return found
