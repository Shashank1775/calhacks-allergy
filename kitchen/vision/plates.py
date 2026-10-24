"""Start-ticket binding: binds the single plate at the plating spot to a ticket, or refuses.

Owner: vision (agents)
Step:  Vision (detection + tracking on the Pi)
Reads: current plates and zones, StateClient.get_plate_binding
Emits: StateClient.bind_plate -> plate_binding table
Rules: 1 never guess: 0 plates, 2+ plates or an already-bound plate -> 409
Tasks: vision-07

Not built yet.
"""
