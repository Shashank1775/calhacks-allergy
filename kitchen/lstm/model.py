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

class convGRUCell(nn.Module):
    def __init__(self, in_ch, hid_ch, k=3):
        super().__init__()
        self.gates = nn.Conv2d(in_ch + hid_ch, 2 * hid_ch, k, padding=k // 2)
        self.cand = nn.Conv2d(in_ch + hid_ch, hid_ch, k, padding=k // 2)

    def forward(self, x, h):
        z, r = torch.sigmoid(self.gates(torch.cat([x, h], dim=1))).chunk(2, dim=1)
        n = torch.tanh(self.cand(torch.cat([x, r * h], dim=1)))
        return (1-z) * h + z * n
    

class InteractionNet(nn.Module):
    def __init__(self, inch = 4, hidch=16):
        super().__init__()
        self.hidch = hidch
        self.pool = nn.AdaptiveAvgPool2d(1)
        self.cell = convGRUCell(inch, hidch)
        self.head = nn.Sequential(
            nn.Flatten(),
            nn.Linear(hidch, 32),
            nn.ReLU(),
            nn.Linear(32, 2)
        )

    def add_dt_channel(self, x, dt_ms):
        b, t, _, h, w = x.shape
        dt = dt_ms.float() / 100.0                    # ~0.6 per step at 16 steps/s, a nice small number
        dt_plane = dt.view(b, t, 1, 1, 1).expand(b, t, 1, h, w)
        return torch.cat([x, dt_plane], dim=2)


    def forward(self, x, dt_ms):
        x = self.add_dt_channel(x, dt_ms) #increment the channel dimension of x based on time passing to include dt_ms and affect gradients/change for lstm model (GRU actually)
        b, t, _, h, w = x.shape
        hidden = x.new_zeros(b, self.hidch, h, w)
        for step in range(t):
            hidden = self.cell(x[:, step], hidden)
        return self.head(self.pool(hidden))