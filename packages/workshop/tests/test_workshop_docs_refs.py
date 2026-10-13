"""The gate judges docstring cross-references against the anchors the site renders."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from livery.extensions.docs._refs import (
    Reference,
    docstring_references,
    reference_problems,
)
from livery.extensions.docs._steps import docrefs_run
from livery.footman import Failed
from livery.workshop._phases import PhaseContext
from workshop_extension_fakes import fake_extensions

IMPL = '''"""The widget's home."""


class Widget:
    """A widget."""

    size: int = 1

    def turn(self) -> None:
        """Turn it."""


def _spin() -> None:
    """Spin it, out of the reference's sight."""


VALUE = 3
"""A module attribute."""
'''

INIT = '''"""The thing's public names."""

from acme.extensions.tool._leg import helper
from acme.thing._impl import Widget

__all__ = ["Widget", "helper"]
'''

LEG = '''"""The tool's leg: a module of an extension that ships in the thing's wheel."""


def helper() -> None:
    """Help the leg."""


def top_up() -> None:
    """Top the suites up."""
'''

TASKS = '''"""The tool's verbs."""

from livery.footman import group

tool_group = group("tool", help="The tool's verbs")


@tool_group.task(name="leg")
def tool_leg() -> None:
    """{docstring}"""


@tool_group.task(name="quiet")
def tool_quiet() -> None:
    pass


def _helper() -> None:
    """Help no task."""
