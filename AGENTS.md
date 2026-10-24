# AGENTS.md

**This is the only file you need to read.** It covers what we're building, where everything lives, the safety rules, your role, and how work flows. Before you edit a folder, also read that folder's `README.md` (they're short). Machine-readable contracts live in [`contracts/`](contracts/README.md).

> **Phase gate: BUILD APPROVED.** The owner approved the plan on 2026-10-23: *"Assume hailo isn't connected right now, do stuff locally and be ready to upload to Hailo though. Beyond that everything looks good."* Agents build per this file, starting with M0 (§10). Only the Director changes this line, and only on the owner's word. Setting it back to `PLANNING` stops all product-code PRs (CI enforces this).

---

## 1. What we're building

A kitchen allergen tracker. A camera on a Raspberry Pi with a Hailo-8 watches food prep and tracks every bin, tool, hand and plate. Every contact between them is logged as a time-stamped edge in a provenance graph stored in SpacetimeDB. When a plate reaches the pass, the system works out which allergens could have reached it, compares them with what the ticket forbids, and holds the plate if there is any doubt.

People can get hurt if this system is wrong in the PASS direction. Wrong in the HOLD direction is an annoyance. Every judgment call leans toward HOLD.

**The Hailo-8 isn't connected yet.** Everything runs locally: vision uses ONNX Runtime on a laptop, with video files or a webcam. It stays ready to upload to the Hailo later (same model file, same processing code, a deploy bundle; see `kitchen/vision/README.md`). Nothing waits on the device.

**Two kinds of builders work in this repo:**
- **Agents** (you): a Director, a Manager for each of seven teams, Workers, and Reviewers.
- **The partner**, a human who builds the LSTM and the entity graph/data system. Their folders are off limits to agents (§4).

## 2. Pipeline and code map

All Python lives in one package, `kitchen/`, with one folder per pipeline step. Each folder's `README.md` explains the step, its files, its inputs and outputs, and its tests.

| # | Step | What happens | Folder | Owner |
|---|---|---|---|---|
| 1 | **Orders** | The server types or speaks a ticket (Cartesia Ink). Jev turns the note into typed constraints, low-confidence fields become one-tap questions, results are cached, and the sent ticket is written to the database | [`kitchen/orders/`](kitchen/orders/) | team `orders` |
| 2 | **Profiling** | At shift start, Jev reads bin labels and prep sheets and assigns allergen probabilities; unclear ingredients go to the chef; an Apify menu import is optional | [`kitchen/profiling/`](kitchen/profiling/) | team `orders` |
| 3 | **Vision** | YOLOv8 detection and pose, run locally with ONNX Runtime now and packaged to upload to the Hailo-8 later; ByteTrack, entity identity and origin, zones, plate binding, a per-frame **change mask**, frame recording | [`kitchen/vision/`](kitchen/vision/) | team `vision` |
| 4 | **Entities** | In-memory entity store on the Pi, synced to SpacetimeDB about 5 times a second | [`kitchen/entities/`](kitchen/entities/) | **partner** |
| 5 | **Interactions** | Candidate pairs; per pair, a window with vector features (for zone rules) and a **clip** of box masks plus change mask (for the ConvLSTM); zone rules; a classifier wrapper with fallback; edges written to the database | [`kitchen/interactions/`](kitchen/interactions/) | team `interactions` |
| 5b | **LSTM** | ConvLSTM or ConvGRU over clips of two boxes plus a change mask: learns whether objects touched and material moved, not what they are. Adopted only if it beats zone rules | [`kitchen/lstm/`](kitchen/lstm/) | **partner** |
| 6 | **Graph** | Provenance graph: noisy-OR propagation, wash resets, time-forward paths, exact reachability, live entity risk | [`kitchen/graph/`](kitchen/graph/) | **partner** |
| 6b | **Database** | SpacetimeDB module (tables, reducers) and the real `StateClient` | [`database/`](database/) | **partner** |
| 7 | **Verdict** | When a plate reaches the pass: exact reachability plus Laya, then the verdict policy (plain code, fail closed), late-change rechecks, Repeated Mistakes analysis | [`kitchen/verdict/`](kitchen/verdict/) | team `verdict` |
| 8 | **Gate and screens** | Pass gate (servo, red/green light, buzzer), Cartesia Sonic voice ("Hold seven"), and the tablet, chef and expo screens with camera overlays | [`kitchen/gate/`](kitchen/gate/), [`web/`](web/) | team `outputs` |
| — | **Simulator** | One simulated world, two outputs: event-level scenarios (graph, verdict, Laya) and **rendered training clips** for the ConvLSTM (abstract shapes, boxes, change masks, labels from world truth) | [`kitchen/sim/`](kitchen/sim/) | team `sim` |
| — | **Shared** | Models generated from contracts, interfaces, clock, config, the one change-mask function and clip builder (used live, in sim and in training), fakes (including fakes for partner parts), contract tests | [`kitchen/shared/`](kitchen/shared/) | team `platform` |
| — | **Contracts** | Schemas, interfaces, table spec, fixtures, golden scenarios: the only things teams may depend on across folders | [`contracts/`](contracts/) | platform (Director merges) |
| — | **Repo** | `tools/` (checks, GitHub setup, issue seeding), `tests/e2e/`, `.github/`, `.claude/`, `pyproject.toml` | — | team `platform` |
| — | **Decisions** | ADRs | [`docs/decisions/`](docs/decisions/) | Director |

Data flow:

```
tablet ─▶ orders ─▶ DB: ticket          shift start ─▶ profiling ─▶ DB: bin profiles
camera ─▶ vision ─▶ ObservationFrame + change mask ─┬─▶ entities (partner) ─▶ DB: entity (≈5 Hz)
                                                    └─▶ interactions: pair windows (features + clips) ─▶ zone rules | ConvLSTM (partner) ─▶ DB: interaction edges
sim world ─▶ rendered clips + labels ─▶ ConvLSTM training (partner)        sim world ─▶ scenarios ─▶ graph, verdict, Laya tests and training
DB ─▶ graph (partner) ─▶ reach / subgraph ─▶ verdict (+ Laya) ─▶ DB: verdict + chain ─▶ gate, voice, web screens
```

**The ground rule for boundaries:** a folder may use another folder only through [`contracts/`](contracts/README.md): JSON schemas for every message, the interfaces in [`contracts/apis.md`](contracts/apis.md), and the tables in [`contracts/state-tables.md`](contracts/state-tables.md). Every interface has a **fake**, so every folder can be built and tested alone. Timestamps are `t_ms` (milliseconds since shift start). Entity IDs look like `bin-4`, `tool-knife-17`, `hand-p1-left`, `plate-t7`. Boxes are normalized `[x1, y1, x2, y2]`.

## 3. Safety rules

These nine invariants override everything: a task, a reviewer, a deadline. A PR that breaks one is rejected. Tests guarding them carry the `safety` marker.

1. **Fail closed.** A missing input, an error, a timeout, a stale track or an unknown entity history produces **HOLD**, never PASS.
2. **Models only tighten.** The verdict is plain deterministic code: `hold = exact_flags or model_flags or confidence < ticket.pass_threshold or any_error`. Jev, Laya and the LSTM can add reasons to hold; they can never remove one. If a model the current mode expects is unavailable, the verdict is HOLD (`model_unavailable`). Running without Laya is an explicit `rules_only` mode a person switches on.
3. **No unanswered questions.** A ticket whose parse has a field below `question_threshold` can't be sent until a person answers it.
4. **Tickets store forbidden allergens, not forbidden contacts.** The graph decides which contacts matter.
5. **Time runs forward.** A contamination path is valid only if its edges are in increasing time order.
6. **The interaction classifier is allergen-blind, and the learned one is object-blind.** Feature vectors and clips carry no allergen fields (the schemas forbid extra fields). The ConvLSTM sees only box masks and a change mask over time: no object pixels, classes or kinds, so it learns interaction (touch plus movement), not spoons and forks. Sim never renders realistic utensils.
7. **Rules stay as the fallback.** The LSTM replaces zone rules only after it beats them on held-out clips, and zone rules keep running whenever the LSTM is unavailable.
8. **Clean only from a known-clean source.** Tools, hands and plates are clean at shift start. After that, an entity starts clean only if it's first seen leaving a `clean_stack` zone (clean-plate stack, utensil rack). Anything else that appears mid-shift, including a re-acquired track (which always gets a new ID), has **unknown** history and counts as carrying every allergen until a wash resets it.
9. **Late changes recheck everything.** Amending a ticket ("also no dairy") re-runs the verdict against the plate's full history.

Graph math, for reference: noisy-OR, `P_dst(a) ← 1 − (1 − P_dst(a))·(1 − k·p·P_src(a))` per transfer edge (action efficiency `k`, edge probability `p`). A wash does `P(a) ← (1 − p)·P(a)`, and a wash is a self-loop edge (`a_id == b_id`). The partner owns the details (OQ-13, OQ-17).

## 4. Partner-owned areas

The partner is a human teammate who owns **`kitchen/entities/`, `kitchen/lstm/`, `kitchen/graph/` and `database/`**.

- **Agents never edit these folders**, not even a typo. CI rejects agent PRs that touch them.
- **Agents never wait on the partner.** Build against the fakes in `kitchen/shared/fakes/` (`InMemoryStateClient`, `FakeEntityStore`, `FakeProvenanceGraph`, `ScriptedClassifier`). The interfaces in [`contracts/apis.md`](contracts/apis.md) are the deal between agents and the partner.
- **Integration waits for the partner.** Tasks whose last step needs the partner's real component carry `needs-partner`. Finish everything up to that step against the fake, then move the issue to `status:blocked` with a note.
- **Talking to the partner.** Only Managers and the Director open issues for the partner, labeled `owner:partner`. Workers ask their Manager. Keep partner issues few, concrete and batched; the Director lists open ones on the Program board.
- **Partner PRs** come from `partner/<slug>` branches. The partner merges their own work after CI passes. Contract tests in `kitchen/shared/contract_tests/` must pass against the partner's real code before any fake is swapped out.
- **Changing a partner interface** goes through an RFC like any contract change (§6), and the partner must ACK it.

## 5. Roles

Your launch prompt gives you a **role**, a **team** and a **handle** (for example `role=worker team=vision handle=w2`). If it doesn't, do read-only work and ask the owner. Never promote yourself.

All agents share one GitHub account, so **sign everything** (issue comments, PR bodies, reviews) on its own last line, for example `— [worker/vision/w2]`, and add an `Agent: worker/vision/w2` trailer to every commit.

| Role | Signature | Job |
|---|---|---|
| **Owner** (human) | — | Approves phase gates and milestone exits. Answers `status:needs-human`. Runs hardware tests. Holds keys and money |
| **Director** (one) | `— [director]` | Keeps seven teams pointed at one working system. Owns this file, contracts approval, the roadmap, ADRs, the Program board, and the relationship with the partner. Writes no product code |
| **Manager** (one per team) | `— [manager/<team>]` | Owns the team's backlog and Team board. Writes ready issues, dispatches Workers, reviews and merges team PRs, and represents the team in RFCs |
| **Worker** | `— [worker/<team>/w<N>]` | One issue at a time, in its own worktree, proven with commands and output. Never merges |
| **Safety reviewer** | `— [reviewer/safety]` | Required on every `safety-critical` PR. Not on the team being reviewed. Read-only |

### Director: each cycle (every 30–60 min, or on escalation)
1. Is `main` green (`gh run list --branch main --limit 5`)? If not, stop the line: no merges except the fix.
2. Merge RFC PRs whose affected Managers (and the partner, if touched) have all ACKed. Announce the new contract version on each affected Team board.
3. Clear cross-team blockers. Decide disputes on the issue in one sentence; write an ADR if the decision outlives the milestone.
4. Check the milestone's exit criteria (§10) and update the Program board: done, on track, or at risk, with evidence.
5. Move Worker capacity to the critical path, within the owner's cap.
6. Update the Program board's **Needs the owner** and **Partner** sections. Each item gets one paragraph: the question, options, your recommendation, and what's blocked.

The Director flips the phase gate and closes milestones only on the owner's explicit word, quoted in the PR. It never gives Workers orders directly, and never reports something as done without checking the PR, CI run or owner's hardware report.

### Manager: each cycle (every 20–30 min, or when a Worker finishes)
1. **Review first.** Use the checklist below and check the code yourself; don't trust the PR description.
2. **Unblock.** Answer Worker questions. Turn what you can't answer into a request or an escalation.
3. **Groom.** Keep at least (Workers + 2) issues `status:ready`, each meeting the ready bar (§6).
4. **Dispatch.** Claim a ready issue, then start a Worker with the prompt below. Never put two Workers on the same files.
5. **Requests.** Triage other teams' `type:request` issues: accept with an ETA, counter-propose, or escalate.
6. **Board.** Rewrite the Team board body: in flight (issue → worker → PR), blocked, next, decisions, risks.

Worker launch prompt:

```
role=worker team=<team> handle=w<N>
Issue: #<n> — <title>
Branch: <team>/<n>-<slug>
You may edit only: <folder paths for this issue>
Read AGENTS.md, the README of each folder you will touch, and the issue with all comments.
Done means: a ready-for-review PR that closes #<n>, CI green, and verification commands and output in the PR.
```

Use a stronger model for `safety-critical` issues and anything with tricky concurrency or math.

**Review checklist.** Merge only when all of these hold:
- [ ] The acceptance criteria are met, and you ran the proof commands yourself or read CI logs that show them.
- [ ] The tests would fail if the feature were broken.
- [ ] It touches only this team's folders (or is labeled `cross-team` with the other Manager's comment), and none of the partner's.
- [ ] Contracts are used exactly as written.
- [ ] No secrets, no files over 1 MB.
- [ ] Time is injected and randomness is seeded.
- [ ] `safety-critical` PRs have `review:safety-approved`.
- [ ] Hardware status is honest.
- [ ] It's no bigger than the task.

