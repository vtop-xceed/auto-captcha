#!/usr/bin/env python3
"""Train the captcha model and export it to ONNX in one run."""

import glob
import re
import json
import os

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
import cv2
import torchvision.transforms as transforms
from PIL import Image
from torch.utils.tensorboard import SummaryWriter

from model import (
    CaptchaModel, VOCAB_SIZE, encode_label, decode_greedy, num_to_char,
)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- Color augment: randomized recolor before fixed BT.601 luma (solver.rs) ---
# Model only ever sees luminance, so we sample channel weights on the color
# simplex: any glyph/noise re-tinting the site later ships maps to a luminance
# pattern the model already saw. Weights sum to 1 so neutral bgs stay neutral.
_BASE_W = np.array([0.114, 0.587, 0.299], dtype=np.float32)  # BT.601, BGR order


def _recolor_weights(rng: np.random.Generator) -> np.ndarray:
    if rng.random() < 0.5:
        return rng.dirichlet(_BASE_W * 24 + 1).astype(np.float32)
    return rng.dirichlet(np.ones(3)).astype(np.float32)


def _randomized_luma(bgr: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    gray = bgr.astype(np.float32) @ _recolor_weights(rng)
    return np.clip(gray + 0.5, 0, 255).astype(np.uint8)


def _augment_color(bgr: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Simulated site restyling: re-tint, polarity flip, gamma, blur, noise."""
    gray = _randomized_luma(bgr, rng)
    if rng.random() < 0.05:  # ponytail: polarity guard, raise p if site ever dark bg
        gray = 255 - gray
    lut = (np.arange(256) / 255.0) ** rng.uniform(0.6, 1.6) * 255.0 + 0.5
    gray = lut.astype(np.uint8)[gray]
    if rng.random() < 0.2:
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
    sigma = rng.uniform(0.0, 4.0)
    gray = np.clip(gray + rng.normal(0.0, sigma, gray.shape) + 0.5, 0, 255)
    return gray.astype(np.uint8)


def _fixed_luma(bgr: np.ndarray) -> np.ndarray:
    """Validation path: must match runtime conversion exactly (solver.rs)."""
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

# --- Configuration ---
DATA_DIR = os.environ.get(
    "CAPTCHA_DATA_DIR", os.path.join(ROOT, "training", "data", "captchas")
)
MODEL_OUT = os.path.join(ROOT, "training", "train", "model", "captcha_model.pth")
METRICS_OUT = os.path.join(ROOT, "training", "train", "model", "metrics.json")
ONNX_PATH = os.path.join(ROOT, "deploy", "captcha_model.onnx")
BATCH_SIZE = 16
IMG_WIDTH = 200
IMG_HEIGHT = 40
NUM_EPOCHS = 50
LEARNING_RATE = 1e-3
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


# --- Dataset ---
class CaptchaDataset(Dataset):
    def __init__(self, image_paths, transform=None, color_augment=False):
        self.image_paths = image_paths
        self.transform = transform
        self.color_augment = color_augment
        self.rng = np.random.default_rng()

    def __len__(self):
        return len(self.image_paths)

    def __getitem__(self, idx):
        img_path = self.image_paths[idx]
        image = cv2.imread(img_path, cv2.IMREAD_COLOR)

        if self.color_augment:
            image = _augment_color(image, self.rng)
        else:
            image = _fixed_luma(image)

        image = Image.fromarray(image)
        if self.transform:
            image = self.transform(image)
        else:
            image = transforms.ToTensor()(image)
            image = transforms.Normalize((0.5,), (0.5,))(image)

        # Extract label from filename
        label_str = os.path.basename(img_path).split(".")[0].lower()
        label = torch.tensor(encode_label(label_str), dtype=torch.long)
        label_len = torch.tensor(len(label), dtype=torch.long)

        return image, label, label_len


def collate_fn(batch):
    images, labels, label_lens = zip(*batch)
    images = torch.stack(images)

    # Pad labels for CTC
    max_label_len = max(label_lens)
    padded_labels = torch.zeros((len(labels), max_label_len), dtype=torch.long)
    for i, label in enumerate(labels):
        padded_labels[i, :len(label)] = label

    return images, padded_labels, torch.stack(label_lens)


# --- Training / Validation ---
def labeled_paths():
    """Labels live in filenames: only 6-char [0-9a-z] stems are labeled.

    Unlabeled captures (e.g. report_<epoch>.png from /malformed) are skipped
    instead of silently training on their garbage stems.
    """
    all_paths = [
        p
        for ext in ("jpg", "png")
        for p in glob.glob(os.path.join(DATA_DIR, f"*.{ext}"))
    ]
    valid = re.compile(r"^[0-9a-z]{6}\.(jpg|png)$")
    paths = [p for p in all_paths if valid.match(os.path.basename(p).lower())]
    print(f"{len(all_paths) - len(paths)} unlabeled files skipped, {len(paths)} labeled images")
    return paths


def train():
    image_paths = labeled_paths()
    # ponytail: test_size=0 — every labeled image trains. Eval separately
    # via decode_captchas.py on a held-out test dir if you need accuracy numbers.

    train_transform = transforms.Compose([
        transforms.RandomAffine(
            degrees=5, translate=(0.06, 0.10), scale=(0.9, 1.1), shear=4,
            fill=255,
        ),
        transforms.ToTensor(),
        transforms.Normalize((0.5,), (0.5,)),
        transforms.RandomErasing(p=0.3, scale=(0.02, 0.08)),
    ])

    train_ds = CaptchaDataset(image_paths, transform=train_transform, color_augment=True)
    train_loader = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True, collate_fn=collate_fn)

    model = CaptchaModel(VOCAB_SIZE).to(DEVICE)
    criterion = nn.CTCLoss(blank=0, zero_infinity=True)
    optimizer = optim.AdamW(model.parameters(), lr=LEARNING_RATE, weight_decay=1e-4)
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode='min', factor=0.5, patience=4)
    writer = SummaryWriter(os.path.join(ROOT, "training", "train", "model", "runs"))

    metrics = {"model": "crnn-bgru-ctc", "per_epoch": []}

    for epoch in range(NUM_EPOCHS):
        model.train()
        train_loss = 0
        for images, labels, label_lens in train_loader:
            images = images.to(DEVICE)
            optimizer.zero_grad()

            outputs = model(images)
            input_lengths = torch.full(
                size=(images.size(0),), fill_value=50, dtype=torch.long
            ).to(DEVICE)

            loss = criterion(
                outputs.log_softmax(2), labels.to(DEVICE),
                input_lengths, label_lens.to(DEVICE),
            )
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            optimizer.step()
            train_loss += loss.item()

        avg_train_loss = train_loss / len(train_loader)
        scheduler.step(avg_train_loss)

        metrics["per_epoch"].append({
            "epoch": epoch + 1,
            "train_loss": round(avg_train_loss, 5),
        })

        writer.add_scalar('Loss/train', avg_train_loss, epoch)
        print(f"Epoch {epoch+1}/{NUM_EPOCHS}, Train Loss: {avg_train_loss:.4f}")

    writer.close()

    # ponytail: no val set → save final model. With AdamW + Dropout2d +
    # augmentation the model regularises itself; pick the converged result.
    os.makedirs(os.path.dirname(MODEL_OUT), exist_ok=True)
    torch.save(model.state_dict(), MODEL_OUT)
    with open(METRICS_OUT, "w") as f:
        json.dump(metrics, f, indent=2)
    print("Model saved.")

    return model


# --- ONNX export ---
def export_onnx():
    import warnings

    os.makedirs(os.path.dirname(ONNX_PATH), exist_ok=True)
    print(f"Loading PyTorch model from {MODEL_OUT}...")
    model = CaptchaModel(VOCAB_SIZE)
    model.load_state_dict(torch.load(MODEL_OUT, map_location="cpu"))
    model.eval()
    model.rnn.flatten_parameters()

    dummy_input = torch.randn(1, 1, IMG_HEIGHT, IMG_WIDTH)

    print(f"Exporting to ONNX format: {ONNX_PATH}")

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning)
        warnings.filterwarnings("ignore", category=FutureWarning)
        torch.onnx.export(
            model,
            dummy_input,
            ONNX_PATH,
            input_names=["image"],
            output_names=["output"],
            dynamic_axes={
                "image": {0: "batch_size"},
                "output": {1: "batch_size"},
            },
            opset_version=18,
            do_constant_folding=True,
        )

    import onnx
    onnx_model = onnx.load(ONNX_PATH)
    onnx.checker.check_model(onnx_model)
    print("ONNX model verified successfully")


if __name__ == "__main__":
    train()
    export_onnx()
