"""The one clip builder: a pair's boxes and change masks over time to an interaction_clip and its [T, C, H, W] tensor.

Owner: platform (agents)
Step:  shared
Reads: ClipStep sequences (t_ms, box_a, box_b, other boxes, change mask)
Emits: interaction_clip metadata + tensor; PairWindow helpers
Rules: blind classifier: no object pixels, classes, kinds or allergen data; same builder live, in sim and in partner training

Not built yet.
"""
