"""``fm submit``: get the branch onto the remote, verified, and landed.

Local gate first (the gate the CI legs run, so a red gate costs
zero network calls), then the
closes-link resolution with existence probes, disarm-before-push,
push, find-or-open with the title rules, arm per the arming ladder,
and follow until it lands or says what stopped it. Exits 10 and 17 self-heal by
integrating the base and re-submitting; every other code surfaces
unchanged, because skills, hooks, and humans branch on them.

``fm abandon`` is the exit: disarm, close the pull request, delete
both branches, return to the base. ``fm submit.merge`` merges a
green pull request that was deliberately left unarmed, and refuses
anything less than green. ``fm submit.arm`` and ``fm submit.disarm``
set and take off a pull request's auto-merge and leave it open. The
three act on this branch's pull request, or on the one a number
names, from any checkout.

The arming ladder, highest wins: ``--armed``/``--no-armed`` for one
invocation; ``WORKSHOP_AUTOMERGE`` as the per-user standing preference;
the workspace contract's ``[ci] automerge`` as committed repo policy;
off. The ladder is footman's option ladder; only the printed reason
is derived here.
"""

from __future__ import annotations

import contextlib
import os
import re
import tomllib
from collections.abc import Callable, Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, ParamSpec

import livery.footman as footman
from livery import git
from livery.footman import Arg, doc, fail, group
from livery.forge import ForgeError, PullRequest, Repository, Unsupported
from livery.workshop import _stack
from livery.workshop._contract import load_contract
from livery.workshop._conventional import TITLE_RE, TYPES
from livery.workshop._extensions import workspace_root
from livery.workshop._points import EVERY_RUNNER_LABEL
from livery.workshop._traces import drop_run
from livery.workshop._verdict import (
    EXIT_BEHIND,
    EXIT_CONFLICTS,
    EXIT_DISARMED,
    EXIT_UNREACHABLE,
    ForgeUnreachable,
    Pending,
    follow,
    follow_pull_request,
)

submit = group(
    "submit", help="Gate, push, open-or-reuse the PR, arm, and watch it land"
)

#: Branch names submit accepts: the conventional type, a slash, a slug
#: that may open with an issue number (``feat/41-title-rules``).
_BRANCH_TYPES = TYPES
_BRANCH_RE = re.compile(rf"^({'|'.join(_BRANCH_TYPES)})/.+$")
_BRANCH_ISSUE_RE = re.compile(r"^[a-z]+/(\d+)-")

#: PR titles follow the published commit convention, breaking
#: marker included.
_TITLE_RE = TITLE_RE

#: Self-heal ceiling. Exits 10 and 17 are cured by one integrate and
#: re-push; needing a third round means the base is moving faster than
#: the heal, and a person should look rather than a loop spin.
_MAX_HEALS = 2

#: How many times arming is re-tried when the forge silently loses the
#: schedule (the lost-auto-merge-schedule quirk): armed is verified by
#: read-back, never assumed from a 2xx.
_ARM_RETRIES = 3

_P = ParamSpec("_P")

#: How often a followed merge hold re-tries or re-reads, seconds.
_HOLD_POLL = 3.0


def _contract_forge_kind(root: Path) -> str:
    """The forge the contract names; [livery.workshop._forge_lane.forge_kind][]."""
    from livery.workshop._forge_lane import forge_kind

    return forge_kind(root)


def _reported_context_names(repo: Repository, sha: str) -> tuple[str, ...]:
    """The status contexts CI actually reports for *sha*.

    Derived from the runs and their jobs in the grammar the forge
    reports under ("<workflow> / <job> (<event>)"), so a required
    context can be judged against what exists rather than what was
    hoped for.
    """
    names: list[str] = []
    for run in repo.checks.runs(head_sha=sha):
        base = run.workflow.rsplit("/", 1)[-1]
        base = base.removesuffix(".yml").removesuffix(".yaml")
        for job in repo.checks.jobs(run.id):
            names.append(f"{base} / {job.name} ({run.event})")
    return tuple(dict.fromkeys(names))


def _refuse_unreported_contexts(repo: Repository, number: int) -> None:
    """Fail when protection requires a context nothing reports.

    The never-converging cousin of the settling beat: the checks
    read green, protection waits for a context name CI never emits,
    and waiting would hang forever. The refusal names both sides.
    """
    pr = repo.pr.get(number)
    if pr is None:
        return
    protection = repo.protection(pr.base_branch)
    if protection is None:
        return
    reported = _reported_context_names(repo, pr.head_sha)
    missing = [c for c in protection.required_contexts if c not in reported]
    if missing:
        fail(
            f"protection requires contexts CI never reports:"
            f" {', '.join(missing)}. Reported:"
            f" {', '.join(reported) or 'nothing'}. Fix the required"
            " context names to match what CI reports."
        )


def _wait_for_verdict(repo: Repository, number: int, *, local_head: str = "") -> None:
    """Wait until *number*'s head has a completed combined verdict.

    No inner deadline: merge means merge as soon as possible, so a
    running CI is followed to its verdict and the task's own timeout
    is the backstop. Progress prints on state changes and every
    minute, so a long wait stays visible. A head that moves during
    the wait is followed, and the move is said: the old and new
    heads, whether the new one is *local_head* (this clone's own
    push) or arrived from elsewhere, and the newest run for it, so
    the cancelled run it leaves behind needs no investigation. A
    forge unreachable past the transient budget exits 15.
    """
    pr = repo.pr.get(number)
    if pr is None:
        return
    head = pr.head_sha

    def read() -> str | Pending:
        nonlocal head
        current = repo.pr.get(number)
        if current is not None and current.head_sha and current.head_sha != head:
            print(_head_moved_line(repo, number, head, current.head_sha, local_head))
            head = current.head_sha
        state = repo.checks.status(head).state
        if state not in ("pending", "none"):
            return state
        return Pending(f"  waiting: checks are {state} for PR #{number}", key=state)

    try:
        follow(
            repo,
            read,
            subject=f"PR #{number}",
            interval=_HOLD_POLL,
            timeout=None,
            quiet=60,
        )
    except ForgeUnreachable as exc:
        print(exc.line)
        raise SystemExit(EXIT_UNREACHABLE) from None


def _head_moved_line(
    repo: Repository, number: int, old: str, new: str, local_head: str
) -> str:
    """The one line for a pull request whose head moved under the watch."""
    if local_head and new == local_head:
        whose = "this clone's own push"
    elif local_head:
        whose = "pushed from elsewhere, not this clone's HEAD"
    else:
        whose = "a newer push"
    try:
        runs = repo.checks.runs(head_sha=new)
    except (ForgeError, Unsupported):
        runs = ()
    newest = max(runs, key=lambda run: run.id, default=None)
    run_words = (
        f"; following run {newest.id}" if newest is not None else "; following it"
    )
    return (
        f"  PR #{number}: the head moved from {old[:12]} to {new[:12]}"
        f" ({whose}){run_words}"
    )


