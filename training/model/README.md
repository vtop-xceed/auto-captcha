# Model directory

Training artifacts live here on disk but are **gitignored**; the ONNX
graph ships as a GitHub release asset (tag pattern `model-YYYY.MM`)
and `training/onnx.py` also drops it straight into `deploy/`.

- `captcha_model.pth` - PyTorch float32 (~21MB), training artifact.
- `captcha_model.onnx` (+ `.onnx.data`) - exported graph consumed by
  the Rust solver (`service/`), opset 18, dynamic batch; written to
  `deploy/` by `training/onnx.py`.
- `tensorboard_charts.png`, `metrics.json`, `runs/` - experiment
  outputs from `training/onnx.py`.

Current trained artifact stats (v1 baseline):

- 50 epochs, CTC loss, final val loss 0.0013
- ~100% on the validation split of the then-labeled 811 images
