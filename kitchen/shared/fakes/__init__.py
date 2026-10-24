"""Stand-ins for the database and for every partner-owned part, so agents build and test without waiting on anyone.

Owner: platform (agents)
Step:  shared
Reads: contracts/apis.md, contracts/state-tables.md, contracts/scenarios/*.json, kitchen.shared.interfaces
Emits: the fakes below, plus the scenario helpers
Rules: each fake passes the same suite in kitchen/shared/contract_tests as the real implementation; each can be
       told to fail (fail closed); fakes of partner parts do no domain math

Fakes that live here (platform):
  FakeStateClient      dict-backed StateClient with subscribe() and failure injection (state_client.py);
                       never deletes entities, never changes first_seen_t_ms or origin, keeps entity history append-only
  load_scenario        writes a golden scenario's starting world into any StateClient; scenario_timeline lists its
                       events, amendments and plates reaching the pass in time order (scenarios.py)
  FakeEntityStore      EntityStore that applies ObservationFrames to a dict; start_sync() writes register_entity and
                       move_entity calls to whatever StateClient it gets (entity_store.py; stands in for kitchen/entities)
  FakeProvenanceGraph  ProvenanceGraph that answers reach() and unknown_ancestors() from a scenario's ground truth
                       (expected allergens_reached, chain_event_ids, late_entities); no graph math
                       (provenance_graph.py; stands in for kitchen/graph)
  FakeClassifier       InteractionClassifier returning scripted labels, or raising on cue
                       (classifier.py; stands in for kitchen/interactions zone rules and kitchen/lstm)
  FakeClock            re-exported from kitchen.shared.clock

Fakes other teams ship (with the code they stand in for):
  FakeLayaParser       kitchen/orders (orders)
  FakeTranscriber      kitchen/orders (orders)
  FakeVisionAPI        kitchen/vision (vision)
  FakeVerdictFeed      kitchen/verdict (verdict)
  FakeGate             kitchen/gate (outputs)
  FakeSpeaker          kitchen/gate (outputs)
"""

from kitchen.shared.clock import FakeClock
from kitchen.shared.fakes.classifier import FakeClassifier
from kitchen.shared.fakes.entity_store import FakeEntityStore
from kitchen.shared.fakes.provenance_graph import FakeProvenanceGraph
from kitchen.shared.fakes.scenarios import (
    GOLDEN_SCENARIO_PATHS,
    TimelineItem,
    load_scenario,
    read_scenario,
    scenario_timeline,
)
from kitchen.shared.fakes.state_client import FakeStateClient

__all__ = [
    "GOLDEN_SCENARIO_PATHS",
    "FakeClassifier",
    "FakeClock",
    "FakeEntityStore",
    "FakeProvenanceGraph",
    "FakeStateClient",
    "TimelineItem",
    "load_scenario",
    "read_scenario",
    "scenario_timeline",
]
