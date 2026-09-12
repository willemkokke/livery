"""The state store: JSON rows on git refs, on the remote and in the checkout.

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
that itself. A [livery.workshop._state.Keyed][] family declares one
series per key (a leg and a package; a run and a leg) and lists the
keys the remote holds. The bare `read` and `put` are the transport
underneath, and `sweep` is the janitor. Every failure is a printed
reason, never a boolean: a stamp is best-effort by contract, so the
only way its failure is ever noticed is by being printed.

A series declared ``local`` lives under ``refs/workshop-local/`` in
the checkout's own git directory instead of on the remote. No refspec
names that namespace, so a fetch never brings it and a push never
carries it, and a mirror push of ``refs/workshop/*`` cannot carry it
either; the checkout's worktrees share it, and a fresh clone starts
empty. Its rows are read and written the same way. Only the transport
differs: git's own ``update-ref`` with the old value, in place of a
push with a lease.

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
- The push's own report is its verdict: git prints which refs moved
  from the server's report-status, and the compare-and-swap is the
  guard. A readback through the same git configuration cannot catch
  what would mislead the push itself.
- The commit carries its own identity and ``[skip ci]``: the natural
  writer is a CI container with no global git config, and a push to
  this namespace must never start a workflow.

Plumbing, never porcelain: ``hash-object``, ``mktree``,
``commit-tree``, ``push``, ``ls-remote``, ``cat-file``. The working
tree and the index are never touched, so a write is safe from the
middle of any job. A read or a write is a fixed handful of git
processes whatever the series holds: one ``cat-file --batch`` reads
every blob of a tree, one ``hash-object --stdin-paths`` writes every
blob of a commit. The object names go NUL-separated (git 2.38 or
newer), the paths one per line, which git reads with or without the
carriage return a text-mode stdin appends on Windows; the tree is fed
NUL-separated too, so no file name ever crosses a text-mode stdin,
where that carriage return once named a file no ``show`` could find.
A blob is hashed from a temporary file written without newline
translation, so it reads the same bytes on every platform.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Callable, Generator, Iterable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import livery.footman as footman
from livery.toolroom import tools

#: Every state ref lives under this prefix. A push here matches no
#: push trigger on any forge, so a store write can never start a
#: workflow, whatever the token.
NAMESPACE = "refs/workshop/"

#: Every local series lives under this prefix, in the checkout's own
#: git directory: no refspec names it, so a fetch never brings it and
#: a push never carries it, and a mirror push of ``refs/workshop/*``
#: cannot carry it either. Worktrees share it; a fresh clone is empty.
LOCAL_NAMESPACE = "refs/workshop-local/"

#: The per-run refs' prefix, for the janitor's orphan rule: one ref
#: per leg, written by that leg alone, read and deleted by the run's
#: gate job, swept here when a run is cancelled before its gate could.
#: The family itself is declared where its rows mean something,
#: [livery.workshop._metrics.RUNS][].
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


def slug(name: str) -> str:
    """*name* as a ref-safe and file-safe token: every other character is a dash."""
    return re.sub(r"[^A-Za-z0-9_-]", "-", name)


@dataclass(frozen=True)
class Skipped:
    """A file a read passed over: its name and why.

    Attributes:
        name: The file's name on the ref.
        why: Why it is not a row, in the store's one wording.
    """

    name: str
    why: str

    def __str__(self) -> str:
        return f"{self.name}: {self.why}; skipped"


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
        skipped: One entry per file that is not such a row, naming the
            file and why; printing one gives the line.
        failed: Whether the transport could not answer for a ref that
            exists, or reach the remote at all. An absent ref reads as
            no rows and no failure.
        reason: Why it could not answer, in the store's one wording.
    """

    rows: tuple[Row, ...]
    skipped: tuple[Skipped, ...] = ()
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
        local: Whether the series lives in the checkout's git directory
            under ``refs/workshop-local/`` instead of on the remote:
            never fetched or pushed, shared by the worktrees, written
            by local runs, so ``ci_only`` must be false.
        age: How long a row stays before the janitor drops it, by the
            store's stamp; ``None`` keeps rows for the window alone.
    """

    name: str
    window: int | None = None
    ci_only: bool = True
    schema: int = 1
    local: bool = False
    age: timedelta | None = None

    def __post_init__(self) -> None:
        if self.local and self.ci_only:
            raise ValueError(
                f"{self.name}: a local series is written by local runs; declare"
                " it ci_only=False"
            )

    @property
    def ref(self) -> str:
        """The full ref name, under the local namespace for a local series."""
        return (LOCAL_NAMESPACE if self.local else NAMESPACE) + self.name

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
        skipped: list[Skipped] = []
        for name in sorted(found.files):
            data, why = _parse(self, found.files[name])
            if data is None:
                skipped.append(Skipped(name, why))
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
        self,
        root: Path,
        rows: Mapping[str, Mapping[str, Any]],
        *,
        message: str,
        remove: Iterable[str] = (),
    ) -> str:
        """Write *rows* by file name, stamped; ``""`` or the reason.

        Every row is written with the schema and the time stamped on
        it, and the stamp wins over a ``schema`` or ``when`` the row
        carries. The rows the series already holds stand, less the
        file names in *remove*, so a caller replacing a record in
        place names what goes and puts what changed.

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
            remove=remove,
        )

    def _unreadable(self, found: Read) -> str:
        if not found.failed:
            return ""
        return f"the {self.name} series could not be read: {found.reason}"


