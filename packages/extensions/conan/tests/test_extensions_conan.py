"""The conan extension: its distribution, its declaration, its answers and steps.

The refusals first: a workspace that lists no conan has no verb for its
template, a consumer's recipe answers no registry, and a sync over
packages that hold no conan makes no profile. Then its own
distribution, the entry module, the kit, the answers a recipe gives,
the steps a member runs through, the conan workspace file, and the dev
wheels of the loop.
"""

from __future__ import annotations

import importlib.metadata
import re
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from extensions_conan_workspace import skip_unless_installed
from livery.footman import Group
from livery.workshop import _lifecycle
from livery.workshop._packages import Package
from livery.workshop._shipped_files import deliver, shipped_drift
from livery.workshop.testing import CLAUSES, Clause

#: A recipe with a version and one requirement, as a born library's has.
RECIPE = (
    "from conan import ConanFile\n\n\n"
    "class Recipe(ConanFile):\n"
    '    name = "acme-lib"\n'
    '    version = "1.0.0"\n'
    '    requires = ("acme-base/[>=0.1 <1]",)\n'
)


def _member(directory: Path, extensions: tuple[str, ...]) -> Package:
    """A C++ library in *directory* listing *extensions*, with the recipe."""
    directory.mkdir(parents=True)
    (directory / "conanfile.py").write_text(RECIPE)
    return Package(
        directory=directory,
        path=f"packages/{directory.name}",
        name="acme-lib",
        depends=(),
        extensions=extensions,
    )


def _mounted_tree(root: Path) -> Group:
    """The tree a process started now at *root* mounts, the templates' verbs in it."""
    from livery.footman import _registry as registry
    from livery.workshop import _points
    from livery.workshop._extensions import mount_extensions

    _points.restore(_points.snapshot(initial=True))
    with registry.capture() as tree:
        mount_extensions(root)
    return tree


def _mount(root: Path) -> tuple[str, ...]:
    """Mount what *root*'s lists hold, as a verb there would; the names mounted."""
    from livery.footman import _registry as registry
    from livery.workshop._extensions import mount_extensions

    with registry.capture():
        return mount_extensions(root)


@pytest.fixture
def conan_profiles(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[Path]]:
    """Conan's steps registered as the mount registers them; each profile call's root.

    The profile call is faked where conan's sync step reads it, so no
    conan starts.
    """
    from livery.workshop import _steps as registry
    from livery.workshop._extensions import declaration, register_declared

    made: list[Path] = []

    def _made(cwd: Path) -> list[str]:
        made.append(cwd)
        return []

    monkeypatch.setattr("livery.extensions.conan._steps._package.ensure_profile", _made)
    found = declaration("conan")
    assert found is not None
    state = registry.snapshot()
    register_declared("conan", found.additions)
    yield made
    registry.restore(state)


def _listing(root: Path, name: str, *extensions: str) -> Package:
    """The package ``packages/<name>`` listing *extensions*, as a phase reads it."""
    return Package(
        directory=root / "packages" / name,
        path=f"packages/{name}",
        name=name,
        depends=(),
        extensions=extensions,
    )


# --- refusals first -----------------------------------------------------------


def test_a_workspace_that_lists_no_conan_has_no_verb_for_its_template(
    tmp_path: Path,
) -> None:
    from livery.footman.testing import Runner

    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nname = "acme"\nnamespace = "acme"\nextensions = ["python"]\n'
    )
    # The refusal first: nothing lists conan, so no verb creates its
    # template, and footman refuses a task it does not know.
    refused = Runner().invoke(
        "new.package.cpp-package geometry",
        tasks=_mounted_tree(tmp_path),
        cwd=tmp_path,
    )
    assert refused.exit_code == 64, refused.stderr
    assert "no task named 'new.package.cpp-package'" in refused.stderr
    assert not (tmp_path / "packages").exists()
    # Listed, the template's verb exists.
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nname = "acme"\nnamespace = "acme"\nextensions = ["conan"]\n'
    )
    verbs = _mounted_tree(tmp_path).groups["new"].groups["package"]
    assert "cpp-package" in verbs.tasks


