"""Annotation normalization and value coercion.

The manifest (introspection), the splitter (validation), and the executor
(binding) all reason about a parameter's type through this one module, so a
parameter's CLI shape is derived in exactly one place.

A parameter is normalized by `peel` into `(multiple, element, completer)`
and its scalar *element* is described as ordered "type tags"
(`bool`/`int`/`float`/`path`/`str`) or as choices (`Literal`/`Enum`).
Coercion tries the tags in **specificity order** — the most restrictive parser
first, `str` last as the universal fallback — so `str | int` turns `"5"`
into `5` and `"x"` into `"x"`.
"""

from __future__ import annotations

import dataclasses as _dataclasses
import datetime as _datetime
import enum
import types
import typing
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Annotated, Any, TypedDict

from livery.footman.params import (
    PathRequirement,
    StdoutMarker,
    ask,
    between,
    check,
    doc,
    env,
    matching,
    suggest,
)
from livery.footman.params import _arg as _ARG
from livery.footman.params import ask as _ask_marker
from livery.footman.params import default as _default_marker
from livery.footman.params import forward as _FORWARD
from livery.footman.params import hidden as _HIDDEN
from livery.footman.params import nosplit as _NOSPLIT
from livery.footman.params import stdin as _stdin_marker
from livery.footman.params import stdout as _STDOUT

_TAG_ORDER = {"bool": 0, "int": 1, "float": 2, "path": 3, "str": 4}

# The tokens a non-flag `bool` accepts (a scalar `bool` is a --flag and never
# parses a token; these cover bool inside collections, dict values, and unions).
_BOOL_TOKENS = {
    "true": True,
    "1": True,
    "yes": True,
    "on": True,
    "false": False,
    "0": False,
    "no": False,
    "off": False,
}


def _tag_of(t: Any) -> str | None:
    if t is bool:
        return "bool"
    if t is int:
        return "int"
    if t is float:
        return "float"
    if isinstance(t, type) and issubclass(t, PurePath):
        return "path"
    if t is str:
        return "str"
    return None


def _is_union(ann: Any) -> bool:
    return typing.get_origin(ann) in (typing.Union, types.UnionType)


def _strip_none(members: list[Any]) -> list[Any]:
    return [m for m in members if m is not type(None)]


def union_members(element: Any) -> list[Any]:
    """Members of a union (None stripped), or `[element]` for a non-union."""
    if _is_union(element):
        return _strip_none(list(typing.get_args(element)))
    return [element]


def _union_of(parts: list[Any]) -> Any:
    parts = list(dict.fromkeys(parts))
    union = parts[0]
    for part in parts[1:]:
        union = union | part
    return union


@dataclass
class Peeled:
    multiple: bool  # a list-valued parameter?
    element: Any  # scalar type / Union (or, for a mapping, the value type)
    completer: suggest | None
    nosplit: bool = False  # opt OUT of comma-splitting (collections split by default)
    mapping: bool = False  # a dict[K, V] parameter?
    key: Any = None  # mapping key type
    value_multiple: bool = False  # mapping value is a list (dict[K, list[E]])
    path_req: str | None = None  # exists / file / dir requirement on a Path
    glob: str | None = None  # matching(): narrow Tab to names like this
    bounds: tuple[float | None, float | None] | None = None  # inclusive lo/hi
    env: str | None = None  # environment-variable fallback
    checks: tuple[Any, ...] = ()  # post-coercion validators (check(fn))
    doc: str | None = None  # per-parameter help text (doc("..."))
    # `_ask_marker`, not `ask`: the field name shadows the class inside
    # this scope, so the plain spelling would annotate with the field.
    ask: _ask_marker | None = None  # prompt-if-missing marker (ask())
    forward: bool = False  # thread this value to dispatched tasks (forward)
    optional: bool = False  # Arg[T]: an optional trailing positional
    stdin: _stdin_marker | None = None  # bind from the boundary's stdin read
    hidden: bool = False  # out of the listings; still binds, still completes
    default_fn: _default_marker | None = None  # computed default (default(fn))
    container: type = list  # the collection named: list/tuple/set/frozenset


