"""The state store: JSON rows on git refs, on the remote and in the checkout.

A [livery.git.StateStore][] is the checkout and the policy a caller
built it with: who may write, under which identity, and which
variable a published listing travels in. Every function below takes
one; nothing is held between calls, and a snapshot block is the one
scoped state, open for its block alone.

State that CI runs keep across runs (timing rows, verified trees,
coverage marks) lives as files on git refs in a namespace outside
``refs/heads``, so a default clone or fetch never downloads it. Each
ref is one root commit whose tree holds the series' files; a write
reads the current tree, rebuilds it with the kept entries plus the
new ones, and pushes a fresh root commit over the old one, so a
window needs no history and a reader never sees a torn tree.

A [livery.git.Series][] declares one ref's rows: reach
for its `rows` and `row` to read and its `put` to write. The store
parses, stamps the schema and the time, applies the window, and
refuses what the declaration forbids, so no caller does any of
that itself. A [livery.git.Keyed][] family declares one
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
differs: the ref moves by an in-process compare-and-swap on the old
value, in place of a push with a lease. Every worktree writes the same
refs, so a write that finds a ref's lock file held waits a bounded
time for it, and a lock that does not clear is the returned reason,
naming the lock file.

The gate reads the remote namespace without the network. `fetch_store`
mirrors the row series its caller names, and only them, under
``refs/workshop-origin/``, one ref per series and never a keyed
family, and stamps the fetch and the names on the local `FETCHED`
series; `fetched_snapshot` then serves every read of a named series
from that mirror, fails a read or a listing of any other remote ref
naming the ref and the mirror's scope, refuses every write of a
remote series, and, in a checkout the store was never fetched into,
fails every read naming the sync, never as an absence. `fm sync` and
`fm start` fetch; `fm check` reads.

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

Only the wire goes through a git process: ``fetch``, ``push`` and
``ls-remote``. Every tree is read, and every root commit written, in
the checkout's own object store without a process, as bytes, so no
newline translation reaches a row or a file name on Windows. The
working tree and the index are never touched, so a write is safe
from the middle of any job, and a read or a write costs a fixed
handful of processes whatever the series holds: a remote read one
fetch, a remote write one fetch and one push, a local read or write
none.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Callable, Generator, Iterable, Mapping, Sequence
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

import livery.toolroom.tools as tools
from livery.git import _objects

#: Every state ref lives under this prefix. A push here matches no
#: push trigger on any forge, so a store write can never start a
#: workflow, whatever the token.
NAMESPACE = "refs/workshop/"

#: Every local series lives under this prefix, in the checkout's own
#: git directory: no refspec names it, so a fetch never brings it and
#: a push never carries it, and a mirror push of ``refs/workshop/*``
#: cannot carry it either. Worktrees share it; a fresh clone is empty.
LOCAL_NAMESPACE = "refs/workshop-local/"

#: The traces' own namespace: pushed to origin like a shared series
#: and mirrored by nothing, because every refspec `fetch_store` names
#: is a row series under `NAMESPACE`. So a sync brings no traces, a
#: gate reads none, and a checkout pays for them only when someone
#: asks for one. A push here starts no workflow, for the same reason
#: a push under `NAMESPACE` does not: it is neither a branch nor a tag.
TRACE_NAMESPACE = "refs/workshop-trace/"

#: Origin's store as this checkout last fetched it: the row series
#: `fetch_store` was told to mirror, which `fm sync` and `fm start`
#: name, each at ``refs/workshop-origin/<name>``, and nothing else. A
#: `fetched_snapshot` block reads those series from this mirror alone,
#: so the gate never reaches origin, and fails a read of any other
#: remote ref naming the mirror's scope.
FETCHED_NAMESPACE = "refs/workshop-origin/"

#: The per-run refs' prefix, for the janitor's orphan rule: one ref
#: per leg, written by that leg alone, read and deleted by the run's
#: gate job, swept here when a run is cancelled before its gate could.
#: The family itself is declared where its rows mean something,
#: the metrics' run family.
RUN_PREFIX = NAMESPACE + "run/"

#: How many times a compare-and-swap write re-reads and retries after
#: another writer moved the ref.
ATTEMPTS = 3

#: How a window ranks the files it keeps: a key from a file's name
#: and text, the newest sorting last.
Order = Callable[[str, str], tuple[str, str]]


def slug(name: str) -> str:
    """*name* as a ref-safe and file-safe token: every other character is a dash."""
    return re.sub(r"[^A-Za-z0-9_-]", "-", name)


@dataclass(frozen=True)
class StateStore:
    """A checkout's state store and the policy its builder gave it.

    Attributes:
        root: The checkout whose origin holds the remote namespace and
            whose git directory holds the local one.
        in_ci: Whether this process is a CI run: a put to a series
            declared ``ci_only`` refuses otherwise.
        identity: The name and address every commit the store makes
            carries, author and committer.
        snapshot_variable: The environment variable a published
            listing's path travels in, from a parent process to its
            children.
        prog: The runner's command name, for the words a refusal
            gives a person (``run `fm sync```).
    """

    root: Path
    in_ci: bool
    identity: tuple[str, str]
    snapshot_variable: str
    prog: str = "fm"


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
        unread: The files a read that named the ones it wants passed
            over, by name, never read.
        blobs: The blobs the read was asked for, by name, each the
            text its writer wrote ([livery.git.Series][]'s ``blobs``).
    """

    rows: tuple[Row, ...]
    skipped: tuple[Skipped, ...] = ()
    failed: bool = False
    reason: str = ""
    unread: tuple[str, ...] = ()
    blobs: Mapping[str, str] = field(default_factory=dict)


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
        namespace: The prefix the ref lives under, when it is neither
            the shared namespace nor the local one:
            [livery.git.TRACE_NAMESPACE][] is the one
            such prefix, pushed to origin and mirrored by nothing.
            Empty means the class ``local`` chooses.
        blobs: The directory, ending in a slash, of the files the
            series keeps beside its rows as their writer wrote them,
            such as a file named by its own content (``facts/``). A
            read hands them back as text and never parses one as a
            row, a write puts them unstamped, and the janitor counts
            the rows alone. Empty for none. A window ranks files by
            their stamps, so a series with blobs declares none.
    """

    name: str
    window: int | None = None
    ci_only: bool = True
    schema: int = 1
    local: bool = False
    age: timedelta | None = None
    namespace: str = ""
    blobs: str = ""

    def __post_init__(self) -> None:
        if self.local and self.ci_only:
            raise ValueError(
                f"{self.name}: a local series is written by local runs; declare"
                " it ci_only=False"
            )
        if self.namespace and self.local:
            raise ValueError(
                f"{self.name}: a series in {self.namespace} is not local; declare"
                " one or the other"
            )
        if self.blobs and (not self.blobs.endswith("/") or self.window is not None):
            raise ValueError(
                f"{self.name}: blobs name a directory ending in a slash, and a"
                " series with blobs declares no window, which ranks files by"
                " their stamps"
            )

    def blob(self, name: str) -> bool:
        """Whether the file *name* is one of the series' blobs, not a row."""
        return bool(self.blobs) and name.startswith(self.blobs)

    @property
    def ref(self) -> str:
        """The full ref name, under the local namespace for a local series."""
        return (self.namespace or (LOCAL_NAMESPACE if self.local else NAMESPACE)) + (
            self.name
        )

    def rows(
        self, store: StateStore, *, only: Callable[[str], bool] | None = None
    ) -> Rows:
        """Every row of the series, newest first, with what was skipped and why.

        An absent ref is no rows and no failure. A ref the transport
        could not read is a failure with its reason, so every caller
        falls open the same way. A file that does not parse or is of
        another schema is skipped and named, and the rest stand. A blob
        the read accepts comes back as text in ``blobs``. With
        *only*, the files whose names it accepts are read and parsed,
        and every other file is named in ``unread``, never read: a
        series of large rows answers a reader of a few of them at the
        cost of those few.
        """
        found = read(store, self.ref, only=only)
        if found.files is None:
            return Rows((), failed=found.failed, reason=self._unreadable(found))
        rows: list[Row] = []
        skipped: list[Skipped] = []
        blobs: dict[str, str] = {}
        for name in sorted(found.files):
            if self.blob(name):
                blobs[name] = found.files[name]
                continue
            data, why = _parse(self, found.files[name])
            if data is None:
                skipped.append(Skipped(name, why))
            else:
                rows.append(Row(name, data))
        rows.sort(key=_newest, reverse=True)
        unread = tuple(name for name in found.names if name not in found.files)
        return Rows(tuple(rows), tuple(skipped), unread=unread, blobs=blobs)

    def row(self, store: StateStore, name: str) -> tuple[Row | None, str]:
        """The row in the file *name*.

        ``(None, "")`` when the ref or the file is absent, and
        ``(None, reason)`` when the ref could not be read or the file
        is not a row of the schema. The ref's other files are not read.
        """
        found = read(store, self.ref, only=name.__eq__)
        if found.files is None:
            return None, self._unreadable(found)
        text = found.files.get(name)
        if text is None:
            return None, ""
        data, why = _parse(self, text)
        if data is None:
            return None, f"{name}: {why}"
        return Row(name, data), ""

    def file(self, store: StateStore, name: str) -> tuple[str | None, str]:
        """The text of the file *name* on the ref, or why it could not be read.

        For a series whose files are not rows: a leg's trace, kept as
        its writer wrote it. ``(None, "")`` is absence, of the ref or
        of the file. The ref's other files are not read.
        """
        found = read(store, self.ref, only=name.__eq__)
        if found.files is None:
            return None, self._unreadable(found)
        return found.files.get(name), ""

    def put(
        self,
        store: StateStore,
        rows: Mapping[str, Mapping[str, Any]],
        *,
        message: str,
        remove: Iterable[str] = (),
        blobs: Mapping[str, str] | None = None,
    ) -> str:
        """Write *rows*, stamped, and *blobs* by file name; ``""`` or the reason.

        Every row is written with the schema and the time stamped on
        it, and the stamp wins over a ``schema`` or ``when`` the row
        carries. Each blob is written as the text given, so a blob of
        the same text is the same object git already holds. The files
        the series already holds stand, less the file names in
        *remove*, so a caller replacing a record in place names what
        goes and puts what changed.

        The window keeps the newest rows by their stamps, so a series
        keyed by something other than time (a tree id) keeps its
        newest too. A write the declaration forbids is refused with
        the rule, and so is a row named as a blob, or a blob named
        outside the series' ``blobs``; the transport's refusals come
        back as they are.
        """
        given = dict(blobs or {})
        astray = sorted(
            [name for name in rows if self.blob(name)]
            + [name for name in given if not self.blob(name)]
        )
        if astray:
            return (
                f"refusing {self.ref}: {', '.join(astray)}: a row lives outside"
                f" the series' blobs ({self.blobs or 'none declared'}) and a"
                " blob inside them"
            )
        stamp = datetime.now(UTC).isoformat(timespec="microseconds")
        files = {
            name: json.dumps(
                {**row, "schema": self.schema, "when": stamp}, sort_keys=True
            )
            for name, row in rows.items()
        } | given
        # The transport's put: a class body is no scope for its methods.
        return put(
            store,
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
            [livery.git.Series][].
        namespace: The prefix every series of the family lives under,
            as for a [livery.git.Series][].
        stale_after: How long a key's ref may live, by its commit
            time, before the janitor drops it as an orphan; ``None``
            never drops by age.
        current: The keys the world still produces, from the checkout
            root, or ``None`` when it cannot tell; a listed key outside
            them is an orphan the janitor drops. ``None`` drops none.
        linger: How long a key outside the current ones stays, by its
            ref's commit time, before the janitor drops it, so a run
            in flight that relies on it can still read it: a branch's
            coverage record is read by main's run for the squash that
            merged it, minutes after the branch is gone. ``None``
            drops such a key at once.
        keep: How many values of the key's *first* part the family
            keeps, read from the checkout root; the rest go. The parts
            are ranked by value, as numbers where they are numbers, so
            a forge's run id ranks by age without reading a commit.
            For a family keyed by a run, this is how many runs are
            kept. ``None``, and a bound the callable cannot tell, keep
            everything.
        holds_rows: Whether the family's series hold JSON rows the
            janitor bounds like any other series. A family of blobs (a
            leg's trace) says false: its files are not rows, and
            ``keep`` is the whole bound.
        blobs: The directory of every series' blobs, as for a
            [livery.git.Series][].
    """

    name: str
    keys: tuple[str, ...]
    window: int | None = None
    ci_only: bool = True
    schema: int = 1
    local: bool = False
    namespace: str = ""
    stale_after: timedelta | None = None
    current: Callable[[Path], set[tuple[str, ...]] | None] | None = None
    linger: timedelta | None = None
    keep: Callable[[Path], int | None] | None = None
    holds_rows: bool = True
    blobs: str = ""

    def __post_init__(self) -> None:
        if self.local and self.ci_only:
            raise ValueError(
                f"{self.name}: a local family is written by local runs; declare"
                " it ci_only=False"
            )

    @property
    def prefix(self) -> str:
        """The refs' common prefix, ending in a slash."""
        base = self.namespace or (LOCAL_NAMESPACE if self.local else NAMESPACE)
        return f"{base}{self.name}/"

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
            namespace=self.namespace,
            blobs=self.blobs,
        )

    def listed(self, store: StateStore, *head: str) -> list[tuple[str, ...]] | None:
        """The keys the remote holds under the leading parts *head*.

        ``None`` when the remote cannot be listed. A ref under the
        prefix with another number of parts is not the family's and
        is left out.
        """
        prefix = self.prefix + "".join(slug(part) + "/" for part in head)
        refs = list_refs(store, prefix)
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
class Read:
    """What a read found: the files, the lease, and whether it could answer.

    Attributes:
        files: The tree's files by name, or ``None`` when there is
            nothing to read; a read that named the files it wants holds
            those alone.
        sha: The commit the ref points at on the remote, the lease a
            write carries; ``None`` when the ref does not exist.
        failed: Whether ``None`` means the transport could not answer
            for a ref that does exist, or a remote it could not reach,
            rather than a ref that has never been written. A reader
            may fall open on either; a writer refuses on a failure.
        reason: The transport's words when it could not answer.
        names: The name of every file the tree holds, read or not,
            sorted; empty when there is nothing to read.
    """

    files: dict[str, str] | None
    sha: str | None
    failed: bool
    reason: str = ""
    names: tuple[str, ...] = ()


@dataclass
class _Snapshot:
    """One listing of the remote namespace, and the commits known to be local.

    Attributes:
        refs: Every ref under the namespace by sha, or ``None`` when
            the listing could not be taken.
        reason: The transport's words when it could not be.
        local: The shas the checkout is known to hold, checked or
            fetched inside this snapshot.
        published: The file the listing was published to for child
            processes, when it was.
        offline: Whether the listing is the checkout's mirror of
            origin, never origin itself: every commit is local, and a
            write of a remote series is refused.
        mirrored: The refs the mirror is scoped to, one per series
            `fetch_store` was told to mirror, under `NAMESPACE`; or
            ``None`` for a live listing. A read or a listing of a
            remote ref outside them fails naming the scope, never
            as an absence, since the mirror never held it.
    """

    refs: dict[str, str] | None
    reason: str = ""
    local: set[str] = field(default_factory=set)
    published: Path | None = None
    offline: bool = False
    mirrored: frozenset[str] | None = None


#: The *fetch* of a `remote_snapshot` that wants every ref of the
#: namespace: the empty prefix below it.
WHOLE: tuple[str, ...] = ("",)

#: The snapshot each checkout reads the remote namespace through while
#: a `remote_snapshot` block is open for it; one per checkout at a time.
_SNAPSHOTS: dict[Path, _Snapshot] = {}

#: The stamp `fetch_store` leaves: one row, ``origin``, saying when the
#: mirror was last taken, how many refs it holds, and which series it
#: was told to mirror, sorted. Its presence is what lets a
#: `fetched_snapshot` read a missing ref of a named series as absent,
#: and the names are the mirror's scope. The schema changes with the
#: row's meaning, so a stamp of another schema reads as no stamp and
#: the gate names the sync, which takes the mirror again.
FETCHED = Series("fetched", window=1, ci_only=False, schema=2, local=True)

#: The ref namespaces one listing carries: the store's own, and the
#: branches the coverage families' current keys are told by.
LISTED = (NAMESPACE, TRACE_NAMESPACE, "refs/heads/")


def _snapshot(store: StateStore) -> _Snapshot | None:
    return _SNAPSHOTS.get(store.root.resolve())


def _list_namespace(store: StateStore) -> tuple[dict[str, str] | None, str]:
    """Every ref under the listed namespaces by sha, or ``(None, reason)``.

    One call lists the store's refs and the branches together, so a
    family's current keys cost no listing of their own.
    """
    listed = _git(store.root, "ls-remote", "origin", *(space + "*" for space in LISTED))
    if listed.code != 0:
        return None, _words(listed)
    refs: dict[str, str] = {}
    for line in listed.stdout.splitlines():
        sha, _, name = line.partition("\t")
        if name:
            refs[name.strip()] = sha.strip()
    return refs, ""


def _missing_locally(store: StateStore, shas: Iterable[str]) -> set[str]:
    """The shas among *shas* the checkout's object store does not hold."""
    wanted = sorted(set(shas))
    if not wanted:
        return set()
    try:
        with _objects.opened(store.root) as repo:
            return {sha for sha in wanted if not _objects.holds(repo, sha.encode())}
    except _objects.NotGitRepository:
        return set(wanted)


@contextmanager
def remote_snapshot(
    store: StateStore, *, fetch: Iterable[str] = (), publish: bool = False
) -> Generator[str | None]:
    """Read the remote namespace through one listing for the block.

    One ``ls-remote`` of the store's namespace and the branches when
    the block opens; every read and listing of a remote ref inside
    answers from it, and a ref whose commit the checkout already
    holds is read with no network at all. The refs whose names start
    with a prefix in *fetch* (spelled below the namespace, ``metrics``
    or ``run/1400/``, or in full from ``refs/`` for another namespace,
    ``refs/workshop-trace/about/``) and whose commits the checkout
    lacks are fetched together, one round trip, when the block opens.
    A read of a ref outside them brings the ref's whole key group in
    one fetch (`_key_group`), so a loop over a family's keys costs one
    round trip whichever ref it reads first. A ref another writer
    moved after the listing is listed again and read at its current
    commit, so a concurrent writer never turns a read into a failure.
    A write inside keeps its compare-and-swap on the listed sha: a
    refused push lists again and retries, and a successful write
    records its new sha, so a read after it sees it. A listing the
    forge could not answer makes every read inside a failure naming
    the reason and every listing unlistable, never an absence. A block
    opened inside another for the same checkout shares the outer
    listing and brings what its own *fetch* names against it.

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
    key = store.root.resolve()
    if key in _SNAPSHOTS:
        _bring_prefixes(store, _SNAPSHOTS[key], fetch)
        yield None
        return
    snapshot = _published_snapshot(store, key)
    if snapshot is None:
        refs, why = _list_namespace(store)
        snapshot = _Snapshot(refs, why)
    refs = snapshot.refs
    _bring_prefixes(store, snapshot, fetch)
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


def _prefix_ref(prefix: str) -> str:
    """A *fetch* prefix as a ref prefix: under `NAMESPACE` unless spelled in full."""
    return prefix if prefix.startswith("refs/") else NAMESPACE + prefix


def _bring_prefixes(
    store: StateStore, snapshot: _Snapshot, fetch: Iterable[str]
) -> None:
    """Bring every listed ref under the prefixes *fetch* names local, in one fetch.

    A ref the fetch cannot bring is left to fail at its read, which
    names the reason.
    """
    prefixes = tuple(_prefix_ref(prefix) for prefix in fetch)
    if snapshot.refs is None or not prefixes:
        return
    wanted = {
        ref: sha for ref, sha in snapshot.refs.items() if ref.startswith(prefixes)
    }
    _bring(store, snapshot, wanted)


def _key_group(snapshot: _Snapshot, ref: str, sha: str) -> dict[str, str]:
    """*ref* and the listed refs of its key group, by sha.

    A family keeps one ref per key under one prefix, and a reader of
    one key reads the others soon after, so a read of one brings the
    group in one fetch. The group is every listed ref under the ref's
    parent with the same number of parts: a run's legs, a base's legs,
    every commit a run was about. A ref directly under its namespace
    (``metrics``) is a group of one, and so is a family's single
    series beside its keys (``coverage/marks`` beside
    ``coverage/<base>/<leg>``), since its depth differs.
    """
    group = {ref: sha}
    space = next((s for s in (NAMESPACE, TRACE_NAMESPACE) if ref.startswith(s)), "")
    if snapshot.refs is None or not space or "/" not in ref[len(space) :]:
        return group
    parent = ref.rpartition("/")[0] + "/"
    depth = ref.count("/")
    for name, listed in snapshot.refs.items():
        if name.startswith(parent) and name.count("/") == depth:
            group[name] = listed
    return group


class Fetched(NamedTuple):
    """What `fetch_store` did: the mirror's size, why it failed, the refs that moved."""

    held: int
    """The refs the mirror holds after the fetch; zero when it failed."""
    why: str
    """Empty, or why the fetch or the stamp failed, in git's words."""
    changed: int = 0
    """The refs this fetch added, moved or pruned; zero on a quiet origin."""


def fetch_store(
    store: StateStore, *, series: Iterable[str], branches: bool = False
) -> Fetched:
    """Mirror the row series *series* under `FETCHED_NAMESPACE` and stamp it.

    One fetch, pruned, with one refspec per name below `NAMESPACE`:
    ``metrics`` lands at ``refs/workshop-origin/metrics``, a series
    origin holds no ref for yet costs nothing, and a ref origin
    dropped goes from the mirror. Afterwards the mirror holds the
    named series alone: every ref under it that is not one of them,
    such as a family's key a fetch of a wider scope left, is deleted
    here. Then the ``origin`` row of `FETCHED` records the time, the
    count of refs, and the names, sorted. The names are the mirror's
    scope: a `fetched_snapshot` reads a missing ref of a named series
    as absent and fails a read of any other remote ref. With
    *branches* the same fetch brings origin's heads into
    ``refs/remotes/origin/``, pruned the same way, so a sync that
    needs both pays one round trip. A fetch transfers only what the
    checkout lacks: on a quiet origin the round trip is the whole
    cost and nothing changes. Named no series and no branches, the
    fetch is skipped and the mirror is emptied.

    Returns:
        The refs the mirror holds and how many this fetch changed, or
        zero of each and the reason when the fetch, the prune or the
        stamp failed, with git's words; nothing here raises or prints.
    """
    names = sorted(set(series))
    before = _local_refs(store, FETCHED_NAMESPACE) or {}
    # A pattern anchored on the whole series name, not the bare name:
    # git refuses a fetch that names an exact ref origin does not hold
    # (a series nobody has written yet) and tolerates a pattern that
    # matches nothing. The one ref such a pattern brings beyond its
    # series is one whose name extends the series' name, which the
    # deletion below takes out of the mirror again.
    refspecs = [f"+{NAMESPACE}{name}*:{FETCHED_NAMESPACE}{name}*" for name in names]
    if branches:
        refspecs.insert(0, "+refs/heads/*:refs/remotes/origin/*")
    if refspecs:
        fetched = _git(store.root, "fetch", "--quiet", "--prune", "origin", *refspecs)
        if fetched.code != 0:
            return Fetched(0, _words(fetched))
    mirrored = _local_refs(store, FETCHED_NAMESPACE)
    if mirrored is None:
        return Fetched(0, "the mirror could not be listed")
    kept = {FETCHED_NAMESPACE + name for name in names}
    strays = sorted(name for name in mirrored if name not in kept)
    if strays:
        why = _drop_mirror_refs(store, strays)
        if why:
            return Fetched(0, why)
        mirrored = {name: sha for name, sha in mirrored.items() if name in kept}
    why = FETCHED.put(
        store,
        {"origin": {"refs": len(mirrored), "series": names}},
        message="fetched origin's store",
    )
    if why:
        return Fetched(0, why)
    changed = sum(
        1
        for name in set(before) | set(mirrored)
        if before.get(name) != mirrored.get(name)
    )
    return Fetched(len(mirrored), "", changed)


def _drop_mirror_refs(store: StateStore, refs: Sequence[str]) -> str:
    """Delete *refs* from the mirror, in-process; ``""`` or the reason."""
    try:
        with _objects.opened(store.root) as repo:
            for ref in refs:
                _objects.drop_ref(repo, ref.encode())
    except (_objects.NotGitRepository, OSError) as error:
        return f"the mirror could not be pruned: {error}"
    return ""


def _stamped_series(stamp: Row) -> tuple[str, ...] | None:
    """The series the `FETCHED` row *stamp* names, or ``None`` when it names none."""
    names: object = stamp.data.get("series")
    if not isinstance(names, list):
        return None
    listed: list[object] = names
    if not all(isinstance(name, str) for name in listed):
        return None
    return tuple(name for name in listed if isinstance(name, str))


@contextmanager
def fetched_snapshot(store: StateStore) -> Generator[None]:
    """Read the remote namespace from the checkout's mirror of origin, for the block.

    Nothing inside reaches origin. The listing is the mirror
    `fetch_store` took, with origin's branches beside it from the
    remote-tracking refs, every commit is local already, and a write
    of a remote series is refused with its reason; a local series
    reads and writes as ever. The series the stamp names are the
    mirror's scope: a read of one of them answers from the mirror, a
    missing ref among them is absence, and a read or a listing of any
    other remote ref, a keyed family's refs among them, fails naming
    the ref and the scope, never as an absence, since the mirror never
    held it; a live `remote_snapshot` reads those. In a checkout
    `fetch_store` never stamped, or stamped under another schema, the
    listing is a failure naming the sync, so every read inside fails
    the way an unreachable origin fails, never as an absence, and
    every caller falls open on it. A block opened inside another
    snapshot for the same checkout shares the outer one.
    """
    key = store.root.resolve()
    if key in _SNAPSHOTS:
        yield
        return
    stamped, why = FETCHED.row(store, "origin")
    names = None if stamped is None else _stamped_series(stamped)
    if stamped is not None and names is None:
        why = "origin: the stamp names no series"
    if names is None:
        detail = f" ({why})" if why else ""
        reason = (
            "origin's state store has not been fetched into this checkout"
            f"{detail}: run `{store.prog} sync`"
        )
        snapshot = _Snapshot(None, reason, offline=True)
    else:
        mirrored = _local_refs(store, FETCHED_NAMESPACE)
        branches = _local_refs(store, "refs/remotes/origin/")
        if mirrored is None or branches is None:
            snapshot = _Snapshot(None, "the mirror could not be listed", offline=True)
        else:
            refs = {
                NAMESPACE + name.removeprefix(FETCHED_NAMESPACE): sha
                for name, sha in mirrored.items()
            }
            for name, sha in branches.items():
                short = name.removeprefix("refs/remotes/origin/")
                if short != "HEAD":
                    refs["refs/heads/" + short] = sha
            snapshot = _Snapshot(
                refs,
                "",
                set(refs.values()),
                offline=True,
                mirrored=frozenset(NAMESPACE + name for name in names),
            )
    _SNAPSHOTS[key] = snapshot
    try:
        yield
    finally:
        _SNAPSHOTS.pop(key, None)


def _published_snapshot(store: StateStore, key: Path) -> _Snapshot | None:
    """The snapshot a parent published for *key*, or ``None`` when there is none."""
    named = os.environ.get(store.snapshot_variable, "")
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


def _relist(store: StateStore, snapshot: _Snapshot) -> None:
    """List the namespace again after another writer moved a ref."""
    snapshot.refs, snapshot.reason = _list_namespace(store)
    _write_published(store.root.resolve(), snapshot)


def _bring(
    store: StateStore, snapshot: _Snapshot, wanted: dict[str, str]
) -> dict[str, str]:
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
        missing = _missing_locally(store, refs.values())
        snapshot.local.update(sha for sha in refs.values() if sha not in missing)
        to_fetch = sorted(ref for ref, sha in refs.items() if sha in missing)
        if not to_fetch:
            return failed
        fetched = _git(store.root, "fetch", "--quiet", "origin", *to_fetch)
        if fetched.code != 0:
            return {ref: _words(fetched) for ref in to_fetch}
        still = _missing_locally(store, [refs[ref] for ref in to_fetch])
        snapshot.local.update(refs[ref] for ref in to_fetch if refs[ref] not in still)
        moved = [ref for ref in to_fetch if refs[ref] in still]
        if not moved:
            return failed
        _relist(store, snapshot)
        if snapshot.refs is None:
            reason = f"the remote could not be listed: {snapshot.reason}"
            return dict.fromkeys(moved, reason)
        refs = {ref: snapshot.refs[ref] for ref in moved if ref in snapshot.refs}
    missing = _missing_locally(store, refs.values())
    return {
        ref: "another writer kept moving it"
        for ref, sha in refs.items()
        if sha in missing
    }


def _reachable(
    store: StateStore, snapshot: _Snapshot, ref: str, sha: str
) -> tuple[str | None, str]:
    """The commit to read for *ref* inside *snapshot*, made local; or the reason.

    The listed *sha* when the checkout holds it or the fetch brings it;
    the ref's current commit when the remote moved past the listing;
    ``None`` with no reason when the ref is gone from the remote. The
    fetch brings the ref's whole key group (`_key_group`), so the
    reads of its siblings that follow cost no round trip.
    """
    if sha in snapshot.local:
        return sha, ""
    failed = _bring(store, snapshot, _key_group(snapshot, ref, sha))
    if ref in failed:
        return None, failed[ref]
    if snapshot.refs is None:
        return None, f"the remote could not be listed: {snapshot.reason}"
    return snapshot.refs.get(ref), ""


def _no_repository(store: StateStore) -> str:
    """The reason a local read or write gives when the store's root is no repository."""
    return f"{store.root} is not a git repository"


def _outside_mirror(store: StateStore, snapshot: _Snapshot, ref: str) -> str:
    """The reason a read of *ref* gives when the snapshot's mirror never held it."""
    names = sorted(name.removeprefix(NAMESPACE) for name in snapshot.mirrored or ())
    scope = ", ".join(names) or "no series"
    return (
        f"{ref} is outside the mirror `{store.prog} sync` takes, which holds the"
        f" series {scope} and nothing else; a live snapshot reads it"
    )


def _git(root: Path, *args: str) -> tools.Result:
    return tools.git.opts(cwd=root, nofail=True, recorded=False)(*args)


def _words(result: tools.Result) -> str:
    """Git's own words for a failed call, whole.

    A remote's refusal often names its reason past the first lines: a
    push protection names the secret it found, its path, and the link
    that allows it at the end of its message.
    """
    return (result.stderr or result.stdout).strip() or "no output"


def _ref_exists(store: StateStore, ref: str) -> tuple[bool | None, str]:
    """Whether *ref* is on the remote; ``None`` when the probe cannot say."""
    probe = _git(store.root, "ls-remote", "origin", ref)
    if probe.code != 0:
        return None, _words(probe)
    return bool(probe.stdout.strip()), ""


def read(
    store: StateStore, ref: str, *, only: Callable[[str], bool] | None = None
) -> Read:
    """Read *ref*'s files, saying whether a miss is absence or failure.

    A remote ref is fetched from origin: the fetch lands in
    ``FETCH_HEAD`` and creates no local ref, so a checkout accrues
    only unreachable objects git's own gc prunes. A failed fetch is a
    failure only when the ref is there: a ref that does not exist
    also fails the fetch, and that is the ordinary first-run case. A
    remote that cannot answer the probe either counts as failed: an
    unreachable forge must never read as "there was never a stamp".
    A local ref, or one of the mirror under `FETCHED_NAMESPACE`, is
    read from the checkout's own git directory: a missing ref is
    absence, and anything else git refuses is a failure with git's
    words. With *only*, the files whose names it accepts are read and
    the rest are named in ``names`` alone, never read.
    """
    if ref.startswith((LOCAL_NAMESPACE, FETCHED_NAMESPACE)):
        try:
            with _objects.opened(store.root) as repo:
                held = _objects.ref_value(repo, ref.encode())
        except _objects.NotGitRepository:
            return Read(None, None, failed=True, reason=_no_repository(store))
        if held is None:
            return Read(None, None, failed=False)
        return _tree_files(store, held.decode(), only)
    snapshot = _snapshot(store)
    if snapshot is not None:
        if snapshot.refs is None:
            return Read(
                None,
                None,
                failed=True,
                reason=f"the remote could not be listed: {snapshot.reason}",
            )
        if snapshot.mirrored is not None and ref not in snapshot.mirrored:
            return Read(
                None, None, failed=True, reason=_outside_mirror(store, snapshot, ref)
            )
        listed = snapshot.refs.get(ref)
        if listed is None:
            return Read(None, None, failed=False)
        sha, why = _reachable(store, snapshot, ref, listed)
        if why:
            return Read(None, listed, failed=True, reason=why)
        if sha is None:
            return Read(None, None, failed=False)
        return _tree_files(store, sha, only)
    fetched = _git(store.root, "fetch", "--quiet", "origin", ref)
    if fetched.code != 0:
        exists, why = _ref_exists(store, ref)
        if exists is False:
            return Read(None, None, failed=False)
        return Read(None, None, failed=True, reason=why or _words(fetched))
    return _tree_files(store, _fetch_head(store), only)


def _fetch_head(store: StateStore) -> str:
    """The commit the last fetch left in ``FETCH_HEAD``, read from the file."""
    with _objects.opened(store.root) as repo:
        text = Path(repo.controldir(), "FETCH_HEAD").read_text("utf-8", "replace")
    return text.split()[0] if text.strip() else ""


def _tree_files(
    store: StateStore, sha: str, only: Callable[[str], bool] | None = None
) -> Read:
    """The files of the root commit *sha*, read in-process as the bytes git holds.

    No process and no checkout conversion: each blob comes back as it
    was written on any platform, and a row a Windows checkout once
    wrote with carriage returns reads back with them, which the
    row's parser takes as whitespace. With *only*, the files it
    refuses are named and never read.
    """
    try:
        with _objects.opened(store.root) as repo:
            blobs, names = _objects.tree_files(repo, sha.encode(), only)
    except _objects.NotGitRepository:
        return Read(None, sha, failed=True, reason=_no_repository(store))
    except (KeyError, _objects.Unresolved):
        reason = f"the tree {sha[:12]} is not in the repository"
        return Read(None, sha, failed=True, reason=reason)
    try:
        files = {name: data.decode("utf-8") for name, data in blobs.items()}
    except UnicodeDecodeError as error:
        return Read(None, sha, failed=True, reason=f"the tree's files: {error}")
    return Read(files, sha, failed=False, names=names)


def list_refs(store: StateStore, prefix: str) -> dict[str, str] | None:
    """The refs under *prefix* by sha, remote or local; ``None`` when unlistable.

    A prefix under the local namespace, the mirror, or the
    remote-tracking refs is listed from the checkout's git directory;
    any other answers from the open snapshot when one covers it, and
    from origin otherwise. A snapshot of the mirror covers the
    branches and a prefix at or below one of its series; a wider
    prefix under a remote namespace, a keyed family's, is unlistable
    from it, never empty, since the mirror holds none of its refs.
    """
    if prefix.startswith((LOCAL_NAMESPACE, FETCHED_NAMESPACE, "refs/remotes/")):
        return _local_refs(store, prefix)
    snapshot = _snapshot(store)
    if snapshot is not None and prefix.startswith(LISTED):
        if snapshot.refs is None:
            return None
        if (
            snapshot.mirrored is not None
            and not prefix.startswith("refs/heads/")
            and not any(prefix.startswith(ref) for ref in snapshot.mirrored)
        ):
            return None
        return {
            name: sha for name, sha in snapshot.refs.items() if name.startswith(prefix)
        }
    listed = _git(store.root, "ls-remote", "origin", prefix + "*")
    return _parse_listing(listed)


def _local_refs(store: StateStore, prefix: str) -> dict[str, str] | None:
    """The checkout's own refs under *prefix* by sha; ``None`` without a repository."""
    try:
        with _objects.opened(store.root) as repo:
            return _objects.refs_under(repo, prefix.encode())
    except _objects.NotGitRepository:
        return None


def _parse_listing(listed: tools.Result) -> dict[str, str] | None:
    if listed.code != 0:
        return None
    refs: dict[str, str] = {}
    for line in listed.stdout.splitlines():
        sha, _, name = line.partition("\t")
        if name:
            refs[name.strip()] = sha.strip()
    return refs


#: The scratch ref ``fast-import`` writes each root commit to, in the
#: checkout's own git directory: never pushed, since no refspec names
#: it, and overwritten by every write, so it holds the last commit
#: built and nothing accrues.
IMPORT_REF = "refs/workshop-import/head"


def _committer_moment() -> datetime:
    """The commit's time: ``GIT_COMMITTER_DATE``, else now.

    git's own override, which the janitor's tests use to write an old
    row. An ISO 8601 stamp or git's raw ``<seconds> <zone>`` form;
    anything else is now.
    """
    raw = os.environ.get("GIT_COMMITTER_DATE", "").strip()
    if raw:
        try:
            moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=UTC)
            return moment
        except ValueError:
            seconds = raw.split()[0].lstrip("@")
            if seconds.isdigit():
                return datetime.fromtimestamp(int(seconds), tz=UTC)
    return datetime.now(UTC)


