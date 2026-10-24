"""The one change-mask function: consecutive RGB frames to a single-channel gradient (brighter = more movement).

Owner: platform (agents)
Step:  shared
Reads: two letterboxed RGB frames, the previous mask, gain / blur_sigma_px / decay
Emits: an H x W mask in [0, 1]
Rules: identical for live frames (vision) and sim renders (sim), so training matches live; parameters recorded in every clip

Not built yet.
"""
