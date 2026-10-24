"""The ConvLSTM or ConvGRU network over one pair's clip: two box masks and a change mask over time, with elapsed time.

Owner: partner (human). Agents never edit this file.
Step:  LSTM classifier
Reads: interaction_clip tensors and dt_ms
Emits: per-head probabilities (contact, material moved)
Rules: 6 no object pixels, classes or allergen data in the input
Tasks: partner

Not built yet.
"""
