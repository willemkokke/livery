"""The CI state store: text files on ``refs/workshop/*`` refs.

State that CI runs keep across runs (timing rows, verified trees,
coverage marks) lives as files on git refs in a namespace outside
``refs/heads``, so a default clone or fetch never downloads it. Each
ref is one root commit whose tree holds the series' files; a write
reads the current tree, rebuilds it with the kept entries plus the
new ones, and pushes a fresh root commit over the old one, so a
window needs no history and a reader never sees a torn tree.

Reach for [livery.workshop._state.put][] to write, `read` to read,
and `sweep` for the janitor. Every failure is a printed reason,
never a boolean: a stamp is best-effort by contract, so the only way
its failure is ever noticed is by being printed.

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

import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

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


@dataclass(frozen=True)
class Series:
    """A shared ref's declaration: its window and who may write it.

    Attributes:
        name: The ref's name under the namespace.
        window: The newest files kept, by name, after every write;
            ``None`` keeps everything.
        ci_only: Whether only a CI run may write it. Local runs read.
    """

    name: str
    window: int | None = None
    ci_only: bool = True

    @property
    def ref(self) -> str:
        """The full ref name."""
        return NAMESPACE + self.name


@dataclass(frozen=True)
class RunContext:
    """The CI run this process belongs to, as the runner's environment names it.

    Attributes:
        forge: ``github``, ``gitea``, or ``gitlab``.
        run_id: The forge's identifier of the run (GitLab: the pipeline).
        event: What started the run (``push``, ``pull_request``, ...).
        ref: The ref the run is for, as the runner spells it.
    """

    forge: str
    run_id: str
    event: str
    ref: str


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
        )
    if env.get("GITLAB_CI") == "true":
        return RunContext(
            "gitlab",
            env.get("CI_PIPELINE_ID", ""),
            env.get("CI_PIPELINE_SOURCE", ""),
            env.get("CI_COMMIT_REF_NAME", ""),
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
    attempts: int = ATTEMPTS,
) -> str:
    """Write *files* onto *ref*, keeping what it already holds; ``""`` or the reason.

    The tree is the ref's current files with *files* laid over them,
    trimmed to the newest *window* names when one is given. The push
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
            merged = {name: merged[name] for name in sorted(merged)[-window:]}
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
        )
        lines.append(
            f"  {declared.ref}: {len(current.files)} files trimmed to"
            f" {declared.window}" + (f"; {why}" if why else "")
        )
    return lines


def _hours(age: timedelta) -> str:
    return f"{age.total_seconds() / 3600:.1f}h"
