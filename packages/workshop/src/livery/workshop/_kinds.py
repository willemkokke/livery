"""The kind registry: what a package kind is, in one record.

A kind answers three questions through one registration: how to
build (the backend, three callables), what a birth writes (the seed
tree, with a parent chain written beneath it), and what the machine
needs (the tools it contributes to the derived profile). What the gate
runs over a package is the checks' to say: each names the kinds it
judges, and a package of another kind skips it by name.

Adding a kind means one call: ``register_kind`` with the record.
The workshop registers ``base`` and ``python`` at import; an extension's
plugin registers its own kinds at mount, which is how a brand ships
a kind the way it ships fragments. An unknown declared kind refuses
naming the vocabulary: a typo that silently builds the wrong kind
is worse than a stop.

``base`` is abstract: the record behind the ``package-base`` template
every package template renders first. It heads every kind's chain
and carries what every kind needs because every kind releases, the
changelog engine and the ``cliff.toml`` it reads, so a leaf kind
never restates them. A package's ``kind`` never names it: the
vocabulary a contract may use is the concrete kinds.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from livery.footman.api import fail

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
from livery.workshop._categories import (
    register_categories,
)

if TYPE_CHECKING:
    from pathlib import Path

    from livery.workshop._packages import Neighbours, Package
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

    def declare_requirement(
        self, package: Package, dependency: Package, floor: str
    ) -> list[str]:
        """Write *dependency* at *floor* into the package's native manifest.

        The files changed, relative to the package; empty when the
        manifest already names the dependency. Which file that is, and
        how a requirement is spelled there, is the kind's knowledge:
        the layering check's fix mode calls this for a sibling the
        graph already reaches, at the floor the graph carries.
        """
        ...

    def module_roots(self, package: Package) -> tuple[str, ...]:
        """The names other packages reference this one's code by."""
        ...

    def public_modules(self, package: Package) -> tuple[str, ...]:
        """The modules that declare the package's public API, by import path.

        What a type-completeness check verifies; empty for a package
        whose kind has no importable API.
        """
        ...

    def referenced_siblings(
        self, package: Package, around: Neighbours
    ) -> dict[str, str]:
        """Which siblings the package's sources use, by area, unaccounted for.

        The area is ``src`` or ``tests``, which decides a runtime edge
        from a test one. A reference the kind's own conventions
        already account for is left out, so the caller judges only
        what nothing explains.
        """
        ...

    def gate_build(self, package: Package, root: Path) -> None:
        """Build what the kind's tests run on, into the gate's build directory.

        Incremental, so a rebuild after one edit costs that edit. A
        kind whose tests run on source does nothing here, and its
        record says so in ``tests_need_build``.
        """
        ...

    def test(
        self,
        package: Package,
        root: Path,
        *,
        selection: tuple[str, ...] = (),
    ) -> None:
        """Run the kind's tests of *package*; a refusal is the verdict.

        Every test, or with *selection* the tests of the named files
        alone, relative to the package. A selection the kind cannot
        map to a test is a refusal, never a silent empty run.
        """
        ...


#: The private-members policies an extractor applies: ``public``
#: documents what its handler's default filter keeps, ``all`` every
#: member. The docs assembly composes the policy from the extensions.
PUBLIC_MEMBERS = "public"
ALL_MEMBERS = "all"
MEMBERS_POLICIES = (PUBLIC_MEMBERS, ALL_MEMBERS)


