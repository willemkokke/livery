"""Manifest generation and caching — the "cold" path.

The manifest is a JSON description of the command tree (groups, tasks, and the
CLI shape of every parameter). The execution path imports the user's tasks
module anyway, so introspecting the tree and rewriting the cache is effectively
free. The completion hot path (`footman._complete`) only ever *reads* the
cached JSON — it never imports this module or the user's code.

Parameter mapping (function signature -> CLI shape):

| Signature                | CLI shape                                 |
| ------------------------ | ----------------------------------------- |
| `fix: bool = False`      | flag `--fix` / `--no-fix`                 |
| `mode: str = "loose"`    | option `--mode=VALUE`, bare `--mode` ok   |
| `env: Literal[...]`      | completable, eagerly-validated choices    |
| `count: int = 100`       | typed option, validated at parse time     |
| `paths: list[Path] = ()` | repeatable option (`--paths a --paths b`) |
| `template: Path`         | required positional (exact arity)         |
| `*cmd: str`              | variadic trailing passthrough             |
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime
import decimal
import enum
import hashlib
import inspect
import json
import os
import sys
import typing
import uuid
from pathlib import Path, PurePath
from typing import Any

from livery.footman import _binder, _coerce, _describe, _discover, _paths, registry
from livery.footman.context import context_param_name
from livery.footman.params import suggest
from livery.footman.registry import Group

SCHEMA_VERSION = 11

_warned: set[str] = set()


def _warn(message: str) -> None:
    """An advisory on stderr, once per message — never a Python UserWarning.

    `warnings.warn` dressed these as `…/_manifest.py:416: UserWarning:` with
    footman's own source line quoted underneath — internals nobody asked
    about, above every `--help` and `--list`, pointing at the framework
    instead of the user's file the message already names. The message is
    the whole story; it is delivered as one line, once, like every other
    advisory footman prints.
    """
    if message in _warned:
        return
    _warned.add(message)
    print(message, file=sys.stderr)


class ManifestError(Exception):
    """A tasks file describes a command surface footman cannot honour.

    Raised at manifest-build time (the execution path) with a taught message;
    the app layer reports it and exits 2.
    """


class CompleterError(ManifestError):
    """A strict dynamic completer failed while refreshing its choices.

    Raised so a broken completer surfaces as a taught error instead of
    silently baking an empty choice list — which would disable the very
    validation `strict=True` promises.
    """


class SpecError(ManifestError):
    """A parameter's markers are inconsistent (e.g. `env()` with no default)."""


_SIGNATURE = "_footman_signature"
_CALL_SIGNATURE = "_footman_call_signature"


def _memo(fn: Any, attr: str) -> tuple[Any, inspect.Signature | None]:
    """The body to stamp a signature memo on, and the memo already there.

    Keyed on the **body**, through `task_body`, so a handle, an `.opts()`
    reference and the bare function share one answer — they share one
    signature by construction (`__wrapped__` runs the whole way down). A
    callable that refuses attributes (a builtin) simply never memoises.
    """
    body = registry.task_body(fn)
    return body, getattr(body, attr, None)


def resolved_signature(fn: Any) -> inspect.Signature:
    """Signature of *fn* with string annotations evaluated to real types.

    `from __future__ import annotations` (and any PEP 563 usage) turns a
    tasks file's annotations into strings; `eval_str` turns them back into the
    types the grammar reasons about. `eval_str` is all-or-nothing — one name
    that cannot resolve raises for the whole signature — so on failure each
    annotation is evaluated on its own: one broken parameter degrades to
    pass-through text, and the rest keep their types, choices and completion.

    Memoised on the function, the `_footman_*` house pattern: the answer is a
    pure function of a function object whose annotations are fixed by the
    time anything asks (discovery imports a module whole before a task in it
    can run), and the ask is *hot* — every execution path asks about a task
    around eight times, at ~68 µs a call on a string-annotated file, and the
    manifest asks once more per task. The memo dies with the function it is
    stamped on, and discovery's per-file isolation mints fresh functions on
    every load, so it cannot outlive what it describes.
    """
    body, cached = _memo(fn, _SIGNATURE)
    if cached is not None:
        return cached
    try:
        sig = inspect.signature(fn, eval_str=True)
    except (NameError, TypeError, AttributeError):
        sig = _partially_resolved(fn)
    with contextlib.suppress(AttributeError, TypeError):
        setattr(body, _SIGNATURE, sig)
    return sig


def _partially_resolved(fn: Any) -> inspect.Signature:
    """The raw signature with every annotation that *can* resolve, resolved.

    Evaluates each string annotation against the function's module globals —
    the same environment `eval_str` uses — and leaves only the failing ones
    as strings, warning once per broken parameter with the underlying error.
    (`warnings` dedups an identical message per process, so the repeated
    `resolved_signature` calls of one invocation say this once.)
    """
    sig = inspect.signature(fn)
    module_globals = getattr(fn, "__globals__", {})
    where = f"{getattr(fn, '__module__', '?')}.{getattr(fn, '__qualname__', fn)}"

    def resolve(name: str, annotation: Any) -> Any:
        if not isinstance(annotation, str):
            return annotation
        try:
            # What `eval_str` does per annotation, minus the all-or-nothing.
            return eval(annotation, module_globals)
        except Exception as exc:  # any failure means "not a usable type"
            _warn(
                f"footman: {where} <{name}>: annotation {annotation!r} did "
                f"not resolve ({exc}); values pass through as text"
            )
            return annotation

    params = [
        p.replace(annotation=resolve(p.name, p.annotation))
        for p in sig.parameters.values()
    ]
    return sig.replace(
        parameters=params,
        return_annotation=resolve("return", sig.return_annotation),
    )


def call_signature(fn: Any) -> inspect.Signature:
    """The signature a Python caller binds against: the declared one minus the
    context parameter. A body call never passes `ctx` — `run_bound` injects it
    at the task boundary — so binding a call's arguments against the declared
    signature would land the first positional value in the `ctx` slot.

    Memoised beside `resolved_signature`, for the same reason and with the
    same lifetime: `given()` and the futures memo ask per call, not per run.
    """
    body, cached = _memo(fn, _CALL_SIGNATURE)
    if cached is not None:
        return cached
    sig = resolved_signature(fn)
    name = context_param_name(sig)
    if name is not None:
        sig = sig.replace(
            parameters=[p for p in sig.parameters.values() if p.name != name]
        )
    with contextlib.suppress(AttributeError, TypeError):
        setattr(body, _CALL_SIGNATURE, sig)
    return sig


