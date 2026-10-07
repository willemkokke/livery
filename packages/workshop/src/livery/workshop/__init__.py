"""The livery ecosystem's devkit.

The task surface arrives through the footman plugin
(``plugin("livery.workshop")``); this module's own API is the extension
walk (livery.workshop.extension_names, livery.workshop.mount_extensions,
livery.workshop.workspace_root), the package contracts
(livery.workshop.discover_packages, livery.workshop.verify_workspace
over livery.workshop.Package and livery.workshop.Edge), and the one
helper a package's docs generator needs
(livery.workshop.rewrite_nav_block). The forge lane belongs to
livery.forge.Forge; the workshop orchestrates local, git, and forge
steps and never hands a raw forge verb to a user.

An extension in its own wheel declares its checks with
[livery.workshop.CheckRecord][], its claims with
[livery.workshop.Claim][] and the files it manages with
[livery.workshop.Fragment][], in a ``CHECKS`` tuple of its declaring
module, and the options a workspace may list it with in an ``OPTIONS``
map. A check's body takes a [livery.workshop.GateContext][]. A
check that narrows by paths (``narrowing=PATHS``) reads them from
[livery.workshop.scoped_paths][], which answers
[livery.workshop.WHOLE][] for the tool's configured whole, and
calls its tool through [livery.workshop.run_batched][]; one that
narrows by packages (``narrowing=PACKAGES``) reads them from
[livery.workshop.scoped_packages][], and one that judges a package
at a time (``scope=PACKAGE``) reads its files from
[livery.workshop.scoped_files][]. A check declares the options a
package may set on it with [livery.workshop.Option][] and reads a
package's value with [livery.workshop.check_option][]. A package's
public modules are its kind's answer,
[livery.workshop.public_modules][], and so is where its build
writes a compilation database, [livery.workshop.compile_commands][].
A kind also runs tests: the suites of several packages in one call,
[livery.workshop.run_suites][], the workspace's own tests among
them as [livery.workshop.workspace_suite][], and a package's
documentation examples, [livery.workshop.kind_examples][].
"""

from __future__ import annotations

# The literal-False spelling the checkers honour without importing
# `typing`: the names below are what a checker sees, and at runtime
# `__getattr__` serves each from its module on first use, so importing
# one private module of the workshop never loads the rest.
TYPE_CHECKING = False
if TYPE_CHECKING:
    from livery.workshop import testing as testing
    from livery.workshop._checks import PACKAGE as PACKAGE
    from livery.workshop._checks import PACKAGES as PACKAGES
    from livery.workshop._checks import PATHS as PATHS
    from livery.workshop._checks import WHOLE as WHOLE
    from livery.workshop._checks import CheckRecord as CheckRecord
    from livery.workshop._checks import Claim as Claim
    from livery.workshop._checks import GateContext as GateContext
    from livery.workshop._checks import Option as Option
    from livery.workshop._checks import check_option as check_option
    from livery.workshop._checks import scoped_files as scoped_files
    from livery.workshop._checks import scoped_packages as scoped_packages
    from livery.workshop._checks import scoped_paths as scoped_paths
    from livery.workshop._coverage_store import workspace_suite as workspace_suite
    from livery.workshop._extensions import extension_names as extension_names
    from livery.workshop._extensions import mount_extensions as mount_extensions
    from livery.workshop._extensions import workspace_root as workspace_root
    from livery.workshop._fragments import Fragment as Fragment
    from livery.workshop._invoke import run_batched as run_batched
    from livery.workshop._kinds import compile_commands as compile_commands
    from livery.workshop._kinds import kind_examples as kind_examples
    from livery.workshop._kinds import public_modules as public_modules
    from livery.workshop._kinds import run_suites as run_suites
    from livery.workshop._navblocks import rewrite_nav_block as rewrite_nav_block
    from livery.workshop._packages import Edge as Edge
    from livery.workshop._packages import Package as Package
    from livery.workshop._packages import discover_packages as discover_packages
    from livery.workshop._packages import verify_workspace as verify_workspace

__version__ = "0.5.0"

__all__ = [
    "PACKAGE",
    "PACKAGES",
    "PATHS",
    "WHOLE",
    "CheckRecord",
    "Claim",
    "Edge",
    "Fragment",
    "GateContext",
    "Option",
    "Package",
    "__version__",
    "check_option",
    "compile_commands",
    "discover_packages",
    "extension_names",
    "kind_examples",
    "mount_extensions",
    "public_modules",
    "rewrite_nav_block",
    "run_batched",
    "run_suites",
    "scoped_files",
    "scoped_packages",
    "scoped_paths",
    "testing",
    "verify_workspace",
    "workspace_root",
    "workspace_suite",
]

# The module each lazily served name lives in.
_EXPORTS: dict[str, str] = {
    "CheckRecord": "livery.workshop._checks",
    "Claim": "livery.workshop._checks",
    "Edge": "livery.workshop._packages",
    "Fragment": "livery.workshop._fragments",
    "GateContext": "livery.workshop._checks",
    "Option": "livery.workshop._checks",
    "PACKAGE": "livery.workshop._checks",
    "PACKAGES": "livery.workshop._checks",
    "PATHS": "livery.workshop._checks",
    "Package": "livery.workshop._packages",
    "WHOLE": "livery.workshop._checks",
    "check_option": "livery.workshop._checks",
    "compile_commands": "livery.workshop._kinds",
    "discover_packages": "livery.workshop._packages",
    "extension_names": "livery.workshop._extensions",
    "kind_examples": "livery.workshop._kinds",
    "mount_extensions": "livery.workshop._extensions",
    "public_modules": "livery.workshop._kinds",
    "rewrite_nav_block": "livery.workshop._navblocks",
    "run_batched": "livery.workshop._invoke",
    "run_suites": "livery.workshop._kinds",
    "scoped_files": "livery.workshop._checks",
    "scoped_packages": "livery.workshop._checks",
    "scoped_paths": "livery.workshop._checks",
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