def _follow_merge_state(
    repo: Repository,
    kind: str,
    number: int,
    act: Callable[_P, object],
    *args: _P.args,
    **kwargs: _P.kwargs,
) -> None:
    """Drive *act* to a decided state; merge means as soon as possible.

    A 405, or the 422 GitLab answers while its mergeability recompute
    runs, classifies through [livery.forge.classify_merge_refusal][]
    with the pull request, its combined status and the forge's
    published hold fetched fresh.
    In-progress states follow through to completion, the task's own
    timeout the only backstop; recoverable and terminal states fail
    with the user-facing message and the forge's words verbatim;
    discovered-merged walks past quietly. Entering the settling
    state first verifies every required context has a reporter,
    because a context nothing reports never settles.
    """
    from livery.forge import classify_merge_refusal

    settling_checked = False
    local_head = str(kwargs.pop("local_head", "") or "")

    def read() -> bool | Pending:
        nonlocal settling_checked
        try:
            act(*args, **kwargs)
            return True
        except ForgeError as exc:
            if exc.status not in (405, 422):
                raise
            pr = repo.pr.get(number)
            if pr is None:
                raise
            hold = classify_merge_refusal(
                kind,
                str(exc),
                combined=repo.checks.status(pr.head_sha),
                item_state=pr.state,
                merged=pr.merged,
                hold=repo.pr.merge_hold(number),
            )
        if hold.category == "success":
            print(f"  PR #{number}: {hold.message}; walking past")
            return True
        if hold.category == "in-progress":
            if hold.state == "contexts-settling" and not settling_checked:
                settling_checked = True
                _refuse_unreported_contexts(repo, number)
            if hold.state == "ci-running":
                _wait_for_verdict(repo, number, local_head=local_head)
            return Pending(f"  waiting: {hold.message}", key=hold.state)
        native = f"\n  the forge said: {hold.native}" if hold.native else ""
        fail(f"PR #{number}: {hold.message}{native}")

    # A refusal other than the merge endpoint's two hold codes is the
    # forge's answer, not a transient: it reaches the caller unretried.
    follow(
        repo,
        read,
        subject=f"PR #{number}",
        interval=_HOLD_POLL,
        timeout=None,
        quiet=None,
        retried=(),
    )


def _root() -> Path:
    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    return root


def ci_automerge() -> bool:
    """The committed repo policy: the workspace contract's ``[ci] automerge``."""
    root = workspace_root()
    if root is None:
        return False
    contract = load_contract(root / "workshop.toml")
    return bool((contract.get("ci") or {}).get("automerge", False))


def arming_reason(*, armed: bool, flag_given: bool) -> str:
    """One sentence naming the ladder level that decided *armed*."""
    if flag_given:
        return f"--{'' if armed else 'no-'}armed (this invocation)"
    if os.environ.get("WORKSHOP_AUTOMERGE") is not None:
        return "WORKSHOP_AUTOMERGE (per-user standing preference)"
    root = workspace_root()
    if root is not None:
        contract = load_contract(root / "workshop.toml")
        if "automerge" in (contract.get("ci") or {}):
            return "[ci] automerge in workshop.toml (committed repo policy)"
    return "the default (auto-merge is opt-in; nothing configured)"


def branch_issue(branch: str) -> int | None:
    """The issue number a ``<type>/<number>-slug`` branch carries, or None."""
    match = _BRANCH_ISSUE_RE.match(branch)
    return int(match.group(1)) if match else None


def parse_number(token: str) -> int | None:
    """Read the issue or pull request number *token* spells; None when it spells none.

    The one rule every verb that takes a number from a person reads it
    by. A leading ``#`` is accepted, as the forges print a number, so
    ``#123`` and ``123`` are the same number. Anything else that is
    not all decimal digits spells no number.
    """
    bare = token.lstrip("#")
    return int(bare) if bare.isdecimal() else None


def with_closes(body: str, number: int) -> str:
    """*body* with its ``Closes #number`` trailer, present exactly once."""
    trailer = f"Closes #{number}"
    if trailer in body:
        return body
    return f"{body}\n\n{trailer}" if body else trailer


def resolve_closes(repo: Repository, branch: str, closes_flag: int) -> int | None:
    """The issue submit will link, per the three guards.

    The flag beats the branch-name parse; a disagreement is printed,
    never silently resolved; and the issue must exist before
    ``Closes #N`` is written. A branch-parsed number that does not
    exist drops the link with a note (plain branches always work); an
    explicit ``--closes`` that does not exist refuses.
    """
    parsed = branch_issue(branch)
    if closes_flag:
        if parsed is not None and parsed != closes_flag:
            print(f"  note: --closes {closes_flag} beats the branch's #{parsed}")
        if repo.issue.get(closes_flag) is None:
            fail(f"--closes {closes_flag} refused: the issue does not exist")
        return closes_flag
    if parsed is None:
        return None
    if repo.issue.get(parsed) is None:
        print(
            f"  note: branch names issue #{parsed}, which does not exist;"
            " submitting without a Closes link"
        )
        return None
    return parsed


@dataclass(frozen=True)
class Plan:
    """Everything a push and pull request need, validated, no network.

    Attributes:
        branch: The feature branch being submitted.
        base: The branch the pull request targets.
        title: The pull request title, validated.
        body: The pull request body.
        title_given: Whether the title came from the caller. A
            defaulted title tracks HEAD, so only a given title may
            overwrite an existing pull request's.
        body_given: Whether the body came from the caller; the same
            rule for an existing pull request's body.
        subjects: The subjects of the commits the branch carries beyond
            ``origin/<base>``, read once for the checks that need them.
    """

    branch: str
    base: str
    title: str
    body: str
    title_given: bool
    body_given: bool = False
    subjects: tuple[str, ...] = ()


def prepare(
    root: Path,
    *,
    title: str = "",
    body: str = "",
    base: str = "main",
    branch: str = "",
) -> Plan:
    """Validate the branch and assemble the pull request shape.

    *branch* is the checked-out branch when the caller read it already;
    read here otherwise.
    """
    branch = branch or git.current_branch(root)
    if not branch or branch == base:
        fail(
            f"not on a feature branch (on {branch or '(detached)'!r}, base is"
            f" {base!r}): create a <type>/<slug> branch first"
        )
    if not _BRANCH_RE.match(branch) and not branch.startswith("workflow/"):
        # Reserved workflow branches are the engine's, named by their
        # workflow identity, outside the feature grammar on purpose.
        fail(
            f"branch {branch!r} does not match <type>/<slug>"
            f" (types: {', '.join(_BRANCH_TYPES)})"
        )
    # A merge commit (the engine's MERGE_DEFAULT, `fm integrate`) can
    # be HEAD, and "Merge branch ..." is never the intent: the last
    # real subject is. The squash erases the merge commits anyway.
    default_title, default_body = git.head_message(root)
    subjects = tuple(git.subjects_ahead(root, base))
    if not title and default_title.startswith("Merge "):
        real = [subject for subject in subjects if not subject.startswith("Merge ")]
        if real:
            default_title = real[-1]
    resolved_title = title or default_title
    if not _TITLE_RE.match(resolved_title):
        fail(
            f"PR title rejected: {resolved_title!r}\n"
            '  pass a valid one: --title="type(scope): subject"'
        )
    return Plan(
        branch=branch,
        base=base,
        title=resolved_title,
        body=body or default_body,
        title_given=bool(title),
        body_given=bool(body),
        subjects=subjects,
    )


def resolve_base(root: Path, base: str, *, given: bool, branch: str = "") -> str:
    """The branch the pull request targets.

    A ``--base`` flag wins. Otherwise the parent ``fm start --from``
    recorded for the branch is the target while origin still has it,
    and *base* once it is gone: a merged parent's branch is deleted,
    and the forge retargets the child's pull request to the parent's
    own base at that moment.

    Origin is asked for the branch, never the remote-tracking ref: a
    plain fetch does not prune, so the tracking ref of a branch the
    forge deleted at the merge survives, and a pull request against a
    branch that is gone is refused with a validation error rather than
    a sentence.
    """
    if given:
        return base
    parent, _tip = _stack.stack(root, branch or git.current_branch(root))
    if not parent:
        return base
    if parent in git.remote_branches(root, parent):
        print(f"  base: {parent}, the parent `{footman.prog()} start --from` recorded")
        return parent
    print(f"  base: {base}; the recorded parent {parent} is gone from origin (merged)")
    return base


