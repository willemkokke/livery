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
and reads a package's value with [livery.workshop.step_option][],
which answers from the installed declaration in a process that
mounted nothing. A step names the extensions it judges packages by,
[livery.workshop.step_extensions][], and judges a package whose set
holds one of them: the extensions it lists and every one they
require, [livery.workshop.extension_set][]. A step declared
``enabled = false`` registers once the root contract turns it on at
the step's address, which [livery.workshop.step_enabled][] answers.
A step asks a package's extensions
what it needs of the package with [livery.workshop.answer][]: the
modules a type-completeness step verifies,
[livery.workshop.PUBLIC_MODULES][], or where the package's build
writes the compilation database a linter reads,
[livery.workshop.COMPILE_COMMANDS][]. The workspace's own tests, the
``tests`` directory at its root, are a unit of their own,
[livery.workshop.workspace_suite][], which holds the root's set. A
test step runs them beside the packages it judges while that set
holds an extension the step names: [livery.workshop.scoped_suite][]
returns the unit then. A step whose tool of the store is not on
``PATH`` refuses with [livery.workshop.undeployed][]: the refusal
names the tool and the sync that deploys it.

An extension reads the workspace through the names the engine reads
it by: the root of the workspace a package is a member of,
[livery.workshop.Package.root][]; a contract's judged keys,
[livery.workshop.read_contract][]; a query's answer,
[livery.workshop.answer][], asked with a package or
the workspace root through a [livery.workshop.Query][] handle; the CI
run it belongs to, [livery.workshop.ci_run][], a
[livery.workshop.RunContext][] or None at a desk; the workspace's
repository on its forge, [livery.workshop.forge_repository][], and,
connected, [livery.workshop.this_repository][] on
[livery.workshop.this_forge][], whose everyday token
[livery.workshop.forge_token][] resolves; the
registry an artifact kind goes to, [livery.workshop.registry][], a
[livery.workshop.RegistryTarget][], which a step of the ``publish``
phase reads from its ``registry-target`` key with
[livery.workshop.RegistryTarget.from_table][]; and the release
notes' provider the listed extensions answer,
[livery.workshop.release_notes][], which
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
[livery.workshop.contributions_for][]. A phase the gate never runs,
one declared ``gated = false``, runs over the workspace through
[livery.workshop.run_phase][], which first removes what a step that
stopped running declared it writes under ``outputs``. The members come
in release order from [livery.workshop.order_topologically][], every
dependency before its dependents, and [livery.workshop.release_tag][]
reads the member and the version a release tag names. A query's
combine function that decides between answers by what each answering
extension requires hands the lookup it is called with to
[livery.workshop.nearest_extensions][].

An extension that keeps records of a CI run reaches them through the
names the gate's own records use. The workshop's series live on the
checkout's state store, [livery.workshop.state_store][]; on it,
[livery.workshop.RUNS][] keeps each check leg's files for one run
until the gate job collects them. A check leg's entry leaves a file
for the leg's one write with [livery.workshop.leave_leg_file][], and
reads what the gate ran on the leg, a [livery.workshop.LegScope][],
from [livery.workshop.leg_scope][]. The gate job's entry leaves a
section of the run's row with [livery.workshop.leave_section][]. The
branch whose run proved the checkout's tree, a
[livery.workshop.VerifiedBranch][], is what
[livery.workshop.verified_branch][] reads. The check legs the gate produces
are [livery.workshop.check_legs][], and those a pull request's run
produces are [livery.workshop.pull_request_legs][], unless the pull
request carries [livery.workshop.EVERY_RUNNER_LABEL][], when its run
produces every leg; split into the
shards the contract gives each runner they are
[livery.workshop.check_shards][] and
[livery.workshop.pull_request_shards][], each shard spelled by a
[livery.workshop.Shard][] at the end of its label, which
[livery.workshop.platform_leg][] and [livery.workshop.shard_of][]
split. A shard's share of the test files is what
[livery.workshop.partition][] packs from the medians
[livery.workshop.read_durations][] takes over the platform leg's rows
in the [livery.workshop.DURATIONS][] family, bounded by
[livery.workshop.checkout_time][]; the set it partitioned has the
digest [livery.workshop.files_digest][], and the shard leaves what it
ran with [livery.workshop.leave_shard][], a
[livery.workshop.ShardMarker][] that [livery.workshop.read_shard][]
reads back. Of a set of
units, each a member's suite or the workspace's own tests at
[livery.workshop.WORKSPACE_TESTS][], [livery.workshop.tested][] keeps
those a test step runs. Whether a package holds the extension of the
workshop's own language is [livery.workshop.holds_runtime_language][],
and what a path is to its package, a [livery.workshop.Category][], is
[livery.workshop.category_of][].

