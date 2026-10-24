"""Stand-ins for the database and for every partner-owned part, so agents build and test without waiting on anyone.

Owner: platform (agents)
Step:  shared
Reads: contracts/apis.md, contracts/scenarios/*.json, kitchen.shared.interfaces
Emits: the fakes below
Rules: each fake passes the same suite in kitchen/shared/contract_tests as the real implementation; each can be
       told to raise its *Unavailable error or hang (invariant 1); fakes of partner parts do no domain math
Tasks: platform-04, platform-06

Fakes that live here (platform):
  InMemoryStateClient  dict-backed StateClient with subscribe(), failure injection and a scenario loader
                       (load_scenario: bins, late entities, tickets, plate bindings, events, in time order);
                       never deletes entities, never changes first_seen_t_ms or origin, keeps every ticket revision
  FakeEntityStore      EntityStore that applies ObservationFrames to a dict; start_sync() writes snapshots to
                       whatever StateClient it gets (stands in for the partner's kitchen/entities)
  FakeProvenanceGraph  ProvenanceGraph that answers reach(), subgraph() and unknown_ancestors() from a scenario's
                       ground truth (expected allergens_reached, chain_event_ids, late_entities); no graph math
                       (stands in for the partner's kitchen/graph)
  ScriptedClassifier   InteractionClassifier returning scripted labels, or erroring or hanging on cue
                       (stands in for the partner's LSTM in kitchen/lstm)
  FixedClock           re-exported from kitchen.shared.clock

Fakes other teams ship (with the code they stand in for):
  FakeLayaParser       kitchen/orders (orders)
  TextFileTranscriber  kitchen/orders (orders)
  ExactLaya            kitchen/verdict (verdict): answers with ProvenanceGraph.reach
  AdversarialLaya      kitchen/verdict (verdict): random, late, empty or malformed answers
  ConsoleGate          kitchen/gate (outputs)
  MockGPIOGate         kitchen/gate (outputs)
  ConsoleSpeaker       kitchen/gate (outputs)

Not built yet.
"""