@dataclass(frozen=True)
class Keyed:
    """A family of series under one prefix, one ref per key.

    The coverage record keeps one series per base and leg, the
    per-run halves one per run and leg. A family declares its key
    parts once: `series` makes the series of one key, with every
    part made ref-safe, and `listed` reads the keys the remote
    holds, so no caller spells a ref of the family itself.

    Attributes:
        name: The family's name under the namespace (``coverage``).
        keys: The names of the key's parts, in ref order
            (``("leg", "package")``).
        window: Every series' window; ``None`` keeps everything.
        ci_only: Whether only a CI run may write the family.
        schema: The rows' schema, shared by every series.
        local: Whether every series of the family is local, as for a
            [livery.workshop._state.Series][].
        stale_after: How long a key's ref may live, by its commit
            time, before the janitor drops it as an orphan; ``None``
            never drops by age.
        current: The keys the world still produces, from the checkout
            root, or ``None`` when it cannot tell; a listed key outside
            them is an orphan the janitor drops. ``None`` drops none.
    """

    name: str
    keys: tuple[str, ...]
    window: int | None = None
    ci_only: bool = True
    schema: int = 1
    local: bool = False
    stale_after: timedelta | None = None
    current: Callable[[Path], set[tuple[str, ...]] | None] | None = None

    def __post_init__(self) -> None:
        if self.local and self.ci_only:
            raise ValueError(
                f"{self.name}: a local family is written by local runs; declare"
                " it ci_only=False"
            )

    @property
    def prefix(self) -> str:
        """The refs' common prefix, ending in a slash."""
        return f"{LOCAL_NAMESPACE if self.local else NAMESPACE}{self.name}/"

    def series(self, *key: str) -> Series:
        """The series of one *key*, one part per declared key name.

        Raises:
            ValueError: When *key* has another number of parts.
        """
        return self.at(*(slug(part) for part in key))

    def at(self, *key: str) -> Series:
        """The series of a *key* as its ref spells it, every part verbatim.

        `listed` returns keys as their refs spell them, and a key
        written before the family made its parts ref-safe may spell a
        part another way; `series` would re-spell it and name a ref
        that does not exist. A listed key comes back through here.

        Raises:
            ValueError: When *key* has another number of parts.
        """
        if len(key) != len(self.keys):
            raise ValueError(
                f"{self.name} is keyed by {', '.join(self.keys)}; got"
                f" {len(key)} part(s)"
            )
        return Series(
            "/".join((self.name, *key)),
            window=self.window,
            ci_only=self.ci_only,
            schema=self.schema,
            local=self.local,
        )

    def listed(self, root: Path, *head: str) -> list[tuple[str, ...]] | None:
        """The keys the remote holds under the leading parts *head*.

        ``None`` when the remote cannot be listed. A ref under the
        prefix with another number of parts is not the family's and
        is left out.
        """
        prefix = self.prefix + "".join(slug(part) + "/" for part in head)
        refs = list_refs(root, prefix)
        if refs is None:
            return None
        keys = [tuple(name[len(self.prefix) :].split("/")) for name in refs]
        return sorted(key for key in keys if len(key) == len(self.keys))


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
        head_ref: The branch a pull request comes from, as the event
            payload names it (GitLab: the merge request's source), the
            base of the branch's own coverage record; empty on a push
            or when the runner did not say.
    """

    forge: str
    run_id: str
    event: str
    ref: str
    head_sha: str = ""
    base_ref: str = ""
    leg: str = ""
    head_ref: str = ""


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


def _event_head_ref(env: Mapping[str, str]) -> str:
    """The pull request's source branch: the payload's, else the runner's variable."""
    payload = event_payload(env)
    if payload is not None:
        head = (payload.get("pull_request") or {}).get("head") or {}
        if isinstance(head, dict) and head.get("ref"):
            return str(head["ref"])
    return env.get("GITHUB_HEAD_REF", "")


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
            _event_head_ref(env),
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
            env.get("CI_MERGE_REQUEST_SOURCE_BRANCH_NAME", ""),
        )
    return None


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


