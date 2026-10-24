# Organization

## Shape

```
                        Owner (human)
                             │  approves phase gates, answers status:needs-human,
                             │  does hardware testing, holds keys and money
                         Director
              ┌──────┬───────┼───────┬────────┬─────────┬──────┬───────────┐
          platform intake  state perception interactions provenance  sim  experience
           Manager Manager Manager Manager    Manager     Manager  Manager  Manager
             │       │       │       │          │           │        │        │
          Workers Workers Workers Workers    Workers     Workers  Workers  Workers

          Reviewers (spawned on demand): safety reviewer, cross-team reviewer
```

## Roles

### Owner (human)

- The only one who can approve a phase gate (`PLANNING` → `BUILD APPROVED`, and each milestone exit).
- Answers `status:needs-human` issues: product decisions, keys, purchases, anything physical.
- Runs hardware tests and records results on the issue. Agents cannot touch the Pi, camera, servo or Hailo.

### Director (exactly one)

Playbook: [DIRECTOR.md](DIRECTOR.md).

- Owns [ARCHITECTURE.md](../ARCHITECTURE.md), [ROADMAP.md](../ROADMAP.md), ADRs, and this folder.
- Approves and merges every contract change (RFC).
- Keeps the **Program board** issue current: milestone status, cross-team blockers, open RFCs, `needs-human` items.
- Starts, steers and stops Managers. Settles disputes between teams.
- Writes no component code.

### Manager (one per team)

Playbook: [MANAGER.md](MANAGER.md).

- Owns the team's charter, backlog and **Team board** issue.
- Turns milestone goals into small issues with acceptance criteria, and dispatches them to Workers.
- Reviews every team PR, and merges it when it is green and correct.
- Represents the team in RFCs and cross-team requests.
- May write small fixes when no Worker is free, but its main job is keeping Workers unblocked and the main branch green.

### Worker (several per team)

Playbook: [WORKER.md](WORKER.md).

- Takes one issue at a time, from its Manager only.
- Works in its own worktree and branch, opens a draft PR early, and proves the result with commands and output.
- Never merges.

### Reviewer (on demand)

Playbook: [REVIEWER.md](REVIEWER.md).

- **Safety reviewer.** Required on every PR labeled `safety-critical` (anything on the verdict path, the fail-closed paths, or the gate). Spawned by the Director or a Manager; not a member of the team being reviewed.
- **Cross-team reviewer.** The Manager of the other team, on any PR labeled `cross-team`.
- Read-only on code. Writes review comments and applies `review:*` labels.

## Teams

| ID | Team | Mission | Owns | Charter |
|---|---|---|---|---|
| `platform` | Platform & Integration | Repo tooling, CI, generated types, `libs/py-common` (interfaces and the in-memory state fake), the end-to-end harness, keeping contracts healthy | `contracts/`, `libs/`, `tools/`, `.github/`, `.claude/` | [CHARTER](../../teams/platform/CHARTER.md) |
| `intake` | Orders & Profiling | Ticket notes → typed constraints via Jev, clarifying questions, the parse cache, Cartesia Ink, bin profiling at shift start, chef confirmation, Apify menu import, the intake HTTP API | `components/intake/` | [CHARTER](../../teams/intake/CHARTER.md) |
| `state` | SpacetimeDB State | The SpacetimeDB module (tables, reducers, writer enforcement), the real `StateClient`, generated TS bindings, local DB ops, throughput | `components/state/` | [CHARTER](../../teams/state/CHARTER.md) |
| `perception` | Pi Perception | YOLOv8 detection and pose on the Hailo-8, ByteTrack, zones and slot calibration, entity store, 5 Hz sync, plate binding, MJPEG stream, frame recording and replay | `components/perception/` | [CHARTER](../../teams/perception/CHARTER.md) |
| `interactions` | Interaction Classification | Candidate pairs, allergen-blind feature vectors, zone-rule classifier, LSTM training and its held-out benchmark gate, labeled clip dataset | `components/interactions/` | [CHARTER](../../teams/interactions/CHARTER.md) |
| `provenance` | Provenance & Verdict | Graph engine (noisy-OR, wash resets, time-forward), exact reachability, verdict policy, Laya runtime integration, late-change recheck, Repeated Mistakes | `components/provenance/` | [CHARTER](../../teams/provenance/CHARTER.md) |
| `sim` | Simulation & Models | Kitchen simulator, golden scenario generation, Laya training data, fine-tuning and evaluation, synthetic stress tests | `components/sim/` | [CHARTER](../../teams/sim/CHARTER.md) |
| `experience` | Expo, Screens, Gate & Voice | Expo board, camera overlays, Repeated Mistakes view, server tablet and chef screens, pass-gate hardware driver, Cartesia Sonic voice | `components/experience/` | [CHARTER](../../teams/experience/CHARTER.md) |

`provenance` is the safety-critical team. Its PRs on the verdict path always need the safety reviewer.

## Handles

| Role | Handle | Signature |
|---|---|---|
| Director | `director` | `— [director]` |
| Manager | `mgr-<team>` | `— [manager/<team>]` |
| Worker | `w<N>` (unique within team) | `— [worker/<team>/w<N>]` |
| Safety reviewer | `safety` | `— [reviewer/safety]` |
| Cross-team reviewer | the reviewing Manager | `— [manager/<team>]` |

## How many agents

Concurrency is set by the owner, because tokens are the budget. The defaults:

| | Concurrent workers |
|---|---|
| Per Manager | 2–3 to start, up to 6 once the team's backlog has that many independent `status:ready` issues |
| Whole org | Owner's cap (start around 16) |

A Manager adds a Worker only when there is a ready, independent issue for it. Idle Workers are shut down, not given busywork.

## Dependencies between teams

Thanks to fakes, every team can start on day one. These are the real integration dependencies, the order in which fakes get swapped for real parts:

```
platform: py-common interfaces + InMemoryStateClient ──▶ everyone
sim: golden scenarios ─────────────────────────────────▶ provenance, platform e2e
state: module + real StateClient ──────────────────────▶ intake, perception, interactions, provenance, experience
perception: recorded ObservationFrame JSONL ───────────▶ interactions
interactions: InteractionEvent stream ─────────────────▶ provenance
provenance: verdicts + chains ─────────────────────────▶ experience
sim: Laya model ───────────────────────────────────────▶ provenance (full mode)
```
