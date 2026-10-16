"""The site's phases: the docs phase before a build, the deploy phase before a publish.

The refusals and fallbacks first: a failing step, a step for an
extension the workspace does not list, a step that no longer runs, a
dormant drawer. Then the steps side by side, and the publish that runs
the deploy phase.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from livery.extensions.docs import _site as _docs
from livery.footman import Failed, fail
from livery.workshop import PhaseContext
from livery.workshop._packages import discover_packages
from livery.workshop._phases import walk_steps
from livery.workshop._steps import StepRecord, register_step, steps_by_name
from workshop_coverage_meter import install_meter, leave_part, stored_unit
from workshop_installed import skip_unless_installed

_FAILURES = (SystemExit, Failed)

#: The member's one measured source, and what the coverage pages draw.
SOURCE = "packages/core/src/acme/core/mod.py"


def _workspace(tmp_path: Path, listed: str = '["docs"]') -> Path:
    """A workspace listing *listed*, its member core declaring an htmlcov report.

    The member advertises a task provider and places the ``tasks`` block,
    so the task reference has something to render.
    """
    root = tmp_path / "ws"
    core = root / "packages" / "core"
    (core / "src" / "acme" / "core").mkdir(parents=True)
    (core / "src" / "acme" / "core" / "__init__.py").write_text("")
    (root / SOURCE).write_text("a = 1\nb = 2\n")
    (root / "docs").mkdir()
    (root / "docs" / "index.md").write_text("# Home\n")
    (root / "workshop.toml").write_text(f"[workspace]\nextensions = {listed}\n")
    (root / "pyproject.toml").write_text('[project]\nname = "acme-home"\n')
    (core / "workshop.toml").write_text(
        'extensions = ["python"]\nname = "acme-core"\n\n[docs]\n'
        'coverage = [{ label = "Python", path = "htmlcov" }]\n'
    )
    (core / "pyproject.toml").write_text(
        '[project]\nname = "acme-core"\n\n[project.entry-points."footman.tasks"]\n'
        '"acme.core" = "acme.core._tasks"\n'
    )
    (core / "docs").mkdir()
    (core / "docs" / "index.md").write_text("# core\n")
    (core / "docs" / "nav.toml").write_text(
        'nav = [\n    { "Index" = "index.md" },\n'
        "    # nav:begin tasks\n    # nav:end tasks\n]\n"
    )
    return root


def _mount(root: Path) -> None:
    """Mount what *root* lists, so each phase's steps register as a verb finds them."""
    from livery.footman import _registry as registry
    from livery.workshop._extensions import mount_extensions

    with registry.capture():
        mount_extensions(root)


def _measured(root: Path) -> None:
    """Leave a native measurer's part for the member's source, as a gate run would."""
    leave_part(root, "packages/core", {SOURCE: {1: 1, 2: 0}})


def _failed(capsys: pytest.CaptureFixture[str], step: str) -> bool:
    """Whether the walk's lines captured so far say *step* failed."""
    captured = capsys.readouterr()
    return any(
        line.startswith("FAIL") and step in line.split()
        for line in (captured.out + captured.err).splitlines()
    )


# The refusals and fallbacks first.


def test_a_failing_docs_step_names_it_and_the_build_is_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _workspace(tmp_path)
    _mount(root)
    ran: list[str] = []

    def broken(ctx: PhaseContext) -> None:
        del ctx
        fail("the pages could not be written")

    def beside(ctx: PhaseContext) -> None:
        ran.append(ctx.step)

    register_step(StepRecord("broken", "docs", broken, extension="acme.broken"))
    register_step(StepRecord("beside", "docs", beside, extension="acme.beside"))
    monkeypatch.chdir(root)
    with pytest.raises(_FAILURES, match="the pages could not be written"):
        _docs.docs_build()
    # The walk names the step that failed, a step beside it still ran,
    # and the build stops before it assembles the config.
    assert _failed(capsys, "docs.broken")
    assert ran == ["docs.beside"]
    assert not (root / _docs.SITE_CONFIG).exists()