@dataclass
class _Snapshot:
    """One listing of the remote namespace, and the commits known to be local.

    Attributes:
        refs: Every ref under the namespace by sha, or ``None`` when
            the listing could not be taken.
        reason: The transport's words when it could not be.
        local: The shas the checkout is known to hold, checked or
            fetched inside this snapshot.
    """

    refs: dict[str, str] | None
    reason: str = ""
    local: set[str] = field(default_factory=set)
    published: Path | None = None


#: The *fetch* of a `remote_snapshot` that wants every ref of the
#: namespace: the empty prefix below it.
WHOLE: tuple[str, ...] = ("",)

#: The snapshot each checkout reads the remote namespace through while
#: a `remote_snapshot` block is open for it; one per checkout at a time.
_SNAPSHOTS: dict[Path, _Snapshot] = {}

#: The variable a published snapshot's file is named in: the job
#: runner takes one listing for the whole job and every entry it
#: spawns reads through it instead of listing again.
SNAPSHOT_VARIABLE = "WORKSHOP_SNAPSHOT"

#: The ref namespaces one listing carries: the store's own, and the
#: branches the coverage families' current keys are told by.
LISTED = (NAMESPACE, "refs/heads/")


def _snapshot(root: Path) -> _Snapshot | None:
    return _SNAPSHOTS.get(root.resolve())


def _list_namespace(root: Path) -> tuple[dict[str, str] | None, str]:
    """Every ref under the listed namespaces by sha, or ``(None, reason)``.

    One call lists the store's refs and the branches together, so a
    family's current keys cost no listing of their own.
    """
    listed = _git(root, "ls-remote", "origin", *(space + "*" for space in LISTED))
    if listed.code != 0:
        return None, _words(listed)
    refs: dict[str, str] = {}
    for line in listed.stdout.splitlines():
        sha, _, name = line.partition("\t")
        if name:
            refs[name.strip()] = sha.strip()
    return refs, ""


def _missing_locally(root: Path, shas: Iterable[str]) -> set[str]:
    """The shas among *shas* the checkout's object store does not hold."""
    wanted = sorted(set(shas))
    if not wanted:
        return set()
    checked = _git(
        root,
        "cat-file",
        "--batch-check",
        "-z",
        stdin="".join(f"{sha}\0" for sha in wanted),
    )
    if checked.code != 0:
        return set(wanted)
    missing: set[str] = set()
    for line in checked.stdout.splitlines():
        words = line.split()
        if len(words) >= 2 and words[1] == "missing":
            missing.add(words[0])
    return missing


