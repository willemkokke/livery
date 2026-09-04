"""The kind registry: what a package type is, in one record.

A kind answers four questions through one registration: how to
build (the backend, three callables), what to render (the template
directory, with a parent chain rendered beneath it), what the
machine needs (the tools it contributes to the derived profile),
and what the gate runs (the CI contract naming the verbs that
apply, so a verb that does not apply skips saying so and never
passes vacuously).

Adding a kind means one call: ``register_kind`` with the record.
The workshop registers ``python`` at import; a layer's plugin
registers its own kinds at mount, which is how a brand ships a
kind the way it ships fragments. An unknown declared type refuses
naming the vocabulary: a typo that silently builds the wrong kind
is worse than a stop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from footman import fail

if TYPE_CHECKING:
    from pathlib import Path

    from livery.workshop._packages import Package


class Backend(Protocol):
    """The three callables every kind's backend module exposes.

    The dispatch layer absorbs a new kind automatically: call sites
    ask the registry, never a module by name.
    """

    def build(self, package: Package, root: Path, *, epoch: int = 0) -> Path:
        """Build the package's artifacts into its dist; the dist dir."""
        ...


@dataclass(frozen=True)
class CiContract:
    """Which gate verbs apply to a kind.

    The default is the widest answer (everything applies), so an
    unregistered override can only widen the gate, never quietly
    narrow it. A verb absent here skips by name in the gate output.
    """

    check_verbs: tuple[str, ...] = (
        "format",
        "lint",
        "typecheck",
        "typecomplete",
        "test",
    )


@dataclass(frozen=True)
class KindRecord:
    """One package kind, completely.

    Attributes:
        name: The contract's ``type`` value.
        backend: The module carrying the kind's build callables.
        template: The template directory name the kind renders, or
            empty for a kind with no template of its own.
        parent: The kind this one extends; the chain renders parent
            first, then this kind's files over it, and the managed
            set is the union along the chain.
        tools: What the kind contributes to the derived tool
            profile by existing in a workspace.
        managed: The rendered files the template keeps matching in
            a package of this kind; the chain's union is what the
            drift gate judges.
        ci: The gate verbs that apply.
    """

    name: str
    backend: Backend
    template: str = ""
    parent: str = ""
    tools: tuple[str, ...] = ()
    managed: tuple[str, ...] = ()
    ci: CiContract = field(default_factory=CiContract)


_KINDS: dict[str, KindRecord] = {}


def register_kind(record: KindRecord) -> None:
    """Register *record*; a parent must already be registered.

    Layers call this from their plugin at mount. Re-registering a
    name replaces it, which is how a test injects a fake and how a
    layer deliberately overrides a kind it owns.
    """
    if record.parent and record.parent not in _KINDS:
        fail(
            f"kind {record.name!r} extends {record.parent!r}, which is"
            f" not registered; register the parent first"
        )
    _KINDS[record.name] = record


def kind_names() -> tuple[str, ...]:
    """The registered vocabulary, sorted."""
    return tuple(sorted(_KINDS))


def kind_for(type_name: str) -> KindRecord:
    """The record for the contract's ``type`` value; refusal teaches."""
    record = _KINDS.get(type_name)
    if record is None:
        known = ", ".join(kind_names())
        fail(f"{type_name!r} is not a registered package kind; kinds: {known}")
    return record


def backend_for(package: Package) -> Backend:
    """The backend that builds *package*, by its declared type."""
    return kind_for(package.type).backend


def kind_chain(type_name: str) -> tuple[KindRecord, ...]:
    """The render chain, parent first, ending at *type_name*.

    A cycle refuses naming the chain rather than recursing forever.
    """
    chain: list[KindRecord] = []
    seen: set[str] = set()
    name = type_name
    while name:
        if name in seen:
            fail(
                "the kind chain cycles: "
                + " -> ".join([*[r.name for r in chain], name])
            )
        seen.add(name)
        record = kind_for(name)
        chain.append(record)
        name = record.parent
    chain.reverse()
    return tuple(chain)


def template_chain(template_kind: str) -> tuple[str, ...]:
    """The template kinds to render, parent first, leaf last.

    Derived from the registry: the record whose template is
    *template_kind* chains through its parents' templates. A
    template the registry does not map (a variant such as
    ``package-python-layer``) renders alone, today's behaviour.
    """
    by_template = {r.template: r for r in _KINDS.values() if r.template}
    record = by_template.get(template_kind)
    if record is None:
        return (template_kind,)
    return tuple(r.template for r in kind_chain(record.name) if r.template)


def managed_files(type_name: str) -> tuple[str, ...]:
    """The drift-judged rendered files: the chain's union, sorted."""
    managed: set[str] = set()
    for record in kind_chain(type_name):
        managed.update(record.managed)
    return tuple(sorted(managed))


def kind_tools(present_types: set[str]) -> tuple[str, ...]:
    """The union of tools the present kinds contribute, sorted."""
    tools: set[str] = set()
    for type_name in present_types:
        for record in kind_chain(type_name):
            tools.update(record.tools)
    return tuple(sorted(tools))


def _register_builtin() -> None:
    from livery.workshop._backends import _python

    # One contract kind exists today. The layer package template
    # (package-python-layer) is a template variant of this kind, not
    # a contract type of its own: every member declares "python".
    register_kind(
        KindRecord(
            name="python",
            backend=_python,
            template="package-python",
            managed=("cliff.toml",),
        )
    )


_register_builtin()
