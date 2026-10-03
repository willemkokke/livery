"""Every template and check fragment renders the same bytes in minijinja.

The extensions plan moves rendering from copier's jinja2 to minijinja
(its contract 14). Until the templates go, this test renders each
template file and each check fragment of this repository through both
engines, over the data copier renders them with, and requires the same
bytes, so no template gains a construct minijinja cannot render.

Copier's own answers file is the one exception: its name comes from
copier's configuration object and its body uses copier's
`to_nice_yaml` filter, and the file goes with copier.

The context comes from copier's `Worker` internals (`_ask`,
`_render_context`), which no public API exposes; the test goes with
copier too.
"""

from __future__ import annotations

import difflib
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import minijinja
from copier._main import Worker  # pyright: ignore[reportPrivateUsage]

from livery.workshop import _fragments
from livery.workshop._checks import checks_by_name
from livery.workshop._identity import package_facts, project_facts
from livery.workshop._kinds import template_chain
from livery.workshop._templates import (
    _package_regions,  # pyright: ignore[reportPrivateUsage]
    _release_baseline,  # pyright: ignore[reportPrivateUsage]
    package_injections,
    render_injections,
    render_source,
)

ROOT = Path(__file__).resolve().parents[3]


def _plain(value: Any) -> Any:
    """*value* as the plain data minijinja takes: dicts, lists, scalars."""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, (list, tuple, set)):
        return [_plain(v) for v in value]  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _minijinja(text: str, context: Mapping[str, Any]) -> str:
    environment = minijinja.Environment()
    environment.keep_trailing_newline = True
    environment.undefined_behavior = "lenient"
    return environment.render_str(text, **_plain(context))


def _diff(label: str, expected: str, got: str) -> str:
    lines = difflib.unified_diff(
        expected.splitlines(True), got.splitlines(True), "jinja2", "minijinja", n=1
    )
    return f"{label}:\n" + "".join(list(lines)[:20])


def _template_differences(source: Path, kind: str, data: dict[str, Any]) -> list[str]:
    found: list[str] = []
    with tempfile.TemporaryDirectory() as scratch:
        worker = Worker(
            src_path=str(source),
            dst_path=Path(scratch),
            data={**data, "kind": kind},
            defaults=True,
            unsafe=True,
            quiet=True,
        )
        worker._ask()  # pyright: ignore[reportPrivateUsage]
        context = worker._render_context()  # pyright: ignore[reportPrivateUsage]
        environment = worker.jinja_env
        for path in sorted((source / kind).rglob("*.jinja")):
            relative = path.relative_to(source).as_posix()
            text = path.read_text("utf-8")
            expected = environment.from_string(text).render(**context)
            got = _minijinja(text, context)
            if got != expected:
                found.append(_diff(relative, expected, got))
    return found


def test_every_template_renders_the_same_in_minijinja() -> None:
    source = Path(render_source(ROOT)[0])
    answers = project_facts(ROOT)
    differences = _template_differences(
        source, "project", {**answers, **render_injections(ROOT, answers)}
    )
    members = sorted(
        path.parent for path in (ROOT / "packages").glob("*/workshop.toml")
    )
    data: dict[str, Any] = {}
    for directory in members:
        data = {
            **package_facts(ROOT, directory),
            **package_injections(ROOT),
            "package_dir": directory.name,
            "release_baseline": _release_baseline(directory),
            "regions": _package_regions(directory),
        }
        for kind in template_chain(str(data.get("kind", ""))):
            differences += _template_differences(source, kind, data)
    # The kinds no member here uses, rendered over the last member's data.
    for kind in ("package-cpp-conan", "package-python-nanobind", "package-extension"):
        differences += _template_differences(source, kind, data)
    assert differences == []


def test_every_check_fragment_renders_the_same_in_minijinja() -> None:
    answers = project_facts(ROOT)
    data = {**answers, **render_injections(ROOT, answers)}
    differences: list[str] = []
    for name, record in sorted(checks_by_name().items()):
        for fragment in record.fragments:
            expected = _fragments._render(fragment.text, data)  # pyright: ignore[reportPrivateUsage]
            got = _minijinja(fragment.text, data)
            if got != expected:
                differences.append(_diff(f"{name} -> {fragment.file}", expected, got))
    assert differences == []
