"""The livery ecosystem's devkit.

The task surface arrives through the footman plugin
(``plugin("livery.workshop")``); this module's own API is the extension
walk (livery.workshop.extension_names, livery.workshop.workspace_root),
the package contracts
(livery.workshop.discover_packages, livery.workshop.verify_workspace
over livery.workshop.Package and livery.workshop.Edge). The forge
lane belongs to
livery.forge.Forge; the workshop orchestrates local, git, and forge
steps and never hands a raw forge verb to a user.

An extension in its own wheel declares itself in an ``extension.toml``
beside the package its ``workshop.extensions`` entry point names: the
steps it adds to the gate's phases under ``[phases.<phase>.<step>]``,
each with its body under ``main``, a template of its tool's words
with ``{paths}`` where the paths the run reaches go and
``{arguments}`` where the words after ``--`` go, or the function that
runs it as ``"module:function"``. A step in code takes a
[livery.workshop.PhaseContext][]. One that narrows by paths reads them
from [livery.workshop.scoped_paths][], which answers
[livery.workshop.WHOLE][] for the tool's configured whole, and
calls its tool through [livery.workshop.run_batched][]; a workspace
step that declares no ``inputs`` narrows by packages and reads them
from [livery.workshop.scoped_packages][], and one that judges a
package at a time (``scope = "package"``) reads its files from
[livery.workshop.scoped_files][]. One that declares ``inputs`` judges
the whole workspace in every scope, and asks
[livery.workshop.selected_files][] which of them this run's change
touched, with the context its run was handed; its ``widen``
reference receives the run's [livery.workshop.Changes][]. A step
declares the options a package may set on it under its ``options``
and reads a package's value with [livery.workshop.step_option][]; a
step declared ``enabled = false`` registers once the root contract
turns it on at the step's address. A package's
public modules are its extensions' answer,
[livery.workshop.public_modules][], and so is where its build
writes a compilation database, [livery.workshop.compile_commands][].
The workspace's own tests, the ``tests`` directory at its root, are a
unit of their own, [livery.workshop.workspace_suite][], which a test
step runs beside the packages it judges.

An extension reads the workspace through the names the engine reads
it by: a contract's judged keys, [livery.workshop.read_contract][];
a query's answer, [livery.workshop.answer][], asked with a package or
the workspace root through a [livery.workshop.Query][] handle; the CI
run it belongs to, [livery.workshop.ci_run][], a
[livery.workshop.RunContext][] or None at a desk; the workspace's
repository on its forge, [livery.workshop.forge_repository][], and,
connected, [livery.workshop.this_repository][] on
[livery.workshop.this_forge][], whose everyday token
[livery.workshop.forge_token][] resolves; the
registry an artifact kind goes to, [livery.workshop.registry][], a
[livery.workshop.RegistryTarget][]; and the release notes' provider
the listed extensions answer, [livery.workshop.release_notes][], which
answers the [livery.workshop.ReleaseNotes][] protocol. A file an extension
writes outside the engine opens with
[livery.workshop.generated_header][]. What a CI run changed
against the base it measures from is [livery.workshop.ci_changes][].
The guidance for one reader, [livery.workshop.AGENT][] or
[livery.workshop.HUMAN][], is the composed set of
[livery.workshop.Prose][] fragments [livery.workshop.guidance][]
answers; each listed extension's shipped files live where
[livery.workshop.shipped_content][] says; the gate's fixers run over
named files through [livery.workshop.fix_files][], and a phase's steps
over named packages through [livery.workshop.run_phase_over][]; and
what the listed extensions declare for an extension is
[livery.workshop.contributions_for][].

A package-level extension adds steps to a package's lifecycle phases
under ``[phases.<phase>.<step>]``, with the keys every step takes;
each runs once per package whose set holds the extension and
receives the same [livery.workshop.PhaseContext][], which carries the
keys the steps provide and read. It
answers questions about a package under ``[queries]``: each a
[livery.workshop.Query][] the workshop defines, such as
[livery.workshop.PUBLIC_MODULES][] or [livery.workshop.EXECUTABLES][],
which [livery.workshop.answer][] asks of a package's extensions and
combines by the query's rule.
"""

from __future__ import annotations

