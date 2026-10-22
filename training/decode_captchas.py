import os
import glob

import cv2
import numpy as np
import torch

from model import (
    IMG_WIDTH, IMG_HEIGHT, CaptchaModel, VOCAB_SIZE, decode_greedy,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(ROOT, "model", "captcha_model.pth")
TEST_DIR = os.path.join(ROOT, "data", "test")
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


def load_image(img_path):
    image = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)
    image = image.astype(np.float32) / 255.0
    image = (image - 0.5) / 0.5
    return torch.tensor(image).unsqueeze(0).unsqueeze(0)  # [1, 1, H, W]


def predict():
    model = CaptchaModel(VOCAB_SIZE).to(DEVICE)
    model.load_state_dict(torch.load(MODEL_PATH, map_location=DEVICE))
    model.eval()

    test_images = glob.glob(os.path.join(TEST_DIR, "*.jpg"))
    print(f"Found {len(test_images)} test images. Decoding...")

    correct = 0
    results = []
    with torch.no_grad():
        for img_path in sorted(test_images):
            outputs = model(load_image(img_path).to(DEVICE))
            pred = decode_greedy(outputs)[0].upper()
            truth = os.path.basename(img_path).split(".")[0].upper()
            mark = "OK " if pred == truth else "BAD"
            if pred == truth:
                correct += 1
            print(f"{mark} {truth} -> {pred}")
            results.append(f"{truth}: {pred}")

    print(f"Exact match: {correct}/{len(test_images)}")
    out = os.path.join(ROOT, "data", "test_results.txt")
    with open(out, "w") as f:
        f.write("\n".join(results))
    print(f"Results saved to {out}")


if __name__ == "__main__":
    predict()