def abort_if_merged(repo: Repository, root: Path, branch: str) -> int | None:
    """Stop before pushing into a pull request that already merged.

    The merge took the pre-push head, so pushing on would open a
    second pull request carrying the follow-up. That is only true
    when the local branch still carries the merged head: a branch
    with the same name cut fresh off the merged base is a new cycle
    (the release train reuses its reserved branch name every
    release), and it proceeds. Detection is best-effort: a merged
    pull request whose head branch is already deleted is found by
    head sha instead, and one invisible both ways surfaces later as
    the stray-PR symptom the message names.

    Returns the merged pull request's number when HEAD is the head it
    merged: there is nothing to push, and the re-run's job is to
    report the merge. None when no merged pull request claims the
    branch or the branch is a fresh cycle.
    """
    pr = merged_pull_request(repo, root, branch)
    if pr is None:
        return None
    if pr.head_sha and not git.is_ancestor(root, pr.head_sha, "HEAD"):
        return None
    if pr.head_sha and pr.head_sha == git.head_sha(root):
        return pr.number
    fail(
        f"PR #{pr.number} for {branch} has already merged.\n"
        "  Your commit is NOT in it and has not been pushed: the merge took"
        " the pre-push head.\n"
        "  Start a fresh branch off the merged base; check for a stray PR"
        " before re-running."
    )


def disarm_before_push(repo: Repository, root: Path, branch: str) -> int | None:
    """Disarm an armed pull request before pushing to it.

    A push to an armed pull request races auto-merge: the merge can
    take the pre-push head. The disarm narrows that window; it cannot
    close it, so the merged check runs after it on every path. An
    unreadable arming state must not block the submit; the push itself
    surfaces a real transport problem. Returns what
    livery.workshop._submit.abort_if_merged returns: the number of a
    pull request that already merged this very head, else None.
    """
    try:
        pr = repo.pr.find_by_head(branch)
        if pr is not None and repo.pr.is_armed(pr.number):
            repo.pr.disarm(pr.number)
            print(f"  disarmed PR #{pr.number} before pushing (the race)")
    except ForgeError as exc:
        print(f"  note: could not read the arming state before pushing ({exc})")
    return abort_if_merged(repo, root, branch)


def _arm_verified(
    repo: Repository, number: int, *, title: str, message: str, kind: str = ""
) -> bool:
    """Arm and read back, retrying a silently lost schedule.

    Each arm attempt follows the merge holds first (the arm shares
    the merge endpoint): the two quirks are separate, so each keeps
    its own loop.

    Returns:
        Whether the pull request merged before this returned, rather
        than waiting armed for its checks.
    """
    for attempt in range(1, _ARM_RETRIES + 1):
        try:
            _follow_merge_state(
                repo, kind, number, repo.pr.arm, number, title=title, message=message
            )
        except ForgeError as error:
            # GitHub arms only a pull request that something blocks, so one
            # already green is refused with "in clean status", and it refuses
            # a squash subject it finds too long whatever the state. Merged
            # when green was the intent either way: a running CI is followed
            # to its verdict, and a green one merges now.
            pr = repo.pr.get(number)
            if pr is None or pr.merged or pr.state != "open":
                raise
            state = repo.checks.status(pr.head_sha).state
            if state in ("pending", "none"):
                print(
                    f"  arming refused ({error}); CI is still running, so the"
                    " submit follows it and merges when green"
                )
                _wait_for_verdict(repo, number)
                pr = repo.pr.get(number) or pr
                state = repo.checks.status(pr.head_sha).state
            elif state == "success":
                print(
                    f"  arming refused ({error}); the pull request is already"
                    " green, so it is merged now"
                )
            if state != "success":
                fail(
                    f"PR #{number}: arming was refused ({error}), and CI is {state}:"
                    f" fix it and `{footman.prog()} submit`. A red pull request"
                    " is never merged."
                )
            _follow_merge_state(
                repo,
                kind,
                number,
                repo.pr.merge_now,
                number,
                title=title,
                message=message,
            )
            return True
        pr = repo.pr.get(number)
        if pr is not None and pr.merged:
            return True  # the arm found green checks and merged on the spot
        if repo.pr.is_armed(number):
            if attempt > 1:
                print(f"  armed on attempt {attempt} (the forge lost a schedule)")
            return False
    fail(
        f"PR #{number}: the forge lost the auto-merge schedule"
        f" {_ARM_RETRIES} times; arm it by hand or re-run"
    )


def _arm_and_say(
    repo: Repository, number: int, *, title: str, message: str, kind: str
) -> None:
    """Arm *number* through the verified arm, and print how it ended."""
    merged = _arm_verified(repo, number, title=title, message=message, kind=kind)
    print(
        f"  merged: PR #{number}"
        if merged
        else f"  armed: PR #{number} merges when green"
    )


def _disarm_and_say(repo: Repository, number: int) -> bool:
    """Take auto-merge off *number*, printing that it did; whether one was set."""
    if not repo.pr.disarm(number):
        return False
    print(f"  disarmed PR #{number}")
    return True


def _merge_title(repo: Repository, plan: Plan) -> str:
    """The subject the squash will carry: the pull request's, not HEAD's.

    A defaulted title tracks HEAD, so a bare re-run on an open
    many-commit pull request would silently rewrite the squash subject
    to the last fixup's. A given title wins; otherwise the open pull
    request's existing title does.
    """
    if plan.title_given:
        return plan.title
    try:
        pr = repo.pr.find_by_head(plan.branch)
    except ForgeError:
        return plan.title
    return (pr.title.strip() if pr else "") or plan.title


#: The words git uses when origin holds commits a pushed branch does not.
_BEHIND = ("non-fast-forward", "fetch first", "stale info")


def _behind_origin(refusal: str) -> bool:
    """Whether git refused a push because origin holds commits it lacks.

    A push the forge rejected for its own reasons (``(Internal Server
    Error)``, a rule) also says "rejected", and reading it as this
    would name the wrong cause and the wrong advice.
    """
    return any(words in refusal for words in _BEHIND)