def _build_commit(
    store: StateStore, files: dict[str, str], message: str
) -> tuple[str, str]:
    """A root commit of *files*; ``(sha, "")`` or ``("", reason)``.

    Written in-process: every blob, the tree and the commit go into
    the checkout's object store, and the commit's id is returned for
    the caller to move the real ref to under its own compare-and-swap.
    `IMPORT_REF` is pointed at it so the objects stay reachable.
    """
    try:
        with _objects.opened(store.root) as repo:
            sha = _objects.root_commit(
                repo,
                {path: text.encode("utf-8") for path, text in files.items()},
                f"{message} [skip ci]",
                identity=store.identity,
                when=_committer_moment(),
            )
            _objects.point(repo, IMPORT_REF.encode(), sha)
    except _objects.NotGitRepository:
        return "", f"the commit could not be written: {_no_repository(store)}"
    except OSError as error:
        return "", f"the commit could not be written: {error}"
    return sha.decode(), ""


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
    store: StateStore,
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
    if not ref.startswith((NAMESPACE, LOCAL_NAMESPACE, TRACE_NAMESPACE)):
        return (
            f"refusing {ref}: the state store writes only under {NAMESPACE},"
            f" {LOCAL_NAMESPACE} and {TRACE_NAMESPACE}"
        )
    if ci_only and not store.in_ci:
        return f"refusing {ref}: only a CI run writes this series; local runs read"
    opened = _snapshot(store)
    if opened is not None and opened.offline and not ref.startswith(LOCAL_NAMESPACE):
        return (
            f"refusing {ref}: this block reads origin's store as last fetched"
            " and writes nothing to origin"
        )
    for _ in range(attempts):
        current = read(store, ref)
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
        commit, why = _build_commit(store, merged, message)
        if why:
            return why
        if ref.startswith(LOCAL_NAMESPACE):
            # The compare-and-swap in-process: the old value is the sha
            # the read returned, or none for a ref that must not exist
            # yet, and a lost swap costs a retry.
            try:
                with _objects.opened(store.root) as repo:
                    moved = _objects.set_ref(
                        repo,
                        ref.encode(),
                        commit.encode(),
                        current.sha.encode() if current.sha else None,
                    )
            except OSError as error:
                return f"update refused: {error}"
            if not moved:
                continue
            return ""
        lease = f"--force-with-lease={ref}:{current.sha or ''}"
        pushed = _git(store.root, "push", "--quiet", lease, "origin", f"{commit}:{ref}")
        snapshot = _snapshot(store)
        if pushed.code != 0:
            if _stale(pushed):
                if snapshot is not None:
                    _relist(store, snapshot)
                continue
            return f"push refused: {_words(pushed)}"
        if snapshot is not None and snapshot.refs is not None:
            snapshot.refs[ref] = commit
            snapshot.local.add(commit)
            _write_published(store.root.resolve(), snapshot)
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


