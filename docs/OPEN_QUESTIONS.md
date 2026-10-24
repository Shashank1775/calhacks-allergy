# Open questions

Each has an ID that code, contracts and issues can cite. **Blocking** questions must be answered before the phase gate opens. The Director keeps this list current; resolved questions move to the bottom with their answer and a link to the ADR or issue.

| ID | Question | Owner of the answer | Blocking? | Default if unanswered |
|---|---|---|---|---|
| OQ-1 | How does Python (Pi and laptop services) read and write SpacetimeDB: HTTP reducer calls plus SQL polling, a small bridge process using an official SDK, or a community Python SDK? | state, via ADR | No (M0 task) | HTTP reducer calls, plus polling for subscriptions |
| OQ-2 | Are allergen units only the nine majors, or do we need child units (e.g. `coconut`, `almond` under `tree_nut`) so exceptions like "coconut is fine" can be represented exactly? | Owner | **Yes** | Nine majors; exceptions stored as free-text ingredient on the parent unit |
| OQ-3 | Default severity per allergen unit (currently `allergy` for all, a placeholder). | Owner | No | `allergy` |
| OQ-4 | Pass threshold (minimum clean-confidence) per severity: `preference`, `intolerance`, `allergy`, `anaphylactic`. | Owner | **Yes** | 0.80 / 0.90 / 0.95 / 0.99 |
| OQ-5 | What backs Jev and Laya? Provider or base model, hosting (local vs API), cost, offline story, and who holds the keys. | Owner | **Yes** | Both behind interfaces with fakes until answered |
| OQ-6 | Camera overlays: drawn client-side on the expo board from entity boxes, or burned into the Pi's MJPEG stream? | perception + experience | No | Client-side |
| OQ-7 | Where does SpacetimeDB run for the demo: local standalone on the laptop, or Maincloud? | Owner | No | Local standalone (offline-friendly) |
| OQ-8 | What does `trace_sensitive: false` change in the verdict? Proposal: only `transfer.scoop` edges count toward the hold, while contact and wipe edges are ignored for that constraint. | provenance, via ADR, owner approves | No | Every transfer edge counts, i.e. treat everything as trace-sensitive (the strict reading) |
| OQ-9 | Edge direction for `transfer.contact` and `transfer.wipe`: always both ways, or inferred? | interactions + provenance | No | Both ways |
| OQ-10 | Timeline: is there a hard deadline (e.g. a hackathon demo), and how many hours of agent time per day? | Owner | **Yes** | No dates; milestones in order |
| OQ-11 | Hardware inventory: Pi model, Hailo-8 form factor (AI HAT+ or M.2), camera model and mount, servo, lights, buzzer, speaker. | Owner | No (needed by M2) | Pi 5 + Hailo-8 AI HAT+ + Pi Camera Module 3 |
| OQ-12 | Who records and labels the real clips for training and evaluating the LSTM and zone rules, and with what tool? | Owner + interactions | No (needed by M3) | Owner records; interactions provides a labeling script |
| OQ-13 | How does the unknown-history profile enter noisy-OR: as probability 1 for every unit, or as a prior? | provenance, via ADR | No | Probability 1 for every unit (strictest) |
| OQ-14 | One camera or several? | Owner | No | One |
| OQ-15 | Budget cap for agent tokens and for paid APIs (Jev, Laya, Cartesia, Apify). | Owner | **Yes** | Owner sets the concurrency cap in the launch prompt |
| OQ-16 | Licensing: Ultralytics YOLOv8 code and weights are AGPL-3.0. Is that acceptable for this project (private repo, demo), or do we need another detector or a commercial license? | Owner | No (needed before perception ships M2) | Acceptable for a non-distributed demo; revisit before any release |
| OQ-17 | How are edges with equal or overlapping timestamps ordered in propagation? | provenance, via ADR | No | Order by `t_start_ms`, then `t_end_ms`, then `event_id`; a wash tied with a transfer is applied before it, so a tie can never erase contamination (strict) |

## Resolved

_None yet._