def _push(
    root: Path, branch: str, *, force: bool, repo: Repository | None = None
) -> None:
    """Push *branch*, or force it with a lease and full disclosure.

    With *repo*, the runs still moving for the head the push
    supersedes are cancelled afterwards (livery.workshop._ci_tasks.
    cancel_superseded_runs): a stale head's runs would delay the new
    head's on a shared runner and prove nothing.

    A force is refused on ``workflow/`` branches: recovery reads the
    ref, the branch's commits are the durable record of what was
    prepared, and the engine re-prepares instead of rewriting. Before
    a force, the commits it discards from origin are named: the flag
    is consent to discard those commits, not consent in the abstract
    (the flow fetched already, so the listing is current up to the
    push itself, which the lease guards).
    """
    previous = ""
    if repo is not None:
        try:
            previous = git.remote_head(root, branch)
        except git.GitError:
            previous = ""
    if not force:
        try:
            git.push(root, branch)
        except git.GitError as error:
            if not _behind_origin(str(error)):
                # The forge refused or failed the push for a reason of
                # its own (a server error, a rule): its words, verbatim.
                fail(
                    f"the push of {branch} failed: {error}\n  run"
                    f" `{footman.prog()} submit` again once the forge"
                    " answers; nothing was pushed"
                )
            fail(
                f"the push of {branch} was rejected: origin holds commits"
                " this branch does not. Add the fix as a new commit (the"
                " squash collapses it anyway) and re-submit; or, if you"
                " rewrote history deliberately (a rebase onto the base),"
                f" re-run with `{footman.prog()} submit --force`."
            )
        return
    if branch.startswith("workflow/"):
        fail(
            "a workflow branch is never force-pushed: its commits are the"
            " record recovery reads, and the engine re-prepares instead."
            f" Re-run the workflow verb, or `{footman.prog()} workflow.abort` it."
        )
    if git.remote_head(root, branch):
        discards = git.run(
            root, "log", "--format=%s", f"{branch}..origin/{branch}"
        ).strip()
        if discards:
            listed = "\n".join(f"    - {line}" for line in discards.splitlines())
            print(f"  forcing discards these commits on origin/{branch}:\n{listed}")
        else:
            print("  forcing; origin holds nothing this branch does not")
    try:
        git.push_force(root, branch)
    except git.GitError as error:
        if "stale info" not in str(error) and "rejected" not in str(error):
            raise
        fail(
            f"the lease refused the force: origin/{branch} moved past what"
            f" this clone last saw.\n{error}\n  Fetch to see what arrived"
            " (`git fetch origin`), then decide again and re-run."
        )
    if repo is not None and previous and previous != git.head_sha(root):
        from livery.workshop._ci_tasks import cancel_superseded_runs

        for line in cancel_superseded_runs(repo, previous):
            print(line)


def _required_context_at(root: Path, ref: str) -> str:
    """The contract's required context at *ref*; "" when unreadable."""
    try:
        text = git.run(root, "show", f"{ref}:workshop.toml")
    except git.GitError:
        return ""
    data = tomllib.loads(text)
    return str((data.get("ci") or {}).get("required-context") or "gate")


def _heal_context_rename(
    repo: Repository, root: Path, plan: Plan, *, fix: bool
) -> None:
    """The one order-sensitive transition: a renamed required context.

    The renaming branch produces the new context while protection
    still demands the old, so its PR can never go green and the
    post-merge apply never runs. Submit owns the heal because the
    gate is offline by contract and submit already talks to the
    forge: detected offline from the branch diff, refused teaching
    that protection must move before the merge, healed under --fix
    through the admin ladder with a read-compare keeping re-runs
    quietly green. Between the apply and this PR's merge other
    armed merges park (never fail) on the old context, so the
    teaching recommends `fm submit --fix --armed` to close that
    window at CI speed; --fix never implies --armed.
    """
    ours = _required_context_at(root, plan.branch)
    theirs = _required_context_at(root, f"origin/{plan.base}")
    if not ours or not theirs or ours == theirs:
        return
    from livery.workshop._extensions import workspace_root
    from livery.workshop._forge_lane import this_forge

    heal_root = workspace_root()
    if heal_root is not None and not this_forge(heal_root).supports(
        "required_contexts"
    ):
        # A forge whose protection cannot name contexts has nothing
        # to move: the rename only renames the emitted pipeline job,
        # and the pull request goes green on its own.
        print(
            f"  required context renamed ({theirs!r} -> {ours!r}); this"
            " forge names no contexts in protection, nothing to heal"
        )
        return
    if fix:
        from livery.workshop._forge_lane import admin_repository

        admin_repo, admin_var = admin_repository(root)
        try:
            # Read through the admin ladder too: GitHub gates the
            # protection read on admin, and an unreadable state must
            # not turn the quiet re-run into a re-apply.
            protection = admin_repo.protection(plan.base)
        except ForgeError:
            protection = None

        from livery.workshop._workflow_tasks import required_context_string

        contract = load_contract(root / "workshop.toml")
        kind = str((contract.get("forge") or {}).get("kind", ""))
        spelled = required_context_string(kind, ours)
        if protection is not None and spelled in protection.required_contexts:
            return  # already applied: the re-run is quietly green
        from livery.forge import RepoConfig

        try:
            admin_repo.configure(RepoConfig(required_contexts=(spelled,)))
        except ForgeError as error:
            used = admin_var or "the everyday token"
            fail(
                f"the context rename could not be applied using {used}:\n"
                f"{error}\n  An administrator sets the per-kind admin"
                f" variable and re-runs `{footman.prog()} submit --fix --armed`,"
                " or runs"
                f" `{footman.prog()} workflow.configure` from this branch."
            )
        print(
            f"  protection now requires {ours!r} (was {theirs!r}); other"
            " armed merges park on the old context until this PR lands,"
            " so keep the rename diff minimal and land it fast."
        )
        return
    fail(
        f"this branch renames the required CI context ({theirs!r} ->"
        f" {ours!r}), and protection still demands the old name, so this"
        " PR can never go green until protection moves BEFORE the merge."
        f" Heal and submit in one step: `{footman.prog()} submit --fix --armed` (the"
        " apply parks other armed merges until this lands; --fix never"
        " implies --armed). Without an admin token in reach, an"
        f" administrator runs `{footman.prog()} workflow.configure` from this branch."
    )


def label_every_runner(repo: Repository, number: int) -> None:
    """Put [livery.workshop.EVERY_RUNNER_LABEL][] on pull request *number*.

    The gate then runs every runner for it, where its check legs run on
    fewer. Idempotent: a label the
    pull request already carries stays. A forge that refuses, one
    whose repository does not carry the label among them, fails with
    the forge's words; the pull request exists by then, so a re-run
    labels the same one.
    """
    try:
        repo.pr.add_labels(number, (EVERY_RUNNER_LABEL,))
    except ForgeError as error:
        fail(
            f"PR #{number} exists, but {EVERY_RUNNER_LABEL} could not be set on"
            f" it: {error}\n  `{footman.prog()} workflow.configure` creates the"
            " label on the repository; then re-run"
            f" `{footman.prog()} submit --every-runner`"
        )
    print(
        f"  every runner: labelled {EVERY_RUNNER_LABEL}; the gate runs every"
        " runner for this pull request"
    )


def push_and_pr(
    repo: Repository,
    root: Path,
    plan: Plan,
    *,
    closes: int | None,
    armed: bool,
    force: bool = False,
    every_runner: bool = False,
) -> int:
    """Disarm, push, find-or-open the pull request, label and arm as asked."""
    body = with_closes(plan.body, closes) if closes is not None else plan.body
    merged = disarm_before_push(repo, root, plan.branch)
    if merged is not None:
        # Re-running the submit is its recovery procedure: a head the
        # pull request already merged has nothing to push, open, or
        # arm, and the caller's follow reports the merge.
        print(f"  PR #{merged} already merged this head; nothing to push")
        return merged
    # Found by branch name, so no push is needed first - and the
    # refusal below must run before the push: a refusal that leaves a
    # branch on the remote makes the next submit of a rebuilt branch
    # fail non-fast-forward.
    pr = repo.pr.find_by_head(plan.branch)
    if pr is None:
        _push(root, plan.branch, force=force, repo=repo)
        pr = repo.pr.open(plan.branch, plan.base, plan.title, body)
        print(f"  opened PR #{pr.number}: {pr.title}")
    else:
        _push(root, plan.branch, force=force, repo=repo)
        print(f"  reusing PR #{pr.number}")
        if plan.title_given and pr.title != plan.title:
            repo.pr.update_title(pr.number, plan.title)
            print(f"  title updated: {plan.title}")
        if plan.body_given and pr.body != body:
            repo.pr.update_body(pr.number, body)
            print("  body updated")
    if every_runner:
        label_every_runner(repo, pr.number)
    if armed:
        _arm_and_say(
            repo,
            pr.number,
            title=_merge_title(repo, plan),
            message=body,
            kind=_contract_forge_kind(root),
        )
    return pr.number


