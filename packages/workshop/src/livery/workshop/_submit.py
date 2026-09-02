"""``fm submit``: get the branch onto the remote, verified, and landed.

Local gate first (a red gate costs zero network calls), then the
closes-link resolution with existence probes, disarm-before-push,
push, find-or-open with the title rules, arm per the arming ladder,
and follow until it lands or says what stopped it. Exits 10 and 17 self-heal by
integrating the base and re-submitting; every other code surfaces
unchanged, because skills, hooks, and humans branch on them.

``fm abandon`` is the exit: disarm, close the pull request, delete
both branches, return to the base. ``fm submit.merge`` merges a
green pull request that was deliberately left unarmed, and refuses
anything less than green.

The arming ladder, highest wins: ``--armed``/``--no-armed`` for one
invocation; ``WORKSHOP_AUTOMERGE`` as the per-user standing preference;
the workspace contract's ``[ci] automerge`` as committed repo policy;
off. The ladder is footman's option ladder; only the printed reason
is derived here.
"""

from __future__ import annotations

import os
import re
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import footman
from footman import doc, fail, group

from livery.forge import ForgeError, Repository
from livery.workshop._conventional import TITLE_RE, TYPES
from livery.workshop._git_ops import GitError, GitOps
from livery.workshop._layers import workspace_root
from livery.workshop._verdict import (
    EXIT_BEHIND,
    EXIT_CONFLICTS,
    EXIT_DISARMED,
    follow,
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

#: Attempts through the forge's mergeability-recompute window, where
#: an immediate merge answers 405.
_MERGE_NOW_ATTEMPTS = 5


def _root() -> Path:
    root = workspace_root()
    if root is None:
        fail("no workspace: no livery.toml above the working directory")
    return root


def ci_automerge() -> bool:
    """The committed repo policy: the workspace contract's ``[ci] automerge``."""
    root = workspace_root()
    if root is None:
        return False
    contract = tomllib.loads((root / "livery.toml").read_text("utf-8"))
    return bool((contract.get("ci") or {}).get("automerge", False))


def arming_reason(*, armed: bool, flag_given: bool) -> str:
    """One sentence naming the ladder level that decided *armed*."""
    if flag_given:
        return f"--{'' if armed else 'no-'}armed (this invocation)"
    if os.environ.get("WORKSHOP_AUTOMERGE") is not None:
        return "WORKSHOP_AUTOMERGE (per-user standing preference)"
    root = workspace_root()
    if root is not None:
        contract = tomllib.loads((root / "livery.toml").read_text("utf-8"))
        if "automerge" in (contract.get("ci") or {}):
            return "[ci] automerge in livery.toml (committed repo policy)"
    return "the default (auto-merge is opt-in; nothing configured)"


def branch_issue(branch: str) -> int | None:
    """The issue number a ``<type>/<number>-slug`` branch carries, or None."""
    match = _BRANCH_ISSUE_RE.match(branch)
    return int(match.group(1)) if match else None


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
    """

    branch: str
    base: str
    title: str
    body: str
    title_given: bool


def prepare(
    git: GitOps, *, title: str = "", body: str = "", base: str = "main"
) -> Plan:
    """Validate the branch and assemble the pull request shape."""
    branch = git.current_branch()
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
    default_title = git.head_subject()
    if not title and default_title.startswith("Merge "):
        real = [
            subject
            for subject in git.subjects_ahead(base)
            if not subject.startswith("Merge ")
        ]
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
        body=body or git.head_body(),
        title_given=bool(title),
    )


def abort_if_merged(repo: Repository, git: GitOps, branch: str) -> None:
    """Stop before pushing into a pull request that already merged.

    The merge took the pre-push head, so pushing on would open a
    second pull request carrying the follow-up. Refusing leaves the
    commit unpushed and the situation legible. Detection is
    best-effort: a merged pull request whose head branch is already
    deleted is found by head sha instead, and one invisible both ways
    surfaces later as the stray-PR symptom the message names.
    """
    pr = repo.pr.find_by_head(branch, state="all")
    if pr is None or not pr.merged:
        return
    fail(
        f"PR #{pr.number} for {branch} has already merged.\n"
        "  Your commit is NOT in it and has not been pushed: the merge took"
        " the pre-push head.\n"
        "  Start a fresh branch off the merged base; check for a stray PR"
        " before re-running."
    )


def disarm_before_push(repo: Repository, git: GitOps, branch: str) -> None:
    """Disarm an armed pull request before pushing to it.

    A push to an armed pull request races auto-merge: the merge can
    take the pre-push head. The disarm narrows that window; it cannot
    close it, so the merged check runs after it on every path. An
    unreadable arming state must not block the submit; the push itself
    surfaces a real transport problem.
    """
    try:
        pr = repo.pr.find_by_head(branch)
        if pr is not None and repo.pr.is_armed(pr.number):
            repo.pr.disarm(pr.number)
            print(f"  disarmed PR #{pr.number} before pushing (the race)")
    except ForgeError as exc:
        print(f"  note: could not read the arming state before pushing ({exc})")
    abort_if_merged(repo, git, branch)


def _arm_verified(repo: Repository, number: int, *, title: str, message: str) -> None:
    """Arm and read back, retrying a silently lost schedule."""
    for attempt in range(1, _ARM_RETRIES + 1):
        repo.pr.arm(number, title=title, message=message)
        pr = repo.pr.get(number)
        if pr is not None and pr.merged:
            return  # the arm found green checks and merged on the spot
        if repo.pr.is_armed(number):
            if attempt > 1:
                print(f"  armed on attempt {attempt} (the forge lost a schedule)")
            return
    fail(
        f"PR #{number}: the forge lost the auto-merge schedule"
        f" {_ARM_RETRIES} times; arm it by hand or re-run"
    )


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


def _push(git: GitOps, branch: str, *, force: bool) -> None:
    """Push *branch*, or force it with a lease and full disclosure.

    A force is refused on ``workflow/`` branches: recovery reads the
    ref, the branch's commits are the durable record of what was
    prepared, and the engine re-prepares instead of rewriting. Before
    a force, the commits it discards from origin are named: the flag
    is consent to discard those commits, not consent in the abstract
    (the flow fetched already, so the listing is current up to the
    push itself, which the lease guards).
    """
    if not force:
        try:
            git.push(branch)
        except GitError as error:
            if "non-fast-forward" not in str(error) and "rejected" not in str(error):
                raise
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
    if git.remote_head(branch):
        discards = git._run("log", "--format=%s", f"{branch}..origin/{branch}").strip()
        if discards:
            listed = "\n".join(f"    - {line}" for line in discards.splitlines())
            print(f"  forcing discards these commits on origin/{branch}:\n{listed}")
        else:
            print("  forcing; origin holds nothing this branch does not")
    try:
        git.push_force(branch)
    except GitError as error:
        if "stale info" not in str(error) and "rejected" not in str(error):
            raise
        fail(
            f"the lease refused the force: origin/{branch} moved past what"
            f" this clone last saw.\n{error}\n  Fetch to see what arrived"
            " (`git fetch origin`), then decide again and re-run."
        )


def _required_context_at(git: GitOps, ref: str) -> str:
    """The contract's required context at *ref*; "" when unreadable."""
    try:
        text = git._run("show", f"{ref}:livery.toml")
    except GitError:
        return ""
    data = tomllib.loads(text)
    return str((data.get("ci") or {}).get("required_context") or "gate")


def _heal_context_rename(
    repo: Repository, git: GitOps, plan: Plan, *, fix: bool
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
    ours = _required_context_at(git, plan.branch)
    theirs = _required_context_at(git, f"origin/{plan.base}")
    if not ours or not theirs or ours == theirs:
        return
    if fix:
        from livery.workshop._forge_lane import admin_repository
        from livery.workshop._layers import workspace_root

        root = workspace_root()
        if root is None:
            fail(
                "the context rename needs the workspace contract and no"
                " livery.toml is above the working directory; run"
                f" `{footman.prog()} submit --fix` from inside the workspace"
            )
        admin_repo, admin_var = admin_repository(root)
        try:
            # Read through the admin ladder too: GitHub gates the
            # protection read on admin, and an unreadable state must
            # not turn the quiet re-run into a re-apply.
            protection = admin_repo.protection(plan.base)
        except ForgeError:
            protection = None
        if protection is not None and ours in protection.required_contexts:
            return  # already applied: the re-run is quietly green
        from livery.forge import RepoConfig

        try:
            admin_repo.configure(RepoConfig(required_contexts=(ours,)))
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


def push_and_pr(
    repo: Repository,
    git: GitOps,
    plan: Plan,
    *,
    closes: int | None,
    armed: bool,
    force: bool = False,
) -> int:
    """Disarm, push, find-or-open the pull request, arm per *armed*."""
    body = with_closes(plan.body, closes) if closes is not None else plan.body
    disarm_before_push(repo, git, plan.branch)
    # Found by branch name, so no push is needed first - and the
    # refusal below must run before the push: a refusal that leaves a
    # branch on the remote makes the next submit of a rebuilt branch
    # fail non-fast-forward.
    pr = repo.pr.find_by_head(plan.branch)
    if pr is None:
        # A defaulted title is trustworthy only when it is unambiguous:
        # one commit ahead, its subject is the intent. More, and
        # whichever commit is HEAD would name the pull request - a
        # guess dressed as a default, so it refuses instead (the
        # recurring mis-title hse lived with). Re-submits never enter
        # this branch, where the default is inert anyway.
        if not plan.title_given:
            subjects = [
                subject
                for subject in git.subjects_ahead(plan.base)
                if not subject.startswith("Merge ")
            ]
            if len(subjects) > 1:
                listed = "\n".join(f"    - {subject}" for subject in subjects)
                fail(
                    f"the branch is {len(subjects)} commits ahead, so no"
                    " commit subject can default the PR title:\n"
                    f"{listed}\n"
                    '  pass the intent: --title="type(scope): subject"'
                )
        _push(git, plan.branch, force=force)
        pr = repo.pr.open(plan.branch, plan.base, plan.title, body)
        print(f"  opened PR #{pr.number}: {pr.title}")
    else:
        _push(git, plan.branch, force=force)
        print(f"  reusing PR #{pr.number}")
        if plan.title_given and pr.title != plan.title:
            repo.pr.update_title(pr.number, plan.title)
            print(f"  title updated: {plan.title}")
    if armed:
        _arm_verified(repo, pr.number, title=_merge_title(repo, plan), message=body)
        print(f"  armed: PR #{pr.number} merges when green")
    return pr.number


def submit_flow(
    repo: Repository,
    git: GitOps,
    *,
    title: str = "",
    body: str = "",
    base: str = "main",
    closes: int = 0,
    armed: bool = False,
    armed_reason: str = "",
    gate: bool = True,
    fix: bool = False,
    force: bool = False,
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
    if gate:
        _gate(fix)
        if fix and not git.is_clean():
            _fold_fixes(git, base)
    else:
        print("  gate skipped (--no-gate): CI is now the first verifier")
    git.fetch()
    plan = prepare(git, title=title, body=body, base=base)
    _heal_context_rename(repo, git, plan, fix=fix)
    linked = resolve_closes(repo, plan.branch, closes)
    if linked is not None:
        print(f"  Closes #{linked} on merge")
    if armed_reason:
        print(f"  arming: {'on' if armed else 'off'} - decided by {armed_reason}")
    number = push_and_pr(repo, git, plan, closes=linked, armed=armed, force=force)
    if not follow_to_verdict:
        return number
    heals = 0
    while True:
        try:
            follow(repo, plan.branch, git, interval=interval, timeout=timeout)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
            if code == EXIT_DISARMED and not armed:
                # The submit was asked not to arm, so a green parked PR is
                # this run finishing its job, not a blocker: prose above
                # already said where it is parked, and the exit is clean.
                # 11 still surfaces on an *armed* submit, where a gone
                # schedule means something interfered.
                print(f"  done: CI is green and PR #{number} awaits your call")
                return number
            if code in (EXIT_CONFLICTS, EXIT_BEHIND) and heals < _MAX_HEALS:
                heals += 1
                print(
                    f"  exit {code}: integrating origin/{plan.base} and"
                    f" re-submitting (self-heal {heals}/{_MAX_HEALS})"
                )
                disarm_before_push(repo, git, plan.branch)
                try:
                    git.integrate(plan.base)
                except GitError as exc2:
                    fail(
                        f"the merge stopped on a conflict; resolve it, commit,"
                        f" and re-run `{footman.prog()} submit`:\n{exc2}"
                    )
                if gate:
                    _gate()
                number = push_and_pr(
                    repo, git, plan, closes=linked, armed=armed, force=force
                )
                continue
            raise
        return number


def _gate(fix: bool = False) -> None:
    """The local gate; red raises before any network call.

    *fix* runs format and lint in their fix modes, so mechanical
    findings heal instead of failing; the caller folds any rewrites
    into the branch before pushing.
    """
    from livery.workshop._quality import check

    check(fix=fix)


def _fold_fixes(git: GitOps, base: str) -> None:
    """Put the gate's rewrites into the branch, consented by ``--fix``.

    Amended into HEAD while the commit is still the branch's own and
    unpushed; otherwise a follow-up commit, which the squash merge
    collapses away. Amending is never applied to a pushed or base
    commit: rewriting either would force-push or rewrite history the
    remote already trusts.
    """
    git.fetch()
    branch = git.current_branch()
    pushed = git.remote_head(branch) == git.head_sha()
    if pushed or not git.subjects_ahead(base):
        git.commit_all("chore: apply gate fixes")
        print("  gate fixes committed (the squash merge collapses this)")
    else:
        git.amend_all()
        print("  gate fixes amended into HEAD")


@submit.default
def submit_default(
    title: Annotated[str, doc("PR title; defaults to HEAD's subject")] = "",
    body: Annotated[str, doc("PR body; defaults to HEAD's body")] = "",
    base: Annotated[str, doc("target branch")] = "main",
    closes: Annotated[int, doc("issue to close on merge; 0 = from branch name")] = 0,
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
    HEAD while unpushed, a follow-up commit otherwise.
    """
    root = _root()
    from livery.workshop._forge_lane import this_repository

    repo = this_repository(root)
    reason = arming_reason(armed=armed, flag_given=footman.given("armed"))
    submit_flow(
        repo,
        GitOps(root),
        title=title,
        body=body,
        base=base,
        closes=closes,
        armed=armed,
        armed_reason=reason,
        gate=gate,
        force=force,
        fix=fix,
        follow_to_verdict=follow,
        interval=interval,
        timeout=timeout,
    )


def teardown_branch(
    repo: Repository,
    git: GitOps,
    branch: str,
    base: str,
    *,
    keep_branches: bool = False,
) -> None:
    """The one branch teardown every stop verb wears; idempotent.

    Disarm, close the PR (a merged one is left alone), delete the
    remote and local branch, and step back onto an up-to-date *base*
    when standing on *branch*. *keep_branches* stops after the PR:
    the submission ends, both branches stay. Mechanism only: state
    gates, forces, and refusals are the calling policy's job
    (livery.workshop._submit.abandon_flow for a feature,
    ``workflow.abort`` for a reserved workflow, ``issue.close`` for
    an issue), so the policies can never drift apart.
    """
    pr = repo.pr.find_by_head(branch)
    if pr is not None and not pr.merged:
        if repo.pr.is_armed(pr.number):
            repo.pr.disarm(pr.number)
            print(f"  disarmed PR #{pr.number}")
        repo.pr.close(pr.number)
        print(f"  closed PR #{pr.number}")
    if keep_branches:
        return
    if repo.branch_exists(branch):
        repo.delete_branch(branch)
        print(f"  deleted origin/{branch}")
    if git.current_branch() == branch:
        git.switch(base)
        git.integrate(base)
    if git.local_branch_exists(branch):
        git.delete_local_branch(branch)
        print(f"  deleted {branch}; back on {base}")


def abandon_flow(repo: Repository, git: GitOps, branch: str, base: str) -> None:
    """Give the feature up: close the PR, delete both branches; idempotent.

    A dirty tree refuses before anything else moves: abandoning
    deletes the branch, and uncommitted work must be a person's
    explicit loss, not a side effect.
    """
    if not branch or branch == base:
        fail(f"not on a feature branch (on {branch or '(detached)'!r})")
    if not git.is_clean():
        fail(
            "the working tree has uncommitted changes; commit or discard"
            " them before abandoning"
        )
    pr = repo.pr.find_by_head(branch)
    if pr is not None and pr.merged:
        fail(f"PR #{pr.number} already merged: nothing to abandon")
    if pr is None:
        print(f"  no open pull request for {branch}")
    teardown_branch(repo, git, branch, base)


@footman.task
def abandon() -> None:
    """Give up this feature: close the PR, delete the branches, return to base.

    Disarms and closes the pull request, deletes the remote and the
    local branch, and leaves you on an up-to-date base. A dirty tree
    refuses first. Idempotent: a second run finds nothing left to
    undo.
    """
    root = _root()
    from livery.workshop._forge_lane import this_repository

    git = GitOps(root)
    abandon_flow(this_repository(root), git, git.current_branch(), "main")


def merge_flow(repo: Repository, git: GitOps, branch: str, *, title: str = "") -> None:
    """Merge the branch's green pull request, riding out the 405 window.

    The verb for a deliberately-unarmed pull request whose CI already
    passed. Anything less than green refuses with the reason: red
    names the failing job, pending says wait, behind base says
    integrate. There is no force; merging red stays a person's act in
    the forge's own interface.
    """
    pr = repo.pr.find_by_head(branch)
    if pr is None:
        fail(f"no open pull request for {branch}")
    status = repo.checks.status(pr.head_sha)
    if status.state == "failure":
        fail(
            f"CI is red for PR #{pr.number}: fix it and `{footman.prog()} submit`."
            " Merging red is the forge UI's decision, not this verb's."
        )
    if status.state in ("pending", "none"):
        fail(
            f"CI is {status.state} for PR #{pr.number}: wait for the verdict"
            f" or `{footman.prog()} status --watch`"
        )
    git.fetch()
    if git.behind_base(pr.base_branch):
        fail(
            f"PR #{pr.number} is behind {pr.base_branch}: `{footman.prog()} submit` to"
            " integrate and re-verify first"
        )
    subject = title or pr.title
    for attempt in range(1, _MERGE_NOW_ATTEMPTS + 1):
        try:
            repo.pr.merge_now(pr.number, title=subject)
        except ForgeError as exc:
            if exc.status == 405 and attempt < _MERGE_NOW_ATTEMPTS:
                print(
                    f"  405 (mergeability recompute in flight), attempt"
                    f" {attempt}/{_MERGE_NOW_ATTEMPTS}"
                )
                time.sleep(attempt)
                continue
            raise
        print(f"  merged PR #{pr.number}")
        return


@submit.task(name="merge")
def submit_merge(
    title: Annotated[str, doc("squash subject; defaults to the PR title")] = "",
) -> None:
    """Merge this branch's green, deliberately-unarmed PR.

    Refuses red, pending, or behind-base, saying why. Idempotent
    through livery.forge.PullRequests.merge_now, and patient through
    the forge's mergeability-recompute window (405).
    """
    root = _root()
    from livery.workshop._forge_lane import this_repository

    git = GitOps(root)
    merge_flow(this_repository(root), git, git.current_branch(), title=title)
