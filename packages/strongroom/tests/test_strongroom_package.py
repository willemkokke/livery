# Seeded from the template channel (package-python kind) at
# birth; this file is the workspace's own. Edit it directly:
# the template never rewrites it.
"""The package's standing contracts, pinned before anything else."""

from __future__ import annotations

import ast
import subprocess
import sys
import tomllib
from pathlib import Path

import livery.strongroom as package

PACKAGE = Path(__file__).resolve().parents[1]
SOURCE = PACKAGE / "src" / "livery" / "strongroom"


def test_the_distribution_has_no_runtime_dependency() -> None:
    # Contract 2 of the plan: stdlib-only at runtime, no extras yet.
    parsed = tomllib.loads((PACKAGE / "pyproject.toml").read_text("utf-8"))
    assert parsed["project"]["dependencies"] == []
    assert "optional-dependencies" not in parsed["project"]


def test_the_namespace_carries_no_init() -> None:
    # PEP 420: livery/ is a namespace directory, never a package.
    assert not (PACKAGE / "src" / "livery" / "__init__.py").exists()


def test_no_module_imports_beyond_the_stdlib_and_itself() -> None:
    # Contracts 1 and 14: strongroom imports nothing first-party and
    # nothing third-party, at import time or lazily, anywhere.
    allowed = set(sys.stdlib_module_names) | {"livery.strongroom"}
    offending: list[str] = []
    for source in sorted(SOURCE.rglob("*.py")):
        tree = ast.parse(source.read_text("utf-8"), filename=str(source))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                top = name.split(".")[0]
                if top in allowed or name.startswith("livery.strongroom"):
                    continue
                offending.append(f"{source.relative_to(PACKAGE)}: {name}")
    assert offending == []


def test_importing_loads_no_other_first_party_module() -> None:
    # A fresh interpreter, so this suite's own imports do not count.
    script = (
        "import sys, livery.strongroom;"
        " print(sorted(m for m in sys.modules if m.startswith('livery.')"
        " and not m.startswith('livery.strongroom')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[]"


def test_imports_and_carries_a_version() -> None:
    from importlib.metadata import version

    # Against the installed metadata, never a literal: the release
    # train stamps the version, and a spelled copy here would need a
    # hand edit every release.
    assert package.__version__ == version("livery-strongroom")


def test_the_public_surface_is_pinned() -> None:
    assert set(package.__all__) == {
        "ALGORITHMS",
        "Algorithm",
        "Clock",
        "ConformanceFailure",
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
        "HashConstructor",
        "Hasher",
        "Hooks",
        "HttpSource",
        "IntegrityError",
        "LAYOUT_VERSION",
        "Landed",
        "Link",
        "LockHolder",
        "LockTimeout",
        "MANIFEST_NAME",
        "MUTATION_CLASSES",
        "MadeRung",
        "Manifest",
        "ManifestError",
        "MissingObject",
        "MutationClass",
        "NAME_BUDGET",
        "Namespace",
        "NoSuchPending",
        "NotFastForward",
        "OWNED",
        "ObjectState",
        "OriginHint",
        "PATH_BUDGET",
        "PENDING",
        "PINS",
        "Pending",
        "Progress",
        "PythonHooks",
        "REFUSALS",
        "RUNGS",
        "RefConflict",
        "RefProtected",
        "RefRecord",
        "RefTampered",
        "Rung",
        "RungUnavailable",
        "SHA256",
        "Scenario",
        "ScrubReport",
        "ShedReport",
        "Source",
        "Store",
        "StoreError",
        "StoreLike",
        "Subject",
        "SubjectKind",
        "SweepReport",
        "Tombstone",
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
        "check_name",
        "check_target",
        "check_timestamp",
        "digest_of",
        "digest_stream",
        "fetch_url",
        "load_scenarios",
        "now",
        "run_scenario",
        "silent",
    }
    for name in package.__all__:
        assert hasattr(package, name), name


def test_every_module_is_underscore_private_except_the_init() -> None:
    public = [
        path.name for path in SOURCE.glob("*.py") if not path.name.startswith("_")
    ]
    assert public == []