'''

#: The line of TASKS a task docstring's third line lands on.
TASK_LINE = 12


def _workspace(tmp_path: Path, *, extensions: str = '["docs"]') -> Path:
    """A workspace whose member ``thing`` ships ``acme.thing`` and an extension.

    ``acme.thing`` is a regular package, so the API reference has a page
    per module of it. ``acme.extensions.tool`` ships in the same wheel
    with no package of its own, so the reference has no page of it.
    """
    root = tmp_path / "ws"
    member = root / "packages" / "thing"
    package = member / "src" / "acme" / "thing"
    tool = member / "src" / "acme" / "extensions" / "tool"
    package.mkdir(parents=True)
    tool.mkdir(parents=True)
    (root / "workshop.toml").write_text(f"[workspace]\nextensions = {extensions}\n")
    (root / "pyproject.toml").write_text('[project]\nname = "acme-home"\n')
    (member / "workshop.toml").write_text('kind = "python"\nname = "acme-thing"\n')
    (member / "pyproject.toml").write_text('[project]\nname = "acme-thing"\n')
    (package / "__init__.py").write_text(INIT)
    (package / "_impl.py").write_text(IMPL)
    (tool / "_leg.py").write_text(LEG)
    return root


def _problems(root: Path, docstring: str) -> list[str]:
    """The refusals once ``acme.thing._user``, which has a page, carries *docstring*."""
    user = root / "packages" / "thing" / "src" / "acme" / "thing" / "_user.py"
    user.write_text(f'"""{docstring}"""\n')
    return reference_problems(root)


def _refusal(place: str, path: str, page: str) -> str:
    """The line refusing *path* at *place*, its docstring rendered on *page*."""
    return (
        f"{place}: [{path}][] has no anchor on the site, where the docstring"
        f" renders on {page}; write the name as plain code instead: `{path}`"
    )


def _advertise(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    docstring: str,
    *,
    reference: bool = True,
) -> None:
    """The thing advertises the tool's verbs, installed where the child finds them.

    With *reference*, the thing declares the task reference among its
    docs generators, so the site renders a page per task.
    """
    member = root / "packages" / "thing"
    tasks = member / "src" / "acme" / "extensions" / "tool" / "_tasks.py"
    tasks.write_text(TASKS.format(docstring=docstring))
    (member / "pyproject.toml").write_text(
        '[project]\nname = "acme-thing"\n\n[project.entry-points."footman.tasks"]\n'
        '"acme.extensions.tool" = "acme.extensions.tool._tasks"\n'
    )
    if reference:
        (member / "workshop.toml").write_text(
            'kind = "python"\nname = "acme-thing"\n\n'
            '[docs]\ngenerators = ["docs.task-reference"]\n'
        )
    site = root.parent / "site-packages"
    dist = site / "acme_thing-0.1.dist-info"
    dist.mkdir(parents=True)
    (dist / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: acme-thing\nVersion: 0.1\n"
    )
    (dist / "entry_points.txt").write_text(
        "[footman.tasks]\nacme.extensions.tool = acme.extensions.tool._tasks\n"
    )
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(site), str(member / "src")]))


# The refusals first: each names an object the site renders no anchor for.


def test_a_task_docstring_naming_an_extensions_private_function_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    _advertise(
        root,
        monkeypatch,
        "Run the leg.\n\n"
        "    It tops up first ([acme.extensions.tool._leg.top_up][]).\n    ",
    )
    assert reference_problems(root) == [
        _refusal(
            f"packages/thing/src/acme/extensions/tool/_tasks.py:{TASK_LINE}",
            "acme.extensions.tool._leg.top_up",
            "packages/thing/tasks/tool/leg.md",
        )
    ]


def test_a_module_docstring_naming_an_underscore_function_on_an_api_page_is_refused(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    assert _problems(root, "Built on [acme.thing._impl._spin][].") == [
        _refusal(
            "packages/thing/src/acme/thing/_user.py:1",
            "acme.thing._impl._spin",
            "packages/thing/api/_user.md",
        )
    ]
    # The step refuses with every refusal under its heading.
    with pytest.raises(Failed, match="cross-references the site cannot link"):
        docrefs_run(PhaseContext(root=root, packages=()))


@pytest.mark.parametrize(
    "reference",
    [
        "acme.extensions.tool._leg.top_up",  # a module the reference has no page of
        "acme.thing._impl.Gadget",  # a missing attribute
        "acme.thing._impl.turn",  # a method named without its class
        "acme.thnig._impl.Widget",  # a misspelt module
        "acme.thing.Gadget",  # a re-export that is not there
    ],
)
def test_a_reference_the_site_renders_no_anchor_for_is_refused(
    tmp_path: Path, reference: str
) -> None:
    root = _workspace(tmp_path)
    assert _problems(root, f"See [{reference}][].") == [
        _refusal(
            "packages/thing/src/acme/thing/_user.py:1",
            reference,
            "packages/thing/api/_user.md",
        )
    ]


def test_an_import_the_module_does_not_export_has_no_anchor(tmp_path: Path) -> None:
    # The site links such a name only when griffe happened to resolve the
    # import before the page was built, which the page order decides.
    root = _workspace(tmp_path)
    package = root / "packages" / "thing" / "src" / "acme" / "thing"
    (package / "_other.py").write_text(
        '"""Another home."""\n\nfrom acme.thing._impl import Widget\n'
    )
    assert _problems(root, "See [acme.thing._other.Widget][].") == [
        _refusal(
            "packages/thing/src/acme/thing/_user.py:1",
            "acme.thing._other.Widget",
            "packages/thing/api/_user.md",
        )
    ]


def test_a_probe_that_fails_refuses_naming_its_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    _advertise(root, monkeypatch, "Run the leg: [acme.thing.Widget][].")
    tasks = root / "packages" / "thing" / "src" / "acme" / "extensions" / "tool"
    (tasks / "_tasks.py").write_text("raise RuntimeError('no import here')\n")
    with pytest.raises(Failed, match=r"probe exited 1(.|\n)*no import here"):
        _problems(root, "See [acme.thing.Widget][].")


# What passes: an object a page renders, and what the gate leaves alone.


@pytest.mark.parametrize(
    "reference",
    [
        "acme.thing._impl",  # a module, on its own page
        "acme.thing._impl.Widget",  # a class
        "acme.thing._impl.Widget.turn",  # a method through its class
        "acme.thing._impl.Widget.size",  # a class attribute
        "acme.thing._impl.VALUE",  # a module attribute
        "acme.thing.Widget",  # a re-export the package lists in __all__
        "acme.extensions.tool._leg.helper",  # pageless, its re-export on a page
    ],
)
def test_a_reference_to_an_object_a_page_renders_passes(
    tmp_path: Path, reference: str
) -> None:
    root = _workspace(tmp_path)
    assert _problems(root, f"See [{reference}][].") == []


def test_a_task_docstring_naming_an_object_a_page_renders_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    _advertise(root, monkeypatch, "Run the leg with [acme.thing.Widget][].")
    assert reference_problems(root) == []


def test_a_task_docstrings_part_its_page_does_not_show_is_not_judged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A task's page shows its summary, its long description, its
    # parameters and its return value; another section is not on it.
    root = _workspace(tmp_path)
    _advertise(
        root,
        monkeypatch,
        "Run the leg.\n\n    Raises:\n"
        "        Failed: when [acme.extensions.tool._leg.top_up][] left nothing.\n    ",
    )
    assert reference_problems(root) == []


def test_a_task_docstring_without_a_task_reference_renders_nowhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    _advertise(
        root,
        monkeypatch,
        "Run the leg: [acme.extensions.tool._leg.top_up][].",
        reference=False,
    )
    assert reference_problems(root) == []


def test_a_docstring_the_site_renders_nowhere_is_not_judged(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    package = root / "packages" / "thing" / "src"
    # An underscore function's docstring, and a module without a page.
    impl = package / "acme" / "thing" / "_impl.py"
    impl.write_text(
        IMPL.replace(
            "Spin it, out of the reference's sight.",
            "Spin it, as [acme.thing._impl.Gone][] did.",
        )
    )
    leg = package / "acme" / "extensions" / "tool" / "_leg.py"
    leg.write_text(LEG.replace("Top the suites up.", "See [acme.thing.Gone][]."))
    assert reference_problems(root) == []


def test_a_directive_on_an_authored_page_renders_its_object(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    docs = root / "packages" / "thing" / "docs"
    directive = "# Spinning\n\n::: acme.thing._impl._spin\n"
    # What the build writes is no authored page: a member's generated
    # tree, and the root tree's mounts.
    for written in (docs / "_generated", root / "docs" / "packages" / "thing"):
        written.mkdir(parents=True)
        (written / "spinning.md").write_text(directive)
    assert len(_problems(root, "See [acme.thing._impl._spin][].")) == 1
    package = root / "packages" / "thing" / "src" / "acme" / "thing"
    (package / "_other.py").write_text(
        '"""Another home."""\n\nfrom pathlib import Path\n'
    )
    # A directive that names nothing, or an import griffe cannot follow,
    # renders nothing, and the page's other directives still count.
    (docs / "spinning.md").write_text(
        directive + "\n::: acme.thing.Nowhere\n\n::: acme.thing._other.Path\n"
    )
    assert _problems(root, "See [acme.thing._impl._spin][].") == []
    # The page shows the function's docstring, so its references count.
    (package / "_impl.py").write_text(
        IMPL.replace(
            "Spin it, out of the reference's sight.", "See [acme.thing.Gone][]."
        )
    )
    assert reference_problems(root) == [
        _refusal(
            "packages/thing/src/acme/thing/_impl.py:14",
            "acme.thing.Gone",
            "packages/thing/spinning.md",
        )
    ]


def test_a_class_shows_its_own_init_docstring_on_every_page_it_renders_on(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    impl = root / "packages" / "thing" / "src" / "acme" / "thing" / "_impl.py"
    impl.write_text(
        IMPL.replace(
            "    size: int = 1\n",
            "    size: int = 1\n\n    def __init__(self) -> None:\n"
            '        """Make it, as [acme.thing.Gone][] did."""\n',
        )
    )
    # The package re-exports the class, so its index page renders it too.
    assert reference_problems(root) == [
        _refusal(
            "packages/thing/src/acme/thing/_impl.py:10",
            "acme.thing.Gone",
            "packages/thing/api/_impl.md, packages/thing/api/index.md",
        )
    ]


def test_the_all_members_policy_renders_underscore_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(
        tmp_path,
        monkeypatch,
        base='[extension]\nlevels = ["workspace"]\n'
        '[queries."docs.members"]\nvalue = "all"\n',
    )
    root = _workspace(tmp_path, extensions='["docs", "acme.base"]')
    assert _problems(root, "See [acme.thing._impl._spin][].") == []


def test_a_reference_into_a_member_that_declines_its_reference_is_left_to_the_site(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    other = root / "packages" / "other"
    (other / "src" / "acme" / "other").mkdir(parents=True)
    (other / "src" / "acme" / "other" / "__init__.py").write_text(
        '"""Other."""\n\n\nclass Thing:\n    """A thing."""\n'
    )
    (other / "workshop.toml").write_text(
        'kind = "python"\nname = "acme-other"\n\n[docs]\napi = false\n'
    )
    (other / "pyproject.toml").write_text('[project]\nname = "acme-other"\n')
    assert _problems(root, "See [acme.other.Thing][].") == []
    assert _problems(root, "See [acme.other.Anything][].") == []
    # A namespace another member shares is judged.
    assert len(_problems(root, "See [acme.othr.Anything][].")) == 1


def test_a_member_whose_source_is_one_module_has_no_page_to_link(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    tool = root / "packages" / "tool"
    (tool / "src").mkdir(parents=True)
    (tool / "src" / "tool.py").write_text(
        '"""A tool, unlike [tool.Gone][]."""\n\nX = 1\n'
    )
    (tool / "workshop.toml").write_text('kind = "python"\nname = "tool"\n')
    (tool / "pyproject.toml").write_text('[project]\nname = "tool"\n')
    # A member whose kind extracts nothing renders no page either.
    native = root / "packages" / "native"
    (native / "src").mkdir(parents=True)
    (native / "workshop.toml").write_text('kind = "cpp-conan"\nname = "native"\n')
    assert _problems(root, "See [tool.X][].") == [
        _refusal(
            "packages/thing/src/acme/thing/_user.py:1",
            "tool.X",
            "packages/thing/api/_user.md",
        )
    ]


def test_a_source_that_does_not_parse_holds_no_reference(tmp_path: Path) -> None:
    source = tmp_path / "broken.py"
    source.write_text('"""See [acme.a.b][]."""\n\ndef (\n')
    assert docstring_references(source) == []


def test_a_reference_outside_every_members_namespace_is_left_to_the_site(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    assert _problems(root, "See [pathlib.Nowhere][].") == []


def test_code_is_text_not_a_reference(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    docstring = (
        "Inline `[acme.thing.Missing][]` and ``[acme.thing.Missing][]``.\n\n"
        "```text\n[acme.thing.Missing][]\n```\n"
    )
    assert _problems(root, docstring) == []


def test_a_reference_is_placed_on_its_own_line_and_by_its_docstring(
    tmp_path: Path,
) -> None:
    source = tmp_path / "m.py"
    source.write_text(
        '"""First line.\n\nThird: [acme.a.b][].\n"""\n\n\n'
        "X = 1\n"
        '"""After an assignment: [acme.c.d][]."""\n'
    )
    assert docstring_references(source) == [
        Reference(1, 3, "acme.a.b"),
        Reference(8, 8, "acme.c.d"),
    ]


def test_the_handler_options_choose_members_only_as_the_walk_reads_them() -> None:
    from livery.extensions.python._backend import PYTHON_HANDLER_OPTIONS

    # The walk reads the filters, show_if_no_docstring and
    # merge_init_into_class, and takes the handler's defaults for the
    # other options that choose members: setting one of those needs the
    # walk to read it first.
    unread = {"members", "inherited_members", "show_submodules"}
    assert not unread & set(PYTHON_HANDLER_OPTIONS)
    assert isinstance(PYTHON_HANDLER_OPTIONS.get("filters", []), list)
