"""Agent lifecycle hooks: the guards a session's tooling runs.

Wired from ``.claude/settings.json`` through one shim
(``.claude/hooks/fm-hook.sh``); each event arrives on stdin and the
verdict is the exit code: 0 is quiet, 2 blocks the gated action with
stderr as the agent's feedback. The shim propagates only the hook's
own 2 and turns every other failure into 0, because a guard that
cannot run must not deny.
"""

from __future__ import annotations

import dataclasses
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from footman import RunFailed, fail, run, stdin

from livery.workshop._tree import agent_hooks


@dataclass
class ToolInput:
    """What the agent handed the tool, as the hook event carries it."""

    file_path: str = ""
    command: str = ""  # Bash only: what the agent is about to run


@dataclass
class HookEvent:
    """One Claude Code hook event, parsed from stdin."""

    tool_input: ToolInput = dataclasses.field(default_factory=ToolInput)
    stop_hook_active: bool = False
    session_id: str = ""
    transcript_path: str = ""


_QUOTED = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")


def _runs_runner() -> re.Pattern[str]:
    """The pattern matching an invocation of the running CLI.

    Built per call, not at import: the brand installs before tasks
    run but tests repoint it, and ``fm``/``footman`` always match so
    the stock spellings stay guarded under any brand.
    """
    from livery.workshop._brand import runner_prog

    return re.compile(
        r"^\s*(?:uv run(?: --\S+)* )?(?:" + re.escape(runner_prog()) + r"|fm|footman)\b"
    )


# `|&` is bash's pipe-with-stderr; it hides the exit code exactly
# like a bare pipe, so both forms count.
_TRUNCATES = re.compile(r"\|&?\s*(?:tail|head)\b")
_PUSHES = re.compile(r"^\s*git\s+(?:-C\s+(\S+)\s+)?push\b")
_PUSH_EXEMPT = re.compile(r"\s(?:--delete|-d|--tags)\b|\bpush\s+(?:\S+\s+)?main\b")


def _push_conflicts(repo: str | None) -> bool:
    """Whether HEAD conflicts with origin/main.

    GitHub's test-merge, run locally in milliseconds, before the push
    can create the silent state.

    Fails open on every uncertainty: an offline fetch probes whatever
    origin/main the clone last saw, and a repo with no such ref is not
    this guard's business. Only a conflict, merge-tree exit 1,
    distinct from its other failures, speaks.
    """
    import contextlib

    git = ["git", *(("-C", repo) if repo else ())]
    with contextlib.suppress(RunFailed):
        # Offline / no remote: the last-seen origin/main still answers.
        run([*git, "fetch", "--quiet", "origin", "main"], capture=True)
    try:
        run([*git, "rev-parse", "--verify", "-q", "origin/main^{commit}"], capture=True)
    except RunFailed:
        return False  # no origin/main at all: not this guard's business
    try:
        run([*git, "merge-tree", "--write-tree", "origin/main", "HEAD"], capture=True)
    except RunFailed as exc:
        # With the ref verified, exit 1 is merge-tree's one honest meaning:
        # "merged, with conflicts". (Unverified, 1 also means "no such ref".)
        return exc.result == 1
    return False


