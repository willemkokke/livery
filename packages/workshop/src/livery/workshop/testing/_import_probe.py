"""Import one module and print what its import registered, as JSON.

The conformance kit runs ``python -m livery.workshop.testing._import_probe
<module>`` in a process of its own, so the module is imported for the
first time. The workshop's own registering modules are imported before
the recording starts, so a builtin they register is never counted. Then
each of their registration functions records its calls, and footman's
task tree is captured, for the one import.
"""

from __future__ import annotations

import importlib
import json
import sys
from typing import Any

#: Each workshop module that registers, with its registration functions.
REGISTRARS: dict[str, tuple[str, ...]] = {
    "livery.workshop._ast_rules": ("register_ast_rule",),
    "livery.workshop._categories": ("register_categories", "register_channels"),
    "livery.workshop._checks": ("register_check",),
    "livery.workshop._kinds": ("register_kind",),
    "livery.workshop._points": ("contribute_job",),
    "livery.workshop._prose": ("register_section", "register_fragment"),
    "livery.workshop._release_notes": ("register_release_notes",),
    "livery.workshop._site_files": ("register_site_file",),
    "livery.workshop._slots": ("register_slot", "contribute"),
}


def registered_by(module: str) -> list[str]:
    """What importing *module* registers, one line per registration."""
    from livery.footman import capture

    found: list[str] = []
    for name, functions in REGISTRARS.items():
        owner = importlib.import_module(name)
        for function in functions:
            setattr(owner, function, _recording(getattr(owner, function), found))
    with capture() as captured:
        importlib.import_module(module)
    found += [f"the footman group {name!r}" for name in sorted(captured.groups)]
    found += [f"the footman task {name!r}" for name in sorted(captured.tasks)]
    return found


def _recording(function: Any, found: list[str]) -> Any:
    """*function*, noting each call in *found* by the function's name."""

    def recorder(*args: Any, **kwargs: Any) -> Any:
        found.append(f"a call to {function.__module__}.{function.__name__}")
        return function(*args, **kwargs)

    return recorder


def main() -> None:
    """Print what importing the module the first argument names registers."""
    print(json.dumps(registered_by(sys.argv[1])))


if __name__ == "__main__":
    main()