def refuse_ambiguous_title(root: Path, plan: Plan) -> None:
    """Refuse a defaulted title on a branch more than one commit ahead.

    A defaulted title is trustworthy only when it is unambiguous: one
    commit ahead, its subject is the intent. More, and whichever
    commit is HEAD would name the pull request, a guess dressed as a
    default, so it refuses instead, listing the subjects. A given
    title never refuses, and a pull request that already exists keeps
    its title, so the caller asks only on a first open.
    """
    if plan.title_given:
        return
    subjects = [
        subject for subject in plan.subjects if not subject.startswith("Merge ")
    ]
    if len(subjects) > 1:
        listed = "\n".join(f"    - {subject}" for subject in subjects)
        fail(
            f"the branch is {len(subjects)} commits ahead, so no"
            " commit subject can default the PR title:\n"
            f"{listed}\n"
            '  pass the intent: --title="type(scope): subject"'
        )


def _restack_before_push(
    root: Path, *, branch: str = "", stack: tuple[str, str] | None = None
) -> bool:
    """Move a stacked branch onto its moved or merged parent; whether it moved.

    The pull request of a branch whose parent merged is retargeted to
    main, and a merge of main into it replays the parent's squash
    against the parent's own commits; the restack moves the branch's
    own commits alone ([livery.workshop._sync.restack][]), so the push
    that follows needs the lease. A dirty tree is left to the gate's
    fold, and a restack with conflicts refuses naming the verb that
    resolves it.
    """
    branch = branch or git.current_branch(root)
    stack = stack if stack is not None else _stack.stack(root, branch)
    parent, _tip = stack
    if not parent or not git.is_clean(root):
        return False
    from livery.workshop._sync import restack

    moved = restack(root, branch, interactive=False, stack=stack)
    if moved is None:
        fail(
            f"{branch} could not follow its parent without conflicts: run"
            f" `{footman.prog()} sync` where a person can resolve them, then"
            f" `{footman.prog()} submit` again"
        )
    return moved


def submit_flow(
    repo: Repository,
    root: Path,
    *,
    title: str = "",
    body: str = "",
    base: str = "main",
    closes: int = 0,
    close: bool = True,
    armed: bool = False,
    armed_reason: str = "",
    gate: bool = True,
    fix: bool = False,
    force: bool = False,
    every_runner: bool = False,
    follow_to_verdict: bool = True,
    interval: float = 15,
    timeout: float = 1800,
) -> int:
    """The whole submitting act; returns the pull request number.

    The task shell resolves the repository, the git seam, and the
    arming ladder; everything after that lives here so tests drive the
    same flow against livery.forge.testing.FakeForge and a temporary
    repository.
    """
    # Every refusal the arguments decide comes before the gate: the
    # branch's name, the title's shape, and whether a title can
    # default at all. A person never pays minutes of gate for a flag
    # they could have passed, and the title is decided from the
    # branch as it is, before a fold adds a commit of its own.
    git.fetch(root)
    branch = git.current_branch(root)
    # The stack, read once for the flow: a restack that moved the branch
    # may have forgotten it, so it is read again after one.
    stack = _stack.stack(root, branch)
    moved = _restack_before_push(root, branch=branch, stack=stack)
    if moved:
        stack = _stack.stack(root, branch)
    force = moved or force
    plan = prepare(root, title=title, body=body, base=base, branch=branch)
    # A first open only: a pull request that exists, open, merged, or
    # closed, keeps its title, and the merged and closed cases get
    # their own refusals further on.
    if repo.pr.find_by_head(plan.branch, state="all") is None:
        if not plan.subjects:
            fail(
                f"{plan.branch} has no commits beyond origin/{plan.base}, so"
                " there is nothing to submit: commit first"
                f' (`{footman.prog()} commit <type> "<subject>"`), then re-run'
                f" `{footman.prog()} submit`"
            )
        refuse_ambiguous_title(root, plan)
    # The check CI's setup makes with `sync --locked`, made before the
    # push and before the gate's minutes: a lock that is not current
    # refuses here, on this machine.
    from livery.workshop._sync import stale_locks

    for problem in stale_locks(root):
        fail(
            f"{problem}. CI's setup refuses a lock that is not current:"
            f" `{footman.prog()} sync` writes it; commit it, then re-run"
            f" `{footman.prog()} submit`"
        )
    if gate:
        _gate(fix, root=root, base=base)
        if fix and not git.is_clean(root):
            _fold_fixes(root, base)
    else:
        print("  gate skipped (--no-gate): CI is now the first verifier")
    _heal_context_rename(repo, root, plan, fix=fix)
    linked = resolve_closes(repo, plan.branch, closes) if close else None
    if linked is not None:
        print(f"  Closes #{linked} on merge")
    elif not close:
        print("  closes nothing (--no-close): the branch's issue stays open")
    parent, _tip = stack
    if armed and parent and plan.base == parent:
        # The forge refuses to arm a pull request into a branch with no
        # required checks, and arming into the parent would land the
        # child there, not on main: the arm waits for the parent's merge.
        armed = False
        print(
            f"  arming: off - {parent} is still open; after it merges, run"
            f" `{footman.prog()} sync` then `{footman.prog()} submit --armed`"
        )
    elif armed_reason:
        print(f"  arming: {'on' if armed else 'off'} - decided by {armed_reason}")
    number = push_and_pr(
        repo,
        root,
        plan,
        closes=linked,
        armed=armed,
        force=force,
        every_runner=every_runner,
    )
    if not follow_to_verdict:
        return number
    heals = 0

    def trace_the_run() -> None:
        """Hand the run just followed to this command's own trace, if any.

        Called at every way out of the watch, a red run included: a timeline
        is wanted most where something went wrong. Nothing is kept in the
        ordinary case, and nothing here can fail the submit.
        """
        if traced := drop_run(repo, root):
            print(f"  {traced}")

    while True:
        try:
            follow_pull_request(
                repo, plan.branch, root, interval=interval, timeout=timeout
            )
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
            if code == EXIT_DISARMED and not armed:
                # The submit was asked not to arm, so a green parked PR is
                # this run finishing its job, not a blocker: prose above
                # already said where it is parked, and the exit is clean.
                # 11 still surfaces on an *armed* submit, where a gone
                # schedule means something interfered.
                print(f"  done: CI is green and PR #{number} awaits your call")
                trace_the_run()
                return number
            if code in (EXIT_CONFLICTS, EXIT_BEHIND) and heals < _MAX_HEALS:
                heals += 1
                print(
                    f"  exit {code}: integrating origin/{plan.base} and"
                    f" re-submitting (self-heal {heals}/{_MAX_HEALS})"
                )
                disarm_before_push(repo, root, plan.branch)
                before = git.head_sha(root)
                try:
                    git.integrate(root, plan.base)
                except git.GitError as exc2:
                    fail(
                        f"the merge stopped on a conflict; resolve it, commit,"
                        f" and re-run `{footman.prog()} submit`:\n{exc2}"
                    )
                # The merge can bring the code this process runs, and the
                # gate must judge the merged tree with the code it carries:
                # the rest is a fresh submit on that code. Re-running the
                # verb is the recovery, so nothing is lost by the handoff.
                from livery.workshop._sync import continue_on_moved_code

                continue_on_moved_code(root, before, git.head_sha(root), verb="submit")
                if gate:
                    _gate(root=root, base=plan.base)
                number = push_and_pr(
                    repo,
                    root,
                    plan,
                    closes=linked,
                    armed=armed,
                    force=force,
                    every_runner=every_runner,
                )
                continue
            trace_the_run()
            raise
        # Before the tidy moves this checkout off the branch, whose head is
        # how the run is found.
        trace_the_run()
        _tidy_after_merge(repo, root, plan.branch, plan.base, number)
        return number


