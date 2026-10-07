"""The livery ecosystem's devkit.

The task surface arrives through the footman plugin
(``plugin("livery.workshop")``); this module's own API is the extension
walk (livery.workshop.extension_names, livery.workshop.workspace_root),
the package contracts
(livery.workshop.discover_packages, livery.workshop.verify_workspace
over livery.workshop.Package and livery.workshop.Edge). A package's
docs generator writes its nav block through the docs extension's
own names, [livery.extensions.docs.write_nav_block][]. The forge
lane belongs to
livery.forge.Forge; the workshop orchestrates local, git, and forge
steps and never hands a raw forge verb to a user.

An extension in its own wheel declares itself in an ``extension.toml``
beside the package its ``workshop.extensions`` entry point names: its
checks under ``[checks.<tool>.<role>]``, each naming the function that
runs it as ``"module:function"``, and the options a workspace may list
it with under ``[options]``. A check's body takes a
[livery.workshop.GateContext][]. A check that narrows by paths
(``narrowing = "paths"``) reads them from
[livery.workshop.scoped_paths][], which answers
[livery.workshop.WHOLE][] for the tool's configured whole, and
calls its tool through [livery.workshop.run_batched][]; one that
narrows by packages (``narrowing = "packages"``) reads them from
[livery.workshop.scoped_packages][], and one that judges a package
at a time (``scope = "package"``) reads its files from
[livery.workshop.scoped_files][]. One that narrows by neither
(``narrowing = "none"``, [livery.workshop.NONE][]) judges the whole
workspace in every scope, and when it declares ``inputs`` it asks
[livery.workshop.selected_files][] which of them this run's change
touched, with the context its run was handed; its ``widen``
reference receives the run's [livery.workshop.Changes][]. A check
declares the options a
package may set on it under the check's ``options`` and reads a
package's value with [livery.workshop.check_option][]. A package's
public modules are its kind's answer,
[livery.workshop.public_modules][], and so is where its build
writes a compilation database, [livery.workshop.compile_commands][].
A kind also runs tests: the suites of several packages in one call,
[livery.workshop.run_suites][], the workspace's own tests among
them as [livery.workshop.workspace_suite][], and a package's
documentation examples, [livery.workshop.kind_examples][].

An extension reads the workspace through the names the engine reads
it by: a contract's judged keys, [livery.workshop.read_contract][];
a slot's composed value, [livery.workshop.slot][]; the CI run it
belongs to, [livery.workshop.ci_run][], a
[livery.workshop.RunContext][] or None at a desk; the workspace's
repository on its forge, [livery.workshop.forge_repository][]; the
registry an artifact kind goes to, [livery.workshop.registry][], a
[livery.workshop.RegistryTarget][]; and the mounted release-notes
provider, [livery.workshop.release_notes][], which answers the
[livery.workshop.ReleaseNotes][] protocol. A file an extension
writes outside the engine opens with
[livery.workshop.generated_header][]. What a CI run changed
against the base it measures from is [livery.workshop.ci_changes][].
The guidance for one reader, [livery.workshop.AGENT][] or
[livery.workshop.HUMAN][], is the composed set of
[livery.workshop.Prose][] fragments [livery.workshop.guidance][]
answers; and what the listed extensions declare for an extension
is [livery.workshop.contributions_for][].
"""

from __future__ import annotations