def drop(store: StateStore, ref: str) -> str:
    """Delete *ref*, on origin or in the checkout; ``""`` when gone, else the reason.

    A ref that is already absent counts as gone: the janitor re-runs
    as its own recovery, and a second sweep must find nothing to do.
    """
    if not ref.startswith((NAMESPACE, LOCAL_NAMESPACE, TRACE_NAMESPACE)):
        return (
            f"refusing {ref}: the state store deletes only under {NAMESPACE},"
            f" {LOCAL_NAMESPACE} and {TRACE_NAMESPACE}"
        )
    if ref.startswith(LOCAL_NAMESPACE):
        try:
            with _objects.opened(store.root) as repo:
                _objects.drop_ref(repo, ref.encode())
        except _objects.NotGitRepository:
            return f"delete refused: {_no_repository(store)}"
        except OSError as error:
            return f"delete refused: {error}"
        return ""
    deleted = _git(store.root, "push", "--quiet", "origin", f":{ref}")
    words = deleted.stderr + deleted.stdout
    if deleted.code != 0 and "remote ref does not exist" not in words:
        return f"delete refused: {_words(deleted)}"
    snapshot = _snapshot(store)
    if snapshot is not None and snapshot.refs is not None:
        snapshot.refs.pop(ref, None)
        _write_published(store.root.resolve(), snapshot)
    return ""


