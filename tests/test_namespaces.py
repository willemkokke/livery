"""This repository's roots: each holds its public names in its __init__.py.

Our convention, not the workshop's: every distribution root with public
names is a regular package whose __init__.py declares them, imports
what need not load under TYPE_CHECKING and serves it on first use. The
shared namespaces, livery, livery.toolroom and livery.extensions, carry
no __init__.py, nor does a root with no public names, an extension's:
nobody imports an extension but the workshop, which mounts it.
A public package under a root keeps its own path and its root declares
it, and testing is a name no module takes anywhere but directly under
a root.
"""

from __future__ import annotations

import ast
import importlib
import json
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]

#: Each root's public names. livery.toolroom.tools has no __all__, so
#: its public module names stand in.
EXPORTS: dict[str, list[str]] = {
    "livery.footman": [
        "App",
        "Arg",
        "Argv",
        "AuditEntry",
        "Brand",
        "Context",
        "Exists",
        "Failed",
        "FetchError",
        "Forward",
        "GlobalOption",
        "Group",
        "Hidden",
        "Invocation",
        "IsDir",
        "IsFile",
        "Lane",
        "Many",
        "NoSplit",
        "Result",
        "ResultView",
        "RunFailed",
        "RunTimeout",
        "Runner",
        "Secret",
        "Section",
        "Stdin",
        "Stdout",
        "Stream",
        "TaskView",
        "Tasks",
        "TimedOut",
        "__version__",
        "ask",
        "attended",
        "between",
        "cache_dir",
        "capture",
        "chdir",
        "check",
        "colored",
        "config_dir",
        "config_file",
        "config_section",
        "confirm",
        "console_lane",
        "current",
        "cwd",
        "cwd_lane",
        "data_dir",
        "default",
        "dist",
        "doc",
        "docs",
        "docstrings",
        "env",
        "exists",
        "expose",
        "fail",
        "fetch",
        "forward",
        "given",
        "group",
        "handing_off",
        "hidden",
        "include",
        "inherited",
        "installed_entry_points",
        "isdir",
        "isfile",
        "lane",
        "main",
        "mark",
        "markdown",
        "matching",
        "nosplit",
        "parallel",
        "passthrough",
        "plugin",
        "post_task",
        "post_tasks",
        "pre_bind",
        "pre_record",
        "pre_reexec",
        "pre_task",
        "pre_tasks",
        "profile",
        "prog",
        "progress",
        "project_root",
        "prompt",
        "real_stderr",
        "recording",
        "requires",
        "requires_dep",
        "requires_env",
        "requires_tool",
        "rescan_entry_points",
        "root_group",
        "run",
        "section",
        "select",
        "stdin",
        "stdout",
        "step",
        "stream",
        "suggest",
        "task",
        "testing",
        "track",
        "tty",
        "use_context",
        "user_tasks_file",
        "wrap_bind",
        "wrap_task",
    ],
    "livery.forge": [
        "Asset",
        "Capability",
        "CheckState",
        "Checks",
        "Codeowners",
        "CodeownersEntry",
        "CombinedStatus",
        "Comment",
        "Conclusion",
        "Forge",
        "ForgeError",
        "GiteaForge",
        "GithubForge",
        "GitlabForge",
        "Issue",
        "Issues",
        "Job",
        "Label",
        "MergeCategory",
        "MergeState",
        "Protection",
        "PullRequest",
        "PullRequests",
        "RateBudget",
        "RateLimited",
        "Registry",
        "RegistryKind",
        "Release",
        "Releases",
        "RepoConfig",
        "RepoInfo",
        "Repository",
        "Review",
        "ReviewState",
        "Run",
        "RunStatus",
        "Schedule",
        "ScheduleEvent",
        "ScheduleEventKind",
        "Schedules",
        "SimpleRegistry",
        "StateFilter",
        "Step",
        "Unsupported",
        "__version__",
        "classify_gitea_merge_refusal",
        "classify_github_mergeable_state",
        "classify_gitlab_detailed_status",
        "classify_merge_refusal",
        "gitea_configured_host",
        "gitea_is_configured_host",
        "gitlab_configured_host",
        "gitlab_is_configured_host",
        "merge_state",
        "testing",
    ],
    "livery.strongroom": [
        "ALGORITHMS",
        "Algorithm",
        "Clock",
        "Digest",
        "DropReport",
        "ENTRY_RUNGS",
        "Entry",
        "EntryKind",
        "EntryRung",
        "ErasedObject",
        "FillPolicy",
        "FolderSource",
        "FormatError",
        "Group",
        "GroupHalfApplied",
        "HashConstructor",
        "Hasher",
        "HttpSource",
        "IntegrityError",
        "LAYOUT_VERSION",
        "Landed",
        "Link",
        "LockTimeout",
        "MANIFEST_NAME",
        "MUTATION_CLASSES",
        "MadeRung",
        "Manifest",
        "ManifestError",
        "MissingObject",
        "Move",
        "MutationClass",
        "NAME_BUDGET",
        "Namespace",
        "NoSuchPending",
        "NotAGroup",
        "NotFastForward",
        "OWNED",
        "ObjectState",
        "OriginHint",
        "PATH_BUDGET",
        "PENDING",
        "PINS",
        "Pending",
        "Progress",
        "RUNGS",
        "RefConflict",
        "RefProtected",
        "RefRecord",
        "RefTampered",
        "Rung",
        "RungUnavailable",
        "SHA256",
        "ScrubReport",
        "ShedReport",
        "Source",
        "Store",
        "StoreError",
        "Subject",
        "SubjectKind",
        "SweepReport",
        "Tombstone",
        "Transaction",
        "Tree",
        "UnknownNamespace",
        "Unreachable",
        "Value",
        "Version",
        "ViewEntry",
        "ViewRecord",
        "WriteOnceRefused",
        "__version__",
        "canonical",
        "cbor",
        "check_name",
        "check_target",
        "check_timestamp",
        "digest_of",
        "digest_stream",
        "fetch_url",
        "now",
        "silent",
        "testing",
    ],
    "livery.toolroom.bench": ["Refreshed", "__version__", "submit_refresh", "tasks"],
    "livery.toolroom.store": [
        "ARCHES",
        "ARCHIVE_SUFFIXES",
        "Artifact",
        "BUILD_FILE",
        "Catalogue",
        "CatalogueError",
        "DOWNLOAD_KINDS",
        "Delta",
        "Deployment",
        "Ensured",
        "Event",
        "FORMATS",
        "FetchError",
        "Fetched",
        "GRAPHS",
        "Graph",
        "HOSTS",
        "Home",
        "KINDS",
        "LAYOUT_KEYS",
        "LINKS",
        "LOCK_FILE",
        "Layout",
        "Listed",
        "Lock",
        "LockError",
        "Locked",
        "MODES",
        "NameCollision",
        "OPTION_KEYS",
        "Observation",
        "Option",
        "PACKAGE_VAR",
        "PLATFORMS",
        "POINTER",
        "Progress",
        "RECORD_SUFFIX",
        "RUNTIMES",
        "Record",
        "RecordDelta",
        "RecordError",
        "Requirement",
        "SURFACE_PLATFORMS",
        "Scope",
        "Spec",
        "SpecError",
        "Store",
        "StoreError",
        "Surface",
        "TOOLS",
        "ToolSpec",
        "URLS",
        "UnpackError",
        "VERB_KEYS",
        "VERSION_VAR",
        "Verb",
        "__version__",
        "api_headers",
        "artifact_format",
        "build_current",
        "bun_global_project",
        "class_name",
        "default_mode",
        "export_schema",
        "fetch_bytes",
        "fetch_file",
        "fetch_json",
        "host_key",
        "npm_cli",
        "observations",
        "read_pointer",
        "records_in",
        "render",
        "render_observation",
        "resolve",
        "resolve_lock",
        "schema",
        "silent",
        "spec_from",
        "surface_at",
        "tree_fingerprint",
        "unpack",
        "validate",
        "version_key",
    ],
    "livery.toolroom.tools": [
        "Any",
        "Argv",
        "ArgvTool",
        "Flag",
        "Iterator",
        "NamedTuple",
        "Result",
        "Sequence",
        "Tool",
        "ToolError",
        "TypeAlias",
        "Value",
        "ValuedFlag",
        "annotations",
        "off",
        "read_version",
        "version_tuple",
    ],
    "livery.workshop": [
        "AGENT",
        "Changes",
        "Edge",
        "GateContext",
        "HUMAN",
        "NONE",
        "PACKAGE",
        "PACKAGES",
        "PATHS",
        "Package",
        "Prose",
        "RegistryTarget",
        "ReleaseNotes",
        "RunContext",
        "WHOLE",
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
    ],
}

