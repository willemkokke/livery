# Quirks

A quirk without a fake fault is a debt. Every forge quirk discovered
gets a deterministic, millisecond reproduction and a regression test
the same day it is understood, plus its line here. A quirk that leaks
through the protocol surface gets a `livery.forge.testing.Faults`
fault mode (tests in `packages/forge/tests/test_fake_faults.py`); a
quirk a backend absorbs at its own boundary reproduces through that
backend's recorded cassettes, which replay the server's refusal
verbatim on every merge.

| Date | Forge | Quirk | Reproduction | Regression test |
| --- | --- | --- | --- | --- |
| 2026-08-31 | Gitea | An armed auto-merge schedule can be silently lost: the arm is accepted and nothing is recorded, so the pull request later reads unarmed | fault mode `Faults.lose_arm_schedule` | `test_a_lost_arm_schedule_reads_unarmed_and_rearming_recovers` |
| 2026-08-31 | Gitea | Merge answers 405 while the checks are not green or a mergeability recompute is in flight, on a pull request that looks mergeable | fault mode `Faults.merge_405_window` | `test_the_405_window_refuses_merges_then_passes` |
| 2026-08-31 | Gitea | The Actions status queue can wedge: jobs finish and their results are never applied, so the combined status stays pending forever; cancelling the run is the relief | fault mode `Faults.wedge_status_queue` | `test_a_wedged_status_queue_holds_pending_until_cancel_relieves_it` |
| 2026-08-31 | Gitea | A freshly pushed commit reads as having no statuses for a while, so "nothing reported" must mean "keep waiting", never "no CI configured" | fault mode `Faults.slow_status_reads` | `test_slow_status_reads_answer_none_before_the_truth` |
| 2026-08-31 | GitLab | Project deletion is asynchronous: the project is marked for deletion, its path stays occupied, a second DELETE answers 400 "already marked for deletion", the old path answers 405 "moved" for non-GET methods, and a GET on the old path serves the renamed corpse with 200. `delete_repo` absorbs both refusals as success and `get_repo` verifies `path_with_namespace` before answering | gitlab cassettes | `test_gitlab_conformance[repo-lifecycle]` |
| 2026-08-31 | GitLab | Merge-when-pipeline-succeeds answers 405 until two asynchronous facts land: the mergeability recompute and the head pipeline's association with the merge request | gitlab cassettes | `test_gitlab_conformance[arm-disarm]`, `[armed-pr-merges-on-green]` |
| 2026-08-31 | GitLab | The project's delete-branch-on-merge setting only pre-fills the UI checkbox; an API merge deletes the source branch only when the merge call passes `should_remove_source_branch`, and the deletion lands about a second after the merge | gitlab cassettes | `test_gitlab_conformance[pr-merge-now]` |
| 2026-08-31 | GitLab | Pipeline cancellation is asynchronous: a pipeline reads `canceling` until its jobs acknowledge, and cancelling an already finished pipeline answers 200, so the backend probes and raises the terminal refusal itself | gitlab cassettes | `test_gitlab_conformance[cancel-run]` |
| 2026-08-31 | GitHub | Merging an already merged pull request answers 200 "successfully merged" where Gitea and GitLab answer 405. The protocol normalises to GitHub's shape: `merge_now` on a merged pull request is success everywhere, the other backends absorbing their refusal after verifying the merge | all three cassette sets | `[pr-merge-now]` on every backend |
| 2026-08-31 | GitHub | A push workflow triggers for tag pushes too, and `GITHUB_TOKEN` is an expression, not an ambient variable: a run step's shell sees it only when the step exports it in `env:`. Both cost a held job its release signal | driver workflow (`branches` filter, `env:` block) | `test_github_conformance[armed-pr-merges-on-green]` |
| 2026-08-31 | GitHub | Workflow-run creation for a push can be silently dropped under load, and issue listings run about nine seconds behind writes | driver verified pushes and `await_issue` | `test_github_conformance` (whole set) |