def _unique_globals(root: Group) -> list[Any]:
    """The tree's plugin globals, deduped by identity, contribution order.

    The same singleton mounted through two routes is one option; a name clash
    between two different singletons was refused at discovery, before the
    manifest could bake either."""
    seen: list[Any] = []
    for opt in root.contributions.get("globals", ()):
        if not any(o is opt for o in seen):
            seen.append(opt)
    return seen


def _global_spec(
    opt: Any, memo: dict[int, list[str]], *, bake: bool = False
) -> dict[str, Any]:
    """One manifest entry for a plugin's global option — described by the
    same machinery as a task parameter, so choices, path-typed file
    completion and `suggest()` come along by construction."""
    # The option builds its own synthetic parameter — one construction,
    # shared with the binder's absent-option ladder. (A bool default is
    # already normalised at declaration, by GlobalOption itself.)
    spec = _finish(param_spec(opt._as_parameter()), memo, bake=bake)
    spec["name"] = opt.name  # the cli spelling is the identity
    if opt.help:
        spec["help"] = opt.help
    spec["owner"] = opt.owner
    return spec


def param_spec(param: inspect.Parameter) -> dict[str, Any]:
    """Map one function parameter to its CLI shape (one manifest entry).

    Dynamic-completer params get a transient `_completer` key that
    `_finish` replaces with the completer's (cached) choices.
    """
    spec: dict[str, Any] = {"name": registry.cli_name(param.name)}
    ann = param.annotation
    empty = inspect.Parameter.empty

    if param.kind is inspect.Parameter.VAR_KEYWORD:
        raise SpecError(
            f"**{param.name} is not supported — declare named parameters, or "
            f"accept KEY=VALUE pairs with a dict[str, str] parameter"
        )

    if param.kind is inspect.Parameter.VAR_POSITIONAL:
        spec["kind"] = "variadic"
        if ann is not empty:
            peeled = _coerce.peel(ann)  # unwrap Annotated so markers reach the spec
            tags = _coerce.element_tags(peeled.element)
            if tags and tags != ["str"]:
                spec["types"] = tags
            _marker_keys(spec, peeled, param, has_default=False)
        return spec

    has_default = param.default is not empty
    if has_default:
        # Bake the default into the manifest when it survives the JSON
        # coercion mirror (Path → str, Enum → value, …) — an additive key for
        # help, the catalog, and the markdown exporter. An exotic default is
        # simply omitted, never an error.
        ok_default, encoded = _describe.jsonable(param.default)
        if ok_default:
            spec["default"] = encoded
    # A keyword-only parameter (after `*` or `*args`) is an option by
    # Python's own declaration — defaultless, it is a *required* option,
    # the same shape defaultless dicts and flags already take.
    kw_only = param.kind is inspect.Parameter.KEYWORD_ONLY

    if ann is empty:
        if isinstance(param.default, bool):
            spec["kind"] = "flag"
        elif has_default or kw_only:
            spec["kind"] = "option"
            if not has_default:
                spec["required"] = True
        else:
            spec["kind"] = "positional"
        # A basic default types the parameter as surely as an annotation
        # would — `port=8000` is an int to Python and to every checker, so
        # it is an int here. Recording the tags is what buys the eager
        # refusal, the catalog's `types`, and the coercion at bind time;
        # `str` is omitted like any other str-tagged spec, since it is the
        # shape a bare command-line value already has.
        inferred = _coerce.inferred_type(param.default)
        if inferred is not None and (tags := _coerce.element_tags(inferred)) != ["str"]:
            spec["types"] = tags
        return spec

    peeled = _coerce.peel(ann)
    if peeled.mapping:
        # A dict is always an option (--name KEY=VALUE); when it has no default
        # it is a *required* option — footman has no positional-mapping syntax.
        spec["kind"] = "option"
        spec["mapping"] = True
        if not has_default:
            spec["required"] = True
        _marker_keys(spec, peeled, param, has_default)
        if peeled.nosplit:
            spec["nosplit"] = True
        if (ktags := _coerce.element_tags(peeled.key)) and ktags != ["str"]:
            spec["key_types"] = ktags
        vchoices = _coerce.all_choices(peeled.element)
        vtags = _coerce.element_tags(peeled.element)
        if vchoices is not None:
            spec["value_choices"] = vchoices
        if vtags and vtags != ["str"] and _coerce.eagerly_checkable(peeled.element):
            spec["value_types"] = vtags
        return spec

    element = peeled.element
    reads_document = (
        peeled.stdin is not None
        and peeled.stdin.field is None
        and not peeled.stdin.lines
        and element is not bytes
    )
    record = _coerce.fields_of(element) if reads_document else None
    if record is not None:
        # What a machine needs to build the JSON this parameter expects: the
        # shape's name, its fields, each field's coercion tags and whether it
        # must be given. A name alone — which is all this used to carry — told
        # a reader the pipe wanted a `Config` and nothing whatever about what
        # a `Config` is.
        spec["shape"] = _shape_spec(element)
    if record is not None and not peeled.multiple and _coerce.group_of(element) is None:
        # No command-line spelling, so no token spelling: a shape with a
        # nested record or a collection in it cannot say where a slot ends
        # inside a comma-separated group, and a TypedDict has named keys
        # rather than positions. The splitter and completion key on the known
        # kinds, so `"stdin"` is invisible to them by construction; help still
        # lists it, with the shape it binds.
        #
        # A shape that *does* group keeps its `--opt=a,b` spelling and reads
        # the pipe too — the command line wins when both are given.
        spec["kind"] = "stdin"
        _marker_keys(spec, peeled, param, has_default)
        return spec

    if _coerce.is_flag(element) and not peeled.multiple:
        # Only a *scalar* bool is a --flag; `list[bool]` stays a repeatable
        # option whose tokens parse as booleans (true/false/1/0/yes/no/on/off).
        spec["kind"] = "flag"
        # ask() prompts if absent; stdin fills at the boundary — either one
        # makes a defaultless flag satisfiable without the command line.
        if not has_default and peeled.ask is None and peeled.stdin is None:
            spec["required"] = True  # else state it explicitly: --x or --no-x
        _marker_keys(spec, peeled, param, has_default)
        return spec

    if peeled.optional:
        # Arg[T]: an optional trailing positional — greedy for one token when
        # present, running on the default when absent (`+` says "absent, next
        # task"). Deterministic by construction: no name-peeking, cap one.
        if not has_default:
            raise SpecError(
                f"<{param.name}>: Arg[…] needs a default — an optional "
                f"positional's absence must mean something"
            )
        if peeled.multiple:
            raise SpecError(
                f"<{param.name}>: Arg[…] takes at most one token; a "
                f"many-valued positional is already optionalable as "
                f"`Many[…]` with a default"
            )
        spec["kind"] = "positional"
        spec["optional"] = True
    elif (peeled.ask is not None or peeled.stdin is not None) and not has_default:
        # ask() and stdin both make a defaultless parameter a CLI-optional
        # option: absence is filled at the boundary (a prompt, the piped
        # payload) or refused with a taught message there, so the splitter
        # must let it be missing rather than enforce it as a required
        # positional.
        spec["kind"] = "option"
    elif has_default or kw_only:
        spec["kind"] = "option"
        if not has_default:
            spec["required"] = True
    else:
        spec["kind"] = "positional"
    _marker_keys(spec, peeled, param, has_default)
    # A fixed-arity shape accumulates values exactly as a collection does —
    # commas and repetition both feed one stream — and the declared arity
    # groups that stream. So it is `multiple` to the splitter, plus a
    # `group` the splitter and `--help` read for arity and per-position
    # types. The class itself is not in the manifest (it is not JSON); the
    # executor reads it back off the annotation when it builds.
    group = _coerce.group_of(peeled.element)
    if group is not None:
        spec["multiple"] = True
        spec["group"] = {
            "names": list(group.names) if group.names else None,
            "types": [_coerce.element_tags(t) for t in group.types],
            "min": group.required,
            "max": group.total,
            "many": peeled.multiple,
            "label": group.label(),
        }
        if peeled.nosplit:
            spec["nosplit"] = True
        return spec
    if peeled.multiple:
        spec["multiple"] = True
        if peeled.nosplit:
            spec["nosplit"] = True
    if peeled.completer is not None:
        # No `choices` key at all unless someone bakes one: *absent* means
        # "nobody has asked yet", which is what lets validation and help
        # resolve live, while a baked `[]` keeps its old meaning — the
        # completer ran and genuinely offered nothing.
        spec["dynamic"] = {"strict": peeled.completer.strict}
        spec["_completer"] = peeled.completer
        return spec

    try:
        choices = _coerce.all_choices(element)
        accepts = _coerce.enum_accepts(element) if choices is not None else []
    except _coerce.EnumContractError as exc:
        # Declaration-time refusal, loudly, through the taught door: the
        # enum spells an ambiguous or unprojectable surface, and refusing
        # here — where the contract is first read — is what keeps every
        # later door free of precedence rules.
        raise ManifestError(f"parameter {param.name!r}: {exc}") from exc
    tags = _coerce.element_tags(element)
    if choices is not None:
        spec["choices"] = choices
        # The accepted synonym set beyond the choices — an enum's aliases
        # and value faces. Baked, so every frontend's eager gate accepts
        # the same inputs by contract: authored and enumerated, never
        # leniency. Completion and help read `choices` alone; a synonym is
        # accepted, not taught.
        if accepts:
            spec["accepts"] = accepts
    # Emit `types` only when the element is eagerly checkable — a union with a
    # custom member (`UUID | int`) can't be accept/rejected up front, so leave
    # it to binding rather than eagerly rejecting valid values.
    if tags and tags != ["str"] and _coerce.eagerly_checkable(element):
        spec["types"] = tags
    elif choices is None and not tags and not isinstance(element, type):
        if isinstance(element, str) and "stdin" in element:
            # `eval_str` failed for the whole signature, so the marker never
            # even peeled — but the annotation names it, and a silently
            # text-degraded stdin parameter would be a mystery at bind time.
            raise SpecError(
                f"<{param.name}>: annotation {element!r} did not resolve — "
                f"a stdin payload type and everything it names must be "
                f"module-level, where `eval_str` can see them"
            )
        # The annotation resolves to nothing footman can coerce (a value, an
        # exotic generic): values will pass through as plain text. Silent
        # degrade is a debugging tax — say so. A *string* landing here was
        # already reported by `_partially_resolved`, which knows the task and
        # the underlying error; repeating it per spec build would turn one
        # broken name back into a warning block.
        if not isinstance(element, str):
            _warn(
                f"footman: parameter {param.name!r}: annotation {element!r} "
                f"is not a usable type; values are passed through as text"
            )
    return spec