@dataclass(frozen=True)
class Extractor:
    """How a kind's API reference is extracted for the site.

    Extraction belongs to the kind: the docs assembly asks a
    package's kind for its extractor and knows nothing of the
    language behind it. A kind without one gets a section naming the
    absence.

    Attributes:
        name: The mkdocstrings handler's name, `python`; one config
            block is written per name, over every package that
            extracts through it.
        pages: The reference pages of a package, `(page path, dotted
            name)` per page, in the order the section lists them;
            empty when the package declines or has nothing to
            extract.
        sources: The handler's search paths for a package, relative
            to the workspace root.
        options: The handler's options, by name, which the site's
            assembly renders as the handler's options table; the
            members policy, one of `MEMBERS_POLICIES`, is applied
            beside them by the assembly.
        inventories: The inventories cross-references resolve
            against.
    """

    name: str
    pages: Callable[[Package], list[tuple[str, str]]]
    sources: Callable[[Package], list[str]]
    options: dict[str, object] = field(default_factory=dict)
    inventories: tuple[str, ...] = ()


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
        tools: The tools the kind requires by existing in a
            workspace, each spelled `name` or `name>=floor`; the
            lock resolves them beside the packages' and the
            project's own.
        host_tools: What the host must already provide and no cache
            will ever install (a C compiler); ``fm doctor`` and
            ``fm env.check`` name an absence instead of letting a
            build fail midway.
        host_allowed: The kind's tools whose copy already on the
            machine may serve when it satisfies the floor, so a
            workspace of this kind installs them only where the
            machine has none; the union along the chain joins the
            root contract's `[tools] host-allowed`.
        artifact: Which registry kind the release wave publishes
            through (``python`` or ``conan``); empty for a kind
            that publishes nothing.
        wheel_identity: What a built wheel's tag must say:
            ``pure`` refuses a platform tag, ``platform`` refuses
            ``none-any``, empty skips the guard (no wheels).
        tests_need_build: Whether the kind's tests run on a build
            rather than on source, so a test step is preceded by the
            backend's ``gate_build``.
        native_sources: Whether the kind's members carry C or C++ the
            gate formats with clang-format, against the member's own
            `.clang-format`.
        extractor: How the kind's API reference is extracted for the
            site; None for a kind with no reference, which the site
            says by name. A child kind takes the nearest ancestor's.
        examples: How the kind runs a package's documentation
            examples, the files under ``docs/examples/``, given the
            package, the workspace root and the named example files,
            empty for all of them; None for a kind that runs
            none, which the examples check says by name. A child kind
            takes the nearest ancestor's.
        coverage_pages: How the kind renders the coverage report
            pages of its packages for the site, given the workspace
            root and the packages that declare one; returns the names
            of the packages it rendered. None for a kind with no such
            pages. A child kind takes the nearest ancestor's.
        abstract: Whether the kind exists for its children alone: it
            heads their chains with its tools and seed tree, builds
            nothing, and is never a package's ``kind``. A concrete
            kind needs a backend; an abstract one has none.
    """

    name: str
    backend: Backend | None = None
    template: str = ""
    parent: str = ""
    tools: tuple[str, ...] = ()
    host_tools: tuple[str, ...] = ()
    host_allowed: tuple[str, ...] = ()
    artifact: str = "python"
    wheel_identity: str = "pure"
    tests_need_build: bool = False
    native_sources: bool = False
    extractor: Extractor | None = None
    examples: Callable[[Package, Path, tuple[str, ...]], None] | None = None
    coverage_pages: Callable[[Path, tuple[Package, ...]], list[str]] | None = None
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


def public_modules(package: Package) -> tuple[str, ...]:
    """The modules that declare *package*'s public API, as its kind reads them.

    What a type-completeness check verifies, by import path. A kind
    with no importable API answers nothing.
    """
    return backend_for(package).public_modules(package)


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


def kind_tools(present_types: set[str]) -> tuple[str, ...]:
    """The union of tool requirements the present kinds declare, sorted."""
    tools: set[str] = set()
    for kind_name in present_types:
        for record in kind_chain(kind_name):
            tools.update(record.tools)
    return tuple(sorted(tools))


def kind_host_tools(present_types: set[str]) -> tuple[str, ...]:
    """The union of host requirements the present kinds name, sorted."""
    tools: set[str] = set()
    for kind_name in present_types:
        for record in kind_chain(kind_name):
            tools.update(record.host_tools)
    return tuple(sorted(tools))


def kind_extractor(kind_name: str) -> Extractor | None:
    """The extractor *kind_name* uses: its own, else its nearest ancestor's."""
    for record in reversed(kind_chain(kind_name)):
        if record.extractor is not None:
            return record.extractor
    return None


def kind_examples(
    kind_name: str,
) -> Callable[[Package, Path, tuple[str, ...]], None] | None:
    """The examples runner *kind_name* uses: its own, else its nearest ancestor's."""
    for record in reversed(kind_chain(kind_name)):
        if record.examples is not None:
            return record.examples
    return None


def kind_coverage_pages(
    kind_name: str,
) -> Callable[[Path, tuple[Package, ...]], list[str]] | None:
    """The coverage page renderer of *kind_name*, else its nearest ancestor's."""
    for record in reversed(kind_chain(kind_name)):
        if record.coverage_pages is not None:
            return record.coverage_pages
    return None


def kind_host_allowed(present_types: set[str]) -> tuple[str, ...]:
    """The union of host allowances the present kinds grant, sorted."""
    tools: set[str] = set()
    for kind_name in present_types:
        for record in kind_chain(kind_name):
            tools.update(record.host_allowed)
    return tuple(sorted(tools))


def is_python_kind(kind_name: str) -> bool:
    """Whether the kind is a Python distribution, by its chain.

    True when ``python`` sits anywhere in the chain: a child kind (a
    binary extension) is still a wheel with a ``pyproject.toml``,
    while a kind outside the chain (``cpp-conan``) is not and never
    joins the uv workspace.
    """
    return any(record.name == "python" for record in kind_chain(kind_name))


def requires_pyproject(kind_name: str) -> bool:
    """Whether a package of this declared kind must carry a pyproject.

    An unregistered kind answers False so discovery can finish and
    the backend refusal can name the vocabulary; a missing file
    would otherwise mask the real problem, the typo.
    """
    if kind_name not in _KINDS:
        return False
    return is_python_kind(kind_name)


