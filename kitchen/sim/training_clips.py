"""ConvLSTM training clips: world runs drawn as abstract frames, run through the shared change mask and clip builder,
labeled from world truth (with hard negatives), and split into leak-free train/val/test sets.

Owner: sim (agents)
Step:  simulator
Reads: world runs (world.py); kitchen/shared/change_mask.py and clips.py
Emits: data/clips/<version>/{train,val,test}/ with interaction_clip metadata, npz tensors and a manifest
Rules: blind classifier: plain shapes and food blobs only, never realistic utensils; labels from the world, never a model;
       no seed or kitchen layout in two splits; deterministic per seed

Not built yet.
"""
