"""Entity database on the Pi: keeps every entity seen this shift in memory and syncs it to SpacetimeDB.

Owner: partner (human). Agents never edit this file.
Step:  Entity database
Reads: observation_frame (EntityStore.ingest, called by vision)
Emits: Entity (get, snapshot); entity table via StateClient.upsert_entities
Rules: 8 never delete, never change first_seen or origin; 1 sync failures visible
Tasks: partner

Not built yet.
"""