def test_a_docs_step_for_an_unlisted_extension_is_never_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.extensions.coverage import _pages

    install_meter(tmp_path, monkeypatch)
    asked: list[str] = []

    def drawn(root: Path, *, merged: bool) -> list[str]:
        del root
        asked.append("deploy" if merged else "docs")
        return []

    monkeypatch.setattr(_pages, "draw", drawn)

    def note(ctx: PhaseContext) -> None:
        asked.append(f"{ctx.step} {ctx.member.path}")

    def both_phases(root: Path) -> None:
        _docs.run_site_phase(root, _docs.DOCS_PHASE)
        _docs.run_site_phase(root, _docs.DEPLOY_PHASE)

    # Coverage listed with an entry that leaves docs out: its [for.docs]
    # table adds no step to either phase. A package step naming an
    # extension no package holds runs over no package.
    root = _workspace(
        tmp_path, '["docs", "acme.meter", { name = "coverage", without = ["docs"] }]'
    )
    _mount(root)
    assert not {"docs.coverage", "deploy.coverage"} & set(steps_by_name())
    register_step(
        StepRecord(
            "held",
            "docs",
            note,
            scope="package",
            extensions=("acme.nobody",),
            extension="acme.held",
        )
    )
    both_phases(root)
    assert asked == []
    # Listed for docs, coverage is asked in each phase, and a package
    # step naming the member's extension runs over the member.
    (root / "workshop.toml").write_text(
        '[workspace]\nextensions = ["docs", "acme.meter", "coverage"]\n'
    )
    _mount(root)
    register_step(
        StepRecord(
            "held",
            "docs",
            note,
            scope="package",
            extensions=("python",),
            extension="acme.held",
        )
    )
    both_phases(root)
    assert sorted(asked) == ["deploy", "docs", "docs.held packages/core"]


def test_a_step_no_longer_listed_leaves_no_pages_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    from livery.extensions.docs import _taskref
    from livery.workshop import _extensions, _points, _steps

    skip_unless_installed("mkdocstrings")
    row = {"help": "One line.", "params": []}
    tree: dict[str, object] = {
        "help": "",
        "groups": {"acme": {"help": "", "tasks": {"page": row}, "groups": {}}},
        "tasks": {},
    }
    monkeypatch.setattr(_taskref, "provider_tree", lambda _root, _identity: tree)
    monkeypatch.setattr(shutil, "which", lambda name: "/stub/fm")
    root = _workspace(tmp_path, '["docs", "mkdocstrings"]')
    contract = root / "workshop.toml"
    contract.write_text(
        contract.read_text() + "\n[phases.docs.task-reference]\nenabled = true\n"
    )
    generated = root / "packages" / "core" / "docs" / "_generated"
    # A build with the API reference listed and the task reference on
    # writes both, each with the nav block the site places.
    _mount(root)
    _docs._generate_all(root)
    written = ("api/index.md", "nav.api.toml", "tasks/index.md", "nav.tasks.toml")
    assert all((generated / name).is_file() for name in written)
    config = (root / _docs.SITE_CONFIG).read_text()
    assert "packages/core/api/index.md" in config
    assert "packages/core/tasks/index.md" in config
    # The reference unlisted and the task reference off, the next build,
    # a process of its own, writes neither, and what the earlier build
    # wrote is gone: no page, no block, no nav entry and no mounted page.
    contract.write_text('[workspace]\nextensions = ["docs"]\n')
    _steps.restore(_steps.snapshot(initial=True))
    _points.restore(_points.snapshot(initial=True))
    _extensions.restore(_extensions.snapshot(initial=True))
    _mount(root)
    assert not {"docs.mkdocstrings", "docs.task-reference"} & set(
        _steps.steps_by_name()
    )
    _docs._generate_all(root)
    assert not [name for name in written if (generated / name).exists()]
    assert not (generated / "api").exists() and not (generated / "tasks").exists()
    config = (root / _docs.SITE_CONFIG).read_text()
    assert "packages/core/api/" not in config
    assert "packages/core/tasks/" not in config
    mounted = root / "docs" / "packages" / "core"
    assert (mounted / "index.md").is_file()
    assert not (mounted / "api").exists() and not (mounted / "tasks").exists()