def _gate(fix: bool = False, *, root: Path, base: str = "main") -> None:
    """The local gate; red raises before any network call.

    The chain of this machine's own green gates
    (`livery.workshop._gate_record`) is walked from HEAD's tree: a
    chain that reaches a root, a full gate here or a tree in HEAD's
    history that CI's record holds, proves the tree and the gate is
    skipped, saying so. Otherwise the reflex runs, ``fm check`` as a person
    runs it, which gates the delta from the nearest proved tree and
    records HEAD's tree at its end. *fix* runs format and lint in
    their fix modes, so mechanical findings heal instead of failing;
    the caller folds any rewrites into the branch before pushing.
    """
    from livery.workshop._gate_record import covering
    from livery.workshop._quality import check, say_skipped

    with contextlib.suppress(git.GitError):
        git.fetch(root)
    proof, why = covering(root, base=base)
    if proof is not None:
        by = " by fm check" if proof.steps else ""
        say_skipped(
            f"gate: tree {proof.tree[:12]} proved green{by}"
            f" ({proof.describe()}); skipping"
        )
        return
    print(f"  gate: {why}; running the gate")
    check(fix=fix, base=base)


def _fold_fixes(root: Path, base: str) -> None:
    """Put the gate's rewrites into the branch, consented by ``--fix``.

    Amended into HEAD while the commit is still the branch's own and
    unpushed; otherwise a follow-up commit, which the squash merge
    collapses away. Amending is never applied to a pushed or base
    commit: rewriting either would force-push or rewrite history the
    remote already trusts.
    """
    git.fetch(root)
    branch, head = git.position(root)
    pushed = git.remote_head(root, branch) == head
    if pushed or not git.subjects_ahead(root, base):
        git.commit_all(root, "chore: apply gate fixes")
        print("  gate fixes committed (the squash merge collapses this)")
    else:
        git.amend_all(root)
        print("  gate fixes amended into HEAD")


@submit.default(serial=True)
def submit_default(
    title: Annotated[str, doc("PR title; defaults to HEAD's subject")] = "",
    body: Annotated[str, doc("PR body; defaults to HEAD's body")] = "",
    base: Annotated[str, doc("target branch")] = "main",
    closes: Annotated[int, doc("issue to close on merge; 0 = from branch name")] = 0,
    close: Annotated[bool, doc("close the branch's issue on merge")] = True,
    armed: Annotated[
        bool,
        footman.env("WORKSHOP_AUTOMERGE"),
        footman.default(ci_automerge),
        doc("arm auto-merge (ladder: flag, WORKSHOP_AUTOMERGE, [ci] automerge)"),
    ] = False,
    gate: Annotated[bool, doc(f"run `{footman.prog()} check` first")] = True,
    fix: Annotated[bool, doc("heal mechanical gate findings, fold into HEAD")] = False,
    force: Annotated[
        bool, doc("force-push with a lease after a deliberate history rewrite")
    ] = False,
    every_runner: Annotated[
        bool,
        doc(
            f"label the PR {EVERY_RUNNER_LABEL} so the gate runs every runner: for"
            " a change only another platform reaches while the pull request's"
            " legs run on fewer"
        ),
    ] = False,
    follow: Annotated[bool, doc("watch until it lands or says what stopped it")] = True,
    interval: Annotated[int, doc("watch poll seconds")] = 15,
    timeout: Annotated[int, doc("watch deadline seconds")] = 1800,
) -> None:
    """Gate, push, open-or-reuse the PR, arm, and watch it land.

    Idempotent: re-running it is the recovery procedure. Exits 10 and
    17 self-heal by integrating the base and re-submitting; the other
    verdict codes surface unchanged (see livery.workshop._verdict).
    ``--fix`` runs the gate's format and lint in their fix modes and
    folds any rewrites into the branch before pushing: amended into
    HEAD while unpushed, a follow-up commit otherwise. ``--no-close``
    submits without the close footer, for a branch that carries
    preparatory work on an issue the merge must leave open.
    ``--every-runner`` labels the pull request ``ci:every-runner``, so
    the gate runs every runner for it where its check legs run on
    fewer: for a change to a line only another platform reaches, which
    a narrowed run judges red by name.
    """
    root = _root()
    from livery.workshop._forge_lane import this_repository

    repo = this_repository(root)
    reason = arming_reason(armed=armed, flag_given=footman.given("armed"))
    base = resolve_base(root, base, given=footman.given("base"))
    with forge_failures_named():
        submit_flow(
            repo,
            root,
            title=title,
            body=body,
            base=base,
            closes=closes,
            close=close,
            armed=armed,
            armed_reason=reason,
            gate=gate,
            force=force,
            fix=fix,
            every_runner=every_runner,
            follow_to_verdict=follow,
            interval=interval,
            timeout=timeout,
        )


@contextlib.contextmanager
def forge_failures_named() -> Generator[None]:
    """Fail with the forge's own words when it refuses or does not answer.

    A submit is safe to run again, so a read that timed out or a write
    the forge refused ends the run with the forge's message and that
    advice, not a traceback.
    """
    try:
        yield
    except ForgeError as error:
        fail(
            f"{error}\n  `{footman.prog()} submit` is safe to run again: it"
            " starts from what the forge already has"
        )


def worktree_for(root: Path, branch: str) -> str:
    """The linked worktree holding *branch*; "" when none does or *root* does."""
    listing = git.run(root, "worktree", "list", "--porcelain")
    tree = ""
    current: dict[str, str] = {}
    for line in [*listing.splitlines(), ""]:
        if not line.strip():
            if current.get("branch", "").endswith(f"refs/heads/{branch}"):
                tree = current.get("worktree", "")
            current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    if tree and Path(tree).resolve() == root.resolve():
        return ""
    return tree


def only_local_work(root: Path, branch: str, *, merged_head: str = "") -> str:
    """Why *branch* holds work that exists nowhere else; "" when safe.

    The one keep-or-drop rule: ``fm issue.close``, ``fm issue.stop``,
    the submit's own teardown after a merge, the sweep, and ``fm sync``
    all ask it, so what they destroy is decided in one place. Dirt is
    looked for where the branch actually lives: this checkout when it
    stands on the branch, the branch's linked worktree otherwise.

    *merged_head* is the head a merged pull request took. A tip equal
    to it holds nothing only here: the merge took every commit, and
    the squash rewrote them, so the remote's ancestry says nothing
    (after a squash the branch's commits are never ancestors of the
    base, and the forge deletes the remote branch). A tip past it
    holds the commits made after the merge. A tip the merged head
    does not reach was rewritten after the merge, and the ancestry
    rule below judges it. Without a merged head, ahead is measured
    against the remote branch when one exists, and against the base
    when none does.
    """
    if git.current_branch(root) == branch and not git.is_clean(root):
        return "uncommitted changes"
    tree = worktree_for(root, branch)
    if tree:
        status = git.run(root, "-C", tree, "status", "--porcelain").strip()
        if status:
            return f"uncommitted changes in the worktree {tree}"
    if merged_head:
        tip = git.object_id(root, f"refs/heads/{branch}")
        if tip == merged_head:
            return ""
        if git.is_ancestor(root, merged_head, tip):
            count = git.run(
                root, "rev-list", "--count", f"{merged_head}..{tip}"
            ).strip()
            return f"{count} commit(s) past the merged head"
    remote = f"origin/{branch}"
    upstream = remote if branch in git.remote_branches(root, "") else "origin/main"
    count = git.run(root, "rev-list", "--count", f"{upstream}..{branch}").strip()
    if count.isdigit() and int(count) > 0:
        where = "the remote branch" if upstream == remote else "any remote"
        return f"{count} commit(s) not on {where}"
    return ""


