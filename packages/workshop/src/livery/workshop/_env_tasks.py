"""The ``env`` group: enter, show, set, and verify the environment.

The cascade (``livery.workshop._envfile``) is the one config
mechanism; this module is every way it reaches a consumer: applied
to each ``fm`` run (a pre-tasks hook, environment always winning),
emitted for a shell or an agent or GitHub Actions, shown masked, set
scoped, and verified against the tool profile the present package
types derive.

Every task here repairs or diagnoses the environment, so none
carries an availability gate: they must run on a cold checkout.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal

import footman
from footman import Arg, Stdout, doc, fail, group, pre_tasks

from livery.workshop._envfile import (
    DEFAULT_HOME,
    EnvStack,
    Source,
    _expand_tilde,
    cascade_dirs,
    is_secret_name,
    load_cascade,
    member_keys,
    parse_env_file,
    preferred_home,
    set_value,
)

#: Names never emitted for an agent: the PATH family is composed per
#: shell, not copied between sessions.
NEVER_AGENT = ("PATH",)

env = group("env", help="The environment: enter, show, set, verify")

#: Keys the pre-tasks hook itself contributed this run. The hook's
#: setdefault-ed values look pre-existing to a later cascade load, so
#: the emitters merge this back before selecting on provenance.
_APPLIED: dict[str, str] = {}


def _workspace() -> tuple[Path, Path]:
    from livery.workshop._layers import workspace_root

    root = workspace_root()
    if root is None:
        fail("no workspace: no livery.toml above the working directory")
    return root, Path.cwd()


def _shared_dir(root: Path, cwd: Path, environ: dict[str, str]) -> Path:
    stack = EnvStack()
    from livery.workshop._envfile import load_layer, resolve_all

    for directory in cascade_dirs(root, cwd):
        load_layer(directory / ".repo.env", Source.repo, stack, environ)
    for directory in cascade_dirs(root, cwd):
        load_layer(directory / ".repo.env.local", Source.local, stack, environ)
    resolve_all(stack, environ)
    return Path(_expand_tilde(preferred_home(stack, environ) or DEFAULT_HOME))


@pre_tasks
def apply_cascade(inv: footman.Invocation) -> None:
    """Fold the cascade into this run's environment; environment wins.

    Every ``fm`` command sees the repo's declared environment, so a
    cold shell still runs with the tokens and settings the files
    hold. Only absent keys are written, and what the hook itself
    contributed is remembered: those values look pre-existing to a
    later load, and the emitters must not misread them as the
    shell's own.
    """
    from livery.workshop._layers import workspace_root

    _ = inv
    _APPLIED.clear()
    root = workspace_root()
    if root is None:
        return
    stack = load_cascade(root, Path.cwd())
    for key, value in stack.managed().items():
        # Truthiness, not presence, matches the cascade's own
        # env-wins rule: a key exported empty reads as unset.
        if not os.environ.get(key):
            os.environ[key] = value
            _APPLIED[key] = value


@dataclass(frozen=True)
class EnvDelta:
    """What entering the environment adds: variables and PATH entries."""

    values: dict[str, str] = field(default_factory=dict)
    paths: tuple[str, ...] = ()


def workspace_delta(root: Path, cwd: Path) -> EnvDelta:
    """The delta an entered shell needs for this workspace.

    The cascade's managed values, plus the venv: ``VIRTUAL_ENV`` and
    the venv's bin on PATH, so ``fm`` and the venv tools resolve.
    The seam a tool store would extend.
    """
    stack = load_cascade(root, cwd)
    values = dict(stack.managed())
    for key, value in _APPLIED.items():
        values.setdefault(key, os.environ.get(key, value))
    values["VIRTUAL_ENV"] = str(root / ".venv")
    return EnvDelta(values=values, paths=(str(root / ".venv" / "bin"),))


def _posix_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def _check_emittable(name: str, value: str) -> None:
    import re

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        fail(
            f"{name!r} is not an exportable variable name: only the value"
            " side is quoted, and a malformed name changes what the line"
            " means."
        )
    if "\n" in value or "\r" in value:
        fail(
            f"{name} carries a line break, which cannot be quoted into a"
            " single export line. Remove it from the env file."
        )


def emit_lines(delta: EnvDelta, dialect: str) -> list[str]:
    """Serialise *delta* for *dialect* (``posix`` or ``pwsh``).

    One export per variable, then exactly one PATH prepend. A value
    or name that cannot be spelled safely is refused: only the value
    side is quoted, and ``export A B=1`` exports ``A``.
    """
    lines: list[str] = []
    for name in sorted(delta.values):
        value = delta.values[name]
        _check_emittable(name, value)
        if dialect == "pwsh":
            doubled = value.replace("'", "''")
            lines.append(f"$env:{name} = '{doubled}'")
        else:
            lines.append(f"export {name}={_posix_quote(value)}")
    if delta.paths:
        for entry in delta.paths:
            _check_emittable("PATH", entry)
        if dialect == "pwsh":
            joined = ";".join(entry.replace("'", "''") for entry in delta.paths)
            lines.append(f"$env:PATH = '{joined};' + $env:PATH")
        else:
            joined = ":".join(_posix_quote(entry) for entry in delta.paths)
            lines.append(f'export PATH={joined}:"$PATH"')
    return lines


_COMPLETION_HOOK = (
    "# Interactive shells get completion; a pipe or script never does.\n"
    'case $- in *i*) eval "$(fm --setup-completion)";; esac'
)

# MenuComplete, because registering completions is only half the job:
# PSReadLine's default Tab handler cycles candidates one keypress at a
# time and shows neither the list nor the per-item help, so a shell
# with completion fully working still feels like it has none. Guarded
# twice over: PSReadLine is absent in constrained hosts, and a missing
# module must not break entering the shell. No interactive guard:
# pwsh has no reliable one-liner for it.
_COMPLETION_PWSH = (
    "if (Get-Command fm -ErrorAction SilentlyContinue) {"
    ' $fmHook = (fm --setup-completion=pwsh 2>$null) -join "`n";'
    " if ($fmHook) { $fmHook | Invoke-Expression } }"
    "; if (-not $env:LIVERY_NO_SHELL_CUSTOMISATION"
    " -and (Get-Module -ListAvailable PSReadLine)) {"
    " Set-PSReadLineKeyHandler -Key Tab -Function MenuComplete }"
)


def agent_delta(root: Path, cwd: Path, environ: dict[str, str]) -> EnvDelta:
    """The variables an agent session needs, selected by membership.

    Membership, not provenance: a session that already carries these
    variables would reclassify them as pre-existing, and a
    provenance filter would silently drop them. Secrets are included
    on purpose, availability is the point; the PATH family is
    excluded, it is composed per shell.
    """
    shared = _shared_dir(root, cwd, environ)
    keys = member_keys(root, cwd, shared)
    values = {
        key: environ.get(key, _APPLIED.get(key, ""))
        for key in sorted(keys)
        if key not in NEVER_AGENT and (environ.get(key) or _APPLIED.get(key))
    }
    values["VIRTUAL_ENV"] = str(root / ".venv")
    return EnvDelta(values=values, paths=(str(root / ".venv" / "bin"),))


def github_persist(delta: EnvDelta, environ: dict[str, str]) -> list[str]:
    """Append *delta* to GITHUB_ENV and GITHUB_PATH; what was written.

    Only under GitHub Actions, and never a secret name: the runner's
    env file is not a secret store, and the workflow's own secrets
    mechanism carries those.
    """
    if not environ.get("GITHUB_ACTIONS"):
        fail("--github persists into GITHUB_ENV, which only Actions sets")
    written: list[str] = []
    env_file = environ.get("GITHUB_ENV", "")
    if env_file:
        with Path(env_file).open("a", encoding="utf-8") as handle:
            for name in sorted(delta.values):
                if is_secret_name(name):
                    continue
                _check_emittable(name, delta.values[name])
                handle.write(f"{name}={delta.values[name]}\n")
                written.append(name)
    path_file = environ.get("GITHUB_PATH", "")
    if path_file:
        with Path(path_file).open("a", encoding="utf-8") as handle:
            for entry in delta.paths:
                handle.write(f"{entry}\n")
                written.append(f"PATH+{entry}")
    return written


@env.task(name="emit")
def env_emit(
    target: Arg[Literal["posix", "pwsh", ""]] = "",
    agent: Annotated[
        bool, doc("the agent selection: membership, secrets included")
    ] = False,
    github: Annotated[
        bool, doc("persist into GITHUB_ENV/GITHUB_PATH (Actions only)")
    ] = False,
) -> Stdout[str]:
    """Emit the environment for evaluation by a shell.

    Bare: the entered-shell emission plus the interactive completion
    hook. ``--agent``: the membership selection for an agent session
    (evaluated by its env file, so no completion hook). ``--github``
    writes the runner's env files instead of printing.
    """
    import sys

    root, cwd = _workspace()
    dialect = target or ("pwsh" if sys.platform == "win32" else "posix")
    if github:
        delta = workspace_delta(root, cwd)
        written = github_persist(delta, dict(os.environ))
        return "\n".join(written)
    if agent:
        delta = agent_delta(root, cwd, dict(os.environ))
        return "\n".join(emit_lines(delta, dialect))
    delta = workspace_delta(root, cwd)
    lines = emit_lines(delta, dialect)
    lines.append(_COMPLETION_PWSH if dialect == "pwsh" else _COMPLETION_HOOK)
    return "\n".join(lines)


def _mask(key: str, value: str) -> str:
    if not is_secret_name(key) or not value:
        return value
    return f"({len(value)} chars, masked)"


@env.task(name="show")
def env_show(
    full: Annotated[bool, doc("show secret values unmasked")] = False,
) -> None:
    """Show the cascade: sources, values, and the PATH breakdown.

    Secrets are masked by the suffix convention unless ``--full``.
    The PATH breakdown numbers every entry and marks the missing
    directories; duplicates are shown as they are.
    """
    root, cwd = _workspace()
    environ = dict(os.environ)
    shared = _shared_dir(root, cwd, environ)
    print("  Sources:")
    for directory in cascade_dirs(root, cwd):
        for name in (".repo.env", ".repo.env.local"):
            candidate = directory / name
            if candidate.is_file():
                print(f"    {candidate}")
    if (shared / ".repo.shared.env").is_file():
        print(f"    {shared / '.repo.shared.env'}")
    # The pre-tasks hook already exported every managed key into this
    # process, so a live-environ load would classify everything as
    # the shell's own. Subtracting the hook's contributions restores
    # the files' provenance; a key the shell genuinely exported still
    # reads as environment.
    seen = {k: v for k, v in environ.items() if k not in _APPLIED}
    stack = load_cascade(root, cwd, environ=seen)
    files_only = load_cascade(root, cwd, environ={})
    if stack.values:
        print("  Variables:")
        width = max(len(key) for key in stack.values)
        for key in sorted(stack.values):
            value = stack.values[key] if full else _mask(key, stack.values[key])
            source = stack.sources[key].name
            marker = ""
            declared = files_only.values.get(key)
            if (
                stack.sources[key] is Source.environment
                and declared is not None
                and declared != stack.values[key]
            ):
                # An older export than the file declares: stale until
                # the person re-enters.
                marker = "  [stale - the files say otherwise; re-enter]"
            print(f"    {key:<{width}}  {value}  ({source}){marker}")
    print("  PATH:")
    for index, entry in enumerate(environ.get("PATH", "").split(os.pathsep), 1):
        marker = "" if Path(entry).is_dir() else "  (missing)"
        print(f"    {index:>2}  {entry}{marker}")


@env.task(name="set", interactive=True)
def env_set(
    key: Annotated[str, doc("the variable name")],
    value: Annotated[str, doc("the value; empty deletes the key")] = "",
    scope: Annotated[
        str, doc("local (this machine), shared (this person), repo (committed)")
    ] = "local",
) -> None:
    """Set or delete one variable in the chosen scope's file.

    ``local`` writes ``.repo.env.local`` at the workspace root,
    ``shared`` the person-wide ``$LIVERY_HOME/.repo.shared.env``,
    ``repo`` the committed ``.repo.env``. After a write the
    higher-precedence sources are checked: a shadowed value is named
    rather than discovered later.
    """
    root, cwd = _workspace()
    environ = dict(os.environ)
    shared = _shared_dir(root, cwd, environ)
    files = {
        "local": root / ".repo.env.local",
        "shared": shared / ".repo.shared.env",
        "repo": root / ".repo.env",
    }
    if scope not in files:
        fail(f"unknown scope {scope!r}: local, shared, or repo")
    path = files[scope]
    if not value:
        existing = parse_env_file(path)
        if key not in existing:
            print(f"  {key} is not set in {path}")
            return
        if not footman.confirm(f"Delete {key} from {path}?"):
            print("  Left alone")
            return
        lines = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.strip().startswith(f"{key}=")
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"  Deleted {key} from {path}")
        return
    try:
        set_value(path, key, value)
    except ValueError as error:
        fail(str(error))
    print(f"  Set {key} in {path}")
    order: dict[str, Source] = {
        "repo": Source.repo,
        "shared": Source.shared,
        "local": Source.local,
    }
    written = order[scope]
    # The pre-tasks hook exported the cascade into this process, so a
    # bare environ check would call every file-defined key the
    # shell's own and warn falsely; only a key the hook did not
    # contribute is genuinely the shell's.
    if environ.get(key) and key not in _APPLIED:
        print(
            f"  Note: {key} is set in your shell's own environment, which"
            " overrides every env file, so this value will not take effect"
            " until that export is gone."
        )
    else:
        for other_scope, source in order.items():
            if source < written and key in parse_env_file(files[other_scope]):
                print(
                    f"  Note: {key} is also defined in {files[other_scope]}"
                    f" ({source.name}), which overrides {written.name}: this"
                    " value will not take effect."
                )
    print("  Restart your terminal (or the editor) for this to take effect.")


def _uv_drift(root: Path) -> str:
    """A DRIFT line when the running uv is not the lock's pin; else "".

    uv rides the dev group, so the lock pins it exactly; the venv
    tools follow the lock by construction, but uv itself is resolved
    from the machine and can drift. A workspace without a lock has
    no pin to judge against and answers "".
    """
    import subprocess

    lock = root / "uv.lock"
    if not lock.is_file():
        return ""
    text = lock.read_text("utf-8")
    anchor = text.find('name = "uv"')
    if anchor == -1:
        return ""
    pinned = ""
    for line in text[anchor : anchor + 200].splitlines():
        if line.startswith("version = "):
            pinned = line.split('"')[1]
            break
    if not pinned:
        return ""
    probe = subprocess.run(
        ["uv", "--version"], capture_output=True, text=True, check=False
    )
    running = probe.stdout.split()[1] if probe.stdout.split()[1:] else ""
    if probe.returncode != 0 or not running:
        return "uv: ? (could not read the running version)"
    if running != pinned:
        return f"uv: DRIFT (running {running}, the lock pins {pinned})"
    return ""


def tool_profile(root: Path) -> tuple[str, ...]:
    """The tools the present package types require, by discovery.

    No package of a type, no tool for it: a pure-python workspace
    answers uv plus the venv toolchain, and a future package type
    contributes its own tools by existing.
    """
    from livery.workshop._packages import discover_packages

    types = (
        {package.type for package in discover_packages(root)}
        if (root / "packages").is_dir()
        else set()
    )
    profile: list[str] = ["uv"]
    if not types or "python" in types:
        profile += ["ruff", "pytest", "basedpyright", "mypy", "ty", "pyrefly"]
    return tuple(profile)


@env.task(name="check")
def env_check() -> int:
    """Verify this shell against the derived tool profile.

    Run it bare from the session being checked: a freshly entered
    shell would report itself, not yours. Every tool of the profile
    must resolve; a miss prints the PATH breakdown and the remedy.
    """
    root, _cwd = _workspace()
    problems: list[str] = []
    venv_bin = root / ".venv" / "bin"
    for tool in tool_profile(root):
        if not (shutil.which(tool) or (venv_bin / tool).is_file()):
            problems.append(f"{tool}: MISSING")
            continue
        if tool == "uv":
            drift = _uv_drift(root)
            if drift:
                problems.append(drift)
    if not problems:
        print("  environment ok: every profile tool resolves")
        return 0
    for problem in problems:
        print(f"  {problem}")
    print("  PATH:")
    for index, entry in enumerate(os.environ.get("PATH", "").split(os.pathsep), 1):
        marker = "" if Path(entry).is_dir() else "  (missing)"
        print(f"    {index:>2}  {entry}{marker}")
    print(
        "  The profile derives from the present package types. Run"
        " `fm sync` to provision, or enter the environment with"
        " `fm shell`."
    )
    return 1