An extension that builds a release's artifacts in a CI job of its own
reaches the release through the names the release train uses. The
members a release squash releases, each with its version, are what
[livery.workshop.discover_release][] reads at a commit, and
[livery.workshop.build_release][] builds one of them as the release
train does, into its ``dist/``. The members a job of the release point
builds before the wave are the workspace's answer to
[livery.workshop.JOB_BUILT_MEMBERS][]. The interpreters the CI matrix
runs are [livery.workshop.python_matrix][]. What the checkout
installed of each tool is [livery.workshop.tool_receipts][], a
[livery.workshop.ToolReceipt][] per tool, and the tool store's home on
this machine is [livery.workshop.store_home][].

An extension that judges a language's sources reads what each source
references from its package's ``references`` answer: one
[livery.workshop.SourceReferences][] per source, and a
[livery.workshop.ReferencedName][] for each name the source references
and how it makes the reference. Which member owns a referenced name is
what [livery.workshop.neighbours][] answers, a
[livery.workshop.Neighbours][] made from each member's module roots. A
step over the sources runs over every one of them when
[livery.workshop.graph_changed][] says a contract, a native manifest or
a root ``src/`` changed.

An extension that runs python in a venv names its interpreter with
[livery.workshop.venv_python][] and its executables directory with
[livery.workshop.scripts_dir][]. A runner that starts pytest shares two
names with the workshop's pytest plugins: [livery.workshop.is_example][]
says which files the examples plugin collects as documentation
examples, and the speed plugin sums each package's test time into the
file the runner names in the variable
[livery.workshop.SPEED_FILE_VARIABLE][]. Each package's mark on each
check leg, a [livery.workshop.SpeedMark][], is what
[livery.workshop.speed_marks][] reads.

