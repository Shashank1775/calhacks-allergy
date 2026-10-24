# Worker playbook

You do one issue, well, and prove it. Your Manager gives you the issue, the branch name and the paths you may edit.

## Steps

1. **Read.** [AGENTS.md](../../AGENTS.md), your team's charter, the issue **with all comments** (`gh issue view <n> --comments`), and every contract the issue names.
2. **Confirm.** Post on the issue:
   ```
   STARTED in worktree <path>, branch <team>/<n>-<slug>
   Plan: <3–6 bullet plan>
   — [worker/<team>/w<N>]
   ```
   If the acceptance criteria are ambiguous, ask in that same comment and wait. Don't guess.
3. **Branch.** You are in your own worktree. `git branch -m <team>/<n>-<slug>` if the harness gave it another name.
4. **Build in small commits.** Write the test first for anything safety-relevant. Commit format is in [WORKFLOW.md](WORKFLOW.md#commits).
5. **Open a draft PR early:** `git push -u origin HEAD`, then `gh pr create --draft --fill`. Fill in the template.
6. **Verify.** Run the component's lint and tests, `python3 tools/validate_contracts.py`, and `python3 tools/check_ownership.py --diff origin/main`. Paste the exact commands and trimmed output into the PR's verification section.
7. **Rebase** on `origin/main`, re-run verification, then `git push --force-with-lease`.
8. **Ready.** `gh pr ready`, then label the issue `status:review` and remove `status:in-progress`.
9. **Respond to review.** Fix each numbered item, reply to each number, and push. Don't argue in code. Argue in a comment, and escalate if you still disagree.
10. **Finish.** After the Manager merges, remove your worktree and report to your Manager in two lines: what merged, and anything you noticed that deserves a new issue.

## Definition of done

- [ ] Every acceptance criterion is met, with proof in the PR.
- [ ] New behavior has tests that would fail without it. Safety-relevant tests are marked `safety`.
- [ ] Contract tests pass. Everything you emit validates against its schema.
- [ ] Lint, type check and tests pass locally and in CI.
- [ ] Only your team's paths are touched.
- [ ] No secrets, no files over 1 MB, no `TODO` without an issue number.
- [ ] The component README is updated if you changed how to run, test or demo it.
- [ ] Hardware status is stated: `not hardware`, `fake only`, or `verified on device by owner (link)`.

## When to stop and ask

Everything under **Hard stops** in [AGENTS.md](../../AGENTS.md#hard-stops), plus:

- You've tried two approaches and neither works. Post both, with the errors.
- The fix wants a bigger refactor than the issue describes. Propose it as a new issue; don't do it.
- You found a bug outside your issue. Open an issue for it (right team label), link it, and keep going on yours.

## Scope discipline

Do exactly what the issue asks. Don't add configuration options, abstraction layers, "while I'm here" refactors or extra features. A reviewer will send them back, and they cost the team a review cycle.
