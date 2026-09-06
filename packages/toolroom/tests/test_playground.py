"""The playground: the shipped browser driver, run in CPython over the
shipped default files — the same harness footman's docs use, pointed at
this site's assets. The sim sandbox (`_FM_PLAYGROUND_SIM`) is footman's,
reached through the dev dependency.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

DOCS = Path(__file__).resolve().parents[1] / "docs"

FRAGMENT = "<!-- example: fragment -->"
FRESH = "<!-- example: fresh-session -->"
REVISION = "<!-- example: revision -->"
_OPEN = re.compile(r"^(?P<indent>[ ]*)```python\s*$")


def _js_source() -> str:
    return (DOCS / "assets" / "playground.js").read_text(encoding="utf-8")


def _js_bootstrap() -> str:
    """The driver: the text of the top-level `const BOOTSTRAP = \\`…\\`;`."""
    match = re.search(r"^const BOOTSTRAP = `(.*?)`;$", _js_source(), re.S | re.M)
    assert match, "playground.js no longer defines BOOTSTRAP"
    return match.group(1)


def _js_default_files() -> dict[str, str]:
    """The editor's opening tabs: `const DEFAULT_FILES = {"name": \\`…\\`, …}`."""
    match = re.search(r"^const DEFAULT_FILES = \{(.*?)^\};$", _js_source(), re.S | re.M)
    assert match, "playground.js no longer defines DEFAULT_FILES"
    return dict(re.findall(r'"([^"]+)": `(.*?)`,', match.group(1), re.S))


def _playground_invoke(tmp_path: Path, line: str) -> tuple[int, str]:
    """Drive the shipped browser driver over the shipped default files.

    The exact BOOTSTRAP text runs in CPython under `_FM_PLAYGROUND_SIM`, in
    a subprocess because it monkeypatches `subprocess.Popen` process-wide.
    """
    files = _js_default_files()
    assert set(files) == {"tasks.py", "test_demo.py"}, files
    probe = tmp_path / "probe.py"
    probe.write_text(
        _js_bootstrap()
        + "\nimport json, sys\n"
        + "print(_fm_invoke(sys.argv[1], sys.argv[2]))\n",
        encoding="utf-8",
    )
    work = Path(tempfile.mkdtemp(dir=tmp_path))  # a fresh cwd per invocation
    out = subprocess.run(
        [sys.executable, str(probe), json.dumps(files), line],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=work,
        env={**os.environ, "_FM_PLAYGROUND_SIM": "1", "PYTHONPATH": str(work)},
        check=False,
    )
    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout)
    # The page prints both streams into the one output pane; so does this.
    return int(result["exit_code"]), str(result["stdout"]) + str(result["stderr"])


def test_playground_default_sample_runs(tmp_path: Path):
    """`fm check` on the default sample: lint green through the simulated
    child, then the deliberately wrong test failing with pytest's own diff.
    """
    code, output = _playground_invoke(tmp_path, "-s check")
    assert code == 1, output
    assert re.search(r"^ok\s+lint", output, re.M), output
    assert re.search(r"^FAIL\s+test", output, re.M), output
    assert "assert '4' == 'fizz'" in output, output
    assert "1 failed, 2 passed" in output, output


def test_playground_default_sample_spells_its_tools(tmp_path: Path):
    """The sample's `ruff.check("src", fix=fix)` builds the command a reader
    would write by hand, in both states of the flag.
    """
    for line, command in (
        ("-s --dry-run lint", "$ ruff check src"),
        ("-s --dry-run lint --fix", "$ ruff check src --fix"),
    ):
        _, output = _playground_invoke(tmp_path, line)
        assert command in output, output


def test_playground_ship_builds_without_running(tmp_path: Path):
    """`.argv` in the sandbox: a whole docker line built, quoted, and never
    executed — no docker exists in the page, and none is needed.
    """
    code, output = _playground_invoke(tmp_path, "-s ship")
    assert code == 0, output
    assert "docker compose up --detach" in output, output


def test_example_markers_are_spent():
    """Every example marker sits directly above a ```python fence — a
    marker that drifted away from its fence would silently stop exempting
    anything.
    """
    for page in sorted(p for p in DOCS.rglob("*.md") if "_generated" not in p.parts):
        lines = page.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if line.strip() not in (FRAGMENT, FRESH, REVISION):
                continue
            following = next((ln for ln in lines[i + 1 :] if ln.strip()), "")
            assert _OPEN.match(following), (
                f"{page.name}:{i + 1}: example marker without a python fence"
            )
