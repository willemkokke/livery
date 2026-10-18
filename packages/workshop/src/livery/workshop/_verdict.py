"""Where a branch's pull request stands, as one plain answer.

One read of the forge plus two local git probes, folded into a
livery.workshop._verdict.Verdict that names the state and the exit
code a caller branches on. The codes are stable interface: skills,
hooks, and humans read them, so a new state gets a new code, never a
reused one.

Exit codes:
    10: the merge would conflict; integrate the base and re-submit.
    11: nothing will merge it; the pull request is not armed.
    12: the pull request is closed unmerged.
    13: CI is red; the message names the failing job.
    14: still in flight when the watch deadline passed.
    15: the forge was unreachable past the transient budget.
    16: green and armed with no merge; the server lost the evaluation.
    17: the head is behind the base; integrate and re-submit.

A required-review blocker is not an error: readable protection with
approvals outstanding reads as ``awaiting-approvals``, exit 0, and
the message names the missing count and the eligible reviewers.
Protection the token cannot read is never asserted as a blocker; the
pull request reads as 16 and the message says what to check.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

import livery.footman as footman
from livery import git
from livery.forge import (
    CombinedStatus,
    ForgeError,
    PullRequest,
    RateBudget,
    RateLimited,
    Repository,
    Run,
    classify_merge_refusal,
)

EXIT_CONFLICTS = 10
EXIT_DISARMED = 11
EXIT_CLOSED = 12
EXIT_CI_FAILED = 13
EXIT_TIMEOUT = 14
EXIT_UNREACHABLE = 15
EXIT_STALLED = 16
EXIT_BEHIND = 17
#: ``ci.status``'s exit while the head's runs are still moving, or
#: none has started: the state a script waits on.
EXIT_PENDING = 18

#: Consecutive unreadable polls before a watch gives up with 15.
_UNREACHABLE_BUDGET = 5


#: The share of the forge's API budget below which a watch slows its
#: polling, so one long watch never spends what every other caller needs.
LOW_BUDGET = 0.2

#: About how many requests one poll of a watch makes: the pull request,
#: its checks and their jobs.
REQUESTS_PER_POLL = 10

#: How long a watch waits when the forge refuses with no reset time.
UNSAID_RESET = 60.0


def paced(interval: float, budget: RateBudget | None, *, now: float) -> float:
    """The seconds to wait before the next poll, given what is left of the budget.

    *interval* while the budget is ample or unknown. Below
    [livery.workshop._verdict.LOW_BUDGET] of it, the polls left are
    spread over the time until it renews, so the watch never spends it.
    """
    if budget is None or budget.limit <= 0 or budget.reset_at is None:
        return interval
    if budget.remaining >= budget.limit * LOW_BUDGET:
        return interval
    polls = max(budget.remaining // REQUESTS_PER_POLL, 1)
    return max(interval, (budget.reset_at - now) / polls)


def _duration(seconds: float) -> str:
    """*seconds* as a person reads a wait: seconds under two minutes, minutes above."""
    if seconds < 120:
        return f"{seconds:.0f}s"
    return f"{seconds / 60:.0f} min"


def wait_for_reset(error: RateLimited, *, now: float) -> float:
    """The seconds a watch waits for a spent budget: until it returns, and a margin."""
    if error.reset_at is None:
        return UNSAID_RESET
    return max(error.reset_at - now, 0.0) + 5.0


class Pace:
    """A watch's waits between polls of the forge.

    The interval while the API budget is ample; slower while it is low
    ([livery.workshop._verdict.paced][]), said once; and a spent budget
    waited out until it returns, said with the forge's own refusal.
    """

    def __init__(
        self, interval: float, *, sleep: Callable[[float], None] | None = None
    ) -> None:
        """Pace polls *interval* seconds apart while the budget allows.

        Args:
            interval: Seconds between polls while the budget is ample.
            sleep: What waits; `time.sleep` when None, looked up at
                each wait.
        """
        self.interval = interval
        self._sleep = sleep
        self._slowed = False

    def _wait(self, seconds: float) -> None:
        """Wait *seconds* with the sleep this pace was given."""
        (time.sleep if self._sleep is None else self._sleep)(seconds)

    def rest(self, repo: Repository) -> None:
        """Sleep until the next poll, slowed while *repo*'s forge budget is low."""
        wait = paced(self.interval, repo.rate_budget(), now=time.time())
        slowed = wait > self.interval
        if slowed and not self._slowed:
            print(
                f"  the forge's API budget is low; polling every {wait:.0f}s"
                " until it renews"
            )
        self._slowed = slowed
        self._wait(wait)

    def wait_out(self, error: RateLimited) -> float:
        """Sleep until a spent budget returns; the seconds waited.

        A caller with a deadline moves it by the wait: the forge's
        budget, not the work being watched, took the time.
        """
        wait = wait_for_reset(error, now=time.time())
        print(f"  {error}; waiting {_duration(wait)}, then watching again")
        self._wait(wait)
        return wait