# The literal-False spelling the checkers honour without importing
# `typing`: the names below are what a checker sees, and at runtime
# `__getattr__` serves each from its module on first use, so importing
# one private module of the workshop never loads the rest.
TYPE_CHECKING = False
if TYPE_CHECKING:
    from livery.workshop import testing as testing
    from livery.workshop._checks import NONE as NONE
    from livery.workshop._checks import PACKAGE as PACKAGE
    from livery.workshop._checks import PACKAGES as PACKAGES
    from livery.workshop._checks import PATHS as PATHS
    from livery.workshop._checks import WHOLE as WHOLE
    from livery.workshop._checks import GateContext as GateContext
    from livery.workshop._checks import check_option as check_option
    from livery.workshop._checks import scoped_files as scoped_files
    from livery.workshop._checks import scoped_packages as scoped_packages
    from livery.workshop._checks import scoped_paths as scoped_paths
    from livery.workshop._checks import selected_files as selected_files
    from livery.workshop._contract import read_contract as read_contract
    from livery.workshop._coverage_store import workspace_suite as workspace_suite
    from livery.workshop._extensions import contributions_for as contributions_for
    from livery.workshop._extensions import extension_names as extension_names
    from livery.workshop._extensions import workspace_root as workspace_root
    from livery.workshop._forge_lane import forge_repository as forge_repository
    from livery.workshop._influence import Changes as Changes
    from livery.workshop._invoke import run_batched as run_batched
    from livery.workshop._kinds import compile_commands as compile_commands
    from livery.workshop._kinds import kind_examples as kind_examples
    from livery.workshop._kinds import public_modules as public_modules
    from livery.workshop._kinds import run_suites as run_suites
    from livery.workshop._packages import Edge as Edge
    from livery.workshop._packages import Package as Package
    from livery.workshop._packages import discover_packages as discover_packages
    from livery.workshop._packages import verify_workspace as verify_workspace
    from livery.workshop._prose import AGENT as AGENT
    from livery.workshop._prose import HUMAN as HUMAN
    from livery.workshop._prose import Prose as Prose
    from livery.workshop._prose import guidance as guidance
    from livery.workshop._provenance import generated_header as generated_header
    from livery.workshop._quality import ci_changes as ci_changes
    from livery.workshop._registries import RegistryTarget as RegistryTarget
    from livery.workshop._registries import registry as registry
    from livery.workshop._release_notes import ReleaseNotes as ReleaseNotes
    from livery.workshop._release_notes import release_notes as release_notes
    from livery.workshop._slots import slot as slot
    from livery.workshop._state import RunContext as RunContext
    from livery.workshop._state import ci_run as ci_run

__version__ = "0.6.0"

__all__ = [
    "AGENT",
    "HUMAN",
    "NONE",
    "PACKAGE",
    "PACKAGES",
    "PATHS",
    "WHOLE",
    "Changes",
    "Edge",
    "GateContext",
    "Package",
    "Prose",
    "RegistryTarget",
    "ReleaseNotes",
    "RunContext",
    "__version__",
    "check_option",
    "ci_changes",
    "ci_run",
    "compile_commands",
    "contributions_for",
    "discover_packages",
    "extension_names",
    "forge_repository",
    "generated_header",
    "guidance",
    "kind_examples",
    "public_modules",
    "read_contract",
    "registry",
    "release_notes",
    "run_batched",
    "run_suites",
    "scoped_files",
    "scoped_packages",
    "scoped_paths",
    "selected_files",
    "slot",
    "testing",
    "verify_workspace",
    "workspace_root",
    "workspace_suite",
]

# The module each lazily served name lives in.
_EXPORTS: dict[str, str] = {
    "AGENT": "livery.workshop._prose",
    "Changes": "livery.workshop._influence",
    "Edge": "livery.workshop._packages",
    "GateContext": "livery.workshop._checks",
    "HUMAN": "livery.workshop._prose",
    "NONE": "livery.workshop._checks",
    "PACKAGE": "livery.workshop._checks",
    "PACKAGES": "livery.workshop._checks",
    "PATHS": "livery.workshop._checks",
    "Package": "livery.workshop._packages",
    "Prose": "livery.workshop._prose",
    "RegistryTarget": "livery.workshop._registries",
    "ReleaseNotes": "livery.workshop._release_notes",
    "RunContext": "livery.workshop._state",
    "WHOLE": "livery.workshop._checks",
    "check_option": "livery.workshop._checks",
    "ci_changes": "livery.workshop._quality",
    "ci_run": "livery.workshop._state",
    "compile_commands": "livery.workshop._kinds",
    "contributions_for": "livery.workshop._extensions",
    "discover_packages": "livery.workshop._packages",
    "extension_names": "livery.workshop._extensions",
    "forge_repository": "livery.workshop._forge_lane",
    "generated_header": "livery.workshop._provenance",
    "guidance": "livery.workshop._prose",
    "kind_examples": "livery.workshop._kinds",
    "public_modules": "livery.workshop._kinds",
    "read_contract": "livery.workshop._contract",
    "registry": "livery.workshop._registries",
    "release_notes": "livery.workshop._release_notes",
    "run_batched": "livery.workshop._invoke",
    "run_suites": "livery.workshop._kinds",
    "scoped_files": "livery.workshop._checks",
    "scoped_packages": "livery.workshop._checks",
    "scoped_paths": "livery.workshop._checks",
    "selected_files": "livery.workshop._checks",
    "slot": "livery.workshop._slots",
    "verify_workspace": "livery.workshop._packages",
    "workspace_root": "livery.workshop._extensions",
    "workspace_suite": "livery.workshop._coverage_store",
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