#: Where each root lives.
SOURCES = {
    "livery.footman": "packages/footman/src/livery/footman",
    "livery.forge": "packages/forge/src/livery/forge",
    "livery.strongroom": "packages/strongroom/src/livery/strongroom",
    "livery.workshop": "packages/workshop/src/livery/workshop",
    "livery.toolroom.store": "packages/toolroom-store/src/livery/toolroom/store",
    "livery.toolroom.bench": "packages/toolroom-bench/src/livery/toolroom/bench",
    "livery.toolroom.tools": "packages/toolroom/src/livery/toolroom/tools",
}

#: The roots with no public names: a namespace with no __init__.py,
#: reached through an entry point alone.
BARE = {
    "livery.extensions.docs": "packages/workshop/src/livery/extensions/docs",
    "livery.extensions.basedpyright": (
        "packages/extensions/basedpyright/src/livery/extensions/basedpyright"
    ),
    "livery.extensions.ruff": "packages/extensions/ruff/src/livery/extensions/ruff",
}

#: The shared namespaces under a member's src directory.
NAMESPACES = ("livery", "livery/toolroom", "livery/extensions")

#: The names a module takes only directly under a root.
RESERVED = ("testing",)

#: The private modules importing a root loads, beyond the root itself:
#: what its entry module needs at runtime. The handles bind the result
#: types for real and build the colour table at import.
EAGER = {
    "livery.toolroom.tools": [
        "livery.toolroom.tools._colordata",
        "livery.toolroom.tools._host",
    ],
}


