# Model directory

Binaries live here on disk but are **gitignored**; they ship as
GitHub release assets (tag pattern `model-YYYY.MM`).

- `captcha_model.pth` - PyTorch float32 (~21MB), training artifact.
- `captcha_model.onnx` (+ `.onnx.data`) - exported graph consumed by
  the Rust solver (`service/`), opset 18, dynamic batch.
- `tensorboard_charts.png`, `metrics.json`, `runs/` - experiment
  outputs from `training/train.py`.

Architecture and the decode contract: `../docs/model-spec.md`.
Refit routine: `../docs/model-refresh.md`.

Current trained artifact stats (v1 baseline):

- 50 epochs, CTC loss, final val loss 0.0013
- ~100% on the validation split of the then-labeled 811 images