@contextmanager
def remote_snapshot(
    root: Path, *, fetch: Iterable[str] = (), publish: bool = False
) -> Generator[str | None]:
    """Read the remote namespace through one listing for the block.

    One ``ls-remote`` of the store's namespace and the branches when
    the block opens; every read and listing of a remote ref inside
    answers from it, and a ref whose commit the checkout already
    holds is read with no network at all. The refs whose names start
    with a prefix in *fetch* (spelled below the namespace, ``metrics``
    or ``run/1400/``) and whose commits the checkout lacks are fetched
    together, one round trip, when the block opens; a ref outside
    them is fetched on its own read. A ref another writer moved after
    the listing is listed again and read at its current commit, so a
    concurrent writer never turns a read into a failure. A write
    inside keeps its compare-and-swap on the listed sha: a refused
    push lists again and retries, and a successful write records its
    new sha, so a read after it sees it. A listing the forge could not
    answer makes
    every read inside a failure naming the reason and every listing
    unlistable, never an absence. A block opened inside another for
    the same checkout shares the outer listing.

    With *publish* the listing is written to a file whose path the
    block yields, for the caller to hand its child processes under
    `SNAPSHOT_VARIABLE`: a block a child opens reads that file
    instead of listing, and the child's writes record their shas in
    it for the children after it. A listing the parent could not
    take is not published and the block yields ``None``, so a child
    lists for itself; a file a child cannot read, or one taken for
    another checkout, counts as none. Without *publish* the block
    yields ``None``.
    """
    key = root.resolve()
    if key in _SNAPSHOTS:
        yield None
        return
    snapshot = _published_snapshot(key)
    if snapshot is None:
        refs, why = _list_namespace(root)
        snapshot = _Snapshot(refs, why)
    refs = snapshot.refs
    if refs is not None:
        prefixes = tuple(NAMESPACE + prefix for prefix in fetch)
        wanted = {ref: sha for ref, sha in refs.items() if ref.startswith(prefixes)}
        _bring(root, snapshot, wanted)  # a ref it cannot bring fails at its read
    published_here = ""
    if publish and refs is not None and snapshot.published is None:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", suffix=".json", delete=False
        ) as handle:
            snapshot.published = Path(handle.name)
        _write_published(key, snapshot)
        published_here = str(snapshot.published)
    _SNAPSHOTS[key] = snapshot
    try:
        yield published_here or None
    finally:
        _SNAPSHOTS.pop(key, None)
        if published_here:
            Path(published_here).unlink(missing_ok=True)


