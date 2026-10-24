#!/usr/bin/env bash
# One-time GitHub setup: labels, milestones, and a ruleset protecting main. Safe to re-run.
# Run after the owner approves the plan:  tools/bootstrap_github.sh [owner/repo]
set -euo pipefail
REPO="${1:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"
echo "bootstrapping $REPO"

label() { gh label create "$1" --repo "$REPO" --color "$2" --description "$3" --force >/dev/null && echo "  label $1"; }

for t in platform orders vision verdict sim outputs; do
  label "team:$t" 1d76db "Owned by team $t"
done
label owner:partner  0052cc "For the human partner (entities, interactions, lstm, graph, database)"
label needs-partner  0052cc "Last step needs the partner's real component; build against the fake first"
label type:task     0e8a16 "A unit of work for one Worker"
label type:epic     5319e7 "Milestone-sized goal; checklist links tasks"
label type:bug      d73a4a "Something is wrong on main"
label type:spike    c5def5 "Time-boxed research; output is a written answer"
label type:request  fbca04 "One team asking another for something"
label type:rfc      b60205 "Proposed contract or architecture change"
label type:question d876e3 "Needs a decision from the Director or owner"
label type:board    000000 "Pinned status board"
label status:backlog     ededed "Not ready yet"
label status:ready       0e8a16 "Ready to dispatch"
label status:in-progress 1d76db "A Worker has it (see claim comment)"
label status:review      5319e7 "PR open and ready for review"
label status:blocked     b60205 "Waiting on something named in a comment"
label status:needs-human d93f0b "Only the owner can unblock"
label prio:p0 b60205 "Blocks the current milestone demo"
label prio:p1 fbca04 "Needed this milestone"
label prio:p2 c5def5 "Nice to have"
label safety-critical         b60205 "Verdict, fail-closed or gate path; needs safety reviewer"
label hardware                d93f0b "Needs a physical device; only a human can verify"
label contract-change         5319e7 "Touches contracts/"
label cross-team              fbca04 "Touches another team's paths with their agreement"
label good-first-task         7057ff "Small and self-contained"
label review:approved         0e8a16 "Owning Manager approved"
label review:safety-approved  0e8a16 "Safety reviewer approved"
label review:changes-requested d73a4a "Back to the Worker"

existing_milestones="$(gh api "repos/$REPO/milestones?state=all&per_page=100" -q '.[].title')"
milestone() {
  if grep -qxF "$1" <<<"$existing_milestones"; then echo "  milestone exists: $1"
  else gh api "repos/$REPO/milestones" -f title="$1" -f description="$2" >/dev/null && echo "  milestone $1"; fi
}
milestone "M0 Foundations"    "Everyone can build against fakes. Exit criteria in AGENTS.md section 10"
milestone "M1 Vertical Slice" "A simulated kitchen produces correct verdicts on screen"
milestone "M2 Live on Pi"     "The camera drives it"
milestone "M3 Models"         "Learned parts, gated by evaluation"
milestone "M4 Demo Hardening" "It survives a live demo"

# Ruleset: PRs required, no force-push or deletion of main, CI must pass.
# Approvals are 0 because every agent shares one account (approvals are labels; see WORKFLOW.md).
if gh api "repos/$REPO/rulesets" -q '.[].name' 2>/dev/null | grep -qxF protect-main; then
  echo "  ruleset exists: protect-main"
else
  if gh api -X POST "repos/$REPO/rulesets" --input - >/dev/null <<'JSON'
{
  "name": "protect-main",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] } },
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    { "type": "pull_request", "parameters": {
        "required_approving_review_count": 0, "dismiss_stale_reviews_on_push": false,
        "require_code_owner_review": false, "require_last_push_approval": false,
        "required_review_thread_resolution": false, "allowed_merge_methods": ["squash"] } },
    { "type": "required_status_checks", "parameters": {
        "strict_required_status_checks_policy": false,
        "required_status_checks": [ { "context": "contracts-and-ownership" } ] } }
  ]
}
JSON
  then echo "  ruleset protect-main created"
  else echo "  WARNING: could not create ruleset (private repos need GitHub Pro/Team for rulesets). Protect main manually or make the repo public."
  fi
fi
echo "done"
