# AGENTS.md

**This is the only file you need to read.** It covers what we're building, where everything lives, the safety rules, your role, and how work flows. Before you edit a folder, also read that folder's `README.md`. Data shapes and interfaces live in [`contracts/`](contracts/README.md).

> **Status: hackathon MVP, build approved (2026-10-23).** The Hailo-8 isn't connected yet: build locally and stay ready to upload. Keep everything as simple as it can be. CI is the check, a Manager reviews once, and the owner merges.

---

## 1. What we're building

A kitchen allergen tracker. A camera on a Raspberry Pi with a Hailo-8 watches food prep and tracks every bin, tool, hand and plate. Every contact between them is logged as a time-stamped edge in a provenance graph stored in SpacetimeDB. When a plate reaches the pass, the system works out which allergens could have reached it, compares them with what the ticket forbids, and holds the plate if there is any doubt.

People can get hurt if this system is wrong in the PASS direction. Wrong in the HOLD direction is an annoyance. Every judgment call leans toward HOLD.

**The Hailo-8 isn't connected yet.** Vision runs on a laptop with ONNX Runtime, from video files or a webcam, and stays ready to upload to the Hailo later (same model file, same processing code, a deploy bundle; see `kitchen/vision/README.md`). Nothing waits on the device.

**Two kinds of builders work in this repo:** agents (a Director, a Manager per team, and Workers), and **the partner**, a human who owns the interactions step, the LSTM, the entity store, the graph and the database (§4).

## 2. Pipeline and code map

All Python lives in one package, `kitchen/`, with one folder per pipeline step. Each folder's `README.md` explains the step, its files, its inputs and outputs, and its tests.

| # | Step | What happens | Folder | Owner |
|---|---|---|---|---|
| 1 | **Orders** | The server types or speaks a ticket (Cartesia Ink). Laya turns the note into typed constraints, low-confidence fields become one-tap questions, results are cached, and the sent ticket is written to the database | [`kitchen/orders/`](kitchen/orders/) | team `orders` |
| 2 | **Profiling** | At shift start, Laya reads bin labels and prep sheets and assigns allergen probabilities; unclear ingredients go to the chef; an Apify menu import is optional | [`kitchen/profiling/`](kitchen/profiling/) | team `orders` |
| 3 | **Vision** | YOLOv8 detection and pose, run locally with ONNX Runtime now and packaged to upload to the Hailo-8 later; ByteTrack, entity identity and origin, zones, plate binding, a per-frame **change mask**, frame recording | [`kitchen/vision/`](kitchen/vision/) | team `vision` |
| 4 | **Entities** | In-memory entity store on the Pi, synced to SpacetimeDB about 5 times a second | [`kitchen/entities/`](kitchen/entities/) | **partner** |
| 5 | **Interactions** | Candidate pairs; per pair, a window with vector features (for zone rules) and a **clip** of box masks plus change mask (for the ConvLSTM); zone rules; a classifier wrapper with fallback; edges written to the database | [`kitchen/interactions/`](kitchen/interactions/) | **partner** |
| 5b | **LSTM** | ConvLSTM or ConvGRU over clips of two boxes plus a change mask: learns whether objects touched and material moved, not what they are. Adopted only if it beats zone rules | [`kitchen/lstm/`](kitchen/lstm/) | **partner** |
| 6 | **Graph** | Provenance graph: noisy-OR propagation, wash resets, time-forward paths, exact reachability, live entity risk | [`kitchen/graph/`](kitchen/graph/) | **partner** |
| 6b | **Database** | SpacetimeDB module (tables, reducers) and the real `StateClient` | [`database/`](database/) | **partner** |
| 7 | **Verdict** | When a plate reaches the pass: the partner graph's exact reachability, then the verdict policy (plain code, fail closed), late-change rechecks, Repeated Mistakes analysis | [`kitchen/verdict/`](kitchen/verdict/) | team `verdict` |
| 8 | **Gate and screens** | Pass gate (servo, red/green light, buzzer), Cartesia Sonic voice ("Hold seven"), and the tablet, chef and expo screens with camera overlays | [`kitchen/gate/`](kitchen/gate/), [`web/`](web/) | team `outputs` |
| — | **Simulator** | One simulated world, two outputs: event-level scenarios (graph, verdict) and **rendered training clips** for the ConvLSTM (abstract shapes, boxes, change masks, labels from world truth) | [`kitchen/sim/`](kitchen/sim/) | team `sim` |
| — | **Shared** | Models generated from contracts, interfaces, clock, config, the one change-mask function and clip builder (used live, in sim and in training), fakes (including fakes for partner parts), contract tests | [`kitchen/shared/`](kitchen/shared/) | team `platform` |
| — | **Contracts** | Schemas, interfaces, table spec, fixtures, golden scenarios: the only things folders may depend on across teams | [`contracts/`](contracts/) | platform (Director reviews) |
| — | **Repo** | `tools/`, `tests/e2e/`, `.github/`, `.claude/`, `pyproject.toml` | — | team `platform` |
| — | **Decisions** | ADRs | [`docs/decisions/`](docs/decisions/) | whoever made the decision |