def merged_pull_request(
    repo: Repository, root: Path, branch: str
) -> PullRequest | None:
    """The merged pull request *branch* was the head of, or None.

    [livery.workshop._forge_lane.pull_request_for][] finds it, by head
    branch and then by the branch's head commit; a forge that cannot
    be asked raises.
    """
    from livery.workshop._forge_lane import pull_request_for

    pr = pull_request_for(repo, root, branch)
    return pr if pr is not None and pr.merged else None


def _tidy_after_merge(
    repo: Repository, root: Path, branch: str, base: str, number: int
) -> None:
    """Remove the linked worktree a merged submission stood in.

    The submit is the flow that opened the tree (through ``fm start``),
    so it removes the tree when the follow sees the merge, the way
    ``fm issue.close`` does from inside one; the janitor is the net
    for a submit that was not followed to the end. A tree that holds
    something the merge did not take is kept and named. The main
    checkout keeps its branch: a person reads the run's logs from it,
    and the janitor's branch rule drops it later.

    The janitor runs with any ``fm`` command, so another one may sweep
    the tree once its branch merged, before or while this tidy runs.
    That leaves nothing to do: the tidy says so and moves the process
    out of the directory that is gone. A missing executable is not
    that, and still raises.
    """
    try:
        _tidy_tree(repo, root, branch, base, number)
    except (FileNotFoundError, NotADirectoryError):
        # A process started in a directory that is gone raises
        # FileNotFoundError on POSIX and NotADirectoryError on Windows.
        if root.is_dir():
            raise
        _swept(root)


def _swept(root: Path) -> None:
    """Say the tree at *root* was swept already, standing the process somewhere real."""
    print(f"  the worktree {root} is gone already: swept after the merge")
    home = root.parent
    while not home.is_dir() and home != home.parent:
        home = home.parent
    os.chdir(home)


def _tidy_tree(
    repo: Repository, root: Path, branch: str, base: str, number: int
) -> None:
    """The tidy itself, for a tree that may vanish under it."""
    if not root.is_dir():
        _swept(root)
        return
    try:
        git_dir = git.run(root, "rev-parse", "--git-dir").strip()
        common_dir = git.run(root, "rev-parse", "--git-common-dir").strip()
    except git.GitError:
        return  # no checkout to speak of, so no linked worktree to remove
    if git_dir == common_dir:
        return
    pr = repo.pr.get(number)
    merged_head = pr.head_sha if pr is not None and pr.merged else ""
    why = only_local_work(root, branch, merged_head=merged_head)
    if why:
        print(
            f"  kept the worktree {root}: it holds {why};"
            f" `{footman.prog()} issue.close --discard` or"
            f" `{footman.prog()} abandon` removes it"
        )
        return
    teardown_branch(repo, root, branch, base)


def teardown_branch(
    repo: Repository,
    root: Path,
    branch: str,
    base: str,
) -> None:
    """The one branch teardown every stop verb wears; idempotent.

    Disarm, close the PR (a merged one is left alone), delete the
    remote and local branch, and step back onto an up-to-date *base*
    when standing on *branch*. Mechanism only: state gates, forces,
    and refusals are the calling policy's job
    (livery.workshop._submit.abandon_flow for a feature,
    ``workflow.abort`` for a reserved workflow), so the policies can
    never drift apart. Removing a
    linked worktree moves the process into the main checkout first:
    Windows refuses to remove a directory a process stands in, and
    the shell that ran the verb is told where to go. A real move of
    the process directory is a serial task's to make (footman refuses
    it in a parallel one), so every verb that reaches this from
    inside a worktree is declared ``serial``.
    """
    pr = repo.pr.find_by_head(branch)
    if pr is not None and not pr.merged:
        _disarm_and_say(repo, pr.number)
        repo.pr.close(pr.number)
        print(f"  closed PR #{pr.number}")
    if repo.branch_exists(branch):
        repo.delete_branch(branch)
        print(f"  deleted origin/{branch}")
    git_dir = git.run(root, "rev-parse", "--git-dir").strip()
    common_dir = git.run(root, "rev-parse", "--git-common-dir").strip()
    standing = git.current_branch(root) == branch
    if standing and git_dir != common_dir:
        # A linked worktree cannot switch to *base*: the main
        # checkout holds it. The worktree itself is the leftover, so
        # it is removed from the main checkout, branch and all. The
        # shell that ran this stands in a deleted directory afterwards
        # and is told where to go.
        import os

        main_root = git.main_checkout(root)
        os.chdir(main_root)
        git.run(main_root, "worktree", "remove", "--force", str(root))
        if git.local_branch_exists(main_root, branch):
            git.delete_branch(main_root, branch)
        print(f"  removed the worktree {root} and deleted {branch}")
        print(f"  this shell's directory is gone: cd {main_root}")
        return
    if standing:
        git.switch(root, base)
        git.integrate(root, base)
    else:
        tree = worktree_for(root, branch)
        if tree:
            git.run(root, "worktree", "remove", "--force", tree)
            print(f"  removed the worktree {tree}")
    if git.local_branch_exists(root, branch):
        git.delete_branch(root, branch)
        # Only a checkout that stood on the branch moved to the base.
        print(f"  deleted {branch}" + (f"; back on {base}" if standing else ""))


def abandon_flow(repo: Repository, root: Path, branch: str, base: str) -> None:
    """Give the feature up: close the PR, delete both branches; idempotent.

    A dirty tree refuses before anything else moves: abandoning
    deletes the branch, and uncommitted work must be a person's
    explicit loss, not a side effect.
    """
    if not branch or branch == base:
        fail(f"not on a feature branch (on {branch or '(detached)'!r})")
    if branch == git.current_branch(root):
        if not git.is_clean(root):
            fail(
                "the working tree has uncommitted changes; commit or discard"
                " them before abandoning"
            )
    else:
        tree = worktree_for(root, branch)
        if tree and git.run(root, "-C", tree, "status", "--porcelain").strip():
            fail(
                f"the worktree {tree} has uncommitted changes; commit or"
                " discard them before abandoning"
            )
    pr = repo.pr.find_by_head(branch)
    if pr is not None and pr.merged:
        fail(f"PR #{pr.number} already merged: nothing to abandon")
    if pr is None:
        print(f"  no open pull request for {branch}")
    teardown_branch(repo, root, branch, base)


@footman.task(serial=True)
def abandon(
    branch: Annotated[Arg[str], doc("a branch to give up; this one when absent")] = "",
) -> None:
    """Give up a feature: close the PR, delete the branches, return to base.

    This branch when none is named, or the named one from anywhere:
    its linked worktree goes with it. Disarms and closes the pull
    request, deletes the remote and the local branch, and leaves this
    checkout on an up-to-date base when it stood on the branch. A
    dirty tree, here or in the branch's worktree, refuses first.
    Idempotent: a second run finds nothing left to undo.
    """
    root = _root()
    from livery.workshop._forge_lane import this_repository

    abandon_flow(
        this_repository(root), root, branch or git.current_branch(root), "main"
    )


