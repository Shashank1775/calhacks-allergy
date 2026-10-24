"""ConvLSTM / ConvGRU interaction model: implements InteractionClassifier as "lstm".

Owner: partner (human). Agents never edit this file.
Step:  LSTM classifier
Reads: interaction_clip tensors [T, C, H, W] (box_a, box_b, change) plus dt_ms
Emits: list of (action, probability): P(contact), P(material moved)
Rules: 6 object-blind and allergen-blind input; 7 adopted only after winning the benchmark, rules stay fallback
Tasks: partner

Built: model.py (InteractionNet and BoxPooledInteractionNet), train.py (trains them on sim clips), export_onnx.py (a
checkpoint to ONNX, checked against torch) and infer.py (LstmClassifier, the "lstm" InteractionClassifier over that ONNX
file). Not adopted: zone rules stay the default until the benchmark says otherwise.
"""