def _remount(root: Path) -> None:
    """Mount *root* afresh, as the next build's process of its own does."""
    from livery.workshop import _extensions, _points, _steps

    _steps.restore(_steps.snapshot(initial=True))
    _points.restore(_points.snapshot(initial=True))
    _extensions.restore(_extensions.snapshot(initial=True))
    _mount(root)


def _generator(root: Path, entry: str) -> None:
    """Make *entry* the one generator the member lists under ``[docs] generators``."""
    contract = root / "packages" / "core" / "workshop.toml"
    lines = contract.read_text().splitlines(keepends=True)
    text = "".join(line for line in lines if not line.startswith("generators = "))
    contract.write_text(text.replace("[docs]\n", f"[docs]\ngenerators = [{entry}]\n"))


def test_a_generator_output_outside_the_root_refuses_before_any_step_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._phases import PhaseError

    root = _workspace(tmp_path)
    _generator(root, '{ verb = "acme.index", outputs = ["../index/"] }')
    _mount(root)
    ran: list[str] = []

    def note(ctx: PhaseContext) -> None:
        ran.append(ctx.step)

    register_step(StepRecord("note", "docs", note, extension="acme.note"))
    with pytest.raises(
        PhaseError,
        match=r"^an output of the docs phase names '\.\./index/', which leaves the"
        r" root",
    ):
        _docs.run_site_phase(root, _docs.DOCS_PHASE)
    assert ran == []


def test_a_generator_no_package_lists_any_more_leaves_no_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import shutil

    import livery.footman as footman

    def generate(argv: list[str], **kwargs: object) -> int:
        # The index generator writes its index under the root's docs/tools/.
        if argv[-1] == "acme.index":
            index = Path(str(kwargs["cwd"])) / "docs" / "tools" / "index.json"
            index.parent.mkdir(parents=True, exist_ok=True)
            index.write_text(f"{argv[-1]}\n")
        return 0

    monkeypatch.setattr(footman, "run", generate)
    monkeypatch.setattr(shutil, "which", lambda name: "/stub/fm")
    root = _workspace(tmp_path)
    _generator(root, '{ verb = "acme.index", outputs = ["docs/tools/"] }')
    _mount(root)
    _docs.run_site_phase(root, _docs.DOCS_PHASE)
    assert (root / "docs" / "tools" / "index.json").read_text() == "acme.index\n"
    # Listed again, the generator finds its index as it left it.
    (root / "docs" / "tools" / "kept.json").write_text("{}\n")
    _docs.run_site_phase(root, _docs.DOCS_PHASE)
    assert (root / "docs" / "tools" / "kept.json").is_file()
    capsys.readouterr()
    # The member lists it no more: the next build removes the index.
    _generator(root, '"acme.other"')
    _docs.run_site_phase(root, _docs.DOCS_PHASE)
    assert not (root / "docs" / "tools").exists()
    assert "  removed docs/tools/: no running step of the docs phase writes it\n" in (
        capsys.readouterr().out
    )


def test_the_task_reference_turned_off_leaves_no_redirect_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    from livery.extensions.docs import _taskref
    from livery.workshop import _steps

    row = {"help": "One line.", "params": []}
    tree: dict[str, object] = {
        "help": "",
        "groups": {"acme": {"help": "", "tasks": {"page": row}, "groups": {}}},
        "tasks": {},
    }
    monkeypatch.setattr(_taskref, "provider_tree", lambda _root, _identity: tree)
    monkeypatch.setattr(shutil, "which", lambda name: "/stub/fm")
    root = _workspace(tmp_path)
    contract = root / "workshop.toml"
    listed = contract.read_text()
    contract.write_text(listed + "\n[phases.docs.task-reference]\nenabled = true\n")
    _remount(root)
    assert _steps.steps_by_name()["docs.task-reference"].outputs == ("docs/tasks/",)
    _docs.run_site_phase(root, _docs.DOCS_PHASE)
    assert (root / "docs" / "tasks" / "acme-page.md").is_file()
    # Turned off, the step no longer runs, and its redirect pages go.
    contract.write_text(listed)
    _remount(root)
    assert "docs.task-reference" not in _steps.steps_by_name()
    _docs.run_site_phase(root, _docs.DOCS_PHASE)
    assert not (root / "docs" / "tasks").exists()


