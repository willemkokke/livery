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

    def check(self, package: Package, root: Path) -> None:
        """Run the kind's own per-package gate; a refusal is the verdict.

        The gate calls it only for a kind whose contract names
        ``kind_verbs``: a kind whose verbs run at workspace scope
        (python's checkers cover every python package in one
        invocation) declares none and is never called here.
        """
        ...


@dataclass(frozen=True)
class CiContract:
    """Which gate verbs apply to a kind.

    The default is the widest answer (everything applies), so an
    unregistered override can only widen the gate, never quietly
    narrow it. A verb absent from ``check_verbs`` skips by name in
    the gate output. ``kind_verbs`` names the kind's own per-package
    checks: a non-empty tuple makes the gate call the backend's
    ``check`` for each package of the kind and print these names as
    what ran.
    """

    check_verbs: tuple[str, ...] = (
        "format",
        "lint",
        "typecheck",
        "typecomplete",
        "test",
    )
    kind_verbs: tuple[str, ...] = ()


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
        host_tools: What the host must already provide and no cache
            will ever install (a C compiler); ``fm doctor`` and
            ``fm env.check`` name an absence instead of letting a
            build fail midway.
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
    host_tools: tuple[str, ...] = ()
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


def kind_host_tools(present_types: set[str]) -> tuple[str, ...]:
    """The union of host requirements the present kinds name, sorted."""
    tools: set[str] = set()
    for type_name in present_types:
        for record in kind_chain(type_name):
            tools.update(record.host_tools)
    return tuple(sorted(tools))


def is_python_kind(type_name: str) -> bool:
    """Whether the kind is a Python distribution, by its chain.

    True when ``python`` sits anywhere in the chain: a child kind (a
    binary extension) is still a wheel with a ``pyproject.toml``,
    while a kind outside the chain (``cpp-conan``) is not and never
    joins the uv workspace.
    """
    return any(record.name == "python" for record in kind_chain(type_name))


def requires_pyproject(type_name: str) -> bool:
    """Whether a package of this declared type must carry a pyproject.

    An unregistered type answers False so discovery can finish and
    the backend refusal can name the vocabulary; a missing file
    would otherwise mask the real problem, the typo.
    """
    if type_name not in _KINDS:
        return False
    return is_python_kind(type_name)


def record_for_template(template_kind: str) -> KindRecord | None:
    """The record whose template is *template_kind*; None when unmapped.

    A template variant (``package-python-layer``) maps to no record
    and the caller falls back to the python wiring.
    """
    for record in _KINDS.values():
        if record.template == template_kind:
            return record
    return None


def _register_builtin() -> None:
    from livery.workshop._backends import _cpp_conan, _python

    # Two contract kinds exist today. The layer package template
    # (package-python-layer) is a template variant of python, not
    # a contract type of its own: every member declares "python".
    register_kind(
        KindRecord(
            name="python",
            backend=_python,
            template="package-python",
            managed=("cliff.toml",),
        )
    )
    # The C/C++ library: cmake configures and builds, ctest is the
    # test verb, conan packages the result. The python checkers do
    # not gate it (there is no dist to verify types on), but ruff
    # still formats and lints its conanfile.py, so those two verbs
    # stay in the contract.
    register_kind(
        KindRecord(
            name="cpp-conan",
            backend=_cpp_conan,
            template="package-cpp-conan",
            tools=("cmake", "conan", "ninja"),
            host_tools=("cc", "c++"),
            managed=("cliff.toml",),
            ci=CiContract(
                check_verbs=("format", "lint"),
                kind_verbs=("configure", "build", "ctest"),
            ),
        )
    )


_register_builtin()