class _Markers(TypedDict):
    """The marker bundle `peel` collects and hands to `Peeled` — typed so the
    `**markers` unpack checks against the dataclass's own field types."""

    path_req: str | None
    glob: str | None
    bounds: tuple[float | None, float | None] | None
    env: str | None
    checks: tuple[Any, ...]
    doc: str | None
    ask: _ask_marker | None
    forward: bool
    optional: bool
    stdin: _stdin_marker | None
    hidden: bool
    default_fn: _default_marker | None


def collection_of(ann: Any) -> type | None:
    """The collection *ann* names, or `None` if it names none.

    `list[T]`, `set[T]`, `frozenset[T]` and `tuple[T, ...]` share one grammar
    — one or many, comma or repetition, `nosplit` and all — and differ only in
    what the body is handed. A *fixed-arity* `tuple[X, Y]` is a shape rather
    than a collection: its values are grouped, not accumulated, so `group_of`
    answers for it and this returns `None`.

    A bare `list` / `set` / `frozenset` / `tuple` means a collection of `str`,
    the way a bare annotation always does here.
    """
    origin = typing.get_origin(ann) or ann
    if origin is list or origin is set or origin is frozenset:
        return origin
    if origin is tuple:
        args = typing.get_args(ann)
        if not args or (len(args) == 2 and args[1] is Ellipsis):
            return tuple
    return None