def _commit_times(store: StateStore, refs: Sequence[str]) -> dict[str, datetime | None]:
    """When each of *refs*' commits was made; ``None`` for one gone or unreadable.

    The remote refs are brought local together through the open
    snapshot, one fetch for the family; every commit's time is then
    read in-process, so a family of a thousand keys costs the janitor
    one fetch. A local ref is read the same way with no fetch. Remote
    refs are read inside a snapshot alone, which `sweep` opens;
    outside one they are unknown.
    """
    found: dict[str, datetime | None] = dict.fromkeys(refs)
    at: dict[str, str] = {}
    remote = [ref for ref in refs if not ref.startswith(LOCAL_NAMESPACE)]
    if remote:
        snapshot = _snapshot(store)
        if snapshot is not None and snapshot.refs is not None:
            wanted = {ref: snapshot.refs[ref] for ref in remote if ref in snapshot.refs}
            failed = _bring(store, snapshot, wanted)
            for ref in wanted:
                # A ref another writer moved is listed again by the bring
                # and read at its current commit; one it dropped is left
                # unknown.
                sha = snapshot.refs.get(ref) if snapshot.refs is not None else None
                if ref not in failed and sha is not None:
                    at[ref] = sha
    try:
        with _objects.opened(store.root) as repo:
            for ref in refs:
                if ref.startswith(LOCAL_NAMESPACE):
                    held = _objects.ref_value(repo, ref.encode())
                    if held is not None:
                        at[ref] = held.decode()
            for ref, sha in at.items():
                try:
                    found[ref] = _objects.when_made(repo, sha.encode())
                except (KeyError, _objects.Unresolved):
                    found[ref] = None
    except _objects.NotGitRepository:
        return found
    return found


