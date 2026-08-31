# Quirks

A quirk without a fake fault is a debt. Every forge quirk discovered
gets a `FakeForge` fault mode and a regression test the same day it is
understood, plus its line here. The fault modes are the attributes of
`livery.forge.testing.Faults`; the regression tests live in
`packages/forge/tests/test_fake_faults.py`.

| Date | Forge | Quirk | Fake fault mode | Regression test |
| --- | --- | --- | --- | --- |
| 2026-08-31 | Gitea | An armed auto-merge schedule can be silently lost: the arm is accepted and nothing is recorded, so the pull request later reads unarmed | `Faults.lose_arm_schedule` | `test_a_lost_arm_schedule_reads_unarmed_and_rearming_recovers` |
| 2026-08-31 | Gitea | Merge answers 405 while the checks are not green or a mergeability recompute is in flight, on a pull request that looks mergeable | `Faults.merge_405_window` | `test_the_405_window_refuses_merges_then_passes` |
| 2026-08-31 | Gitea | The Actions status queue can wedge: jobs finish and their results are never applied, so the combined status stays pending forever; cancelling the run is the relief | `Faults.wedge_status_queue` | `test_a_wedged_status_queue_holds_pending_until_cancel_relieves_it` |
| 2026-08-31 | Gitea | A freshly pushed commit reads as having no statuses for a while, so "nothing reported" must mean "keep waiting", never "no CI configured" | `Faults.slow_status_reads` | `test_slow_status_reads_answer_none_before_the_truth` |
