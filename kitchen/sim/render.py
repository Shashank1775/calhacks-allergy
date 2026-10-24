"""2D clip renderer: a simulated world run drawn as abstract frames, turned into labeled ConvLSTM clips.

Owner: sim (agents)
Step:  simulation
Reads: world runs (kitchen.py); kitchen/shared/change_mask.py and clips.py
Emits: interaction_clip metadata + npz tensors with labels from world truth (contact, transferred, action, direction)
Rules: 6 never render realistic utensils, plain shapes and food blobs only; labels from the world, never from a model; deterministic per seed
Tasks: sim-12, sim-13, sim-14

Not built yet.
"""