An extension adds steps to a package's lifecycle phases under
``[phases.<phase>.<step>]``, with the keys every step takes;
each runs once per package whose set holds the extension and
receives the same [livery.workshop.PhaseContext][], which carries the
keys the steps provide and read. It
answers questions about a package under ``[queries]``: each a
[livery.workshop.Query][] the workshop defines, such as
[livery.workshop.PUBLIC_MODULES][] or [livery.workshop.EXECUTABLES][],
which [livery.workshop.answer][] asks of a package's extensions and
combines by the query's rule. Each function it answers
``requirement-writers`` with writes a requirement on a sibling into
the package's manifest, and is a [livery.workshop.RequirementWriter][].
"""

from __future__ import annotations

# The literal-False spelling the checkers honour without importing
# `typing`: the names below are what a checker sees, and at runtime
# `__getattr__` serves each from its module when it is read, so importing
# one private module of the workshop never loads the rest.
TYPE_CHECKING = False
if TYPE_CHECKING:
    from livery.workshop import testing as testing
    from livery.workshop._categories import Category as Category
    from livery.workshop._categories import category_of as category_of
    from livery.workshop._ci_context import RunContext as RunContext
    from livery.workshop._ci_context import ci_run as ci_run
    from livery.workshop._ci_context import state_store as state_store
    from livery.workshop._composition import extension_set as extension_set
    from livery.workshop._composition import (
        nearest_extensions as nearest_extensions,
    )
    from livery.workshop._contract import read_contract as read_contract
    from livery.workshop._examples import is_example as is_example
    from livery.workshop._extensions import contributions_for as contributions_for
    from livery.workshop._extensions import extension_names as extension_names
    from livery.workshop._extensions import shipped_content as shipped_content
    from livery.workshop._extensions import workspace_root as workspace_root
    from livery.workshop._forge_lane import forge_repository as forge_repository
    from livery.workshop._forge_lane import this_forge as this_forge
    from livery.workshop._forge_lane import this_repository as this_repository
    from livery.workshop._graph import order_topologically as order_topologically
    from livery.workshop._influence import Changes as Changes
    from livery.workshop._invoke import run_batched as run_batched
    from livery.workshop._lifecycle import build_release as build_release
    from livery.workshop._metrics import RUNS as RUNS
    from livery.workshop._metrics import leave_leg_file as leave_leg_file
    from livery.workshop._metrics import leave_section as leave_section
    from livery.workshop._packages import WORKSPACE_TESTS as WORKSPACE_TESTS
    from livery.workshop._packages import Edge as Edge
    from livery.workshop._packages import Neighbours as Neighbours
    from livery.workshop._packages import Package as Package
    from livery.workshop._packages import ReferencedName as ReferencedName
    from livery.workshop._packages import SourceReferences as SourceReferences
    from livery.workshop._packages import discover_packages as discover_packages
    from livery.workshop._packages import (
        holds_runtime_language as holds_runtime_language,
    )
    from livery.workshop._packages import neighbours as neighbours
    from livery.workshop._packages import release_tag as release_tag
    from livery.workshop._packages import verify_workspace as verify_workspace
    from livery.workshop._packages import workspace_suite as workspace_suite
    from livery.workshop._phases import PhaseContext as PhaseContext
    from livery.workshop._phases import run_phase as run_phase
    from livery.workshop._points import EVERY_RUNNER_LABEL as EVERY_RUNNER_LABEL
    from livery.workshop._points import check_legs as check_legs
    from livery.workshop._points import check_shards as check_shards
    from livery.workshop._points import pull_request_legs as pull_request_legs
    from livery.workshop._points import pull_request_shards as pull_request_shards
    from livery.workshop._prose import AGENT as AGENT
    from livery.workshop._prose import HUMAN as HUMAN
    from livery.workshop._prose import Prose as Prose
    from livery.workshop._prose import guidance as guidance
    from livery.workshop._provenance import generated_header as generated_header
    from livery.workshop._publish import discover_release as discover_release
    from livery.workshop._pythons import python_matrix as python_matrix
    from livery.workshop._pythons import scripts_dir as scripts_dir
    from livery.workshop._pythons import venv_python as venv_python
    from livery.workshop._quality import ci_changes as ci_changes
    from livery.workshop._quality import fix_files as fix_files
    from livery.workshop._quality import run_phase_over as run_phase_over
    from livery.workshop._queries import COMPILE_COMMANDS as COMPILE_COMMANDS
    from livery.workshop._queries import CURRENT_VERSION as CURRENT_VERSION
    from livery.workshop._queries import DISTRIBUTIONS as DISTRIBUTIONS
    from livery.workshop._queries import EXECUTABLES as EXECUTABLES
    from livery.workshop._queries import JOB_BUILT_MEMBERS as JOB_BUILT_MEMBERS
    from livery.workshop._queries import MODULE_ROOTS as MODULE_ROOTS
    from livery.workshop._queries import PUBLIC_MODULES as PUBLIC_MODULES
    from livery.workshop._queries import REQUIREMENTS as REQUIREMENTS
    from livery.workshop._queries import VERSION_FILES as VERSION_FILES
    from livery.workshop._queries import Query as Query
    from livery.workshop._queries import RequirementWriter as RequirementWriter
    from livery.workshop._queries import answer as answer
    from livery.workshop._registries import RegistryTarget as RegistryTarget
    from livery.workshop._registries import registry as registry
    from livery.workshop._release_notes import ReleaseNotes as ReleaseNotes
    from livery.workshop._release_notes import release_notes as release_notes
    from livery.workshop._shard import DURATIONS as DURATIONS
    from livery.workshop._shard import Shard as Shard
    from livery.workshop._shard import ShardMarker as ShardMarker
    from livery.workshop._shard import checkout_time as checkout_time
    from livery.workshop._shard import files_digest as files_digest
    from livery.workshop._shard import leave_shard as leave_shard
    from livery.workshop._shard import partition as partition
    from livery.workshop._shard import platform_leg as platform_leg
    from livery.workshop._shard import read_durations as read_durations
    from livery.workshop._shard import read_shard as read_shard
    from livery.workshop._shard import shard_of as shard_of
    from livery.workshop._speed import SPEED_FILE_VARIABLE as SPEED_FILE_VARIABLE
    from livery.workshop._speed import SpeedMark as SpeedMark
    from livery.workshop._speed import speed_marks as speed_marks
    from livery.workshop._steps import NONE as NONE
    from livery.workshop._steps import PACKAGE as PACKAGE
    from livery.workshop._steps import PACKAGES as PACKAGES
    from livery.workshop._steps import PATHS as PATHS
    from livery.workshop._steps import WHOLE as WHOLE
    from livery.workshop._steps import graph_changed as graph_changed
    from livery.workshop._steps import scoped_files as scoped_files
    from livery.workshop._steps import scoped_packages as scoped_packages
    from livery.workshop._steps import scoped_paths as scoped_paths
    from livery.workshop._steps import scoped_suite as scoped_suite
    from livery.workshop._steps import selected_files as selected_files
    from livery.workshop._steps import step_enabled as step_enabled
    from livery.workshop._steps import step_extensions as step_extensions
    from livery.workshop._steps import step_option as step_option
    from livery.workshop._steps import tested as tested
    from livery.workshop._tokens import forge_token as forge_token
    from livery.workshop._tools import ToolReceipt as ToolReceipt
    from livery.workshop._tools import store_home as store_home
    from livery.workshop._tools import tool_receipts as tool_receipts
    from livery.workshop._tools import undeployed as undeployed
    from livery.workshop._verified import LegScope as LegScope
    from livery.workshop._verified import VerifiedBranch as VerifiedBranch
    from livery.workshop._verified import leg_scope as leg_scope
    from livery.workshop._verified import verified_branch as verified_branch

__version__ = "0.10.0"

__all__ = [
    "AGENT",
    "COMPILE_COMMANDS",
    "CURRENT_VERSION",
    "DISTRIBUTIONS",
    "DURATIONS",
    "EVERY_RUNNER_LABEL",
    "EXECUTABLES",
    "HUMAN",
    "JOB_BUILT_MEMBERS",
    "MODULE_ROOTS",
    "NONE",
    "PACKAGE",
    "PACKAGES",
    "PATHS",
    "PUBLIC_MODULES",
    "REQUIREMENTS",
    "RUNS",
    "SPEED_FILE_VARIABLE",
    "VERSION_FILES",
    "WHOLE",
    "WORKSPACE_TESTS",
    "Category",
    "Changes",
    "Edge",
    "LegScope",
    "Neighbours",
    "Package",
    "PhaseContext",
    "Prose",
    "Query",
    "ReferencedName",
    "RegistryTarget",
    "ReleaseNotes",
    "RequirementWriter",
    "RunContext",
    "Shard",
    "ShardMarker",
    "SourceReferences",
    "SpeedMark",
    "ToolReceipt",
    "VerifiedBranch",
    "__version__",
    "answer",
    "build_release",
    "category_of",
    "check_legs",
    "check_shards",
    "checkout_time",
    "ci_changes",
    "ci_run",
    "contributions_for",
    "discover_packages",
    "discover_release",
    "extension_names",
    "extension_set",
    "files_digest",
    "fix_files",
    "forge_repository",
    "forge_token",
    "generated_header",
    "graph_changed",
    "guidance",
    "holds_runtime_language",
    "is_example",
    "leave_leg_file",
    "leave_section",
    "leave_shard",
    "leg_scope",
    "nearest_extensions",
    "neighbours",
    "order_topologically",
    "partition",
    "platform_leg",
    "pull_request_legs",
    "pull_request_shards",
    "python_matrix",
    "read_contract",
    "read_durations",
    "read_shard",
    "registry",
    "release_notes",
    "release_tag",
    "run_batched",
    "run_phase",
    "run_phase_over",
    "scoped_files",
    "scoped_packages",
    "scoped_paths",
    "scoped_suite",
    "scripts_dir",
    "selected_files",
    "shard_of",
    "shipped_content",
    "speed_marks",
    "state_store",
    "step_enabled",
    "step_extensions",
    "step_option",
    "store_home",
    "tested",
    "testing",
    "this_forge",
    "this_repository",
    "tool_receipts",
    "undeployed",
    "venv_python",
    "verified_branch",
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
    "JOB_BUILT_MEMBERS": "livery.workshop._queries",
    "ToolReceipt": "livery.workshop._tools",
    "build_release": "livery.workshop._lifecycle",
    "discover_release": "livery.workshop._publish",
    "python_matrix": "livery.workshop._pythons",
    "store_home": "livery.workshop._tools",
    "tool_receipts": "livery.workshop._tools",
    "Neighbours": "livery.workshop._packages",
    "ReferencedName": "livery.workshop._packages",
    "SourceReferences": "livery.workshop._packages",
    "neighbours": "livery.workshop._packages",
    "graph_changed": "livery.workshop._steps",
    "scripts_dir": "livery.workshop._pythons",
    "venv_python": "livery.workshop._pythons",
    "is_example": "livery.workshop._examples",
    "SPEED_FILE_VARIABLE": "livery.workshop._speed",
    "SpeedMark": "livery.workshop._speed",
    "speed_marks": "livery.workshop._speed",
    "RUNS": "livery.workshop._metrics",
    "WORKSPACE_TESTS": "livery.workshop._packages",
    "Category": "livery.workshop._categories",
    "LegScope": "livery.workshop._verified",
    "VerifiedBranch": "livery.workshop._verified",
    "category_of": "livery.workshop._categories",
    "EVERY_RUNNER_LABEL": "livery.workshop._points",
    "check_legs": "livery.workshop._points",
    "check_shards": "livery.workshop._points",
    "pull_request_shards": "livery.workshop._points",
    "DURATIONS": "livery.workshop._shard",
    "Shard": "livery.workshop._shard",
    "ShardMarker": "livery.workshop._shard",
    "checkout_time": "livery.workshop._shard",
    "files_digest": "livery.workshop._shard",
    "leave_shard": "livery.workshop._shard",
    "partition": "livery.workshop._shard",
    "platform_leg": "livery.workshop._shard",
    "read_durations": "livery.workshop._shard",
    "read_shard": "livery.workshop._shard",
    "shard_of": "livery.workshop._shard",
    "holds_runtime_language": "livery.workshop._packages",
    "leave_leg_file": "livery.workshop._metrics",
    "leave_section": "livery.workshop._metrics",
    "leg_scope": "livery.workshop._verified",
    "pull_request_legs": "livery.workshop._points",
    "state_store": "livery.workshop._ci_context",
    "tested": "livery.workshop._steps",
    "verified_branch": "livery.workshop._verified",
    "Query": "livery.workshop._queries",
    "RequirementWriter": "livery.workshop._queries",
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
    "contributions_for": "livery.workshop._extensions",
    "discover_packages": "livery.workshop._packages",
    "extension_names": "livery.workshop._extensions",
    "extension_set": "livery.workshop._composition",
    "nearest_extensions": "livery.workshop._composition",
    "order_topologically": "livery.workshop._graph",
    "forge_repository": "livery.workshop._forge_lane",
    "this_forge": "livery.workshop._forge_lane",
    "this_repository": "livery.workshop._forge_lane",
    "forge_token": "livery.workshop._tokens",
    "fix_files": "livery.workshop._quality",
    "shipped_content": "livery.workshop._extensions",
    "generated_header": "livery.workshop._provenance",
    "guidance": "livery.workshop._prose",
    "read_contract": "livery.workshop._contract",
    "registry": "livery.workshop._registries",
    "release_notes": "livery.workshop._release_notes",
    "release_tag": "livery.workshop._packages",
    "run_batched": "livery.workshop._invoke",
    "run_phase": "livery.workshop._phases",
    "run_phase_over": "livery.workshop._quality",
    "scoped_files": "livery.workshop._steps",
    "scoped_packages": "livery.workshop._steps",
    "scoped_paths": "livery.workshop._steps",
    "scoped_suite": "livery.workshop._steps",
    "selected_files": "livery.workshop._steps",
    "step_enabled": "livery.workshop._steps",
    "step_extensions": "livery.workshop._steps",
    "step_option": "livery.workshop._steps",
    "undeployed": "livery.workshop._tools",
    "verify_workspace": "livery.workshop._packages",
    "workspace_root": "livery.workshop._extensions",
    "workspace_suite": "livery.workshop._packages",
}

#: The public packages beneath the root, served whole on first use.
_PACKAGES = ("testing",)


def __getattr__(name: str) -> object:
    """Serve a public name from its module, each time it is read.

    The value is never kept here. A kept value would be whatever the
    module held at the first read, so a test that patched the module's
    name before that read would leave its fake in this module for the
    rest of the process.
    """
    import importlib

    if name in _PACKAGES:
        value: object = importlib.import_module(f"livery.workshop.{name}")
    else:
        module = _EXPORTS.get(name)
        if module is None:
            raise AttributeError(f"module 'livery.workshop' has no attribute {name!r}")
        value = getattr(importlib.import_module(module), name)
    return value
