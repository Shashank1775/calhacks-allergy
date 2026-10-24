# kitchen/profiling: Entity profiling

**Owner:** team `orders` (agents), alongside [`kitchen/orders/`](../orders/README.md).

**Pipeline step:** 2 of the pipeline. At shift start, every bin gets an allergen profile from its label or prep sheet, and a chef confirms anything unclear. See [AGENTS.md §2](../../AGENTS.md#2-pipeline-and-code-map).

## What happens here

- Laya reads each bin's label text or prep sheet and assigns allergen units with probabilities.
- Ingredients Laya can't place become a one-time chef confirmation. "Unclear" never means "clean".
- The profile is written to the `bin_profile` table. Tools, hands and plates start clean; their profiles come from the partner graph, not from here.
- Optional (M4): an Apify Actor imports the restaurant's online menu to seed prep sheets.

## Files

| File | What it does |
|---|---|
| `bin_labels.py` | Label or prep sheet → `AllergenProfile` with probabilities and `unclear_ingredients` |
| `chef_confirm.py` | Opens one confirmation per unclear ingredient, applies answers, and sets `confirmed_by_chef` when none remain |
| `menu_import.py` | Optional Apify menu import (Website Content Crawler, per [ADR 0013](../../docs/decisions/0013-cartesia-ink-and-apify-menu-import.md)) that seeds prep-sheet text |

The HTTP endpoints (`/v1/profiles/bins`, `/confirm`, `/v1/menu/import`) live in [`kitchen/orders/api.py`](../orders/api.py).

## Inputs and outputs

| Direction | What | Contract | From / to |
|---|---|---|---|
| In | Bin label text, prep sheet | [Orders HTTP API](../../contracts/apis.md#orders-http-api) `/v1/profiles/bins` | the person setting up the shift |
| In | Chef answers | `/v1/profiles/bins/{entity_id}/confirm` | outputs chef screen |
| Out | Bin profile (`bin_profile` table) | [`allergen_profile`](../../contracts/schemas/allergen_profile.schema.json) via `StateClient.put_bin_profile` | partner graph, verdict, outputs |
| Out | Chef confirmations (`chef_confirmation` table) | `StateClient.open_chef_confirmation` / `answer_chef_confirmation` | outputs chef screen |
| Uses | Laya | [`LayaParser.profile_bin`](../../contracts/apis.md#layaparser) | fine-tuned Laya |
| Uses | Apify Actor | `/v1/menu/import` | external, optional |

Bin entity IDs come from vision (bins are identified by slot: `bin-4` is whatever sits in `slot-4`).

## Safety rules that apply

See [AGENTS.md §3](../../AGENTS.md#3-safety-rules). Tests are marked `safety`.

- **1. Fail closed.** An unclear ingredient always opens a chef confirmation, and "none of these" is an explicit answer, never a default. Laya unavailable means an error and manual entry, never an empty profile. Test: a profile with unclear ingredients is never written as `confirmed_by_chef`.
- **4. Allergen units only.** Profiles contain allergen unit IDs from [`allergen_units.json`](../../contracts/allergen_units.json) and nothing else. Test: every emitted profile validates against `allergen_profile.schema.json`.

## Fakes

- **We use:** `FakeLayaParser.profile_bin` (from `kitchen/orders/laya_parser.py`), `InMemoryStateClient` (platform), and a recorded menu JSON fixture for Apify.
- **We ship:** seeded chef-confirmation examples in `kitchen/orders/fakes/http/` for the outputs chef screen.

## Tests

Tests live in `kitchen/profiling/tests/`. Run `uv run pytest kitchen/profiling`.

## Milestones

- **M0:** nothing beyond the Apify spike (orders-04).
- **M1:** bin profiling and chef confirmation on `FakeLayaParser`, writing through `StateClient` (orders-09).
- **M2:** shift-start profiling run with real bin labels in the demo kitchen.
- **M3:** fine-tuned Laya profiling from label text and prep sheets, with unclear items routed to the chef.
- **M4:** optional Apify menu import seeds prep sheets.

## Risks and open questions

| Risk | Mitigation |
|---|---|
| Laya reads text, not images | Text labels and prep sheets only; a photo would need OCR first |
| Coarse units hide exceptions (OQ-2) | Free-text ingredient on the parent unit until OQ-2 is answered |
| Bins swapped between slots mid-shift | Out of scope for the demo (vision's identity rule). Raise it with the owner if it matters |
| Menu sites vary (JavaScript, PDF, bot walls) | [ADR 0013](../../docs/decisions/0013-cartesia-ink-and-apify-menu-import.md): Apify's own crawler, a capped run, and an error with nothing seeded when no text comes back. The feature is optional, and only seeds what the chef confirms |