def _published_snapshot(key: Path) -> _Snapshot | None:
    """The snapshot a parent published for *key*, or ``None`` when there is none."""
    named = os.environ.get(SNAPSHOT_VARIABLE, "")
    if not named:
        return None
    try:
        loaded = json.loads(Path(named).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(loaded, dict) or loaded.get("root") != str(key):
        return None
    refs = loaded.get("refs")
    local = loaded.get("local")
    if not isinstance(refs, dict) or not isinstance(local, list):
        return None
    return _Snapshot(
        {str(name): str(sha) for name, sha in refs.items()},
        "",
        {str(sha) for sha in local},
        Path(named),
    )


def _write_published(key: Path, snapshot: _Snapshot) -> None:
    """Write *snapshot* to its published file, for the entries after this one."""
    if snapshot.published is None or snapshot.refs is None:
        return
    body = json.dumps(
        {"root": str(key), "refs": snapshot.refs, "local": sorted(snapshot.local)}
    )
    try:
        snapshot.published.write_text(body, encoding="utf-8")
    except OSError:
        snapshot.published = None


def _relist(root: Path, snapshot: _Snapshot) -> None:
    """List the namespace again after another writer moved a ref."""
    snapshot.refs, snapshot.reason = _list_namespace(root)
    _write_published(root.resolve(), snapshot)


def _bring(root: Path, snapshot: _Snapshot, wanted: dict[str, str]) -> dict[str, str]:
    """Make the listed commits of *wanted* (ref to sha) local; the failures by ref.

    A fetch names the ref and brings whatever the remote holds under it
    now. A ref another writer moved after the listing brings its new
    commit, not the listed one, so the listed sha is checked after the
    fetch and never assumed: the moved refs are listed again, the
    block's view takes their current commits, and one more round
    brings those. A ref moved again inside that round is reported;
    a ref gone from the new listing was dropped, and is left out.
    """
    failed: dict[str, str] = {}
    refs = dict(wanted)
    for _round in range(2):
        missing = _missing_locally(root, refs.values())
        snapshot.local.update(sha for sha in refs.values() if sha not in missing)
        to_fetch = sorted(ref for ref, sha in refs.items() if sha in missing)
        if not to_fetch:
            return failed
        fetched = _git(root, "fetch", "--quiet", "origin", *to_fetch)
        if fetched.code != 0:
            return {ref: _words(fetched) for ref in to_fetch}
        still = _missing_locally(root, [refs[ref] for ref in to_fetch])
        snapshot.local.update(refs[ref] for ref in to_fetch if refs[ref] not in still)
        moved = [ref for ref in to_fetch if refs[ref] in still]
        if not moved:
            return failed
        _relist(root, snapshot)
        if snapshot.refs is None:
            reason = f"the remote could not be listed: {snapshot.reason}"
            return dict.fromkeys(moved, reason)
        refs = {ref: snapshot.refs[ref] for ref in moved if ref in snapshot.refs}
    missing = _missing_locally(root, refs.values())
    return {
        ref: "another writer kept moving it"
        for ref, sha in refs.items()
        if sha in missing
    }


def _reachable(
    root: Path, snapshot: _Snapshot, ref: str, sha: str
) -> tuple[str | None, str]:
    """The commit to read for *ref* inside *snapshot*, made local; or the reason.

    The listed *sha* when the checkout holds it or the fetch brings it;
    the ref's current commit when the remote moved past the listing;
    ``None`` with no reason when the ref is gone from the remote.
    """
    if sha in snapshot.local:
        return sha, ""
    failed = _bring(root, snapshot, {ref: sha})
    if ref in failed:
        return None, failed[ref]
    if snapshot.refs is None:
        return None, f"the remote could not be listed: {snapshot.reason}"
    return snapshot.refs.get(ref), ""


def _git(root: Path, *args: str, stdin: str | None = None) -> tools.Result:
    tool = tools.git.opts(cwd=root, nofail=True, recorded=False)
    if stdin is not None:
        tool = tool.opts(input=stdin)
    return tool(*args)


def _words(result: tools.Result) -> str:
    return (result.stderr or result.stdout).strip()[:200] or "no output"


def _ref_exists(root: Path, ref: str) -> tuple[bool | None, str]:
    """Whether *ref* is on the remote; ``None`` when the probe cannot say."""
    probe = _git(root, "ls-remote", "origin", ref)
    if probe.code != 0:
        return None, _words(probe)
    return bool(probe.stdout.strip()), ""


def read(root: Path, ref: str) -> Read:
    """Read *ref*'s files, saying whether a miss is absence or failure.

    A remote ref is fetched from origin: the fetch lands in
    ``FETCH_HEAD`` and creates no local ref, so a checkout accrues
    only unreachable objects git's own gc prunes. A failed fetch is a
    failure only when the ref is there: a ref that does not exist
    also fails the fetch, and that is the ordinary first-run case. A
    remote that cannot answer the probe either counts as failed: an
    unreachable forge must never read as "there was never a stamp".
    A local ref is read from the checkout's own git directory: a
    missing ref is absence, and anything else git refuses is a
    failure with git's words.
    """
    if ref.startswith(LOCAL_NAMESPACE):
        verified = _git(root, "rev-parse", "--verify", "--quiet", ref)
        if verified.code == 1 and not verified.stderr.strip():
            return Read(None, None, failed=False)
        if verified.code != 0:
            return Read(None, None, failed=True, reason=_words(verified))
        return _tree_files(root, verified.stdout.strip())
    snapshot = _snapshot(root)
    if snapshot is not None:
        if snapshot.refs is None:
            return Read(
                None,
                None,
                failed=True,
                reason=f"the remote could not be listed: {snapshot.reason}",
            )
        listed = snapshot.refs.get(ref)
        if listed is None:
            return Read(None, None, failed=False)
        sha, why = _reachable(root, snapshot, ref, listed)
        if why:
            return Read(None, listed, failed=True, reason=why)
        if sha is None:
            return Read(None, None, failed=False)
        return _tree_files(root, sha)
    fetched = _git(root, "fetch", "--quiet", "origin", ref)
    if fetched.code != 0:
        exists, why = _ref_exists(root, ref)
        if exists is False:
            return Read(None, None, failed=False)
        return Read(None, None, failed=True, reason=why or _words(fetched))
    return _tree_files(root, _git(root, "rev-parse", "FETCH_HEAD").stdout.strip())


def _tree_files(root: Path, sha: str) -> Read:
    """The files of the root commit *sha*, or a failure with git's words.

    Two processes whatever the tree holds: one ``ls-tree`` for the
    names, one ``cat-file --batch`` for every blob, read back by the
    byte sizes git prints before each. An object git does not have or
    a stream that ends early is a failure naming the file: a partial
    tree must never read as the series. A blob whose bytes do not
    match its size after the pipe's newline translation (a row a
    Windows checkout wrote with carriage returns, before blobs were
    written without translation) is read on its own with ``show``,
    the translation both agree on.
    """
    listed = _git(root, "ls-tree", "--name-only", sha)
    if listed.code != 0:
        return Read(None, sha, failed=True, reason=_words(listed))
    names = listed.stdout.split()
    if not names:
        return Read({}, sha, failed=False)
    batch = _git(
        root,
        "cat-file",
        "--batch",
        "-z",
        stdin="".join(f"{sha}:{name}\0" for name in names),
    )
    if batch.code != 0:
        return Read(None, sha, failed=True, reason=_words(batch))
    files, mismatched, why = _split_batch(batch.stdout, names)
    if why:
        return Read(None, sha, failed=True, reason=why)
    for name in mismatched:
        shown = _git(root, "show", f"{sha}:{name}")
        if shown.code != 0:
            return Read(None, sha, failed=True, reason=_words(shown))
        files[name] = shown.stdout
    return Read(files, sha, failed=False)


def _split_batch(
    output: str, names: list[str]
) -> tuple[dict[str, str], list[str], str]:
    """The blobs of a ``cat-file --batch`` stream, by *names* in order.

    Returns the files, the names whose bytes did not match their
    size, and the reason when the stream is not a whole answer: an
    object git does not have, or a stream that ends before a header.
    """
    data = output.encode("utf-8")
    files: dict[str, str] = {}
    mismatched: list[str] = []
    at = 0
    for name in names:
        end = data.find(b"\n", at)
        if end < 0:
            return {}, [], f"{name}: the batch stream ended before its header"
        header = data[at:end].decode("utf-8", "replace").split()
        at = end + 1
        if len(header) < 3 or header[1] != "blob" or not header[2].isdigit():
            said = " ".join(header[1:]) or "nothing"
            return {}, [], f"{name}: git said {said}"
        size = int(header[2])
        body = data[at : at + size]
        if len(body) == size and data[at + size : at + size + 1] == b"\n":
            files[name] = body.decode("utf-8")
            at += size + 1
            continue
        # The pipe translated a carriage return away: the sizes no
        # longer index the stream, so the rest is read one by one.
        mismatched.extend(names[len(files) + len(mismatched) :])
        break
    return files, mismatched, ""


def list_refs(root: Path, prefix: str) -> dict[str, str] | None:
    """The refs under *prefix* by sha, remote or local; ``None`` when unlistable."""
    if prefix.startswith(LOCAL_NAMESPACE):
        listed = _git(
            root, "for-each-ref", "--format=%(objectname)%09%(refname)", prefix
        )
    else:
        snapshot = _snapshot(root)
        if snapshot is not None and prefix.startswith(LISTED):
            if snapshot.refs is None:
                return None
            return {
                name: sha
                for name, sha in snapshot.refs.items()
                if name.startswith(prefix)
            }
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
    names = sorted(files)
    paths: list[str] = []
    try:
        for name in names:
            # A temporary file, never stdin, and written without newline
            # translation: text-mode stdin translates newlines on
            # Windows, and a translated file would hash to a different
            # blob per platform.
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", newline="", delete=False
            ) as handle:
                handle.write(files[name])
                paths.append(handle.name)
        # One path per line: git strips the carriage return a text-mode
        # stdin appends on Windows, and a temporary file's path holds
        # no newline of its own.
        hashed = (
            _git(
                root,
                "hash-object",
                "-w",
                "--stdin-paths",
                stdin="".join(f"{path}\n" for path in paths),
            )
            if paths
            else None
        )
    finally:
        for path in paths:
            os.unlink(path)
    if hashed is not None:
        if hashed.code != 0:
            return "", f"hash-object failed: {_words(hashed)}"
        shas = hashed.stdout.split()
        if len(shas) != len(paths):
            return "", (
                f"hash-object returned {len(shas)} hash(es) for {len(paths)} file(s)"
            )
        entries.extend(
            f"100644 blob {sha}\t{name}" for name, sha in zip(names, shas, strict=True)
        )
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