Data flow:

```
tablet ─▶ orders ─▶ DB: ticket          shift start ─▶ profiling ─▶ DB: bin profiles
camera ─▶ vision ─▶ ObservationFrame + change mask ─┬─▶ entities (partner) ─▶ DB: entity (≈5 Hz)
                                                    └─▶ interactions (partner): pair windows (features + clips) ─▶ zone rules | ConvLSTM ─▶ DB: interaction edges
sim world ─▶ rendered clips + labels ─▶ ConvLSTM training (partner)        sim world ─▶ scenarios ─▶ graph and verdict tests
DB ─▶ graph (partner) ─▶ reach ─▶ verdict ─▶ DB: verdict + chain ─▶ gate, voice, web screens
```

**The ground rule for boundaries:** a folder may use another team's folder only through [`contracts/`](contracts/README.md): JSON schemas for every message, the interfaces in [`contracts/apis.md`](contracts/apis.md), and the tables in [`contracts/state-tables.md`](contracts/state-tables.md). Every interface has a **fake**, so every folder can be built and tested alone. Timestamps are `t_ms` (milliseconds since shift start). Entity IDs look like `bin-4`, `tool-knife-17`, `hand-p1-left`, `plate-t7`. Boxes are normalized `[x1, y1, x2, y2]`.

## 3. Safety rules

These nine invariants override everything. Tests guarding them carry the `safety` marker.

1. **Fail closed.** A missing input, an error, a timeout, a stale track or an unknown entity history produces **HOLD**, never PASS.
2. **The verdict is plain code.** `hold = forbidden_allergen_reachable or confidence < ticket.pass_threshold or any_error`, computed from the partner graph's reachability. Learned models (Laya, the ConvLSTM) only produce its inputs: a low-confidence input becomes a one-tap question or a HOLD, never a PASS.
3. **No unanswered questions.** A ticket whose parse has a field below `question_threshold` can't be sent until a person answers it.
4. **Tickets store forbidden allergens, not forbidden contacts.** The graph decides which contacts matter.
5. **Time runs forward.** A contamination path is valid only if its edges are in increasing time order.
6. **The interaction classifier is allergen-blind, and the learned one is object-blind.** Feature vectors and clips carry no allergen fields (the schemas forbid extra fields). The ConvLSTM sees only box masks and a change mask over time: no object pixels, classes or kinds, so it learns interaction (touch plus movement), not spoons and forks. Sim never renders realistic utensils.
7. **Rules stay as the fallback.** The LSTM replaces zone rules only after it beats them on held-out clips, and zone rules keep running whenever the LSTM is unavailable.
8. **Clean only from a known-clean source.** Tools, hands and plates are clean at shift start. After that, an entity starts clean only if it's first seen leaving a `clean_stack` zone (clean-plate stack, utensil rack). Anything else that appears mid-shift, including a re-acquired track (which always gets a new ID), has **unknown** history and counts as carrying every allergen until a wash resets it.
9. **Late changes recheck everything.** Amending a ticket ("also no dairy") re-runs the verdict against the plate's full history.

Graph math, for reference: noisy-OR, `P_dst(a) ← 1 − (1 − P_dst(a))·(1 − k·p·P_src(a))` per transfer edge (action efficiency `k`, edge probability `p`). A wash does `P(a) ← (1 − p)·P(a)`, and a wash is a self-loop edge (`a_id == b_id`). The partner owns the details (OQ-13, OQ-17).

## 4. Partner-owned areas

