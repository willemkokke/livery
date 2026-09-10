"""The CI state store: text files on ``refs/workshop/*`` refs.

State that CI runs keep across runs (timing rows, verified trees,
coverage marks) lives as files on git refs in a namespace outside
``refs/heads``, so a default clone or fetch never downloads it. Each
ref is one root commit whose tree holds the series' files; a write
reads the current tree, rebuilds it with the kept entries plus the
new ones, and pushes a fresh root commit over the old one, so a
window needs no history and a reader never sees a torn tree.

A [livery.workshop._state.Series][] declares one ref's rows: reach
for its `rows` and `row` to read and its `put` to write. The store
parses, stamps the schema and the time, applies the window, and
refuses what the declaration forbids, so no caller does any of
that itself. The bare `read` and `put` move files that are not
rows (a per-run half, a coverage entry), and `sweep` is the
janitor. Every failure is a printed reason, never a boolean: a
stamp is best-effort by contract, so the only way its failure is
ever noticed is by being printed.

The rules, ported from hse's stamp transport:

- Only CI writes a series declared ``ci_only``; local runs read.
- A write never starts from a state it could not read: an
  unreachable ref refuses the write, because rebuilding the tree
  from an empty map would erase every file the writer did not know
  about. A ref that does not exist yet is not a failure.
- Across runs a shared ref is written by compare-and-swap: the push
  carries the sha the writer read (``--force-with-lease`` with an
  explicit value), the server refuses a stale write atomically, and
  the loser re-reads, merges, and retries. A concurrency loss costs
  a retry, never a wrong result.
- The push is read back: a push that reports success can lie.
- The commit carries its own identity and ``[skip ci]``: the natural
  writer is a CI container with no global git config, and a push to
  this namespace must never start a workflow.

Plumbing, never porcelain: ``hash-object``, ``mktree``,
``commit-tree``, ``push``, ``ls-remote``. The working tree and the
index are never touched, so a write is safe from the middle of any
job. Payloads are text: a blob is hashed from a temporary file and
the tree is fed NUL-separated, so no newline ever crosses a text-mode
stdin, where Windows appends a carriage return that once named a
file no ``show`` could find.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import livery.footman as footman
from livery import toolroom

#: Every state ref lives under this prefix. A push here matches no
#: push trigger on any forge, so a store write can never start a
#: workflow, whatever the token.
NAMESPACE = "refs/workshop/"

#: The per-run refs: one per leg, written by that leg alone, read and
#: deleted by the run's gate job, swept by the janitor when a run is
#: cancelled before its gate could.
RUN_PREFIX = NAMESPACE + "run/"

#: How many times a compare-and-swap write re-reads and retries after
#: another writer moved the ref.
ATTEMPTS = 3

#: The variable the job runner sets for every entry it spawns, naming
#: the check leg (``check-ubuntu-latest-3.14``): the key of the leg's
#: rows and stamps.
LEG_VARIABLE = "WORKSHOP_LEG"

#: How a window ranks the files it keeps: a key from a file's name
#: and text, the newest sorting last.
Order = Callable[[str, str], tuple[str, str]]


@dataclass(frozen=True)
class Row:
    """One row of a series: the file it came from and its fields.

    Attributes:
        name: The file's name on the ref.
        data: The row's fields, ``schema`` and ``when`` included.
    """

    name: str
    data: dict[str, Any]

    @property
    def when(self) -> str:
        """When the store stamped the row, ISO 8601; empty when it never did."""
        return str(self.data.get("when", "") or "")


@dataclass(frozen=True)
class Rows:
    """What a series read found: the rows, what it skipped, and whether it could answer.

    Attributes:
        rows: The rows of the series' schema, newest first.
        skipped: One line per file that is not such a row, naming the
            file and why.
        failed: Whether the transport could not answer for a ref that
            exists, or reach the remote at all. An absent ref reads as
            no rows and no failure.
        reason: Why it could not answer, in the store's one wording.
    """

    rows: tuple[Row, ...]
    skipped: tuple[str, ...] = ()
    failed: bool = False
    reason: str = ""


@dataclass(frozen=True)
class Series:
    """A shared ref's declaration: its rows' schema, its window, and who may write it.

    A series is one ref holding JSON rows of one schema, one file per
    row. `rows` and `row` read, `put` writes; the store stamps the
    schema and the time, keeps the window, and refuses what the
    declaration forbids, so a caller never parses a row, checks a
    schema, or trims a ref itself.

    Attributes:
        name: The ref's name under the namespace.
        window: The newest rows kept after every write, by the time
            the store stamped them; ``None`` keeps everything.
        ci_only: Whether only a CI run may write it. Local runs read.
        schema: The rows' schema. A reader skips a row of another
            version and names it; a new field is the same version, a
            changed meaning is the next.
    """

    name: str
    window: int | None = None
    ci_only: bool = True
    schema: int = 1

    @property
    def ref(self) -> str:
        """The full ref name."""
        return NAMESPACE + self.name

    def rows(self, root: Path) -> Rows:
        """Every row of the series, newest first, with what was skipped and why.

        An absent ref is no rows and no failure. A ref the transport
        could not read is a failure with its reason, so every caller
        falls open the same way. A file that does not parse or is of
        another schema is skipped and named, and the rest stand.
        """
        found = read(root, self.ref)
        if found.files is None:
            return Rows((), failed=found.failed, reason=self._unreadable(found))
        rows: list[Row] = []
        skipped: list[str] = []
        for name in sorted(found.files):
            data, why = _parse(self, found.files[name])
            if data is None:
                skipped.append(f"{name}: {why}; skipped")
            else:
                rows.append(Row(name, data))
        rows.sort(key=_newest, reverse=True)
        return Rows(tuple(rows), tuple(skipped))

    def row(self, root: Path, name: str) -> tuple[Row | None, str]:
        """The row in the file *name*.

        ``(None, "")`` when the ref or the file is absent, and
        ``(None, reason)`` when the ref could not be read or the file
        is not a row of the schema.
        """
        found = read(root, self.ref)
        if found.files is None:
            return None, self._unreadable(found)
        text = found.files.get(name)
        if text is None:
            return None, ""
        data, why = _parse(self, text)
        if data is None:
            return None, f"{name}: {why}"
        return Row(name, data), ""

    def put(
        self, root: Path, rows: Mapping[str, Mapping[str, Any]], *, message: str
    ) -> str:
        """Write *rows* by file name, stamped; ``""`` or the reason.

        Every row is written with the schema and the time stamped on
        it, and the stamp wins over a ``schema`` or ``when`` the row
        carries.

        The window keeps the newest rows by their stamps, so a series
        keyed by something other than time (a tree id) keeps its
        newest too. A write the declaration forbids is refused with
        the rule; the transport's refusals come back as they are.
        """
        stamp = datetime.now(UTC).isoformat(timespec="microseconds")
        files = {
            name: json.dumps(
                {**row, "schema": self.schema, "when": stamp}, sort_keys=True
            )
            for name, row in rows.items()
        }
        # The transport's put: a class body is no scope for its methods.
        return put(
            root,
            self.ref,
            files,
            message=message,
            window=self.window,
            ci_only=self.ci_only,
            order=_by_when,
        )

    def _unreadable(self, found: Read) -> str:
        if not found.failed:
            return ""
        return f"the {self.name} series could not be read: {found.reason}"


def _parse(series: Series, text: str) -> tuple[dict[str, Any] | None, str]:
    """A file's row of *series*' schema, or ``(None, why)`` in one wording."""
    try:
        loaded: Any = json.loads(text)
    except ValueError:
        return None, "does not parse"
    seen = loaded.get("schema") if isinstance(loaded, dict) else None
    if seen != series.schema:
        version = "none" if seen is None else str(seen)
        return None, f"schema {version}, this reader speaks {series.schema}"
    return loaded, ""


def _by_when(name: str, text: str) -> tuple[str, str]:
    """A window's rank for a file: its stamp then its name; unstamped is oldest."""
    try:
        loaded: Any = json.loads(text)
    except ValueError:
        return "", name
    when = loaded.get("when") if isinstance(loaded, dict) else None
    return (when if isinstance(when, str) else ""), name


