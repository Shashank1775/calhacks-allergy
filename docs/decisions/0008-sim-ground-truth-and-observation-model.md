# 0008: Sim's ground truth and observation model

- **Status:** proposed
- **Date:** 2026-10-23
- **Deciders:** director, mgr-sim

## Context

Sim builds kitchens whose truth we know, so that the partner's graph and the verdict policy can be tested against answers that none of them produced (issue #31, [`kitchen/sim/README.md`](../../kitchen/sim/README.md)). Three things must be settled before the generator (sim-01) and the error injectors (sim-06) are built:

1. How the simulated world moves allergens: how often each action really moves a unit, and how often a wash really cleans.
2. How observations (the `interaction_event`s a scenario carries) are derived from that world: probability noise, dropped edges and track loss.
3. What a generated scenario's `expected` block promises, and which generated scenarios may fail CI.

The forces at play:

- **Independence.** If expected answers came from noisy-OR or from the verdict policy, a bug in either one would be copied into its own test. Sim never imports `kitchen.graph` or `kitchen.verdict` internals.
- **The camera sees contact, not allergens.** A real kitchen never shows whether peanut moved. It only shows that a knife touched a plate. Some true outcomes are hidden from every observer, such as a wash that looked fine but left residue. A gate that demands HOLD for hidden contamination would fail a correct system.
- **No published per-contact rates.** Studies of allergen cleaning and cross-contact report whether residue stayed above a detection limit or a ppm threshold. They don't report the chance that one touch moves an allergen [1][2]. Our rates are placeholders until M2 calibrates them from recordings.
- **The schema has no field for any of this.** `scenario.schema.json` has only free-form `tags`, and golden scenarios use the same `expected` block with a different meaning.

## Decision

### 1. The world (truth)

Truth is boolean: at each moment, each entity truly carries a set of allergen units. v0 models no amounts or doses.

- **Start states.** Tools, hands and plates present at shift start are truly clean. Anything taken from a `clean_stack` zone is truly clean. Invariant 8 relies on these facts, so v0 never breaks them.
- **Bins.** At world start, a bin truly contains unit `u` with probability `allergen_profile.probabilities[u]`, sampled once per world. A unit missing from the profile is never present. v0 generates no bins with `unknown: true`. We sample because a profile is a belief: sampling makes it a calibrated one, so a bin marked 0.98 peanut is truly peanut-free in about 2 worlds out of 100, and false holds become measurable.
- **Transfers.** In each true interaction, every unit the sender truly carries moves to the receiver independently, with the rate in the table below. A transfer copies; it never removes a unit from the sender. Both sides update from their states just before the interaction, so the order of updates within one event never matters.
- **Washes.** One draw per wash. With probability `wash_efficacy` the entity is truly clean afterwards; otherwise it keeps everything it carried.
- **Time.** An entity carries a unit only after a true interaction gave it that unit (invariant 5).

| Action | True movement | v0 rate | Why this value (all are unverified placeholders) |
|---|---|---|---|
| `transfer.scoop` | bin → tool | 0.90 | Scooping picks up food, so the tool nearly always ends up carrying what the bin holds |
| `transfer.scoop` | tool → bin | 0.30 | A dirty spoon dipped into a bin leaves residue in it (the shared-jar problem). Lower, because most of the flow is out of the bin |
| `transfer.contact` | both ways | 0.50 | A brief touch often moves residue, but not always. Controlled tests of shared utensils often found no transfer above threshold [2] |
| `transfer.wipe` | both ways | 0.70 | Wiping smears residue across a surface |
| `clean.wash` | resets the entity | efficacy 0.95 | Soap and wipes removed peanut allergen from every hand tested. Plain water left it on 3 of 12 hands, sanitizer on 6 of 12, and dishwashing liquid left it on 4 of 12 tables [1]. A proper wash usually works, but not always |
| `unimportant` | none | 0 | No contact, so nothing moves |

All rates live in sim's config (prefix `SIM_`) and are recorded with the generator version. They are never read from, or tuned against, the graph's action efficiency `k`. Changing any of them bumps the generator version and regenerates the CI subset (sim README, Tests).

### 2. Observations (what a scenario carries)

Every true interaction becomes one `interaction_event` with `classifier.name = sim_ground_truth`, unless an injector changes that.