class Transient:
    """A bounded run of unreadable polls: each printed once, the budget ends it.

    A transport error during a poll is a retry, not a verdict: the
    server dropped a connection, answered 5xx, or timed out, and the
    next poll may answer. `note` prints the error with its place in
    the run and returns True when the run has reached the budget;
    `reset` is the answered poll that ends the run. `giving_up` is
    the last line a watcher prints before exiting 15.

    Attributes:
        budget: The consecutive failures the watcher tolerates.
        interval: The seconds between polls, for the line that says
            how long the forge was unreachable.
        count: The failures in the current run.
        last: The last error's words.
    """

    def __init__(self, *, budget: int = _UNREACHABLE_BUDGET, interval: float = 15):
        self.budget = budget
        self.interval = interval
        self.count = 0
        self.last = ""

    def note(self, error: Exception) -> bool:
        """Print *error* as one failure of the run; True when the run is spent."""
        self.count += 1
        self.last = str(error)
        print(f"  forge unreachable ({self.count}/{self.budget}): {error}")
        return self.count >= self.budget

    def reset(self) -> None:
        """An answered poll ends the run."""
        self.count = 0

    def giving_up(self, subject: str) -> str:
        """The line for a spent budget: how long, the last error, and *subject*."""
        seconds = self.budget * self.interval
        return (
            f"  giving up: the forge was unreachable for {self.budget} consecutive"
            f" polls ({seconds:.0f}s at {self.interval:.0f}s); last error:"
            f" {self.last}; {subject}"
        )


@dataclass(frozen=True)
class Pending:
    """What one poll answers while the thing followed is not there yet.

    Attributes:
        line: The line the follow prints when the state changes, and
            again at its quiet cadence while the state holds; empty
            prints nothing.
        key: What tells one pending state from another; the line
            when empty.
    """

    line: str = ""
    key: str = ""

    @property
    def state(self) -> str:
        """The key, or the line when no key was given."""
        return self.key or self.line


class FollowTimedOut(Exception):
    """A follow reached its deadline with the thing followed still pending.

    Attributes:
        subject: What was followed, in the caller's words.
        timeout: The seconds the follow waited.
        last: The last pending answer, for the caller's own line.
    """

    def __init__(self, subject: str, timeout: float, last: Pending) -> None:
        super().__init__(f"{subject}: still pending after {timeout:.0f}s")
        self.subject = subject
        self.timeout = timeout
        self.last = last


class ForgeUnreachable(Exception):
    """A follow spent its transient budget on unreadable polls.

    Attributes:
        line: The giving-up line, how long and the last error, for the
            caller to print before it exits 15 or fails.
        error: The last error.
    """

    def __init__(self, line: str, error: Exception) -> None:
        super().__init__(line.strip())
        self.line = line
        self.error = error


_T = TypeVar("_T")


