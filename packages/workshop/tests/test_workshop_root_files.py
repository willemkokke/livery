"""Root files: the refusals first, then what listed extensions write at the root."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.footman import Failed
from livery.workshop import _extensions
from livery.workshop._declaration import DeclarationError
from livery.workshop._shipped_files import deliver
from workshop_extension_fakes import fake_extensions, fake_steps

#: A package-level extension's identity, the start of every fake here.
PACKAGE = '[extension]\nlevels = ["package"]\n'

#: A root file written by the fake's render.
ROOT_FILE = '\n[root-files."members.txt"]\nrender = "{package}._steps:render"\n'

#: The render: one line per member, in the order it is handed them.
RENDER = (
    "def render(members):\n"
    "    return ''.join(member.path + '\\n' for member in members)\n"
)

#: The render above, logging each call beside its module: the members it
#: was handed, one line per call.
LOGGED_RENDER = (
    "from pathlib import Path\n"
    "\n"
    "\n"
    "def render(members):\n"
    "    handed = ' '.join(member.path for member in members)\n"
    "    with Path(__file__).with_name('calls.txt').open('a') as log:\n"
    "        log.write(handed + '\\n')\n"
    "    return ''.join(member.path + '\\n' for member in members)\n"
)


def _workspace(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty workspace at *root*, which the extension readers resolve to."""
    root.mkdir()
    (root / "workshop.toml").write_text("[workspace]\nextensions = []\n")
    monkeypatch.setattr(
        "livery.workshop._extensions.workspace_root", lambda start=None: root
    )
    return root


def _member(root: Path, name: str, extensions: str) -> None:
    """A python member ``packages/<name>`` listing *extensions*, a TOML list."""
    directory = root / "packages" / name
    directory.mkdir(parents=True)
    (directory / "workshop.toml").write_text(
        f'kind = "python"\nname = "acme-{name}"\nextensions = {extensions}\n'
    )
    (directory / "pyproject.toml").write_text(f'[project]\nname = "acme-{name}"\n')


# The refusals first.


@pytest.mark.parametrize(
    ("declared", "refusal"),
    [
        (
            ROOT_FILE,
            'root-files."members.txt" is written while a package lists the'
            " extension, and this extension's levels are workspace; add 'package'"
            " to levels, or drop the file",
        ),
        (
            PACKAGE + '\n[root-files."members.txt"]\n',
            'root-files."members.txt" names no render; a root file\'s code is'
            " render = 'module:function'",
        ),
        (
            PACKAGE + ROOT_FILE.replace(":render", ":rendered"),
            'root-files."members.txt".render names rendered, which acme.odd._steps'
            " does not define at its top level; did you mean 'render'?",
        ),
    ],
    ids=["workspace-level", "no-render", "no-function"],
)
def test_a_root_file_the_declaration_alone_shows_wrong_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, declared: str, refusal: str
) -> None:
    fake_extensions(tmp_path, monkeypatch, odd=declared)
    fake_steps(tmp_path, "odd", RENDER)
    with pytest.raises(DeclarationError) as raised:
        _extensions.declaration("acme.odd")
    path = tmp_path / "site" / "acme" / "odd" / "extension.toml"
    assert str(raised.value) == f"{path}: {refusal}"


def test_two_extensions_writing_one_root_file_refuse_naming_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(
        tmp_path,
        monkeypatch,
        one=PACKAGE + ROOT_FILE,
        two=PACKAGE + ROOT_FILE,
    )
    fake_steps(tmp_path, "one", RENDER)
    fake_steps(tmp_path, "two", RENDER)
    root = _workspace(tmp_path / "ws", monkeypatch)
    _member(root, "core", '["acme.one", "acme.two"]')
    with pytest.raises(Failed) as raised:
        deliver(root)
    assert str(raised.value) == (
        "members.txt: both acme.one and acme.two write it at the root; a root file"
        " has one writer, so list one of the two"
    )


# What the listed extensions write.


def test_a_root_file_comes_with_the_first_member_listing_it_and_goes_with_the_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    fake_extensions(tmp_path, monkeypatch, lists=PACKAGE + ROOT_FILE)
    fake_steps(tmp_path, "lists", RENDER)
    root = _workspace(tmp_path / "ws", monkeypatch)
    _member(root, "plain", "[]")
    deliver(root)
    assert not (root / "members.txt").exists()
    _member(root, "b", '["acme.lists"]')
    _member(root, "a", '["acme.lists"]')
    deliver(root)
    # The render is handed the members listing it, in path order.
    assert (root / "members.txt").read_text() == "packages/a\npackages/b\n"
    shutil.rmtree(root / "packages" / "a")
    shutil.rmtree(root / "packages" / "b")
    deliver(root)
    assert not (root / "members.txt").exists()


def test_a_package_level_root_file_is_written_once_while_any_package_lists_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    from livery.workshop._shipped_files import _root_file_outputs

    fake_extensions(
        tmp_path,
        monkeypatch,
        left=PACKAGE + ROOT_FILE,
        right=PACKAGE + ROOT_FILE,
        once=PACKAGE + ROOT_FILE,
    )
    fake_steps(tmp_path, "left", RENDER)
    fake_steps(tmp_path, "right", RENDER)
    fake_steps(tmp_path, "once", LOGGED_RENDER)
    root = _workspace(tmp_path / "ws", monkeypatch)
    # The refusal first: two extensions writing one path refuse when
    # different packages list them, since the root has one file there.
    _member(root, "a", '["acme.left"]')
    _member(root, "b", '["acme.right"]')
    with pytest.raises(Failed) as raised:
        _root_file_outputs(root)
    assert str(raised.value) == (
        "members.txt: both acme.left and acme.right write it at the root; a root"
        " file has one writer, so list one of the two"
    )
    # One extension listed by two packages renders once, handed both members
    # in path order, into one output it owns alone.
    for name in ("a", "b"):
        shutil.rmtree(root / "packages" / name)
        _member(root, name, '["acme.once"]')
    (found,) = _root_file_outputs(root)
    assert (found.path, found.owners) == ("members.txt", ("acme.once:members.txt",))
    assert found.body == b"packages/a\npackages/b\n"
    calls = tmp_path / "site" / "acme" / "once" / "calls.txt"
    assert calls.read_text() == "packages/a packages/b\n"
    # The sync writes that one file at the root.
    deliver(root)
    assert (root / "members.txt").read_text() == "packages/a\npackages/b\n"
