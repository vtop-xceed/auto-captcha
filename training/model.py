"""Single source of truth for the captcha model architecture and vocab.

Imported by onnx.py and decode_captchas.py. Do not copy this class
anywhere else; the Rust solver (deploy/api/src/solver.rs) mirrors it.
"""

import torch
import torch.nn as nn

IMG_WIDTH = 200
IMG_HEIGHT = 40
TIME_STEPS = 50
BLANK_IDX = 0

CHARS = sorted("0123456789abcdefghijklmnopqrstuvwxyz")
VOCAB_SIZE = len(CHARS) + 1  # +1 for CTC blank token

char_to_num = {c: i + 1 for i, c in enumerate(CHARS)}
num_to_char = {i + 1: c for i, c in enumerate(CHARS)}


def encode_label(label: str):
    return [char_to_num[c.lower()] for c in label if c.lower() in char_to_num]


def decode_greedy(outputs: torch.Tensor):
    """Greedy CTC decoding.

    outputs: [T, B, vocab] from CaptchaModel.forward.
    Returns list of lowercase strings, length B.
    """
    predictions = outputs.argmax(dim=2)  # [T, B]
    decoded = []
    for b in range(predictions.size(1)):
        pred = predictions[:, b]
        chars = []
        for j in range(len(pred)):
            idx = pred[j].item()
            if idx != BLANK_IDX and (j == 0 or idx != pred[j - 1].item()):
                chars.append(num_to_char[idx])
        decoded.append("".join(chars))
    return decoded


class CaptchaModel(nn.Module):
    def __init__(self, vocab_size):
        super().__init__()
        self.cnn = nn.Sequential(
            nn.Conv2d(1, 64, kernel_size=3, padding=1),
            nn.BatchNorm2d(64),
            nn.ReLU(),
            nn.MaxPool2d(2),  # 40x200 -> 20x100
            nn.Conv2d(64, 128, kernel_size=3, padding=1),
            nn.BatchNorm2d(128),
            nn.ReLU(),
            nn.MaxPool2d(2),  # 20x100 -> 10x50
            nn.Conv2d(128, 256, kernel_size=3, padding=1),
            nn.BatchNorm2d(256),
            nn.ReLU(),
            nn.Conv2d(256, 256, kernel_size=3, padding=1),
            nn.BatchNorm2d(256),
            nn.ReLU(),
            nn.MaxPool2d((2, 1)),  # 10x50 -> 5x50
            nn.Conv2d(256, 512, kernel_size=3, padding=1),
            nn.BatchNorm2d(512),
            nn.ReLU(),
            nn.MaxPool2d((2, 1)),  # 5x50 -> 2x50
        )
        self.rnn = nn.GRU(
            1024, 256, bidirectional=True, batch_first=True,
            num_layers=2, dropout=0.3,
        )
        self.fc = nn.Linear(512, vocab_size)

    def forward(self, x):
        # x: [B, 1, 40, 200]
        x = self.cnn(x)
        x = x.permute(0, 3, 1, 2)          # [B, 50, 512, 2]
        x = x.reshape(x.size(0), x.size(1), -1)  # [B, 50, 1024]
        x, _ = self.rnn(x)                 # [B, 50, 512]
        x = self.fc(x)                     # [B, 50, vocab]
        return x.permute(1, 0, 2)          # [50, B, vocab]