Approve with a signed `APPROVED` comment and the `review:approved` label, then `gh pr merge <n> --squash --delete-branch`, then confirm `main` is still green. To request changes, label `review:changes-requested` and post a numbered list.

### Worker: steps
1. Read this file, the README of each folder you'll touch, the issue with all comments (`gh issue view <n> --comments`), and every contract it names.
2. Comment `STARTED in worktree <path>, branch <branch>` with a 3–6 bullet plan. If the acceptance criteria are ambiguous, ask in that comment and wait.
3. Work in your own worktree. Rename its branch to `<team>/<n>-<slug>`. Commit small; for anything safety-relevant, write the test first.
4. Open a **draft** PR early (`git push -u origin HEAD && gh pr create --draft --fill`).
5. Verify: the folder's tests, `python3 tools/validate_contracts.py`, `python3 tools/check_ownership.py --diff origin/main`. Paste the exact commands and trimmed output into the PR.
6. Rebase on `origin/main`, re-verify, `git push --force-with-lease`, `gh pr ready`, and set the issue to `status:review`.
7. Fix each numbered review item and reply to each number.
8. After merge, remove your worktree and report two lines to your Manager: what merged, and anything worth a new issue.

**Scope discipline.** Do exactly what the issue asks. No extra options, layers, refactors or features. Found a bug elsewhere? Open an issue for it and keep going on yours.

