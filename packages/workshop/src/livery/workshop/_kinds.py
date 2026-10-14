"""The kind registry: what a package kind is, in one record.

A kind answers two questions through one registration: how to
build (the backend) and what a birth writes (the seed tree, with a
parent chain written beneath it). The extensions a package holds
require the tools it needs, and the steps say what the gate runs over
it: each names the extensions it judges.

Adding a kind means one call: ``register_kind`` with the record.
The workshop registers ``base`` and ``python`` at import; an extension's
plugin registers its own kinds at mount, which is how a brand ships
a kind the way it ships fragments. An unknown declared kind refuses
naming the vocabulary: a typo that silently builds the wrong kind
is worse than a stop.

``base`` is abstract: the record behind the ``package-base`` template
every package template renders first. It heads every kind's chain,
so what it carries reaches every kind without a leaf restating it.
A package's ``kind`` never names it: the vocabulary a contract may
use is the concrete kinds.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

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

if TYPE_CHECKING:
    from pathlib import Path

    from livery.workshop._packages import Package
    from livery.workshop._registries import RegistryTarget


class Stamper(Protocol):
    """What a backend's ``stamp_version`` answers with."""

    def stamp(self, version: str) -> list[str]:
        """Write *version* into the kind's homes; the files changed."""
        ...

    def homes(self) -> list[Path]:
        """Every file `stamp` may write, so a caller can snapshot and restore them."""
        ...


class Backend(Protocol):
    """The callables every kind's backend module exposes.

    The dispatch extension absorbs a new kind automatically: call sites
    ask the registry, never a module by name.
    """

    def build(self, package: Package, root: Path, *, epoch: int = 0) -> Path:
        """Build the package's artifacts into its dist; the dist dir."""
        ...

    def publish_artifact(
        self,
        package: Package,
        root: Path,
        *,
        version: str,
        target: RegistryTarget,
    ) -> bool:
        """Upload the built artifacts to *target*, the resolved registry.

        False when everything was already there: a re-run walks past
        what an earlier attempt landed. The wave resolves the target
        for the kind's artifact and hands it whole; each kind reads
        the fields its own publisher needs.
        """
        ...

    def current_version(self, package: Package) -> str:
        """The version the package's own manifest declares."""
        ...

    def stamp_version(self, package: Package) -> Stamper:
        """Where the kind keeps its version, ready to stamp."""
        ...

    def declared_requirements(self, package: Package) -> dict[str, str]:
        """What the package declares natively, name to constraint."""
        ...

    def module_roots(self, package: Package) -> tuple[str, ...]:
        """The names other packages reference this one's code by."""
        ...

    def public_modules(self, package: Package) -> tuple[str, ...]:
        """The modules that declare the package's public API, by import path.

        What a type-completeness step verifies; empty for a package
        whose kind has no importable API.
        """
        ...

    def compile_commands(self, package: Package) -> Path | None:
        """Where the package's gate build writes its compilation database.

        What a tool that reads one takes (clang-tidy's ``-p``); None for
        a kind whose build writes none. The file exists once the build
        is configured.
        """
        ...

    def gate_build(self, package: Package, root: Path) -> None:
        """Build what the kind's tests run on, into the gate's build directory.

        Incremental, so a rebuild after one edit costs that edit. A
        kind whose tests run on source does nothing here.
        """
        ...

    def test(
        self,
        package: Package,
        root: Path,
        *,
        selection: tuple[str, ...] = (),
        arguments: tuple[str, ...] = (),
    ) -> None:
        """Run the kind's tests of *package*; a refusal is the verdict.

        Every test, or with *selection* the tests of the named files
        alone, relative to the package. A selection the kind cannot
        map to a test is a refusal, never a silent empty run.
        *arguments* go to the kind's test runner after its own.
        """
        ...


@dataclass(frozen=True)
class KindRecord:
    """One package kind, completely.

    Attributes:
        name: The contract's ``kind`` value.
        backend: The module carrying the kind's build callables.
        template: The seed tree a package of the kind is born from,
            or empty for a kind with no seeds of its own.
        parent: The kind this one extends; a birth writes the
            parent's seeds first, then this kind's over them.
        host_tools: What the host must already provide and no cache
            will ever install (a C compiler); ``fm doctor`` and
            ``fm env.check`` name an absence instead of letting a
            build fail midway.
        artifact: Which registry kind the release wave publishes
            through (``python`` or ``conan``); empty for a kind
            that publishes nothing.
        abstract: Whether the kind exists for its children alone: it
            heads their chains with its seed tree, builds nothing,
            and is never a package's ``kind``. A concrete
            kind needs a backend; an abstract one has none.
        stands_for: The package-level extensions a package of the kind
            that lists none is taken to hold: their categories, root
            files and steps are its own. A child kind adds to its
            parent's.
        host_build: How the kind builds, on one host, the native
            artifacts a release ships, before the wave publishes them,
            given the workspace root; it reads the release at the
            root's HEAD. None for a kind whose packages the wave builds
            itself.
    """

    name: str
    backend: Backend | None = None
    template: str = ""
    parent: str = ""
    host_tools: tuple[str, ...] = ()
    artifact: str = "python"
    abstract: bool = False
    stands_for: tuple[str, ...] = ()
    host_build: Callable[[Path], None] | None = None


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
    if record.backend is None and not record.abstract:
        fail(
            f"kind {record.name!r} has no backend: a kind a package may declare"
            " builds through one, and a kind that exists for its children alone"
            " is registered abstract"
        )
    _KINDS[record.name] = record