The partner owns **`kitchen/entities/`, `kitchen/interactions/`, `kitchen/lstm/`, `kitchen/graph/` and `database/`**.

- **Agents never edit these folders**, not even a typo. CI fails any non-`partner/` branch that touches them.
- **Agents never wait on the partner.** Build against the fakes in `kitchen/shared/fakes/` (`InMemoryStateClient`, `FakeEntityStore`, `FakeProvenanceGraph`, `ScriptedClassifier`). The interfaces in [`contracts/apis.md`](contracts/apis.md) are the deal.
- **Integration waits for the partner.** A task whose last step needs the partner's real code carries `needs-partner`: finish against the fake, then set `status:blocked` with a note.
- **Asking the partner.** A Manager or the Director opens an issue labeled `owner:partner`. Keep them few and concrete.
- **Partner PRs** come from `partner/<slug>` branches, and the partner merges their own. Before a fake is swapped for the partner's real code, the contract tests in `kitchen/shared/contract_tests/` must pass against it.
- **Changing a partner interface** in `contracts/` needs the partner's OK on the PR.

## 5. Roles

Your launch prompt gives you a **role**, a **team** and a **handle** (for example `role=worker team=vision handle=w2`). If it doesn't, do read-only work and ask the owner.

All agents share one GitHub account, so **sign** every issue comment and PR body on its last line, for example `— [worker/vision/w2]`.

| Role | Signature | Job |
|---|---|---|
| **Owner** (human) | — | Merges every PR. Product decisions, keys, money, hardware tests, milestone sign-off |
| **Director** (one) | `— [director]` | Keeps the teams pointed at one working demo. Owns this file, reviews `contracts/` changes, keeps the roadmap and the Program board, talks to the partner. Writes no product code |
| **Manager** (one per team) | `— [manager/<team>]` | Writes issues, dispatches Workers, reviews the team's PRs |
| **Worker** | `— [worker/<team>/w<N>]` | One issue, one worktree, one PR |

### Director: each cycle
1. If `main` is red, get the fix reviewed first and tell the owner nothing else should merge until it's green.
2. Review PRs that change `contracts/` or this file.
3. Unblock cross-team issues; settle disputes in one sentence on the issue. Move Worker capacity to the critical path, within the owner's cap.
4. Rewrite the **Program board** issue body: milestone status, blockers, what needs the owner, what waits on the partner.

### Manager: each cycle
1. Review the team's open PRs (below).
2. Answer Worker questions.
3. Keep a few issues `status:ready` (§6).
4. Dispatch Workers on ready issues, never two on the same files.

Worker launch prompt:

```
role=worker team=<team> handle=w<N>
Issue: #<n> — <title>
Branch: <team>/<n>-<slug>
You may edit only: <folder paths for this issue>
Read AGENTS.md, the README of each folder you will touch, and the issue with all comments.
Done means: a PR that closes #<n> with CI green.
```

Use a stronger model for `safety-critical` issues and anything with tricky concurrency or math.

**Review: one pass, by the Manager.** CI is the proof: read the diff and the CI result, and don't re-run what CI already ran. When all of these hold, post a signed `APPROVED` comment and add the `ready-to-merge` label; the owner merges:
- CI is green and the acceptance criteria are met;
- the tests would fail if the feature broke;
- it touches only this team's folders and no partner folder, and is no bigger than the issue;
- for `safety-critical` PRs: every error, timeout, missing input or unknown history ends in HOLD (no bare `except`, no `None` treated as clean); no model output can remove a hold reason; each invariant touched has a `safety` test.

Otherwise post one numbered list of fixes.

### Worker: steps
1. Read this file, the README of each folder you'll touch, and the issue with all comments (`gh issue view <n> --comments`). If it's ambiguous, ask on the issue and wait.
2. Work in your own worktree on branch `<team>/<n>-<slug>`. For anything safety-relevant, write the test first.
3. Run the folder's tests (`uv run pytest kitchen/<step>`), push, open a PR that closes the issue, and get CI green.
4. Fix each review item. **If three tries haven't produced a passing, approved PR, stop:** comment what's blocking, set `status:blocked`, and move on.
5. After merge, remove your worktree.

**Scope discipline.** Do exactly what the issue asks. No extra options, layers, refactors or features. Found a bug elsewhere? Open an issue for it and keep going.

## 6. How work flows

**Issues are the work queue.** Labels:

