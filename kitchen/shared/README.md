# kitchen/shared: models, interfaces, clock and fakes

**Owner:** team `platform` (agents). Records are plain dicts checked against the contract schemas; nothing is generated for Python.

Everything the other folders import to talk to each other. Every Python folder in `kitchen/` depends on this one; this one depends on nothing else in `kitchen/`. If two teams need the same type, interface or fake, it lives here, written against [`contracts/`](../../contracts/README.md).

## Files

| File | What it does |
|---|---|
| `models.py` | `validate(record, schema)` checks a dict against `contracts/schemas/<schema>.schema.json` (Draft 2020-12, cross-file `$ref`s resolved) and raises `jsonschema.ValidationError`. Readable aliases (`Ticket`, `Entity`, `Verdict`, ... are all `dict[str, Any]`) and `Action`. The table dataclasses `Zone`, `PlateBinding`, `EntityState` and `ChangeEvent` |
| `interfaces.py` | Every Protocol in [`contracts/apis.md`](../../contracts/apis.md), the `PairWindow` dataclass, and the `*Unavailable` exceptions |
| `clock.py` | `SystemClock` (live, monotonic) and `FakeClock` (tests and replay; `advance(ms)`, `set(t_ms)`, never backwards) |
| `config.py` | `load_settings(SettingsClass, prefix="VERDICT_")` fills a dataclass from env vars; `require_secret(name)`; `log_settings(settings)` logs one JSON line at startup |
| `fakes/` | `FakeStateClient`, `load_scenario` and `scenario_timeline`, `FakeEntityStore`, `FakeProvenanceGraph`, `FakeClassifier`, `FakeClock` |
| `contract_tests/` | One pytest suite per interface; every fake passes it, and the partner's real code must too |
| `change_mask.py` | The one change-mask function (RGB frame pair → gradient mask), used by vision on real frames and by sim on rendered frames |
| `clips.py` | The one clip builder (boxes + change masks over time → `interaction_clip` + `[T, C, H, W]` tensor), used by sim and by the partner |

TypeScript types for `web/` are generated from the same schemas into `web/src/generated/`.

## Using it

```python
from kitchen.shared.models import validate
from kitchen.shared.fakes import FakeStateClient, FakeProvenanceGraph, load_scenario, scenario_timeline

validate(ticket, "ticket")                      # raises jsonschema.ValidationError if the dict breaks the schema

state = FakeStateClient()
load_scenario(state, "contracts/scenarios/late-amendment-adds-dairy.json")   # bins, entities, tickets, bindings, edges
graph = FakeProvenanceGraph("contracts/scenarios/late-amendment-adds-dairy.json")
for item in scenario_timeline("contracts/scenarios/late-amendment-adds-dairy.json"):
    ...  # item.kind is "event", "amendment" or "plate_at_pass"; play amendments with state.put_ticket(item.record)

state.set_unavailable(True)                     # every StateClient method now raises StateUnavailable
state.fail_next(2)                              # or just the next two calls
graph.set_unavailable(True)                     # reach() and unknown_ancestors() raise GraphUnavailable
```

## Rules

- **Schemas are the types.** A record is a dict; `validate()` is the check. A shape change is a contracts PR, never a local field.
- **Signatures match `contracts/apis.md` exactly.** A method that isn't in apis.md doesn't go in `interfaces.py`; `tests/test_interfaces.py` compares the two.
- **Fakes pass the same contract tests as the real thing.** If a fake and a real implementation disagree, one of them has a bug.
- **Fakes for partner parts do no domain math.** `FakeProvenanceGraph` answers from a scenario's ground truth (`expected`), never by propagating allergens. It reports each allergen with probability 1 and raises `GraphUnavailable` for a plate the scenario doesn't cover.
- **Fail-closed hooks.** `FakeStateClient` and `FakeProvenanceGraph` can be told to raise their `*Unavailable` error; `FakeClassifier` raises `RuntimeError` (apis.md names no classifier exception).
- **One implementation for training and live.** Nobody else writes change-mask or clip code. `clips.py` has no parameter for object pixels, classes or kinds.
- **Known-clean source.** `FakeStateClient` and `FakeEntityStore` never delete entities and never change `first_seen_t_ms` or `origin`. Only a tool, hand or plate first seen with a clean origin starts clean; bins and unknown-origin entities start unknown.

## Contract tests

Each suite in `contract_tests/` is a module of test functions marked `contract` that take factory fixtures. Here, `tests/conftest.py` points them at the fakes. To run a suite against real code, the partner adds the same fixture names to a `conftest.py` in their own folder and a test file that imports the suite:

```python
# kitchen/graph/tests/conftest.py
import pytest
from kitchen.shared.fakes import FakeStateClient, load_scenario
from kitchen.graph.graph import ProvenanceGraph   # the partner's real class (name is theirs)


@pytest.fixture
def make_graph():
    def make(scenario):
        state = FakeStateClient()
        load_scenario(state, scenario)
        return ProvenanceGraph(state)
    return make


@pytest.fixture
def make_unavailable_graph():
    def make(scenario):
        state = FakeStateClient()
        load_scenario(state, scenario)
        graph = ProvenanceGraph(state)
        state.set_unavailable(True)
        return graph
    return make
```

```python
# kitchen/graph/tests/test_graph_contract.py
from kitchen.shared.contract_tests.provenance_graph import *  # noqa: F403
```

Then `uv run pytest kitchen/graph -m contract`. The fixtures each suite needs:

| Suite | Fixtures |
|---|---|
| `contract_tests/state_client.py` | `make_state_client()` (new, empty client), `make_unavailable_state_client()` (database unreachable) |
| `contract_tests/entity_store.py` | `make_entity_store()` (new, empty store); it syncs into a `FakeStateClient` |
| `contract_tests/provenance_graph.py` | `make_graph(scenario)`, `make_unavailable_graph(scenario)`; runs every golden scenario |
| `contract_tests/interaction_classifier.py` | `make_classifier()`; its `classify()` test needs numpy for the window's tensor and skips without it |

## Tests

```bash
uv run pytest kitchen/shared
uv run pytest kitchen/shared -m contract     # just the contract suites, against the fakes
```

- Every file in `contracts/fixtures/valid/` passes `validate()` and every file in `fixtures/invalid/` fails it (`safety`).
- Every scenario in `contracts/scenarios/` loads into `FakeStateClient`.
- Every fake passes its suite in `contract_tests/`.
- `interfaces.py` matches the python blocks in `contracts/apis.md`, method and parameter names included.

## Watch out for

- `validate()` finds `contracts/` two folders above this one, so it needs the repo checkout (the editable install `uv sync` makes). A wheel without `contracts/` can't validate.
- In `FakeStateClient.get_entity`, allergens come from the newest `entity_state` row with `caused_by` set (the graph's), else the bin profile, else the entity as registered. Zone and box come from the newest row of any kind. The partner's real join may differ; the contract suite only checks what state-tables.md promises.
- Fakes can drift from the partner's real behavior. One contract suite runs against both, and the partner reviews changes to fakes of their parts.
