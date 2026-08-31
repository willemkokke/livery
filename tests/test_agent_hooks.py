"""The pre-bash guard refuses the two silently-breaking command shapes.

Each case drives `fm hooks.pre-bash` exactly as Claude Code does: the
hook event on stdin, the verdict in the exit code (2 refuses, 0
passes).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _guard(command: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    event = {"tool_name": "Bash", "tool_input": {"command": command}}
    return subprocess.run(
        ["uv", "run", "--no-sync", "fm", "hooks.pre-bash"],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )


def test_a_piped_gate_is_refused() -> None:
    result = _guard("uv run fm check 2>&1 | tail -3")
    assert result.returncode == 2
    assert "exit code" in result.stdout + result.stderr


def test_a_bare_fm_piped_into_head_is_refused() -> None:
    assert _guard("fm test | head -5").returncode == 2


def test_quoted_fm_text_is_data() -> None:
    assert _guard('rg "fm check" notes | head -3').returncode == 0


def test_a_separator_splits_the_segments() -> None:
    assert _guard("uv run fm check && echo done | tail -1").returncode == 0


def test_an_ordinary_pipe_passes() -> None:
    assert _guard("git log --oneline | head -3").returncode == 0


def test_a_conflicting_push_is_refused(tmp_path: Path) -> None:
    def git(*args: str, cwd: Path) -> None:
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    origin = tmp_path / "origin"
    origin.mkdir()
    git("init", "--bare", "-b", "main", cwd=origin)
    clone = tmp_path / "clone"
    git("clone", str(origin), str(clone), cwd=tmp_path)
    git("config", "user.email", "t@t", cwd=clone)
    git("config", "user.name", "t", cwd=clone)
    git("config", "commit.gpgsign", "false", cwd=clone)
    (clone / "f.txt").write_text("base\n")
    git("add", ".", cwd=clone)
    git("commit", "-m", "base", cwd=clone)
    git("push", "origin", "main", cwd=clone)
    # main moves on with a conflicting line...
    git("switch", "-c", "feature", cwd=clone)
    (clone / "f.txt").write_text("feature\n")
    git("commit", "-am", "feature", cwd=clone)
    git("switch", "main", cwd=clone)
    (clone / "f.txt").write_text("main moved\n")
    git("commit", "-am", "moved", cwd=clone)
    git("push", "origin", "main", cwd=clone)
    git("switch", "feature", cwd=clone)

    refused = _guard(f"git -C {clone} push origin feature")
    assert refused.returncode == 2
    assert "conflicts with origin/main" in refused.stdout + refused.stderr
    # ...while the exempt shapes pass untouched.
    assert _guard(f"git -C {clone} push --tags").returncode == 0
