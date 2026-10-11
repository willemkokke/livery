"""A package-level extension's fragments: refusals first, then what the sync writes."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.footman import Failed
from livery.workshop import _extensions
from livery.workshop._declaration import DeclarationError
from livery.workshop._shipped_files import deliver, outputs
from workshop_extension_fakes import fake_extensions, fake_steps

#: A package-level extension's identity, the start of every fake here.
PACKAGE = '[extension]\nlevels = ["package"]\n'

#: A fragment written by the fake's render.
FRAGMENT = '\n[fragments."members.txt"]\nrender = "{package}._steps:render"\n'

#: What a stale declaration is told: the fragments spelling.
SPELLING = (
    'is [fragments."{path}"]: a file the extension\'s code writes names its'
    " render there, module:function called with the workspace root, and a"
    " package-level extension's fragment lives while a package lists it"
)

#: The render: one line per package whose set holds the extension, in
#: path order, found through the root it is handed.
RENDER = (
    "from livery.workshop._kinds import extension_set\n"
    "from livery.workshop._packages import discover_packages\n"
    "\n"
    "\n"
    "def render(root):\n"
    "    name = __name__.rpartition('.')[0]\n"
    "    held = [p.path for p in discover_packages(root) if name in extension_set(p)]\n"
    "    return ''.join(path + '\\n' for path in held)\n"
)

#: The render above, logging each call beside its module: the root it
#: was handed, one line per call.
LOGGED_RENDER = (
    "from pathlib import Path\n"
    "\n"
    "from livery.workshop._kinds import extension_set\n"
    "from livery.workshop._packages import discover_packages\n"
    "\n"
    "\n"
    "def render(root):\n"
    "    with Path(__file__).with_name('calls.txt').open('a') as log:\n"
    "        log.write(str(root) + '\\n')\n"
    "    name = __name__.rpartition('.')[0]\n"
    "    held = [p.path for p in discover_packages(root) if name in extension_set(p)]\n"
    "    return ''.join(path + '\\n' for path in held)\n"
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
            PACKAGE + FRAGMENT.replace("[fragments.", "[root-files."),
            'root-files."members.txt" ' + SPELLING.format(path="members.txt"),
        ),
        (
            PACKAGE + "\n[root-files]\n",
            "root-files " + SPELLING.format(path="<path>"),
        ),
        (
            PACKAGE + '\n[for."acme.other".root-files."members.txt"]\n',
            'for."acme.other".root-files."members.txt" '
            + SPELLING.format(path="members.txt"),
        ),
        (
            PACKAGE + '\n[fragments."members.txt"]\n',
            'fragments."members.txt" names no render; a fragment\'s code is'
            " render = 'module:function'",
        ),
        (
            PACKAGE + FRAGMENT.replace(":render", ":rendered"),
            'fragments."members.txt".render names rendered, which acme.odd._steps'
            " does not define at its top level; did you mean 'render'?",
        ),
    ],
    ids=[
        "root-files",
        "bare-root-files",
        "root-files-for-a-target",
        "no-render",
        "no-function",
    ],
)
def test_a_fragment_the_declaration_alone_shows_wrong_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, declared: str, refusal: str
) -> None:
    fake_extensions(tmp_path, monkeypatch, odd=declared)
    fake_steps(tmp_path, "odd", RENDER)
    with pytest.raises(DeclarationError) as raised:
        _extensions.declaration("acme.odd")
    path = tmp_path / "site" / "acme" / "odd" / "extension.toml"
    assert str(raised.value) == f"{path}: {refusal}"


def test_two_extensions_writing_one_fragment_refuse_naming_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_extensions(
        tmp_path,
        monkeypatch,
        one=PACKAGE + FRAGMENT,
        two=PACKAGE + FRAGMENT,
    )
    fake_steps(tmp_path, "one", RENDER)
    fake_steps(tmp_path, "two", RENDER)
    root = _workspace(tmp_path / "ws", monkeypatch)
    _member(root, "core", '["acme.one", "acme.two"]')
    with pytest.raises(Failed) as raised:
        deliver(root)
    assert str(raised.value) == (
        "members.txt has one writer, and acme.one:members.txt and"
        " acme.two:members.txt both write it"
    )


# What the listed extensions write.


def test_a_fragment_comes_with_the_first_package_listing_it_and_goes_with_the_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    fake_extensions(tmp_path, monkeypatch, lists=PACKAGE + FRAGMENT)
    fake_steps(tmp_path, "lists", RENDER)
    root = _workspace(tmp_path / "ws", monkeypatch)
    _member(root, "plain", "[]")
    deliver(root)
    assert not (root / "members.txt").exists()
    _member(root, "b", '["acme.lists"]')
    _member(root, "a", '["acme.lists"]')
    deliver(root)
    # The render reads the root: the packages whose set holds the
    # extension, in path order.
    assert (root / "members.txt").read_text() == "packages/a\npackages/b\n"
    shutil.rmtree(root / "packages" / "a")
    shutil.rmtree(root / "packages" / "b")
    deliver(root)
    assert not (root / "members.txt").exists()


def test_a_package_level_fragment_renders_once_while_any_package_lists_its_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    fake_extensions(
        tmp_path,
        monkeypatch,
        left=PACKAGE + FRAGMENT,
        right=PACKAGE + FRAGMENT,
        once=PACKAGE + FRAGMENT,
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
        outputs(root)
    assert str(raised.value) == (
        "members.txt has one writer, and acme.left:members.txt and"
        " acme.right:members.txt both write it"
    )
    # One extension listed by two packages renders once, handed the
    # root, into one output it owns alone.
    for name in ("a", "b"):
        shutil.rmtree(root / "packages" / name)
        _member(root, name, '["acme.once"]')
    (found,) = [output for output in outputs(root) if output.path == "members.txt"]
    assert found.owners == ("acme.once:members.txt",)
    assert found.body == b"packages/a\npackages/b\n"
    calls = tmp_path / "site" / "acme" / "once" / "calls.txt"
    assert calls.read_text() == f"{root}\n"
    # The sync writes that one file at the root.
    deliver(root)
    assert (root / "members.txt").read_text() == "packages/a\npackages/b\n"