- **Action, entities and times** are the true ones. v0 adds no timing noise.
- **Direction** is what the live system emits for that action, not the truth: `both` for contact and wipe (the OQ-9 default), and bin → tool for scoop (the schema's convention). When a scoop truly moves a unit from tool to bin, the observation doesn't show it. The live system has the same gap, and sim measures it instead of hiding it (rule 2 in section 3).
- **Probability** is how sure a classifier is that the action happened, not whether an allergen moved. It is `clip(mean[action] + noise, 0.05, 0.99)` with `noise ~ Normal(0, sigma)`, seeded. The means are scoop 0.90, contact 0.85, wipe 0.80 and wash 0.90. `sigma` is 0.05, and the `probability_noise` injector raises it to 0.20.
  - The number never depends on whether a unit truly moved, because that would leak truth into the observation.
  - The floor of 0.05 keeps noise from turning an emitted edge into a silent "didn't happen".
  - The cap of 0.99 is there because no classifier is certain.
- **Dropped edges** (the `dropped_edge` injector) leave out each true interaction with probability 0.05 (placeholder). The v0 happy-path generator drops nothing.
- **Track loss** (the `track_loss` injector, tools and hands only in v0):
  - Between two of its interactions, an entity loses its track with probability 0.05 (placeholder). It returns 1 to 5 s later under a new ID, `<old id>-r<n>` (for example `tool-knife-1-r1`).
  - The new ID is listed in `late_entities` with `origin: unknown` and `first_seen_t_ms` set to the return time.
  - Its true contents carry over, because it is the same object. It has no interactions while unseen (one would count as a dropped edge).
  - Why a new ID every time: vision gives every re-acquired track a new ID (invariant 8). ByteTrack itself keeps a lost track for only 30 frames by default, about 1 s at 30 FPS [3][4].
- **Late entities** (the `late_entity` injector): an entity first seen mid-shift outside a `clean_stack` zone is listed with `origin: unknown`. It truly carries each unit present in any of the shift's bins with probability 0.2 (placeholder).
- **Spurious edges** are not in v0. If they are added later, a spurious transfer can only add holds, so it keeps the tag. A spurious wash can erase contamination, so it counts as a failed wash.

### 3. What `expected` means, and what gates CI

In a generated scenario, `expected` is **world truth**:

- `allergens_reached` lists every unit truly on the plate when it reaches the pass, sorted, whatever the ticket forbids.
- `decision` is `hold` if and only if `allergens_reached` contains a unit that the ticket revision forbids. `trace_sensitive` is treated as true (the OQ-8 default), since v0 has no amounts.
- `chain_event_ids` and `reason_codes` are left out. Contamination can take several true paths at once, and reason codes belong to the verdict team's vocabulary, not to the world.

In a golden scenario (`contracts/scenarios/`), `expected` is what a correct system must output, which is a different promise. In `unknown-tool-mid-shift`, the spoon may truly be clean, yet the right answer is HOLD.

A generated scenario is **`observation-complete`** when its observations hide nothing that made a plate truly contaminated. Sim adds that tag only when all of the following hold, each checked against world truth:

1. No true interaction was dropped.
2. Every emitted event has its true action and entities and keeps the true time order. Its direction covers every way a unit truly moved, so a scoop that truly moved a unit from tool to bin breaks this rule.
3. No emitted wash failed on an entity that truly carried a unit at that moment. A failed wash on a clean entity hides nothing.
4. Every lost track returns under a new ID in `late_entities` with `origin: unknown`. This is true by construction, so track loss keeps the tag.

Why this is the right line: in an observation-complete scenario, every truly contaminating path shows up as a time-forward chain of emitted transfers, with no emitted wash on it. The chain starts from a source that declares the unit possible (a profile probability above 0, or `origin: unknown`). A system that follows the contracts (exact reachability and invariant 8) holds every such plate for any `k` in (0, 1]. So a PASS there is a bug, not bad luck.

When rule 1, 2 or 3 fails, sim adds a tag that says why: `dropped-edge`, `hidden-direction` or `failed-wash`.

| Scenario | Marked by (v0) | `expected` means | Expected HOLD | Expected PASS | Fails CI? |
|---|---|---|---|---|---|
| Golden | Hand-written in `contracts/scenarios/`, no `generated` tag | What a correct system outputs | Exact match | Exact match | Yes, on any mismatch |
| Generated, observation-complete | Tags `generated` and `observation-complete` | World truth | **Hard:** a PASS fails CI | **Target:** a HOLD counts as a false hold | Only on a false PASS |
| Generated, not observation-complete | Tag `generated`, plus `dropped-edge`, `hidden-direction` or `failed-wash` | World truth | Measured: false-pass rate per tag | Target | No |


Sim's tests check these exact tag strings: `generated`, `observation-complete`, `dropped-edge`, `hidden-direction` and `failed-wash`. `observation-complete` never appears without `generated`, and a golden scenario never carries `generated`.

### 4. The schema gap, and the RFC we propose

`scenario.schema.json` can express all of this only through free-form tags. That has three costs:

- A typo such as `observation_complete` silently moves a scenario out of the CI gate, and nothing fails.
- The schema can't stop a golden scenario from carrying `generated`, which would quietly soften an exact match into a target.
- The generator version and seed have no home, even though the sim README promises byte-for-byte reproduction from them.

A field is needed before anything gates on generated corpora (the verdict team's M1 corpus gate, and sim-07). Until then, the tags above are the rule. Here is the proposed RFC, for mgr-sim to open as a `type:rfc` issue:

- **Change:** add an optional top-level object to `scenario`:
  ```json
  "generation": {
    "type": "object",
    "additionalProperties": false,
    "required": ["generator_version", "seed", "observation_complete"],
    "properties": {
      "generator_version": { "type": "string" },
      "seed": { "type": "integer", "minimum": 0 },
      "observation_complete": { "type": "boolean" }
    }
  }
  ```
- **Meaning:** when the object is absent, the scenario is hand-written and needs an exact match. That is the strictest reading, so every existing file and reader stays safe. When it is present, `expected` is world truth. An expected HOLD is hard when `observation_complete` is true, and an expected PASS is a target.
- **Producers and consumers:** sim produces it. The verdict team's corpus gate, platform's e2e harness and `FakeProvenanceGraph` and the partner's graph tests consume it.
- **Breaking:** no. The field is optional and additive, so `contracts/VERSION` goes from 0.1.0 to 0.2.0. Add a valid fixture with the field, plus invalid fixtures for a missing `seed` and for an unknown key inside the object.
- **Migration:** during M1, sim writes both the field and the tags. After that, the tags stay as labels for people.
- **Safety impact:** a typo can no longer drop a scenario out of the hard gate, and the seed makes every false PASS reproducible.

## Consequences

- Sim's answers depend only on the world, so a graph or verdict bug can't hide inside its own test. The cost is that a correct system will miss soft expectations by design. For example, a clean tool that loses its track and then touches a plate is an expected PASS, but a correct system holds it (unknown history). That counts as a false hold, never a CI failure.
- Hidden failures (failed washes, dropped edges, scoop back-transfer) never fail CI. The metrics script (sim-07) reports their false-pass rates per tag. Those numbers show what each sensing failure costs, for the partner (OQ-9, wash handling) and for the owner.
- **Watch: `FakeProvenanceGraph` answers from `expected`.** For a generated scenario, that is world truth. It includes units no observer could see, and it leaves out the unknown-history holds a correct graph must report. So a verdict test that runs generated scenarios through the fake checks nothing, or checks the wrong thing. A meaningful corpus gate needs the partner's real graph (`needs-partner`), or must check only the hard side of the decision. The RFC's field lets the fake and the harness tell the two kinds of scenario apart.
- Every rate is a placeholder. M2 calibrates the rates, and the track-loss rate, from recorded live sessions. Until then, a result that depends on a rate's exact value belongs in a sensitivity sweep, not in a gate.
- Sim's tests enforce this ADR (sim-01 and sim-06):
  - `test_truth_from_world`: `expected` comes from world truth.
  - `test_tags` and `test_tag_semantics`: each rule in section 3 adds or removes the right tag.
  - `test_no_forbidden_imports`.
  - A determinism test for each (generator version, seed).

## Sources

1. Perry TT, Conover-Walker MK, Pomés A, Chapman MD, Wood RA. "Distribution of peanut allergen in the environment." *J Allergy Clin Immunol.* 2004;113(5):973–976. doi:10.1016/j.jaci.2004.02.035. The results come from the abstract as quoted in search results and from ScienceDaily's summary of 10 May 2004. We did not read the full text.
2. Damasceno RPB et al. "Risk of gluten cross-contamination due to food handling practices: a mini-review." *Nutrients.* 2024;16(8):1198. doi:10.3390/nu16081198. Read in full on PubMed Central (PMC11055037). It summarizes Studerus et al. and Weisbrod et al. (*Gastroenterology* 2020;158(1):273–275): shared knives, colanders, ladles and spoons did not push samples above 20 ppm gluten, and rinsing removed it. Its results are reported in ppm, not as per-contact rates. The Weisbrod volume and pages come from search results.
3. Zhang Y et al. "ByteTrack: Multi-Object Tracking by Associating Every Detection Box." ECCV 2022. arXiv:2110.06864.
4. ByteTrack reference code, `tools/demo_track.py` at github.com/ifzhang/ByteTrack: `--track_buffer` defaults to 30 ("the frames for keep lost tracks"). Checked on 2026-10-23.

**Unverified:** every number in sections 1 and 2 (transfer rates, wash efficacy, noise, drop, track-loss and late-entity rates). They are engineering placeholders, not measurements.
