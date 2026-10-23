#!/usr/bin/env python3
"""Train the captcha model and export it to ONNX in one run."""

import glob
import re
import json
import os

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
import cv2
import torchvision.transforms as transforms
from PIL import Image
from sklearn.model_selection import train_test_split
from torch.utils.tensorboard import SummaryWriter

from model import (
    CaptchaModel, VOCAB_SIZE, encode_label, decode_greedy, num_to_char,
)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
    def __init__(self, image_paths, transform=None):
        self.image_paths = image_paths
        self.transform = transform

    def __len__(self):
        return len(self.image_paths)

    def __getitem__(self, idx):
        img_path = self.image_paths[idx]
        image = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)

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
    train_paths, val_paths = train_test_split(image_paths, test_size=0.2, random_state=42)

    train_transform = transforms.Compose([
        transforms.RandomAffine(degrees=3, translate=(0.02, 0.05), scale=(0.95, 1.05), fill=255),
        transforms.ToTensor(),
        transforms.Normalize((0.5,), (0.5,)),
    ])
    val_transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize((0.5,), (0.5,)),
    ])

    train_ds = CaptchaDataset(train_paths, transform=train_transform)
    val_ds = CaptchaDataset(val_paths, transform=val_transform)

    train_loader = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True, collate_fn=collate_fn)
    val_loader = DataLoader(val_ds, batch_size=BATCH_SIZE, shuffle=False, collate_fn=collate_fn)

    model = CaptchaModel(VOCAB_SIZE).to(DEVICE)
    criterion = nn.CTCLoss(blank=0, zero_infinity=True)
    optimizer = optim.Adam(model.parameters(), lr=LEARNING_RATE)
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode='min', factor=0.5, patience=4)
    writer = SummaryWriter(os.path.join(ROOT, "training", "train", "model", "runs"))

    best_val_loss = float('inf')
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
            optimizer.step()
            train_loss += loss.item()

        model.eval()
        val_loss = 0
        exact, total = 0, 0
        char_correct, char_total = 0, 0
        with torch.no_grad():
            for images, labels, label_lens in val_loader:
                images = images.to(DEVICE)
                outputs = model(images)
                input_lengths = torch.full(
                    size=(images.size(0),), fill_value=50, dtype=torch.long
                ).to(DEVICE)
                loss = criterion(
                    outputs.log_softmax(2), labels.to(DEVICE),
                    input_lengths, label_lens.to(DEVICE),
                )
                val_loss += loss.item()

                # Greedy decode and compare against filename labels
                preds = decode_greedy(outputs)
                for b, pred_str in enumerate(preds):
                    truth = "".join(
                        num_to_char[i] for i in labels[b][:label_lens[b]].tolist()
                    )
                    exact += int(pred_str == truth)
                    total += 1
                    for p, t in zip(pred_str, truth):
                        char_correct += int(p == t)
                    char_total += len(truth)

        avg_train_loss = train_loss / len(train_loader)
        avg_val_loss = val_loss / len(val_loader)
        exact_acc = exact / max(total, 1)
        char_acc = char_correct / max(char_total, 1)

        metrics["per_epoch"].append({
            "epoch": epoch + 1,
            "train_loss": round(avg_train_loss, 5),
            "val_loss": round(avg_val_loss, 5),
            "val_exact_match": round(exact_acc, 4),
            "val_char_acc": round(char_acc, 4),
        })

        writer.add_scalar('Loss/train', avg_train_loss, epoch)
        writer.add_scalar('Loss/val', avg_val_loss, epoch)
        writer.add_scalar('Metrics/val_exact_match', exact_acc, epoch)
        writer.add_scalar('Metrics/val_char_acc', char_acc, epoch)

        print(
            f"Epoch {epoch+1}/{NUM_EPOCHS}, Train Loss: {avg_train_loss:.4f}, "
            f"Val Loss: {avg_val_loss:.4f}, Exact: {exact_acc:.4f}, Char: {char_acc:.4f}"
        )

        scheduler.step(avg_val_loss)

        if avg_val_loss < best_val_loss:
            best_val_loss = avg_val_loss
            os.makedirs(os.path.dirname(MODEL_OUT), exist_ok=True)
            torch.save(model.state_dict(), MODEL_OUT)
            with open(METRICS_OUT, "w") as f:
                json.dump(metrics, f, indent=2)
            print("Model saved.")

    writer.close()
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