| Group | Labels |
|---|---|
| Team | `team:platform` `team:orders` `team:vision` `team:verdict` `team:sim` `team:outputs`, plus `owner:partner` |
| Status | `status:backlog` → `status:ready` → `status:in-progress` → `status:review` → closed, plus `status:blocked` and `status:needs-human` |
| Priority | `prio:p0` (blocks this milestone's demo) · `prio:p1` (needed this milestone) · `prio:p2` (nice to have) |
| Flags | `safety-critical` (verdict, fail-closed or gate path) · `hardware` (needs a real device; only a human verifies) · `needs-partner` · `bug` |
| PRs | `ready-to-merge` (reviewed and green; the owner's merge queue) |

An issue is **ready** when it has a goal, acceptance criteria with the command that proves each, an out-of-scope line, and any `Blocked by #N`. It should fit one PR of about 400 changed lines. Milestones (M0–M4) group issues; there are no epic issues.

**Dispatch.** Only Managers dispatch: set `status:in-progress` and comment which Worker and branch.

**Branches and commits.** Branches are `<team>/<issue>-<slug>` (the partner uses `partner/<slug>`). One agent, one worktree, one branch. Never push to `main`. Commit messages are `<team>: <imperative summary> (#<issue>)`, plus whatever co-author trailer your harness requires.

**Pull requests.** `Closes #N`, what changed, and anything checked by hand that CI doesn't cover. **Agents never run `gh pr merge`:** the team's Manager reviews (the Director, for `contracts/` and `AGENTS.md`) and adds `ready-to-merge`, then the owner squash-merges. The partner merges their own PRs.

**CI** ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) validates the contracts, keeps agent branches out of partner folders, and runs ruff and pytest. That's the whole gate.

**Keeping main green.** Whoever breaks `main` reverts first and fixes second; nothing else merges until it's green.

**Contracts.** To change one, open a PR touching only `contracts/` that names the teams it affects. The Director reviews it; a partner interface also needs the partner's OK. Each affected team updates its code in its own PR.

**Other teams.** Need something from another team? Open an issue with their `team:` label and link it from your blocked issue. Never copy their code into your folder.

**Dependencies.** Add them to the root `pyproject.toml` in your PR and run `uv lock`. Never hand-merge `uv.lock`: take `main`'s version and re-run `uv lock`.

**Decisions.** A spike's answer is a comment on its issue. Write an ADR (`docs/decisions/NNNN-<slug>.md`, from `0000-template.md`) only for a decision that's hard to reverse, and keep it to one page.

**Escalation.** Stuck → your Manager. Needs another team → an issue for them. Contract change, scope change or dispute → the Director. Needs the partner → `owner:partner`. Product decision, keys, money or hardware → the owner (`status:needs-human`, with options and a recommendation).

**Hardware.** Agents can't touch the Pi, camera, Hailo, servo, lights or speaker. Every hardware module has a laptop backend: a video file or webcam instead of the Pi camera, ONNX Runtime instead of the Hailo, console instead of GPIO. Hailo work produces a deploy bundle and a parity script, never a blocker. `hardware` issues go as far as the fake allows, then post a step-by-step test script for the owner and move to `status:needs-human`.

**Data and secrets.** Nothing over 1 MB in git: no video, weights, datasets or HEF/ONNX files. Large artifacts live in `data/` (gitignored), with a `data-manifest.json` giving name, size, sha256 and source. Keys live only in the local `.env` (see `.env.example`); never echo one. If a key is missing, fail with a clear message and file `status:needs-human`.

## 7. Teams

| Team | Folders | Builds | Ships these fakes for others | Safety focus |
|---|---|---|---|---|
| `platform` | `kitchen/shared/`, `tests/e2e/`, `tools/`, `.github/`, `.claude/`, `pyproject.toml` | The `kitchen` Python project, CI, models generated from contracts, interfaces, clock, the end-to-end harness, TS types for `web/` | `InMemoryStateClient`, and fakes for partner parts (`FakeEntityStore`, `FakeProvenanceGraph`, `ScriptedClassifier`), plus contract tests the partner's code must pass | Fakes must be faithful, especially fail-closed behavior |
| `orders` | `kitchen/orders/`, `kitchen/profiling/` | Note parsing via Laya, one-tap questions, cache, apply, amendments, Cartesia Ink, bin profiling, chef confirmation, menu import, the Orders HTTP API, a ticket-note eval set | `FakeLayaParser`, `TextFileTranscriber` | 3 (never send with an open question), 4 |
| `vision` | `kitchen/vision/` | Video and webcam input, YOLOv8 detection and pose run locally with ONNX Runtime, a Hailo deploy bundle and backend ready for upload, the per-frame change mask (shared function), ByteTrack, identity and origin, zone calibration, plate binding, frame recording and replay, the Pi HTTP server | Recorded frame JSONL packs, `FakeVisionAPI` | 8 (new ID on re-acquire, origin set correctly) |
| `verdict` | `kitchen/verdict/` | Verdict policy, clean-confidence, the verdict service (plate at pass, amendment), Repeated Mistakes analysis | `ScriptedVerdictSource` | 1, 2, 9. **The safety-critical team** |
| `sim` | `kitchen/sim/` | Kitchen simulator, scenario generation, error injection, and the **2D clip renderer**: abstract kitchens rendered as boxes plus change masks with labels from world truth, including hard negatives (overlap without contact, motion without contact, contact without movement, camera noise) | Scenario corpora, ConvLSTM training clip datasets | Ground truth independent of the graph; clips object-blind |
| `outputs` | `kitchen/gate/`, `web/` | Gate driver and service (`fault` on a stale feed), Cartesia Sonic voice, the tablet, chef and expo screens, overlays, the Repeated Mistakes view | `ConsoleGate`, `MockGPIOGate`, `ConsoleSpeaker` | 1 (gate fails closed), 3 (tablet can't send with open questions) |
| **partner** (human) | `kitchen/entities/`, `kitchen/interactions/`, `kitchen/lstm/`, `kitchen/graph/`, `database/` | Entity store and sync; the interactions step (candidate pairs, features, zone rules, pair windows, fallback, edges, real-clip labels, the adoption benchmark) and its ConvLSTM/ConvGRU (trained on sim clips); the provenance graph and exact reachability, the SpacetimeDB module and real `StateClient` | — (platform builds the fakes) | 6, 7 in interactions; 5, 8 in the graph; writer enforcement in the database |

This table is the ownership map. Each folder's README lists its milestones, risks and first tasks.

## 8. Engineering conventions

| | Default |
|---|---|
| Python | ≥ 3.11 (vision pins exactly what the Pi runs). One package `kitchen`, one root `pyproject.toml` managed by **uv**, with extras `pi`, `ml`, `server`. `ruff check` and `ruff format` |
| Tests | `pytest`, in `kitchen/<step>/tests/`. Markers: `safety` (guards an invariant), `hardware` (skipped in CI), `slow` (skipped in CI). Run with `uv run pytest kitchen/<step>` |
| Web | React + TypeScript on Vite, Node ≥ 24.15 ([ADR 0007](docs/decisions/0007-outputs-web-framework.md)) |
| Database | SpacetimeDB module (partner's choice of module language) |
| ML | PyTorch. Weights and datasets live outside git |

**Determinism.** Logic never reads the wall clock; it takes a `Clock`. Every RNG is seeded from config. Keep pure logic over contract models, with thin I/O adapters behind the interfaces. Streams are JSONL, one contract object per line, so anyone can replay anyone's output.

**Config and logs.** Typed settings from environment variables, prefixed per step (`ORDERS_`, `VISION_`, `VERDICT_`, ...). Every safety-relevant constant lives in one config object, is logged at startup, and is recorded in the verdict's `versions`. Structured JSON logs with `t_ms` and the relevant IDs. Every HOLD logs all its reasons. Every exception on the verdict path logs at ERROR and produces a HOLD.

**Tests.** Check what a folder emits against its schema, run its main flow on fakes, and add `safety` tests (property-based with `hypothesis` where the rule is a property) on the verdict path.

**Generated files** (`kitchen/shared/models.py`, TS types) are never edited by hand.

**Code style (the owner's rules; reviewers block on these)**
- **KISS: keep it simple.** Write the simplest thing that works. No layer, option, base class or abstraction the task doesn't need. Prefer plain functions and dataclasses over class hierarchies.
- **Readable by a human first.** Short functions that each do one thing, and descriptive names. No clever one-liners, no deep nesting.
- **snake_case** for Python modules, functions and variables, for file and folder names, JSON fields, and TypeScript variables and functions. Use `PascalCase` only for classes, types and React components, and `UPPER_SNAKE_CASE` for constants. React hooks are the one exception: they're named `useSomething`, because React's lint finds hooks by that name.
- **Spacing.** Format with `ruff format` (Python) or Prettier (TypeScript). Put blank lines between logical blocks, and one statement per line.
- **Comments.**
  - Every module starts with its docstring (owner, step, reads, emits, rules).
  - Every public function has a docstring saying what it does and why.
  - Comment the *why* of anything non-obvious, especially safety-related lines.
  - No commented-out code.

## 9. Hard stops

Stop, comment on your issue, and wait if:
- you'd need to edit a folder your team doesn't own, or any partner folder;
- you'd need to change `contracts/`;
- a `safety` test fails, or you'd need to change one;
- the task needs hardware, a paid account, a key you lack, or a purchase;
- two issues contradict each other, or an issue contradicts this file;
- you're about to delete or rewrite more than you were asked to;
- three tries on the same task haven't passed.

**Never:** commit secrets, `.env`, weights or video; push to `main` or force-push someone else's branch; write `@claude` in GitHub text (it can trigger automation); skip or weaken a test to get green; run `gh pr merge` (the owner merges); make the verdict depend on a model being right.

## 10. Roadmap

There's always a working demo on `main` after M1. Each later milestone swaps a fake for a real part. The owner signs off each milestone.

| Milestone | Goal | Exit criteria (summary) |
|---|---|---|
| **Gate** ✅ | Owner approves the plan | Approved 2026-10-23. Backlog seeded |
| **M0 Foundations** | Everyone builds against fakes | `kitchen` project and CI; generated models; interfaces; all fakes, including the partner-part fakes and their contract tests; each step has a skeleton with a passing test; vision replays a video locally (ONNX Runtime, no Hailo) into frame JSONL plus change masks; the shared change-mask function and clip builder; sim renders a first labeled clip set for the partner; sim generates ≥ 20 valid scenarios |
| **M1 Vertical Slice** | A simulated kitchen produces correct verdicts on screen | End-to-end on fakes: typed note → `FakeLayaParser` → question answered → ticket → scenario events → `FakeProvenanceGraph` → verdict → expo board, `ConsoleGate`, `ConsoleSpeaker`. All golden scenarios pass. `safety` tests for invariants 1, 2 and 9. Tablet can't send with open questions. Hailo deploy bundle and parity script ready (no device needed). A sim clip dataset with hard negatives and leak-free splits for the partner's ConvLSTM. Integration with the partner's interactions, graph and database as they land (`needs-partner`) |
| **M2 Live on Pi** | The camera drives it | Connect the Hailo and upload the bundle; the owner runs the parity check; det+pose plus ByteTrack ≥ 30 FPS (`hardware`). Calibration and plate binding. Live zone-rule edges. Gate hardware with `fault` on a stale feed (`hardware`). Overlays. Deterministic replay of a recorded live session |
| **M3 Models** | Learned parts, gated by evaluation | Fine-tuned Laya parses the eval set, with misses becoming questions. Bin profiling from label text. The partner's ConvLSTM vs zone rules, decided by the partner's benchmark. Cartesia Ink and Sonic live |
| **M4 Demo Hardening** | It survives a live demo | Repeated Mistakes view. Live late-change recheck. Offline mode. Failure drills (kill any component → HOLD or `fault`, never PASS). Optional Apify import. Demo script rehearsed |

## 11. Open questions

Everything here runs on its default until its owner decides otherwise.

| ID | Question | Default (who decides) |
|---|---|---|
| OQ-1 | How does Python reach SpacetimeDB? | HTTP reducer calls, plus polling for subscriptions (partner) |
| OQ-2 | Only the nine major allergens, or child units? | **Settled:** nine majors; exceptions stored as free-text ingredient |
| OQ-3 | Default severity per allergen unit | `allergy` (owner) |
| OQ-4 | Pass threshold per severity (preference / intolerance / allergy / anaphylactic) | **Settled:** 0.80 / 0.90 / 0.95 / 0.99 |
| OQ-5 | What backs Laya? | **Decided:** our fine-tuned copy of convaiinnovations/laya, trained on pina1 and run locally (decision 0012) |
| OQ-6 | Camera overlays drawn client-side or burned into the Pi stream? | Client-side (vision + outputs) |
| OQ-7 | SpacetimeDB local or Maincloud? | Local standalone (owner + partner) |
| OQ-8 | What does `trace_sensitive: false` change? | Every transfer edge counts (verdict, owner approves) |
| OQ-9 | Contact and wipe edges: both directions, or inferred? | Both (partner) |
| OQ-11 | Hardware inventory | Pi 5 + Hailo-8 AI HAT+ + Camera Module 3; Hailo not connected yet (owner) |
| OQ-12 | Who records and labels real clips? | Owner records; the partner supplies a labeling tool |
| OQ-13 | How the unknown-history profile enters noisy-OR | Probability 1 for every unit (partner) |
| OQ-14 | One camera or several? | One (owner) |
| OQ-15 | Budget for agent tokens and paid APIs | Owner sets the Worker concurrency cap |
| OQ-16 | Ultralytics YOLOv8 is AGPL-3.0: acceptable? | Fine for a non-distributed demo (owner) |
| OQ-17 | Ordering of edges with equal timestamps | By `t_start_ms`, `t_end_ms`, `event_id`; a tied wash goes before the transfer (partner) |
| OQ-18 | Where do Hailo HEFs come from? | Precompiled Model Zoo HEFs for COCO YOLOv8 det + pose; compile only if we fine-tune (vision) |
| OQ-19 | What does the ConvLSTM output, and how does it map to actions? | P(contact) → `transfer.contact`, P(material moved) → `transfer.scoop`; contact without movement still counts as a possible trace transfer (partner) |
| OQ-20 | Clip geometry | 128 × 128, full frame, T = 16 steps over about 1 s, gain 4, blur 1.5 px, no trail (partner; sim and vision follow) |

## 12. Commands

```bash
uv sync --all-extras                                   # set up
uv run python tools/validate_contracts.py              # schemas, fixtures, golden scenarios
uv run ruff check . && uv run ruff format --check .    # lint
uv run pytest -m 'not hardware and not slow'           # every test CI runs
uv run pytest kitchen/<step>                           # one step's tests
gh issue list -l team:<team> -l status:ready           # your team's ready queue
gh issue view <n> --comments                           # an issue and its history
gh pr list --label ready-to-merge                      # the owner's merge queue
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
| Verdict | PASS or HOLD for one plate against one ticket revision |
| Laya | Our fine-tuned copy of the open Laya model. It doesn't write text: it answers typed questions with calibrated probabilities. As `LayaParser` it reads ticket notes and bin labels. It plays no part in the verdict |
| Zone rules / ConvLSTM | Hand-written classifier, always on / learned spatiotemporal classifier (partner) over clips, adopted only if it wins |
| Change mask | Per-frame single channel, bright where pixels changed since the last frame (brighter = more movement). One shared function for live and sim |
| Clip | One candidate pair over time: box A mask, box B mask, change mask, plus elapsed time per step. The ConvLSTM's only input |
| Fake | A stand-in implementation of a contract, used by other folders' tests |
| Golden scenario | A small hand-checked kitchen story with a known verdict, in `contracts/scenarios/` |

## 14. Running the org (Claude Code)

**One-time setup (owner):** install `gh`, `uv` and Node ≥ 24.15; copy `.env.example` to `.env`; optionally copy `tools/claude-settings.recommended.json` to `.claude/settings.json` (fewer permission prompts).

| Role | How it runs |
|---|---|
| Director | `claude --agent director` at the repo root, with the prompt `role=director handle=director. Concurrency cap: <N> Workers. Run one Director cycle and report only what needs the owner.` |
| Managers | One session each: `claude --bg --agent team-manager --name mgr-<team> "role=manager team=<team> handle=mgr-<team>. Max Workers: <k>. Dispatch only status:ready issues in milestone <M>."`. Keep each awake with `/loop 20m Run one Manager cycle` |
| Workers | The `worker` subagent type, spawned by a Manager, each in its own git worktree under `.claude/worktrees/` |

Agent definitions live in [`.claude/agents/`](.claude/agents/).

**Pitfalls:** every agent shares one GitHub identity (hence signatures); keep task state in GitHub, not local memory; remove worktrees after merge and run `git worktree prune` now and then; concurrency multiplies spend, so respect the owner's cap.