def peel(ann: Any) -> Peeled:
    """Normalize a parameter annotation into (multiple, element, completer)."""
    completer: suggest | None = None
    is_nosplit = False
    path_req: str | None = None
    glob: str | None = None
    bounds: tuple[float | None, float | None] | None = None
    env_var: str | None = None
    checks: tuple[Any, ...] = ()
    doc_text: str | None = None
    ask_marker: ask | None = None
    is_forward = False
    is_hidden = False
    is_optional = False
    stdin_marker: _stdin_marker | None = None
    default_marker: _default_marker | None = None

    # Strip Annotated and Optional wrappers in any order/nesting, e.g. both
    # `Annotated[list[X], nosplit] | None` and `Annotated[list[X] | None, nosplit]`.
    changed = True
    while changed:
        changed = False
        if typing.get_origin(ann) is Annotated:
            base, *meta = typing.get_args(ann)
            for mark in meta:
                if isinstance(mark, suggest):
                    completer = mark
                elif mark is _NOSPLIT:
                    is_nosplit = True
                elif isinstance(mark, PathRequirement):
                    path_req = mark.kind
                elif isinstance(mark, matching):
                    glob = mark.pattern
                elif isinstance(mark, between):
                    bounds = (mark.lo, mark.hi)
                elif isinstance(mark, range):
                    # A bare range: Python's half-open semantics, ints only.
                    bounds = (mark.start, mark.stop - 1)
                elif isinstance(mark, env):
                    env_var = mark.var
                elif isinstance(mark, check):
                    checks = (*checks, mark.fn)
                elif isinstance(mark, doc):
                    doc_text = mark.text
                elif isinstance(mark, _default_marker):
                    default_marker = mark
                elif isinstance(mark, ask):
                    ask_marker = mark
                elif mark is _HIDDEN:
                    is_hidden = True
                elif mark is _FORWARD:
                    is_forward = True
                elif mark is _ARG:
                    is_optional = True
                elif mark is _stdin_marker or isinstance(mark, _stdin_marker):
                    # The bare class and an instance both mark the parameter:
                    # `Annotated[str, stdin]` reads the whole stream,
                    # `stdin("field")` / `stdin(lines=True)` refine it.
                    stdin_marker = (
                        mark if isinstance(mark, _stdin_marker) else _stdin_marker()
                    )
                elif callable(mark) and not isinstance(mark, type):
                    # A bare callable used to mean `suggest(fn)`. One spelling
                    # per concept won: `suggest()` says what it does, and the
                    # guess quietly swallowed anything callable — a plugin's
                    # own marker became a mystery completer with no error
                    # either way. Refused rather than ignored, because this
                    # shape *did* work: silence would break it invisibly.
                    # Unknown metadata that is not callable stays ignored, so
                    # a plugin marker (the house pattern is a non-callable
                    # instance) still rides through untouched.
                    from livery.footman._manifest import SpecError  # circular at import

                    name = getattr(mark, "__name__", type(mark).__name__)
                    raise SpecError(
                        f"Annotated[…, {name}]: a bare callable is not a "
                        f"marker — wrap it to say what it means: "
                        f"suggest({name})"
                    )
            ann, changed = base, True
        elif _is_union(ann):
            members = _strip_none(list(typing.get_args(ann)))
            if len(members) == 1:
                ann, changed = members[0], True

    markers: _Markers = {
        "path_req": path_req,
        "glob": glob,
        "bounds": bounds,
        "env": env_var,
        "checks": checks,
        "doc": doc_text,
        "ask": ask_marker,
        "forward": is_forward,
        "optional": is_optional,
        "stdin": stdin_marker,
        "hidden": is_hidden,
        "default_fn": default_marker,
    }

    if ann is dict or typing.get_origin(ann) is dict:  # dict[K, V] or bare dict
        kv = typing.get_args(ann)
        key_type = kv[0] if kv else str
        value_type = kv[1] if len(kv) > 1 else str
        value = peel(value_type)  # recurse: value may be scalar / union / list
        # A marker on the value type — dict[str, Annotated[int, between(1, 5)]]
        # — applies to each value; an outer marker on the whole dict wins if
        # both are present. (env stays outer-only; env() on a dict is a
        # SpecError.)
        return Peeled(
            False,
            value.element,
            completer,
            is_nosplit,
            mapping=True,
            key=key_type,
            value_multiple=value.multiple,
            path_req=path_req if path_req is not None else value.path_req,
            bounds=bounds if bounds is not None else value.bounds,
            env=env_var,
            checks=(*checks, *value.checks),
            doc=doc_text,
            stdin=stdin_marker,
        )

    if (kind := collection_of(ann)) is not None:  # list/set/frozenset/tuple[T, ...]
        element = (typing.get_args(ann) or (str,))[0]
        if typing.get_origin(element) is Annotated:
            # A marker on the element type — list[Annotated[int, between(1, 5)]]
            # — applies to each element, exactly as the dict branch reads the
            # markers on its value type; an outer marker on the whole
            # collection wins when both are present. The annotated element
            # used to reach the manifest unpeeled, where it read as "not a
            # usable type" and the values passed through as text.
            inner = peel(element)
            if not inner.multiple and not inner.mapping:
                element = inner.element
                completer = completer or inner.completer
                is_nosplit = is_nosplit or inner.nosplit
                if markers["path_req"] is None:
                    markers["path_req"] = inner.path_req
                if markers["glob"] is None:
                    markers["glob"] = inner.glob
                if markers["bounds"] is None:
                    markers["bounds"] = inner.bounds
                markers["checks"] = (*markers["checks"], *inner.checks)
        return Peeled(True, element, completer, is_nosplit, container=kind, **markers)

    if _is_union(ann):
        members = _strip_none(list(typing.get_args(ann)))
        collections = [(m, k) for m in members if (k := collection_of(m)) is not None]
        if collections:  # set[X] | scalar... -> that collection, elements merged
            parts: list[Any] = []
            for member, _ in collections:
                parts += [a for a in typing.get_args(member) if a is not Ellipsis] or [
                    str
                ]
            parts += [m for m in members if collection_of(m) is None]
            return Peeled(
                True,
                _union_of(parts),
                completer,
                is_nosplit,
                container=collections[0][1],
                **markers,
            )
        return Peeled(False, ann, completer, is_nosplit, **markers)  # scalar union

    return Peeled(False, ann, completer, is_nosplit, **markers)  # plain scalar


def emitted(ann: Any) -> tuple[bool, Any]:
    """Whether a *return* annotation carries the `stdout` marker, and the
    type inside it.

    `Stdout[dict | None]` and `Stdout[dict] | None` read identically —
    `Annotated` and `Optional` strip in any order and nesting, the same
    normalisation `peel` applies to parameters. The inner type decides the
    emission: `str` verbatim, `bytes` raw, anything else JSON.
    """
    found = False
    changed = True
    while changed:
        changed = False
        if typing.get_origin(ann) is Annotated:
            base, *meta = typing.get_args(ann)
            if any(m is _STDOUT or isinstance(m, StdoutMarker) for m in meta):
                found = True
            ann, changed = base, True
        elif _is_union(ann):
            members = _strip_none(list(typing.get_args(ann)))
            if len(members) == 1:
                ann, changed = members[0], True
    return found, ann