def _newest(row: Row) -> tuple[str, str]:
    return row.when, row.name


@dataclass(frozen=True)
class RunContext:
    """The CI run this process belongs to, as the runner's environment names it.

    Attributes:
        forge: ``github``, ``gitea``, or ``gitlab``.
        run_id: The forge's identifier of the run (GitLab: the pipeline).
        event: What started the run (``push``, ``pull_request``, ...).
        ref: The ref the run is for, as the runner spells it.
        head_sha: The commit the forge files the run under. On a pull
            request the checkout is the merge commit the forge
            synthesised, and the run's own head is the pull request's
            head, which the event payload names; on a push both are
            the same commit. Empty when the runner did not say.
        base_ref: The branch a pull request proposes into, as the event
            payload names it (GitLab: the merge request's target);
            empty on a push or when the runner did not say.
        leg: The check leg this process runs in (``check-ubuntu-latest-3.14``),
            as the job runner names it in ``WORKSHOP_LEG`` for every entry it
            spawns; empty outside a scheduled job.
    """

    forge: str
    run_id: str
    event: str
    ref: str
    head_sha: str = ""
    base_ref: str = ""
    leg: str = ""


def event_payload(environ: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    """The runner's event payload, or ``None`` when there is none to read.

    GitHub and Gitea write the event that started the run to the file
    ``GITHUB_EVENT_PATH`` names; a payload that is missing or does not
    parse reads as none, so a caller falls back rather than fails.
    """
    env = os.environ if environ is None else environ
    path = env.get("GITHUB_EVENT_PATH", "")
    if not path:
        return None
    try:
        payload = json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _event_head_sha(env: Mapping[str, str]) -> str:
    """The pull request's head from the event payload, or the pushed commit.

    A payload that is missing or does not parse falls back to the
    pushed commit: the lookup then works on a push and misses on a
    pull request, which the collect names rather than fails on.
    """
    payload = event_payload(env)
    if payload is not None:
        head = (payload.get("pull_request") or {}).get("head") or {}
        if isinstance(head, dict) and head.get("sha"):
            return str(head["sha"])
    return env.get("GITHUB_SHA", "")


def _event_base_ref(env: Mapping[str, str]) -> str:
    """The pull request's base branch from the event payload, or empty."""
    payload = event_payload(env)
    if payload is None:
        return ""
    base = (payload.get("pull_request") or {}).get("base") or {}
    if isinstance(base, dict) and base.get("ref"):
        return str(base["ref"])
    return ""


def run_context(environ: dict[str, str] | None = None) -> RunContext | None:
    """The CI run this process runs in, or ``None`` outside CI.

    GitHub and Gitea both speak the ``GITHUB_*`` variables (the
    act_runner keeps GitHub's names and adds ``GITEA_ACTIONS``);
    GitLab speaks ``GITLAB_CI`` and ``CI_*``.
    """
    env = os.environ if environ is None else environ
    if env.get("GITHUB_ACTIONS") == "true":
        forge = "gitea" if env.get("GITEA_ACTIONS") == "true" else "github"
        return RunContext(
            forge,
            env.get("GITHUB_RUN_ID", ""),
            env.get("GITHUB_EVENT_NAME", ""),
            env.get("GITHUB_REF", ""),
            _event_head_sha(env),
            _event_base_ref(env),
            env.get(LEG_VARIABLE, ""),
        )
    if env.get("GITLAB_CI") == "true":
        return RunContext(
            "gitlab",
            env.get("CI_PIPELINE_ID", ""),
            env.get("CI_PIPELINE_SOURCE", ""),
            env.get("CI_COMMIT_REF_NAME", ""),
            env.get("CI_COMMIT_SHA", ""),
            env.get("CI_MERGE_REQUEST_TARGET_BRANCH_NAME", ""),
            env.get(LEG_VARIABLE, ""),
        )
    return None


def run_ref(run: RunContext, leg: str) -> str:
    """The per-run ref one leg of *run* writes."""
    return f"{RUN_PREFIX}{run.run_id}/{leg}"


def identity() -> tuple[str, str]:
    """Who the store's commits are by: the runner's name, an invalid domain."""
    prog = footman.prog()
    return (f"{prog} ci state", f"ci-state@{prog}.invalid")


@dataclass(frozen=True)
class Read:
    """What a read found: the files, the lease, and whether it could answer.

    Attributes:
        files: The tree's files by name, or ``None`` when there is
            nothing to read.
        sha: The commit the ref points at on the remote, the lease a
            write carries; ``None`` when the ref does not exist.
        failed: Whether ``None`` means the transport could not answer
            for a ref that does exist, or a remote it could not reach,
            rather than a ref that has never been written. A reader
            may fall open on either; a writer refuses on a failure.
        reason: The transport's words when it could not answer.
    """

    files: dict[str, str] | None
    sha: str | None
    failed: bool
    reason: str = ""


def _git(root: Path, *args: str, stdin: str | None = None) -> toolroom.Result:
    tool = toolroom.git.opts(cwd=root, nofail=True, recorded=False)
    if stdin is not None:
        tool = tool.opts(input=stdin)
    return tool(*args)


def _words(result: toolroom.Result) -> str:
    return (result.stderr or result.stdout).strip()[:200] or "no output"


def _ref_exists(root: Path, ref: str) -> tuple[bool | None, str]:
    """Whether *ref* is on the remote; ``None`` when the probe cannot say."""
    probe = _git(root, "ls-remote", "origin", ref)
    if probe.code != 0:
        return None, _words(probe)
    return bool(probe.stdout.strip()), ""


def read(root: Path, ref: str) -> Read:
    """Read *ref*'s files from origin, saying whether a miss is absence or failure.

    The fetch lands in ``FETCH_HEAD`` and creates no local ref, so a
    checkout accrues only unreachable objects git's own gc prunes. A
    failed fetch is a failure only when the ref is there: a ref that
    does not exist also fails the fetch, and that is the ordinary
    first-run case. A remote that cannot answer the probe either
    counts as failed: an unreachable forge must never read as "there
    was never a stamp".
    """
    fetched = _git(root, "fetch", "--quiet", "origin", ref)
    if fetched.code != 0:
        exists, why = _ref_exists(root, ref)
        if exists is False:
            return Read(None, None, failed=False)
        return Read(None, None, failed=True, reason=why or _words(fetched))
    sha = _git(root, "rev-parse", "FETCH_HEAD").stdout.strip()
    listed = _git(root, "ls-tree", "--name-only", "FETCH_HEAD")
    if listed.code != 0:
        return Read(None, sha, failed=True, reason=_words(listed))
    files: dict[str, str] = {}
    for name in listed.stdout.split():
        shown = _git(root, "show", f"FETCH_HEAD:{name}")
        if shown.code != 0:
            return Read(None, sha, failed=True, reason=_words(shown))
        files[name] = shown.stdout
    return Read(files, sha, failed=False)


def list_refs(root: Path, prefix: str) -> dict[str, str] | None:
    """The remote's refs under *prefix* by sha; ``None`` when it cannot answer."""
    listed = _git(root, "ls-remote", "origin", prefix + "*")
    if listed.code != 0:
        return None
    refs: dict[str, str] = {}
    for line in listed.stdout.splitlines():
        sha, _, name = line.partition("\t")
        if name:
            refs[name.strip()] = sha.strip()
    return refs


def _build_commit(root: Path, files: dict[str, str], message: str) -> tuple[str, str]:
    """A root commit of *files*; ``(sha, "")`` or ``("", reason)``."""
    entries: list[str] = []
    for name in sorted(files):
        # A temporary file, never stdin: text-mode stdin translates
        # newlines on Windows and the blob would differ per platform.
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write(files[name])
            path = handle.name
        try:
            hashed = _git(root, "hash-object", "-w", path)
        finally:
            os.unlink(path)
        if hashed.code != 0:
            return "", f"hash-object failed: {_words(hashed)}"
        entries.append(f"100644 blob {hashed.stdout.strip()}\t{name}")
    # NUL-separated entries: no newline crosses stdin, so no platform's
    # text mode can rename a file to "<name>\r".
    tree = _git(root, "mktree", "-z", stdin="\0".join(entries) + "\0")
    if tree.code != 0:
        return "", f"mktree failed: {_words(tree)}"
    name, email = identity()
    commit = _git(
        root,
        "-c",
        f"user.name={name}",
        "-c",
        f"user.email={email}",
        "commit-tree",
        tree.stdout.strip(),
        "-m",
        f"{message} [skip ci]",
    )
    if commit.code != 0:
        return "", f"commit-tree failed: {_words(commit)}"
    return commit.stdout.strip(), ""


def _stale(pushed: toolroom.Result) -> bool:
    """Whether a refused push lost the lease rather than failing outright."""
    words = pushed.stderr + pushed.stdout
    return "stale info" in words or "[rejected]" in words


def put(
    root: Path,
    ref: str,
    files: dict[str, str],
    *,
    message: str,
    window: int | None = None,
    ci_only: bool = False,
    order: Order | None = None,
    attempts: int = ATTEMPTS,
) -> str:
    """Write *files* onto *ref*, keeping what it already holds; ``""`` or the reason.

    The tree is the ref's current files with *files* laid over them,
    trimmed to the newest *window* files when one is given, ranked by
    *order* or, without one, by name. The push
    is a compare-and-swap on the sha the read returned: a ref another
    writer moved in between refuses the push, and the write re-reads,
    merges, and retries up to *attempts* times. *ci_only* refuses a
    write from outside CI, naming the rule. Every refusal is the
    returned reason; nothing here raises or prints.
    """
    if not ref.startswith(NAMESPACE):
        return f"refusing {ref}: the state store writes only under {NAMESPACE}"
    if ci_only and run_context() is None:
        return f"refusing {ref}: only a CI run writes this series; local runs read"
    for _ in range(attempts):
        current = read(root, ref)
        if current.failed:
            return (
                f"refusing to write {ref}: its current state could not be"
                f" read ({current.reason}), and a write from an unread state"
                " would erase what it holds"
            )
        merged = {**(current.files or {}), **files}
        if window is not None and len(merged) > window:
            merged = _trim(merged, window, order)
        commit, why = _build_commit(root, merged, message)
        if why:
            return why
        lease = f"--force-with-lease={ref}:{current.sha or ''}"
        pushed = _git(root, "push", "--quiet", lease, "origin", f"{commit}:{ref}")
        if pushed.code != 0:
            if _stale(pushed):
                continue
            return f"push refused: {_words(pushed)}"
        return _readback(root, ref, commit)
    return f"gave up on {ref} after {attempts} attempts: another writer kept moving it"


def _trim(files: dict[str, str], window: int, order: Order | None) -> dict[str, str]:
    """The newest *window* of *files* by *order*, or by name without one."""
    if order is None:
        kept = sorted(files)[-window:]
    else:
        ranked = sorted((order(name, text), name) for name, text in files.items())
        kept = [name for _, name in ranked[-window:]]
    return {name: files[name] for name in kept}


def _readback(root: Path, ref: str, wanted: str) -> str:
    """Read the ref back after the push: a push that reports success can lie."""
    check = _git(root, "ls-remote", "origin", ref)
    if check.code != 0:
        return f"readback failed: {_words(check)}"
    if not check.stdout.strip():
        return f"push reported success but {ref} is absent from the remote"
    seen = check.stdout.split()[0]
    if seen != wanted:
        return (
            f"push reported success but {ref} reads {seen[:12]}, wanted {wanted[:12]}"
        )
    return ""


def drop(root: Path, ref: str) -> str:
    """Delete *ref* on origin; ``""`` when it is gone, the reason otherwise.

    A ref that is already absent counts as gone: the janitor re-runs
    as its own recovery, and a second sweep must find nothing to do.
    """
    if not ref.startswith(NAMESPACE):
        return f"refusing {ref}: the state store deletes only under {NAMESPACE}"
    deleted = _git(root, "push", "--quiet", "origin", f":{ref}")
    if deleted.code == 0:
        return ""
    words = deleted.stderr + deleted.stdout
    if "remote ref does not exist" in words:
        return ""
    return f"delete refused: {_words(deleted)}"


def _commit_time(root: Path, ref: str) -> datetime | None:
    """When *ref*'s commit was made, read after a fetch; ``None`` when unreadable."""
    fetched = _git(root, "fetch", "--quiet", "origin", ref)
    if fetched.code != 0:
        return None
    stamp = _git(root, "log", "-1", "--format=%ct", "FETCH_HEAD").stdout.strip()
    if not stamp.isdigit():
        return None
    return datetime.fromtimestamp(int(stamp), tz=UTC)


def sweep(
    root: Path,
    series: tuple[Series, ...] = (),
    *,
    older_than: timedelta = timedelta(hours=6),
    now: datetime | None = None,
) -> list[str]:
    """The janitor: drop orphaned per-run refs, enforce the windows; what it did.

    A per-run ref outlives its run only when the run was cancelled or
    died before its gate job read and deleted it, so any per-run ref
    older than *older_than* is an orphan: no run lasts that long, and
    the forge has no run-by-id lookup to ask. Each declared series is
    re-put empty under its window, which trims a ref that grew past it.
    Every line names what happened; a failure is a line, never a raise.
    """
    moment = now or datetime.now(UTC)
    lines: list[str] = []
    refs = list_refs(root, RUN_PREFIX)
    if refs is None:
        lines.append(f"  {RUN_PREFIX}*: the remote could not be listed; nothing swept")
    else:
        for ref in sorted(refs):
            made = _commit_time(root, ref)
            if made is None:
                lines.append(f"  {ref}: unreadable; kept")
                continue
            age = moment - made
            if age < older_than:
                lines.append(f"  {ref}: {_hours(age)} old; kept")
                continue
            why = drop(root, ref)
            lines.append(f"  {ref}: {_hours(age)} old; {why or 'dropped'}")
    for declared in series:
        if declared.window is None:
            continue
        current = read(root, declared.ref)
        if current.files is None or len(current.files) <= declared.window:
            lines.append(f"  {declared.ref}: within its window of {declared.window}")
            continue
        why = put(
            root,
            declared.ref,
            {},
            message=f"{declared.name}: window of {declared.window} enforced",
            window=declared.window,
            order=_by_when,
        )
        lines.append(
            f"  {declared.ref}: {len(current.files)} files trimmed to"
            f" {declared.window}" + (f"; {why}" if why else "")
        )
    return lines


def _hours(age: timedelta) -> str:
    return f"{age.total_seconds() / 3600:.1f}h"