def _public(module: ModuleType) -> list[str]:
    declared = getattr(module, "__all__", None)
    if declared is not None:
        return sorted(declared)
    # A tool handle is made on first use and kept on the module, and a
    # submodule is bound there by its first import, so which of either
    # exists depends on what ran before: the module's own names are the
    # surface.
    tool = getattr(module, "Tool", None)
    return sorted(
        name
        for name, value in vars(module).items()
        if not name.startswith("_")
        and not (tool and isinstance(value, tool))
        and not (
            isinstance(value, ModuleType)
            and value.__name__ == f"{module.__name__}.{name}"
        )
    )


def test_each_root_exports_its_public_names() -> None:
    for root, names in EXPORTS.items():
        assert _public(importlib.import_module(root)) == names, root


def test_every_root_with_public_names_is_a_package_and_every_namespace_is_not() -> None:
    problems: list[str] = []
    roots = {ROOT / path for path in SOURCES.values()}
    for directory in sorted(roots):
        if not (directory / "__init__.py").is_file():
            problems.append(f"{directory}: a root declares its names in __init__.py")
    # A member at either depth: packages/<name>/ or packages/<group>/<name>/.
    for src in sorted([*ROOT.glob("packages/*/src"), *ROOT.glob("packages/*/*/src")]):
        for namespace in NAMESPACES:
            if (src / namespace / "__init__.py").exists():
                problems.append(
                    f"{src / namespace}: a namespace carries no __init__.py"
                )
        for path in sorted(src.rglob("*")):
            if "__pycache__" in path.parts or path.stem not in RESERVED:
                continue
            if path.parent not in roots:
                problems.append(f"{path.relative_to(ROOT)}: {path.stem} is reserved")
    assert problems == []


def test_importing_a_root_loads_only_what_its_entry_module_needs() -> None:
    # One fresh interpreter per root: what an earlier import loaded
    # would hide what this one loads.
    loaded: dict[str, list[str]] = {}
    for root in SOURCES:
        script = (
            "import importlib, json, sys\n"
            "before = set(sys.modules)\n"
            f"importlib.import_module({root!r})\n"
            f"prefix = {root!r} + '.'\n"
            "print(json.dumps(sorted(m for m in set(sys.modules) - before"
            " if m.startswith(prefix))))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        loaded[root] = json.loads(result.stdout)
    assert loaded == {root: EAGER.get(root, []) for root in SOURCES}


def _declared(root: str) -> set[str]:
    """The names a root declares: its `__all__`, else its stub's re-exports."""
    module = importlib.import_module(root)
    declared = getattr(module, "__all__", None)
    if declared is not None:
        return set(declared)
    stub = ROOT / SOURCES[root] / "__init__.pyi"
    return {
        alias.name
        for node in ast.walk(ast.parse(stub.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.asname == alias.name
    }


def test_every_public_module_under_a_root_is_declared_by_the_root() -> None:
    # typecomplete verifies what a root declares, so a module or package
    # directly under a root without a leading underscore is declared
    # there.
    problems: list[str] = []
    for root, path in SOURCES.items():
        declared = _declared(root)
        # A declaration the root cannot serve is a lie the checkers believe.
        module = importlib.import_module(root)
        for name in sorted(declared):
            getattr(module, name)
        for child in sorted((ROOT / path).iterdir()):
            if child.name.startswith(("_", ".")):
                continue
            if child.is_dir():
                if not any(child.glob("*.py")):
                    continue  # data, never a module
                name = child.name
            elif child.suffix == ".py":
                name = child.stem
            else:
                continue
            if name not in declared:
                problems.append(f"{root}.{name}: public, and {root} declares no {name}")
    assert problems == []


def test_a_wheel_whose_roots_all_have_entry_modules_lets_none_lose_it() -> None:
    # Without the namespace flag, uv build refuses a module name that has
    # no __init__.py, so no root ships having lost its entry module. A
    # wheel that also ships a root with no public names, as the
    # workshop's ships the docs extension, needs the flag for it.
    problems: list[str] = []
    members = [*ROOT.glob("packages/*/pyproject.toml")]
    members += ROOT.glob("packages/*/*/pyproject.toml")
    for pyproject in sorted(members):
        backend = (
            tomllib.loads(pyproject.read_text("utf-8"))
            .get("tool", {})
            .get("uv", {})
            .get("build-backend", {})
        )
        names = backend.get("module-name", [])
        names = [names] if isinstance(names, str) else list(names)
        if backend.get("namespace") and names and all(n in SOURCES for n in names):
            problems.append(
                f"{pyproject.relative_to(ROOT)}: namespace = true lets a root ship"
                " without its __init__.py"
            )
    assert problems == []


def test_a_root_with_no_public_names_has_no_init() -> None:
    problems = [
        f"{path}: __init__.py in a root with no public names"
        for path in BARE.values()
        if (ROOT / path / "__init__.py").exists()
    ]
    assert problems == []
    assert all((ROOT / path).is_dir() for path in BARE.values())