def emission_mode(inner: Any) -> str:
    """How an emitted value reaches stdout: `text`, `bytes`, or `json`."""
    members = union_members(inner)
    if len(members) == 1:
        if members[0] is str:
            return "text"
        if members[0] is bytes:
            return "bytes"
    return "json"


@dataclass(frozen=True)
class Group:
    """A fixed-arity shape: what to build, from which positions.

    One reading covers four spellings, because `inspect.signature` answers
    identically for all of them — `tuple[X, Y]` from its subscript, a
    `NamedTuple` and a dataclass from their fields, a plain class from its
    `__init__`. They are one case, not four.
    """

    target: Any  # the callable that builds it (`tuple` builds from an iterable)
    names: tuple[str, ...] | None  # field names; None for a bare tuple
    types: tuple[Any, ...]  # the annotation at each position
    required: int  # positions that must be filled
    from_iterable: bool  # `tuple(values)` rather than `T(*values)`

    @property
    def total(self) -> int:
        return len(self.types)

    def build(self, values: list[Any]) -> Any:
        return self.target(values) if self.from_iterable else self.target(*values)

    def label(self) -> str:
        """How the arity reads in an error: `width,height`, or `2 values`.

        The named form is the whole argument for preferring `NamedTuple`:
        a plain tuple can only count, and counting errors read poorly.
        """
        if self.names:
            return ",".join(self.names)
        return f"{self.total} values"


@dataclass(frozen=True)
class Field:
    """One field a record declares: its name, its type, whether it must be
    given."""

    name: str
    type: Any
    required: bool


def fields_of(target: Any) -> tuple[Field, ...] | None:
    """The fields *target* declares, or `None` if it is not a record.

    One answer for the four ways a record is spelled — a dataclass, a
    `NamedTuple`, a `TypedDict`, a class with an annotated `__init__` — read
    three ways: positionally by `group_of` for the command line, by name by
    the stdin binder for a JSON object, and rendered into the manifest so a
    machine can read the shape a pipe expects. They cannot disagree about
    what a record is, which is exactly how a `NamedTuple` once got lost.

    An *untyped* constructor is not a record. `uuid.UUID` takes seven
    optional arguments and describes none of them, so reading it as a shape
    invents a spelling its author never wrote.
    """
    import inspect

    if not isinstance(target, type) or target in (str, bytes):
        return None
    if typing.is_typeddict(target):
        hints = typing.get_type_hints(target)
        # getattr: the checkers narrow `target` to `type` here and lose sight
        # of the TypedDict attributes.
        required: frozenset[str] = getattr(target, "__required_keys__", frozenset())
        return tuple(Field(n, t, n in required) for n, t in hints.items())
    if issubclass(target, enum.Enum):
        return None
    if _dataclasses.is_dataclass(target):
        hints = typing.get_type_hints(target)
        return tuple(
            Field(
                f.name,
                hints.get(f.name, str),
                f.default is _dataclasses.MISSING
                and f.default_factory is _dataclasses.MISSING,
            )
            for f in _dataclasses.fields(target)
        )
    names = getattr(target, "_fields", None)
    if names is not None and issubclass(target, tuple):  # a NamedTuple
        hints = typing.get_type_hints(target)
        defaults = getattr(target, "_field_defaults", {})
        return tuple(Field(n, hints.get(n, str), n not in defaults) for n in names)
    try:
        sig = inspect.signature(target)
        hints = typing.get_type_hints(target.__init__)
    except (TypeError, ValueError, NameError):
        return None
    fields: list[Field] = []
    for param in sig.parameters.values():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            return None  # no declared fields to read
        if param.name not in hints:
            return None  # untyped: see above
        fields.append(
            Field(param.name, hints[param.name], param.default is param.empty)
        )
    return tuple(fields) or None


