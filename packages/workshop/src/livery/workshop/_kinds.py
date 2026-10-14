"""The kind registry: the seed trees a package's birth writes, by name.

A kind names a seed tree and the kind whose seeds a birth writes
beneath it, its parent. Adding a kind means one call: ``register_kind``
with the record. The workshop registers ``base``, ``python``,
``python-nanobind`` and ``cpp-conan`` at import; an extension's plugin
registers its own kinds at mount, which is how a brand ships a seed
tree the way it ships fragments.

A kind builds, tests and releases nothing, and a package holds nothing
through it: a package holds the extensions its contract lists, and
their steps and answers build, test and release it.

``base`` is abstract: the record behind the ``package-base`` template
every package template renders first. It heads every kind's chain,
so what it carries reaches every kind without a leaf restating it.

``python-nanobind`` is abstract too: the record behind the nanobind
seed tree, which a birth writes over python's.
"""

from __future__ import annotations

from dataclasses import dataclass

from livery.footman import fail

# The four builtin categories live beside the registry that answers
# them and are re-exported here as the kind's vocabulary.
from livery.workshop._categories import (
    CONFIGURATION as CONFIGURATION,
)
from livery.workshop._categories import (
    EXAMPLE as EXAMPLE,
)
from livery.workshop._categories import (
    SOURCE as SOURCE,
)
from livery.workshop._categories import (
    TEST as TEST,
)
from livery.workshop._categories import (
    TEST_SUPPORT as TEST_SUPPORT,
)
from livery.workshop._categories import (
    WORKSPACE as WORKSPACE,
)


@dataclass(frozen=True)
class KindRecord:
    """One package kind: the seed tree a birth writes, and its parent.

    Attributes:
        name: The kind's name, which a seed chain names it by.
        template: The seed tree a package of the kind is born from,
            or empty for a kind with no seeds of its own.
        parent: The kind this one extends; a birth writes the
            parent's seeds first, then this kind's over them.
        abstract: Whether the record exists for the seed chain alone,
            as the base and nanobind's tree do: the conformance kit
            probes no package of it.
    """

    name: str
    template: str = ""
    parent: str = ""
    abstract: bool = False


_KINDS: dict[str, KindRecord] = {}


def register_kind(record: KindRecord) -> None:
    """Register *record*; a parent must already be registered.

    Extensions call this from their plugin at mount. Re-registering a
    name replaces it, which is how a test injects a fake and how a
    extension deliberately overrides a kind it owns.
    """
    if record.parent and record.parent not in _KINDS:
        fail(
            f"kind {record.name!r} extends {record.parent!r}, which is"
            f" not registered; register the parent first"
        )
    _KINDS[record.name] = record


def kind_names() -> tuple[str, ...]:
    """The kinds that are not abstract, sorted: the ones a refusal names."""
    return tuple(sorted(name for name, record in _KINDS.items() if not record.abstract))


def all_kinds() -> tuple[KindRecord, ...]:
    """Every registered kind, abstract ones included, in registration order."""
    return tuple(_KINDS.values())


def kind_for(kind_name: str) -> KindRecord:
    """The record registered as *kind_name*; refusal names the kinds."""
    record = _KINDS.get(kind_name)
    if record is None:
        known = ", ".join(kind_names())
        fail(f"{kind_name!r} is not a registered package kind; kinds: {known}")
    return record


def kind_chain(kind_name: str) -> tuple[KindRecord, ...]:
    """The render chain, parent first, ending at *kind_name*.

    A cycle refuses naming the chain rather than recursing forever.
    """
    chain: list[KindRecord] = []
    seen: set[str] = set()
    name = kind_name
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


#: The shared seed carrier every package template renders first:
#: the docs page and its nav live once, here.
BASE_TEMPLATE = "package-base"


def template_chain(template_kind: str) -> tuple[str, ...]:
    """The seed trees a package's birth writes, parent first, leaf last.

    Every package's chain starts at ``package-base``, the shared seed
    tree (the docs page and its nav), so a package of any kind ships a
    docs section; the child's seeds win over it. The chain derives from
    the registry: the record whose template is *template_kind* chains
    through its parents' templates, and the ``base`` kind heads every
    chain. A tree the registry does not map (a variant such as
    ``package-extension``) is written over the base alone.
    """
    by_template = {r.template: r for r in _KINDS.values() if r.template}
    record = by_template.get(template_kind)
    if record is None:
        chain: tuple[str, ...] = (template_kind,)
    else:
        chain = tuple(r.template for r in kind_chain(record.name) if r.template)
    # A kind registered without the base as its parent (a fake, an extension's
    # own) still gets the base's seeds first: the docs seeds live there alone.
    if template_kind.startswith("package-") and chain[0] != BASE_TEMPLATE:
        chain = (BASE_TEMPLATE, *chain)
    return chain


def _register_builtin() -> None:
    # The base every kind derives from: abstract, the record behind the
    # package-base template.
    register_kind(
        KindRecord(
            name="base",
            template=BASE_TEMPLATE,
            abstract=True,
        )
    )
    # The python package's seeds. The extension package template
    # (package-extension) is a variant written over the base alone, not
    # a kind of its own.
    register_kind(
        KindRecord(
            name="python",
            template="package-python",
            parent="base",
        )
    )
    # The nanobind seed tree: a python package's seeds with the
    # extension module's build files over them.
    register_kind(
        KindRecord(
            name="python-nanobind",
            template="package-python-nanobind",
            parent="python",
            abstract=True,
        )
    )
    # The C/C++ library's seeds: a CMake project and its conan recipe.
    register_kind(
        KindRecord(
            name="cpp-conan",
            template="package-cpp-conan",
            parent="base",
        )
    )


_register_builtin()
