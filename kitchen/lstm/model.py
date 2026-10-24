"""The ConvLSTM or ConvGRU network over one pair's clip: two box masks and a change mask over time, with elapsed time.

Owner: partner (human). Agents never edit this file.
Step:  LSTM classifier
Reads: interaction_clip tensors and dt_ms
Emits: per-head probabilities (contact, material moved)
Rules: 6 no object pixels, classes or allergen data in the input
Tasks: partner

Built. Remember to train with BCEwithlogitsloss
"""

import torch
from torch import nn


class ConvGRUCell(nn.Module):
    def __init__(self, in_ch, hid_ch, k=3):
        super().__init__()
        self.gates = nn.Conv2d(in_ch + hid_ch, 2 * hid_ch, k, padding=k // 2)
        self.cand = nn.Conv2d(in_ch + hid_ch, hid_ch, k, padding=k // 2)

    def forward(self, x, h):
        z, r = torch.sigmoid(self.gates(torch.cat([x, h], dim=1))).chunk(2, dim=1)
        n = torch.tanh(self.cand(torch.cat([x, r * h], dim=1)))
        return (1 - z) * h + z * n


class InteractionNet(nn.Module):
    def __init__(self, inch=4, hidch=16):
        super().__init__()
        self.hidch = hidch
        self.pool = nn.AdaptiveAvgPool2d(1)
        self.cell = ConvGRUCell(inch, hidch)
        self.head = nn.Sequential(nn.Flatten(), nn.Linear(hidch, 32), nn.ReLU(), nn.Linear(32, 2))

    def add_dt_channel(self, x, dt_ms):
        b, t, _, h, w = x.shape
        dt = dt_ms.float() / 100.0  # ~0.6 per step at 16 steps/s, a nice small number
        dt_plane = dt.view(b, t, 1, 1, 1).expand(b, t, 1, h, w)
        return torch.cat([x, dt_plane], dim=2)

    def forward(self, x, dt_ms):
        # Add elapsed time as one more channel, so the GRU knows how much time passed between steps.
        x = self.add_dt_channel(x, dt_ms)
        b, t, _, h, w = x.shape
        hidden = x.new_zeros(b, self.hidch, h, w)
        for step in range(t):
            hidden = self.cell(x[:, step], hidden)
        return self.head(self.pool(hidden))


class BoxPooledInteractionNet(nn.Module):
    """The same ConvGRU idea, with two changes so the head can tell where the change happened relative to the boxes.

    1. A strided stem halves the frame (128 to 64) before the GRU, so each cell sees a wider area and the GRU's memory
       can connect A's box to B's box.
    2. Instead of only averaging the GRU state over the whole frame (where a small contact region gets diluted by
       background), the head also reads the state inside box A, inside box B, and where they overlap, plus the
       whole-frame maximum.

    Same inputs and outputs as InteractionNet: clips [B, T, 3, H, W] (box_a, box_b, change) and dt_ms [B, T], two logits.
    """

    def __init__(self, inch=4, hidch=32):
        super().__init__()
        self.hidch = hidch
        self.stem = nn.Sequential(nn.Conv2d(inch, hidch, 3, stride=2, padding=1), nn.ReLU())
        self.cell = ConvGRUCell(hidch, hidch)
        self.head = nn.Sequential(nn.Linear(5 * hidch, 64), nn.ReLU(), nn.Linear(64, 2))

    def forward(self, x, dt_ms):
        # The same elapsed-time channel as InteractionNet (the method only reads x and dt_ms).
        x = InteractionNet.add_dt_channel(self, x, dt_ms)
        b, t, c, h, w = x.shape
        features = self.stem(x.reshape(b * t, c, h, w)).reshape(b, t, self.hidch, h // 2, w // 2)

        hidden = x.new_zeros(b, self.hidch, h // 2, w // 2)
        for step in range(t):
            hidden = self.cell(features[:, step], hidden)

        # Where A and B were at any step, at the GRU's resolution (channel 0 is box_a, channel 1 is box_b).
        where_a = nn.functional.avg_pool2d(x[:, :, 0].amax(dim=1, keepdim=True), 2)
        where_b = nn.functional.avg_pool2d(x[:, :, 1].amax(dim=1, keepdim=True), 2)

        def mean_inside(region):
            return (hidden * region).sum(dim=(2, 3)) / (region.sum(dim=(2, 3)) + 1e-6)

        pooled = [
            hidden.mean(dim=(2, 3)),
            hidden.amax(dim=(2, 3)),
            mean_inside(where_a),
            mean_inside(where_b),
            mean_inside(where_a * where_b),
        ]
        return self.head(torch.cat(pooled, dim=1))
