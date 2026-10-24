"""ConvLSTM clip datasets: balanced sets with hard negatives, leak-free splits, manifest and datasheet for the partner.

Owner: sim (agents)
Step:  simulation
Reads: rendered clips (render.py)
Emits: data/clips/<version>/{train,val,test}/ plus manifest.json (counts, sha256, label balance, generator version)
Rules: no seed or kitchen layout in two splits; nothing over 1 MB in git
Tasks: sim-15

Not built yet.
"""