def test_a_consumer_recipe_answers_no_distribution_and_no_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skip_unless_installed("cmake")
    from livery.footman import Failed
    from livery.workshop import _registries
    from livery.workshop._queries import DISTRIBUTIONS, REGISTRY, answer
    from livery.workshop._registries import RegistryTarget

    def _no_target(root: Path, kind: str) -> RegistryTarget:
        raise AssertionError(f"a {kind} target was resolved under {root}")

    monkeypatch.setattr(_registries, "resolve_registry", _no_target)
    # A consumer's recipe, requirements alone, releases nothing: conan
    # answers neither question, and resolves no target for it.
    consumer = _member(tmp_path / "packages" / "consumer", ("cmake", "conan"))
    (consumer.directory / "conanfile.py").write_text(
        'class C:\n    requires = ("acme-lib/[>=1.0.0]",)\n'
    )
    assert answer(consumer, DISTRIBUTIONS) == ()
    assert not answer(consumer, REGISTRY)
    # So the release train has nowhere to publish it, and says so.
    with pytest.raises(Failed) as refused:
        _lifecycle.registry(consumer)
    assert str(refused.value) == (
        "packages/consumer: its extensions (cmake, cpp, conan) answer no registry:"
        " none of them releases a distribution of it, so the release train has"
        " nowhere to publish it. List the extension that releases it, or set"
        " [release] publish = false to version and tag it without an upload"
    )


