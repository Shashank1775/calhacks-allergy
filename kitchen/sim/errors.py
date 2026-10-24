"""Error injectors: skipped wash, shared utensil, bare-hand contact, late entity, track loss, amendment, dropped edge, probability noise.

Owner: sim (agents)
Step:  simulation
Reads: world runs and observations
Emits: modified world runs or observations (scenario tags updated, e.g. dropped_edge removes observation-complete)
Rules: change the world or the observation, never the expected answer; 8 track loss re-identifies as an unknown entity
Tasks: sim-06

Not built yet (phase gate: PLANNING).
"""