def record_for_template(template_kind: str) -> KindRecord | None:
    """The record whose template is *template_kind*; None when unmapped.

    A template variant (``package-extension``) maps to no record
    and the caller falls back to the python wiring.
    """
    for record in _KINDS.values():
        if record.template == template_kind:
            return record
    return None


def _register_builtin() -> None:
    from livery.workshop._backends import _cpp_conan, _python, _python_nanobind

    # The base every kind derives from: abstract, the record behind the
    # package-base template. Every kind releases, so the changelog
    # engine and the cliff.toml it reads are declared once, here, and
    # a kind an extension adds gets them by naming its parent. A tool is
    # named as its record and its handle are (`git_cliff`), which is
    # how the catalogue lists it.
    register_kind(
        KindRecord(
            name="base",
            template=BASE_TEMPLATE,
            tools=("git_cliff",),
            artifact="",
            wheel_identity="",
            abstract=True,
        )
    )
    # Two contract kinds exist today. The extension package template
    # (package-extension) is a template variant of python, not
    # a contract kind of its own: every member declares "python".
    # The python kind's tools: uv makes the venv the checkers run in,
    # and the checkers, the formatter and the test runner are what the
    # gate runs on every python package. Declared here as data, so a
    # workspace's lock takes them from the kind like any other
    # requirement and no branch in code knows the list.
    register_kind(
        KindRecord(
            name="python",
            backend=_python,
            template="package-python",
            parent="base",
            # uv operates the workspace; the checkers and the formatter
            # ride their check records, and pytest the dev group's slot.
            tools=("uv",),
            extractor=_python.EXTRACTOR,
            examples=_python.run_examples,
            coverage_pages=_python.render_coverage_pages,
        )
    )
    # The binary extension: a python distribution in every checker's
    # eyes (the chain says so), built through cibuildwheel so the
    # wheel carries its platform tag. cmake, ninja, conan and the
    # cmake-conan provider join the profile; nanobind and
    # scikit-build-core arrive through the package's own build-system
    # requires, never the machine.
    register_kind(
        KindRecord(
            name="python-nanobind",
            backend=_python_nanobind,
            template="package-python-nanobind",
            parent="python",
            # The build tools; the native format and lint tools ride
            # their check records.
            tools=("cmake", "ninja", "conan", "cmake_conan"),
            native_sources=True,
            host_tools=("cc", "c++"),
            wheel_identity="platform",
        )
    )
    # The C/C++ library: cmake configures and builds, ctest is the
    # test check, conan packages the result. The python type checkers
    # do not gate it (there is no dist to verify types on); a python
    # formatter and linter judge its conanfile.py beside clang-format
    # and clang-tidy over its sources, and its build and ctest records
    # carry the build and test roles.
    register_kind(
        KindRecord(
            name="cpp-conan",
            backend=_cpp_conan,
            template="package-cpp-conan",
            parent="base",
            # Microsoft's coverage engine measures a run built with
            # MSVC, so it is required on the Windows hosts alone; the
            # .NET SDK it runs on rides as its runtime, there too.
            tools=("cmake", "conan", "ninja", "dotnet_coverage@windows"),
            native_sources=True,
            host_tools=("cc", "c++"),
            artifact="conan",
            wheel_identity="",
            tests_need_build=True,
        )
    )


def _register_categories() -> None:
    """The builtin category tables, one per kind that ships, and the root's."""
    register_categories(
        "base",
        [
            ("docs/**/*.md", "prose"),
            ("docs/nav.toml", "nav"),
            ("docs/assets/**", "asset"),
            ("docs/examples/**/*.py", EXAMPLE),
            ("docs/_generated/**", "generated"),
            ("**", CONFIGURATION),
        ],
    )
    register_categories(
        "python",
        [
            ("tests/**/test_*.py", TEST),
            ("tests/**/*_test.py", TEST),
            ("tests/**", TEST_SUPPORT),
            ("src/**", SOURCE),
        ],
    )
    register_categories(
        "cpp-conan",
        [
            ("tests/**/*.cpp", TEST),
            ("tests/**/*.cc", TEST),
            ("tests/**/*.cxx", TEST),
            ("tests/**/*.c", TEST),
            ("tests/**", TEST_SUPPORT),
            ("src/**", SOURCE),
            ("include/**", SOURCE),
        ],
    )
    # The workspace's own unit: its tests as the python kind reads
    # them, and the files beside them that only the site or nobody
    # reads.
    register_categories(
        WORKSPACE,
        [
            ("tests/**/test_*.py", TEST),
            ("tests/**/*_test.py", TEST),
            ("tests/**", TEST_SUPPORT),
            ("notes/**", "notes"),
            ("docs/**", "site"),
            ("zensical.toml", "site"),
            ("README.md", "readme"),
            ("**", CONFIGURATION),
        ],
    )


_register_builtin()
_register_categories()