def _spellable(slot: Any) -> bool:
    """Whether one command-line token can fill a slot of this type.

    A record or a collection cannot say where it ends inside a
    comma-separated group — `--line=1,2,3,4` has no way to mark which pair
    is the start — so a shape holding one has no command-line spelling and
    lives on the document channel, where JSON's own brackets do the saying.

    A *one-field* record stays spellable: that is the `T(value)` behaviour a
    single-parameter constructor has always had.
    """
    if collection_of(slot) is not None:
        return False
    fields = fields_of(slot)
    return fields is None or (len(fields) < 2 and not typing.is_typeddict(slot))


def group_of(ann: Any) -> Group | None:
    """The fixed-arity shape *ann* names, or `None` if it is not one.

    A one-parameter constructor is deliberately not a group: it keeps
    today's `T(value)` behaviour, where the whole token reaches the type.
    That is what makes this non-breaking — only shapes that are a hard
    error today start grouping.
    """
    if typing.get_origin(ann) is tuple:
        args = typing.get_args(ann)
        if not args or (len(args) == 2 and args[1] is Ellipsis):
            return None  # variadic: a list's grammar, handled by `peel`
        if not all(_spellable(a) for a in args):
            return None
        return Group(tuple, None, args, len(args), from_iterable=True)

    if typing.is_typeddict(ann):
        return None  # named keys, not positions: no arity to group by
    fields = fields_of(ann)
    if fields is None or len(fields) < 2:
        return None  # one parameter keeps the whole token, as it does today
    if not all(_spellable(f.type) for f in fields):
        return None  # a slot no single token can fill: the document channel
    return Group(
        ann,
        tuple(f.name for f in fields),
        tuple(f.type for f in fields),
        sum(1 for f in fields if f.required),
        from_iterable=False,
    )


def is_flag(element: Any) -> bool:
    return element is bool


# The basic types a default may stand in for. Ordered because `bool` is a
# subclass of `int` in Python — and excluded outright, since a bool default
# is a flag the splitter resolves before coercion ever runs.
_INFERRED_FROM_DEFAULT = (int, float, str, Path)


def inferred_type(default: Any) -> type | None:
    """The type a bare default stands in for, or `None` to infer nothing.

    A parameter written `port=8000` carries no annotation, but Python's own
    inference — and every type checker footman gates on — reads it as `int`.
    Without this, the command line would hand the body `'99'` while the
    checker had already concluded `int`: the same parameter arriving as two
    types depending on whether it was passed.

    The rule is *infer exactly where the checker infers*, so the cases it
    declines to type are declined here too: `None` (it says
    `Unknown | None`), containers empty or not (`Unknown`), and anything
    exotic. An `Enum` member is excluded despite `IntEnum` passing the
    `int` check — the value would coerce, but to the wrong kind of thing.
    """
    if default is None or isinstance(default, bool | enum.Enum):
        return None
    for basic in _INFERRED_FROM_DEFAULT:
        if isinstance(default, basic):
            return basic
    return None


def sort_tags(tags: list[str]) -> list[str]:
    return sorted(dict.fromkeys(tags), key=lambda t: _TAG_ORDER.get(t, 99))


def element_tags(element: Any) -> list[str]:
    """Scalar coercion tags (specificity-sorted); empty for choice/unknown types."""
    if _is_union(element):
        tags = [
            t for m in _strip_none(list(typing.get_args(element))) if (t := _tag_of(m))
        ]
    else:
        tag = _tag_of(element)
        tags = [tag] if tag else []
    return sort_tags(tags)


_TYPE_PHRASE = {
    "bool": "true or false",
    "int": "an integer",
    "float": "a number",
    "path": "a path",
    "str": "text",
}


def type_phrase(tags: list[str]) -> str:
    """A human phrase for a list of type tags: `['int']` -> "an integer"."""
    return " or ".join(_TYPE_PHRASE.get(t, t) for t in tags)


class EnumContractError(ValueError):
    """An enum declares a command surface the contract refuses.

    Raised wherever the enum is first read — the manifest bake wraps it as
    the taught `ManifestError`; an execution path reaching a broken enum
    without a manifest reports it as the parameter's failure. Refusal over
    precedence, always: every message names the members involved."""