def test_coverage_unlisted_leaves_no_coverage_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _steps

    install_meter(tmp_path, monkeypatch)
    monkeypatch.setattr("livery.workshop._ci_context.run_context", lambda: None)
    monkeypatch.setattr("livery.workshop.ci_run", lambda: None)
    root = _workspace(tmp_path, '["docs", "acme.meter", "coverage"]')
    _measured(root)
    _remount(root)
    assert _steps.steps_by_name()["docs.coverage"].outputs == ("package/htmlcov/",)
    _docs.run_site_phase(root, _docs.DOCS_PHASE)
    tree = root / "packages" / "core" / "htmlcov"
    assert (tree / "index.html").is_file()
    # Coverage unlisted, its step no longer runs, and the tree it drew goes.
    (root / "workshop.toml").write_text('[workspace]\nextensions = ["docs"]\n')
    _remount(root)
    assert "docs.coverage" not in _steps.steps_by_name()
    _docs.run_site_phase(root, _docs.DOCS_PHASE)
    assert not tree.exists()


def test_a_dormant_coverage_draws_nothing_and_the_page_states_the_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._extensions import dormancy

    monkeypatch.setattr("livery.workshop._ci_context.run_context", lambda: None)
    monkeypatch.setattr("livery.workshop.ci_run", lambda: None)
    root = _workspace(tmp_path, '["docs", "coverage"]')
    _measured(root)
    # Nothing listed measures, so coverage is dormant: its steps never
    # register, though a measurer's data is there to draw.
    assert set(dormancy(root)) == {"coverage"}
    _mount(root)
    assert not {"docs.coverage", "deploy.coverage"} & set(steps_by_name())
    _docs.run_site_phase(root, _docs.DOCS_PHASE)
    assert not (root / "packages" / "core" / "htmlcov").exists()
    assert _docs.generate_coverage_pages(root) == ["core"]
    page = root / "packages" / "core" / "docs" / "_generated" / "coverage.md"
    assert "The declared report (`htmlcov`) was not produced in this build." in (
        page.read_text()
    )


# Then the steps side by side.