# The literal-False spelling the checkers honour without importing
# `typing`: the names below are what a checker sees, and at runtime
# `__getattr__` serves each from its module on first use, so importing
# one private module of the workshop never loads the rest.
TYPE_CHECKING = False
if TYPE_CHECKING:
    from livery.workshop import testing as testing
    from livery.workshop._ci_context import RunContext as RunContext
    from livery.workshop._ci_context import ci_run as ci_run
    from livery.workshop._contract import read_contract as read_contract
    from livery.workshop._extensions import contributions_for as contributions_for
    from livery.workshop._extensions import extension_names as extension_names
    from livery.workshop._extensions import shipped_content as shipped_content
    from livery.workshop._extensions import workspace_root as workspace_root
    from livery.workshop._forge_lane import forge_repository as forge_repository
    from livery.workshop._forge_lane import this_forge as this_forge
    from livery.workshop._forge_lane import this_repository as this_repository
    from livery.workshop._influence import Changes as Changes
    from livery.workshop._invoke import run_batched as run_batched
    from livery.workshop._lifecycle import compile_commands as compile_commands
    from livery.workshop._lifecycle import public_modules as public_modules
    from livery.workshop._packages import Edge as Edge
    from livery.workshop._packages import Package as Package
    from livery.workshop._packages import discover_packages as discover_packages
    from livery.workshop._packages import verify_workspace as verify_workspace
    from livery.workshop._packages import workspace_suite as workspace_suite
    from livery.workshop._phases import PhaseContext as PhaseContext
    from livery.workshop._prose import AGENT as AGENT
    from livery.workshop._prose import HUMAN as HUMAN
    from livery.workshop._prose import Prose as Prose
    from livery.workshop._prose import guidance as guidance
    from livery.workshop._provenance import generated_header as generated_header
    from livery.workshop._quality import ci_changes as ci_changes
    from livery.workshop._quality import fix_files as fix_files
    from livery.workshop._quality import run_phase_over as run_phase_over
    from livery.workshop._queries import COMPILE_COMMANDS as COMPILE_COMMANDS
    from livery.workshop._queries import CURRENT_VERSION as CURRENT_VERSION
    from livery.workshop._queries import DISTRIBUTIONS as DISTRIBUTIONS
    from livery.workshop._queries import EXECUTABLES as EXECUTABLES
    from livery.workshop._queries import MODULE_ROOTS as MODULE_ROOTS
    from livery.workshop._queries import PUBLIC_MODULES as PUBLIC_MODULES
    from livery.workshop._queries import REQUIREMENTS as REQUIREMENTS
    from livery.workshop._queries import VERSION_FILES as VERSION_FILES
    from livery.workshop._queries import Query as Query
    from livery.workshop._queries import answer as answer
    from livery.workshop._registries import RegistryTarget as RegistryTarget
    from livery.workshop._registries import registry as registry
    from livery.workshop._release_notes import ReleaseNotes as ReleaseNotes
    from livery.workshop._release_notes import release_notes as release_notes
    from livery.workshop._steps import NONE as NONE
    from livery.workshop._steps import PACKAGE as PACKAGE
    from livery.workshop._steps import PACKAGES as PACKAGES
    from livery.workshop._steps import PATHS as PATHS
    from livery.workshop._steps import WHOLE as WHOLE
    from livery.workshop._steps import scoped_files as scoped_files
    from livery.workshop._steps import scoped_packages as scoped_packages
    from livery.workshop._steps import scoped_paths as scoped_paths
    from livery.workshop._steps import selected_files as selected_files
    from livery.workshop._steps import step_option as step_option
    from livery.workshop._tokens import forge_token as forge_token

__version__ = "0.9.0"