@dataclass(frozen=True)
class EnumFace:
    """One canonical member's contract faces.

    `token` is the one output spelling — the member *name* projected at
    declaration time to the neutral identifier grammar (lowercase ASCII,
    digits, `-`), the same projection parameter names already use. `aliases`
    are Python's duplicate-value bindings, projected the same way: accepted
    as input everywhere, taught nowhere. `value_text` is the value face in
    the argv door's encoding (a string transport stringifies); the document
    door spells the value face in JSON's own types instead."""

    token: str
    value_text: str
    aliases: tuple[str, ...]
    member: enum.Enum


def enum_token(name: str) -> str:
    """The declaration-time projection: `LOW_PRIORITY` → `low-priority`.

    Folding happens here and only here — no runtime folding algorithm,
    table, or Unicode dependency exists in any binder or on any wire. A
    name that projects outside the grammar is refused at declaration, so
    the Turkish-İ class of divergence is excluded by construction."""
    token = name.lower().replace("_", "-")
    if not token or not all(c.isascii() and (c.isalnum() or c == "-") for c in token):
        raise EnumContractError(
            f"enum member {name!r} projects to {token!r}, outside the "
            f"contract's token grammar (lowercase ASCII letters, digits, "
            f"'-') — rename the member"
        )
    return token


def enum_faces(element: type[enum.Enum]) -> list[EnumFace]:
    """Every canonical member's faces, with the declaration-time refusals.

    Python's alias collapse is adopted verbatim: iteration yields canonical
    members (first binding wins), `__members__` carries the synonyms. Two
    refusals guard the contract: two canonical members projecting to one
    token (`LOW` and `Low`), and any cross-member intersection of face
    spellings on any door. The argv set — token, aliases, stringified
    value — is checked pairwise; the document door's string faces are a
    subset of it (duplicate values already collapsed to aliases, and its
    value face is JSON-typed), so the argv check covers every door."""
    alias_names: dict[enum.Enum, list[str]] = {}
    for name, member in element.__members__.items():
        if name != member.name:  # a duplicate-value binding: a synonym
            alias_names.setdefault(member, []).append(name)
    faces: list[EnumFace] = []
    seen_tokens: dict[str, str] = {}
    for member in element:
        token = enum_token(member.name)
        if token in seen_tokens:
            raise EnumContractError(
                f"enum members {seen_tokens[token]!r} and {member.name!r} "
                f"both project to token {token!r} — rename one"
            )
        seen_tokens[token] = member.name
        faces.append(
            EnumFace(
                token=token,
                value_text=str(member.value),
                aliases=tuple(enum_token(a) for a in alias_names.get(member, ())),
                member=member,
            )
        )
    spellings = [(f, {f.token, *f.aliases, f.value_text}) for f in faces]
    for i, (a, a_set) in enumerate(spellings):
        for b, b_set in spellings[i + 1 :]:
            clash = a_set & b_set
            if clash:
                raise EnumContractError(
                    f"enum members {a.member.name!r} and {b.member.name!r} "
                    f"spell {sorted(clash)!r} ambiguously on the command "
                    f"line — the contract refuses rather than picking"
                )
    return faces


def token_of(member: enum.Enum) -> str:
    """The member's one output spelling. An alias-accessed member has no
    independent identity (`Level.MINIMAL is Level.LOW`), so `.name` is
    always the canonical binding and the projection lands on its token."""
    return enum_token(member.name)


def enum_member_for(element: type[enum.Enum], text: str) -> enum.Enum | None:
    """The member *text* names on a string door: token, alias, or value
    face — the enumerated input set, identical at every door. A raw member
    name (`LOW`) is deliberately not a fourth spelling."""
    for face in enum_faces(element):
        if text == face.token or text in face.aliases or text == face.value_text:
            return face.member
    return None


def element_choices(
    element: Any,
) -> tuple[list[str] | None, type[enum.Enum] | None, tuple[Any, ...] | None]:
    """(choices as strings, Enum class, Literal values) for a choice element.

    Choices speak tokens: a `Literal`'s tokens are its values (the author
    chose the values as the meaning), an `Enum`'s tokens are its projected
    names (the author named the values). `Level.LOW = 1` therefore shows
    `low|high`, never the `1|2` that read as payload."""
    if typing.get_origin(element) is typing.Literal:
        values = typing.get_args(element)
        return [str(v) for v in values], None, values
    if isinstance(element, type) and issubclass(element, enum.Enum):
        return [f.token for f in enum_faces(element)], element, None
    return None, None, None