def _stale(refused: tools.Result) -> bool:
    """Whether a refused write lost its compare-and-swap rather than failing outright.

    A push with a lease says ``stale info`` or ``[rejected]``; an
    ``update-ref`` with an old value says what the ref is at ``but
    expected``, or that the reference ``already exists``.
    """
    words = refused.stderr + refused.stdout
    return any(
        mark in words
        for mark in ("stale info", "[rejected]", "but expected", "already exists")
    )


def put(
    root: Path,
    ref: str,
    files: dict[str, str],
    *,
    message: str,
    window: int | None = None,
    ci_only: bool = False,
    order: Order | None = None,
    remove: Iterable[str] = (),
    attempts: int = ATTEMPTS,
) -> str:
    """Write *files* onto *ref*, keeping what it already holds; ``""`` or the reason.

    The tree is the ref's current files, less *remove*, with *files*
    laid over them, trimmed to the newest *window* files when one is
    given, ranked by *order* or, without one, by name. The push
    is a compare-and-swap on the sha the read returned: a ref another
    writer moved in between refuses the push, and the write re-reads,
    merges, and retries up to *attempts* times. *ci_only* refuses a
    write from outside CI, naming the rule. Every refusal is the
    returned reason; nothing here raises or prints.
    """
    if not ref.startswith((NAMESPACE, LOCAL_NAMESPACE)):
        return (
            f"refusing {ref}: the state store writes only under {NAMESPACE}"
            f" and {LOCAL_NAMESPACE}"
        )
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
        gone = set(remove)
        merged = {
            name: text
            for name, text in (current.files or {}).items()
            if name not in gone
        } | files
        if window is not None and len(merged) > window:
            merged = _trim(merged, window, order)
        commit, why = _build_commit(root, merged, message)
        if why:
            return why
        if ref.startswith(LOCAL_NAMESPACE):
            # git's own compare-and-swap: the old value is the sha the
            # read returned, or empty for a ref that must not exist yet.
            moved = _git(root, "update-ref", ref, commit, current.sha or "")
            if moved.code != 0:
                if _stale(moved):
                    continue
                return f"update refused: {_words(moved)}"
            return ""
        lease = f"--force-with-lease={ref}:{current.sha or ''}"
        pushed = _git(root, "push", "--quiet", lease, "origin", f"{commit}:{ref}")
        snapshot = _snapshot(root)
        if pushed.code != 0:
            if _stale(pushed):
                if snapshot is not None:
                    _relist(root, snapshot)
                continue
            return f"push refused: {_words(pushed)}"
        if snapshot is not None and snapshot.refs is not None:
            snapshot.refs[ref] = commit
            snapshot.local.add(commit)
            _write_published(root.resolve(), snapshot)
        return ""
    return f"gave up on {ref} after {attempts} attempts: another writer kept moving it"