### Safety reviewer: checklist
Find the way this PR lets a contaminated plate PASS.
- [ ] Every exception, timeout, missing input, stale feed or unknown entity ends in HOLD (or gate `fault`). Look for bare `except`, swallowed errors, and `None` treated as clean.
- [ ] No model output can remove a hold reason or raise clean-confidence above what exact reachability allows.
- [ ] Unknown history stays unknown. Amendments recheck full history.
- [ ] Constants come from config and are logged and recorded in the verdict's `versions`.
- [ ] Each invariant touched has a `safety` test that would catch the bug. Property tests where it's a property; `AdversarialLaya` where Laya is involved.
- [ ] Golden scenarios still pass.

Post one comment with numbered findings, each with a severity (`blocker`, `major`, `minor`), a `file:line` and a concrete failure scenario. Then label `review:safety-approved` or `review:changes-requested`.

## 6. How work flows

**Labels.** Every issue has exactly one each of `team:`, `type:` and `status:`, plus `prio:` once triaged.

| Group | Labels |
|---|---|
| Team | `team:platform` `team:orders` `team:vision` `team:interactions` `team:verdict` `team:sim` `team:outputs`, plus `owner:partner` (for the human partner) |
| Type | `type:task` (one Worker) · `type:epic` (milestone goal) · `type:bug` · `type:spike` (time-boxed research; output is a written answer) · `type:request` (one team asks another) · `type:rfc` (contract change) · `type:question` (needs the Director or owner) · `type:board` (pinned status) |
| Status | `status:backlog` → `status:ready` → `status:in-progress` → `status:review` → closed, plus `status:blocked` and `status:needs-human` |
| Priority | `prio:p0` (blocks this milestone's demo) · `prio:p1` (needed this milestone) · `prio:p2` (nice to have) |
| Flags | `safety-critical` (verdict, fail-closed or gate path) · `hardware` (needs a real device; only a human verifies) · `needs-partner` (last step needs the partner's real code) · `contract-change` · `cross-team` · `good-first-task` |
| Review | `review:approved` · `review:safety-approved` · `review:changes-requested` |

**The ready bar.** An issue is `ready` only when it has:
- a goal (the outcome, not the method);
- acceptance criteria, each with the command that proves it;
- the contracts it touches;
- an out-of-scope list (agents over-build; say where to stop);
- dependencies (`Blocked by #N`, or none);
- a size that fits one PR of about 400 changed lines or less.

**Dispatch and claims.** Only Managers dispatch. Swap the label to `status:in-progress` and comment `CLAIM worker=w2 branch=vision/42-bytetrack`, signed. Workers never claim for themselves. A claim with no commits or comments for 2 hours is stale: comment `RECLAIM` and reassign.

**Branches and worktrees.**
- Branch names are `<team>/<issue>-<slug>`. Contract branches are `contracts/<rfc>-<slug>`. The partner uses `partner/<slug>`.
- One agent, one worktree, one branch. Never edit the main checkout. Never push to `main`. Use `--force-with-lease`, and only on your own branch.

**Commits.**
```
<team>: <imperative summary, under 72 chars> (#<issue>)

<why, if not obvious>

Agent: worker/<team>/w2
```
Add whatever co-author trailer your harness requires after the `Agent:` line.

**Lockfiles.** Adding a dependency is a change to the root `pyproject.toml` (platform-owned). Ask platform with a `type:request`, or label your PR `cross-team` with platform's OK. Never hand-merge `uv.lock`: take `main`'s version, re-run `uv lock`, and commit.

**Pull requests.** Open a draft early. Fill in the template: `Closes #N`, what changed, **how you verified it** (exact commands and output), contract impact, safety impact, and hardware status (`not hardware`, `fake only`, or `verified on device by owner (link)`). CI must be green.

| PR touches | Needs before merge | Merged by |
|---|---|---|
| One team's folders | `review:approved` from that team's Manager | That Manager |
| Anything `safety-critical` | Plus `review:safety-approved` | The owning Manager |
| Another team's folders | Plus that Manager's signed approval, and the `cross-team` label | The owning Manager |
| `contracts/` | An RFC issue with every affected Manager's ACK (and the partner's, if a partner interface changes) | The Director |
| `AGENTS.md`, `README.md`, `OWNERS`, `docs/` | The Director's review | The Director |
| Partner folders | Partner PRs only | The partner |

Merge with squash only. GitHub won't let one account approve its own PR, so approvals are a signed comment plus a label.

**Keeping main green.** `main` must always pass CI and run the current demo path on fakes. Whoever sees it break opens a `type:bug` `prio:p0` issue. The team that broke it reverts first and fixes second, and nothing else merges until it's green.

**Cross-team requests.** Open a `type:request` issue with the **other** team's label, saying what you need, why, and by which milestone. Link it from your blocked issue. Never copy another team's code into your folder to work around a missing piece.

**Contract changes (RFCs).**
1. Open a `type:rfc` issue with the change, motivation, producers and consumers, whether it's breaking, the migration path, and the safety impact.
2. Every affected Manager comments `ACK`, `ACK with changes` or `NACK`. The partner too, if their interface is affected.
3. Open a PR on a `contracts/<rfc>-<slug>` branch touching only `contracts/` (plus an ADR if behavior changes). Bump `contracts/VERSION`: minor for additive changes, major for breaking ones.
4. The Director merges it and announces the change on the affected boards.

**ADRs.** A decision that's hard to reverse, or that spans teams, gets `docs/decisions/NNNN-<slug>.md` (copy `0000-template.md`).

**Boards.** Each team has a pinned `Team board: <team>` issue, and its Manager rewrites the body (not comments) every cycle. The Director keeps the `Program board` the same way: milestone status, blockers, RFCs, needs-the-owner, partner. Boards are the first thing a restarted agent reads.

**Escalation.**
- Worker stuck after two attempts → Manager (comment, `status:blocked`).
- Needs another team → `type:request`.
- Contract change, scope change, team dispute, or milestone at risk → Director (Program board).
- Needs the partner → Manager or Director opens `owner:partner`.
- Product decision, keys, money or hardware → owner (`status:needs-human`, with options and a recommendation).

**Hardware.** Agents can't touch the Pi, camera, Hailo, servo, lights or speaker. Every hardware module has a laptop backend: a video file or webcam instead of the Pi camera, ONNX Runtime instead of the Hailo, console instead of GPIO. The Hailo isn't connected yet, so the local backend is the default and Hailo work produces a deploy bundle and a parity script, never a blocker. `hardware` issues go as far as the fake allows, then post a step-by-step test script for the owner and move to `status:needs-human`.

**Data and secrets.**
- Nothing over 1 MB in git: no video, weights, datasets or HEF/ONNX files. Large artifacts live in `data/` (gitignored), with a `data-manifest.json` in the folder giving name, size, sha256, source and author.
- Keys live only in the local `.env` (see `.env.example`). Never echo a key anywhere.
- If a key is missing, fail with a clear message, and file `status:needs-human`.

## 7. Teams

| Team | Folders | Builds | Ships these fakes for others | Safety focus |
|---|---|---|---|---|
| `platform` | `kitchen/shared/`, `tests/e2e/`, `tools/`, `.github/`, `.claude/`, `pyproject.toml`, `contracts/` upkeep | The `kitchen` Python project, CI, models generated from contracts, interfaces, clock, the end-to-end harness, TS types for `web/` | `InMemoryStateClient`, and fakes for partner parts (`FakeEntityStore`, `FakeProvenanceGraph`, `ScriptedClassifier`), plus contract tests the partner's code must pass | Fakes must be faithful, especially fail-closed behavior |
| `orders` | `kitchen/orders/`, `kitchen/profiling/` | Note parsing via Jev, one-tap questions, cache, apply, amendments, Cartesia Ink, bin profiling, chef confirmation, menu import, the Orders HTTP API, a ticket-note eval set | `FakeJev`, `TextFileTranscriber` | 3 (never send with an open question), 4 |
| `vision` | `kitchen/vision/` | Video and webcam input, YOLOv8 detection and pose run locally with ONNX Runtime, a Hailo deploy bundle and backend ready for upload, the per-frame change mask (shared function), ByteTrack, identity and origin, zone calibration, plate binding, frame recording and replay, the Pi HTTP server | Recorded frame JSONL packs, `FakeVisionAPI` | 8 (new ID on re-acquire, origin set correctly) |
| `interactions` | `kitchen/interactions/` | Candidate pairs, allergen-blind features, live pair windows (features + clips via the shared builder), zone rules, the fallback classifier wrapper, event segmentation, real-clip labeling, the benchmark that decides whether the partner's ConvLSTM is adopted | Recorded event JSONL packs, labeled real clips for the partner's fine-tuning and evaluation | 6, 7 (false washes are the costliest error) |
| `verdict` | `kitchen/verdict/` | Verdict policy, the clean-confidence ADR, the Laya adapter, the verdict service (plate at pass, amendment), Repeated Mistakes analysis | `ExactLaya`, `AdversarialLaya`, `ScriptedVerdictSource` | 1, 2, 9. **The safety-critical team** |
| `sim` | `kitchen/sim/` | Kitchen simulator, scenario generation at scale, error injection, Laya datasets and evaluation, and the **2D clip renderer**: abstract kitchens rendered as boxes plus change masks with labels from world truth, including hard negatives (overlap without contact, motion without contact, contact without movement, camera noise) | Scenario corpora, ConvLSTM training clip datasets | Ground truth independent of the graph; clips object-blind |
| `outputs` | `kitchen/gate/`, `web/` | Gate driver and service (`fault` on a stale feed), Cartesia Sonic voice, the tablet, chef and expo screens, overlays, the Repeated Mistakes view | `ConsoleGate`, `MockGPIOGate`, `ConsoleSpeaker` | 1 (gate fails closed), 3 (tablet can't send with open questions) |
| **partner** (human) | `kitchen/entities/`, `kitchen/lstm/`, `kitchen/graph/`, `database/` | Entity store and sync, the ConvLSTM/ConvGRU interaction model (trained on sim clips), the provenance graph and exact reachability, the SpacetimeDB module and real `StateClient` | — (platform builds the fakes) | 5, 8 in the graph; writer enforcement in the database |

Ownership is authoritative in [`OWNERS`](OWNERS). Each folder's README lists its milestones, risks and first tasks.

## 8. Engineering conventions

**Languages and tools**

| | Default |
|---|---|
| Python | ≥ 3.11 (vision pins exactly what the Pi runs). One package `kitchen`, one root `pyproject.toml` managed by **uv**, with extras `pi`, `ml`, `server`. `ruff check` and `ruff format`. `pyright` (basic) |
| Tests | `pytest`, in `kitchen/<step>/tests/`. Markers: `safety` (guards an invariant), `hardware` (skipped in CI), `slow` (nightly). Run with `uv run pytest kitchen/<step>` |
| Web | TypeScript, Node ≥ 20, Vite. The framework is chosen by an ADR from outputs |
| Database | SpacetimeDB module (partner's choice of module language) |
| ML | PyTorch. Weights and datasets live outside git |

**Determinism**
- Logic never reads the wall clock; it takes a `Clock`.
- Every RNG is seeded from config.
- Keep pure logic over contract models, with thin I/O adapters behind the interfaces.
- Streams are JSONL, one contract object per line, so anyone can replay anyone's output.

**Config and logs**
- Typed settings from environment variables, prefixed per step (`ORDERS_`, `VISION_`, `VERDICT_`, ...).
- Every safety-relevant constant lives in one config object, is logged at startup, and is recorded in verdict `versions`.
- Structured JSON logs with `t_ms` and the relevant IDs. Every HOLD logs all its reasons. Every exception on the verdict path logs at ERROR and produces a HOLD.

**Every folder has three kinds of tests**
1. Contract tests: everything it emits validates, and it accepts every valid fixture it consumes.
2. A fake-swap test: the main flow runs with all dependencies faked.
3. `safety` tests if it touches the verdict path, property-based (`hypothesis`) where the rule is a property.

**Generated files** (`kitchen/shared/models.py`, TS types) are never edited by hand.

## 9. Hard stops

Stop, comment on your issue, and wait if:
- you'd need to edit a folder your team doesn't own, or any partner folder;
- you'd need to change `contracts/`;
- a `safety` test fails, or you'd need to change one;
- the task needs hardware, a paid account, a key you lack, or a purchase;
- two issues contradict each other, or an issue contradicts this file;
- you're about to delete or rewrite more than you were asked to;
- anything would push to `main`, force-push someone else's branch, or rewrite published history;
- you've failed twice at the same problem (post both attempts and their errors).

**Never:**
- commit secrets, `.env`, weights or video;
- write `@claude` in GitHub text (it can trigger automation);
- skip or weaken a test to get green;
- merge your own PR unless you're the owning Manager (or the Director, for `contracts/` and docs);
- make the verdict depend on a model being right.

## 10. Roadmap

There's always a working demo on `main` after M1. Each later milestone swaps a fake for a real part. A milestone closes only when the owner signs off on the Program board. There are no dates yet (OQ-10).

| Milestone | Goal | Exit criteria (summary) |
|---|---|---|
| **Gate** ✅ | Owner approves the plan | Approved 2026-10-23 with defaults for the blocking questions. GitHub bootstrapped and backlog seeded |
| **M0 Foundations** | Everyone builds against fakes | `kitchen` project and CI; generated models; interfaces; all fakes, including the partner-part fakes and their contract tests; each step has a skeleton with a passing test; vision replays a video locally (ONNX Runtime, no Hailo) into frame JSONL plus change masks; Hailo readiness ADR; the shared change-mask function and clip builder; sim renders a first labeled clip set for the partner; sim generates ≥ 20 valid scenarios |
| **M1 Vertical Slice** | A simulated kitchen produces correct verdicts on screen | End-to-end on fakes: typed note → `FakeJev` → question answered → ticket → scenario events → `FakeProvenanceGraph` → verdict → expo board, `ConsoleGate`, `ConsoleSpeaker`. All golden scenarios pass in `rules_only`. Property tests for invariants 1, 2 and 9 approved by the safety reviewer. Tablet can't send with open questions. Zone rules turn recorded frames into valid edges. Hailo deploy bundle and parity script ready (no device needed). A large sim clip dataset with hard negatives and leak-free splits for ConvLSTM training; live pair windows built with the same clip builder. **Integration with the partner's graph and database as they land** (`needs-partner`) |
| **M2 Live on Pi** | The camera drives it | Connect the Hailo and upload the bundle; the owner runs the parity check; det+pose plus ByteTrack ≥ 30 FPS (`hardware`). Calibration and plate binding. Live zone-rule edges. Gate hardware with `fault` on a stale feed (`hardware`). Overlays. Deterministic replay of a recorded live session |
| **M3 Models** | Learned parts, gated by evaluation | Real Jev parse on the eval set, with misses becoming questions. Bin profiling from photos. Laya fine-tuned on sim; on held-out sim it never misses an allergen exact reachability finds. ConvLSTM (trained on sim clips, checked on labeled real clips) vs zone-rule benchmark, with an ADR co-signed by the partner. Sim clip realism calibrated against real change masks. Cartesia Ink and Sonic live |
| **M4 Demo Hardening** | It survives a live demo | Repeated Mistakes view. Live late-change recheck. Offline mode. Failure drills (kill any component → HOLD or `fault`, never PASS). Optional Apify import. Demo script rehearsed three times |

## 11. Open questions

The gate is open. The blocking questions were settled with their defaults on 2026-10-23. Anything still open uses its default until its owner decides.

| ID | Question | Decides | Blocking | Default |
|---|---|---|---|---|
| OQ-1 | How does Python reach SpacetimeDB (HTTP reducer calls, bridge process, community SDK)? | partner | no | HTTP reducer calls, plus polling for subscriptions |
| OQ-2 | Only the nine major allergens, or child units (coconut under tree nuts)? | owner | resolved | **Default accepted (2026-10-23):** nine majors; exceptions stored as free-text ingredient |
| OQ-3 | Default severity per allergen unit | owner | no | `allergy` |
| OQ-4 | Pass threshold per severity (preference / intolerance / allergy / anaphylactic) | owner | resolved | **Default accepted (2026-10-23):** 0.80 / 0.90 / 0.95 / 0.99 |
| OQ-5 | What backs Jev and Laya: provider, hosting, cost, offline story, keys? | owner | by M3 | **Default accepted (2026-10-23):** both behind interfaces with fakes until the owner decides; the orders and sim spikes gather options |
| OQ-6 | Camera overlays drawn client-side or burned into the Pi stream? | vision + outputs | no | Client-side |
| OQ-7 | SpacetimeDB local on the laptop or Maincloud? | owner + partner | no | Local standalone |
| OQ-8 | What does `trace_sensitive: false` change? | verdict (ADR), owner approves | no | Every transfer edge counts (strict) |
| OQ-9 | Contact and wipe edges: both directions, or inferred? | interactions + partner | no | Both |
| OQ-10 | Deadline, and agent hours per day? | owner | resolved | **Default accepted (2026-10-23):** no dates; milestones in order |
| OQ-11 | Hardware inventory (Pi, Hailo form factor, camera, servo, lights, buzzer, speaker) | owner | by M2 | **Hailo not connected yet (owner, 2026-10-23): build locally, stay upload-ready.** Assumed: Pi 5 + Hailo-8 AI HAT+ + Camera Module 3 |
| OQ-12 | Who records and labels real clips, with what tool? | owner + interactions | by M3 | Owner records; interactions supplies a labeling tool |
| OQ-13 | How the unknown-history profile enters noisy-OR | partner (ADR) | no | Probability 1 for every unit |
| OQ-14 | One camera or several? | owner | no | One |
| OQ-15 | Budget for agent tokens and paid APIs | owner | at launch | Owner sets the Worker concurrency cap when the Director starts |
| OQ-16 | Ultralytics YOLOv8 is AGPL-3.0: acceptable? | owner | by M2 | Fine for a non-distributed demo |
| OQ-17 | Ordering of edges with equal timestamps | partner (ADR) | no | By `t_start_ms`, `t_end_ms`, `event_id`; a tied wash goes before the transfer (strict) |
| OQ-18 | Where do Hailo HEFs come from: precompiled Hailo Model Zoo files, or our own compile with the Dataflow Compiler (needs an x86-64 Linux machine, not the Mac)? | vision (ADR, vision-02), owner provides a machine if needed | by M2 | Precompiled Model Zoo HEFs for COCO YOLOv8 det + pose; compile only if we fine-tune |
| OQ-19 | What does the ConvLSTM output, and how does it map to actions? | partner + interactions (ADR) | by M1 | Two heads: P(contact) → `transfer.contact`, P(material moved) → `transfer.scoop`; a contact without visible movement still counts as a possible trace transfer (OQ-8) |
| OQ-20 | Clip geometry: H × W, steps T and stride, full frame or pair crop, change-mask gain/blur/decay | partner (ADR); sim and interactions follow | by M0 end | 128 × 128, full frame, T = 16 steps covering about 1 s, gain 4, blur 1.5 px, no trail |

The Director moves answered questions to the bottom of this table with the answer and a link.

## 12. Commands and tools

```bash
python3 tools/validate_contracts.py                    # schemas, fixtures, golden scenarios (pip install jsonschema)
python3 tools/check_ownership.py                       # every file has exactly one owner
python3 tools/check_ownership.py --diff origin/main    # which owners this branch touches (fails on partner folders from agent branches)
python3 tools/check_phase_gate.py --diff origin/main   # fails on product code while the gate is PLANNING
python3 tools/seed_issues.py --dry-run                 # validate the seed backlog in tools/backlog/
gh issue list -l team:<team> -l status:ready           # your team's ready queue
gh issue view <n> --comments                           # an issue and its history
uv run pytest kitchen/<step>                           # a step's tests (once the project exists)
```

## 13. Glossary

| Term | Meaning |
|---|---|
| Allergen unit | One row of `contracts/allergen_units.json`, e.g. `peanut` |
| Ticket / revision | A guest's order note and its constraints. Each amendment bumps `revision` |
| Constraint / exception | A forbidden allergen (with severity, trace sensitivity, confidence) / an ingredient that's fine despite its unit |
| `question_threshold` / `pass_threshold` | Parse confidence below which a field becomes a question / clean-confidence a plate needs to pass |
| Clean-confidence | How sure we are a plate carries no forbidden allergen. Defined in verdict's ADR |
| Entity / origin | A tracked bin, tool, hand or plate. `origin` is `shift_start`, `clean_stack` or `unknown` |
| Interaction / edge | A classified contact between two entities: `transfer.contact`, `transfer.scoop`, `transfer.wipe`, `clean.wash` or `unimportant` |
| Pass / expo / plating spot | Where plates leave / the expediter's board / where a plate is bound to its ticket |
| Verdict / mode | PASS or HOLD for one plate against one ticket revision / `full` (with Laya) or `rules_only` |
| Jev / Laya | Model roles: Jev parses notes and reads labels; Laya names the allergens that reached a plate, with chains. What backs each is OQ-5 |
| Zone rules / ConvLSTM | Hand-written classifier, always on / learned spatiotemporal classifier (partner) over clips, adopted only if it wins |
| Change mask | Per-frame single channel, bright where pixels changed since the last frame (brighter = more movement). One shared function for live and sim |
| Clip | One candidate pair over time: box A mask, box B mask, change mask, plus elapsed time per step. The ConvLSTM's only input |
| Fake | A stand-in implementation of a contract, used by other folders' tests |
| Golden scenario | A small hand-checked kitchen story with a known verdict, in `contracts/scenarios/` |

## 14. Running the org (Claude Code)

**For the owner, one-time setup:**
- Install `gh`, `uv` and Node ≥ 20.
- Copy `.env.example` to `.env`.
- Review `tools/claude-settings.recommended.json` and copy it to `.claude/settings.json`.
- After approval, run `tools/bootstrap_github.sh` (labels, milestones, `main` ruleset) and `tools/seed_issues.py`.

**Launching each role:**

| Role | How it runs |
|---|---|
| Director | `claude --agent director` at the repo root (long-lived) |
| Managers | One desktop-app session each, or `claude --bg --agent team-manager --name mgr-<team> "<prompt>"`. Keep each awake with `/loop 20m Run one Manager cycle` |
| Workers | The `worker` subagent type, spawned by a Manager. Each runs in its own git worktree under `.claude/worktrees/`, branched from `origin/main` |
| Reviewers | The `reviewer` subagent type, one per PR |

Agent definitions live in [`.claude/agents/`](.claude/agents/). Nesting is Director → Manager → Worker, which fits Claude Code's default depth.

**Prompts:**
- Director: `role=director handle=director. Concurrency cap: <N> Workers. Phase gate: <state>. Run one Director cycle and report only what needs the owner.`
- Manager: `role=manager team=<team> handle=mgr-<team>. Max Workers: <k>. Dispatch only status:ready issues in milestone <M>.`
- Safety reviewer: `role=reviewer handle=safety. Review PR #<n> per AGENTS.md §5.`

**Pitfalls**
- **One GitHub identity.** That's why signatures are mandatory.
- **Shared auto memory.** Every worktree of this repo on the Mac shares it, so keep task state in GitHub only.
- **Worktrees pile up.** Remove worktrees after merge, and run `git worktree prune` weekly.
- **Settings deny rules can be bypassed.** The GitHub ruleset is the real guard on `main`.
- **Concurrency multiplies spend.** Respect the owner's cap.