def kind_names() -> tuple[str, ...]:
    """The vocabulary a contract's ``kind`` may name, sorted: the concrete kinds."""
    return tuple(sorted(name for name, record in _KINDS.items() if not record.abstract))


def all_kinds() -> tuple[KindRecord, ...]:
    """Every registered kind, abstract ones included, in registration order."""
    return tuple(_KINDS.values())


def kind_for(kind_name: str) -> KindRecord:
    """The record for the contract's ``kind`` value; refusal teaches."""
    record = _KINDS.get(kind_name)
    if record is None:
        known = ", ".join(kind_names())
        fail(f"{kind_name!r} is not a registered package kind; kinds: {known}")
    return record


def backend_for(package: Package) -> Backend:
    """The backend that builds *package*, by its declared kind.

    An abstract kind refuses naming the vocabulary: it builds nothing,
    and a contract that names it has the wrong kind.
    """
    record = kind_for(package.kind)
    if record.backend is None:
        known = ", ".join(kind_names())
        fail(
            f"{package.path}: kind {package.kind!r} is an abstract kind and builds"
            f" nothing; a package's kind is one of {known}"
        )
    return record.backend


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


def kind_host_tools(present_types: set[str]) -> tuple[str, ...]:
    """The union of host requirements the present kinds name, sorted."""
    tools: set[str] = set()
    for kind_name in present_types:
        for record in kind_chain(kind_name):
            tools.update(record.host_tools)
    return tuple(sorted(tools))


def kind_stands_for(kind_name: str) -> tuple[str, ...]:
    """The extensions *kind_name*'s packages are taken to hold while they list none.

    The chain's, ancestors first, each once; empty for a kind the
    registry does not know, such as the workspace's own unit.
    """
    if kind_name not in _KINDS:
        return ()
    found: dict[str, None] = {}
    for record in kind_chain(kind_name):
        found.update(dict.fromkeys(record.stands_for))
    return tuple(found)


def extension_set(package: Package) -> tuple[str, ...]:
    """The package-level extensions *package* holds, which its steps match.

    Its set, the extensions it lists and those they require, for a
    package that lists any; what its kind stands for
    ([livery.workshop._kinds.kind_stands_for][]) otherwise, until the
    kinds go.
    """
    return held_extensions(package.kind, package.extensions)


def held_extensions(kind_name: str, listed: tuple[str, ...]) -> tuple[str, ...]:
    """What a package of *kind_name* listing *listed* holds; see `extension_set`."""
    if not listed:
        return kind_stands_for(kind_name)
    from livery.workshop._composition import package_set
    from livery.workshop._extensions import installed_declaration

    return package_set(listed, installed_declaration)


def _register_builtin() -> None:
    from livery.extensions.python import _backend as _python
    from livery.workshop._backends import _cpp_conan, _python_nanobind

    # The base every kind derives from: abstract, the record behind the
    # package-base template.
    register_kind(
        KindRecord(
            name="base",
            template=BASE_TEMPLATE,
            artifact="",
            abstract=True,
        )
    )
    # Two contract kinds exist today. The extension package template
    # (package-extension) is a template variant of python, not
    # a contract kind of its own: every member declares "python".
    register_kind(
        KindRecord(
            name="python",
            backend=_python,
            template="package-python",
            parent="base",
            # A package that lists no extension is taken to hold the
            # python extension: its categories and steps are the same.
            stands_for=("python",),
        )
    )
    # The binary extension: a python distribution in every checker's
    # eyes (the chain says so), built through cibuildwheel so the
    # wheel carries its platform tag.
    register_kind(
        KindRecord(
            name="python-nanobind",
            backend=_python_nanobind,
            template="package-python-nanobind",
            parent="python",
            host_tools=("cc", "c++"),
            # Beside python's, the extension module's C++ sources, the
            # provider that resolves their dependencies, and nanobind's
            # wheels job: a nanobind package consumes conan and is never
            # a recipe, so conan's sync step applies to it through the
            # bridge, while the lifecycle phases read the listed
            # extensions alone and this kind's backend builds it.
            stands_for=_python_nanobind.STANDS_FOR,
            # What each leg of nanobind's wheels job builds: the release's
            # conan packages and every platform-wheel package.
            host_build=_python_nanobind.host_build,
        )
    )
    # The C/C++ library: cmake configures and builds, ctest is the
    # test step, conan packages the result. The python type checkers
    # do not gate it (there is no dist to verify types on); a python
    # formatter and linter judge its conanfile.py beside clang-format
    # and clang-tidy over its sources, and its build and ctest records
    # carry the build and test phases.
    register_kind(
        KindRecord(
            name="cpp-conan",
            backend=_cpp_conan,
            template="package-cpp-conan",
            parent="base",
            host_tools=("cc", "c++"),
            artifact="conan",
            # A package that lists no extension is taken to hold the
            # three a new one lists: one category table, one conan
            # workspace and the same steps either way.
            stands_for=_cpp_conan.STANDS_FOR,
        )
    )


_register_builtin()
