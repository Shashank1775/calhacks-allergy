# Sim's ground truth and observation model

- **Status:** accepted
- **Date:** 2026-10-23
- **Decided by:** director, sim manager

## Context

Sim builds kitchens whose truth we know, so the partner's graph and the verdict policy can be tested against answers neither produced. If expected answers came from noisy-OR or the verdict policy, a bug in either would be copied into its own test. The camera sees contact, not allergens, and there are no published per-contact transfer rates.

## Decision

- **Truth is boolean.** Each entity truly carries a set of allergens. Tools, hands and plates at shift start, and anything taken from a `clean_stack` zone, are truly clean. A bin truly contains an allergen with the probability its profile gives, sampled once per world.
- **Transfers copy, never remove.** Each unit the sender truly carries moves to the receiver with a placeholder rate: scoop bin → tool 0.90, scoop tool → bin 0.30, contact 0.50 both ways, wipe 0.70 both ways. A wash truly cleans with probability 0.95. Rates live in sim settings (`SIM_`), never in the graph's settings.
- **Observations.** Every true interaction becomes one `interaction_event` (`classifier.name = sim_ground_truth`). Its probability is how sure a classifier is that the action happened, never whether an allergen moved: a mean per action plus seeded noise, clipped to [0.05, 0.99]. Injected mistakes can drop edges, lose tracks (the entity returns under a new ID with `origin: unknown`) or add late entities.
- **What `expected` means.** In a generated scenario it is world truth: the allergens truly on the plate, and HOLD exactly when one is forbidden. In a hand-written golden scenario it is what a correct system must output.
- **Tags.** Generated scenarios carry `generated`. They also carry `observation-complete` only when nothing that made a plate truly contaminated was hidden; otherwise `dropped-edge`, `hidden-direction` or `failed-wash` says why. An expected HOLD in an observation-complete scenario is hard (a PASS fails sim's tests); an expected PASS is a target (a HOLD counts as a false hold).

## Consequences

- A correct system misses some expected PASSes by design (for example, a tool that touched an allergen bin without picking anything up still carries the edge's probability, so its plate holds).
- `FakeProvenanceGraph` answers from `expected`, so running generated scenarios through it checks nothing. Gating on generated corpora needs the partner's real graph.
- Every rate is a placeholder until recorded live sessions calibrate it.
- If a typo in a tag ever matters, add an optional `generation` field (generator version, seed, observation-complete) to the scenario schema in a contracts PR.