def _trim(files: dict[str, str], window: int, order: Order | None) -> dict[str, str]:
    """The newest *window* of *files* by *order*, or by name without one."""
    if order is None:
        kept = sorted(files)[-window:]
    else:
        ranked = sorted((order(name, text), name) for name, text in files.items())
        kept = [name for _, name in ranked[-window:]]
    return {name: files[name] for name in kept}


def drop(root: Path, ref: str) -> str:
    """Delete *ref*, on origin or in the checkout; ``""`` when gone, else the reason.

    A ref that is already absent counts as gone: the janitor re-runs
    as its own recovery, and a second sweep must find nothing to do.
    """
    if not ref.startswith((NAMESPACE, LOCAL_NAMESPACE)):
        return (
            f"refusing {ref}: the state store deletes only under {NAMESPACE}"
            f" and {LOCAL_NAMESPACE}"
        )
    if ref.startswith(LOCAL_NAMESPACE):
        deleted = _git(root, "update-ref", "-d", ref)
        return "" if deleted.code == 0 else f"delete refused: {_words(deleted)}"
    deleted = _git(root, "push", "--quiet", "origin", f":{ref}")
    words = deleted.stderr + deleted.stdout
    if deleted.code != 0 and "remote ref does not exist" not in words:
        return f"delete refused: {_words(deleted)}"
    snapshot = _snapshot(root)
    if snapshot is not None and snapshot.refs is not None:
        snapshot.refs.pop(ref, None)
        _write_published(root.resolve(), snapshot)
    return ""


def _commit_time(root: Path, ref: str) -> datetime | None:
    """When *ref*'s commit was made, remote refs fetched; ``None`` when unreadable."""
    at = ref
    if not ref.startswith(LOCAL_NAMESPACE):
        snapshot = _snapshot(root)
        if snapshot is not None:
            if snapshot.refs is None or ref not in snapshot.refs:
                return None
            sha, why = _reachable(root, snapshot, ref, snapshot.refs[ref])
            if why or sha is None:
                return None
            at = sha
        else:
            fetched = _git(root, "fetch", "--quiet", "origin", ref)
            if fetched.code != 0:
                return None
            at = "FETCH_HEAD"
    stamp = _git(root, "log", "-1", "--format=%ct", at).stdout.strip()
    if not stamp.isdigit():
        return None
    return datetime.fromtimestamp(int(stamp), tz=UTC)


