"""Laya training data: (laya_request, target laya_response) pairs with leak-free train/val/test splits.

Owner: sim (agents)
Step:  simulation
Reads: scenario corpus with world truth
Emits: laya_request and laya_response JSONL per split, plus a manifest with sha256
Rules: no seed, scenario or kitchen/recipe family in two splits
Tasks: sim-08

Not built yet.
"""