def _shape_spec(target: Any, seen: tuple[Any, ...] = ()) -> dict[str, Any]:
    """The JSON a document parameter expects, as data a machine can act on:
    the shape's name and, for each field, its name, its type, whether it must
    be given, and — when the field is itself a record — that field's shape in
    turn.

    A field with no `types`, `choices` or `shape` is one footman does not
    coerce: whatever JSON holds arrives as it is. That is a description, not
    an omission.

    A shape already on the way down is emitted by name alone. A
    self-referential record has no finite expansion, and every shape appears
    in full somewhere above, so the name resolves.
    """
    spec: dict[str, Any] = {"name": getattr(target, "__name__", str(target))}
    if target in seen:
        return spec  # recursive: named above, expanded there
    fields = _coerce.fields_of(target)
    if fields is None:
        return spec
    spec["fields"] = [_field_spec(f, (*seen, target)) for f in fields]
    return spec


def _field_spec(field: _coerce.Field, seen: tuple[Any, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {"name": field.name}
    inner = field.type
    if (collection := _coerce.collection_of(inner)) is not None:
        inner = (typing.get_args(inner) or (str,))[0]
        out["many"] = collection.__name__
    if _coerce.fields_of(inner) is not None:
        out["shape"] = _shape_spec(inner, seen)
    else:
        if (choices := _coerce.all_choices(inner)) is not None:
            out["choices"] = choices
        if tags := _coerce.element_tags(inner):
            out["types"] = tags
    if field.required:
        out["required"] = True
    return out


def _marker_keys(
    spec: dict[str, Any],
    peeled: _coerce.Peeled,
    param: inspect.Parameter,
    has_default: bool,
) -> None:
    """Additive manifest keys for the `Annotated` markers (path/bounds/env/doc).

    `check(fn)` deliberately never lands in the manifest — functions don't
    serialize (the same reason `_finish` strips `_completer`); it runs at
    binding time instead.
    """
    if peeled.hidden:
        # Marked, never missing — the same rule a hidden *task* follows. A
        # machine reading the manifest is exactly who is meant to find it;
        # only the listing a human reads leaves it out.
        spec["hidden"] = True
    if peeled.ask is not None and peeled.ask.secret:
        # A secret parameter never publishes values: no baked choices, no
        # completer run — the flag completes, its value stays yours.
        spec["secret"] = True
    if peeled.doc is not None:
        spec["doc"] = peeled.doc
    if peeled.path_req is not None:
        spec["path"] = peeled.path_req
    if peeled.glob is not None:
        # Completion only: the pattern the shell's own file completion filters
        # by. Never a validation rule — see `matching`.
        spec["glob"] = peeled.glob
    if peeled.bounds is not None:
        lo, hi = peeled.bounds
        if lo is not None:
            spec["min"] = lo
        if hi is not None:
            spec["max"] = hi
    if peeled.default_fn is not None:
        if not has_default:
            raise SpecError(
                f"<{param.name}>: default(…) needs a declared default to sit "
                f"on — a plain Python call of the task, with no run around it, "
                f"has nothing else to fall back to"
            )
        # Called here, on the execution path, so `--help` prints what this run
        # would actually use rather than a value baked whenever a cache was
        # last built. A raising computer is left to raise at bind time instead
        # of taking the whole tree's help down with it.
        spec["computed"] = True  # help says so: a bare number reads arbitrary
        if peeled.default_fn.reads_siblings:
            # It reads values only an invocation has, so there is nothing to
            # show — and the *declared* default must go too: it exists so a
            # plain Python call still binds, and printing that sentinel would
            # advertise the very thing this marker replaces.
            spec.pop("default", None)
        else:
            ok_computed, computed = False, None
            with contextlib.suppress(Exception):  # help degrades; the run teaches
                ok_computed, computed = _describe.jsonable(peeled.default_fn.fn())
            if ok_computed:
                spec["default"] = computed
    if peeled.env is not None:
        if spec.get("mapping"):
            raise SpecError(
                f"<{param.name}>: env() is not supported on dict parameters"
            )
        if not has_default:
            raise SpecError(
                f"<{param.name}>: env({peeled.env!r}) needs a default — an "
                f"env fallback makes the parameter optional, so it needs "
                f"somewhere to fall"
            )
        spec["env"] = peeled.env
    if peeled.stdin is not None:
        marker = peeled.stdin
        if spec.get("mapping") and (marker.field is not None or marker.lines):
            raise SpecError(
                f"<{param.name}>: a dict parameter reads stdin whole (a JSON "
                f"object) — stdin(field)/stdin(lines=True) do not apply"
            )
        if marker.lines and not peeled.multiple:
            raise SpecError(
                f"<{param.name}>: stdin(lines=True) needs a list parameter — "
                f"each line binds as one element"
            )
        if marker.field is not None and peeled.multiple:
            raise SpecError(
                f"<{param.name}>: stdin({marker.field!r}) binds a single "
                f"value — a list parameter reads lines (stdin(lines=True)) "
                f"or a JSON array (bare stdin)"
            )
        if isinstance(peeled.element, str):
            raise SpecError(
                f"<{param.name}>: annotation {peeled.element!r} did not "
                f"resolve — a stdin payload type and everything it names "
                f"must be module-level, where `eval_str` can see them"
            )
        if marker.field is not None:
            spec["stdin"] = f"field:{marker.field}"
        elif marker.lines:
            spec["stdin"] = "lines"
        elif peeled.element is bytes:
            spec["stdin"] = "bytes"
        elif (
            spec.get("mapping")
            or peeled.multiple
            or spec.get("kind") == "stdin"
            or _binder.is_document_target(peeled.element)
        ):
            spec["stdin"] = "json"
        else:
            spec["stdin"] = "text"


class _Undescribable(Exception):
    """Internal: a return annotation outside the describable set. Never a user
    error — "describable" ⊆ "returnable", so the caller degrades to no spec."""


def returned_spec(ann: Any) -> dict[str, Any] | None:
    """The output spec a return annotation declares, or None for no claims.

    The mirror of `param_spec` for the *output* side: the annotation is the
    declaration, and the describable set is exactly what
    `_describe.json_default` serialises — dataclasses (nested), TypedDict,
    NamedTuple, `list`/`tuple[T, ...]`/`set`/`dict[str, …]`, the scalar
    bridge types, `Literal`/`Enum` as choices, `T | None` as nullable. The
    spec is footman's own compact shape (one vocabulary with the param
    specs); `_describe.returns_json_schema` renders it as JSON Schema at the
    describe door.

    An annotation outside the set — a broken string, a wider union, an
    exotic generic — yields `None`, never an error: the task still runs and
    its value still serialises (or refuses) at runtime exactly as before, it
    just makes no claims. Bare `int` is the exit-code channel, not data, so
    it too declares nothing; `Stdout[T]` describes `T` — the document and
    the returned value are one declaration.
    """
    if ann is inspect.Parameter.empty or ann is None or ann is type(None):
        return None
    # `emitted` fully normalises (Annotated *and* Optional stripped) — the
    # right view for the bare-int test, where `int | None` is still the
    # exit-code channel. The walk below gets the *original* annotation:
    # it strips Annotated itself, so `Report | None` keeps its nullability.
    _, flat = _coerce.emitted(ann)
    if flat is int or flat is Any or flat is object:
        return None  # exit-code channel / no claims — a schema would lie
    try:
        return _returned_of(ann, ())
    except _Undescribable:
        return None


_RETURN_SCALARS: list[tuple[type, str]] = [
    # Order matters where the types nest: datetime before date (a datetime
    # *is* a date), bool before int (checked via `is`, so moot, but kept
    # explicit in `_returned_of` below).
    (datetime.datetime, "datetime"),
    (datetime.date, "date"),
    (datetime.time, "time"),
    (uuid.UUID, "uuid"),
    (decimal.Decimal, "decimal"),
    (PurePath, "path"),
]


def _returned_of(ann: Any, seen: tuple[Any, ...]) -> dict[str, Any]:
    """One node of the output spec — recursive, raising on the undescribable."""
    while typing.get_origin(ann) is typing.Annotated:
        # Markers (`Stdout`, a doc) never change the shape; unions survive.
        ann = typing.get_args(ann)[0]
    if ann is Any or ann is object:
        return {"kind": "any"}  # a real "no claims here" inside a container
    if ann is type(None):
        return {"kind": "none"}
    if _coerce._is_union(ann):
        # T | None is nullable; a wider union has no json_default story to
        # mirror, so it makes no claims at all rather than partial ones.
        members = _coerce.union_members(ann)
        if len(members) != 1:
            raise _Undescribable
        spec = _returned_of(members[0], seen)
        spec["nullable"] = True
        return spec
    if typing.get_origin(ann) is typing.Literal:
        values: list[Any] = []
        nullable = False
        for value in typing.get_args(ann):
            if value is None:
                nullable = True
                continue
            ok, encoded = _describe.jsonable(value)
            if not ok:
                raise _Undescribable
            values.append(encoded)
        if not values:
            raise _Undescribable
        literal: dict[str, Any] = {"kind": "enum", "values": values}
        if nullable:
            literal["nullable"] = True
        return literal
    if isinstance(ann, type) and issubclass(ann, enum.Enum):
        if not len(ann):
            raise _Undescribable  # an empty Enum has no members to claim
        # The returns side moves in lockstep with choices: `values` speaks
        # tokens (what documents carry, what the JSON Schema projection
        # lists natively), and `members` carries the pairs first-class —
        # token beside declared value, aliases as input synonyms — so a
        # foreign frontend binds the same faces by contract.
        pairs: list[dict[str, Any]] = []
        for face in _coerce.enum_faces(ann):
            ok, encoded = _describe.jsonable(face.member.value)
            if not ok:
                raise _Undescribable
            entry: dict[str, Any] = {"token": face.token, "value": encoded}
            if face.aliases:
                entry["aliases"] = list(face.aliases)
            pairs.append(entry)
        return {
            "kind": "enum",
            "name": ann.__name__,
            "values": [m["token"] for m in pairs],
            "members": pairs,
        }
    if ann is bool:
        return {"kind": "bool"}
    if ann is int:
        return {"kind": "int"}
    if ann is float:
        return {"kind": "float"}
    if ann is str:
        return {"kind": "str"}
    if isinstance(ann, type):
        for base, kind in _RETURN_SCALARS:
            if issubclass(ann, base):
                return {"kind": kind}
    origin = typing.get_origin(ann)
    if ann in (list, set, frozenset) or origin in (list, set, frozenset):
        args = typing.get_args(ann)
        element = args[0] if args else Any
        return {"kind": "list", "items": _returned_of(element, seen)}
    if origin is tuple:
        args = typing.get_args(ann)
        if len(args) == 2 and args[1] is Ellipsis:
            return {"kind": "list", "items": _returned_of(args[0], seen)}
        raise _Undescribable  # a heterogeneous tuple has no named positions
    if ann is dict or origin is dict:
        args = typing.get_args(ann)
        if not args:
            return {"kind": "object"}  # no field claims
        key, value = args
        if key is not str:
            raise _Undescribable  # JSON object keys are strings
        if value is Any or value is object:
            return {"kind": "object"}
        return {"kind": "map", "values": _returned_of(value, seen)}
    if typing.is_typeddict(ann):
        return _returned_fields(ann, seen, kind="object", typeddict=True)
    if dataclasses.is_dataclass(ann) and isinstance(ann, type):
        # Every dataclass field serialises (`asdict` has no optionality).
        return _returned_fields(ann, seen, kind="object")
    if isinstance(ann, type) and issubclass(ann, tuple) and hasattr(ann, "_fields"):
        # A NamedTuple serialises as a JSON *array* (it is a tuple); "row"
        # keeps that honest — named positions, not an object.
        return _returned_fields(ann, seen, kind="row")
    raise _Undescribable


def _returned_fields(
    cls: Any,
    seen: tuple[Any, ...],
    *,
    kind: str,
    typeddict: bool = False,
) -> dict[str, Any]:
    """An `object`/`row` spec for a dataclass, TypedDict, or NamedTuple.

    Fields ride as a name-keyed mapping in declaration order (for a "row"
    the order *is* the positions). A recursive shape has no finite spec to
    bake, so re-entering a class under description bails the whole
    annotation out to "no claims"."""
    if any(cls is s for s in seen):
        raise _Undescribable
    # Read the class-level facts before any `is_dataclass` narrowing below
    # can convince a type-checker `cls` no longer has them.
    class_name = str(getattr(cls, "__name__", cls))
    total = bool(getattr(cls, "__total__", True))
    try:
        # `include_extras` keeps `Required`/`NotRequired` visible: under
        # `from __future__ import annotations` a TypedDict's own
        # `__required_keys__` cannot see through the string forms, but the
        # resolved wrappers can't lie.
        hints = typing.get_type_hints(cls, include_extras=True)
    except Exception as exc:
        raise _Undescribable from exc  # a name in a field didn't resolve
    if dataclasses.is_dataclass(cls):
        names = [f.name for f in dataclasses.fields(cls)]
    elif kind == "row":
        names = list(cls._fields)
    else:
        names = list(hints)
    fields: dict[str, Any] = {}
    for name in names:
        hint = hints[name]
        required = True
        if typeddict:
            origin = typing.get_origin(hint)
            if origin is typing.Required:
                hint = typing.get_args(hint)[0]
            elif origin is typing.NotRequired:
                hint, required = typing.get_args(hint)[0], False
            else:
                required = total
        spec = _returned_of(hint, (*seen, cls))
        if not required:
            spec["required"] = False
        fields[name] = spec
    if not fields:
        raise _Undescribable  # a fieldless shape claims nothing worth baking
    return {"kind": kind, "name": class_name, "fields": fields}


def _run_completer(completer: suggest, memo: dict[int, list[str]]) -> list[str]:
    """Call a completer at most once per build (deduped by function identity).

    A raising *strict* completer aborts the build with `CompleterError` — its
    whole point is validation, so failing silent would validate nothing. A
    best-effort completer (`strict=False`) degrades to no candidates.
    """
    key = id(completer.fn)
    if key not in memo:
        try:
            memo[key] = [str(v) for v in completer.fn()]
        except Exception as exc:
            if completer.strict:
                name = getattr(completer.fn, "__qualname__", repr(completer.fn))
                raise CompleterError(
                    f"dynamic choices from {name}() failed: "
                    f"{type(exc).__name__}: {exc} — fix the completer, or pass "
                    f"suggest(fn, strict=False) if this data is best-effort"
                ) from exc
            memo[key] = []
    return memo[key]


def _finish(
    spec: dict[str, Any], memo: dict[int, list[str]], *, bake: bool = False
) -> dict[str, Any]:
    """Drop the transient completer, baking its choices only when asked.

    *bake* is for a surface that renders every parameter it can find and has
    nowhere to resolve later — the docs exporter. Everywhere else the
    completer stays unrun: the command line resolves the one parameter it is
    validating, help resolves the ones it prints, and a <kbd>Tab</kbd>
    recomputes fresh anyway. Running them all on every invocation is what
    made an unrelated `fm build` shell out to a git-branch completer.
    """
    completer = spec.pop("_completer", None)
    if spec.get("secret"):
        spec.pop("choices", None)  # never bake a secret's values
        completer = None
    if completer is not None and bake:
        spec["choices"] = _run_completer(completer, memo)
    return spec


def _cli_params(fn: Any) -> list[inspect.Parameter]:
    """The parameters that form a task's CLI (the injected ctx is not one)."""
    sig = resolved_signature(fn)
    ctx_name = context_param_name(sig)
    return [p for p in sig.parameters.values() if p.name != ctx_name]


def _source_of(fn: Any) -> str:
    """Where *fn* is written, for the shadow chain `--help` prints.

    The one answer, shared with the refusals that name a declaration and the
    failure line that places an exception: three spellings of
    `co_filename:co_firstlineno` is how they drift apart.
    """
    return _describe.source_of(fn)


def _task_node(
    fn: Any,
    memo: dict[int, list[str]],
    *,
    bake: bool = False,
    user_dir: str | None = None,
) -> dict[str, Any]:
    sig = resolved_signature(fn)
    infinite = registry.is_infinite(fn)
    interactive = registry.is_interactive(fn)
    confirm = registry.task_confirm(fn)
    lane = registry.task_lane(fn)
    ctx_name = context_param_name(sig)  # the injected ctx param is not a CLI arg
    # Imported here, not at module scope: the module compiles eleven regexes
    # on the way in (~0.24 ms of its 0.65 ms) and only manifest *building*
    # reads a docstring.
    #
    # Worth being honest about what that buys today: `sync_manifest` builds
    # unconditionally, so every run reaches this line anyway and only
    # `--version` is spared. It pays properly once building is skipped when
    # nothing changed — see notes/20260816-startup-perf.md, which measures
    # that rebuild at ~70 us per task on every run.
    from livery.footman import docstrings

    parsed = docstrings.parse(inspect.getdoc(fn))
    params = [
        _finish(param_spec(p), memo, bake=bake)
        for p in sig.parameters.values()
        if p.name != ctx_name
    ]
    # An optional positional must trail everything positional: a required
    # argument or a rest-consumer after it would make "which token is
    # whose" ambiguous — exactly what the grammar refuses to be.
    seen_optional: str | None = None
    for spec in params:
        if seen_optional is not None and (
            spec["kind"] == "variadic"
            or (spec["kind"] == "positional" and not spec.get("optional"))
            or (spec["kind"] == "positional" and spec.get("multiple"))
        ):
            raise SpecError(
                f"<{seen_optional}>: an Arg[…] optional positional must come "
                f"last — <{spec['name']}> follows it, so which token belongs "
                f"to whom would be a guess. Reorder, or make "
                f"<{spec['name']}> an option."
            )
        if spec["kind"] == "positional" and spec.get("optional"):
            seen_optional = spec["name"]
    for spec in params:
        if spec["name"] == "help" and spec["kind"] in ("flag", "option"):
            raise SpecError(
                "<help>: 'help' is a reserved parameter name — it maps to "
                "--help, which footman intercepts anywhere on the line to show "
                "help and never run a task, so the option could never be "
                "reached. Rename it (e.g. show_help). It is the only reserved "
                "name: every other global must come before the first task, so a "
                "task parameter may reuse it (fm deploy --json binds --json to "
                "deploy)."
            )
    known: set[str] = set()
    for spec in params:
        python_name = str(spec["name"]).replace("-", "_")
        known.add(python_name)
        # The docstring fills in; an explicit doc() marker already won.
        if "doc" not in spec and (text := parsed.params.get(python_name)):
            spec["doc"] = text
    if ctx_name:
        known.add(ctx_name)  # documenting the injected ctx param is fine
    if unknown := sorted(set(parsed.params) - known):
        _warn(
            f"footman: {getattr(fn, '__name__', fn)!s}: docstring documents "
            f"unknown parameter(s): {', '.join(unknown)}"
        )
    node: dict[str, Any] = {"help": parsed.summary, "params": params}
    # Where this task comes from, as the listings group it: a built-in
    # carries no defining directory (the cascade never tagged it), and the
    # personal rung's directory is the user tasks file's own. Everything
    # else is the project's cascade — including a plugin the project chose
    # to mount, which is the project's decision and reads as its own task.
    # Marked only when global: the project's tasks are the common case and
    # a per-row field for them would be bytes on the completion hot path.
    defined_in = _discover.defining_dir(fn)
    if defined_in is None or (user_dir is not None and defined_in == user_dir):
        node["global"] = True
    if (provider := registry.mounted_from(fn)) is not None:
        # Additive: which provider this task came from — the entry-point
        # identity for `plugin()`, the module name for `include()`. Omitted
        # for a task the tasks file defines itself, which is the honest
        # answer rather than a hole: the project owns it.
        #
        # A provider, not a file. A source path would answer only for a
        # Python function (`__code__`), and would make a reader infer
        # ownership by matching path prefixes; this names the owner
        # outright, and keeps naming it if a task is one day implemented
        # over something that is not Python. It also keeps absolute home
        # paths out of `--json --list` output people paste and commit.
        node["mounted_from"] = provider
    used = registry.task_uses(fn)
    if used:
        # The globals this task declares it reads — help, the catalog and
        # agents see the dependency without running anything.
        node["uses"] = [opt.name for opt in used]
    if (previous := _discover.shadowed(fn)) is not None:
        # Additive, and only for the rare overridden task: the options of
        # the task this one shadows, so `--help` can show the call
        # `inherited()` will make.
        node["shadows"] = {
            "params": [
                _finish(param_spec(p), memo, bake=bake) for p in _cli_params(previous)
            ],
            "where": _source_of(previous),
        }
    declares, _inner = _coerce.emitted(sig.return_annotation)
    if declares:
        if interactive:
            raise SpecError(
                f"{getattr(fn, '__name__', fn)!s}: Stdout[…] and "
                f"interactive=True cannot both hold — an interactive task "
                f"owns the real terminal, uncaptured, and a declaring task's "
                f"stdout belongs to its return value. Drop one."
            )
        node["emits"] = True  # additive: this task's stdout is its return value
    if (returned := returned_spec(sig.return_annotation)) is not None:
        # Additive: the output contract the return annotation declares —
        # static, so it bakes beside the param specs and every surface
        # (envelope, --describe, help, docs) reads the same shape.
        node["returned"] = returned
    if parsed.returns:
        node["returned_doc"] = parsed.returns  # additive: what the value means
    if infinite:
        node["infinite"] = True  # additive: listings and help say how it ends
    if interactive:
        node["interactive"] = True  # additive: this task owns the terminal
    if lane is not None:
        node["lane"] = lane  # additive: "serial" or "exclusive" scheduling
    if confirm:
        node["confirm"] = confirm  # additive: the yes/no gate before it runs
    # Additive, and deliberately visible: a caller that can see a task already
    # retries will not wrap it in a retry of its own, and every layer that
    # cannot see it is one more that can stack — curl's `--retry` multiplying
    # with `@task(retries=)` is the case the design note names. Baking the
    # field in while the schema is being touched is free; adding it later is a
    # version bump (notes/20260807-timeout-and-retry.md).
    if retries := registry.task_retries(fn):
        node["retries"] = retries
    if timeout := registry.task_timeout(fn):
        node["timeout"] = timeout
    if parsed.long:
        node["long"] = parsed.long
    # Additive availability annotation (`@requires`): the name stays listed and
    # completable either way — execution re-checks the predicate live.
    if (reason := registry.availability(fn)) is not None:
        node["disabled"] = reason
    return node


def _scoped(
    node: dict[str, Any], fn: Any, inherited: str | None, project: bool
) -> dict[str, Any]:
    """Stamp `unexposed` on a task that does not belong here.

    Resolved at build time, where the mode is known, so every reader
    downstream — listing, help, completion, the address resolver — asks one
    flat question instead of each re-deriving "am I in a project". The three
    `expose` answers collapse to that one flag against the mode, and the
    flag alone is enough to phrase the refusal: a task kept out *of* a
    project can only be `global_only`, one kept out *outside* one can only
    be `project_only`.
    """
    # Unsealed means unclaimed: a person's own tasks file and a project's
    # own cascade ride everywhere. `project_only` is imposed by `seal_expose`
    # on the rung that promises it, never assumed here.
    where = registry.declared_expose(fn) or inherited or "always"
    if where == "always":
        return node
    if (where == "project_only") is not project:
        node["unexposed"] = True
    return node


def _hide(node: dict[str, Any], own: bool | None, inherited: bool) -> dict[str, Any]:
    """Stamp the resolved `hidden` onto a task node: its own answer if it gave
    one, otherwise the group's. Additive — absent means listed."""
    if inherited if own is None else own:
        node["hidden"] = True
    return node


def _node(
    g: Group,
    memo: dict[int, list[str]],
    hidden: bool = False,
    *,
    bake: bool = False,
    expose: str | None = None,
    project: bool = True,
    user_dir: str | None = None,
) -> dict[str, Any]:
    # `hidden` is resolved here, where the tree structure is: a node that never
    # declared one inherits its group's answer, so hiding a subtree is said once
    # at the root and `hidden=False` on a child is a real way back. The group's
    # own answer may come from its default action — hiding what a bare
    # `fm <group>` runs hides the group it speaks for.
    own = g.hidden
    if own is None and g.default_task is not None:
        own = registry.declared_hidden(g.default_task)
    mine = hidden if own is None else own
    # The same inheritance for scope, one line down: a group's answer covers
    # its subtree, a child overrides it, and silence asks whoever encloses it.
    here = g.expose if g.expose is not None else expose
    node: dict[str, Any] = {
        "help": g.help,
        "tasks": {
            name: _scoped(
                _hide(
                    _task_node(fn, memo, bake=bake, user_dir=user_dir),
                    registry.declared_hidden(fn),
                    mine,
                ),
                fn,
                here,
                project,
            )
            for name, fn in g.tasks.items()
        },
        "groups": {
            name: _node(
                sub,
                memo,
                mine,
                bake=bake,
                expose=here,
                project=project,
                user_dir=user_dir,
            )
            for name, sub in g.groups.items()
        },
    }
    if mine:
        node["hidden"] = True  # additive: listings skip it, the address still runs
    if g.mounted_from is not None:
        # The same field, answering a *stronger* question here: this whole
        # group is one provider's, which is true only when the mount landed
        # on a free name and grafted the stamped group whole. Compose into a
        # group that already exists and the destination group survives
        # unstamped — shared, so no single owner — while every task under it
        # still answers exactly. Read ownership off the task rows; this is
        # for "is this entire subtree theirs".
        node["mounted_from"] = g.mounted_from
    # A runnable group (one with `@group.default`) carries the default's option
    # surface — the same `{help, params}` shape a task node has — so the splitter
    # parses a bare `fm <group> [flags]` against it and completion/help render it.
    # The fan-out flag rides beside it (not inside: `_task_node` memoises task
    # specs, and fan-out is a property of this default, not of the function)
    # so listings can *say* what an undocumented default does.
    if g.default_task is not None:
        node["default"] = _scoped(
            _hide(
                _task_node(g.default_task, memo, bake=bake, user_dir=user_dir),
                registry.declared_hidden(g.default_task),
                mine,
            ),
            g.default_task,
            here,
            project,
        )
        node["default_fanout"] = registry.fans_out(g.default_task)
        # The bare group name IS this action's other spelling, so the group
        # answers whatever its default answers — stamped here, once, rather
        # than taught to each of the listing, completion and dispatch paths.
        # `hidden` already resolves this way a few lines up; this is the same
        # rule, and without it `fm lint` ran while `fm lint.default` refused.
        if node["default"].get("unexposed"):
            node["unexposed"] = True
    return node


def tree_hash(tree: dict[str, Any]) -> str:
    """Stable hash of the tree's structure (names, params, help)."""
    blob = json.dumps(tree, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def build_manifest(
    root: Group,
    *,
    completion_max_age: int | None = None,
    bake_completers: bool = False,
    project: bool = True,
    user_dir: str | None = None,
) -> dict[str, Any]:
    """Introspect *root* into a serialisable manifest dict.

    *project* says whether this manifest describes a directory that has one.
    It is the only place the question is asked: tasks that need a project are
    stamped `unexposed` here, so listing, help, completion and the address
    resolver each read one flat flag rather than re-deriving the mode.

    *completion_max_age* (seconds, or `None` to disable) is baked in so the
    stdlib-only completion hot path can decide whether to trigger a background
    refresh without reading config.

    Dynamic completers do **not** run here. They used to — every invocation
    executed every `suggest()` function in the tree, so a git-branch completer
    on one task shelled out on every `fm anything` — and nothing needed it:
    a <kbd>Tab</kbd> recomputes fresh, the command line resolves the one
    parameter whose value it validates, and help resolves the ones it prints.
    *bake_completers* is for the surface with nowhere to resolve later: the
    docs exporter, which renders every parameter it can find into a page.
    """
    memo: dict[int, list[str]] = {}
    tree = _node(root, memo, bake=bake_completers, project=project, user_dir=user_dir)
    tree["globals"] = [
        _global_spec(opt, memo, bake=bake_completers) for opt in _unique_globals(root)
    ]
    tree["global_help"] = _core_global_help()
    # Which side this tree was built for. Every `unexposed` stamp under it
    # was resolved against this one answer, so a reader that has to *phrase*
    # the refusal can say which way the task was withheld without the stamp
    # having to carry a reason of its own.
    tree["project"] = project
    return {
        "schema": SCHEMA_VERSION,
        "hash": tree_hash(tree),
        "completion_max_age": completion_max_age,
        "tree": tree,
    }


def _core_global_help() -> dict[str, str]:
    """`--flag -> one line`, for footman's own globals.

    The completion hot path knows the core flags by name (its own frozenset
    mirror — it may not import `_split`), but a name with no words beside it
    is the difference between a Tab that teaches and a Tab that lists. Rather
    than mirror thirty-five help strings into `_complete` as well — prose is
    exactly what rots — the words ride in the manifest the hot path already
    reads and parses. `CORE_OPTIONS` stays the one place they are written.

    Aliases carry their long form's line: `-j` and `--jobs` are one option,
    and a reader scanning a column wants to know what it does, not that it
    has two spellings.

    `{prog}` is substituted here, the way `--help` substitutes it when it
    renders the same table — a manifest belongs to one brand (its path is
    brand-scoped and it already bakes `tasks_file`), so the brand's own
    command name is the honest thing to store. Left raw, a Tab would offer
    "help for {prog}" in braces.

    The keys are the *offerable spellings*, exactly as the splitter reads
    them — what plugin options always got from `_opt_rows`, the core table
    now gets too. An option's value goes attached, so `--opt=` is a key of
    its own; its bare mention stands for its default, so only a defaulted
    option gets a bare key (bare `--where` is a taught refusal, and a menu
    must not offer what the grammar refuses). The bare key's line carries
    the resolved default — a manifest belongs to this machine, so `--jobs`
    naming its actual width is the honest thing to store.
    """
    from livery.footman import _app, _describe, _split

    rows: dict[str, str] = {}
    for name, alias, kind, _hint, default, help_text in _split.GLOBALS:
        if not help_text:
            continue
        line = help_text.replace("{prog}", _app._brand.prog)
        for flag in (name, alias):
            if not flag:
                continue
            if kind != "option":
                rows[flag] = line
                continue
            rows[flag + "="] = line
            if default is not None:
                rows[flag] = line + _describe.global_default_suffix(name)
    return rows


_REPLACE_ATTEMPTS = 25
_REPLACE_PAUSE = 0.04  # ≈1s of retries in all


def write_manifest(manifest: dict[str, Any], path: Path) -> None:
    """Write *manifest* to *path* atomically (never leave a half file).

    Windows refuses to replace a destination another process holds open, and
    a reader holding it open is the *design* here: a completion poll reads
    this file every few milliseconds while a detached refresh rewrites it.
    Losing that race silently means the rebuild never lands — the caller is
    a background child that swallows its errors — so the file stays stale
    until some later write happens to find a quiet moment.

    Hence a bounded retry: about a second of attempts, then give up without
    leaving the temp file behind. POSIX never takes this path (rename over
    an open file is fine), and no caller waits longer than it already did.
    """
    import time

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), "utf-8")
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                # Out of patience: clean up rather than litter the cache
                # directory with `<name>.<pid>.tmp` files nobody reads.
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(_REPLACE_PAUSE)


def load_manifest(path: Path) -> dict[str, Any] | None:
    """Read a cached manifest, or `None` if missing/unreadable/corrupt."""
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def sync_manifest(
    root: Group,
    key_dir: Path,
    *,
    completion_max_age: int | None = None,
    tasks_file: str | None = None,
    path: Path | None = None,
    bake_cwd: bool = True,
    builtin: tuple[str, ...] = (),
    project: bool = True,
    user_dir: str | None = None,
) -> dict[str, Any]:
    """Build the fresh manifest and rewrite the cache only on a hash change.

    Called on the execution path, which has already paid to import the tree.
    The cache is keyed by *key_dir* (the cwd), since the effective task set is
    the cascade from the repo root down. The hash guard avoids needless disk
    writes (and mtime churn) when nothing about the command surface changed — a
    changed *completion_max_age* also forces a rewrite so a config edit takes
    effect.

    The write itself is best-effort. Everything the caller needs is the
    returned tree, which is already in hand; the file is only there to make
    the next TAB fast. A cache nobody can write to — a read-only HOME in a
    locked-down image, a stray `chmod` — must cost a cold completion, not
    every command, so an `OSError` here is swallowed the way `_progress`
    swallows the timing history's.
    """
    fresh = build_manifest(
        root,
        completion_max_age=completion_max_age,
        project=project,
        user_dir=user_dir,
    )
    if bake_cwd:
        # The directory this manifest describes, baked in (additive) so the
        # cache collector can tell a deleted project's leftovers from a
        # living one's without guessing from hashes. The global manifest
        # bakes none — it describes no directory, so the collector's idle
        # sweep owns it rather than the deleted-project rule.
        fresh["cwd"] = str(key_dir)
    if builtin:
        # What the refresh child rebuilds the global tree from: the entry-
        # point names, which travel where live objects cannot.
        fresh["builtin"] = sorted(builtin)
    if tasks_file:
        # Additive, like `cwd`: the background refresh reads it back, so a
        # branded CLI's custom filename survives a refresh it can't attend.
        fresh["tasks_file"] = tasks_file
    # What the user-level rungs looked like when this was built. The
    # resolver compares it to spot a config or user-tasks edit the age
    # clock would otherwise hide for a whole `max_age` — and it joins the
    # write guard below, or a rebuild triggered by a stamp change would
    # leave the old stamp in place and every later TAB would spawn again.
    fresh["user_stamp"] = _paths.user_stamp()
    # `path` lets a caller key the cache file separately from the baked
    # `key_dir` — a `-f` run caches by (cwd, file) yet still bakes the cwd, so
    # the collector prunes it with the project like any other.
    path = path or _paths.manifest_path(key_dir)
    cached = load_manifest(path)
    if (
        cached is None
        # An upgrade bumps the schema with the tree unchanged, so the hash
        # alone says "nothing to do" — and the old-schema file then lives
        # forever, every TAB refusing it, spawning a rebuild that "succeeds"
        # without writing, and paying the full cold bound. The schema is
        # part of what the file IS, not part of what it describes.
        or cached.get("schema") != SCHEMA_VERSION
        or cached.get("hash") != fresh["hash"]
        or cached.get("completion_max_age") != completion_max_age
        or cached.get("cwd") != fresh.get("cwd")
        or cached.get("tasks_file") != fresh.get("tasks_file")
        or cached.get("user_stamp") != fresh.get("user_stamp")
    ):
        # Guarded here rather than inside `write_manifest`, whose contract is
        # "the write landed, or you hear about it" — that is what its retry
        # loop is for. Only a caller can say the file was optional.
        with contextlib.suppress(OSError):
            write_manifest(fresh, path)
    return fresh