def test_a_sync_over_packages_holding_no_conan_makes_no_profile(
    tmp_path: Path, conan_profiles: list[Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _lifecycle.sync((), tmp_path)
    _lifecycle.sync(
        (_listing(tmp_path, "plain"), _listing(tmp_path, "tool", "python")), tmp_path
    )
    assert conan_profiles == []
    said = capsys.readouterr().out.splitlines()
    assert said.count("  sync.conan: no package holds conan; not run") == 2


def test_a_sync_over_two_packages_holding_conan_makes_the_profile_once(
    tmp_path: Path, conan_profiles: list[Path]
) -> None:
    # The workspace has one conan home and so one default profile.
    everyone = (
        _listing(tmp_path, "lib", "conan"),
        _listing(tmp_path, "app", "cpp", "conan"),
        _listing(tmp_path, "tool", "python"),
    )
    _lifecycle.sync(everyone, tmp_path)
    assert conan_profiles == [tmp_path]


def test_a_sync_over_one_package_holding_conan_makes_the_profile_once(
    tmp_path: Path, conan_profiles: list[Path]
) -> None:
    everyone = (
        _listing(tmp_path, "cpp", "conan"),
        _listing(tmp_path, "tool", "python"),
    )
    _lifecycle.sync(everyone, tmp_path)
    assert conan_profiles == [tmp_path]


# --- its own distribution -----------------------------------------------------


def _floor_cut(requirement: str) -> str:
    """*requirement* with its floor's version written ``<floor>``.

    A release raises a floor on a member released beside it to that
    member's new version, so the value is the release's to move. The
    name, the floor's presence and the marker stay as the wheel wrote
    them.
    """
    return re.sub(r">=\s*\d[^\s,;]*", ">=<floor>", requirement)


def test_its_own_distribution_ships_conan_and_the_workshop_wheel_none() -> None:
    from livery.workshop._extensions import declaration, shipping_distribution

    ours = {
        (entry.group, entry.name, entry.value)
        for entry in importlib.metadata.distribution(
            "livery-extensions-conan"
        ).entry_points
    }
    assert ours == {("workshop.extensions", "conan", "livery.extensions.conan")}
    theirs = importlib.metadata.distribution("livery-workshop").entry_points
    assert [
        entry.name
        for entry in theirs
        if entry.value.startswith("livery.extensions.conan")
    ] == []
    shipped = shipping_distribution("livery.extensions.conan")
    assert shipped is not None and shipped.name == "livery-extensions-conan"
    # The extension it requires, cpp, ships in a distribution of its
    # own, which this wheel requires beside the workshop's, each at a
    # floor.
    required = importlib.metadata.requires("livery-extensions-conan") or []
    assert [
        _floor_cut(line)
        for line in required
        if line.startswith(("livery-workshop", "livery-extensions-cpp"))
    ] == ["livery-extensions-cpp>=<floor>", "livery-workshop>=<floor>"]
    declared = declaration("conan")
    assert declared is not None
    # conan packages C and C++.
    assert declared.requires == ("cpp",)
    assert {
        name: getattr(shipping_distribution(f"livery.extensions.{name}"), "name", "")
        for name in declared.requires
    } == {"cpp": "livery-extensions-cpp"}


def test_the_entry_module_refuses_a_name_it_does_not_export() -> None:
    import livery.extensions.conan as conan_module

    # The refused name is a function of the module that defines the
    # exported ones: only the declared names are served.
    with pytest.raises(
        AttributeError,
        match=re.escape(
            "module 'livery.extensions.conan' has no attribute 'restore_cache'"
        ),
    ):
        conan_module.restore_cache  # noqa: B018  # pyright: ignore[reportAttributeAccessIssue]


def test_the_entry_module_serves_what_the_defining_module_holds_at_each_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import livery.extensions.conan as conan_module
    from livery.extensions.conan import _package

    # A test patches the defining module, and a reader of the entry
    # module sees the patch.
    saved = object()
    monkeypatch.setattr(_package, "save_cache", saved)
    assert conan_module.save_cache is saved
    monkeypatch.undo()
    assert conan_module.save_cache is _package.save_cache
    assert conan_module.__all__ == [
        "WORKSPACE_FILE",
        "__version__",
        "restore_from_releases",
        "save_cache",
        "workspace_aside",
        "workspace_file",
    ]


def test_its_build_serves_the_isolated_side_alone() -> None:
    # The gate builds each package on the build phase's tree side, so
    # the release's conan create never reaches the gate's registry.
    from livery.workshop._extensions import declaration

    declared = declaration("conan")
    assert declared is not None
    assert [(r.name, r.isolated) for r in declared.phases["build"]] == [
        ("build.conan", True)
    ]
    assert [r.name for r in declared.additions.steps if r.phase == "build"] == []


def test_a_package_listing_conan_mounts_it_with_what_it_requires(
    tmp_path: Path,
) -> None:
    (tmp_path / "workshop.toml").write_text('[workspace]\nextensions = ["conan"]\n')
    assert _mount(tmp_path) == ("cpp", "conan")


@pytest.mark.parametrize("clause", CLAUSES, ids=lambda clause: clause.name)
def test_conan_conforms(clause: Clause, tmp_path: Path) -> None:
    from livery.workshop._steps import StepRecord, steps_by_name
    from livery.workshop.testing import Subject

    if clause.name == "template-composes":
        # The cpp-package template lists cmake, a distribution of its
        # own, which the clause reads to show the template composes.
        skip_unless_installed("cmake")
    (tmp_path / "workshop.toml").write_text('[workspace]\nextensions = ["conan"]\n')
    _mount(tmp_path)
    # conan registers one step, the sync phase's, which fm sync runs once
    # over the workspace; its stamp, build and publish steps run in a
    # release, and none of them reaches the gate.
    steps: tuple[StepRecord, ...] = tuple(
        record for record in steps_by_name().values() if record.extension == "conan"
    )
    assert [(record.name, record.scope) for record in steps] == [
        ("sync.conan", "workspace")
    ]
    subject = Subject("livery.extensions.conan", sets=(("conan",),), steps=steps)
    assert clause.judge(subject) == []


def test_the_loop_s_dev_wheels_hold_this_distribution() -> None:
    # The loop's plain pass creates loop-cpp from cpp-package, so its
    # stack lists conan, and the dev wheels the pass builds and
    # publishes before the birth hold this distribution: the newborn
    # installs it, and loop-cpp's release publishes through conan's
    # publish step from it.
    from livery.workshop._composition import closure
    from livery.workshop._e2e import LOOP_MEMBERS, dev_members, loop_stack
    from livery.workshop._extensions import installed_declaration

    # The loop's members need cmake and nanobind too, so the stack is
    # read where both are installed.
    skip_unless_installed("cmake", "nanobind")
    root = Path(__file__).resolve().parents[4]
    templates = {member.name: member.template for member in LOOP_MEMBERS}
    assert templates["loop-cpp"] == "cpp-package"
    # The canonical list leaves conan out, since nanobind requires it,
    # and the stack's set holds it.
    assert "conan" in closure(loop_stack(), installed_declaration)
    assert "extensions/conan" in dev_members(root, loop_stack())


def test_the_loop_probes_its_conan_member_through_conan_s_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The loop's conan member is probed at the conan target, by its exact name.

    A conan registry matches the name exactly, so the loop asks it by the
    contract's name, which the recipe's reference carries. The probe is
    built once, at the target the loop's workspace resolves, and polled
    through the wave's wait.
    """
    from livery.extensions.conan import _package
    from livery.workshop import _e2e
    from livery.workshop._registries import RegistryTarget

    remote = "http://gitea:3000/conan"
    asked: list[str] = []
    built: list[str] = []

    class _Conan:
        def __init__(self, target: RegistryTarget, *, root: Path) -> None:
            built.append(f"{target.url} {root.name}")

        def versions(self, name: str) -> tuple[str, ...]:
            asked.append(name)
            return ()

    monkeypatch.setattr(_package, "ConanRegistry", _Conan)
    monkeypatch.setenv("CONAN_REMOTE_URL", remote)
    root = tmp_path / "loop"
    member = root / "packages" / "loop-cpp"
    member.mkdir(parents=True)
    (root / "workshop.toml").write_text("[workspace]\n")
    # The library as its seeds write it: it lists cmake and conan and
    # names its reference.
    (member / "workshop.toml").write_text(
        'extensions = ["cmake", "conan"]\nname = "ci_e2e_loop-loop-cpp"\n'
    )
    (member / "conanfile.py").write_text(
        'class L:\n    name = "ci_e2e_loop-loop-cpp"\n    version = "0.1.0"\n'
    )
    served = _e2e._serving_probe(root)
    assert served("loop-cpp") == ()
    assert served("loop-cpp") == ()
    assert asked == ["ci_e2e_loop-loop-cpp", "ci_e2e_loop-loop-cpp"]
    assert built == [f"{remote} loop"]


# --- the answers a recipe gives -----------------------------------------------


def test_a_recipe_naming_a_reference_releases_its_package_to_the_conan_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._queries import DISTRIBUTIONS, answer

    # The target the ladder resolves for conan, a folder here, which
    # holds a saved cache of the package.
    (tmp_path / "workshop.toml").write_text("[workspace]\n")
    share = tmp_path / "share"
    share.mkdir()
    (share / "acme-lib-1.0.0.tgz").touch()
    monkeypatch.setenv("CONAN_REMOTE_URL", str(share))
    listing = _member(tmp_path / "packages" / "listing", ("cmake", "conan"))
    assert answer(listing, DISTRIBUTIONS) == (("conan", "acme-lib"),)
    assert _lifecycle.ecosystem(listing) == "conan"
    assert _lifecycle.registry(listing).versions("acme-lib") == ("1.0.0",)


def test_a_consumer_recipe_answers_no_version_and_no_version_file(
    tmp_path: Path,
) -> None:
    from livery.workshop._queries import CURRENT_VERSION, VERSION_FILES, answer

    # A consumer's recipe, requirements alone, holds no version to read or
    # to stamp: conan answers neither, so the set's other answers stand.
    consumer = _member(tmp_path / "consumer", ("cmake", "conan"))
    (consumer.directory / "conanfile.py").write_text(
        'class C:\n    requires = ("acme-lib/[>=1.0.0]",)\n'
    )
    assert answer(consumer, CURRENT_VERSION) == ""
    assert answer(consumer, VERSION_FILES) == ()
    # A recipe that names its reference answers both.
    listing = _member(tmp_path / "listing", ("cmake", "conan"))
    assert answer(listing, CURRENT_VERSION) == "1.0.0"
    assert answer(listing, VERSION_FILES) == (listing.directory / "conanfile.py",)


def test_a_member_without_a_recipe_names_no_reference(tmp_path: Path) -> None:
    from livery.extensions.conan import _package

    member = _listing(tmp_path, "lib", "cmake", "conan")
    member.directory.mkdir(parents=True)
    assert not _package.names_a_reference(member)


def test_a_requirement_that_names_no_version_is_no_requirement(tmp_path: Path) -> None:
    from livery.extensions.conan import _package

    recipe = tmp_path / "conanfile.py"
    recipe.write_text('requires = ("fmt", "zlib/[>=1.3]")\n')
    assert _package.conan_requirements(recipe) == {"zlib": "[>=1.3]"}


def test_the_extensions_answer_a_cpp_conan_package_from_its_recipe(
    tmp_path: Path,
) -> None:
    """A member listing cmake and conan is answered from its recipe."""
    from livery.workshop._queries import (
        CURRENT_VERSION,
        MODULE_ROOTS,
        PUBLIC_MODULES,
        REQUIREMENTS,
        VERSION_FILES,
        answer,
    )

    listing = _member(tmp_path / "listing", ("cmake", "conan"))
    assert answer(listing, CURRENT_VERSION) == _lifecycle.current_version(listing)
    assert _lifecycle.current_version(listing) == "1.0.0"
    assert answer(listing, VERSION_FILES) == (listing.directory / "conanfile.py",)
    requirements = answer(listing, REQUIREMENTS)
    assert dict(requirements) == _lifecycle.declared_requirements(listing)
    assert dict(requirements) == {"acme-base": "[>=0.1 <1]"}
    assert answer(listing, MODULE_ROOTS) == _lifecycle.module_roots(listing) == ()
    assert answer(listing, PUBLIC_MODULES) == ()


def test_the_conan_steps_stamp_build_and_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One member through conan's steps."""
    from livery.extensions.conan import _package
    from livery.workshop._registries import RegistryTarget

    listing = _member(tmp_path / "listing", ("cmake", "conan"))
    assert _lifecycle.stamp(listing, tmp_path, "2.0.0") == ["conanfile.py"]
    assert 'version = "2.0.0"' in (listing.directory / "conanfile.py").read_text()
    built: list[str] = []

    def _build(package: Package, root: Path, *, epoch: int = 0) -> Path:
        del root, epoch
        built.append(package.path)
        return package.directory / "build"

    # The step calls the extension's own build, which conan create runs.
    monkeypatch.setattr(_package, "build", _build)
    assert (
        _lifecycle.build_release(listing, tmp_path, epoch=7)
        == listing.directory / "build"
    )
    assert built == ["packages/listing"]
    uploads: list[tuple[str, str, str]] = []

    def _publish(
        package: Package, url: str, *, version: str, local: bool, token: str = ""
    ) -> bool:
        del url, local
        uploads.append((package.path, version, token))
        return True

    monkeypatch.setattr(_package, "publish", _publish)
    target = RegistryTarget(kind="conan", url="https://conan.example/remote", token="t")
    assert _lifecycle.publish(listing, tmp_path, version="2.0.0", target=target)
    assert uploads == [("packages/listing", "2.0.0", "t")]


# --- the conan workspace file ---------------------------------------------------


def test_the_conan_workspace_comes_with_the_first_conan_member_and_goes_with_the_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "workshop.toml").write_text("[workspace]\nextensions = []\n")
    monkeypatch.setattr(
        "livery.workshop._extensions.workspace_root", lambda start=None: root
    )
    deliver(root)
    assert not (root / "conanws.yml").exists()
    member = root / "packages" / "geometry"
    member.mkdir(parents=True)
    (member / "workshop.toml").write_text(
        'extensions = ["cmake", "conan"]\nname = "acme-geometry"\n'
    )
    # The recipe's reference is what the workspace lists a member by.
    (member / "conanfile.py").write_text('name = "acme-geometry"\nversion = "0.0.0"\n')
    assert "  wrote conanws.yml" in deliver(root)
    text = (root / "conanws.yml").read_text()
    assert text.endswith("packages:\n  - path: packages/geometry\n")
    assert shipped_drift(root) == []
    # A second member listing conan joins the same file.
    listing = root / "packages" / "mesh"
    listing.mkdir(parents=True)
    (listing / "workshop.toml").write_text(
        'name = "acme-mesh"\nextensions = ["cmake", "conan"]\n'
    )
    (listing / "conanfile.py").write_text('name = "acme-mesh"\nversion = "0.0.0"\n')
    deliver(root)
    text = (root / "conanws.yml").read_text()
    assert text.endswith(
        "packages:\n  - path: packages/geometry\n  - path: packages/mesh\n"
    )
    assert shipped_drift(root) == []
    # The last members leave, and the file goes with them.
    shutil.rmtree(listing)
    shutil.rmtree(member)
    assert any(line.startswith("  removed conanws.yml") for line in deliver(root))
    assert not (root / "conanws.yml").exists()
