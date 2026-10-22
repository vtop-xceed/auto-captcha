#!/usr/bin/env python3
"""Export the trained PyTorch captcha model to ONNX."""

import os
import warnings

import torch

from model import CaptchaModel, VOCAB_SIZE

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(ROOT, "model", "captcha_model.pth")
ONNX_PATH = os.path.join(ROOT, "model", "captcha_model.onnx")


def main():
    os.makedirs(os.path.dirname(ONNX_PATH), exist_ok=True)
    print(f"Loading PyTorch model from {MODEL_PATH}...")
    model = CaptchaModel(VOCAB_SIZE)
    model.load_state_dict(torch.load(MODEL_PATH, map_location="cpu"))
    model.eval()
    model.rnn.flatten_parameters()

    dummy_input = torch.randn(1, 1, 40, 200)

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

    print("Verifying export...")
    import onnx
    onnx_model = onnx.load(ONNX_PATH)
    onnx.checker.check_model(onnx_model)
    print("ONNX model verified successfully")


if __name__ == "__main__":
    main()
