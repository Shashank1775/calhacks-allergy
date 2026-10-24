"""Trains the model on sim-rendered clips, fine-tunes and checks on labeled real clips.

Owner: partner (human). Agents never edit this file.
Step:  LSTM classifier
Reads: sim clip datasets (sim-13 to sim-15), labeled real clips (interactions-12), never the frozen held-out set
Emits: weights in data/ listed in data-manifest.json (never in git)
Rules: 6 object-blind; 7 never trains on the frozen held-out benchmark
Tasks: partner

Not built yet.
"""