def _tree(root: Path) -> dict[str, bytes]:
    """Every file under *root* by its path from it, with its bytes."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_the_docs_steps_write_apart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    import livery.footman as footman
    from livery.extensions.docs import _taskref

    skip_unless_installed("mkdocstrings")
    install_meter(tmp_path, monkeypatch)
    monkeypatch.setattr("livery.workshop._ci_context.run_context", lambda: None)
    row = {"help": "One line.", "params": []}
    tree: dict[str, object] = {
        "help": "",
        "groups": {"acme": {"help": "", "tasks": {"page": row}, "groups": {}}},
        "tasks": {},
    }
    monkeypatch.setattr(_taskref, "provider_tree", lambda _root, _identity: tree)
    monkeypatch.setattr(shutil, "which", lambda name: "/stub/fm")

    def generate(argv: list[str], **kwargs: object) -> int:
        # The member's generator writes one page into its generated tree.
        written = Path(str(kwargs["cwd"])) / "packages/core/docs/_generated/made.md"
        written.parent.mkdir(parents=True, exist_ok=True)
        written.write_text(f"# Made by {argv[-1]}\n")
        return 0

    monkeypatch.setattr(footman, "run", generate)

    def workspace(name: str) -> Path:
        root = _workspace(
            tmp_path / name, '["docs", "mkdocstrings", "acme.meter", "coverage"]'
        )
        (root / "workshop.toml").write_text(
            (root / "workshop.toml").read_text()
            + "\n[phases.docs.task-reference]\nenabled = true\n"
        )
        contract = root / "packages" / "core" / "workshop.toml"
        contract.write_text(
            contract.read_text().replace(
                "[docs]\n", '[docs]\ngenerators = ["acme.page"]\n'
            )
        )
        _measured(root)
        return root

    # Each step alone, over a workspace of its own: what each writes.
    alone = workspace("alone")
    _mount(alone)
    steps = [record for record in steps_by_name().values() if record.phase == "docs"]
    # In composition order: the docs extension's own, then coverage's
    # table for it, grafted once docs mounts, then mkdocstrings'.
    assert [record.name for record in steps] == [
        "docs.task-reference",
        "docs.generators",
        "docs.coverage",
        "docs.mkdocstrings",
    ]
    members = discover_packages(alone)
    written: dict[str, set[str]] = {}
    for record in steps:
        before = _tree(alone)
        walk_steps(PhaseContext(root=alone, packages=members, phase="docs"), (record,))
        after = _tree(alone)
        written[record.name] = {
            path for path, held in after.items() if before.get(path) != held
        }
    assert all(written.values())
    names = list(written)
    for index, name in enumerate(names):
        for other in names[index + 1 :]:
            assert not written[name] & written[other], (name, other)
    # All of them side by side, as a build runs the phase, write the same.
    together = workspace("together")
    _mount(together)
    _docs.run_site_phase(together, _docs.DOCS_PHASE)
    made, wrote = _tree(together), _tree(alone)
    for path in sorted({path for paths in written.values() for path in paths}):
        assert made[path] == wrote[path], path


# And the publish.


def test_the_publish_runs_the_deploy_phase_first_and_a_failing_step_stops_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _workspace(tmp_path)
    (root / "workshop.toml").write_text(
        '[workspace]\nextensions = ["docs"]\n\n[docs]\npublish = "container"\n'
    )
    _mount(root)
    site = root / "site"
    site.mkdir()
    (site / "index.html").write_text("<h1>site</h1>\n")
    events: list[str] = []
    monkeypatch.setattr(_docs, "_publish_container", lambda at: events.append("pushed"))

    def draw(ctx: PhaseContext) -> None:
        built = (ctx.root / "site" / "index.html").is_file()
        events.append(f"{ctx.step} on {'the built site' if built else 'nothing'}")

    register_step(StepRecord("draw", "deploy", draw, extension="acme.draw"))
    monkeypatch.chdir(root)
    _docs.docs_publish()
    assert events == ["deploy.draw on the built site", "pushed"]
    # A failing step stops the publish and is named; nothing is pushed.
    events.clear()

    def broken(ctx: PhaseContext) -> None:
        del ctx
        fail("main's record could not be read")

    register_step(StepRecord("broken", "deploy", broken, extension="acme.broken"))
    with pytest.raises(_FAILURES, match="main's record could not be read"):
        _docs.docs_publish()
    assert _failed(capsys, "deploy.broken")
    assert events == ["deploy.draw on the built site"]


def test_the_publish_draws_main_s_record_into_the_built_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from livery.extensions.coverage import _pages

    install_meter(tmp_path, monkeypatch)
    monkeypatch.setattr("livery.workshop._ci_context.run_context", lambda: None)
    root = _workspace(tmp_path, '["docs", "acme.meter", "coverage"]')
    _mount(root)
    assert {"docs.coverage", "deploy.coverage"} <= set(steps_by_name())
    # Main's record holds the member's part as a leg made it; this
    # machine holds no data at all.
    written = leave_part(root, "packages/core", {SOURCE: {1: 3, 2: 0}})
    part = json.loads(written.read_text())
    written.unlink()
    held = {"check-a": {"packages/core": stored_unit("packages/core", (part,))}}
    monkeypatch.setattr(_pages, "_stored", lambda at: (held, []))
    site = root / "site"
    (site / "packages" / "core" / "coverage").mkdir(parents=True)
    (site / "index.html").write_text("<h1>site</h1>\n")
    monkeypatch.chdir(root)
    _docs.docs_publish()
    placed = site / "packages" / "core" / "coverage" / "python" / "index.html"
    assert "from main&#x27;s coverage record" in placed.read_text()
    out = capsys.readouterr().out
    assert "  coverage: pages drawn for core\n" in out
    assert "  site refreshed for core\n" in out
