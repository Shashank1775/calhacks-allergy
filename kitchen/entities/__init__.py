"""Entity database on the Pi: keeps every entity seen this shift in memory and syncs it to SpacetimeDB.

Owner: partner (written by an agent at the owner's request)
Step:  Entity database
Reads: observation_frame (EntityStore.ingest, called by vision)
Emits: Entity (get, snapshot); entity rows via StateClient.register_entity, entity_state rows via move_entity
Rules: 8 never delete, never change first_seen or origin; 5 frames only move forward; 1 sync failures visible
Tasks: partner

Built so far: store.py (EntityStore, the real EntityStore) and sync.py (EntitySync, its 5 Hz background sync). It
passes the shared contract suite (kitchen/entities/tests). Vision doesn't call it yet; agents still test on
FakeEntityStore.
"""
