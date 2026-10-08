"""Each workspace check runs when the files it reads change, on those files."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from livery.footman import Failed
from livery.workshop._graph import reaches_no_package
from livery.workshop._influence import WHOLE, Changes, Inputs, Selection, select

_FAILURES = (SystemExit, Failed)

PAGES = Inputs(reads=("docs/**/*.md",))


def _changes(root: Path, *paths: str) -> Changes:
    for path in paths:
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text("x\n")
    return Changes(root, paths)


# What widens a check to every file comes first: a wrong answer there
# lets a change slip past the check that should have read it.


def test_a_run_that_knows_nothing_of_the_change_judges_everything() -> None:
    assert select(PAGES, None) == WHOLE


def test_a_change_to_the_checks_own_code_judges_everything(tmp_path: Path) -> None:
    changes = _changes(tmp_path, "packages/workshop/src/livery/workshop/_x.py")
    assert select(PAGES, changes, provider="packages/workshop") == WHOLE
    # The member's tests are not the check's code.
    tests = _changes(tmp_path, "packages/workshop/tests/test_x.py")
    assert not select(PAGES, tests, provider="packages/workshop").runs


def test_a_checks_own_code_is_found_through_the_name_it_is_listed_by(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A declared check registers under its extension's listed name, which
    # is no module: "docs" finds the root's docs directory and "pytest" the
    # index's pytest. The member that ships it is found through the
    # package its entry point names, so a change to its sources still
    # judges everything.
    from importlib.metadata import EntryPoint

    from livery.workshop import _extensions
    from livery.workshop._checks import CheckRecord, GateContext, selected
    from livery.workshop._packages import discover_packages

    member = tmp_path / "packages" / "site"
    (member / "src" / "acme_site").mkdir(parents=True)
    (member / "src" / "acme_site" / "__init__.py").write_text("")
    (member / "workshop.toml").write_text('kind = "python"\nname = "acme-site"\n')
    (member / "pyproject.toml").write_text('[project]\nname = "acme-site"\n')
    (tmp_path / "workshop.toml").write_text("[workspace]\n")
    monkeypatch.syspath_prepend(str(member / "src"))
    entry = EntryPoint("site", "acme_site", _extensions.GROUP)
    monkeypatch.setattr(_extensions, "_declared", lambda: {"site": entry})
    packages = discover_packages(tmp_path)
    assert _extensions.extension_provider("site", packages) == "packages/site"
    assert _extensions.extension_provider("absent", packages) == ""
    record = CheckRecord(
        "site", "lint", lambda ctx: None, inputs=PAGES, extension="site"
    )
    changes = _changes(tmp_path, "packages/site/src/acme_site/rule.py")
    ctx = GateContext(root=tmp_path, packages=packages, changes=changes)
    assert selected(record, ctx) == WHOLE


def test_an_installed_check_reads_everything_when_the_lock_moves(
    tmp_path: Path,
) -> None:
    assert select(PAGES, _changes(tmp_path, "uv.lock")) == WHOLE
    # A member's check moves with its sources, not with the lock.
    assert not select(PAGES, _changes(tmp_path, "uv.lock"), provider="p").runs


def test_a_widening_input_judges_everything(tmp_path: Path) -> None:
    inputs = Inputs(reads=("out/*",), widens=("workshop.toml",))
    assert select(inputs, _changes(tmp_path, "workshop.toml")) == WHOLE


def test_a_removed_file_widens_a_check_that_asks(tmp_path: Path) -> None:
    removed = Changes(tmp_path, ("gone/elsewhere.txt",))
    assert select(Inputs(reads=PAGES.reads, on_removal=True), removed) == WHOLE
    assert not select(PAGES, removed).runs


def test_the_checks_own_reason_widens_it(tmp_path: Path) -> None:
    inputs = Inputs(reads=PAGES.reads, widen=lambda changes: True)
    assert select(inputs, _changes(tmp_path, "notes/a.md")) == WHOLE


def test_an_ignored_file_never_counts(tmp_path: Path) -> None:
    inputs = Inputs(reads=("pkg/*",), per_file=False, ignores=("**/*.md",))
    assert not select(inputs, _changes(tmp_path, "pkg/README.md")).runs
    assert select(inputs, _changes(tmp_path, "pkg/pyproject.toml")) == WHOLE


def test_the_changed_files_a_check_reads_are_judged_alone(tmp_path: Path) -> None:
    changes = _changes(tmp_path, "docs/a.md", "notes/b.md", "docs/c.txt")
    assert select(PAGES, changes) == Selection(whole=False, files=("docs/a.md",))
    whole = Inputs(reads=PAGES.reads, per_file=False)
    assert select(whole, changes) == WHOLE
    assert not select(PAGES, _changes(tmp_path, "notes/b.md")).runs


# --- the root files no package's checks read ----------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "LICENSE",
        ".github/CODEOWNERS",
        ".github/workflows/ci.yml",
        ".gitlab-ci.yml",
        ".vscode/settings.json",
        ".claude/settings.json",
        ".gitattributes",
        ".gitignore",
        "setup.sh",
        ".release-manifest.json",
        "overrides/main.html",
        ".workshop-rendered",
    ],
)
def test_a_root_file_no_package_reads_affects_no_package(path: str) -> None:
    assert reaches_no_package(path)


@pytest.mark.parametrize(
    "path", ["pyproject.toml", "tasks.py", "workshop.toml", "uv.lock", "toolroom.lock"]
)
def test_a_root_file_every_gate_reads_still_reaches_them(path: str) -> None:
    assert not reaches_no_package(path)


# --- each check judges what it was handed -------------------------------------


def test_a_removed_heading_has_every_page_judged(tmp_path: Path) -> None:
    from livery.extensions.docs._checks import headings_removed

    root = tmp_path / "ws"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    page = root / "docs" / "guide.md"
    page.parent.mkdir()
    page.write_text("# Guide\n\n## Install\n\n## Use\n")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.email=t@x",
            "-c",
            "user.name=T",
            "commit",
            "-q",
            "-m",
            "seed",
        ],
        check=True,
    )
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    page.write_text("# Guide\n\n## Install\n\n## Use\n\n## More\n")
    assert not headings_removed(Changes(root, ("docs/guide.md",), head))
    page.write_text("# Guide\n\n## Use\n")
    assert headings_removed(Changes(root, ("docs/guide.md",), head))


def test_the_link_check_judges_the_pages_it_was_handed(tmp_path: Path) -> None:
    from livery.extensions.docs._checks import link_problems

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "good.md").write_text("[ok](bad.md)\n")
    (docs / "bad.md").write_text("[broken](nowhere.md)\n")
    assert link_problems(tmp_path, frozenset({"docs/good.md"})) == []
    assert link_problems(tmp_path, frozenset({"docs/bad.md"})) == [
        "docs/bad.md: nowhere.md does not exist"
    ]


def test_drift_judges_only_the_files_it_was_handed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop import _templates

    monkeypatch.setattr(_templates, "_root", lambda: tmp_path)
    monkeypatch.setattr(
        "livery.workshop._shipped_files.shipped_drift",
        lambda root: ["pyproject.toml: differs from its render"],
    )
    monkeypatch.setattr(
        _templates,
        "project_drift",
        lambda root: [".github/workflows/ci.yml: differs from its generation"],
    )
    _templates.drift_check(files=frozenset({"tasks.py"}))
    with pytest.raises(_FAILURES) as caught:
        _templates.drift_check(files=frozenset({"pyproject.toml"}))
    assert "pyproject.toml: differs" in str(caught.value)
    assert "ci.yml" not in str(caught.value)
    with pytest.raises(_FAILURES):
        _templates.drift_check()


def test_the_import_rules_judge_the_files_they_were_handed(tmp_path: Path) -> None:
    from livery.workshop._ast_rules import (
        AstRule,
        ParsedModule,
        RuleContext,
        register_ast_rule,
        unregister_ast_rule,
    )
    from livery.workshop._packages import verify_imports

    member = tmp_path / "packages" / "tool"
    (member / "src").mkdir(parents=True)
    (member / "workshop.toml").write_text('kind = "python"\nname = "acme-tool"\n')
    (member / "pyproject.toml").write_text('[project]\nname = "acme-tool"\n')
    (member / "src" / "a.py").write_text("x = 1\n")
    (member / "src" / "b.py").write_text("y = 1\n")
    seen: list[str] = []

    def judge(modules: tuple[ParsedModule, ...], context: RuleContext) -> list[str]:
        del context
        seen.extend(module.relative for module in modules)
        return []

    register_ast_rule(AstRule("acme-seen", judge, extension="acme.brand"))
    try:
        verify_imports(tmp_path, frozenset({"packages/tool/src/a.py"}))
        assert seen == ["packages/tool/src/a.py"]
        seen.clear()
        verify_imports(tmp_path)
        assert sorted(seen) == ["packages/tool/src/a.py", "packages/tool/src/b.py"]
    finally:
        unregister_ast_rule("acme-seen")
