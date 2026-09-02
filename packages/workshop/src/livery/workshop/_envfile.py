r"""The one dotenv implementation: parsing, substitution, the cascade.

Every ``.repo.env`` in the wild is written against these rules:

- **Precedence** (highest first, kind-major): the pre-existing
  process environment, ``.repo.env.local`` files (nearest to
  farthest), ``$LIVERY_HOME/.repo.shared.env``, committed
  ``.repo.env`` files (nearest to farthest). The shared file's
  location may itself use ``${...}``, so a first pass over the
  repo-level files resolves it.
- **Values**: ``~`` expands at the start of a raw value; double
  quotes strip and allow substitution; single quotes strip and are
  literal; an unquoted value loses inline ``#`` comments and
  trailing whitespace.
- **Substitution**: ``$VAR`` and ``${VAR}`` resolve from the stack
  first, then the pre-existing environment, else empty; ``\$``
  stays a literal dollar; iterated until stable.
- **Provenance** is tracked per key (which layer supplied the
  value); the display and the agent emission select on it.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path

#: Name suffixes whose values are masked in display output.
SECRET_SUFFIXES = ("_TOKEN", "_SECRET", "_PASSWORD", "_KEY", "_API_KEY")

#: Matches a ``KEY=value`` assignment line in an env file.
KEY_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)")

_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

_LITERAL = "\x00literal\x00"
_ESCAPED_DOLLAR = "\x00dollar\x00"

#: The shared store's default: per user, shared by every repo on the
#: machine. Override per machine in ``.repo.env.local`` or in the
#: environment.
DEFAULT_HOME = "~/.livery"

_HOME_NAME = "LIVERY_HOME"


class Source(IntEnum):
    """Where a stack value came from (lower is higher precedence)."""

    environment = 0
    """The pre-existing process environment: always wins."""
    local = 1
    """``.repo.env.local`` (per machine, gitignored)."""
    shared = 2
    """``$LIVERY_HOME/.repo.shared.env`` (per person, cross-repo)."""
    repo = 3
    """``.repo.env`` (committed repo config)."""


def is_secret_name(key: str) -> bool:
    """Whether *key* names a secret by the suffix convention."""
    return any(key.endswith(suffix) for suffix in SECRET_SUFFIXES)


def _expand_tilde(value: str) -> str:
    if value.startswith("~"):
        return os.path.expanduser("~") + value[1:]
    return value


def _parse_raw(raw: str) -> str:
    """Quote and comment handling for one raw value."""
    double = re.match(r'^"(.*)"\s*(#.*)?$', raw)
    if double:
        return double.group(1)
    single = re.match(r"^'(.*)'\s*(#.*)?$", raw)
    if single:
        return _LITERAL + single.group(1)
    value = raw.split("#", 1)[0]
    return value.rstrip()


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse an env file into ``key -> raw value`` pairs.

    Comments and blank lines are skipped, surrounding quotes
    stripped, ``${VAR}`` left raw. The membership-only readers use
    this; full resolution goes through
    ``livery.workshop._envfile.load_cascade``. A missing file
    answers an empty dict.
    """
    result: dict[str, str] = {}
    if not path.is_file():
        return result
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = KEY_RE.match(line)
        if match:
            key = match.group(1)
            value = match.group(2).strip()
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                value = value[1:-1]
            result[key] = value
    return result


@dataclass
class EnvStack:
    """The resolved env-file stack: values plus per-key provenance."""

    values: dict[str, str] = field(default_factory=dict)
    sources: dict[str, Source] = field(default_factory=dict)

    def managed(self) -> dict[str, str]:
        """The keys the files supplied (everything not pre-existing)."""
        return {
            key: value
            for key, value in self.values.items()
            if self.sources[key] is not Source.environment
        }


def _substitute(value: str, lookup: dict[str, str], environ: dict[str, str]) -> str:
    """One substitution pass over *value* (``$VAR`` and ``${VAR}``)."""
    out: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value) and value[i + 1] == "$":
            out.append(_ESCAPED_DOLLAR)
            i += 2
            continue
        if ch != "$":
            out.append(ch)
            i += 1
            continue
        rest = value[i + 1 :]
        if rest.startswith("{"):
            match = _NAME_RE.match(rest, 1)
            if match and rest[match.end() : match.end() + 1] == "}":
                name = match.group(0)
                out.append(lookup.get(name, environ.get(name, "")))
                i += 2 + len(name) + 1
                continue
            out.append("${")
            i += 2
            continue
        match = _NAME_RE.match(rest)
        if match:
            name = match.group(0)
            out.append(lookup.get(name, environ.get(name, "")))
            i += 1 + len(name)
            continue
        out.append("$")
        i += 1
    return "".join(out)


def load_layer(
    path: Path,
    source: Source,
    stack: EnvStack,
    environ: dict[str, str],
) -> None:
    """Fold one file into *stack*; pre-existing environment wins."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = KEY_RE.match(stripped)
        if match is None:
            continue
        key, raw = match.group(1), match.group(2)
        env_value = environ.get(key, "")
        if env_value:
            stack.values[key] = _expand_tilde(env_value)
            stack.sources[key] = Source.environment
            continue
        # Later loads override earlier ones; the caller feeds files
        # from lowest precedence (repo) to highest (local).
        stack.values[key] = _parse_raw(_expand_tilde(raw))
        stack.sources[key] = source


MAX_SUBSTITUTION_PASSES = 16
"""How many times one value may be re-substituted before it is given up.

