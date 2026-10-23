# Scripts

- **model.py**: CaptchaModel (CNN + BiGRU + CTC), vocab and encode/decode. Single source of truth; the Rust solver mirrors it.
- **train.py**: Trains the model and exports `deploy/captcha_model.onnx` (+ external `.data`) in one run.
- **decode_captchas.py**: Exact-match eval of a trained model over `data/test`, writes `data/test_results.txt`.
- **package.py**: Zips the labeler + data for PythonAnywhere upload.
- **requirements.txt**: Python deps for all of the above.

Subdirectories:

- **labeler/**: Flask crowd-labeling site (deployed on PythonAnywhere).
- **data/**: dataset (captchas + labels.db + test), gitignored.
- **model/**: training checkpoint (.pth) + tensorboard runs, gitignored.