def enum_accepts(element: Any) -> list[str]:
    """The accepted input spellings *beyond* the choices, across a union:
    aliases and value faces. Baked into the manifest so every frontend's
    eager gate accepts the same set by contract — authored and enumerated,
    not leniency."""
    extra: list[str] = []
    for member in union_members(element):
        if isinstance(member, type) and issubclass(member, enum.Enum):
            for face in enum_faces(member):
                for spelling in (*face.aliases, face.value_text):
                    if spelling != face.token and spelling not in extra:
                        extra.append(spelling)
    return extra


def all_choices(element: Any) -> list[str] | None:
    """Choice strings gathered across a union's Literal/Enum members (or a
    scalar Literal/Enum); `None` if no member contributes choices."""
    out: list[str] = []
    for member in union_members(element):
        member_choices, _, _ = element_choices(member)
        if member_choices:
            out.extend(member_choices)
    return out or None


def eagerly_checkable(element: Any) -> bool:
    """Whether every union member is taggable (bool/int/float/path/str) or a
    Literal/Enum — so the splitter can accept/reject a value up front. A member
    like `UUID` or `Any` is not eagerly checkable; only binding can coerce it."""
    for member in union_members(element):
        if _tag_of(member) is not None:
            continue
        member_choices, _, _ = element_choices(member)
        if member_choices is not None:
            continue
        return False
    return True


def coerce_scalar(value: str, tags: list[str]) -> tuple[bool, Any]:
    """Try to coerce *value* to one of *tags* in specificity order."""
    for tag in sort_tags(tags):
        if tag == "bool":
            if value.lower() in _BOOL_TOKENS:
                return True, _BOOL_TOKENS[value.lower()]
        elif tag == "int":
            # `isascii` guards the gap between `str.isdigit` and `int()`:
            # "²".isdigit() is true but int("²") raises.
            digits = value[1:] if value[:1] in "+-" else value
            if digits.isdigit() and digits.isascii():
                return True, int(value)
        elif tag == "float":
            try:
                return True, float(value)
            except ValueError:
                pass
        elif tag == "path":
            return True, Path(value)
        elif tag == "str":
            return True, value
    return False, None


def coerce_one(value: str, element: Any) -> Any:
    """Coerce a single token to its annotated element type (best effort)."""
    _, enum_cls, literal = element_choices(element)
    if enum_cls is not None:
        # The three-face input set — token, alias, value face — identical
        # at every door. A raw member NAME (`LOW`) is deliberately not a
        # fourth spelling: the name's one legal form is its token.
        member = enum_member_for(enum_cls, value)
        if member is not None:
            return member
        return enum_cls(value)
    if literal is not None:
        for lit in literal:
            if str(lit) == value:
                return lit
        return value
    if _is_union(element):
        return _coerce_union(value, element)
    tags = element_tags(element)
    if tags:
        ok, out = coerce_scalar(value, tags)
        return out if ok else value
    return coerce_custom(value, element)


def coerce_checked(value: str, element: Any) -> Any:
    """Coerce a token, refusing rather than falling back to the raw string.

    `coerce_one` is best-effort on purpose: on the ordinary path the
    splitter has already validated the token eagerly, so its fall-through
    is unreachable. A grouped position has no such pre-check — the
    splitter validates a parameter against one type, and a group's
    positions have one each — so it needs the strict form, or a
    `tuple[str, int]` would quietly accept `height='tall'`.
    """
    _choices, enum_cls, literal = element_choices(element)
    if enum_cls is not None:
        return coerce_one(value, element)  # `enum_cls(value)` refuses its own
    if literal is not None:
        if not any(str(lit) == value for lit in literal):
            spelled = "|".join(str(lit) for lit in literal)
            raise ValueError(f"must be one of {spelled} (got {value!r})")
        return coerce_one(value, element)
    tags = element_tags(element)
    if tags:
        ok, out = coerce_scalar(value, tags)
        if not ok:
            raise ValueError(f"expects {type_phrase(tags)} (got {value!r})")
        return out
    return coerce_custom(value, element)