Each pass resolves one level of reference, so a legitimate chain
settles in a handful. Two keys referring to each other (``A=$B``
beside ``B=$A``) never settle: the value alternates, so it never
equals its own one-step predecessor and an unbounded loop runs
forever. That loop is inside the cascade every ``fm`` command loads,
so the bound is what keeps a mistyped pair from hanging the whole
toolchain.
"""


def resolve_all(stack: EnvStack, environ: dict[str, str]) -> None:
    """Substitute every value until stable; honour literals and escapes.

    A value that has not settled within
    ``livery.workshop._envfile.MAX_SUBSTITUTION_PASSES`` keeps
    its raw text, unexpanded: showing ``$B`` is the answer a reader
    can act on, and half an expansion would look like a resolved
    value.
    """
    for key, value in list(stack.values.items()):
        if value.startswith(_LITERAL):
            stack.values[key] = value[len(_LITERAL) :]
            continue
        raw = value
        previous = None
        for _ in range(MAX_SUBSTITUTION_PASSES):
            if value == previous:
                break
            previous = value
            value = _substitute(value, stack.values, environ)
        else:
            value = raw.replace("\\$", "$")
        stack.values[key] = value.replace(_ESCAPED_DOLLAR, "$")


def cascade_dirs(repo_root: Path, cwd: Path) -> list[Path]:
    """Directories from the repo ceiling down to *cwd*, farthest first.

    Farthest first, so folding with later-wins gives the nearest
    file precedence within its kind.
    """
    root = repo_root.resolve()
    cur = cwd.resolve()
    if root != cur and root not in cur.parents:
        return [root]
    dirs: list[Path] = []
    while True:
        dirs.append(cur)
        if cur == root:
            break
        cur = cur.parent
    dirs.reverse()
    return dirs


def preferred_home(stack: EnvStack, environ: dict[str, str] | None = None) -> str:
    """The winning ``LIVERY_HOME``, or "" when nothing set it.

    The environment beats every file; with no shipped default line
    the name may appear in no file at all, yet a plain exported
    ``LIVERY_HOME`` must still win. The caller applies
    ``livery.workshop._envfile.DEFAULT_HOME`` on "".
    """
    env = environ or {}
    if env.get(_HOME_NAME):
        return env[_HOME_NAME]
    return stack.values.get(_HOME_NAME, "")


def load_cascade(
    repo_root: Path,
    cwd: Path,
    *,
    shared_dir: Path | None = None,
    environ: dict[str, str] | None = None,
) -> EnvStack:
    """Load the multi-level env-file cascade, the one config mechanism.

    ``.repo.env`` (committed) and ``.repo.env.local`` (machine,
    secrets, gitignored) at every level from the repo ceiling down
    to *cwd*. Precedence is kind-major: real environment, then
    ``.local`` files nearest to farthest, then the shared file, then
    committed files nearest to farthest, so a machine override beats
    committed config at any depth.

    *shared_dir* is where ``.repo.shared.env`` lives (the livery
    home); when None it is resolved from the repo-level files first,
    the two-pass behaviour: the home may be named only by the
    environment or a file, and either way the shared store's file
    still loads. *environ* defaults to ``os.environ`` and is
    injectable for tests.
    """
    env = dict(os.environ) if environ is None else environ
    dirs = cascade_dirs(repo_root, cwd)
    committed = [d / ".repo.env" for d in dirs]
    local = [d / ".repo.env.local" for d in dirs]

    if shared_dir is None:
        first_pass = EnvStack()
        for path in committed:
            load_layer(path, Source.repo, first_pass, env)
        for path in local:
            load_layer(path, Source.local, first_pass, env)
        resolve_all(first_pass, env)
        home = preferred_home(first_pass, env) or DEFAULT_HOME
        shared_dir = Path(_expand_tilde(home))

    stack = EnvStack()
    for path in committed:
        load_layer(path, Source.repo, stack, env)
    load_layer(shared_dir / ".repo.shared.env", Source.shared, stack, env)
    for path in local:
        load_layer(path, Source.local, stack, env)
    resolve_all(stack, env)
    return stack


def quote_value(value: str) -> str:
    """*value* spelled so the parser reads it back unchanged.

    An unquoted value loses everything from its first ``#`` onward
    and all trailing whitespace, so a value carrying either is
    written inside double quotes, which the parser strips again. A
    line break cannot be spelled on one line at all, the value would
    read back as several lines with the second silently discarded,
    so it is refused rather than written.

    Raises:
        ValueError: *value* contains a line break.
    """
    if "\n" in value or "\r" in value:
        raise ValueError("a line break cannot be stored in an env file")
    if "#" in value or value != value.strip():
        return f'"{value}"'
    return value


def set_value(path: Path, key: str, value: str) -> None:
    """Set ``key=value`` in *path*, preserving the file's other content.

    The stack's one write primitive: creates the file and parents
    when missing, replaces the key's line in place when present,
    appends with a blank separator otherwise. The value is spelled
    by ``livery.workshop._envfile.quote_value``, so what is read
    back is what was set.
    """
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    new_line = f"{key}={quote_value(value)}"
    for index, line in enumerate(lines):
        match = KEY_RE.match(line)
        if match and match.group(1) == key:
            lines[index] = new_line
            break
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(new_line)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def member_keys(repo_root: Path, cwd: Path, shared_dir: Path) -> set[str]:
    """Every key the cascade's files define (membership, no values).

    The one definition of which files make up the stack, for the
    membership consumers (the agent emission, ``env.show``): the
    same files ``livery.workshop._envfile.load_cascade`` folds
    for a run from *cwd*.
    """
    keys: set[str] = set()
    for directory in cascade_dirs(repo_root, cwd):
        keys |= set(parse_env_file(directory / ".repo.env"))
        keys |= set(parse_env_file(directory / ".repo.env.local"))
    keys |= set(parse_env_file(shared_dir / ".repo.shared.env"))
    return keys
