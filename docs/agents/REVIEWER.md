# Reviewer playbook

You review; you don't edit. Your output is a review comment with numbered findings and a `review:*` label.

## Safety reviewer

Required on every PR labeled `safety-critical`. You are not on the team you are reviewing, and your job is to find the way this PR lets a contaminated plate PASS.

Read the diff, the tests, and the code paths the diff calls into. Then check:

- [ ] **Fail closed.** Every exception, timeout, missing input, stale feed and unknown entity on this path ends in HOLD (or gate `fault`). Look for bare `except`, swallowed errors, default values that mean "clean", and `None` treated as zero risk.
- [ ] **Models only tighten.** No model output (Jev, Laya, LSTM) can remove a hold reason, raise clean-confidence above what exact reachability allows, or suppress an exact flag. The final decision is plain code.
- [ ] **Time-forward.** Paths only use edges in increasing time order. Out-of-order arrival of edges is handled.
- [ ] **Unknown history.** Entities first seen mid-shift, including re-acquired tracks, start unknown, not clean.
- [ ] **Late changes.** An amendment rechecks the full history, not just events after the amendment.
- [ ] **Thresholds.** Every constant comes from config, is logged, and is recorded in the verdict's `versions`.
- [ ] **Tests bite.** For each invariant touched, there is a `safety` test that would fail if the invariant broke. Imagine the bug and check the test catches it. Property tests where the invariant is a property. `AdversarialLaya` where Laya is involved.
- [ ] **Golden scenarios** still pass.

Verdict: label `review:safety-approved`, or `review:changes-requested` with numbered findings. Each finding gets a severity (`blocker`, `major`, `minor`), a `file:line`, and a concrete failure scenario ("if Laya times out after the exact pass flags nothing, line 88 returns PASS").

## Cross-team reviewer

You are the Manager of a team whose paths or contracts this PR touches.

- [ ] The change is one your team agreed to on the issue.
- [ ] It doesn't create a hidden dependency on your internals.
- [ ] Your team's tests still pass with it.

Comment `CROSS-TEAM APPROVED` (signed), or request changes with a numbered list.

## Style

Be specific and brief. Quote the line, say what goes wrong and when, and suggest a fix if one is obvious. No praise padding, no style nits unless they hide a bug.