def follow(
    repo: Repository | None,
    read: Callable[[], _T | Pending],
    *,
    subject: str | Callable[[], str],
    interval: float = 15,
    timeout: float | None = 1800,
    quiet: float | None = 60,
    transient: Transient | None = None,
    retried: tuple[type[Exception], ...] = (ForgeError,),
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> _T:
    """Poll *read* until it answers: the one wait on the forge.

    *read* returns the answer, or a [livery.workshop._verdict.Pending][]
    while there is none yet. The follow prints a pending line when its
    state changes and again every *quiet* seconds while it holds
    (``None`` prints on change alone), rests *interval* seconds between
    polls, slower while *repo*'s API budget is low, and waits out a
    spent budget, moving the deadline by the wait. An error of a
    *retried* type is a retry within *transient*'s budget; a run of
    them that spends it raises
    [livery.workshop._verdict.ForgeUnreachable][] with the giving-up
    line. The deadline, *timeout* seconds from the start, raises
    [livery.workshop._verdict.FollowTimedOut][]; ``None`` waits as long
    as the caller's own timeout allows. Without a *repo* the rests are
    plain sleeps. The deadline and the quiet cadence read *clock*, and
    every wait goes through *sleep*, so a caller that passes both
    decides how much time each poll takes.

    Args:
        repo: The repository polled, for its rate budget; None for a
            wait on something that is not a forge.
        read: One poll: the answer, or a pending state.
        subject: What is followed, for the giving-up and timeout
            lines; a callable is asked only when a line needs it.
        interval: Seconds between polls.
        timeout: Seconds until the follow gives up, or None for none.
        quiet: Seconds after which a held pending line prints again.
        transient: The budget for unreadable polls; a fresh one at
            *interval* when None, shared across calls when given.
        retried: The exception types a poll may raise and be retried.
        clock: Seconds from a fixed start; `time.monotonic` when None.
        sleep: What waits between polls; `time.sleep` when None.

    Returns:
        What *read* answered.

    Raises:
        FollowTimedOut: The deadline passed with *read* still pending.
        ForgeUnreachable: *read* raised a retried error past the budget.
    """
    now_of = time.monotonic if clock is None else clock
    pause = time.sleep if sleep is None else sleep
    started = now_of()
    deadline = None if timeout is None else started + timeout
    if transient is None:
        transient = Transient(interval=interval)
    pace = Pace(interval, sleep=pause)
    last = Pending()
    printed_at: float | None = None
    while True:
        try:
            answer = read()
        except RateLimited as exc:
            waited = pace.wait_out(exc)
            if deadline is not None:
                deadline += waited
            continue
        except retried as exc:
            if transient.note(exc):
                words = subject if isinstance(subject, str) else subject()
                raise ForgeUnreachable(transient.giving_up(words), exc) from exc
            pause(interval)
            continue
        transient.reset()
        if not isinstance(answer, Pending):
            return answer
        now = now_of()
        held = (
            quiet is not None and printed_at is not None and now - printed_at >= quiet
        )
        if answer.line and (printed_at is None or answer.state != last.state or held):
            print(answer.line)
            printed_at = now
        last = answer
        if deadline is not None and now >= deadline:
            words = subject if isinstance(subject, str) else subject()
            raise FollowTimedOut(words, timeout or 0.0, answer)
        if repo is not None:
            pace.rest(repo)
        else:
            pause(interval)


#: Green-and-armed polls the watch grants the server to merge before
#: probing for behind/conflicts and calling the evaluation lost.
#: Forge evidence (livery PR #35): github.com's auto-merge can take
#: over a minute after green, and three 15s polls declared it stalled
#: 25 seconds before the merge landed.
_MERGE_GRACE_POLLS = 8

#: A blocker ends the watch only after holding this many
#: consecutive polls (closed excepted): one-poll blips are races,
#: not verdicts.
_CONFIRM_POLLS = 2


@dataclass(frozen=True)
class Verdict:
    """One reading of where a branch's pull request stands.

    Attributes:
        state: A short name: ``merged``, ``in-flight``, ``conflicts``,
            ``disarmed``, ``closed``, ``ci-failed``, ``stalled``,
            ``behind``, ``awaiting-approvals``, ``no-pr``.
        exit_code: The stable code for the state; 0 when nothing is
            wrong (``merged``, ``in-flight``, ``awaiting-approvals``,
            ``no-pr``).
        detail: One sentence for a person, naming the evidence.
        pr_number: The pull request, when one exists.
    """

    state: str
    exit_code: int
    detail: str
    pr_number: int = 0


def _deciding_runs(runs: tuple[Run, ...]) -> tuple[Run, ...]:
    """The runs that decide a commit: the newest run of each workflow at it.

    A workflow that ran again at the same commit superseded its earlier
    run, so a rehearsal dispatched by hand that failed and then passed,
    or a red run re-run green, no longer counts against the commit.
    """
    newest: dict[str, Run] = {}
    for run in runs:
        held = newest.get(run.workflow)
        if held is None or run.id > held.id:
            newest[run.workflow] = run
    return tuple(newest.values())


def _failing_job(repo: Repository, head_sha: str) -> tuple[str, tuple[Run, ...]]:
    """The first failing job among the runs that decide *head_sha*, and those runs.

    A job carries its own conclusion while its run continues, so a red
    leg is named the moment it ends, not when the last leg does. The
    name is empty when no deciding run has failed: the red the forge
    aggregates then belongs to a superseded run.
    """
    deciding = _deciding_runs(repo.checks.runs(head_sha=head_sha))
    for run in deciding:
        for job in repo.checks.jobs(run.id):
            if job.conclusion in ("failure", "cancelled"):
                tail = "" if run.conclusion else "; the run continues"
                return f"{run.workflow}: {job.name} ({job.conclusion}){tail}", deciding
        if run.conclusion in ("failure", "cancelled"):
            return f"{run.workflow} ({run.conclusion})", deciding
    return "", deciding


def _published_conflict(
    repo: Repository, root: Path, pr: PullRequest, status: CombinedStatus
) -> str:
    """The forge's own words for a pull request it cannot merge, or empty.

    GitHub and GitLab publish a mergeability state beside the pull
    request and start no run for one in conflict, so its combined
    status stays ``none`` for as long as anyone waits. The local probe
    can miss the conflict (a merge driver the forge ignores resolves it
    here), so the forge's answer decides. Gitea publishes none.
    """
    from livery.workshop import _forge_lane

    kind = _forge_lane.forge_kind(root)
    if kind not in ("github", "gitlab"):
        return ""
    hold = repo.pr.merge_hold(pr.number)
    if hold in ("", "clean", "mergeable"):
        return ""
    classified = classify_merge_refusal(
        kind, "", combined=status, item_state=pr.state, merged=pr.merged, hold=hold
    )
    if classified.state != "conflict":
        return ""
    return f"{classified.message} (the forge reports {hold})"


_GREEN_CONCLUSIONS = ("", "success", "skipped", "neutral")


def elapsed_label(seconds: float) -> str:
    """*seconds* as a person reads a wait: `37s`, `4m12s`, `1h02m`."""
    whole = int(seconds)
    if whole < 60:
        return f"{whole}s"
    if whole < 3600:
        return f"{whole // 60}m{whole % 60:02d}s"
    return f"{whole // 3600}h{whole % 3600 // 60:02d}m"


class JobWatch:
    """The jobs of a head's runs, reported as they move, each line stamped.

    Each report reads every run for the head and the jobs of each,
    prints one line per job whose status or conclusion changed since
    the last report, and under a job that just failed the log's
    failure lines, when the forge already has the log. A report that
    finds nothing moved prints nothing. Every line opens with the time
    since the watch began, so a log read later shows where the minutes
    went; *clock* is the seam a test hands a clock of its own to.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.seen: dict[tuple[str, str], str] = {}
        self._clock = clock
        self._started = clock()

    def stamp(self) -> str:
        """The elapsed time since the watch began, padded for a line's head."""
        return f"  {elapsed_label(self._clock() - self._started):>6}  "

    def report(self, repo: Repository, head_sha: str) -> list[str]:
        """Print and return the lines for what moved since the last report."""
        lines: list[str] = []
        if not head_sha:
            return lines
        for run in repo.checks.runs(head_sha=head_sha):
            for job in repo.checks.jobs(run.id):
                state = job.conclusion or job.status
                key = (run.workflow, job.name)
                if self.seen.get(key) == state:
                    continue
                self.seen[key] = state
                word = {"failure": "failed", "in_progress": "running"}.get(state, state)
                stamp = self.stamp()
                workflow = run.workflow.rsplit("/", 1)[-1]
                lines.append(f"{stamp}{workflow} / {job.name}: {word}")
                if job.conclusion not in _GREEN_CONCLUSIONS:
                    pad = " " * len(stamp)
                    lines += [pad + line for line in _failure_excerpt(repo, job.id)]
        for line in lines:
            print(line)
        return lines


def _failure_excerpt(repo: Repository, job_id: int, *, limit: int = 12) -> list[str]:
    """The failure lines of a job's log; nothing while the log is unstored."""
    from livery.workshop._ci_tasks import failure_lines

    try:
        log = repo.checks.job_log(job_id)
    except ForgeError:
        return []
    return failure_lines(log, limit=limit)


def _head_of(repo: Repository, branch: str) -> str:
    """The pull request's head for *branch*, or empty without one."""
    pr = repo.pr.find_by_head(branch)
    return pr.head_sha if pr is not None else ""


def classify(
    repo: Repository, branch: str, root: Path, *, grace_spent: bool = False
) -> Verdict:
    """One reading of the branch, named and coded.

    *grace_spent* is the watcher's signal that a green-and-armed pull
    request has already been given time to merge, so the local probes
    (behind, conflicts) may now decide between 10, 16, and 17. A
    single un-watched read never reports 16: the server may simply not
    have evaluated yet.
    """
    from livery.workshop._forge_lane import pull_request_for

    pr = pull_request_for(repo, root, branch)
    if pr is None:
        return Verdict("no-pr", 0, f"no pull request for {branch}")
    if pr.merged:
        return Verdict("merged", 0, f"PR #{pr.number} merged", pr.number)
    if pr.state == "closed":
        return Verdict(
            "closed",
            EXIT_CLOSED,
            f"PR #{pr.number} is closed unmerged; `{footman.prog()} submit`"
            " after reopening,"
            " or start over",
            pr.number,
        )
    # The local head is ground truth for a verdict: right after a
    # push the forge's pull request read can lag and still report
    # the previous head, and a verdict on that head judges the
    # commit the push replaced. In-flight until the forge catches
    # up; a branch checked out elsewhere (submit.merge from another
    # tree) matches by construction.
    standing, head = git.position(root)
    local_head = head if standing == branch else ""
    if local_head and pr.head_sha != local_head:
        return Verdict(
            "in-flight",
            0,
            f"the forge still reports {pr.head_sha[:10]}; the local head"
            f" is {local_head[:10]}",
            pr.number,
        )
    status = repo.checks.status(pr.head_sha)
    state = status.state
    if state == "failure":
        failing, deciding = _failing_job(repo, pr.head_sha)
        if failing or not deciding:
            return Verdict(
                "ci-failed", EXIT_CI_FAILED, failing or "CI is red", pr.number
            )
        # The red the forge aggregates belongs to a superseded run: a
        # workflow that ran again at this commit. The runs that decide
        # are green or still moving, so they judge the commit.
        moving = any(run.status != "completed" for run in deciding)
        state = "pending" if moving else "success"
    if state == "none":
        # A pull request whose merge ref the forge cannot build gets no
        # run at all, so its status stays none for as long as anyone
        # waits. The conflict probe decides at once instead: exit 10,
        # and the self-heal integrates and re-submits. The forge's
        # published state decides where the local probe cannot see the
        # conflict.
        git.fetch(root)
        if git.conflicts_with_base(root, pr.base_branch):
            return Verdict(
                "conflicts",
                EXIT_CONFLICTS,
                f"PR #{pr.number} conflicts with {pr.base_branch}, so the forge"
                " runs nothing for it",
                pr.number,
            )
        published = _published_conflict(repo, root, pr, status)
        if published:
            return Verdict(
                "conflicts",
                EXIT_CONFLICTS,
                f"PR #{pr.number} cannot merge into {pr.base_branch}, so the"
                f" forge runs nothing for it: {published}",
                pr.number,
            )
    if state in ("pending", "none"):
        return Verdict("in-flight", 0, f"CI {state} for {pr.head_sha[:10]}", pr.number)
    # Green. A conflict blocks the merge whatever the arming state, so
    # it is classified first; behind matters only once an armed pull
    # request has been given time, because without strict protection an
    # armed behind head still merges.
    git.fetch(root)
    if git.conflicts_with_base(root, pr.base_branch):
        return Verdict(
            "conflicts",
            EXIT_CONFLICTS,
            f"PR #{pr.number} conflicts with {pr.base_branch}",
            pr.number,
        )
    if not repo.pr.is_armed(pr.number):
        # Forge evidence (livery PR #21): a merge in flight consumes the
        # schedule between the open-PR read and this one, so an armed
        # merge can read as "green and not armed". Re-read before
        # reporting: merged wins over any blocker derived from stale
        # reads.
        current = repo.pr.get(pr.number)
        if current is not None and current.merged:
            return Verdict("merged", 0, f"PR #{pr.number} merged", pr.number)
        behind = git.behind_base(root, pr.base_branch)
        if behind:
            # Without this, the parked answer says "merge it" while
            # the merge verb refuses behindness, and the two point at
            # each other with no exit.
            return Verdict(
                "disarmed",
                EXIT_DISARMED,
                f"PR #{pr.number} is green, parked unarmed, and {behind}"
                f" commit(s) behind {pr.base_branch}:"
                f" `{footman.prog()} integrate`, then"
                f" `{footman.prog()} submit`, then merge",
                pr.number,
            )
        return Verdict(
            "disarmed",
            EXIT_DISARMED,
            f"PR #{pr.number} is green and parked unarmed; arm it with"
            f" `{footman.prog()} submit --armed`, or merge it with"
            f" `{footman.prog()} submit.merge`",
            pr.number,
        )
    if not grace_spent:
        return Verdict("in-flight", 0, "green and armed; awaiting the merge", pr.number)
    if git.behind_base(root, pr.base_branch) > 0:
        return Verdict(
            "behind",
            EXIT_BEHIND,
            f"PR #{pr.number} is behind {pr.base_branch}, and protection"
            " blocks outdated branches",
            pr.number,
        )
    current = repo.pr.get(pr.number)
    if current is not None and current.merged:
        return Verdict("merged", 0, f"PR #{pr.number} merged", pr.number)
    outstanding = _approvals_outstanding(repo, pr.number, pr.base_branch, root)
    if outstanding is not None:
        return Verdict("awaiting-approvals", 0, outstanding, pr.number)
    return Verdict(
        "stalled",
        EXIT_STALLED,
        f"PR #{pr.number} is green and armed with no merge: the server lost"
        " the evaluation (or a required review is missing)",
        pr.number,
    )


def _approvals_outstanding(
    repo: Repository, number: int, base: str, root: Path
) -> str | None:
    """The awaiting-approvals message, or None when reviews are not it.

    Green and armed with reviews missing is nobody's error: the arm
    survives, and the last approval merges it with nothing to
    re-run. Answered only when protection is readable and actually
    requires approvals the pull request does not have; an unreadable
    protection stays with the stalled verdict's softer wording,
    because a blocker must never be asserted from a state that could
    not be read.
    """
    try:
        protection = repo.protection(base)
    except ForgeError:
        return None
    if protection is None or protection.required_approvals <= 0:
        return None
    try:
        reviews = repo.pr.reviews(number)
        details = repo.pr.get(number)
    except ForgeError:
        return None
    approved = {r.author for r in reviews if r.state == "approved"}
    needed = protection.required_approvals - len(approved)
    if needed <= 0:
        return None
    author = details.author if details is not None else ""
    eligible = _eligible_reviewers(root, base) - approved - {author}
    who = ", ".join(sorted(eligible)) if eligible else "the declared owners"
    plural = "approval" if needed == 1 else "approvals"
    return (
        f"green and armed, awaiting {needed} {plural}: {who} can give"
        " them. The arm survives, so the last approval merges it with"
        " nothing to re-run."
    )


def _eligible_reviewers(root: Path, base: str) -> set[str]:
    """Owners of the touched paths, from the package declarations.

    Best effort: an unreadable workspace answers empty and the
    message falls back to naming the declared owners collectively.
    """
    try:
        from livery.workshop._governance import governance_entries

        touched = git.run(
            root, "diff", "--name-only", f"origin/{base}...HEAD"
        ).splitlines()
        owners: set[str] = set()
        for entry in governance_entries(root):
            prefix = entry.path.strip("/")
            if any(name.startswith(prefix) for name in touched if name):
                owners.update(owner for owner in entry.owners if "/" not in owner)
        return owners
    except Exception:
        return set()


def follow_pull_request(
    repo: Repository,
    branch: str,
    root: Path,
    *,
    interval: float = 15,
    timeout: float = 1800,
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> Verdict:
    """Watch until a terminal verdict, or raise SystemExit with its code.

    Returns the ``merged`` verdict on success, or ``awaiting-approvals``
    once the arm holds and a review is what happens next. Every job's
    status change prints as a line of its own as it happens
    ([livery.workshop._verdict.JobWatch][]), and a job that fails
    prints its failure lines under its name. Every terminal blocker
    (10-13, 16, 17) raises SystemExit carrying its code, after printing
    the detail; the deadline raises 14; a forge unreachable past the
    transient budget raises 15, naming the last error and the pull
    request. The polls, the transient budget, the API budget, and the
    *clock* and *sleep* the deadline and the waits use are
    [livery.workshop._verdict.follow][]'s.
    """
    green_polls = 0
    confirm_streak = 0
    last_state = ""
    last_verdict: Verdict | None = None
    jobs = JobWatch()

    def read() -> Verdict | Pending:
        nonlocal green_polls, confirm_streak, last_state, last_verdict
        grace_spent = green_polls >= _MERGE_GRACE_POLLS
        verdict = classify(repo, branch, root, grace_spent=grace_spent)
        # Every job's move prints as it happens, before the verdict
        # line it may lead to: a red leg is named while the others
        # run, with its failure lines, so the fix starts at once.
        jobs.report(repo, _head_of(repo, branch))
        last_verdict = verdict
        changed = verdict.state != last_state
        last_state = verdict.state
        if changed:
            confirm_streak = 0
        line = f"{jobs.stamp()}{verdict.state}: {verdict.detail}"
        if verdict.state in ("merged", "awaiting-approvals"):
            if changed:
                print(line)
            return verdict
        if verdict.exit_code:
            # A blocker must hold for two consecutive polls before it
            # ends the watch: the submit-then-arm race, an async
            # mergeability recompute, and a close-supersede all read
            # as a blocker for one poll and clear on the next. CLOSED
            # is the exception, definitive on a single read: a closed
            # pull request does not reopen itself.
            confirm_streak += 1
            if verdict.state == "closed" or confirm_streak >= _CONFIRM_POLLS:
                if changed:
                    print(line)
                return verdict
            return Pending(line, key=verdict.state)
        if verdict.state == "in-flight" and verdict.detail.startswith("green"):
            green_polls += 1
        else:
            green_polls = 0
        return Pending(line, key=verdict.state)

    try:
        verdict = follow(
            repo,
            read,
            subject=lambda: _pull_request_words(repo, branch),
            interval=interval,
            timeout=timeout,
            quiet=None,
            clock=clock,
            sleep=sleep,
        )
    except ForgeUnreachable as exc:
        print(exc.line)
        raise SystemExit(EXIT_UNREACHABLE) from None
    except FollowTimedOut:
        print(f"  still in flight after {timeout:.0f}s")
        if last_verdict is not None:
            _record_ending(repo, last_verdict, branch, root)
        raise SystemExit(EXIT_TIMEOUT) from None
    if verdict.exit_code:
        _record_ending(repo, verdict, branch, root)
        raise SystemExit(verdict.exit_code)
    return verdict


def _pull_request_words(repo: Repository, branch: str) -> str:
    """The pull request to look at, or the branch when the forge cannot say."""
    try:
        pr = repo.pr.find_by_head(branch, state="all")
    except ForgeError:
        pr = None
    if pr is None:
        return f"the pull request for {branch} could not be read"
    return f"the pull request: PR #{pr.number} {pr.url}"


def _record_ending(repo: Repository, verdict: Verdict, branch: str, root: Path) -> None:
    """Write the diagnostic bundle for a non-merged ending; best effort."""
    from livery.workshop._diagnostics import SERIES, record

    name, why = record(root, repo, verdict, branch=branch)
    if name:
        print(f"  diagnostics: {name} on {SERIES.ref}")
    else:
        print(f"  diagnostics: not recorded ({why})")
