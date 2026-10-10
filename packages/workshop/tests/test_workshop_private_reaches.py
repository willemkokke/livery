"""The python extension's private-reaches rule: refusals first, then the scan."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.extensions.python._reaches import private_reaches, scan
from livery.workshop._ast_rules import RuleContext, parsed_modules
from livery.workshop._packages import discover_packages

ALLOWANCE = """
[[python.private-reaches]]
from = "acme-beta"
reaches = "{reaches}"
reason = "a reason"
"""


def _member(root: Path, name: str, modules: list[str], files: dict[str, str]) -> None:
    directory = root / "packages" / name
    directory.mkdir(parents=True)
    names = ", ".join(f'"{module}"' for module in modules)
    (directory / "workshop.toml").write_text(
        f'kind = "python"\nextensions = ["python"]\nname = "acme-{name}"\n',
        encoding="utf-8",
    )
    (directory / "pyproject.toml").write_text(
        f'[project]\nname = "acme-{name}"\n\n'
        f"[tool.uv.build-backend]\nmodule-name = [{names}]\n",
        encoding="utf-8",
    )
    for relative, text in files.items():
        path = directory / "src" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


REACHING = """\
from typing import TYPE_CHECKING

import acme.alpha._impl
import acme.alpha as alpha
import acme.alpha.public
from acme.alpha import _impl, thing, __version__
from acme.alpha._impl import helper
from acme.alpha.public import _hidden
from acme.beta import _own
from . import _local

try:
    from acme.alpha import _guarded
except ImportError:
    pass
if TYPE_CHECKING:
    from acme.alpha._types import Shape

alpha._secret
acme.alpha.public._deep.value
alpha.__doc__
"""


def _reaching(root: Path, *allowed: str) -> RuleContext:
    """A workspace whose acme-beta reaches acme-alpha's privates; its rule context.

    *allowed* are the privates the root contract lets acme-beta reach.
    """
    (root / "workshop.toml").write_text(
        "[workspace]\nextensions = []\n"
        + "".join(ALLOWANCE.format(reaches=name) for name in allowed),
        encoding="utf-8",
    )
    _member(root, "alpha", ["acme.alpha"], {"acme/alpha/__init__.py": ""})
    _member(
        root,
        "beta",
        ["acme.beta", "acme.extensions.gamma"],
        {
            "acme/beta/__init__.py": REACHING,
            "acme/beta/_own.py": "",
            "acme/extensions/gamma/_checks.py": "from acme.beta import _own\n",
        },
    )
    return RuleContext(root, discover_packages(root))


def _judged(context: RuleContext) -> list[str]:
    return private_reaches(
        parsed_modules(context.root, context.packages, context.files), context
    )


# The refusals first.


def test_a_reach_outside_the_allowance_refuses_naming_its_place(
    tmp_path: Path,
) -> None:
    every = _reaches(_reaching(tmp_path))
    allowed = sorted(name for _, name in every if name != "acme.alpha._secret")
    context = _reaching(_fresh(tmp_path), *allowed)
    assert _judged(context) == [
        "packages/beta/src/acme/beta/__init__.py:19: acme-beta reaches"
        " acme.alpha._secret, another distribution's private; make the name"
        " public where it lives, or allow the reach in the root contract's"
        " [[python.private-reaches]] with its reason"
    ]


def test_an_allowance_no_source_uses_refuses_on_a_full_run_alone(
    tmp_path: Path,
) -> None:
    every = sorted(name for _, name in _reaches(_reaching(tmp_path)))
    context = _reaching(_fresh(tmp_path), *every, "acme.alpha._gone")
    assert _judged(context) == [
        "workshop.toml: [[python.private-reaches]] allows acme-beta to reach"
        " acme.alpha._gone, and no source reaches it any more; delete the entry"
    ]
    # A narrowed run cannot tell an entry nothing uses from one its scope
    # leaves out, so it judges none.
    narrowed = RuleContext(
        context.root,
        context.packages,
        frozenset({"packages/beta/src/acme/beta/__init__.py"}),
    )
    assert _judged(narrowed) == []


def test_a_declared_rule_with_no_judge_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery.workshop._declaration import DeclarationError
    from livery.workshop._extensions import installed_declaration
    from workshop_extension_fakes import fake_package_extensions

    fake_package_extensions(
        tmp_path,
        monkeypatch,
        lint='[extension]\nlevels = ["package"]\n\n[rules.loud]\nfix = "x:y"\n',
    )
    with pytest.raises(DeclarationError, match="names no judge"):
        installed_declaration("acme.lint")


# Then what the scan sees, and what the mount registers.


def test_every_form_of_a_reach_is_seen_with_its_line(tmp_path: Path) -> None:
    reaches = _reaches(_reaching(tmp_path))
    where = "packages/beta/src/acme/beta/__init__.py"
    assert reaches == {
        ("acme-beta", "acme.alpha._impl"): [f"{where}:3", f"{where}:6"],
        ("acme-beta", "acme.alpha._impl.helper"): [f"{where}:7"],
        ("acme-beta", "acme.alpha.public._hidden"): [f"{where}:8"],
        ("acme-beta", "acme.alpha._guarded"): [f"{where}:13"],
        ("acme-beta", "acme.alpha._types.Shape"): [f"{where}:17"],
        ("acme-beta", "acme.alpha._secret"): [f"{where}:19"],
        ("acme-beta", "acme.alpha.public._deep"): [f"{where}:20"],
    }


def test_a_distribution_reaches_its_own_privates_freely(tmp_path: Path) -> None:
    # gamma ships in beta's wheel, so its reach into beta is no reach.
    reaches = _reaches(_reaching(tmp_path))
    assert not any(name.startswith("acme.beta") for _, name in reaches)


def test_the_python_extension_declares_the_rule_and_the_mount_registers_it() -> None:
    from livery.workshop._ast_rules import ast_rules, unregister_ast_rule
    from livery.workshop._extensions import declare_rules, installed_declaration

    declared = installed_declaration("python")
    assert declared is not None
    assert set(declared.rules) == {"private-reaches"}
    before = {rule.name: rule for rule in ast_rules()}
    try:
        assert declare_rules("python", declared.rules)
        registered = {rule.name: rule for rule in ast_rules()}["private-reaches"]
        assert registered.extension == "python"
    finally:
        unregister_ast_rule("private-reaches")
        if "private-reaches" in before:
            from livery.workshop._ast_rules import register_ast_rule

            register_ast_rule(before["private-reaches"])


def _reaches(context: RuleContext) -> dict[tuple[str, str], list[str]]:
    """Every reach the scan sees in *context*'s workspace."""
    return scan(parsed_modules(context.root, context.packages), context.packages)


def _fresh(tmp_path: Path) -> Path:
    """A second workspace beside the first, for the judged run."""
    root = tmp_path / "judged"
    root.mkdir()
    return root
