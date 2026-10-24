"""Stand-ins for the database and for every partner-owned part, so agents build and test without waiting on anyone.

Owner: platform (agents)
Step:  shared
Reads: contracts/apis.md, contracts/scenarios/*.json, kitchen.shared.interfaces
Emits: the fakes below
Rules: each fake passes the same suite in kitchen/shared/contract_tests as the real implementation; each can be
       told to raise its *Unavailable error or hang (fail closed); fakes of partner parts do no domain math

Fakes that live here (platform):
  FakeStateClient      dict-backed StateClient with subscribe(), failure injection and a scenario loader
                       (load_scenario: bins, late entities, tickets, plate bindings, events, in time order);
                       never deletes entities, never changes first_seen_t_ms or origin, keeps every ticket revision
  FakeEntityStore      EntityStore that applies ObservationFrames to a dict; start_sync() writes snapshots to
                       whatever StateClient it gets (stands in for the partner's kitchen/entities)
  FakeProvenanceGraph  ProvenanceGraph that answers reach() and unknown_ancestors() from a scenario's
                       ground truth (expected allergens_reached, chain_event_ids, late_entities); no graph math
                       (stands in for the partner's kitchen/graph)
  FakeClassifier       InteractionClassifier returning scripted labels, or erroring or hanging on cue
                       (stands in for the partner's LSTM in kitchen/lstm)
  FakeClock            re-exported from kitchen.shared.clock

Fakes other teams ship (with the code they stand in for):
  FakeLayaParser       kitchen/orders (orders)
  FakeTranscriber      kitchen/orders (orders)
  FakeVisionAPI        kitchen/vision (vision)
  FakeVerdictFeed      kitchen/verdict (verdict)
  FakeGate             kitchen/gate (outputs)
  FakeSpeaker          kitchen/gate (outputs)

Not built yet.
"""