@agent_hooks.task(name="pre-bash")
def pre_bash(event: Annotated[HookEvent, stdin]) -> None:
    """Refuse the Bash commands that succeed while silently breaking state.

    The two: a footman gate piped into tail/head, and a git push of a
    branch that conflicts with origin/main. Ported from footman's own
    loop.

    **The pipe guard.** A gate's exit code is its verdict, and a pipe
    replaces it with the filter's, so `fm check | tail -4` reports 0
    whatever happened and prints the step summary while the failing step
    scrolls past above. A red gate has been reported green here exactly
    that way.

    **The push guard.** Agent sessions share this repository, so main moves
    while a branch is being built, and a branch pushed from a stale base
    opens a conflicting pull request for which GitHub cannot build its
    test-merge and therefore spawns no CI at all: no red X, no checks, just
    an absence nothing points at. The guard runs the same test-merge
    locally (git merge-tree --write-tree) before letting the push through.
    Tag pushes, deletions, and pushes of main itself pass untouched.

    Both are deliberately narrow. Command separators split first, so
    `fm check && echo done | tail` stays legal; quoted spans are data, so
    `rg "fm check" | head` passes. Nudges, not a sandbox.
    """
    segments = re.split(r";|&&|\|\|", event.tool_input.command)
    blind = [_QUOTED.sub('""', segment) for segment in segments]
    if any(
        _TRUNCATES.search(segment[match.end() :])
        for segment in blind
        if (match := _runs_runner().search(segment)) is not None
    ):
        fail(
            "piping a footman command into tail/head replaces its exit code "
            "with the filter's and hides the failing step - a red gate has "
            "been reported green here that way. Run it unpiped and read the "
            "exit code; to keep the output short, redirect to a file and "
            "slice the file.",
            code=2,
        )
    for segment in blind:
        push = _PUSHES.search(segment)
        if push is None or _PUSH_EXEMPT.search(segment):
            continue
        repo = push.group(1)  # a quoted -C path was blinded; probe the cwd then
        if _push_conflicts(None if repo in (None, '""') else repo):
            fail(
                "git push refused: this branch conflicts with origin/main. A "
                "conflicting PR spawns no CI at all - GitHub cannot build its "
                "test-merge, so there is no red X, no checks, just silence. "
                "Rebase (git fetch origin && git rebase origin/main), re-run "
                "the gate, then push.",
                code=2,
            )


@agent_hooks.task(name="post-edit")
def post_edit(event: Annotated[HookEvent, stdin]) -> None:
    """Keep the edited Python file ruff-clean; never block an edit.

    Best-effort by design: every outcome exits 0 and says nothing,
    because a formatter problem must never block an edit. The gate,
    not the hook, is the arbiter; this only saves round trips.
    """
    import contextlib
    import subprocess

    path = event.tool_input.file_path
    if not path.endswith(".py") or not Path(path).is_file():
        return
    for args in (["check", "--fix", "--quiet"], ["format", "--quiet"]):
        with contextlib.suppress(OSError):
            subprocess.run(
                [sys.executable, "-m", "ruff", *args, path],
                check=False,
                capture_output=True,
            )


_FAIL = re.compile(
    r"fm: [\w.-]+: (?:RunFailed|exited with code \d+)"
    # footman pads the verdict word, so FAIL is followed by one
    # space or more, never a fixed two.
    r"|^FAIL +[\w.-]+"
    r"|fm: [\w.-]+: .*SystemExit",
    re.MULTILINE,
)


def _last_bash_result(transcript: Path) -> str:
    """The transcript's last Bash tool_result text; "" when none.

    Any read or parse problem degrades to "" and the hook passes: the
    guard exists to catch an unreported red verdict, not to make the
    transcript's format a way to trap a session.
    """
    import json

    last_bash_ids: set[str] = set()
    last_result = ""
    try:
        lines = transcript.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        content = ((entry.get("message") or {}).get("content")) or []
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") == "Bash":
                last_bash_ids.add(str(block.get("id")))
            elif (
                block.get("type") == "tool_result"
                and str(block.get("tool_use_id")) in last_bash_ids
            ):
                inner = block.get("content")
                if isinstance(inner, str):
                    last_result = inner
                elif isinstance(inner, list):
                    last_result = " ".join(
                        str(part.get("text", ""))
                        for part in inner
                        if isinstance(part, dict)
                    )
    return last_result


@agent_hooks.task(name="stop")
def stop(event: Annotated[HookEvent, stdin]) -> int:
    """Block ending a turn whose last Bash result is a red fm verdict.

    ``stop_hook_active`` (the harness's retry marker) always passes,
    so a deliberate stop with a reported failure costs one nudge and
    never loops.
    """
    if event.stop_hook_active:
        return 0
    transcript = Path(event.transcript_path)
    if not transcript.is_file():
        return 0
    if not _FAIL.search(_last_bash_result(transcript)):
        return 0
    sys.stderr.write(
        "The last Bash result in this turn is a FAILED fm command."
        " Fix it now, or - if the failure is deliberate and already"
        " reported to the user in your final message - stop again to"
        " proceed.\n"
    )
    return 2
