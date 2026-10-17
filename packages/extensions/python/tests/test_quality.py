"""A python package's modules: its roots, what is public, and the test call.

Each root is read from the build's own list or the src tree's marks,
a module from the src tree before the distribution's name, and the
kind's test call maps a selection to the package's own files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.extensions.python import _backend as _python
from livery.workshop import Package


def test_the_module_derives_from_the_src_tree_not_the_dist_name(
    tmp_path: Path,
) -> None:
    # loop-echo under a workspace prefix once became "loop.echo", a
    # module that does not exist: the src tree is the truth.
    member = tmp_path / "packages" / "loop-echo"
    (member / "src" / "ci_e2e_loop" / "loop_echo").mkdir(parents=True)
    (member / "src" / "ci_e2e_loop" / "loop_echo" / "__init__.py").write_text("")
    package = Package(
        directory=member,
        path="packages/loop-echo",
        name="ci-e2e-loop-loop-echo",
        depends=(),
        extensions=("python",),
    )
    assert _python.module_for(package) == "ci_e2e_loop.loop_echo"


def test_a_srcless_package_falls_back_to_the_dist_spelling(
    tmp_path: Path,
) -> None:
    member = tmp_path / "packages" / "plain"
    member.mkdir(parents=True)
    package = Package(
        directory=member,
        path="packages/plain",
        name="livery-loop-echo",
        depends=(),
        extensions=("python",),
    )
    assert _python.module_for(package) == "livery.loop_echo"


def test_a_src_tree_with_no_single_chain_falls_back_to_the_dist_spelling(
    tmp_path: Path,
) -> None:
    # An empty src, and one that opens on two directories, name no module
    # of their own; a dist name without a hyphen is its own module.
    member = tmp_path / "packages" / "plain"
    (member / "src").mkdir(parents=True)
    package = Package(
        directory=member,
        path="packages/plain",
        name="plain",
        depends=(),
        extensions=("python",),
    )
    assert _python.module_for(package) == "plain"
    (member / "src" / "alpha").mkdir()
    (member / "src" / "beta").mkdir()
    hyphenated = Package(
        directory=member,
        path="packages/plain",
        name="acme-plain",
        depends=(),
        extensions=("python",),
    )
    assert _python.module_for(hyphenated) == "acme.plain"


def _python_member(tmp_path: Path, name: str, manifest: str = "") -> Package:
    member = tmp_path / "packages" / name
    member.mkdir(parents=True)
    (member / "pyproject.toml").write_text(
        f'[project]\nname = "acme-{name}"\n{manifest}'
    )
    return Package(
        directory=member,
        path=f"packages/{name}",
        name=f"acme-{name}",
        depends=(),
        extensions=("python",),
    )


def _root(member: Path, name: str) -> Path:
    """A root: its __init__, a public package it declares, a private module."""
    root = member / "src" / "acme" / name
    (root / "codec").mkdir(parents=True)
    (root / "__init__.py").write_text('__version__ = "0.0.0"\n')
    (root / "py.typed").write_text("")
    (root / "codec" / "__init__.py").write_text("")
    (root / "_private.py").write_text("")
    (root / "content").mkdir()
    (root / "content" / "seed.py").write_text("")
    return root


def test_the_public_modules_are_what_each_root_declares(tmp_path: Path) -> None:
    from livery.workshop import PUBLIC_MODULES, answer

    # A root with nothing public, an extension's, declares nothing.
    bare = _python_member(
        tmp_path, "ext", '[tool.uv.build-backend]\nmodule-name = "acme.ext"\n'
    )
    (bare.directory / "src" / "acme" / "ext").mkdir(parents=True)
    assert answer(bare, PUBLIC_MODULES) == ()
    # A root's __init__ declares its public packages too, so a verifier
    # reaches them through it: one module.
    package = _python_member(tmp_path, "one")
    _root(package.directory, "one")
    assert answer(package, PUBLIC_MODULES) == ("acme.one",)


def test_a_root_with_nothing_public_verifies_nothing(tmp_path: Path) -> None:
    # An extension: its build names the root, and nothing in it is public.
    package = _python_member(
        tmp_path,
        "ext",
        '[tool.uv.build-backend]\nmodule-name = "acme.extensions.ext"\n',
    )
    root = package.directory / "src" / "acme" / "extensions" / "ext"
    (root / "content").mkdir(parents=True)
    (root / "_steps.py").write_text("")
    assert _python.module_roots(package) == ("acme.extensions.ext",)
    assert _python.public_modules(package) == ()


def test_the_public_modules_follow_the_layout(tmp_path: Path) -> None:
    # A regular package is one module: the verifier walks the rest.
    regular = _python_member(tmp_path, "two")
    (regular.directory / "src" / "acme" / "two" / "sub").mkdir(parents=True)
    (regular.directory / "src" / "acme" / "two" / "__init__.py").write_text("")
    (regular.directory / "src" / "acme" / "two" / "sub" / "__init__.py").write_text("")
    assert _python.public_modules(regular) == ("acme.two",)
    # A namespace, with no __init__, declares nothing: an api module in
    # it is an ordinary module, and a regular package beneath it is a
    # root of its own.
    spaced = _python_member(tmp_path, "three")
    namespace = spaced.directory / "src" / "acme" / "three"
    (namespace / "codec").mkdir(parents=True)
    (namespace / "api.py").write_text('__version__ = "0.0.0"\n')
    (namespace / "codec" / "__init__.py").write_text("")
    assert _python.public_modules(spaced) == ("acme.three.codec",)
    # No src tree: the distribution name's module.
    srcless = _python_member(tmp_path, "four")
    assert _python.public_modules(srcless) == ("acme.four",)


def test_the_roots_are_what_the_build_ships(tmp_path: Path) -> None:
    # Undeclared: read from the src tree's marks.
    marked = _python_member(tmp_path, "five")
    _root(marked.directory, "five")
    assert _python.module_roots(marked) == ("acme.five",)
    # Declared: the build's own list, a second root with no marks included.
    declared = _python_member(
        tmp_path,
        "six",
        '[tool.uv.build-backend]\nmodule-name = ["acme.six", "acme.extensions.six"]\n',
    )
    _root(declared.directory, "six")
    assert _python.module_roots(declared) == ("acme.extensions.six", "acme.six")
    single = _python_member(
        tmp_path, "seven", '[tool.uv.build-backend]\nmodule-name = "acme.seven"\n'
    )
    (single.directory / "src").mkdir()
    assert _python.module_roots(single) == ("acme.seven",)


def test_an_init_at_the_src_root_marks_no_root(tmp_path: Path) -> None:
    # The file names no importable package, so the roots are the ones
    # beneath it.
    package = _python_member(tmp_path, "eight")
    _root(package.directory, "eight")
    (package.directory / "src" / "__init__.py").write_text("")
    assert _python.module_roots(package) == ("acme.eight",)


def test_the_python_test_entry_maps_a_selection_to_its_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        _python, "run_test", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    package = _python_member(tmp_path, "one")
    _python.test(package, tmp_path, selection=("tests/test_a.py",))
    args, kwargs = calls[0]
    assert args == ()
    assert kwargs["selection"] == {package.path: (f"{package.path}/tests/test_a.py",)}
    _python.test(package, tmp_path)
    assert calls[1][1]["selection"] is None
    # The words for the kind's test runner reach pytest unchanged.
    _python.test(package, tmp_path, arguments=("-k", "name"))
    assert calls[2][0] == ("-k", "name")