def pull_request_named(repo: Repository, root: Path, ref: str) -> PullRequest:
    """Find the pull request *ref* numbers; this branch's when *ref* is empty.

    *ref* is read by [livery.workshop._submit.parse_number][], the
    rule the issue verbs read an issue number by, so ``#123`` and
    ``123`` name the same pull request. On the current branch the open
    pull request is looked up by the branch's name, and when there is
    none, a merged one by the branch's head commit, which outlives a
    head branch the merge deleted: after a merge, the answer is that
    it merged. Every other answer fails and names the next action: a
    *ref* that is no number, a number no pull request has, a branch
    without an open or merged pull request, and a pull request closed
    without merging.

    Args:
        repo: The workspace's repository on its forge.
        root: The checkout whose current branch an empty *ref* means.
        ref: A pull request number, with or without ``#``; empty for
            the current branch's.

    Returns:
        The pull request, open or merged. What a merged one means is
        the verb's to decide.
    """
    prog = footman.prog()
    if ref:
        number = parse_number(ref)
        if number is None:
            fail(
                f"{ref!r} is not a pull request number: name one, 123 or"
                " #123, or none for this branch's"
            )
        pr = repo.pr.get(number)
        if pr is None:
            fail(f"no pull request #{number} on the forge: check the number")
    else:
        branch = git.current_branch(root)
        if not branch:
            fail("HEAD is detached, so no branch names a pull request: name its number")
        pr = repo.pr.find_by_head(branch)
        if pr is None:
            done = repo.pr.find_by_head_sha(git.head_sha(root))
            pr = done if done is not None and done.merged else None
        if pr is None:
            fail(
                f"no open pull request for {branch}: `{prog} submit` opens"
                " one, or name a pull request's number"
            )
    if pr.state != "open" and not pr.merged:
        fail(
            f"PR #{pr.number} is closed: `{prog} submit` on {pr.head_branch}"
            " opens a new one"
        )
    return pr


def _merged_already(pr: PullRequest) -> bool:
    """Whether *pr* has merged, printing that nothing is left to do when it has."""
    if pr.merged:
        print(f"  PR #{pr.number} has already merged; nothing to do")
    return pr.merged


def arm_flow(repo: Repository, root: Path, pr: PullRequest) -> None:
    """Arm *pr* to merge when green, as ``fm submit --armed`` arms; idempotent.

    The squash takes the pull request's own title and body. An armed
    pull request is left as it is, and a merged one is the end the arm
    aims at: each line says so, and neither is an error.
    """
    if _merged_already(pr):
        return
    if repo.pr.is_armed(pr.number):
        print(f"  PR #{pr.number} is armed already; nothing changed")
        return
    _arm_and_say(
        repo,
        pr.number,
        title=pr.title.strip(),
        message=pr.body,
        kind=_contract_forge_kind(root),
    )


def disarm_flow(repo: Repository, pr: PullRequest) -> None:
    """Take auto-merge off *pr* and leave it open; idempotent.

    An unarmed pull request is left as it is, and the line says so. A
    merged one refuses: the merge the disarm was to prevent has
    happened.
    """
    if pr.merged:
        fail(f"PR #{pr.number} has already merged: too late to disarm")
    if not _disarm_and_say(repo, pr.number):
        print(f"  PR #{pr.number} is not armed; nothing changed")


def merge_flow(
    repo: Repository, root: Path, pr: PullRequest, *, title: str = ""
) -> None:
    """Merge the pull request *pr* as soon as possible; a merged one is done.

    Merge means merge asap, never merge-if-instant-else-fail: checks
    still running are followed to their verdict, the forge's own
    holds (the mergeability recompute, the settling beat) are
    followed to completion, and the task's timeout is the only
    backstop. Red refuses at once naming the state, behind base
    teaches integrate, and merging red stays a person's act in the
    forge's own interface.

    Whether *pr* is behind its base is judged at HEAD when *pr* is
    this branch's pull request, since HEAD is what the next submit
    pushes, and at the head the forge holds for any other, which the
    fetch brings here. A head this clone cannot see is left to the
    forge's own refusal.
    """
    if _merged_already(pr):
        return
    own = pr.head_branch == git.current_branch(root)
    _wait_for_verdict(repo, pr.number, local_head=git.head_sha(root) if own else "")
    # The wait follows a head that moves, so the verdict is read for
    # the head it settled on, not the one *pr* was read with.
    pr = repo.pr.get(pr.number) or pr
    status = repo.checks.status(pr.head_sha)
    if status.state == "failure":
        fail(
            f"CI is red for PR #{pr.number}: fix it and `{footman.prog()} submit`."
            " This verb never merges red."
        )
    git.fetch(root)
    head = "HEAD" if own else git.sha_of(root, pr.head_sha)
    if head and not git.is_ancestor(root, f"origin/{pr.base_branch}", head):
        where = "" if own else f" on {pr.head_branch},"
        fail(
            f"PR #{pr.number} is behind {pr.base_branch}:{where}"
            f" `{footman.prog()} integrate`, then `{footman.prog()} submit`,"
            " and merge again"
        )
    subject = title or pr.title
    _follow_merge_state(
        repo,
        _contract_forge_kind(root),
        pr.number,
        repo.pr.merge_now,
        pr.number,
        title=subject,
    )
    print(f"  merged PR #{pr.number}")


def _named(ref: str) -> tuple[Repository, Path, PullRequest]:
    """The workspace's repository, its root, and the pull request *ref* names."""
    root = _root()
    from livery.workshop._forge_lane import this_repository

    repo = this_repository(root)
    return repo, root, pull_request_named(repo, root, ref)


#: The help text of the pull request argument the three verbs share.
_PR_DOC = "a pull request number; this branch's when absent"


@submit.task(name="merge")
def submit_merge(
    pr: Annotated[Arg[str], doc(_PR_DOC)] = "",
    title: Annotated[str, doc("squash subject; defaults to the PR title")] = "",
) -> None:
    """Merge a PR as soon as possible: this branch's, or the one numbered.

    Waits for a running CI's verdict and follows the forge's own
    holds to completion; refuses red or behind-base at once, saying
    why, and refuses a pull request closed without merging. A pull
    request that has merged already is done: the verb says so and
    succeeds, so a re-run is the recovery. A merge that lands while it
    runs is success too, as livery.forge.PullRequests.merge_now reads
    one.
    """
    repo, root, found = _named(pr)
    merge_flow(repo, root, found, title=title)


@submit.task(name="arm")
def submit_arm(pr: Annotated[Arg[str], doc(_PR_DOC)] = "") -> None:
    """Arm a PR to merge when green: this branch's, or the one numbered.

    Arms as ``fm submit --armed`` arms, with the pull request's own
    title as the squash subject, and reads the schedule back. A pull
    request already green is merged at once, since a forge that arms
    only a blocked pull request refuses. An armed one is left as it
    is, and a merged one is done: the verb says so and succeeds, also
    on the branch once its head branch is gone. One closed without
    merging refuses.
    """
    repo, root, found = _named(pr)
    arm_flow(repo, root, found)


@submit.task(name="disarm")
def submit_disarm(pr: Annotated[Arg[str], doc(_PR_DOC)] = "") -> None:
    """Take auto-merge off a PR and leave it open: this branch's, or the one numbered.

    The pull request and its branch stay as they are, so it waits
    unmerged until ``fm submit.arm`` arms it again. An unarmed one is
    left as it is. A merged one refuses, since the merge the disarm
    was to prevent has happened, and so does one closed without
    merging.
    """
    repo, _root_dir, found = _named(pr)
    disarm_flow(repo, found)
