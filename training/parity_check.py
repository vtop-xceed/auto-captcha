"""Cross-implementation parity gate.

Loads the same fixture images through the PyTorch model (pth) and the
exported ONNX graph and asserts their greedy decoded outputs are identical.
Run before any model release:

    python parity_check.py
"""

import glob
import os
import sys

import cv2
import numpy as np
import torch

from model import CaptchaModel, VOCAB_SIZE, decode_greedy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PPTH_PATH = os.path.join(ROOT, "model", "captcha_model.pth")
ONNX_PATH = os.path.join(ROOT, "model", "captcha_model.onnx")
TEST_DIR = os.path.join(ROOT, "data", "test")
MAX_SAMPLES = 50


def load_image(img_path):
    image = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError(f"Failed to read {img_path}")
    if image.shape != (40, 200):
        image = cv2.resize(image, (200, 40), interpolation=cv2.INTER_AREA)
    norm = (image.astype(np.float32) / 255.0 - 0.5) / 0.5
    return norm[None, None, :, :]  # [1, 1, 40, 200]


def main():
    import onnxruntime

    torch_model = CaptchaModel(VOCAB_SIZE)
    torch_model.load_state_dict(torch.load(PPTH_PATH, map_location="cpu"))
    torch_model.eval()
    torch_model.rnn.flatten_parameters()

    session = onnxruntime.InferenceSession(ONNX_PATH, providers=["CPUExecutionProvider"])

    images = sorted(glob.glob(os.path.join(TEST_DIR, "*.jpg")))[:MAX_SAMPLES]
    if not images:
        print(f"No fixtures found in {TEST_DIR}")
        return 1

    batch = np.concatenate([load_image(p) for p in images], axis=0)
    with torch.no_grad():
        torch_out = torch_model(torch.from_numpy(batch))
    onnx_out = session.run(None, {"image": batch})[0]

    torch_preds = decode_greedy(torch_out)
    onnx_preds = decode_greedy(torch.from_numpy(onnx_out))

    mismatches = [
        (os.path.basename(p), t, o)
        for p, t, o in zip(images, torch_preds, onnx_preds)
        if t != o
    ]
    for name, t, o in mismatches:
        print(f"MISMATCH {name}: pth={t!r} onnx={o!r}")

    print(f"Parity: {len(images) - len(mismatches)}/{len(images)} identical.")
    if mismatches:
        sys.exit(1)


if __name__ == "__main__":
    main()
