# Model spec (CRNN + BiGRU + CTC)

This is the contract every implementation mirrors. The Python source of
truth is `training/model.py`; the Rust mirror is `service/src/solver.rs`.
Change both together or not at all.

## Input

- Single channel (luminance), resized to 40 x 200 (H x W), Lanczos3.
- Normalized: `p / 255`, then `(p - 0.5) / 0.5`.

## Vocab / decode

- Classes: `0-9a-z` lowercased, sorted, plus CTC blank.
- `CHARS = "0123456789abcdefghijklmnopqrstuvwxyz"`, 36 chars,
  class index `i` maps to `CHARS[i - 1]`, class 0 is blank. vocab = 37.
- Output tensor: `[T=50, B, 37]` (T is time steps along width).
- Decode: greedy per-step argmax over vocab; drop blank (0); collapse
  consecutive repeats; uppercase the result.

## ONNX

- File: `model/captcha_model.onnx` (+ external `.data`), opset 18,
  dynamic batch on `image` dim 0.

## Parity gate (required before any release)

```sh
python training/parity_check.py
```

Loads fixture images through both the pth and the ONNX graph and asserts
greedy-decoded strings are identical on every fixture. Nonzero exit on
mismatch. The `scripts/build-image.sh` flow refuses to produce a release
model that fails this.

## Release gate

A new model is releasable only if:

1. `parity_check.py` passes,
2. `data/test/` exact-match and char accuracy measured by
   `training/decode_captchas.py` is non-regressing versus the current
   production model,
3. metrics (`model/metrics.json` snapshot) are attached to the release.
