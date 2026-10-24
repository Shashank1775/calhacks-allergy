"""Zones and plate binding: the zones file, a click-to-calibrate tool, zone assignment, and "start plate" binding.

Owner: vision (agents)
Step:  vision
Reads: a still image (calibration), zones.json, current plates, StateClient.get_plate_binding
Emits: a zone (or none) for each detection; StateClient.set_zones; StateClient.bind_plate
Rules: fail closed: no zone is guessed; binding refuses with 0 plates, 2+ plates or an already-bound plate

Not built yet.
"""