def _coerce_union(value: str, element: Any) -> Any:
    """Coerce a token to the best-matching member of a union (best effort).

    Order: an exact Literal/Enum member (so `Literal[5] | str` yields the int
    5, not "5"), then scalar tags in specificity order, then any custom-type
    member's constructor (so `UUID | int` binds a real UUID); falls back to the
    raw string when nothing matches.
    """
    members = union_members(element)
    for member in members:
        _, enum_cls, literal = element_choices(member)
        if enum_cls is not None:
            for m in enum_cls:
                if str(m.value) == value or m.name == value:
                    return m
        elif literal is not None:
            for lit in literal:
                if str(lit) == value:
                    return lit
    tags = element_tags(element)
    if tags:
        ok, out = coerce_scalar(value, tags)
        if ok:
            return out
    for member in members:
        if (
            isinstance(member, type)
            and _tag_of(member) is None
            and not issubclass(member, enum.Enum)
        ):
            try:
                return coerce_custom(value, member)
            except ValueError:
                continue
    return value


def coerce_token(value: str, element: Any) -> Any:
    """Strict `coerce_one` for a token the splitter never validated — an env
    fallback or a `--` passthrough value.

    Raises `ValueError` when a purely tag-typed element cannot parse the token
    (e.g. `JOBS=abc` for an `int`), rather than passing the raw string through
    the way `coerce_one` does for CLI tokens the splitter already validated.
    Choice and
    custom-type membership are left to `coerce_one` (and the caller's own
    choices check), so union values keep working.
    """
    if element_tags(element) and all_choices(element) is None:
        tags = element_tags(element)
        ok, out = coerce_scalar(value, tags)
        if not ok:
            raise ValueError(f"expects {type_phrase(tags)} (got {value!r})")
        return out
    return coerce_one(value, element)


def coerce_custom(value: str, element: Any) -> Any:
    """Coerce to a type footman doesn't special-case, via its constructor.

    Covers `UUID`, `Decimal`, and any user type whose constructor accepts a
    string; `datetime`/`date` use `fromisoformat`. Validated here at
    execution time (the splitter only ever sees strings), and raises
    `ValueError` on a bad value so footman can report it cleanly.
    """
    # `Any`/`object` deliberately mean "take the raw string": both are classes
    # on Python >=3.11, so without this guard `Any("x")` would raise.
    if element is Any or element is object or not isinstance(element, type):
        return value
    if element is bytes:
        # A CLI token for a bytes parameter is its UTF-8 encoding — the raw
        # form arrives via the stdin boundary, where bytes stay bytes.
        return value.encode()
    try:
        if issubclass(element, _datetime.datetime):
            return element.fromisoformat(value)
        if issubclass(element, _datetime.date):
            return element.fromisoformat(value)
        fields = fields_of(element)
        if fields is not None and len(fields) == 1:
            # A one-field record spells as `T(value)` — but its field
            # *declares a type*, and a dataclass constructor validates
            # nothing, so `n: int` silently received the string 'abc' (and
            # '5' stayed a string on the happy path). The token must first
            # be the field's value. Untyped constructors (UUID, Decimal, a
            # user type taking a string) expose no readable fields, so they
            # keep the raw token exactly as before.
            record: Any = element  # the call is deliberately dynamic, as below
            ftype = fields[0].type
            tags = element_tags(ftype)
            if tags:
                ok, out = coerce_scalar(value, tags)
                if not ok:
                    raise ValueError(
                        f"field {fields[0].name!r} takes {tags[0]}, got {value!r}"
                    )
                return record(out)
            return record(coerce_one(value, ftype))
        # A user constructor: its call signature is its own business (the
        # contract is "accepts a string"), so the call is deliberately dynamic.
        ctor: Any = element
        return ctor(value)
    except Exception as exc:
        # Not just ValueError/TypeError: `Decimal("abc")` raises
        # `decimal.InvalidOperation` (an ArithmeticError), and a user type may
        # reject a bad token however it likes. The value came from a command
        # line, so a constructor refusing it is a bad *value*, and it reports
        # as one — the original exception rides the chain for `-v`.
        raise ValueError(f"{value!r} is not a valid {element.__name__}") from exc