__all__ = [
    "AGENT",
    "COMPILE_COMMANDS",
    "CURRENT_VERSION",
    "DISTRIBUTIONS",
    "EXECUTABLES",
    "HUMAN",
    "MODULE_ROOTS",
    "NONE",
    "PACKAGE",
    "PACKAGES",
    "PATHS",
    "PUBLIC_MODULES",
    "REQUIREMENTS",
    "VERSION_FILES",
    "WHOLE",
    "Changes",
    "Edge",
    "Package",
    "PhaseContext",
    "Prose",
    "Query",
    "RegistryTarget",
    "ReleaseNotes",
    "RunContext",
    "__version__",
    "answer",
    "ci_changes",
    "ci_run",
    "compile_commands",
    "contributions_for",
    "discover_packages",
    "extension_names",
    "fix_files",
    "forge_repository",
    "forge_token",
    "generated_header",
    "guidance",
    "public_modules",
    "read_contract",
    "registry",
    "release_notes",
    "run_batched",
    "run_phase_over",
    "scoped_files",
    "scoped_packages",
    "scoped_paths",
    "selected_files",
    "shipped_content",
    "step_option",
    "testing",
    "this_forge",
    "this_repository",
    "verify_workspace",
    "workspace_root",
    "workspace_suite",
]

# The module each lazily served name lives in.
_EXPORTS: dict[str, str] = {
    "COMPILE_COMMANDS": "livery.workshop._queries",
    "CURRENT_VERSION": "livery.workshop._queries",
    "DISTRIBUTIONS": "livery.workshop._queries",
    "EXECUTABLES": "livery.workshop._queries",
    "MODULE_ROOTS": "livery.workshop._queries",
    "PUBLIC_MODULES": "livery.workshop._queries",
    "REQUIREMENTS": "livery.workshop._queries",
    "VERSION_FILES": "livery.workshop._queries",
    "Query": "livery.workshop._queries",
    "answer": "livery.workshop._queries",
    "AGENT": "livery.workshop._prose",
    "Changes": "livery.workshop._influence",
    "Edge": "livery.workshop._packages",
    "HUMAN": "livery.workshop._prose",
    "NONE": "livery.workshop._steps",
    "PACKAGE": "livery.workshop._steps",
    "PACKAGES": "livery.workshop._steps",
    "PATHS": "livery.workshop._steps",
    "Package": "livery.workshop._packages",
    "PhaseContext": "livery.workshop._phases",
    "Prose": "livery.workshop._prose",
    "RegistryTarget": "livery.workshop._registries",
    "ReleaseNotes": "livery.workshop._release_notes",
    "RunContext": "livery.workshop._ci_context",
    "WHOLE": "livery.workshop._steps",
    "ci_changes": "livery.workshop._quality",
    "ci_run": "livery.workshop._ci_context",
    "compile_commands": "livery.workshop._lifecycle",
    "contributions_for": "livery.workshop._extensions",
    "discover_packages": "livery.workshop._packages",
    "extension_names": "livery.workshop._extensions",
    "forge_repository": "livery.workshop._forge_lane",
    "this_forge": "livery.workshop._forge_lane",
    "this_repository": "livery.workshop._forge_lane",
    "forge_token": "livery.workshop._tokens",
    "fix_files": "livery.workshop._quality",
    "shipped_content": "livery.workshop._extensions",
    "generated_header": "livery.workshop._provenance",
    "guidance": "livery.workshop._prose",
    "public_modules": "livery.workshop._lifecycle",
    "read_contract": "livery.workshop._contract",
    "registry": "livery.workshop._registries",
    "release_notes": "livery.workshop._release_notes",
    "run_batched": "livery.workshop._invoke",
    "run_phase_over": "livery.workshop._quality",
    "scoped_files": "livery.workshop._steps",
    "scoped_packages": "livery.workshop._steps",
    "scoped_paths": "livery.workshop._steps",
    "selected_files": "livery.workshop._steps",
    "step_option": "livery.workshop._steps",
    "verify_workspace": "livery.workshop._packages",
    "workspace_root": "livery.workshop._extensions",
    "workspace_suite": "livery.workshop._packages",
}

#: The public packages beneath the root, served whole on first use.
_PACKAGES = ("testing",)


def __getattr__(name: str) -> object:
    """Serve a public name from its module on first use, then keep it."""
    import importlib

    if name in _PACKAGES:
        value: object = importlib.import_module(f"livery.workshop.{name}")
    else:
        module = _EXPORTS.get(name)
        if module is None:
            raise AttributeError(f"module 'livery.workshop' has no attribute {name!r}")
        value = getattr(importlib.import_module(module), name)
    globals()[name] = value
    return value