def sweep(
    root: Path,
    declared: tuple[Series | Keyed, ...],
    *,
    remote: bool,
    dry_run: bool = False,
    now: datetime | None = None,
) -> list[str]:
    """The store's janitor: every series bounded, every orphan dropped; the lines.

    The scope is the run's: the local series always, the remote ones
    only under *remote*, which a caller sets inside CI, since a local
    run never writes the remote store. A series is trimmed to its
    window, and the rows older than the age it declares go. A
    family's keys are listed and each key's series is bounded the
    same way, after its orphans go: a key whose ref is older than the
    family's ``stale_after``, or one the family's ``current`` keys no
    longer include. A family that cannot be listed, or whose current
    keys cannot be told, drops nothing and says so. Under *dry_run*
    every line says what would go and nothing is written. Every line
    names what happened; a failure is a line, never a raise, and a
    second sweep finds nothing to do.
    """
    moment = now or datetime.now(UTC)
    lines: list[str] = []
    skipped = False
    for item in declared:
        if not item.local and not remote:
            skipped = True
        elif isinstance(item, Series):
            lines += _sweep_series(root, item, dry_run=dry_run, now=moment)
        else:
            lines += _sweep_family(root, item, dry_run=dry_run, now=moment)
    if skipped:
        lines.append("  remote series: swept inside CI, never from a machine")
    return lines


def _sweep_series(
    root: Path, series: Series, *, dry_run: bool, now: datetime
) -> list[str]:
    """Trim *series* to its window and drop its aged rows; the lines."""
    found = series.rows(root)
    if found.failed:
        return [f"  {series.ref}: {found.reason}; nothing swept"]
    aged = [row.name for row in found.rows if _older(row.when, series.age, now)]
    held = len(found.rows) + len(found.skipped) - len(aged)
    over = max(0, held - series.window) if series.window is not None else 0
    if not aged and not over:
        return [f"  {series.ref}: {len(found.rows)} row(s), within its bounds"]
    verb = "would drop" if dry_run else "dropped"
    lines: list[str] = []
    if aged and series.age is not None:
        lines.append(
            f"  {series.ref}: {verb} {len(aged)} row(s) older than {_span(series.age)}"
        )
    if over:
        lines.append(
            f"  {series.ref}: {verb} {over} file(s) beyond its window"
            f" of {series.window}"
        )
    if dry_run:
        return lines
    why = put(
        root,
        series.ref,
        {},
        message=f"{series.name}: swept",
        window=series.window,
        ci_only=not series.local,
        order=_by_when,
        remove=aged,
    )
    if why:
        lines.append(f"  {series.ref}: {why}")
    return lines


def _sweep_family(
    root: Path, family: Keyed, *, dry_run: bool, now: datetime
) -> list[str]:
    """Drop *family*'s orphans and bound each key's series; the lines."""
    keys = family.listed(root)
    if keys is None:
        return [f"  {family.prefix}*: could not be listed; nothing swept"]
    lines: list[str] = []
    current = family.current(root) if family.current is not None else None
    if family.current is not None and current is None:
        lines.append(
            f"  {family.prefix}*: the current keys could not be told; no orphan dropped"
        )
    verb = "would drop" if dry_run else "dropped"
    for key in keys:
        series = family.at(*key)
        reason = ""
        if family.stale_after is not None:
            made = _commit_time(root, series.ref)
            if made is None:
                lines.append(f"  {series.ref}: unreadable; kept")
                continue
            age = now - made
            if age > family.stale_after:
                reason = f"{_hours(age)} old, past {_span(family.stale_after)}"
        if not reason and current is not None and key not in current:
            reason = f"no current {' and '.join(family.keys)} produces it"
        if reason:
            why = "" if dry_run else drop(root, series.ref)
            lines.append(f"  {series.ref}: {reason}; {why or verb}")
            continue
        lines += _sweep_series(root, series, dry_run=dry_run, now=now)
    return lines


def _older(when: str, age: timedelta | None, now: datetime) -> bool:
    """Whether a row stamped *when* is older than *age*; an unstamped row never is."""
    if age is None or not when:
        return False
    try:
        moment = datetime.fromisoformat(when)
    except ValueError:
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return now - moment > age


def _span(age: timedelta) -> str:
    """*age* in days when it is a day or more, in hours otherwise."""
    days = age.total_seconds() / 86400
    return f"{days:.0f} day(s)" if days >= 1 else _hours(age)


def _hours(age: timedelta) -> str:
    return f"{age.total_seconds() / 3600:.1f}h"