def sweep(
    store: StateStore,
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
    second sweep finds nothing to do. A remote sweep reads through one
    snapshot of the namespace, its caller's when one is open, so a
    family's keys, rows and commit times cost a fetch per key group,
    never one per key.
    """
    moment = now or datetime.now(UTC)
    lines: list[str] = []
    skipped = False
    with remote_snapshot(store) if remote else nullcontext():
        for item in declared:
            if not item.local and not remote:
                skipped = True
            elif isinstance(item, Series):
                lines += _sweep_series(store, item, dry_run=dry_run, now=moment)
            else:
                lines += _sweep_family(store, item, dry_run=dry_run, now=moment)
    if skipped:
        lines.append("  remote series: swept inside CI, never from a machine")
    return lines


def _sweep_series(
    store: StateStore, series: Series, *, dry_run: bool, now: datetime
) -> list[str]:
    """Trim *series* to its window and drop its aged rows; the lines.

    A series with neither a window nor an age has nothing to trim, so
    its rows are counted by name and none is read: a ref of large
    rows costs its listing alone. Its blobs are not rows.
    """
    if series.window is None and series.age is None:
        named = series.rows(store, only=lambda _name: False)
        if named.failed:
            return [f"  {series.ref}: {named.reason}; nothing swept"]
        counted = sum(1 for name in named.unread if not series.blob(name))
        return [f"  {series.ref}: {counted} row(s), within its bounds"]
    found = series.rows(store)
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
        store,
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
    store: StateStore, family: Keyed, *, dry_run: bool, now: datetime
) -> list[str]:
    """Drop *family*'s orphans and bound each key's series; the lines."""
    keys = family.listed(store)
    if keys is None:
        return [f"  {family.prefix}*: could not be listed; nothing swept"]
    lines: list[str] = []
    current = family.current(store.root) if family.current is not None else None
    if family.current is not None and current is None:
        lines.append(
            f"  {family.prefix}*: the current keys could not be told; no orphan dropped"
        )
    beyond, over_words = _beyond_the_count(store, family, keys)
    if family.keep is not None and not over_words:
        lines.append(
            f"  {family.prefix}*: how many {family.keys[0]}s to keep could not be"
            " told; none dropped by count"
        )
    verb = "would drop" if dry_run else "dropped"
    # The commit times the rules below read, in one listing, one fetch
    # and one log for the whole family: every key's under an age rule,
    # the gone keys' under a linger.
    timed = [
        family.at(*key).ref
        for key in keys
        if family.stale_after is not None
        or (family.linger is not None and current is not None and key not in current)
    ]
    made_by = _commit_times(store, timed) if timed else {}
    for key in keys:
        series = family.at(*key)
        reason = "" if key[0] not in beyond else over_words
        if family.stale_after is not None:
            made = made_by.get(series.ref)
            if made is None:
                lines.append(f"  {series.ref}: unreadable; kept")
                continue
            age = now - made
            if age > family.stale_after:
                reason = f"{_hours(age)} old, past {_span(family.stale_after)}"
        if not reason and current is not None and key not in current:
            reason = f"no current {' and '.join(family.keys)} produces it"
            if family.linger is not None:
                made = made_by.get(series.ref)
                kept: timedelta | None = None if made is None else now - made
                if kept is not None and kept <= family.linger:
                    # A run in flight may still read it: main's union
                    # carries from the record of the branch it merged.
                    lines.append(
                        f"  {series.ref}: {reason}; kept {_hours(kept)} into the"
                        f" {_span(family.linger)} a run in flight may still need it"
                    )
                    continue
        if reason:
            why = "" if dry_run else drop(store, series.ref)
            lines.append(f"  {series.ref}: {reason}; {why or verb}")
            continue
        if family.holds_rows:
            lines += _sweep_series(store, series, dry_run=dry_run, now=now)
    return lines


def _beyond_the_count(
    store: StateStore, family: Keyed, keys: Sequence[tuple[str, ...]]
) -> tuple[set[str], str]:
    """The first key parts past the family's count, and the words for dropping them.

    Ranked by the part's own value, as a number where it is one, so a
    forge's run id puts the newest run last without a commit being
    read: the janitor would otherwise fetch every trace it is about to
    drop. A family that keeps no count, or whose count cannot be told,
    drops nothing here and the empty words say which.
    """
    if family.keep is None:
        return set(), ""
    bound = family.keep(store.root)
    if bound is None:
        return set(), ""
    parts = {key[0] for key in keys}
    ranked = sorted(
        parts,
        key=lambda part: (part.isdigit(), int(part) if part.isdigit() else 0, part),
    )
    return set(ranked[: max(0, len(ranked) - bound)]), (
        f"beyond the newest {bound} {family.keys[0]}(s) kept"
    )


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
